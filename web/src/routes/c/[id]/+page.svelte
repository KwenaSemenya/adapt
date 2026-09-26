<script lang="ts">
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { DECISION_LABEL, listJoin } from '$lib/format';
	import FlagCite from '$lib/FlagCite.svelte';
	import Missing from '$lib/Missing.svelte';
	import { CampaignPoll } from '$lib/poll.svelte';
	import { app, setCampaign } from '$lib/state.svelte';
	import type { VariantSummary } from '$lib/types';

	const id = $derived(page.params.id!);
	let poll = $state<CampaignPoll | null>(null);
	let retrying = $state<Record<string, boolean>>({});
	let retryErr = $state<Record<string, string>>({});

	$effect(() => {
		const p = new CampaignPoll(id);
		poll = p;
		setCampaign(id);
		return p.start();
	});

	const c = $derived(poll?.campaign ?? null);
	const criteria = $derived(
		app.boot?.criteria ?? [
			{ id: 'proposition', name: 'Proposition intact' },
			{ id: 'tone', name: 'Tone' },
			{ id: 'humour', name: 'Humour' },
			{ id: 'signatures', name: 'Voice signatures' },
			{ id: 'mandatories', name: 'Mandatories' }
		]
	);
	const STEP_NAME = { draft: 'drafting', score: 'scoring', flag: 'flagging', done: '' } as const;

	// Drift, per criterion: kept if every ready market scores 2, lost if any scores 0, else changed.
	const drift = $derived.by(() => {
		const ready = (c?.variants ?? []).filter((v) => v.ready && v.scores);
		const out = { kept: 0, changed: 0, lost: 0 };
		if (!ready.length) return null;
		for (const cr of criteria) {
			const vals = ready.map((v) => v.scores!.find((s) => s.criterion === cr.id)?.score ?? 2);
			const min = Math.min(...vals);
			if (min === 2) out.kept++;
			else if (min === 0) out.lost++;
			else out.changed++;
		}
		return out;
	});

	function score(v: VariantSummary, crit: string) {
		return v.scores?.find((s) => s.criterion === crit)?.score ?? '–';
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

	const grid = 'grid grid-cols-[170px_repeat(5,minmax(0,1fr))_200px_110px_110px] gap-4';
</script>

<main class="page gap-16 pt-16 pb-32 md:pt-[88px]">
	{#if poll?.notFound || (poll?.error && !c)}
		<Missing notFound={poll.notFound} error={poll.error} retry={() => poll?.refresh()} />
	{:else if !c}
		<div class="grid gap-4" aria-busy="true">
			<span class="t-note">Loading the campaign…</span>
			<div class="h-[59px] w-2/3 rounded-xl bg-wash"></div>
		</div>
	{:else}
		<div class="flex flex-wrap items-end justify-between gap-8">
			<div class="grid gap-4">
				<span class="t-note">{c.title}</span>
				<h1 class="t-display">{c.brief.proposition}</h1>
				<p class="m-0 text-[16px] text-muted">
					US master adapted for {listJoin(c.variants.map((v) => v.name))}. Every draft waits for a
					creative director.
				</p>
				{#if c.is_seed}
					<p class="m-0 t-note">
						Example campaign, cached from a real run. Your decisions on it stay in this browser.
					</p>
				{/if}
			</div>
			<a class="link-btn text-[16px]" href={`/brief?from=${c.id}`}>View brief</a>
		</div>

		<section class="grid gap-4">
			<h2 class="m-0 text-[16px] font-normal">
				{c.brief_flags.length === 1 ? 'Brief-level flag' : 'Brief-level flags'}
			</h2>
			{#if c.run?.brief_step === 'working' || c.run?.brief_step === 'pending'}
				<p class="rule m-0 pt-4 text-[16px] text-muted">Checking the brief…</p>
			{:else if c.run?.brief_step === 'failed'}
				<p class="rule m-0 pt-4 text-[16px]">
					The brief-level check stopped. Retry it from the processing page.
					<a href={`/c/${c.id}/processing`}>Go to processing</a>
				</p>
			{:else if !c.brief_flags.length}
				<p class="rule m-0 pt-4 text-[16px] text-muted">
					Nothing that affects every market. Market flags are below.
				</p>
			{:else}
				{#each c.brief_flags as f (f.id)}
					<div class="rule grid grid-cols-1 gap-2 pt-4 md:grid-cols-[180px_minmax(0,1fr)] md:gap-8">
						<span class="text-[16px]">{f.severity}</span>
						<div class="grid max-w-[66ch] gap-2">
							<p class="m-0 text-[16px] text-pretty">{f.text}</p>
							<FlagCite cite={f.cite} />
						</div>
					</div>
				{/each}
			{/if}
		</section>

		<section class="grid gap-4">
			<div class="flex flex-wrap items-baseline justify-between gap-4">
				<h2 class="t-title">Drift from the master</h2>
				{#if drift}
					<span class="t-note"
						>Advisory. {drift.kept} kept, {drift.changed} changed, {drift.lost} lost.</span
					>
				{/if}
			</div>
			<div class="table-scroll">
				<div role="table" aria-label="Drift from the master" class="grid" style:min-width="1104px">
					<div role="row" class="{grid} items-end pb-3">
						<span role="columnheader" class="t-note">Market</span>
						{#each criteria as cr (cr.id)}
							<span role="columnheader" class="t-note text-center">{cr.name}</span>
						{/each}
						<span role="columnheader" class="t-note">Flags</span>
						<span role="columnheader" class="t-note">Status</span>
						<span role="columnheader" class="t-note"><span class="sr-only">Action</span></span>
					</div>
					{#each c.variants as v (v.id)}
						<div role="row" class="rule {grid} items-start py-5">
							<div role="cell" class="grid gap-1">
								<span class="text-[18px]">{v.name}</span>
								{#if v.ready && v.low_confidence}<span class="t-note">Low confidence</span>{/if}
								{#if v.ready && v.scores_stale}<span class="t-note">Scores out of date</span>{/if}
							</div>
							{#if v.ready}
								{#each criteria as cr (cr.id)}
									<span
										role="cell"
										class="text-center text-[18px] tabular-nums {v.scores_stale ? 'text-muted' : ''}"
										>{score(v, cr.id)}</span
									>
								{/each}
								<div role="cell" class="grid gap-1">
									<span class="text-[16px] tabular-nums"
										>{v.flag_counts.High} High · {v.flag_counts.Medium} Med · {v.flag_counts.Low +
											v.flag_counts['Low confidence']} Low</span
									>
									{#if v.high_open}
										<span class="text-[13px] text-accent"
											>{v.high_open} high flag{v.high_open === 1 ? '' : 's'} open</span
										>
									{/if}
								</div>
								<span role="cell" class="text-[16px]">{DECISION_LABEL[v.decision]}</span>
								<div role="cell">
									<a class="btn btn-secondary btn-sm" href={`/c/${c.id}/${v.market}`}>Review</a>
								</div>
							{:else}
								<div role="cell" class="col-span-8 flex flex-wrap items-center gap-6">
									{#if v.step_status === 'failed'}
										<span class="text-[16px]"
											>{v.step === 'draft'
												? 'Drafting stopped.'
												: `${STEP_NAME[v.step][0].toUpperCase()}${STEP_NAME[v.step].slice(1)} stopped. The draft is kept.`}</span
										>
										<button
											class="btn btn-secondary btn-sm"
											onclick={() => retry(v)}
											aria-busy={retrying[v.id]}
											disabled={retrying[v.id]}
											>{retrying[v.id] ? 'Retrying…' : `Retry ${STEP_NAME[v.step]}`}</button
										>
										{#if retryErr[v.id]}<span class="text-[16px]" role="alert"
												>{retryErr[v.id]}</span
											>{/if}
									{:else}
										<span class="text-[16px]"
											>Still {STEP_NAME[v.step] || 'drafting'}. This row fills in when it finishes.</span
										>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			</div>
		</section>
	{/if}
</main>
