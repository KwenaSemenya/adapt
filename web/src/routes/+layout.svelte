<script lang="ts">
	import '../app.css';

	let { children } = $props();
</script>

<svelte:head>
	<title>ADAPT</title>
</svelte:head>

<div class="flex min-h-screen flex-col">
	<!-- Plain background with a hairline rule: the design's blurred header is removed. -->
	<header class="sticky top-0 z-10 border-b border-black/8 bg-white">
		<div class="mx-auto flex h-[52px] max-w-[1200px] items-center justify-between gap-8 px-12">
			<a href="/" class="text-body no-underline">ADAPT</a>
		</div>
	</header>
	{@render children()}
</div>
