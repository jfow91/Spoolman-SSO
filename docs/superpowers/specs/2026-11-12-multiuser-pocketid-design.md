# Spoolman multi-user fork: PocketID SSO, per-person accounts, sharing

Date: 2026-11-12
Status: Draft for review
Base: Donkie/Spoolman `06b4643` (2026-11-10)

## 1. Goal

A fork of Spoolman where a household and friends sign in through PocketID, each person
runs their own fully separate Spoolman ("account"), and people can grant each other
access to a whole account or to individual items. Bambu Lab printers (via a bridge,
not yet chosen) report usage into the right person's account using per-person keys.

### Users and trust

- Household and friends; all trusted, all present in the owner's PocketID.
- Separation exists to keep records organised and private by default, not to defend
  against hostile users. It must still be correct: no route may leak or modify another
  account's data without a grant.

### Success criteria

1. Signing in requires PocketID; the app refuses to start without OIDC configured.
2. A person sees and changes only their own account, plus what is shared with them.
3. Whole-account and single-item shares work at view and edit levels; revocation is
   immediate.
4. A bridge configured with only a base URL can record usage against one person's
   spools using a personal key.
5. An automated test proves every API route denies cross-account access.

### Out of scope

Group shares, expiring shares, email invites, re-sharing, keys that act on shared
accounts, the legacy web client, any non-PocketID login method, local passwords.

## 2. Sign-in and users

### OIDC

- Spoolman is a confidential OIDC client of PocketID using authorization code + PKCE.
- Configuration (environment variables, all required unless noted):
  - `SPOOLMAN_OIDC_ISSUER` — PocketID base URL; discovery via `/.well-known/openid-configuration`.
  - `SPOOLMAN_OIDC_CLIENT_ID`, `SPOOLMAN_OIDC_CLIENT_SECRET` (or `SPOOLMAN_OIDC_CLIENT_SECRET_FILE`).
  - `SPOOLMAN_PUBLIC_URL` — external URL; redirect URI is `<public_url>/auth/callback`.
  - `SPOOLMAN_SESSION_SECRET` (or `_FILE`) — at least 32 bytes; signs session cookies.
  - `SPOOLMAN_OIDC_ADMIN_GROUP` — optional, default `spoolman_admins`.
- Scopes: `openid profile email groups`.
- Startup fails with a clear error if any required variable is missing or discovery fails.

### Accounts

- New table `user`: `id`, `oidc_sub` (unique), `display_name`, `email`, `is_admin`,
  `disabled`, `created`, `last_login`.
- First login creates the user. Every login refreshes `display_name`, `email`, and
  `is_admin` (membership of the admin group in the `groups` claim).
- Who may sign in is controlled entirely in PocketID (which users/groups may use the
  client). Spoolman adds only the per-user `disabled` flag.

### Sessions

- Server-issued, signed, HTTP-only, `Secure`, `SameSite=Lax` cookie; 30-day sliding expiry.
- Session record stored server-side (`session` table: id, user_id, created, expires,
  last_seen) so disabling a user or signing out invalidates immediately.
- Unauthenticated page navigation → 302 to PocketID, returning to the original path.
  Unauthenticated API/websocket → 401 / close 4401. The existing
  `client_v2/src/lib/api/auth.ts` reload-on-401 logic covers session expiry.
- `/auth/logout` clears the session and redirects to PocketID's end-session endpoint
  if advertised.
- The existing CSRF/origin guards in `spoolman/security.py` stay in force for
  cookie-authenticated requests.

### Admins

- Can list users, disable/enable a user, revoke a user's keys and sessions, and change
  app-wide (non-account) options.
- Cannot see other accounts' data without a grant.

## 3. Account separation

### Ownership

- Add `owner_id` (FK `user.id`, NOT NULL, indexed) to: `vendor`, `filament`, `spool`,
  `tag`, `setting`.
- `setting` primary key becomes `(owner_id, key)`. Settings that are app-wide (if any are
  identified during planning) move to a separate `app_setting` table, admin-editable.
- Per-account by consequence of the above: locations, location ordering, dashboard
  layout, custom (extra) field definitions, currency and other user settings.
- `tag.uid` uniqueness becomes `(owner_id, uid)`. Tag scans resolve within the scanning
  principal's own account.

### Scope enforcement

- Every request resolves a **principal** (user via session, or key) and an **active
  account** (the principal's own by default; another via the `X-Spoolman-Account`
  header/websocket query param, sent by the account switcher).
- A single access module computes, for the active account, whether the principal holds
  `owner`, `edit` or `view`, plus the set of individually shared item IDs.
- All database read functions take a scope object and apply the owner filter in one
  shared helper; no route builds its own owner filter. Writes check the required level
  through the same module.
- Out-of-scope records return 404 (not 403), so their existence is not revealed.

### Cross-account references

- A record may only reference records in the same account (spool → filament → vendor).
  Create/update validates this and returns 400 otherwise.
- A filament or vendor visible through an item share offers "Copy to my catalogue",
  creating a copy owned by the viewer.

### Other surfaces

- Websocket events: delivered only to sockets whose principal can view the record at
  send time. The subscription tree gains a per-socket visibility check.
- Export (CSV/JSON): active account only.
- External catalogue (SpoolmanDB): remains global and read-only; imports create records
  in the active account (requires `edit`).
- Prometheus metrics: per-account series gain an `owner` label (display name slug).
- Search: scoped like every other read.

### Existing data migration

- An Alembic migration adds the new tables and columns. Rows that exist before the
  migration are assigned to a placeholder system owner.
- The first admin to sign in after upgrade is offered "Claim existing data", which
  reassigns all placeholder-owned rows to them in one transaction. Empty databases skip
  this.

## 4. Sharing

- Table `account_grant`: `owner_id`, `grantee_id`, `level` (`view`|`edit`), `created`.
  Unique `(owner_id, grantee_id)`.
- Table `item_grant`: `owner_id`, `grantee_id`, `item_type` (`vendor`|`filament`|`spool`),
  `item_id`, `level`, `created`. Unique `(grantee_id, item_type, item_id)`.
- Effective level on an item = max(account grant, item grant on that item).
- Account `edit`: create, update, delete and use anything in the account, including
  locations, custom fields and settings. Not: manage grants or keys.
- Item `edit`: update fields and record usage (spool `use`/`measure`); not delete.
- Implied read access: an item-shared spool exposes its filament and vendor read-only;
  an item-shared filament exposes its vendor read-only.
- Only the owner manages grants. No re-sharing. Grantees are picked from existing users.
- Revocation deletes the grant; open websockets for the grantee re-check visibility and
  stop receiving events for that account/item immediately.

## 5. Printer and bridge keys

- Table `api_key`: `id`, `user_id`, `name`, `prefix` (first 8 chars, for display),
  `hash` (SHA-256 of full key; keys are 32 random bytes, base62), `level`
  (`edit`|`view`), `created`, `last_used`, `revoked`.
- A key acts as its creator, in the creator's own account only, at its level.
- Accepted as:
  - URL prefix: `/k/<key>/...` — middleware validates and strips the prefix, so a bridge
    given `https://host/k/<key>/` as its base URL works unmodified;
  - `Authorization: Bearer <key>`;
  - `X-Api-Key: <key>`.
- Keys authorise `/api/v1/**` and websockets only; never web pages, auth endpoints,
  grant/key/user management.
- `last_used` updated at most once a minute per key.
- Keys page warns that URL keys may appear in proxy access logs and recommends headers
  where the bridge supports them.

## 6. Web client (client_v2 only)

- Legacy client disabled; `SPOOLMAN_LEGACY_CLIENT` ignored with a startup warning.
- New: user menu (name, sign out), account switcher, Sharing page (account grants and
  per-item share dialogs on vendor/filament/spool pages), Shared with me page, Printer
  keys page, admin Users page, "Claim existing data" prompt.
- View-only contexts hide create/edit/delete controls and show a "View only" badge.
- The API client sends `X-Spoolman-Account` for the active account.

## 7. Testing

- Fake OIDC provider fixture for unit and integration tests (no real PocketID needed).
- **Route isolation sweep:** enumerates every registered API route and websocket from the
  FastAPI app, and for each calls it as user B against user A's records, asserting
  404/403/close. Fails if a new route is added without being covered by scope
  enforcement. Runs in the existing four-database integration matrix.
- Sharing: each level × each item type × account vs item grant; implied read access;
  revocation mid-websocket.
- Keys: URL prefix, both headers, view-only key refused on writes, revoked key, key
  refused on management endpoints and web pages.
- Migration: upgrade a populated pre-fork database, claim by first admin.
- Existing upstream tests updated to run as an authenticated user.

## 8. Delivery

- Fork lives on the owner's GitHub account.
- GitHub Action builds and publishes a Docker image to GHCR on pushes to the default branch.
- Docs: PocketID client setup (redirect URI, groups claim, allowed groups), environment
  variables, Unraid/Docker Compose example, bridge setup with a key URL.
- Two pull requests:
  1. Sign-in, users, account separation, keys, migration, isolation sweep.
  2. Sharing (grants, switcher, shared-with-me, share UI).
- New code is kept in separate modules (`spoolman/auth/`, `spoolman/access/`) where
  possible to limit merge conflicts with upstream.
