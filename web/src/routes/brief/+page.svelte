<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { app, readLocal, setCampaign, writeLocal } from '$lib/state.svelte';
	import type { Campaign } from '$lib/types';

	type Key =
		| 'proposition'
		| 'audience'
		| 'mandatories'
		| 'tone'
		| 'headline'
		| 'body'
		| 'cta'
		| 'legal';
	type Form = Record<Key, string>;

	const LIMITS: Record<Key, number> = {
		proposition: 60,
		audience: 200,
		mandatories: 300,
		tone: 300,
		headline: 60,
		body: 300,
		cta: 24,
		legal: 80
	};
	const REQUIRED: Key[] = ['proposition', 'headline', 'body', 'cta', 'legal'];
	const LABELS: Record<Key, string> = {
		proposition: 'Proposition',
		audience: 'Audience',
		mandatories: 'Mandatories',
		tone: 'Tone notes',
		headline: 'Headline',
		body: 'Body',
		cta: 'Call to action',
		legal: 'Legal line'
	};
	const HINTS: Partial<Record<Key, string>> = {
		proposition: 'The one idea every market must keep.',
		audience: 'Who this is for.',
		mandatories: 'Lines and claims every market must carry.',
		tone: 'How it should sound, beyond the voice guide.',
		legal: 'Carried into every market word for word.'
	};
	const REQMSG: Partial<Record<Key, string>> = {
		proposition: 'Add the proposition. One line is enough.',
		headline: 'Add the master headline.',
		body: 'Add the master body copy.',
		cta: 'Add the call to action.',
		legal: "Add the legal line. It's carried into every market word for word."
	};
	const ROWS: Partial<Record<Key, number>> = { audience: 2, mandatories: 3, tone: 3, body: 4 };
	const EMPTY: Form = {
		proposition: '',
		audience: '',
		mandatories: '',
		tone: '',
		headline: '',
		body: '',
		cta: '',
		legal: ''
	};
	const DRAFT_KEY = 'adapt.brief.draft';

	let form = $state<Form>({ ...EMPTY });
	let sel = $state<Record<string, boolean>>({ za: true, ng: true, uk: true });
	let showErrors = $state(false);
	let serverErrors = $state<Partial<Record<Key | 'markets', string>>>({});
	let runError = $state('');
	let budgetOut = $state(false);
	let budgetMsg = $state('');
	let budgetReason = $state<string | null>(null);
	let exampleMsg = $state(false);
	let voiceOpen = $state(false);
	let running = $state(false);
	let loadingExample = $state(false);
	let fromCampaign = $state<Campaign | null>(null);
	let fromError = $state('');

	const fromId = $derived(page.url.searchParams.get('from'));

	// Restore: a brief behind an existing campaign, else this browser's unsent draft.
	// Only the URL is tracked; the body reads and writes form state, so it must not re-run on it.
	$effect(() => {
		const id = fromId;
		untrack(() => restore(id));
	});

	function restore(id: string | null) {
		if (id) {
			api<Campaign>(`/campaigns/${id}`)
				.then((c) => {
					fromCampaign = c;
					form = { ...EMPTY, ...c.brief, ...c.master } as Form;
					sel = Object.fromEntries(['za', 'ng', 'uk'].map((m) => [m, c.markets.includes(m)]));
				})
				.catch((e: ApiError) => (fromError = e.message));
			return;
		}
		fromCampaign = null;
		const saved = readLocal<{ form: Form; sel: Record<string, boolean> }>(DRAFT_KEY);
		if (saved) {
			form = { ...EMPTY, ...saved.form };
			sel = { za: true, ng: true, uk: true, ...saved.sel };
		}
	}

	// Keep the brief on this device so a failed run, a reload or the budget limit never loses it.
	$effect(() => {
		const snapshot = { form: { ...form }, sel: { ...sel } };
		if (!fromId) writeLocal(DRAFT_KEY, snapshot);
	});

	const markets = $derived(app.boot?.markets ?? []);
	const perDay = $derived(app.boot?.runs_per_day ?? 5);
	const used = $derived(app.boot?.runs_used_today ?? 0);
	const out = $derived(budgetOut || !!app.boot?.limit_reason || used >= perDay);
	const outMsg = $derived(
		budgetMsg ||
			app.boot?.limit_message ||
			`You've used all ${perDay} runs for today. Your brief is saved on this page. Runs reset at midnight UTC.`
	);
	const sessionOut = $derived((budgetReason ?? app.boot?.limit_reason ?? 'session') === 'session');

	function fieldError(k: Key): string {
		const n = form[k].length;
		if (n > LIMITS[k]) return `${LABELS[k]} is ${n} characters. Cut ${n - LIMITS[k]} to fit ${LIMITS[k]}.`;
		if (serverErrors[k]) return serverErrors[k]!;
		if (showErrors && REQUIRED.includes(k) && !form[k].trim()) return REQMSG[k] ?? '';
		return '';
	}

	const hard = $derived.by(() => {
		const e: string[] = [];
		(Object.keys(LIMITS) as Key[]).forEach((k) => {
			if (form[k].length > LIMITS[k] || (REQUIRED.includes(k) && !form[k].trim())) e.push(LABELS[k]);
		});
		if (!markets.some((m) => sel[m.code])) e.push('Markets');
		return e;
	});

	async function loadExample() {
		loadingExample = true;
		try {
			const { brief, master } = await api<{ brief: Form; master: Form }>('/seed-brief');
			form = { ...EMPTY, ...brief, ...master };
			showErrors = false;
			serverErrors = {};
			exampleMsg = true;
		} catch (e) {
			runError = (e as ApiError).message;
		} finally {
			loadingExample = false;
		}
	}

	function edit(k: Key, v: string) {
		form[k] = v;
		exampleMsg = false;
		if (serverErrors[k]) serverErrors = { ...serverErrors, [k]: undefined };
	}

	async function run() {
		if (out || running) return;
		runError = '';
		if (hard.length) {
			showErrors = true;
			return;
		}
		running = true;
		try {
			const res = await api<{ campaign_id: string }>('/campaigns', {
				method: 'POST',
				json: {
					brief: {
						proposition: form.proposition,
						audience: form.audience,
						mandatories: form.mandatories,
						tone: form.tone
					},
					master: { headline: form.headline, body: form.body, cta: form.cta, legal: form.legal },
					markets: markets.filter((m) => sel[m.code]).map((m) => m.code)
				}
			});
			if (app.boot) app.boot.runs_used_today += 1;
			setCampaign(res.campaign_id);
			await goto(`/c/${res.campaign_id}/processing`);
		} catch (e) {
			const err = e as ApiError;
			if (err.status === 422) {
				serverErrors = err.fields as typeof serverErrors;
				showErrors = true;
			} else if (err.status === 429) {
				budgetOut = true;
				budgetMsg = err.message;
				budgetReason = err.reason;
			} else {
				runError =
					err.status === 0
						? "The run didn't start because the connection dropped. Your brief is saved and no run was used. Check your connection, then try again."
						: `The run didn't start. ${err.message} Your brief is saved.`;
			}
		} finally {
			running = false;
		}
	}

	async function openExample() {
		const { campaign_id } = await api<{ campaign_id: string }>('/examples/baseline', {
			method: 'POST'
		});
		setCampaign(campaign_id);
		await goto(`/c/${campaign_id}`);
	}

	const reqs = [
		{ label: 'Proposition', rule: 'Required · up to 60' },
		{ label: 'Headline', rule: 'Required · up to 60' },
		{ label: 'Body', rule: 'Required · up to 300' },
		{ label: 'Call to action', rule: 'Required · up to 24' },
		{ label: 'Legal line', rule: 'Required · kept word for word' },
		{ label: 'Audience, mandatories, tone', rule: 'Optional · up to 300' },
		{ label: 'Markets', rule: 'At least one' }
	];

	const intro = $derived(
		fromCampaign
			? `The brief behind ${fromCampaign.title}. Change it and run again, or go back to the campaign.`
			: form.headline
				? 'Check the brief, pick markets, then run.'
				: 'Start from scratch, or load the example brief.'
	);
	const summary = $derived(
		`Fix ${hard.length === 1 ? 'one thing' : hard.length + ' things'} above before running: ${hard.join(', ')}. Everything else is kept.`
	);
</script>

{#snippet fieldBlock(k: Key, serif: boolean)}
	{@const err = fieldError(k)}
	<div class="grid gap-2">
		<div class="flex items-baseline justify-between gap-4">
			<label for={`f-${k}`} class="text-[16px]">{LABELS[k]}</label>
			<span class="t-note tabular-nums">{form[k].length} / {LIMITS[k]}</span>
		</div>
		{#if HINTS[k]}<span class="t-note" id={`h-${k}`}>{HINTS[k]}</span>{/if}
		{#if ROWS[k]}
			<textarea
				id={`f-${k}`}
				rows={ROWS[k]}
				value={form[k]}
				oninput={(e) => edit(k, e.currentTarget.value)}
				aria-invalid={err ? 'true' : undefined}
				aria-describedby={err ? `e-${k}` : HINTS[k] ? `h-${k}` : undefined}
				class="field resize-y p-3 leading-[1.4] {serif ? 'serif text-[18px]' : ''}"
			></textarea>
		{:else}
			<input
				id={`f-${k}`}
				value={form[k]}
				oninput={(e) => edit(k, e.currentTarget.value)}
				aria-invalid={err ? 'true' : undefined}
				aria-describedby={err ? `e-${k}` : HINTS[k] ? `h-${k}` : undefined}
				class="field h-[52px] px-4 {serif ? 'serif text-[18px]' : ''}"
			/>
		{/if}
		{#if err}<p class="m-0 text-[16px]" id={`e-${k}`}>{err}</p>{/if}
	</div>
{/snippet}

<main class="page gap-16 pt-16 pb-32 md:pt-[88px]">
	<div class="flex flex-wrap items-end justify-between gap-8">
		<div class="grid gap-4">
			<h1 class="t-display">Brief</h1>
			<p class="m-0 text-[16px] text-muted">{intro}</p>
		</div>
		<div class="flex items-center gap-4">
			{#if exampleMsg}<span class="t-note" role="status">Example brief loaded.</span>{/if}
			{#if fromCampaign}
				<a class="link-btn text-[16px]" href={`/c/${fromCampaign.id}`}>Back to the campaign</a>
			{:else}
				<button class="btn btn-secondary" onclick={loadExample} aria-busy={loadingExample}>
					{loadingExample ? 'Loading…' : 'Load example brief'}
				</button>
			{/if}
		</div>
	</div>
	{#if fromError}
		<p class="m-0 text-[16px]" role="alert">
			Couldn't load that campaign's brief. {fromError} You can still write a new one below.
		</p>
	{/if}

	<div class="grid grid-cols-1 gap-12 lg:grid-cols-12">
		<div class="grid gap-12 lg:col-span-8">
			<section class="grid gap-4">
				<div class="flex flex-wrap items-baseline gap-x-6 gap-y-2">
					<span class="t-note">Brand</span>
					<span class="text-[16px]">{app.boot?.brand.name ?? 'Kin'}</span>
					<span class="t-note">Read-only</span>
					<button
						class="link-btn text-[16px]"
						onclick={() => (voiceOpen = !voiceOpen)}
						aria-expanded={voiceOpen}>{voiceOpen ? 'Hide voice guide' : 'View voice guide'}</button
					>
				</div>
				{#if voiceOpen && app.boot}
					{@const b = app.boot.brand}
					<div class="rule grid max-w-[66ch] gap-2 pt-4">
						<p class="m-0 text-[16px]">Voice: {b.traits.join(', ')}.</p>
						<p class="m-0 text-[16px] text-muted">
							Signatures: {b.signatures.join(' ')} Never: {b.never.join(' ')} Proposition: {b.proposition}
						</p>
					</div>
				{/if}
			</section>

			<section class="grid gap-8">
				<h2 class="t-title">The brief</h2>
				{#each ['proposition', 'audience', 'mandatories', 'tone'] as k (k)}
					{@render fieldBlock(k as Key, false)}
				{/each}
			</section>

			<section class="grid gap-8">
				<div class="grid gap-2">
					<h2 class="t-title">Global master copy</h2>
					<span class="t-note">US English. This is what each market adapts from.</span>
				</div>
				{#each ['headline', 'body', 'cta', 'legal'] as k (k)}
					{@render fieldBlock(k as Key, true)}
				{/each}
			</section>

			<section class="grid gap-4">
				<h2 class="t-title">Markets</h2>
				<div class="flex h-12 items-center gap-4">
					<span
						class="inline-block h-5 w-5 rounded-[6px] border border-edge bg-wash"
						aria-hidden="true"
					></span>
					<span class="text-[16px]">United States</span>
					<span class="t-note">Source market. Always included.</span>
				</div>
				{#each markets as m (m.code)}
					<label class="flex h-12 cursor-pointer items-center gap-4">
						<input
							type="checkbox"
							bind:checked={sel[m.code]}
							class="m-0 h-5 w-5 cursor-pointer accent-ink"
						/>
						<span class="text-[16px]">{m.name}</span>
					</label>
				{/each}
				{#if showErrors && !markets.some((m) => sel[m.code])}
					<p class="m-0 text-[16px]">Pick at least one market to adapt for.</p>
				{/if}
			</section>

			<section class="rule grid gap-4 pt-8">
				<div class="flex flex-wrap items-center gap-6">
					{#if out}
						<button class="btn" disabled aria-disabled="true">Run adaptation</button>
					{:else}
						<button class="btn btn-primary" onclick={run} aria-busy={running}>
							{running ? 'Starting…' : runError ? 'Try again' : 'Run adaptation'}
						</button>
					{/if}
					<span class="text-[16px] text-muted tabular-nums">
						{out
							? sessionOut
								? `All ${perDay} runs used today`
								: 'No runs left today'
							: `Uses 1 of ${perDay} runs today. ${perDay - used} left.`}
					</span>
				</div>
				{#if showErrors && hard.length}
					<p class="m-0 max-w-[66ch] text-[16px]" role="alert">{summary}</p>
				{/if}
				{#if out}
					<div class="grid max-w-[66ch] gap-2" role="status">
						<p class="m-0 text-[16px]">{outMsg}</p>
						<p class="m-0 text-[16px]">
							While you wait, you can <button class="link-btn text-[16px]" onclick={openExample}
								>open the example campaign</button
							>.
						</p>
					</div>
				{/if}
				{#if runError && !out}
					<p class="m-0 max-w-[66ch] text-[16px]" role="alert">{runError}</p>
				{/if}
			</section>
		</div>

		<aside class="grid content-start gap-4 self-start lg:sticky lg:top-[84px] lg:col-span-4">
			<h2 class="m-0 text-[16px] font-normal">Before you run</h2>
			{#each reqs as rq (rq.label)}
				<div class="rule grid grid-cols-[minmax(0,1fr)_auto] gap-4 pt-3">
					<span class="text-[16px]">{rq.label}</span>
					<span class="t-note text-right tabular-nums">{rq.rule}</span>
				</div>
			{/each}
			<p class="rule m-0 t-note pt-3">
				Each market takes under a minute. You can leave this page while it runs.
			</p>
		</aside>
	</div>
</main>
