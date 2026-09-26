<script lang="ts">
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { plural } from '$lib/format';
	import Missing from '$lib/Missing.svelte';
	import { CampaignPoll } from '$lib/poll.svelte';
	import { setCampaign } from '$lib/state.svelte';
	import type { VariantSummary } from '$lib/types';

	const id = $derived(page.params.id!);
	let poll = $state<CampaignPoll | null>(null);
	let now = $state(Date.now());
	let retrying = $state<Record<string, boolean>>({});
	let retryErr = $state<Record<string, string>>({});

	$effect(() => {
		const p = new CampaignPoll(id);
		poll = p;
		setCampaign(id);
		return p.start();
	});

	// A local clock for the estimated progress bars.
	$effect(() => {
		const t = setInterval(() => (now = Date.now()), 200);
		return () => clearInterval(t);
	});

	const c = $derived(poll?.campaign ?? null);
	const STEPS = ['draft', 'score', 'flag'] as const;
	const STEP_NAME = { draft: 'drafting', score: 'scoring', flag: 'flagging' } as const;
	// Typical seconds per step, measured on real runs. Only used to shape the estimate.
	const EXPECTED = { draft: 12, score: 6, flag: 9 } as const;

	function steps(v: VariantSummary) {
		const cur = v.step === 'done' ? 3 : STEPS.indexOf(v.step);
		return STEPS.map((s, i) => {
			if (i < cur) return { st: 'Done', pct: 100, working: false };
			if (i > cur) return { st: 'Waiting', pct: 0, working: false };
			if (v.step_status === 'failed') return { st: 'Stopped', pct: 0, working: false };
			if (v.step_status === 'working') {
				const t = Math.max(0, (now - Date.parse(v.updated_at)) / 1000);
				return { st: 'Working', pct: Math.round(90 * (1 - Math.exp(-t / EXPECTED[s]))), working: true };
			}
			return { st: 'Waiting', pct: 0, working: false };
		});
	}

	async function retry(v: VariantSummary) {
		retrying[v.id] = true;
		retryErr[v.id] = '';
		try {
			await api(`/variants/${v.id}/retry`, { method: 'POST' });
			await poll?.refresh();
		} catch (e) {
			retryErr[v.id] = (e as ApiError).message;
		} finally {
			retrying[v.id] = false;
		}
	}

	async function retryBrief() {
		retrying.brief = true;
		try {
			await api(`/campaigns/${id}/retry-brief`, { method: 'POST' });
			await poll?.refresh();
		} catch (e) {
			retryErr.brief = (e as ApiError).message;
		} finally {
			retrying.brief = false;
		}
	}

	const doneN = $derived(c ? c.variants.filter((v) => v.ready).length : 0);
	const failN = $derived(c ? c.variants.filter((v) => v.step_status === 'failed').length : 0);
	const total = $derived(c?.variants.length ?? 0);
	const summary = $derived(
		`${doneN} of ${total} ready` +
			(failN ? ` · ${failN} stopped` : '') +
			(doneN > 0 && doneN < total ? '. You can start reviewing now.' : '.')
	);

	function failText(v: VariantSummary): string {
		const kept = v.step === 'draft' ? '' : ` The ${v.name} draft is kept, so a retry picks up from ${STEP_NAME[v.step as 'score' | 'flag']}.`;
		return (v.error ?? 'This market stopped.') + kept;
	}
</script>

<main class="page gap-16 pt-16 pb-32 md:pt-[88px]">
	{#if poll?.notFound || (poll?.error && !c)}
		<Missing notFound={poll.notFound} error={poll.error} retry={() => poll?.refresh()} />
	{:else if !c}
		<div class="grid gap-4" aria-busy="true">
			<h1 class="t-display text-line">Drafting…</h1>
			<p class="m-0 text-[16px] text-muted">Loading the run.</p>
		</div>
	{:else}
		<div class="grid gap-4">
			<h1 class="t-display">Drafting for {plural(total, 'market')}</h1>
			<p class="m-0 text-[16px] text-muted">
				Each market drafts, scores and flags on its own. Results show up as each one finishes.
			</p>
		</div>
		{#if poll?.error}
			<p class="m-0 text-[16px]" role="status">
				Lost touch with the server. {poll.error} The run carries on; this page reconnects by itself.
			</p>
		{/if}

		<div class="table-scroll">
			<div class="grid">
				<div class="grid grid-cols-[180px_repeat(3,minmax(0,1fr))_340px] gap-8 pb-3">
					<span class="t-note">Market</span>
					<span class="t-note">Drafting</span>
					<span class="t-note">Scoring</span>
					<span class="t-note">Flagging</span>
					<span class="t-note">Result</span>
				</div>
				{#each c.variants as v (v.id)}
					<div
						class="rule grid grid-cols-[180px_repeat(3,minmax(0,1fr))_340px] items-start gap-8 py-6"
					>
						<span class="text-[18px]">{v.name}</span>
						{#each steps(v) as st, i (i)}
							<div class="grid gap-2">
								<div
									class="h-[3px] overflow-hidden rounded-full bg-line"
									role="progressbar"
									aria-label={`${v.name} ${['drafting', 'scoring', 'flagging'][i]}`}
									aria-valuemin="0"
									aria-valuemax="100"
									aria-valuenow={st.pct}
								>
									<div
										class="h-[3px] rounded-full bg-ink transition-[width] duration-200 ease-linear {st.working
											? 'working-bar'
											: ''}"
										style:width={`${st.pct}%`}
									></div>
								</div>
								<span class="t-note">{st.st}</span>
							</div>
						{/each}
						<div class="grid gap-2" aria-live="polite">
							{#if v.ready}
								<span class="t-note">AI draft ready</span>
								<span class="serif text-[18px] leading-[1.4]">{v.headline}</span>
								<span class="t-note"
									>{plural(v.flags_total, 'flag')} to check{v.low_confidence
										? ' · Low confidence'
										: ''}</span
								>
							{:else if v.step_status === 'failed'}
								<p class="m-0 text-[16px]">{failText(v)}</p>
								<div>
									<button
										class="btn btn-secondary"
										onclick={() => retry(v)}
										aria-busy={retrying[v.id]}
										disabled={retrying[v.id]}
									>
										{retrying[v.id]
											? 'Retrying…'
											: `Retry ${STEP_NAME[v.step as 'draft' | 'score' | 'flag']}`}
									</button>
								</div>
								{#if retryErr[v.id]}<p class="m-0 text-[16px]" role="alert">{retryErr[v.id]}</p>{/if}
							{:else}
								<span class="text-[16px] text-muted">
									{v.step_status === 'working' || v.step !== 'draft'
										? 'Working. Other markets carry on.'
										: 'Waiting to start.'}
								</span>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		</div>

		{#if c.run?.brief_step === 'failed'}
			<p class="m-0 max-w-[66ch] text-[16px]">
				The brief-level check stopped, so issues that affect every market aren't listed yet. Market
				drafts aren't affected.
				<button class="link-btn text-[16px]" onclick={retryBrief} aria-busy={retrying.brief}
					>{retrying.brief ? 'Retrying…' : 'Retry the brief check'}</button
				>
			</p>
		{/if}

		<div class="rule flex flex-wrap items-center gap-6 pt-8">
			{#if doneN > 0}
				<a class="btn btn-primary" href={`/c/${id}`}>Open campaign</a>
			{:else}
				<button class="btn" disabled>Open campaign</button>
			{/if}
			<span class="text-[16px] text-muted tabular-nums">{summary}</span>
		</div>
	{/if}
</main>
