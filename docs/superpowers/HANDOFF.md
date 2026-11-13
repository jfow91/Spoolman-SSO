# Handoff: Spoolman-SSO (multi-user Spoolman fork)

Read this first if you are a Claude session picking up this project.

## What this project is

A fork of [Donkie/Spoolman](https://github.com/Donkie/Spoolman) (3D-printer filament
inventory) that adds:

- sign-in through **PocketID** (OIDC), with no local passwords;
- a **separate, private Spoolman for each person** (vendors, filaments, spools, tags,
  settings, locations, custom fields);
- **sharing** of a whole account or a single item, at view or edit level;
- **personal printer keys**, so Bambu Lab bridges that only accept a "Spoolman URL" can
  record usage against one person's spools (`https://host/k/<key>/`).

Users: the owner's household and friends, all in the owner's PocketID.

## Where everything is

| What | Where |
|---|---|
| Fork | `jfow91/Spoolman-SSO` (account is **jfow91**, no "l"); default branch `master` |
| Working branch | `multiuser-design` |
| Design spec (approved) | `docs/superpowers/specs/2026-11-12-multiuser-pocketid-design.md` |
| PR 1 plan (approved) | `docs/superpowers/plans/2026-11-12-pr1-signin-accounts-keys.md` |
| Upstream base | Donkie/Spoolman `06b4643` (2026-11-10) |

Read the spec and the PR 1 plan in full before writing code. The plan's Global Constraints
and Review Focus sections apply to every task.

## Decisions already made (do not re-ask)

- Built-in OIDC in Spoolman (not proxy-trusted headers, not one instance per person).
- Admins come from the PocketID group `spoolman_admins` and **cannot** see other accounts'
  data without a share.
- Everything is per account, including the catalogue. Records only link within one account;
  shared items get "Copy to my catalogue" (PR 2).
- Sharing: whole account and single item, view or edit, no re-sharing, owner-only
  management (PR 2).
- Keys: shown once, stored hashed, act only in the creator's own account, API and
  websockets only; URL prefix or `Authorization: Bearer` / `X-Api-Key`.
- New web client (`client_v2`) only; the legacy React client is disabled.
- Delivered as two pull requests into `master`:
  1. sign-in, accounts, keys (plan written);
  2. sharing (plan **not yet written**; write it with superpowers:writing-plans after
     PR 1 merges, from spec section 4 and the switcher/Shared-with-me parts of section 6).
- **Execution method chosen: subagent-driven** (superpowers:subagent-driven-development):
  a fresh agent per task, plus a reviewer before the next task.

## Before starting

1. **Package installs must be allowed.** The previous session had network installs off, so
   `uv`/`pip`/`npm` were refused. PR 1 needs `authlib~=1.6` and `itsdangerous~=2.2`, plus
   `npm install` in `client_v2`. Check with `uv sync` first. If installs are still
   refused, stop and tell the owner to enable package installs in network settings and
   start a new chat.
2. Attach and clone the fork: `add_repo` owner `jfow91`, repo `Spoolman-SSO`,
   access `push`. Then `git checkout multiuser-design`.
3. Use a git worktree or feature branch per superpowers:using-git-worktrees. Branch PR 1
   work from `multiuser-design`, e.g. `pr1-signin-accounts-keys`.

## Repo gotchas found so far

- Upstream `.gitignore` ignores `docs/`. Use `git add -f` for files under `docs/`.
- Clones through the session proxy are shallow. Run `git fetch origin <branch>` before
  pushing, or the push can be rejected (HTTP 413).
- Unit tests: `uv run pytest tests` (no DB/server; DB-needing tests build their own
  in-memory SQLite, as `tests/test_tag_autocreate.py` does).
- Integration tests: `uv run python tests_integration/run.py [sqlite|postgres|mariadb|cockroachdb]`.
  They run over HTTP in docker-compose and reach the server via `URL` from
  `tests_integration/tests/conftest.py`, which has about 940 call sites. The plan keeps
  `URL` but makes it a key-prefixed URL.
- Migrations must follow the CockroachDB DDL/DML split pattern
  (see `migrations/versions/2024_03_26_1349-304a32906234_*`).
- `.github/workflows/guard-translations.yml` rejects manual edits to non-English locale
  files. Add new UI strings to English only.
- CI already builds and pushes to GHCR; `GHCR_IMAGE` must become `jfow91/spoolman-sso`
  (plan Task 15).
- No CLAUDE.md in the repo as of handoff.

## Working with the owner

- The owner is **not a regular GitHub user**. When a PR is ready, give plain step-by-step
  instructions for reviewing and merging it on github.com (where to click, which button),
  not git commands.
- Their setup: an Unraid-based home lab with Prometheus/Grafana. The plan's `owner` metrics
  label is for that. Deployment target is a Docker image from GHCR.
- No Bambu bridge is installed yet. Keep keys bridge-agnostic.
- Commit and PR attribution follows the session's system instructions.

## Next actions

1. Confirm installs work (`uv sync`; `cd client_v2 && npm ci`).
2. Run the baseline: `uv run pytest tests`. Note any failures that exist before changes.
3. Run superpowers:subagent-driven-development on
   `docs/superpowers/plans/2026-11-12-pr1-signin-accounts-keys.md`, Tasks 1 to 15 in order.
4. Open PR 1 from the PR 1 branch into `master`, and walk the owner through merging it.
5. Write and run the PR 2 (sharing) plan.
