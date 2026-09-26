// App-wide state: the bootstrap payload and the campaign the viewer is working in.
import { api } from './api';
import type { Bootstrap } from './types';

export const app = $state<{ boot: Bootstrap | null; bootError: string; campaignId: string | null }>({
	boot: null,
	bootError: '',
	campaignId: null
});

export async function loadBoot(): Promise<void> {
	app.bootError = '';
	try {
		app.boot = await api<Bootstrap>('/bootstrap');
	} catch (e) {
		app.bootError = (e as Error).message;
	}
}

// Browser storage is a convenience only: every read and write may throw (private mode, blocked storage).
export function readLocal<T>(key: string): T | null {
	try {
		const raw = localStorage.getItem(key);
		return raw ? (JSON.parse(raw) as T) : null;
	} catch {
		return null;
	}
}

export function writeLocal(key: string, value: unknown): void {
	try {
		localStorage.setItem(key, JSON.stringify(value));
	} catch {
		/* storage unavailable: the page still works, it just won't remember */
	}
}

export function setCampaign(id: string | null): void {
	app.campaignId = id;
	try {
		if (id) sessionStorage.setItem('adapt.campaign', id);
	} catch {
		/* ignore */
	}
}

export function restoreCampaign(): void {
	try {
		app.campaignId = sessionStorage.getItem('adapt.campaign');
	} catch {
		app.campaignId = null;
	}
}
