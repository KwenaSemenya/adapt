<script lang="ts">
	import { goto } from '$app/navigation';
	import { api, ApiError } from '$lib/api';
	import { mmss, plural, stamp } from '$lib/format';
	import { app, setCampaign } from '$lib/state.svelte';
	import type { Metrics } from '$lib/types';

	let metrics = $state<Metrics | null>(null);
	let metricsError = $state('');
	let opening = $state<string | null>(null);
	let openError = $state('');

	$effect(() => {
		api<Metrics>('/metrics')
			.then((m) => (metrics = m))
			.catch((e: ApiError) => (metricsError = e.message));
	});

	const boot = $derived(app.boot);
	const firstVisit = $derived(!!boot && boot.campaigns.length === 0);
	const tricky = $derived(boot?.examples.filter((e) => e.key !== 'baseline') ?? []);
	const hasBaseline = $derived(!boot || boot.examples.some((e) => e.key === 'baseline'));

	async function openExample(key: string) {
		opening = key;
		openError = '';
		try {
			const { campaign_id } = await api<{ campaign_id: string }>(`/examples/${key}`, {
				method: 'POST'
			});
			setCampaign(campaign_id);
			await goto(`/c/${campaign_id}`);
		} catch (e) {
			openError = (e as ApiError).message;
			opening = null;
		}
	}

	const baselineText = $derived(
		metrics?.baseline_hours_per_market
			? `Manual baseline: ${metrics.baseline_hours_per_market} hours per market`
			: 'Manual baseline not yet measured.'
	);
</script>

<main class="page gap-20 pt-20 pb-32 md:pt-32">
	<div class="grid max-w-[760px] gap-6">
		<h1 class="t-display text-balance">The AI drafts and flags. You decide.</h1>
		<p class="m-0 max-w-[60ch] text-[18px] leading-[1.45] text-pretty text-muted">
			Give it a global brief and US master copy. It drafts a version for South Africa, Nigeria and
			the UK, scores each one against the brand voice, and flags what to check. A local creative
			director approves, edits or rejects every draft. Nothing is published from here.
		</p>
	</div>

	<div class="grid gap-4">
		{#if firstVisit}
			<p class="m-0 t-note">Start here. The Kin example is already drafted for three markets.</p>
		{/if}
		<div class="flex flex-wrap items-center gap-4">
			<button
				class="btn btn-primary"
				onclick={() => openExample('baseline')}
				disabled={!hasBaseline}
				aria-busy={opening === 'baseline'}
			>
				{opening === 'baseline' ? 'Opening…' : 'Open the example campaign'}
			</button>
			<a class="btn btn-secondary" href="/brief">Start a new adaptation</a>
		</div>
		{#if openError}
			<p class="m-0 max-w-[66ch] text-[16px]" role="alert">{openError}</p>
		{/if}

		{#if tricky.length}
			<!-- Not in the design: the three tricky inputs, kept quiet so the main example stays the obvious start. -->
			<div class="grid max-w-[760px] gap-3 pt-6">
				<span class="t-note">Or see how it handles a tricky input</span>
				<ul class="m-0 grid list-none gap-2 p-0">
					{#each tricky as ex (ex.key)}
						<li class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
							<button
								class="link-btn text-[16px]"
								onclick={() => openExample(ex.key)}
								aria-busy={opening === ex.key}
								disabled={!!opening}
							>
								{ex.title.replace(/^.*· /, '')}
							</button>
							<span class="t-note">{opening === ex.key ? 'Opening…' : ex.description}</span>
						</li>
					{/each}
				</ul>
			</div>
		{/if}
	</div>

	{#if boot && boot.campaigns.length}
		<!-- Not in the design: lets people get back to runs they started. -->
		<section class="grid gap-4">
			<h2 class="t-title">Your adaptations</h2>
			<div class="grid">
				{#each boot.campaigns as c (c.id)}
					<a
						href={`/c/${c.id}`}
						class="rule grid grid-cols-[minmax(0,1fr)_auto] gap-4 py-3 text-[16px] no-underline"
					>
						<span>{c.title}</span>
						<span class="t-note tabular-nums"
							>{stamp(c.created_at)}{c.status === 'running'
								? ' · Running'
								: c.status === 'partial'
									? ' · Needs a retry'
									: ''}</span
						>
					</a>
				{/each}
			</div>
		</section>
	{/if}

	<section class="rule grid gap-8 pt-8">
		<div class="flex flex-wrap items-baseline justify-between gap-4">
			<h2 class="t-title">Efficiency</h2>
			<span class="t-note tabular-nums">
				{metrics ? `Measured across ${plural(metrics.runs, 'run')}` : ''}
			</span>
		</div>
		{#if metricsError}
			<p class="m-0 text-[16px] text-muted">
				Couldn't load the numbers. {metricsError}
			</p>
		{:else if !metrics}
			<p class="m-0 text-[16px] text-muted" aria-busy="true">Loading the numbers…</p>
		{:else if metrics.runs === 0}
			<p class="m-0 max-w-[66ch] text-[16px] text-muted">
				No runs measured yet. These numbers fill in after your first reviewed run. The example
				campaign doesn't count towards them.
			</p>
		{:else}
			<div class="grid grid-cols-2 gap-8 lg:grid-cols-4">
				{#each [[mmss(metrics.time_to_draft_s), 'Time to draft, per market (min:sec)'], [mmss(metrics.review_time_s), 'Review time, per market (min:sec)'], [metrics.words_kept_pct == null ? '–' : `${Math.round(metrics.words_kept_pct)}%`, 'Words kept from the AI draft'], [metrics.flag_ack_rate == null ? '–' : `${Math.round(metrics.flag_ack_rate)}%`, 'Flags acknowledged rather than dismissed']] as [value, label] (label)}
					<div class="grid content-start gap-2">
						<span
							class="text-[40px] leading-[1.05] tracking-[-0.035em] whitespace-nowrap tabular-nums md:text-[56px]"
							>{value}</span
						>
						<span class="text-[16px] text-muted">{label}</span>
					</div>
				{/each}
			</div>
		{/if}
		{#if metrics}
			<p class="m-0 text-[16px]">{baselineText}</p>
		{/if}
	</section>
</main>
