<script lang="ts">
	import Button from './Button.svelte';
	import { claimData } from '$lib/api/account';
	import { toasts } from '$lib/stores/toasts.svelte';
	import * as m from '$lib/paraglide/messages';

	// Shown once per page load; "Not now" only hides it until the next load.
	let { onclose }: { onclose: () => void } = $props();
	let busy = $state(false);
	let dlg = $state<HTMLDialogElement>();
	$effect(() => dlg?.showModal());

	async function claim() {
		busy = true;
		try {
			await claimData();
			toasts.success(m['account.claim.done']());
			// Everything on screen was loaded before the claim.
			location.reload();
		} catch (e) {
			toasts.error(m['account.error']({ message: (e as Error).message }));
			busy = false;
		}
	}
</script>

<dialog bind:this={dlg} aria-labelledby="claim-title" {onclose}>
	<h2 id="claim-title">{m['account.claim.title']()}</h2>
	<p>{m['account.claim.body']()}</p>
	<div class="foot">
		<Button variant="outline" disabled={busy} onclick={() => dlg?.close()}
			>{m['account.claim.later']()}</Button
		>
		<Button disabled={busy} onclick={claim}>{m['account.claim.confirm']()}</Button>
	</div>
</dialog>

<style>
	dialog {
		margin: 12vh auto auto;
		width: 420px;
		max-width: calc(100% - 32px);
		padding: 18px 20px;
		background: var(--bg);
		color: var(--text);
		border: 1px solid var(--border-strong);
		border-radius: var(--radius-xl);
	}
	dialog::backdrop {
		background: rgba(0, 0, 0, 0.6);
	}
	h2 {
		margin: 0 0 8px;
		font-size: 15px;
	}
	p {
		margin: 0;
		font-size: 13px;
		line-height: 1.5;
		color: var(--text-2);
	}
	.foot {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 16px;
	}
</style>
