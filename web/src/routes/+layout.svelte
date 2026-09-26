<script lang="ts">
	import '../app.css';
	import { page } from '$app/state';
	import { app, loadBoot, restoreCampaign } from '$lib/state.svelte';

	let { children } = $props();

	$effect(() => {
		restoreCampaign();
		loadBoot();
	});

	// Campaign pages carry their id in the URL; remember it so the nav can return there.
	const pathCampaign = $derived(page.params.id ?? null);
	const cid = $derived(pathCampaign ?? app.campaignId);

	const nav = $derived.by(() => {
		const p = page.url.pathname;
		const items = [
			{ label: 'Home', href: '/', current: p === '/' },
			{ label: 'Brief', href: '/brief', current: p.startsWith('/brief') }
		];
		if (cid) {
			items.push(
				{
					label: 'Campaign',
					href: `/c/${cid}`,
					current: p.startsWith('/c/') && !p.endsWith('/handoff')
				},
				{ label: 'Handoff', href: `/c/${cid}/handoff`, current: p.endsWith('/handoff') }
			);
		}
		return items;
	});
</script>

<svelte:head>
	<title>ADAPT</title>
</svelte:head>

<div class="flex min-h-screen flex-col">
	<!-- Plain background with a hairline rule: the design's blurred header is removed on purpose. -->
	<header class="sticky top-0 z-10 border-b border-black/8 bg-white">
		<div
			class="mx-auto flex h-[52px] max-w-[1200px] items-center justify-between gap-8 px-4 md:px-12"
		>
			<a href="/" class="text-[16px] no-underline hover:text-ink">ADAPT</a>
			<nav class="flex gap-5 md:gap-8" aria-label="Main">
				{#each nav as n (n.label)}
					<a
						href={n.href}
						aria-current={n.current ? 'page' : undefined}
						class="py-3 text-[13px] text-ink underline-offset-[6px] transition-colors duration-200 ease-out hover:text-muted {n.current
							? 'underline decoration-1'
							: 'no-underline'}">{n.label}</a
					>
				{/each}
			</nav>
		</div>
	</header>
	{#if app.bootError && !app.boot}
		<div class="page py-3" role="alert">
			<p class="m-0 text-[16px]">
				{app.bootError}
				<button class="link-btn" onclick={loadBoot}>Try again</button>
			</p>
		</div>
	{/if}
	{@render children()}
</div>
