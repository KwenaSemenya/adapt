<script lang="ts">
	let health = $state<{ app: string; markets: string[] } | null>(null);
	let error = $state('');

	$effect(() => {
		fetch('/api/health')
			.then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
			.then((h) => (health = h))
			.catch(() => (error = "The API didn't answer. Start it, then reload."));
	});
</script>

<main class="mx-auto grid w-full max-w-[1200px] gap-20 px-12 py-32">
	<div class="grid max-w-[760px] gap-6">
		<h1 class="text-display m-0 font-normal text-balance">The AI drafts and flags. You decide.</h1>
		<p class="m-0 max-w-[60ch] text-[18px] leading-[1.45] text-pretty text-muted">
			Give it a global brief and US master copy. It drafts a version for South Africa, Nigeria and the
			UK, scores each one against the brand voice, and flags what to check. A local creative director
			approves, edits or rejects every draft. Nothing is published from here.
		</p>
	</div>
	<p class="m-0 text-note text-muted" data-testid="health">
		{#if health}
			{health.app} is running. Market snapshots loaded: {health.markets.length
				? health.markets.join(', ').toUpperCase()
				: 'none yet'}.
		{:else if error}
			{error}
		{:else}
			Checking the API…
		{/if}
	</p>
</main>
