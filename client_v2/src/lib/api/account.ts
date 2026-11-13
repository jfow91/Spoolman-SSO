// Who is signed in, their printer keys, and (for admins) the user list.
import { getJson, postJson, postEmpty, deleteResource } from './http';

export interface Me {
	id: number;
	displayName: string;
	email: string | null;
	isAdmin: boolean;
	via: string;
	canClaim: boolean;
}

export type KeyLevel = 'edit' | 'view';

export interface PrinterKey {
	id: number;
	name: string;
	prefix: string;
	level: KeyLevel;
	created: string;
	lastUsed: string | null;
}

export interface AccountUser {
	id: number;
	displayName: string;
	email: string | null;
	isAdmin: boolean;
	disabled: boolean;
	lastLogin: string | null;
}

/* eslint-disable @typescript-eslint/no-explicit-any -- raw API rows */
const toKey = (r: any): PrinterKey => ({
	id: r.id,
	name: r.name,
	prefix: r.prefix,
	level: r.level,
	created: r.created,
	lastUsed: r.last_used ?? null
});

export async function getMe(): Promise<Me> {
	const r = await getJson<any>('/me');
	return {
		id: r.id,
		displayName: r.display_name,
		email: r.email ?? null,
		isAdmin: !!r.is_admin,
		via: r.via,
		canClaim: !!r.can_claim
	};
}

export const claimData = () => postJson<Record<string, number>>('/me/claim', {});

export async function listKeys(): Promise<PrinterKey[]> {
	return (await getJson<any[]>('/keys')).map(toKey);
}

/** The raw `key` is in this response only; it is never retrievable again. */
export async function createKey(name: string, level: KeyLevel): Promise<PrinterKey & { key: string }> {
	const r = await postJson<any>('/keys', { name, level });
	return { ...toKey(r), key: r.key };
}

export const revokeKey = (id: number) => deleteResource(`/keys/${id}`);

export async function listUsers(): Promise<AccountUser[]> {
	return (await getJson<any[]>('/admin/users')).map((r) => ({
		id: r.id,
		displayName: r.display_name,
		email: r.email ?? null,
		isAdmin: !!r.is_admin,
		disabled: !!r.disabled,
		lastLogin: r.last_login ?? null
	}));
}

export const setUserDisabled = (id: number, disabled: boolean) =>
	postEmpty(`/admin/users/${id}/${disabled ? 'disable' : 'enable'}`);

export const revokeUserAccess = (id: number) => postEmpty(`/admin/users/${id}/revoke-access`);
