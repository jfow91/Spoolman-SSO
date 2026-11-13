import { afterEach, describe, expect, it, vi } from 'vitest';
import { createKey, getMe } from './account';

function mockFetch(status: number, body: unknown) {
	const fn = vi.fn(async () => new Response(JSON.stringify(body), { status }));
	vi.stubGlobal('fetch', fn);
	return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('account api', () => {
	it('getMe parses can_claim', async () => {
		mockFetch(200, {
			id: 1,
			display_name: 'Ann',
			email: null,
			is_admin: true,
			via: 'session',
			can_claim: true
		});
		const me = await getMe();
		expect(me.canClaim).toBe(true);
		expect(me.displayName).toBe('Ann');
		expect(me.isAdmin).toBe(true);
	});

	it('createKey returns raw key once', async () => {
		const fn = mockFetch(201, {
			id: 3,
			name: 'bridge',
			prefix: 'abcd',
			level: 'edit',
			created: '2026-01-01T00:00:00Z',
			last_used: null,
			key: 'sk_secret'
		});
		const k = await createKey('bridge', 'edit');
		expect(k.key).toBe('sk_secret');
		const [url, init] = fn.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toMatch(/\/keys$/);
		expect(JSON.parse(init.body as string)).toEqual({ name: 'bridge', level: 'edit' });
	});
});
