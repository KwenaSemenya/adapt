// One fetch wrapper: every failure becomes a plain-language message the screen can show.

export class ApiError extends Error {
	status: number;
	fields: Record<string, string>;
	reason: string | null;
	constructor(status: number, message: string, fields: Record<string, string> = {}, reason: string | null = null) {
		super(message);
		this.status = status;
		this.fields = fields;
		this.reason = reason;
	}
}

export const NETWORK = "Couldn't reach ADAPT. Check your connection, then try again.";

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
	const { json, ...rest } = init;
	let res: Response;
	try {
		res = await fetch(`/api${path}`, {
			credentials: 'same-origin',
			...rest,
			headers: json !== undefined ? { 'content-type': 'application/json' } : rest.headers,
			body: json !== undefined ? JSON.stringify(json) : rest.body
		});
	} catch {
		throw new ApiError(0, NETWORK);
	}
	let body: any = null;
	try {
		body = await res.json();
	} catch {
		/* non-JSON error page */
	}
	if (!res.ok) {
		const fallback =
			res.status >= 500
				? 'ADAPT hit a problem on its side. Try again in a moment.'
				: 'That request didn’t work. Reload the page and try again.';
		throw new ApiError(res.status, body?.message ?? fallback, body?.fields ?? {}, body?.reason ?? null);
	}
	return body as T;
}
