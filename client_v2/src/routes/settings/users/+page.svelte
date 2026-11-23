<script lang="ts">
	import Card from '$components/Card.svelte';
	import Button from '$components/Button.svelte';
	import SettingRow from '$components/settings/SettingRow.svelte';
	import { listUsers, revokeUserAccess, setUserDisabled, type AccountUser } from '$lib/api/account';
	import { toasts } from '$lib/stores/toasts.svelte';
	import * as m from '$lib/paraglide/messages';

	let users = $state<AccountUser[]>([]);

	const fail = (e: unknown) => toasts.error(m['account.error']({ message: (e as Error).message }));
	const refresh = () => listUsers().then((u) => (users = u), fail);
	$effect(() => {
		refresh();
	});

	async function act(run: () => Promise<void>) {
		try {
			await run();
			await refresh();
		} catch (e) {
			fail(e);
		}
	}

	function revoke(u: AccountUser) {
		if (confirm(m['account.users.revokeConfirm']({ name: u.displayName }))) act(() => revokeUserAccess(u.id));
	}

	const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : m['account.keys.never']());
	const label = (u: AccountUser) =>
		u.displayName +
		(u.isAdmin ? ` (${m['account.users.admin']()})` : '') +
		(u.disabled ? ` [${m['account.users.disabled']()}]` : '');
</script>

<svelte:head>
	<title>{m['documentTitle.settings.users']()}</title>
</svelte:head>

<div class="page scroll-y">
	<div class="wrap">
		<div class="title">{m['account.users']()}</div>
		<div class="gap"></div>
		{#if users.length === 0}
			<p class="note">{m['account.users.empty']()}</p>
		{:else}
			<Card divided>
				{#each users as u (u.id)}
					<SettingRow
						title={label(u)}
						desc={`${u.email ?? ''} ${m['account.users.lastLogin']()}: ${fmt(u.lastLogin)}`.trim()}
					>
						<Button variant="outline" onclick={() => act(() => setUserDisabled(u.id, !u.disabled))}>
							{u.disabled ? m['account.users.enable']() : m['account.users.disable']()}
						</Button>
						<Button variant="danger-ghost" onclick={() => revoke(u)}
							>{m['account.users.revokeAccess']()}</Button
						>
					</SettingRow>
				{/each}
			</Card>
		{/if}
	</div>
</div>

<style>
	.page {
		flex: 1;
		min-height: 0;
		padding: 22px 24px 48px;
	}
	.wrap {
		max-width: 680px;
		margin: 0 auto;
	}
	.title {
		font-weight: 700;
		font-size: 16px;
	}
	.gap {
		height: 14px;
	}
	.note {
		font-size: 12px;
		color: var(--text-dim);
	}
</style>
