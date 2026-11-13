<script lang="ts">
	import Card from '$components/Card.svelte';
	import Button from '$components/Button.svelte';
	import SettingRow from '$components/settings/SettingRow.svelte';
	import { createKey, listKeys, revokeKey, type KeyLevel, type PrinterKey } from '$lib/api/account';
	import { resolve } from '$app/paths';
	import { toasts } from '$lib/stores/toasts.svelte';
	import * as m from '$lib/paraglide/messages';

	let keys = $state<PrinterKey[]>([]);
	let name = $state('');
	let level = $state<KeyLevel>('edit');
	let created = $state<{ key: string; url: string }>();
	let dlg = $state<HTMLDialogElement>();

	const fail = (e: unknown) => toasts.error(m['account.error']({ message: (e as Error).message }));
	const refresh = () => listKeys().then((k) => (keys = k), fail);
	$effect(() => {
		refresh();
	});
	$effect(() => {
		if (created) dlg?.showModal();
	});

	async function create(e: SubmitEvent) {
		e.preventDefault();
		try {
			const k = await createKey(name.trim(), level);
			created = {
				key: k.key,
				url: `${location.origin}${resolve('/').replace(/\/$/, '')}/k/${k.key}/`
			};
			name = '';
			await refresh();
		} catch (err) {
			fail(err);
		}
	}

	async function revoke(k: PrinterKey) {
		try {
			await revokeKey(k.id);
			toasts.success(m['account.keys.revoked']());
			await refresh();
		} catch (err) {
			fail(err);
		}
	}

	async function copy(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			toasts.success(m['account.keys.copied']());
		} catch (err) {
			fail(err);
		}
	}

	const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : m['account.keys.never']());
	const levelLabel = (l: KeyLevel) =>
		l === 'edit' ? m['account.keys.level.edit']() : m['account.keys.level.view']();
</script>

<svelte:head>
	<title>{m['documentTitle.settings.keys']()}</title>
</svelte:head>

<div class="page scroll-y">
	<div class="wrap">
		<div class="title">{m['account.keys']()}</div>
		<p class="intro">{m['account.keys.intro']()}</p>

		<form class="create" onsubmit={create}>
			<input
				aria-label={m['account.keys.name']()}
				placeholder={m['account.keys.name']()}
				required
				bind:value={name}
			/>
			<select aria-label={m['account.keys.level']()} bind:value={level}>
				<option value="edit">{m['account.keys.level.edit']()}</option>
				<option value="view">{m['account.keys.level.view']()}</option>
			</select>
			<Button type="submit">{m['account.keys.create']()}</Button>
		</form>

		{#if keys.length === 0}
			<p class="intro">{m['account.keys.empty']()}</p>
		{:else}
			<Card divided>
				{#each keys as k (k.id)}
					<SettingRow
						title={k.name}
						desc={`${k.prefix}… · ${levelLabel(k.level)} · ${m['account.keys.created']()} ${fmt(k.created)} · ${m['account.keys.lastUsed']()} ${fmt(k.lastUsed)}`}
					>
						<Button variant="danger-ghost" onclick={() => revoke(k)}>{m['account.keys.revoke']()}</Button>
					</SettingRow>
				{/each}
			</Card>
		{/if}
	</div>
</div>

{#if created}
	<dialog bind:this={dlg} aria-labelledby="key-title" onclose={() => (created = undefined)}>
		<h2 id="key-title">{m['account.keys.dialogTitle']()}</h2>
		<p>{m['account.keys.once']()}</p>
		{#each [[m['account.keys.keyLabel'](), created.key], [m['account.keys.urlLabel'](), created.url]] as [label, value] (label)}
			<div class="copy">
				<code aria-label={label}>{value}</code>
				<Button
					variant="outline"
					ariaLabel={`${m['account.keys.copy']()} ${label}`}
					onclick={() => copy(value)}
				>
					{m['account.keys.copy']()}
				</Button>
			</div>
		{/each}
		<p class="warn">{m['account.keys.warning']({ key: '<key>' })}</p>
		<div class="foot"><Button onclick={() => dlg?.close()}>{m['buttons.close']()}</Button></div>
	</dialog>
{/if}

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
	.intro {
		font-size: 12px;
		color: var(--text-dim);
		line-height: 1.5;
	}
	.create {
		display: flex;
		gap: 8px;
		margin: 14px 0 18px;
	}
	input,
	select {
		background: var(--input-bg);
		border: 1px solid var(--border-input);
		border-radius: var(--radius);
		color: var(--text);
		padding: 6px 10px;
		font-size: 12.5px;
	}
	input {
		flex: 1;
		min-width: 0;
	}
	dialog {
		margin: 10vh auto auto;
		width: 560px;
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
	dialog p {
		font-size: 13px;
		line-height: 1.5;
		color: var(--text-2);
	}
	.warn {
		color: var(--text-dim);
		font-size: 12px;
	}
	.copy {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-bottom: 8px;
	}
	code {
		flex: 1;
		min-width: 0;
		padding: 6px 10px;
		overflow-wrap: anywhere;
		background: var(--input-bg);
		border: 1px solid var(--border-input);
		border-radius: var(--radius);
		font-size: 12px;
	}
	.foot {
		display: flex;
		justify-content: flex-end;
		margin-top: 12px;
	}
</style>
