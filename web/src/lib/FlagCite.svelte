<script lang="ts">
	// "Cites ZA-C1: <entry text>", with the snapshot's illustrative status shown, never hidden.
	import type { Cite } from './types';

	let { cite }: { cite: Cite } = $props();
	const label = $derived(cite.id.startsWith('BRIEF-') ? cite.label : cite.id);
</script>

<p class="m-0 t-note">
	Cites {label}{cite.illustrative ? ' (illustrative snapshot)' : ''}: {cite.id.startsWith('BRIEF-')
		? `"${cite.text}"`
		: cite.text}
</p>
