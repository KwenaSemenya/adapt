export type Severity = 'High' | 'Medium' | 'Low' | 'Low confidence';
export type Decision = 'draft' | 'approved' | 'rejected';
export type Step = 'draft' | 'score' | 'flag' | 'done';

export interface Bootstrap {
	app: string;
	brand: {
		id: string;
		name: string;
		proposition: string;
		traits: string[];
		humour: string;
		signatures: string[];
		never: string[];
	};
	markets: { code: string; name: string; status: string }[];
	limits: Record<string, number>;
	runs_per_day: number;
	runs_used_today: number;
	examples: { key: string; title: string; description: string }[];
	campaigns: { id: string; title: string; created_at: string; status: string | null }[];
	criteria: { id: string; name: string }[];
}

export interface Cite {
	id: string;
	label: string;
	text: string;
	illustrative: boolean;
	gap: boolean;
}

export interface Flag {
	id: string;
	severity: Severity;
	text: string;
	cite: Cite;
	change_id: string | null;
	state: 'open' | 'ack' | 'dismissed';
	by: string | null;
	at: string | null;
	reason: string | null;
}

export interface Score {
	criterion: string;
	name: string;
	score: 0 | 1 | 2;
	reason: string;
	adjusted: boolean;
}

export interface VariantSummary {
	id: string;
	market: string;
	name: string;
	step: Step;
	step_status: 'waiting' | 'working' | 'failed' | 'done';
	error: string | null;
	updated_at: string;
	ready: boolean;
	headline: string | null;
	flag_counts: Record<Severity, number>;
	flags_total: number;
	high_open: number;
	scores: Score[] | null;
	scores_stale: boolean;
	low_confidence: boolean;
	decision: Decision;
	decided_by: string | null;
	decided_at: string | null;
}

export interface Copy {
	headline: string;
	body: string;
	cta: string;
	legal: string;
}

export interface Campaign {
	id: string;
	title: string;
	brand: string;
	is_seed: boolean;
	seed_key: string | null;
	brief: Record<string, string>;
	master: Copy;
	markets: string[];
	run: { id: string; status: string; brief_step: string; started_at: string } | null;
	brief_flags: Flag[];
	variants: VariantSummary[];
}

export interface Segment {
	t: string;
	change?: string;
}

export interface Change {
	id: string;
	field: 'headline' | 'body' | 'cta';
	was: string;
	now: string;
	why: string;
	cite: Cite;
	kind: 'marked' | 'removal' | 'nested';
}

export interface Variant extends VariantSummary {
	campaign_id: string;
	master: Copy;
	text: Copy | null;
	segments: Record<'headline' | 'body' | 'cta', Segment[]> | null;
	edited: boolean;
	changes: Change[];
	flags: Flag[];
	confidence_why: string[];
	rescore_count: number;
	blockers: string[];
	decision_reason: string | null;
	snapshot_status: string | null;
}

export interface Handoff {
	campaign: { id: string; title: string };
	approved: (Copy & { market: string; name: string; by: string; at: string })[];
	others: { market: string; name: string; decision: Decision; by: string | null; ready: boolean }[];
	log: { at: string; who: string; market: string; action: string; reason: string }[];
}

export interface Metrics {
	runs: number;
	time_to_draft_s: number | null;
	review_time_s: number | null;
	words_kept_pct: number | null;
	flag_ack_rate: number | null;
	baseline_hours_per_market: number | null;
}
