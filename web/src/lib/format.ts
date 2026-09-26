// "26 Sep, 14:18" in the viewer's time zone, matching the design's stamps.
export function stamp(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return '';
	return (
		d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }) +
		', ' +
		d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
	);
}

// "South Africa, Nigeria and United Kingdom"
export function listJoin(items: string[]): string {
	if (items.length <= 1) return items.join('');
	return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
}

export function plural(n: number, one: string, many = one + 's'): string {
	return `${n} ${n === 1 ? one : many}`;
}

export function mmss(seconds: number | null | undefined): string {
	if (seconds == null) return '–';
	const s = Math.round(seconds);
	return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export const DECISION_LABEL: Record<string, string> = {
	draft: 'Draft',
	approved: 'Approved',
	rejected: 'Rejected'
};
