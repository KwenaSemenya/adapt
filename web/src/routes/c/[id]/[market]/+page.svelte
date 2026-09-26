<script lang="ts">
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { DECISION_LABEL, plural } from '$lib/format';
	import FlagCite from '$lib/FlagCite.svelte';
	import Missing from '$lib/Missing.svelte';
	import { CampaignPoll } from '$lib/poll.svelte';
	import { setCampaign } from '$lib/state.svelte';
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
								? `Approved by ${v.decided_by}`
								: v.edited
									? 'AI draft, edited'
									: 'AI draft'}</span
						>
					</div>
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
							Edited. Change marks are cleared after an edit. The original draft is in the decision log.
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
										? `Acknowledged by ${fl.by}`
										: `Dismissed by ${fl.by}`}</span
							>
						</div>
						<div class="grid max-w-[66ch] content-start gap-2">
							<p class="m-0 text-[16px] text-pretty">{fl.text}</p>
							<FlagCite cite={fl.cite} />
							{#if fl.state === 'dismissed' && fl.reason}<p class="m-0 t-note">Reason: {fl.reason}</p>{/if}
							{#if fl.change_id && !v.edited}
								<div>
									<button class="link-btn text-[13px]" onclick={() => showChange(fl.change_id)}
										>Show in copy</button
									>
								</div>
							{/if}
						</div>
						<div class="flex items-center justify-end gap-6 self-start">
							<!-- Acknowledge and Dismiss arrive with the decision rules (Phase 4). -->
						</div>
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
		{/if}
	{/if}
</main>
