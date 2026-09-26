<script lang="ts">
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { DECISION_LABEL, plural } from '$lib/format';
	import FlagCite from '$lib/FlagCite.svelte';
	import Missing from '$lib/Missing.svelte';
	import { CampaignPoll } from '$lib/poll.svelte';
	import { stamp } from '$lib/format';
	import { readLocal, setCampaign, writeLocal } from '$lib/state.svelte';
	import type { Change, Variant } from '$lib/types';

	const id = $derived(page.params.id!);
	const market = $derived(page.params.market!);
	let poll = $state<CampaignPoll | null>(null);
	let v = $state<Variant | null>(null);
	let vError = $state('');
	let selChange = $state<string | null>(null);
	let retrying = $state(false);
	let retryErr = $state('');
	let loadedKey = '';
	let openedId = '';

	// Decision state. The reviewer's name is remembered on this device only.
	let cdName = $state(readLocal<string>('adapt.cdName') ?? '');
	let panel = $state<'approve' | 'edit' | 'reject' | null>(null);
	let dismissOpen = $state<string | null>(null);
	let dismissText = $state('');
	let rejectText = $state('');
	let edit = $state({ headline: '', body: '', cta: '' });
	let editErrors = $state<Record<string, string>>({});
	let pending = $state<string | null>(null);
	let errors = $state<Record<string, string>>({});

	$effect(() => {
		writeLocal('adapt.cdName', cdName);
	});

	async function act(key: string, path: string, body: Record<string, unknown> = {}) {
		pending = key;
		errors = { ...errors, [key]: '' };
		try {
			v = await api<Variant>(path, { method: 'POST', json: { who: cdName, ...body } });
			poll?.refresh();
			return true;
		} catch (e) {
			const err = e as ApiError;
			errors = { ...errors, [key]: err.message };
			if (key === 'edit') editErrors = err.fields;
			return false;
		} finally {
			pending = null;
		}
	}

	function openPanel(key: 'approve' | 'edit' | 'reject') {
		if (key === 'edit' && panel !== 'edit' && v?.text) {
			edit = { headline: v.text.headline, body: v.text.body, cta: v.text.cta };
			editErrors = {};
		}
		panel = panel === key ? null : key;
	}

	async function saveEdit() {
		if (await act('edit', `/variants/${v!.id}/edit`, { ...edit })) {
			panel = null;
			selChange = null;
		}
	}

	async function confirmDismiss(flagId: string) {
		if (await act(`dismiss:${flagId}`, `/flags/${flagId}/dismiss`, { reason: dismissText })) {
			dismissOpen = null;
			dismissText = '';
		}
	}

	async function reject() {
		if (await act('reject', `/variants/${v!.id}/reject`, { reason: rejectText })) {
			panel = null;
			rejectText = '';
		}
	}

	async function approve() {
		if (await act('approve', `/variants/${v!.id}/approve`)) panel = null;
	}

	$effect(() => {
		const p = new CampaignPoll(id);
		poll = p;
		setCampaign(id);
		return p.start();
	});

	const c = $derived(poll?.campaign ?? null);
	const summary = $derived(c?.variants.find((x) => x.market === market) ?? null);

	// Reset per-market UI state when switching tabs.
	$effect(() => {
		market;
		selChange = null;
		v = null;
		loadedKey = '';
		panel = null;
		dismissOpen = null;
		errors = {};
	});

	// Load the full variant once its summary says it's ready (and again when it changes).
	$effect(() => {
		const s = summary;
		if (!s?.ready) return;
		const key = `${s.id}:${s.updated_at}`;
		if (loadedKey === key) return;
		loadedKey = key;
		api<Variant>(`/variants/${s.id}`)
			.then((data) => {
				v = data;
				vError = '';
				if (data.id !== openedId) {
					openedId = data.id;
					// Starts the review-time clock; failure only costs a metric, so it stays silent.
					api(`/variants/${data.id}/opened`, { method: 'POST' }).catch(() => {});
				}
			})
			.catch((e: ApiError) => (vError = e.message));
	});


	const STEP_NAME = { draft: 'drafting', score: 'scoring', flag: 'flagging', done: '' } as const;

	const marked = $derived((v?.changes ?? []).filter((ch) => ch.kind === 'marked'));
	const removals = $derived((v?.changes ?? []).filter((ch) => ch.kind === 'removal'));
	const navigable = $derived([...marked, ...removals]);
	const detail = $derived(navigable.find((ch) => ch.id === selChange) ?? null);

	function pick(chId: string) {
		selChange = selChange === chId ? null : chId;
	}
	function key(e: KeyboardEvent, chId: string) {
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			pick(chId);
		}
	}
	function showChange(chId: string | null) {
		if (!chId) return;
		const target = navigable.find((ch) => ch.id === chId);
		// A flag may point at a nested change; fall back to whichever marked change contains its text.
		selChange = target
			? chId
			: (marked.find((m) => m.now.includes(v!.changes.find((x) => x.id === chId)?.now ?? '\u0000'))
					?.id ?? null);
		document.getElementById('variant-copy')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}

	function sourceLine(ch: Change): string {
		const base = ch.cite.id.startsWith('BRIEF-') ? ch.cite.label.replace('Brief · ', 'Brief field · ') : `Market snapshot · ${ch.cite.id}`;
		return base + (ch.cite.gap ? ' (gap)' : '') + (ch.cite.illustrative ? ' · Illustrative' : '');
	}

	const sevCount = (n: string) =>
		(v?.flags ?? []).filter((f) => f.severity === n || (n === 'Low' && f.severity === 'Low confidence'))
			.length;
	const highOpen = $derived((v?.flags ?? []).filter((f) => f.severity === 'High' && f.state === 'open'));

	async function retry() {
		if (!summary) return;
		retrying = true;
		retryErr = '';
		try {
			await api(`/variants/${summary.id}/retry`, { method: 'POST' });
			await poll?.refresh();
		} catch (e) {
			retryErr = (e as ApiError).message;
		} finally {
			retrying = false;
		}
	}

	const decided = $derived(!!v && v.decision !== 'draft');
	const blocked = $derived((v?.blockers.length ?? 0) > 0);
	const name = $derived(cdName.trim());
	const left = $derived(3 - (v?.rescore_count ?? 0));
	const nextMarket = $derived.by(() => {
		if (!c) return null;
		const i = c.variants.findIndex((x) => x.market === market);
		const order = [...c.variants.slice(i + 1), ...c.variants.slice(0, i)];
		return order.find((x) => x.ready && x.decision === 'draft') ?? null;
	});
	const approveMsg = $derived(
		blocked
			? `Approval is waiting on the high flag${v!.blockers.length > 1 ? 's' : ''} ${v!.blockers.join(', ')}. Acknowledge or dismiss ${v!.blockers.length > 1 ? 'them' : 'it'} in Flags above. The score doesn't affect this.`
			: !name
				? 'Add your name to approve. It goes in the decision log with the time.'
				: 'Your name and the time go in the decision log.'
	);

	const FIELDS = [
		{ f: 'headline', cls: 'text-[28px] leading-[1.2] text-balance' },
		{ f: 'body', cls: 'text-[18px] leading-[1.4] text-pretty' },
		{ f: 'cta', cls: 'text-[18px]' }
	] as const;
</script>

<main class="page gap-16 pt-12 pb-32 md:pt-16">
	{#if poll?.notFound || (poll?.error && !c)}
		<Missing notFound={poll.notFound} error={poll.error} retry={() => poll?.refresh()} />
	{:else if !c}
		<p class="m-0 t-note" aria-busy="true">Loading the review…</p>
	{:else if !summary}
		<div class="panel">
			<p class="m-0 text-[18px]">This campaign doesn't include that market.</p>
			<div><a class="btn btn-primary" href={`/c/${id}`}>Back to the campaign</a></div>
		</div>
	{:else}
		<div class="grid gap-6">
			<nav class="flex flex-wrap items-baseline gap-x-8 gap-y-2" aria-label="Markets">
				<a class="text-[16px]" href={`/c/${id}`}>Campaign</a>
				{#each c.variants as t (t.id)}
					<a
						href={`/c/${id}/${t.market}`}
						aria-current={t.market === market ? 'page' : undefined}
						class="text-[16px] text-ink underline-offset-[6px] {t.market === market
							? 'underline'
							: 'no-underline'}"
						>{t.name} · {t.ready ? DECISION_LABEL[t.decision] : 'Not ready'}</a
					>
				{/each}
			</nav>
			<div class="flex flex-wrap items-end justify-between gap-8">
				<h1 class="t-display">{summary.name}</h1>
				<div class="flex items-baseline gap-4">
					<span class="t-note">Status</span>
					<span class="text-[18px]">{summary.ready ? DECISION_LABEL[summary.decision] : 'Not ready'}</span>
				</div>
			</div>
		</div>

		{#if !summary.ready}
			<div class="panel">
				{#if summary.step_status === 'failed'}
					<p class="m-0 text-[16px]">
						{summary.error}
						{summary.step !== 'draft' ? ' The draft is kept. Retry to pick up from ' + STEP_NAME[summary.step] + '.' : ''}
					</p>
					<div>
						<button class="btn btn-secondary" onclick={retry} aria-busy={retrying} disabled={retrying}
							>{retrying ? 'Retrying…' : `Retry ${STEP_NAME[summary.step]}`}</button
						>
					</div>
					{#if retryErr}<p class="m-0 text-[16px]" role="alert">{retryErr}</p>{/if}
				{:else}
					<p class="m-0 text-[16px]">
						This market is still {STEP_NAME[summary.step] || 'drafting'}. It appears here when it
						finishes.
					</p>
				{/if}
			</div>
		{:else if vError && !v}
			<Missing error={vError} />
		{:else if !v}
			<p class="m-0 t-note" aria-busy="true">Loading the variant…</p>
		{:else}
			{#if v.low_confidence}
				<div
					role="status"
					class="grid grid-cols-1 gap-2 rounded-2xl border border-edge px-7 py-6 md:grid-cols-[180px_minmax(0,1fr)] md:gap-8"
				>
					<span class="text-[16px]">Low confidence</span>
					<div class="grid max-w-[66ch] gap-2">
						{#each v.confidence_why as why (why)}
							<p class="m-0 text-[16px] text-pretty">{why}</p>
						{/each}
						<p class="m-0 t-note">Your judgement matters most on these lines.</p>
					</div>
				</div>
			{/if}

			<section class="grid grid-cols-1 gap-12 lg:grid-cols-2">
				<div class="grid content-start gap-4">
					<div class="flex h-6 items-baseline justify-between">
						<span class="t-note">US master · source</span>
					</div>
					<div class="serif grid gap-6 rounded-[20px] border border-line p-6 text-muted md:p-10">
						<p class="m-0 text-[28px] leading-[1.2] text-balance">{v.master.headline}</p>
						<p class="m-0 text-[18px] leading-[1.4] text-pretty">{v.master.body}</p>
						<p class="m-0 text-[18px]">{v.master.cta}</p>
						<p class="m-0 text-[16px]">{v.master.legal}</p>
					</div>
				</div>

				<div class="grid content-start gap-4">
					<div class="flex h-6 items-baseline justify-between">
						<span class="t-note">{v.name} variant</span>
						<span class="text-[13px] text-ink"
							>{v.decision === 'approved'
								? `Approved by ${v.decided_by}, ${stamp(v.decided_at)}`
								: v.edited
									? 'AI draft, edited by you'
									: 'AI draft'}</span
						>
					</div>
					{#if panel === 'edit'}
						<div class="grid gap-6 rounded-[20px] border border-edge bg-white p-6 md:p-10">
							{#each [['headline', 'Headline', 2, 'text-[28px] leading-[1.2]'], ['body', 'Body', 6, 'text-[18px] leading-[1.4]']] as [k, label, rows, cls] (k)}
								<label class="grid gap-2"
									><span class="t-note">{label}</span>
									<textarea
										rows={rows as number}
										bind:value={edit[k as 'headline' | 'body']}
										aria-invalid={editErrors[k as string] ? 'true' : undefined}
										class="field serif resize-y p-3 {cls}"
									></textarea>
									{#if editErrors[k as string]}<span class="text-[16px]">{editErrors[k as string]}</span>{/if}
								</label>
							{/each}
							<label class="grid gap-2"
								><span class="t-note">Call to action</span>
								<input
									bind:value={edit.cta}
									aria-invalid={editErrors.cta ? 'true' : undefined}
									class="field serif h-[52px] px-4 text-[18px]"
								/>
								{#if editErrors.cta}<span class="text-[16px]">{editErrors.cta}</span>{/if}
							</label>
							<p class="serif m-0 text-[16px] text-muted">{v.text?.legal}</p>
							<div class="flex flex-wrap items-center gap-6">
								<button class="btn btn-primary" onclick={saveEdit} aria-busy={pending === 'edit'}
									>{pending === 'edit' ? 'Saving…' : 'Save edits'}</button
								>
								<button class="link-btn text-[16px]" onclick={() => (panel = null)}>Cancel</button>
								<span class="t-note"
									>The legal line stays word for word. Saving marks the scores out of date.</span
								>
							</div>
							{#if errors.edit}<p class="m-0 text-[16px]" role="alert">{errors.edit}</p>{/if}
						</div>
					{:else}
					<div
							id="variant-copy"
							class="serif grid gap-6 rounded-[20px] border p-6 transition-colors duration-200 ease-out md:p-10 {v.decision ===
							'approved'
								? 'border-edge bg-white'
								: 'border-wash bg-wash'}"
						>
							{#each FIELDS as { f, cls } (f)}
								<p class="m-0 {cls}">
									{#if v.segments}
										{#each v.segments[f] as sg, i (i)}
											{#if sg.change}
												<span
													role="button"
													tabindex="0"
													aria-pressed={selChange === sg.change}
													onclick={() => pick(sg.change!)}
													onkeydown={(e) => key(e, sg.change!)}
													class="cursor-pointer rounded border-b border-ink px-[3px] py-px transition-colors duration-150 ease-out [box-decoration-break:clone] {selChange ===
													sg.change
														? 'bg-ink text-white'
														: 'bg-line text-ink hover:bg-edge'}">{sg.t}</span
												>
											{:else}<span>{sg.t}</span>{/if}
										{/each}
									{:else}
										{v.text?.[f]}
									{/if}
								</p>
							{/each}
							<p class="m-0 text-[16px]">{v.text?.legal}</p>
						</div>
						<p class="m-0 t-note">
							{#if v.edited}
								Edited by you. Change marks are cleared after an edit. The original draft is in the decision log.
							{:else if marked.length}
								{plural(marked.length, 'change')} from the master {marked.length === 1 ? 'is' : 'are'} marked.
								Select one to see where it came from.
							{:else if !removals.length}
								No changes from the master. The snapshot didn't call for any.
							{/if}
						</p>
						{#if removals.length && !v.edited}
							<div class="grid gap-1">
								{#each removals as r (r.id)}
									<p class="m-0 t-note">
										Removed from the master:
										<button
											class="link-btn text-[13px] text-ink"
											aria-pressed={selChange === r.id}
											onclick={() => pick(r.id)}>“{r.was}”</button
										>
									</p>
								{/each}
							</div>
						{/if}
						{#if detail}
							<div class="grid gap-3 border-t border-ink pt-4" aria-live="polite">
								<div class="flex items-baseline justify-between gap-4">
									<span class="t-note tabular-nums"
										>Change {navigable.indexOf(detail) + 1} of {navigable.length}</span
									>
									<button class="link-btn text-[13px]" onclick={() => (selChange = null)}>Close</button>
								</div>
								<div class="grid grid-cols-[64px_minmax(0,1fr)] gap-x-4 gap-y-2 text-[16px]">
									<span class="text-muted">Was</span><span class="serif text-[18px]"
										>{detail.was || 'Nothing. This is new.'}</span
									>
									<span class="text-muted">Now</span><span class="serif text-[18px]"
										>{detail.now || 'Removed.'}</span
									>
									<span class="text-muted">Source</span><span>{sourceLine(detail)}</span>
									<span></span><span class="text-muted"
										>{detail.cite.id.startsWith('BRIEF-') ? `“${detail.cite.text}”` : detail.cite.text}</span
									>
								</div>
							</div>
						{/if}
	
					{/if}
				</div>
			</section>

			<section class="grid gap-4">
				<div class="flex flex-wrap items-baseline justify-between gap-4">
					<h2 class="t-title">Flags</h2>
					<span class="t-note"
						>{sevCount('High')} High · {sevCount('Medium')} Medium · {sevCount('Low')} Low{highOpen.length
							? ` · ${highOpen.length} high open`
							: ''}</span
					>
				</div>
				{#if !v.flags.length}
					<p class="rule m-0 pt-4 text-[16px] text-muted">
						No flags for this market. Nothing in the snapshot or brief called for a check.
					</p>
				{/if}
				{#each v.flags as fl (fl.id)}
					{@const hiOpen = fl.severity === 'High' && fl.state === 'open'}
					<div
						class="grid grid-cols-1 gap-2 pt-4 pb-2 md:grid-cols-[180px_minmax(0,1fr)_280px] md:gap-8"
						style:border-top={hiOpen ? '2px solid var(--color-accent)' : '1px solid var(--color-line)'}
					>
						<div class="grid content-start gap-1">
							<span class="text-[16px] {hiOpen ? 'text-accent' : 'text-ink'}">{fl.severity}</span>
							<span class="text-[13px] {hiOpen ? 'text-accent' : 'text-muted'}"
								>{fl.state === 'open'
									? hiOpen
										? 'Needs a decision before approval'
										: 'Open'
									: fl.state === 'ack'
										? `Acknowledged by ${fl.by}, ${stamp(fl.at)}`
										: `Dismissed by ${fl.by}, ${stamp(fl.at)}`}</span
							>
						</div>
						<div class="grid max-w-[66ch] content-start gap-2">
							<p class="m-0 text-[16px] text-pretty">{fl.text}</p>
							<FlagCite cite={fl.cite} />
							{#if fl.state === 'dismissed' && fl.reason}<p class="m-0 t-note">Reason: {fl.reason}</p>{/if}
							{#if dismissOpen === fl.id && fl.state === 'open'}
								<div class="grid gap-2 pt-2">
									<label for={`d-${fl.id}`} class="text-[16px]"
										>Why dismiss this? Your reason goes in the decision log.</label
									>
									<!-- svelte-ignore a11y_autofocus -->
									<textarea
										id={`d-${fl.id}`}
										rows="2"
										bind:value={dismissText}
										autofocus
										class="field resize-y p-3 leading-[1.4]"
									></textarea>
									<div class="flex flex-wrap items-center gap-6">
										{#if dismissText.trim()}
											<button
												class="btn btn-secondary"
												onclick={() => confirmDismiss(fl.id)}
												aria-busy={pending === `dismiss:${fl.id}`}
												>{pending === `dismiss:${fl.id}` ? 'Saving…' : 'Dismiss flag'}</button
											>
										{:else}
											<button class="btn" disabled>Dismiss flag</button>
										{/if}
										<button class="link-btn text-[16px]" onclick={() => (dismissOpen = null)}
											>Cancel</button
										>
										{#if !dismissText.trim()}<span class="t-note">Add a reason to dismiss.</span>{/if}
									</div>
									{#if errors[`dismiss:${fl.id}`]}<p class="m-0 text-[16px]" role="alert">
											{errors[`dismiss:${fl.id}`]}
										</p>{/if}
								</div>
							{/if}
							{#if fl.change_id && !v.edited}
								<div>
									<button class="link-btn text-[13px]" onclick={() => showChange(fl.change_id)}
										>Show in copy</button
									>
								</div>
							{/if}
						</div>
						<div class="flex items-center gap-6 self-start md:justify-end">
							{#if fl.state === 'open' && dismissOpen !== fl.id && !decided}
								<button
									class="btn btn-secondary px-5"
									onclick={() => act(`ack:${fl.id}`, `/flags/${fl.id}/ack`)}
									aria-busy={pending === `ack:${fl.id}`}
									>{pending === `ack:${fl.id}` ? 'Saving…' : 'Acknowledge'}</button
								>
								<button
									class="link-btn text-[16px]"
									onclick={() => {
										dismissOpen = fl.id;
										dismissText = '';
									}}>Dismiss</button
								>
							{:else if fl.state !== 'open' && !decided}
								<button
									class="link-btn text-[16px]"
									onclick={() => act(`undo:${fl.id}`, `/flags/${fl.id}/undo`)}
									aria-busy={pending === `undo:${fl.id}`}
									>{pending === `undo:${fl.id}` ? 'Undoing…' : 'Undo'}</button
								>
							{/if}
						</div>
						{#if errors[`ack:${fl.id}`] || errors[`undo:${fl.id}`]}
							<p class="m-0 text-[16px] md:col-start-2" role="alert">
								{errors[`ack:${fl.id}`] || errors[`undo:${fl.id}`]}
							</p>
						{/if}
					</div>
				{/each}
			</section>

			<section class="grid gap-4">
				<div class="flex flex-wrap items-baseline justify-between gap-4">
					<div class="flex items-baseline gap-4">
						<h2 class="m-0 text-[16px] font-normal">Scores</h2>
						<span class="rounded-full border border-edge px-[10px] py-[2px] text-[13px] text-muted"
							>Advisory</span
						>
					</div>
					<span class="t-note">Out of 2. Scores never block approval.</span>
				</div>
				{#if v.scores_stale}
					<div class="flex flex-wrap items-center gap-6 rounded-2xl border border-edge px-7 py-5">
						<p class="m-0 flex-[1_1_360px] text-[16px]">
							{left > 0
								? 'These scores are for the draft before your edit, so they may be out of date. They stay advisory either way.'
								: "You've used all 3 rescores for this variant. The scores stay out of date. They're advisory, so you can still approve."}
						</p>
						{#if pending === 'rescore'}
							<span class="text-[16px] text-muted" role="status">Rescoring…</span>
						{:else if left > 0 && !decided}
							<button
								class="btn btn-secondary px-5 tabular-nums"
								onclick={() => act('rescore', `/variants/${v!.id}/rescore`)}
								>Rescore ({left} of 3 left)</button
							>
						{/if}
						{#if errors.rescore}<p class="m-0 w-full text-[16px]" role="alert">{errors.rescore}</p>{/if}
					</div>
				{/if}
				<div class="grid">
					{#each v.scores ?? [] as s (s.criterion)}
						<div
							class="rule grid grid-cols-[120px_32px_minmax(0,1fr)] gap-4 py-[10px] md:grid-cols-[180px_48px_minmax(0,1fr)] md:gap-8 {v.scores_stale
								? 'text-muted'
								: 'text-ink'}"
						>
							<span class="text-[13px]">{s.name}</span>
							<span class="text-[13px] tabular-nums">{s.score}</span>
							<span class="text-[13px]">{s.reason}</span>
						</div>
					{/each}
				</div>
			</section>

			<section class="grid gap-4 border-t border-ink pt-8">
				{#if !decided}
					<div class="flex flex-wrap items-baseline justify-between gap-4">
						<h2 class="t-title">Your decision</h2>
						<span class="t-note">Approve, edit or reject. Nothing is published from here.</span>
					</div>
					<div class="grid grid-cols-1 gap-6 sm:grid-cols-3">
						{#each [{ key: 'approve', label: 'Approve', help: blocked ? `${v.blockers.length} high flag${v.blockers.length > 1 ? 's need' : ' needs'} a decision first.` : 'Needs your name.', warn: blocked }, { key: 'edit', label: 'Edit', help: 'Change the copy inline.', warn: false }, { key: 'reject', label: 'Reject', help: 'Needs a reason.', warn: false }] as db (db.key)}
							<div class="grid content-start gap-2">
								<button
									aria-pressed={panel === db.key}
									onclick={() => openPanel(db.key as 'approve' | 'edit' | 'reject')}
									class="h-14 rounded-full border border-ink text-[16px] transition-colors duration-150 ease-out {panel ===
									db.key
										? 'bg-ink text-white'
										: 'bg-white text-ink hover:bg-wash'}">{db.label}</button
								>
								<span class="text-[13px] {db.warn ? 'text-accent' : 'text-muted'}">{db.help}</span>
							</div>
						{/each}
					</div>
					{#if panel === 'approve'}
						<div class="grid max-w-[760px] gap-4 pt-4">
							<label for="cd-name" class="text-[16px]">Your name</label>
							<input
								id="cd-name"
								bind:value={cdName}
								placeholder="Creative director's full name"
								autocomplete="name"
								class="field h-[52px] max-w-[400px] px-4"
							/>
							<div class="flex flex-wrap items-center gap-6">
								{#if !blocked && name}
									<button class="btn btn-primary" onclick={approve} aria-busy={pending === 'approve'}
										>{pending === 'approve' ? 'Approving…' : 'Confirm approval'}</button
									>
								{:else}
									<button class="btn" disabled>Confirm approval</button>
								{/if}
								<span class="max-w-[52ch] text-[16px] {blocked ? 'text-accent' : 'text-muted'}"
									>{approveMsg}</span
								>
							</div>
							{#if errors.approve}<p class="m-0 text-[16px]" role="alert">{errors.approve}</p>{/if}
						</div>
					{:else if panel === 'reject'}
						<div class="grid max-w-[760px] gap-4 pt-4">
							<label for="rej" class="text-[16px]"
								>Why reject this draft? The reason goes in the decision log.</label
							>
							<textarea id="rej" rows="3" bind:value={rejectText} class="field resize-y p-3 leading-[1.4]"
							></textarea>
							{#if !name}
								<span class="t-note"
									>Logged as “Unnamed reviewer”. Add your name under Approve to sign it.</span
								>
							{/if}
							<div class="flex flex-wrap items-center gap-6">
								{#if rejectText.trim()}
									<button class="btn btn-primary" onclick={reject} aria-busy={pending === 'reject'}
										>{pending === 'reject' ? 'Rejecting…' : 'Confirm rejection'}</button
									>
								{:else}
									<button class="btn" disabled>Confirm rejection</button>
									<span class="text-[16px] text-muted">Add a reason to reject.</span>
								{/if}
							</div>
							{#if errors.reject}<p class="m-0 text-[16px]" role="alert">{errors.reject}</p>{/if}
						</div>
					{/if}
				{:else}
					<div class="flex flex-wrap items-center justify-between gap-6">
						<div class="grid gap-2" role="status">
							<p class="m-0 text-[28px] leading-[1.2]">
								{DECISION_LABEL[v.decision]} by {v.decided_by}, {stamp(v.decided_at)}.
							</p>
							{#if v.decision_reason}<p class="m-0 text-[16px] text-muted">Reason: {v.decision_reason}</p>{/if}
							{#if v.decision === 'approved'}
								<p class="m-0 text-[16px] text-muted">The approved copy is ready in Handoff.</p>
							{/if}
						</div>
						<div class="flex flex-wrap items-center gap-6">
							<button
								class="btn btn-secondary"
								onclick={() => act('undo', `/variants/${v!.id}/undo`)}
								aria-busy={pending === 'undo'}
								>{pending === 'undo' ? 'Undoing…' : 'Undo, back to Draft'}</button
							>
							{#if nextMarket}
								<a class="text-[16px]" href={`/c/${id}/${nextMarket.market}`}>Next: {nextMarket.name}</a>
							{/if}
							<a class="text-[16px]" href={`/c/${id}/handoff`}>Go to handoff</a>
						</div>
					</div>
					{#if errors.undo}<p class="m-0 text-[16px]" role="alert">{errors.undo}</p>{/if}
				{/if}
			</section>
		{/if}
	{/if}
</main>
