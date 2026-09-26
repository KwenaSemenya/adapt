<script lang="ts">
	import { page } from '$app/state';
	import { api, ApiError } from '$lib/api';
	import { stamp } from '$lib/format';
	import Missing from '$lib/Missing.svelte';
	import { setCampaign } from '$lib/state.svelte';
	import type { Handoff } from '$lib/types';

	const id = $derived(page.params.id!);
	let data = $state<Handoff | null>(null);
	let error = $state('');
	let notFound = $state(false);
	let msgs = $state<Record<string, string>>({});

	async function load() {
		error = '';
		try {
			data = await api<Handoff>(`/campaigns/${id}/handoff`);
		} catch (e) {
			const err = e as ApiError;
			if (err.status === 404) notFound = true;
			else error = err.message;
		}
	}

	$effect(() => {
		setCampaign(id);
		load();
	});

	const MARKET_NAME: Record<string, string> = {
		za: 'South Africa',
		ng: 'Nigeria',
		uk: 'United Kingdom',
		all: 'All'
	};

	function txt(a: Handoff['approved'][number]): string {
		return `Kin · ${a.name}\nApproved by ${a.by}, ${stamp(a.at)}\n\n${a.headline}\n\n${a.body}\n\n${a.cta}\n\n${a.legal}\n`;
	}

	async function copy(a: Handoff['approved'][number]) {
		msgs[a.market] = 'Copying…';
		try {
			await navigator.clipboard.writeText(txt(a));
			msgs[a.market] = 'Copied. Paste it wherever you work.';
		} catch {
			// Clipboard blocked (permissions, embedded frames): select the copy so one keystroke finishes the job.
			const card = document.getElementById(`copy-${a.market}`);
			if (card) {
				const range = document.createRange();
				range.selectNodeContents(card);
				const sel = window.getSelection();
				sel?.removeAllRanges();
				sel?.addRange(range);
			}
			const key = /Mac|iPhone|iPad/.test(navigator.platform) ? 'Cmd+C' : 'Ctrl+C';
			msgs[a.market] = `Couldn't reach the clipboard, so the copy is selected. Press ${key} to copy it.`;
		}
	}

	function download(a: Handoff['approved'][number]) {
		const name = `kin-${a.market}-approved.txt`;
		const url = URL.createObjectURL(new Blob([txt(a)], { type: 'text/plain' }));
		const link = document.createElement('a');
		link.href = url;
		link.download = name;
		document.body.appendChild(link);
		link.click();
		link.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
		msgs[a.market] = `Downloaded ${name}.`;
	}
</script>

<main class="page gap-20 pt-16 pb-32 md:pt-[88px]">
	<div class="grid gap-4">
		<h1 class="t-display">Handoff</h1>
		<p class="m-0 text-[16px] text-muted">
			Only copy a creative director approved appears here. Take it to your usual tools.
		</p>
	</div>

	{#if notFound || (error && !data)}
		<Missing {notFound} {error} retry={load} />
	{:else if !data}
		<p class="m-0 t-note" aria-busy="true">Loading approved copy…</p>
	{:else}
		{#if !data.approved.length}
			<div class="panel">
				<p class="m-0 text-[18px]">Nothing approved yet.</p>
				<p class="m-0 text-[16px] text-muted">
					Approve a variant in review and its copy appears here, ready to copy or download.
				</p>
				<div><a class="btn btn-primary" href={`/c/${id}`}>Go to campaign review</a></div>
			</div>
		{/if}

		{#each data.approved as ap (ap.market)}
			<section class="grid grid-cols-1 gap-8 lg:grid-cols-12 lg:gap-12">
				<div class="grid content-start gap-2 lg:col-span-4">
					<h2 class="t-title">{ap.name}</h2>
					<span class="t-note">Approved by {ap.by}, {stamp(ap.at)}</span>
					<div class="flex flex-wrap items-center gap-6 pt-4">
						<button class="btn btn-secondary px-5" onclick={() => copy(ap)}>Copy to clipboard</button>
						<button class="link-btn text-[16px]" onclick={() => download(ap)}>Download .txt</button>
					</div>
					<span role="status" class="min-h-[18px] t-note">{msgs[ap.market] ?? ''}</span>
				</div>
				<!-- Hairline border instead of the design's box-shadow. -->
				<div
					id={`copy-${ap.market}`}
					class="serif grid gap-6 rounded-[20px] border border-edge p-6 md:p-10 lg:col-span-8"
				>
					<p class="m-0 text-[28px] leading-[1.2] text-balance">{ap.headline}</p>
					<p class="m-0 text-[18px] leading-[1.4] text-pretty">{ap.body}</p>
					<p class="m-0 text-[18px]">{ap.cta}</p>
					<p class="m-0 text-[16px]">{ap.legal}</p>
				</div>
			</section>
		{/each}

		{#if data.approved.length && data.others.length}
			<div class="grid gap-2">
				{#each data.others as o (o.market)}
					<p class="m-0 text-[16px] text-muted">
						{o.decision === 'rejected'
							? `${o.name}: rejected by ${o.by}. Not included.`
							: `${o.name}: still a draft. It appears here once approved.`}
					</p>
				{/each}
			</div>
		{/if}

		<section class="grid gap-4">
			<h2 class="t-title">Decision log</h2>
			<div class="table-scroll">
				<div class="grid">
					<div class="grid grid-cols-[140px_180px_160px_200px_minmax(0,1fr)] gap-6 pb-3">
						<span class="t-note">When</span>
						<span class="t-note">Who</span>
						<span class="t-note">Market</span>
						<span class="t-note">Decision</span>
						<span class="t-note">Detail</span>
					</div>
					{#if !data.log.length}
						<p class="rule m-0 pt-4 text-[16px] text-muted">
							No decisions yet. Every acknowledge, dismiss, edit, approval and rejection lands here
							with a name and time.
						</p>
					{/if}
					{#each data.log as lg, i (i)}
						<div class="rule grid grid-cols-[140px_180px_160px_200px_minmax(0,1fr)] gap-6 py-3">
							<span class="text-[16px] text-muted tabular-nums">{stamp(lg.at)}</span>
							<span class="text-[16px]">{lg.who}</span>
							<span class="text-[16px]">{MARKET_NAME[lg.market] ?? lg.market}</span>
							<span class="text-[16px]">{lg.action}</span>
							<span class="text-[16px] text-pretty text-muted">{lg.reason}</span>
						</div>
					{/each}
				</div>
			</div>
		</section>
	{/if}
</main>
