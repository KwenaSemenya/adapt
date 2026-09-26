// Load a campaign and keep it fresh while anything in it is still running.
import { api, ApiError } from './api';
import type { Campaign } from './types';

export function isActive(c: Campaign | null): boolean {
	if (!c?.run) return false;
	return (
		c.run.status === 'running' ||
		c.run.brief_step === 'working' ||
		c.run.brief_step === 'pending' ||
		c.variants.some((v) => v.step_status === 'waiting' || v.step_status === 'working')
	);
}

export class CampaignPoll {
	campaign = $state<Campaign | null>(null);
	error = $state('');
	notFound = $state(false);
	loading = $state(true);
	#id: string;
	#timer: ReturnType<typeof setTimeout> | null = null;
	#stopped = false;

	constructor(id: string) {
		this.#id = id;
	}

	start(): () => void {
		this.#stopped = false;
		this.refresh();
		return () => this.stop();
	}

	stop(): void {
		this.#stopped = true;
		if (this.#timer) clearTimeout(this.#timer);
	}

	async refresh(): Promise<void> {
		if (this.#timer) clearTimeout(this.#timer);
		try {
			this.campaign = await api<Campaign>(`/campaigns/${this.#id}`);
			this.error = '';
			this.notFound = false;
		} catch (e) {
			const err = e as ApiError;
			if (err.status === 404) this.notFound = true;
			else this.error = err.message;
		} finally {
			this.loading = false;
		}
		if (this.#stopped) return;
		// Poll quickly while work is running; back off when idle or when the connection is down.
		const delay = this.error ? 4000 : isActive(this.campaign) ? 1000 : 0;
		if (delay) this.#timer = setTimeout(() => this.refresh(), delay);
	}
}
