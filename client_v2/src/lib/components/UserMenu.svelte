<script lang="ts">
	import { resolve } from '$app/paths';
	import UserIcon from '@lucide/svelte/icons/user';
	import type { Me } from '$lib/api/account';
	import * as m from '$lib/paraglide/messages';

	let { me }: { me: Me } = $props();
	let open = $state(false);

	// A plain navigation, not fetch: the server answers with a redirect to the IdP.
	const signOut = () => (window.location.href = resolve('/').replace(/\/$/, '') + '/auth/logout');
</script>

<svelte:window onclick={(e) => !(e.target as Element).closest('.user-menu') && (open = false)} />

<div class="user-menu">
	<button class="trigger" aria-haspopup="menu" aria-expanded={open} onclick={() => (open = !open)}>
		<UserIcon size={16} />
		<span class="name">{me.displayName}</span>
	</button>
	{#if open}
		<div class="pop" role="menu">
			<a role="menuitem" href={resolve('/settings/keys')} onclick={() => (open = false)}>
				{m['account.keys']()}
			</a>
			{#if me.isAdmin}
				<a role="menuitem" href={resolve('/settings/users')} onclick={() => (open = false)}>
					{m['account.users']()}
				</a>
			{/if}
			<button role="menuitem" onclick={signOut}>{m['account.signOut']()}</button>
		</div>
	{/if}
</div>

<style>
	.user-menu {
		position: relative;
		flex: none;
	}
	.trigger {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		height: 36px;
		padding: 0 10px;
		border-radius: var(--radius-md);
		border: 1px solid var(--border-strong);
		background: none;
		color: var(--text-2);
		cursor: pointer;
		font-size: 13px;
	}
	.trigger:hover {
		color: var(--text);
		border-color: var(--accent);
	}
	.name {
		max-width: 140px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.pop {
		position: absolute;
		right: 0;
		top: calc(100% + 4px);
		z-index: 40;
		min-width: 160px;
		display: flex;
		flex-direction: column;
		background: var(--surface);
		border: 1px solid var(--border-strong);
		border-radius: var(--radius-md);
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
		overflow: hidden;
	}
	.pop a,
	.pop button {
		padding: 9px 14px;
		text-align: left;
		font-size: 13px;
		color: var(--text-2);
		background: none;
		border: none;
		text-decoration: none;
		cursor: pointer;
	}
	.pop a:hover,
	.pop button:hover {
		background: var(--input-bg);
		color: var(--text);
	}
	@media (max-width: 860px) {
		.name {
			display: none;
		}
		.trigger {
			height: 44px;
			width: 44px;
			justify-content: center;
			padding: 0;
		}
	}
</style>
