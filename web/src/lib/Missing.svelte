<script lang="ts">
	// Shared not-found and load-error states for campaign pages.
	let {
		notFound = false,
		error = '',
		retry
	}: { notFound?: boolean; error?: string; retry?: () => void } = $props();
</script>

<div class="panel">
	{#if notFound}
		<p class="m-0 text-[18px]">This campaign isn't in this browser.</p>
		<p class="m-0 text-[16px] text-muted">
			Campaigns belong to the browser session that started them. Open the example, or start a new
			adaptation.
		</p>
		<div><a class="btn btn-primary" href="/">Go home</a></div>
	{:else}
		<p class="m-0 text-[18px]">Couldn't load this campaign.</p>
		<p class="m-0 text-[16px] text-muted">{error}</p>
		{#if retry}<div><button class="btn btn-secondary" onclick={retry}>Try again</button></div>{/if}
	{/if}
</div>
