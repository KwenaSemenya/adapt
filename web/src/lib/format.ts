// "26 Sep, 14:18" in the viewer's time zone, matching the design's stamps.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function stamp(iso: string | null | undefined): string {
	if (!iso) return '';
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return '';
	// Built by hand: some locales print "Sept", the design uses three-letter months.
	const hh = String(d.getHours()).padStart(2, '0');
	const mm = String(d.getMinutes()).padStart(2, '0');
	return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${hh}:${mm}`;
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
