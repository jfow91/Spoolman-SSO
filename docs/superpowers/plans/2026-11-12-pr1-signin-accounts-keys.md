# PR 1: PocketID sign-in, separate accounts, printer keys — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every request to Spoolman is made by a signed-in PocketID user or a personal key, and sees and changes only that user's own account.

**Architecture:** New `spoolman/auth/` package (OIDC client, sessions, users, keys) and `spoolman/access/` package (principal resolution, `Scope`, the single owner-filter helper). An outer ASGI middleware resolves the principal for every HTTP/websocket request; a FastAPI dependency turns it into a `Scope` that every database function now takes. Sharing (PR 2) later widens `Scope`; nothing in this PR reads another account.

**Tech Stack:** Python 3.10+, FastAPI 0.115, SQLAlchemy 2 async, Alembic, `authlib~=1.6` (OIDC client + JWT validation), `itsdangerous~=2.2` (signed short-lived login-state cookie), SvelteKit client_v2, pytest/pytest-asyncio, docker-compose integration matrix (sqlite, postgres, mariadb, cockroachdb).

**Spec:** `docs/superpowers/specs/2026-11-12-multiuser-pocketid-design.md` (sections 2, 3, 5, 6 except account switcher, 7, 8). Sharing (section 4), the account switcher, `X-Spoolman-Account`, Shared-with-me and share UI are PR 2.

## Global Constraints

- Env vars, exact names: `SPOOLMAN_OIDC_ISSUER`, `SPOOLMAN_OIDC_CLIENT_ID`, `SPOOLMAN_OIDC_CLIENT_SECRET` / `SPOOLMAN_OIDC_CLIENT_SECRET_FILE`, `SPOOLMAN_PUBLIC_URL`, `SPOOLMAN_SESSION_SECRET` / `SPOOLMAN_SESSION_SECRET_FILE`, `SPOOLMAN_OIDC_ADMIN_GROUP` (default `spoolman_admins`).
- Redirect URI is exactly `<SPOOLMAN_PUBLIC_URL>/auth/callback`; never derived from the request.
- Scopes requested: `openid profile email groups`. Authorization code + PKCE (S256).
- Session cookie: name `spoolman_session`, HTTP-only, `Secure` (unless `SPOOLMAN_PUBLIC_URL` is `http://`), `SameSite=Lax`, 30-day sliding expiry; server-side `session` row.
- Session secret ≥ 32 bytes; startup fails otherwise.
- Keys: 32 random bytes, base62; stored as SHA-256 hex; display prefix = first 8 chars; `last_used` written at most once per 60 s per key.
- Key transports: path prefix `/k/<key>/` (after `SPOOLMAN_BASE_PATH`), `Authorization: Bearer <key>`, `X-Api-Key: <key>`.
- Keys reach `/api/v1/**` and websockets only, and never `/api/v1/keys*`, `/api/v1/admin/*`, `/api/v1/me/claim`, `/auth/*`, or web pages.
- Unauthenticated: page navigation → 302 to `/auth/login?next=<path>`; API → 401 JSON; websocket → close code 4401.
- Out-of-scope records → 404, never 403.
- `base_url` is the only app-wide setting; it moves to `app_setting`. All other registered settings are per account.
- The legacy React client is disabled in this fork; `SPOOLMAN_LEGACY_CLIENT` is ignored with a startup warning.
- Placeholder owner for pre-existing rows: a `user` row with `oidc_sub = "__unclaimed__"`, `disabled = true`.
- Admin = `SPOOLMAN_OIDC_ADMIN_GROUP` present in the `groups` claim at last login. Admins get no data access to other accounts.

## Review Focus

1. **`SPOOLMAN_BASE_PATH` set (e.g. `/spoolman`)** — login redirect, callback, cookie path and `/spoolman/k/<key>/api/v1/...` must all work. Pinned in Task 5 and Task 6.
2. **PocketID not reachable yet when the container starts** (common on Unraid boot order) — discovery retries for up to 60 s before startup fails, rather than failing on the first attempt. Pinned in Task 1.
3. **ID token without `email` or `groups` claim** (PocketID user with no email, client without groups scope) — sign-in succeeds, `email = None`, `is_admin = False`. Pinned in Task 4.
4. **Brand-new user with zero settings rows** — every settings read returns defaults and custom-field lists are empty, not 500. Pinned in Task 9.
5. **TLS terminated at a reverse proxy (Spoolman sees `http://`)** — redirect URI still uses `SPOOLMAN_PUBLIC_URL` (`https://...`), and the cookie is still `Secure`. Pinned in Task 4.

---

## File Structure

New:
- `spoolman/auth/__init__.py`
- `spoolman/auth/config.py` — load and validate OIDC/session env, discovery with retry.
- `spoolman/auth/users.py` — upsert user from claims, lookups.
- `spoolman/auth/sessions.py` — create/validate/touch/delete server-side sessions.
- `spoolman/auth/keys.py` — generate/hash/verify keys, `last_used` throttle.
- `spoolman/auth/routes.py` — `/auth/login`, `/auth/callback`, `/auth/logout`.
- `spoolman/access/__init__.py`
- `spoolman/access/principal.py` — `Principal`, `PrincipalMiddleware` (key prefix stripping + resolution).
- `spoolman/access/scope.py` — `Level`, `Scope`, `get_scope`, `require_level`, `owned()` helper, `ensure_same_account()`.
- `spoolman/api/v1/account.py` — `/me`, `/me/claim`, `/keys`, `/admin/users`.
- `migrations/versions/2026_11_12_1200-a1b2c3d4e5f6_multiuser.py`, `migrations/versions/2026_11_12_1201-b2c3d4e5f6a7_multiuser_constraints.py`
- `tests/support/__init__.py`, `tests/support/fake_oidc.py` — fake PocketID (ASGI app) shared by unit and integration tests.
- `tests/support/dbsession.py` — in-memory SQLite session fixture (pattern of `tests/test_tag_autocreate.py`).
- `tests/test_auth_config.py`, `tests/test_auth_flow.py`, `tests/test_keys.py`, `tests/test_principal.py`, `tests/test_scope_*.py`, `tests/test_migration_multiuser.py`
- `tests_integration/fake_oidc/Dockerfile`
- `tests_integration/tests/test_isolation_sweep.py`
- `client_v2/src/lib/api/account.ts`, `client_v2/src/lib/components/UserMenu.svelte`, `client_v2/src/routes/settings/keys/+page.svelte`, `client_v2/src/routes/settings/users/+page.svelte`, `client_v2/src/lib/components/ClaimDataPrompt.svelte`
- `docs/multiuser/pocketid-setup.md`, `docs/multiuser/docker-compose.example.yml`

Modified: `pyproject.toml`, `uv.lock`, `spoolman/env.py`, `spoolman/main.py`, `spoolman/security.py`, `spoolman/ws.py`, `spoolman/settings.py`, `spoolman/extra_fields.py`, `spoolman/export.py`, `spoolman/scanrelay.py`, `spoolman/prometheus/*`, `spoolman/database/{models,vendor,filament,spool,tag,setting,search}.py`, every `spoolman/api/v1/*.py` route module, `tests_integration/tests/conftest.py`, `tests_integration/docker-compose-*.yml`, `tests_frontend_v2/docker-compose.yml`, `.github/workflows/ci.yml`, `client_v2/src/routes/+layout.svelte`, `README.md`.

---

### Task 1: Auth configuration and startup gate

**Files:**
- Create: `spoolman/auth/__init__.py`, `spoolman/auth/config.py`
- Modify: `pyproject.toml` (add `authlib~=1.6`, `itsdangerous~=2.2`), `uv.lock`, `spoolman/main.py` (startup), `spoolman/env.py` (`is_legacy_client_enabled` returns False + warning)
- Test: `tests/test_auth_config.py`

**Interfaces:**
- Produces:
  - `class AuthConfigError(Exception)`
  - `@dataclass(frozen=True) class OidcConfig: issuer: str; client_id: str; client_secret: str; public_url: str; base_path: str; session_secret: bytes; admin_group: str; redirect_uri: str; cookie_secure: bool`
  - `def load_oidc_config() -> OidcConfig` — reads env (with `_FILE` variants), validates; raises `AuthConfigError` naming every missing/invalid variable at once.
  - `@dataclass(frozen=True) class OidcProvider: authorization_endpoint: str; token_endpoint: str; jwks_uri: str; end_session_endpoint: str | None; issuer: str`
  - `async def discover(cfg: OidcConfig, http: httpx.AsyncClient, *, timeout_s: float = 60.0, interval_s: float = 2.0) -> OidcProvider`
  - `def get_auth() -> tuple[OidcConfig, OidcProvider]` — module-level holder set at startup.

- [ ] **Step 1: Write failing tests**

```python
def test_missing_vars_listed_together(monkeypatch):
    for v in ["SPOOLMAN_OIDC_ISSUER","SPOOLMAN_OIDC_CLIENT_ID","SPOOLMAN_OIDC_CLIENT_SECRET","SPOOLMAN_PUBLIC_URL","SPOOLMAN_SESSION_SECRET"]:
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(AuthConfigError) as e:
        load_oidc_config()
    for v in ["SPOOLMAN_OIDC_ISSUER","SPOOLMAN_OIDC_CLIENT_ID","SPOOLMAN_OIDC_CLIENT_SECRET","SPOOLMAN_PUBLIC_URL","SPOOLMAN_SESSION_SECRET"]:
        assert v in str(e.value)

def test_short_session_secret_rejected(full_env, monkeypatch):
    monkeypatch.setenv("SPOOLMAN_SESSION_SECRET", "x" * 31)
    with pytest.raises(AuthConfigError, match="32"):
        load_oidc_config()

def test_secret_file_variant(full_env, monkeypatch, tmp_path): ...  # _FILE wins, value stripped of trailing newline

def test_redirect_uri_from_public_url_with_base_path(full_env, monkeypatch):
    monkeypatch.setenv("SPOOLMAN_PUBLIC_URL", "https://s.lab8.au/")
    monkeypatch.setenv("SPOOLMAN_BASE_PATH", "/spoolman")
    cfg = load_oidc_config()
    assert cfg.redirect_uri == "https://s.lab8.au/spoolman/auth/callback"
    assert cfg.cookie_secure is True
    assert cfg.admin_group == "spoolman_admins"

def test_http_public_url_not_secure(full_env, monkeypatch):
    monkeypatch.setenv("SPOOLMAN_PUBLIC_URL", "http://10.0.0.5:7912")
    assert load_oidc_config().cookie_secure is False

async def test_discover_retries_until_up(full_env):  # Review Focus 2
    # MockTransport: first 3 calls raise ConnectError, then serve fake_oidc discovery doc
    provider = await discover(load_oidc_config(), client, timeout_s=5, interval_s=0.01)
    assert provider.token_endpoint.endswith("/token")

async def test_discover_gives_up(full_env):
    with pytest.raises(AuthConfigError, match="discovery"):
        await discover(load_oidc_config(), always_failing_client, timeout_s=0.05, interval_s=0.01)
```

`full_env` fixture sets all five variables to valid values (issuer `http://fake-oidc`).

- [ ] **Step 2: Run** `uv run pytest tests/test_auth_config.py -v` — Expected: FAIL (`ModuleNotFoundError: spoolman.auth`).
- [ ] **Step 3: Implement** `config.py` as above. `public_url` is normalised without trailing slash; `redirect_uri = public_url + base_path + "/auth/callback"`. In `main.startup()`, call `load_oidc_config()` and `discover()` before DB setup; on `AuthConfigError` log the message and `sys.exit(1)`. `is_legacy_client_enabled()` always returns False and logs a warning if the variable is set.
- [ ] **Step 4: Run** `uv run pytest tests/test_auth_config.py -v` — Expected: PASS. Run `uv run pytest tests` — Expected: only `test_env.py` legacy-client cases fail; update them to assert the new behaviour, rerun, all PASS.
- [ ] **Step 5: Commit** `feat(auth): OIDC configuration and startup gate`

---

### Task 2: Fake PocketID for tests

**Files:**
- Create: `tests/support/__init__.py`, `tests/support/fake_oidc.py`, `tests_integration/fake_oidc/Dockerfile`
- Test: `tests/test_fake_oidc.py`

**Interfaces:**
- Produces:
  - `def make_app(issuer: str) -> Starlette` — endpoints `/.well-known/openid-configuration`, `/authorize`, `/token`, `/jwks`, `/end-session`. `/authorize` auto-approves: identity comes from query params `login_sub`, `login_name`, `login_email` (omit = claim absent), `login_groups` (comma list; omit = claim absent); redirects to `redirect_uri?code=…&state=…`. `/token` verifies PKCE S256 and client secret, returns RS256 `id_token` with `nonce`.
  - Constants `CLIENT_ID = "spoolman"`, `CLIENT_SECRET = "test-secret"`.
  - Dockerfile runs `uvicorn tests.support.fake_oidc:app` on port 9000 with `FAKE_OIDC_ISSUER=http://fake-oidc:9000`.

- [ ] **Step 1: Write failing tests** — `test_discovery_doc_fields`, `test_token_rejects_wrong_verifier` (400), `test_id_token_has_nonce_and_claims` (decode with JWKS; `sub`, `name`, `email`, `groups`, `nonce`, `aud == CLIENT_ID`), `test_omitted_email_absent_from_token`.
- [ ] **Step 2: Run** `uv run pytest tests/test_fake_oidc.py -v` — FAIL.
- [ ] **Step 3: Implement** with authlib `JsonWebKey.generate_key("RSA", 2048, is_private=True)` at import and `jwt.encode`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `test: fake PocketID provider`

---

### Task 3: Data model and migration

**Files:**
- Modify: `spoolman/database/models.py`
- Create: `migrations/versions/2026_11_12_1200-a1b2c3d4e5f6_multiuser.py` (down_revision `fe4970567bb3`), `tests/support/dbsession.py`
- Test: `tests/test_migration_multiuser.py`

**Interfaces:**
- Produces (models):
  - `User`: `id`, `oidc_sub: str(255) unique`, `display_name: str(255)`, `email: str(320) | None`, `is_admin: bool`, `disabled: bool`, `created`, `last_login: datetime | None`.
  - `Session`: `id: str(64) PK` (SHA-256 hex of the cookie token), `user_id FK`, `created`, `expires`, `last_seen`.
  - `ApiKey`: `id`, `user_id FK`, `name: str(64)`, `prefix: str(8)`, `hash: str(64) unique`, `level: str(8)` (`"edit"|"view"`), `created`, `last_used: datetime | None`, `revoked: bool`.
  - `AppSetting`: `key: str(64) PK`, `value: Text`, `last_updated`.
  - `owner_id: int FK user.id NOT NULL, index` on `Vendor`, `Filament`, `Spool`, `Tag`, `Setting`.
  - `Setting` PK becomes `(owner_id, key)`.
  - `Tag` unique index changes from `uid` to `(owner_id, uid)`.
  - `UNCLAIMED_SUB = "__unclaimed__"` in `models.py`.
  - `tests/support/dbsession.py`: `db` async fixture (in-memory SQLite, `create_all`) and `async def make_user(db, sub="a", name="A", admin=False) -> User`.

- [ ] **Step 1: Write failing tests** in `tests/test_migration_multiuser.py` using a temp-file SQLite and `alembic` programmatic API:
  - `test_upgrade_populated_db_assigns_unclaimed_owner`: upgrade to `fe4970567bb3`, insert 1 vendor, 1 filament, 1 spool, 1 tag, settings `currency` and `base_url`; upgrade head; assert one `user` row with `oidc_sub == "__unclaimed__"`, `disabled` true; all five rows carry its id; `base_url` row moved to `app_setting` and absent from `setting`.
  - `test_upgrade_empty_db_creates_unclaimed_user_only`.
  - `test_same_tag_uid_allowed_in_two_accounts` (models-level insert, two owners, same uid — succeeds; same owner twice — `IntegrityError`).
  - `test_downgrade_roundtrip` (upgrade head → downgrade `fe4970567bb3` → upgrade head succeeds).
- [ ] **Step 2: Run** `uv run pytest tests/test_migration_multiuser.py -v` — FAIL.
- [ ] **Step 3: Implement** the models and migration. Follow the existing DDL/DML split pattern from `304a32906234` for cockroachdb: revision creates tables and adds `owner_id` as nullable, inserts the unclaimed user, backfills, then alters to NOT NULL and swaps the `setting` PK and `tag` unique index in a second revision `2026_11_12_1201-b2c3d4e5f6a7_multiuser_constraints.py`.
- [ ] **Step 4: Run** the new tests — PASS. Run `uv run pytest tests` — `test_tag_autocreate.py` fails on missing `owner_id`; switch its fixture to `tests/support/dbsession.py` and create rows with a `make_user` owner (Task 10 changes the function signatures), rerun — PASS.
- [ ] **Step 5: Commit** `feat(db): users, sessions, keys, ownership columns`

---

### Task 4: OIDC sign-in, users, sessions

**Files:**
- Create: `spoolman/auth/users.py`, `spoolman/auth/sessions.py`, `spoolman/auth/routes.py`
- Modify: `spoolman/main.py` (include `/auth` router at `base_path + "/auth"`)
- Test: `tests/test_auth_flow.py`

**Interfaces:**
- Consumes: `get_auth()` (Task 1), fake OIDC (Task 2), models (Task 3).
- Produces:
  - `async def upsert_from_claims(db, claims: dict, admin_group: str) -> User` — keyed on `sub`; sets `display_name = claims.get("name") or claims.get("preferred_username") or sub`, `email = claims.get("email")`, `is_admin = admin_group in claims.get("groups", [])`, `last_login = now`.
  - `async def create_session(db, user_id: int) -> str` (returns raw cookie token; stores hash).
  - `async def resolve_session(db, raw_token: str) -> User | None` — None if missing/expired/user disabled; extends `expires` to now+30d when `last_seen` older than 1 h.
  - `async def delete_session(db, raw_token: str) -> None`
  - `async def delete_user_sessions(db, user_id: int) -> None`
  - Routes: `GET /auth/login?next=` (stores state, nonce, PKCE verifier, `next` in a 10-minute `itsdangerous` signed cookie `spoolman_login`), `GET /auth/callback`, `GET|POST /auth/logout`.
  - `next` accepted only if it is a relative path starting with `/` and not `//`; otherwise `/`.

- [ ] **Step 1: Write failing tests** (in-process app via `httpx.AsyncClient(transport=ASGITransport(app))`, fake OIDC mounted through an `httpx.MockTransport` routing `http://fake-oidc` to `make_app`):
  - `test_full_login_sets_cookie_and_creates_user` — follow redirects manually; final 302 to `next`; `Set-Cookie: spoolman_session` with HttpOnly, SameSite=Lax; one `User` with sub.
  - `test_second_login_updates_name_and_admin` — groups `spoolman_admins` → `is_admin` True; next login without the group → False.
  - `test_login_without_email_or_groups_claim` (Review Focus 3) — succeeds; `email is None`, `is_admin is False`.
  - `test_callback_rejects_bad_state` (400) and `test_callback_rejects_replayed_code`.
  - `test_open_redirect_blocked` — `next=//evil.com` and `next=https://evil.com` both land on `/`.
  - `test_redirect_uri_uses_public_url_behind_proxy` (Review Focus 5) — app reached as `http://internal:7912`, `SPOOLMAN_PUBLIC_URL=https://s.lab8.au`; the `/authorize` redirect carries `redirect_uri=https://s.lab8.au/auth/callback`; session cookie has `Secure`.
  - `test_base_path_login` (Review Focus 1) — `SPOOLMAN_BASE_PATH=/spoolman`; `/spoolman/auth/login` works end to end; cookie `Path=/spoolman`.
  - `test_disabled_user_session_rejected`, `test_expired_session_rejected`, `test_logout_deletes_session_and_redirects_to_end_session`.
- [ ] **Step 2: Run** `uv run pytest tests/test_auth_flow.py -v` — FAIL.
- [ ] **Step 3: Implement** with authlib `AsyncOAuth2Client` (code_challenge_method `S256`) and `authlib.jose.jwt.decode` against the provider JWKS, checking `iss`, `aud`, `exp`, `nonce`. Login of the unclaimed placeholder sub is impossible because `sub` comes from PocketID; still, `upsert_from_claims` raises if `sub == UNCLAIMED_SUB`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(auth): PocketID sign-in and sessions`

---

### Task 5: Printer keys

**Files:**
- Create: `spoolman/auth/keys.py`
- Test: `tests/test_keys.py`

**Interfaces:**
- Produces:
  - `def generate_key() -> str` — 43-char base62 of 32 random bytes.
  - `def hash_key(raw: str) -> str` — SHA-256 hex.
  - `async def create_key(db, user_id: int, name: str, level: Literal["edit","view"]) -> tuple[ApiKey, str]` — returns row and raw key (only time it exists).
  - `async def verify_key(db, raw: str) -> tuple[User, ApiKey] | None` — None if unknown, revoked, or user disabled; updates `last_used` when null or older than 60 s.
  - `async def list_keys(db, user_id) -> list[ApiKey]`, `async def revoke_key(db, user_id, key_id) -> None` (raises `ItemNotFoundError` if not the user's).

- [ ] **Step 1: Write failing tests** — `test_key_format` (regex `^[0-9A-Za-z]{43}$`), `test_hash_stored_not_raw`, `test_verify_roundtrip`, `test_revoked_key_rejected`, `test_disabled_user_key_rejected`, `test_last_used_throttled` (two verifies 1 s apart write once; freeze time with monkeypatched `utcnow`), `test_revoke_other_users_key_not_found`.
- [ ] **Step 2: Run** `uv run pytest tests/test_keys.py -v` — FAIL.
- [ ] **Step 3: Implement.** Use `secrets.token_bytes(32)` and integer base62 encoding left-padded to 43.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(auth): personal printer keys`

---

### Task 6: Principal resolution middleware

**Files:**
- Create: `spoolman/access/__init__.py`, `spoolman/access/principal.py`
- Modify: `spoolman/main.py` (register `PrincipalMiddleware` so it runs inside `TrustedHostMiddleware` and outside `TrustedOriginMiddleware`, which needs `principal.via`), `spoolman/security.py` (CSRF origin check applies only when the principal came from a session cookie)
- Test: `tests/test_principal.py`

**Interfaces:**
- Consumes: `resolve_session` (Task 4), `verify_key` (Task 5).
- Produces:
  - `@dataclass(frozen=True) class Principal: user_id: int; display_name: str; is_admin: bool; via: Literal["session","key"]; key_level: Literal["edit","view"] | None`
  - `class PrincipalMiddleware` (pure ASGI) — for `http` and `websocket` scopes: strips `<base>/k/<key>` from `scope["path"]`/`raw_path` (adds it to `root_path`), else reads `Authorization: Bearer` or `X-Api-Key`, else the `spoolman_session` cookie; stores `scope["state"]["principal"] = Principal | None`.
  - Enforcement inside the middleware:
    - public without a principal: `<base>/auth/*`, `<base>/api/v1/health`, `<base>/config.js`, static assets under `<base>/_app/`.
    - no principal + other path under `/api/` → 401 `{"message":"Not signed in"}`; websocket → close 4401.
    - no principal + any other path → 302 to `<base>/auth/login?next=<path>`.
    - `via == "key"` + path not under `/api/v1/` → 403; + path under the key-forbidden list in Global Constraints → 403.
  - `def get_principal(request: Request) -> Principal` — FastAPI dependency; raises 401 if absent.
  - `def require_session(principal = Depends(get_principal)) -> Principal` — 403 when `via == "key"`.

- [ ] **Step 1: Write failing tests** (app with a dummy `/api/v1/echo` route returning the principal, a dummy websocket, and a dummy page):
  - `test_no_auth_api_401`, `test_no_auth_page_redirects_with_next`, `test_no_auth_ws_closed_4401`.
  - `test_key_in_url_prefix` — `GET /k/<key>/api/v1/echo` → principal via key; route sees path `/api/v1/echo`.
  - `test_key_in_url_prefix_with_base_path` (Review Focus 1) — `/spoolman/k/<key>/api/v1/echo`.
  - `test_key_bearer_header`, `test_key_x_api_key_header`, `test_key_ws_prefix`.
  - `test_key_cannot_open_pages` (403), `test_key_cannot_manage_keys` (`/api/v1/keys` → 403).
  - `test_bad_key_401_not_fallthrough_to_cookie` — an invalid key plus a valid cookie is 401.
  - `test_csrf_still_enforced_for_cookie_post_cross_origin`, `test_csrf_not_applied_to_key_post`.
- [ ] **Step 2: Run** `uv run pytest tests/test_principal.py tests/test_security.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS, including the existing `tests/test_security.py` and `tests/test_host_validation.py`.
- [ ] **Step 5: Commit** `feat(access): resolve principal from session or key`

---

### Task 7: Scope and the single owner filter

**Files:**
- Create: `spoolman/access/scope.py`
- Test: `tests/test_scope.py`

**Interfaces:**
- Consumes: `Principal`, `get_principal` (Task 6).
- Produces:
  - `class Level(IntEnum): VIEW = 1; EDIT = 2; OWNER = 3`
  - `@dataclass(frozen=True) class Scope: principal: Principal; account_id: int; level: Level`
  - `def get_scope(principal = Depends(get_principal)) -> Scope` — PR 1: `account_id = principal.user_id`; `level = OWNER` for sessions, `EDIT` or `VIEW` from `key_level` for keys.
  - `def require_level(min_level: Level) -> Callable[..., Scope]` — dependency factory; 403 `{"message":"View only"}` below `min_level`.
  - `def owned(stmt: Select, model: type[Base], scope: Scope) -> Select` — adds `model.owner_id == scope.account_id`. **The only place an owner filter is written.**
  - `def ensure_same_account(scope: Scope, *rows: Base | None) -> None` — raises `ItemNotFoundError` if any non-None row's `owner_id != scope.account_id`.
  - `def scope_for_user(user_id: int) -> Scope` — test/internal helper with `Level.OWNER`.

- [ ] **Step 1: Write failing tests** — `test_owned_filters_rows` (two owners' vendors in `db`; `owned(select(Vendor), Vendor, scope_a)` returns only A's), `test_require_level_blocks_view_key_on_write` (403), `test_ensure_same_account_raises_not_found`, `test_key_scope_levels`.
- [ ] **Step 2: Run** `uv run pytest tests/test_scope.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(access): Scope and owner filter`

---

### Task 8: Scope vendors and filaments

**Files:**
- Modify: `spoolman/database/vendor.py`, `spoolman/database/filament.py`, `spoolman/api/v1/vendor.py`, `spoolman/api/v1/filament.py`, `spoolman/api/v1/externaldb.py` (imports create in scope)
- Test: `tests/test_scope_vendor_filament.py`

**Interfaces:**
- Consumes: `Scope`, `owned`, `ensure_same_account`, `require_level` (Task 7).
- Produces: every public function in `database/vendor.py` and `database/filament.py` takes `scope: Scope` as its second parameter (after `db`). `create` sets `owner_id = scope.account_id`. `get_by_id` uses `owned(...)` and raises `ItemNotFoundError` when filtered out. `filament.create/update` call `ensure_same_account(scope, vendor)`.
- Route rule: GET/websocket routes depend on `get_scope`; POST/PATCH/DELETE depend on `require_level(Level.EDIT)`.

- [ ] **Step 1: Write failing tests** — for each of `find`, `get_by_id`, `update`, `delete`: B cannot see/alter A's vendor/filament (`ItemNotFoundError`); `test_filament_with_other_accounts_vendor_rejected` (create and update); `test_same_vendor_name_in_two_accounts_ok`; `test_externaldb_import_lands_in_scope`.
- [ ] **Step 2: Run** `uv run pytest tests/test_scope_vendor_filament.py -v` — FAIL.
- [ ] **Step 3: Implement.** No route builds `owner_id ==` itself; grep check in Step 4.
- [ ] **Step 4: Run** the tests — PASS. Run `grep -rn "owner_id ==" spoolman | grep -v access/scope.py` — Expected: no output.
- [ ] **Step 5: Commit** `feat(access): scope vendors and filaments`

---

### Task 9: Scope spools, settings, extra fields, search, export

**Files:**
- Modify: `spoolman/database/spool.py` (all public functions incl. `find_groups`, `use_*`, `measure`, `find_locations`, `find_lot_numbers`, `clear_extra_field`), `spoolman/database/setting.py`, `spoolman/settings.py` (`base_url` → `AppSetting`), `spoolman/extra_fields.py`, `spoolman/database/search.py`, `spoolman/export.py`, `spoolman/api/v1/{spool,setting,field,search,export,other}.py`
- Test: `tests/test_scope_spool_settings.py`

**Interfaces:**
- Consumes: Task 7, Task 8 signatures.
- Produces: same `scope` second-parameter rule for all functions above. `get_extra_fields(db, scope, entity_type)`. `database.setting.get(db, scope, definition)` returns the registered default when no row exists for the account. `get_app_setting(db, key) / set_app_setting(db, key, value)` for `base_url`; setting `base_url` requires `principal.is_admin`.

- [ ] **Step 1: Write failing tests:**
  - spool isolation for find, find_groups, get, update, delete, use_weight, use_length, measure, locations, lot numbers.
  - `test_spool_with_other_accounts_filament_rejected`.
  - `test_new_user_settings_defaults` (Review Focus 4): fresh user, `GET` every key in `SETTINGS` returns its default; extra fields for vendor/filament/spool are `[]`.
  - `test_settings_per_account` — A sets `currency` `"AUD"`, B still `"EUR"`.
  - `test_extra_field_definitions_per_account`.
  - `test_search_scoped`, `test_export_scoped` (CSV and JSON contain only A's rows).
  - `test_base_url_admin_only` (non-admin 403).
- [ ] **Step 2: Run** `uv run pytest tests/test_scope_spool_settings.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; `uv run pytest tests` — all PASS (update `test_measure.py`, `test_export.py`, `test_metrics.py` fixtures to pass a scope); repeat the `owner_id ==` grep — no output.
- [ ] **Step 5: Commit** `feat(access): scope spools, settings, fields, search, export`

---

### Task 10: Scope tags and scan relay

**Files:**
- Modify: `spoolman/database/tag.py`, `spoolman/api/v1/tag.py`, `spoolman/scanrelay.py`, `tests/test_tag_autocreate.py`, `tests/test_scan_relay.py`
- Test: `tests/test_scope_tags.py`

**Interfaces:**
- Produces: `find_spool_by_uid(db, scope, uid)`, `create_spool_from_decoded_tag(db, scope, decoded)`, and every tag function take `scope`. Scan relay keys its channels by `account_id`: a scan made by principal P is delivered only to sockets of P's account.

- [ ] **Step 1: Write failing tests** — `test_same_uid_two_accounts_resolve_to_own_spool`, `test_scan_unknown_to_scanner_even_if_other_account_has_it` (404 / create-path for scanner), `test_relay_not_delivered_cross_account`, `test_autocreate_creates_vendor_filament_spool_in_scope`.
- [ ] **Step 2: Run** `uv run pytest tests/test_scope_tags.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS; `uv run pytest tests` — PASS.
- [ ] **Step 5: Commit** `feat(access): scope tags and scan relay`

---

### Task 11: Websocket visibility and metrics owner label

**Files:**
- Modify: `spoolman/ws.py`, every websocket route in `spoolman/api/v1/*.py`, `spoolman/prometheus/*`
- Test: `tests/test_ws_broadcast.py` (extend), `tests/test_metrics.py` (extend)

**Interfaces:**
- Produces: `SubscriptionTree.add(path, websocket, scope: Scope)`; `send(path, evt, owner_id: int)` delivers only to sockets with `scope.account_id == owner_id`. Every `*_changed` emitter passes the record's `owner_id`. Per-account Prometheus series gain label `owner` = slug of `display_name` (lowercase, `[a-z0-9_]`, collisions suffixed `_<user_id>`).

- [ ] **Step 1: Write failing tests** — `test_event_not_delivered_to_other_account` (root `()` subscriber in B receives nothing for A's spool change), `test_event_delivered_to_owner`, `test_metrics_have_owner_label`, `test_owner_label_collision_suffixed`.
- [ ] **Step 2: Run** `uv run pytest tests/test_ws_broadcast.py tests/test_metrics.py -v` — FAIL.
- [ ] **Step 3: Implement.** Keep the existing per-subscriber isolation in `send`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(access): per-account live updates and metrics`

---

### Task 12: Account API — me, keys, claim, admin users

**Files:**
- Create: `spoolman/api/v1/account.py`
- Modify: `spoolman/api/v1/router.py`
- Test: `tests/test_account_api.py`

**Interfaces:**
- Consumes: Tasks 4–7.
- Produces (all `require_session` except `/me` which accepts keys):
  - `GET /api/v1/me` → `{id, display_name, email, is_admin, via, can_claim: bool}`; `can_claim` is true when principal is admin and any row is owned by the unclaimed user.
  - `POST /api/v1/me/claim` (admin) → reassigns every `owner_id` of the unclaimed user to the caller in one transaction; returns counts per table; second call → 409.
  - `GET /api/v1/keys` → `[{id, name, prefix, level, created, last_used}]`; `POST /api/v1/keys {name, level}` → `{..., key: "<raw>"}` (201); `DELETE /api/v1/keys/{id}` → 204.
  - `GET /api/v1/admin/users` (admin) → `[{id, display_name, email, is_admin, disabled, last_login}]` (excludes unclaimed); `POST /api/v1/admin/users/{id}/disable` / `/enable`; `POST /api/v1/admin/users/{id}/revoke-access` → deletes that user's sessions and revokes their keys.
  - Admin endpoints refuse acting on oneself for `disable` (400).

- [ ] **Step 1: Write failing tests** — `test_me_session`, `test_me_key`, `test_claim_moves_all_rows_and_is_once` (409 second time), `test_claim_requires_admin` (403), `test_key_crud`, `test_key_raw_only_returned_on_create`, `test_admin_users_requires_admin`, `test_disable_user_kills_sessions_and_keys`, `test_admin_cannot_disable_self`, `test_admin_cannot_read_other_accounts_spools` (admin B, A's spool → 404).
- [ ] **Step 2: Run** `uv run pytest tests/test_account_api.py -v` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(api): me, keys, claim, admin users`

---

### Task 13: Integration harness and route isolation sweep

**Files:**
- Modify: `tests_integration/tests/conftest.py`, all four `tests_integration/docker-compose-*.yml`, `tests_integration/Dockerfile` (copy `tests/support/fake_oidc.py`), `tests_integration/requirements.txt`
- Create: `tests_integration/tests/test_isolation_sweep.py`

**Interfaces:**
- Consumes: fake OIDC image (Task 2), key URL prefix (Task 6), `/api/v1/keys` (Task 12).
- Produces in `conftest.py`:
  - `BASE_URL` (old `URL` value).
  - `def login(sub: str, *, name: str | None = None, groups: list[str] | None = None) -> httpx.Client` — runs the browser flow against fake OIDC with cookies; returns a cookie-carrying client.
  - `def make_key(sub: str, level: str = "edit") -> str`
  - `URL = f"{BASE_URL}/k/{make_key('tester-a')}"` computed at import, after an import-time wait for the server (the existing `pytest_sessionstart` wait loop moves into a module-level `_wait_for_server()` called first, and polls `<BASE_URL>/api/v1/health` because `/` now redirects to sign-in). Existing 939 call sites keep using `URL` unchanged and now run as user `tester-a`.
- Compose: add service `fake-oidc` (port 9000); spoolman env `SPOOLMAN_OIDC_ISSUER=http://fake-oidc:9000`, `SPOOLMAN_OIDC_CLIENT_ID=spoolman`, `SPOOLMAN_OIDC_CLIENT_SECRET=test-secret`, `SPOOLMAN_PUBLIC_URL=http://spoolman:8000`, `SPOOLMAN_SESSION_SECRET=<64 x's>`; `depends_on: [fake-oidc]`.

- [ ] **Step 1: Write the sweep test.** `test_isolation_sweep.py`:
  - fixture `a` = `make_key("sweep-a")`, `b` = `make_key("sweep-b")`; A creates one vendor, filament, spool (with extra field and tag), location setting.
  - `GET /api/v1/openapi.json` via A; enumerate every `(method, path)`; substitute `{vendor_id}`, `{filament_id}`, `{spool_id}`, `{tag_id}`, `{key}`, `{uid}` with A's ids.
  - For each, call as B with a minimal valid body (built from the OpenAPI request schema's required fields).
  - Assert: status in {404, 403, 401} **or** (route in `ALLOWED_CROSS_ACCOUNT_OK`, an explicit list of routes that only touch the caller's own account, e.g. list endpoints) **and** the response body contains none of A's ids or names.
  - After the loop, re-read A's records as A and assert unchanged.
  - `test_every_route_classified`: fails if any OpenAPI route is neither id-parameterised nor in `ALLOWED_CROSS_ACCOUNT_OK` nor in `PUBLIC_OR_SELF_ROUTES` — so a new upstream route forces a decision.
  - Websockets: for each websocket path in `router.py`, connect as B, trigger a change as A, assert B receives nothing within 1 s.
- [ ] **Step 2: Run** `uv run python tests_integration/run.py sqlite` — Expected: sweep PASS if Tasks 8–11 are complete; any failure names the leaking route. Fix the route (using `owned`/`ensure_same_account`), never the test.
- [ ] **Step 3: Run the full matrix** `uv run python tests_integration/run.py` — Expected: all four databases PASS.
- [ ] **Step 4: Commit** `test: integration harness with fake PocketID and route isolation sweep`

---

### Task 14: Web client — user menu, keys page, users page, claim prompt

**Files:**
- Create: `client_v2/src/lib/api/account.ts`, `client_v2/src/lib/components/UserMenu.svelte`, `client_v2/src/lib/components/ClaimDataPrompt.svelte`, `client_v2/src/routes/settings/keys/+page.svelte`, `client_v2/src/routes/settings/users/+page.svelte`, `client_v2/src/lib/api/account.test.ts`
- Modify: `client_v2/src/routes/+layout.svelte`, settings navigation, `client_v2/messages/en.json` (new strings; other locales untouched — guard-translations workflow forbids manual non-English edits)
- Modify: `tests_frontend_v2/docker-compose.yml` (fake-oidc service + env as Task 13), `tests_frontend_v2/tests/` login helper

**Interfaces:**
- Consumes: `/api/v1/me`, `/api/v1/me/claim`, `/api/v1/keys`, `/api/v1/admin/users*` (Task 12).
- Produces: `getMe(): Promise<Me>`, `claimData()`, `listKeys()`, `createKey(name, level): Promise<{key: string, ...}>`, `revokeKey(id)`, `listUsers()`, `setUserDisabled(id, disabled)`, `revokeUserAccess(id)` in `account.ts`.
- UI decisions:
  - UserMenu in the top bar: display name, "Printer keys", "Users" (admins only), "Sign out" (navigates to `<base>/auth/logout`).
  - Keys page: name + level (Edit/View) form; on create, a one-time dialog shows the key, the URL form `<origin><base>/k/<key>/` with copy buttons, and the warning "Keys in a URL can appear in proxy logs. If your bridge supports headers, use `Authorization: Bearer <key>` instead."; list with prefix, level, created, last used, Revoke.
  - ClaimDataPrompt: shown once per load when `me.can_claim`; "This Spoolman has existing data from before sign-in was added. Make it yours?" → Claim / Not now.
  - The existing reload-on-401 in `auth.ts` stays unchanged.

- [ ] **Step 1: Write failing tests** — `account.test.ts` (vitest, mocked fetch): `createKey returns raw key once`, `getMe parses can_claim`. Playwright in `tests_frontend_v2`: `signs in via fake PocketID and sees name in menu`, `creates key and sees URL form`, `non-admin has no Users link`.
- [ ] **Step 2: Run** `cd client_v2 && npm test` and `uv run python tests_frontend_v2/run.py` — FAIL.
- [ ] **Step 3: Implement.** Follow existing settings page components and Paraglide message usage.
- [ ] **Step 4: Run** both — PASS; `cd client_v2 && npm run check && npm run lint` — clean.
- [ ] **Step 5: Commit** `feat(client): user menu, printer keys, users, claim prompt`

---

### Task 15: CI, image publishing, docs

**Files:**
- Modify: `.github/workflows/ci.yml`, `README.md`
- Create: `docs/multiuser/pocketid-setup.md`, `docs/multiuser/docker-compose.example.yml`

**Interfaces:**
- Produces: image `ghcr.io/jfow91/spoolman-sso:latest` (default branch) and `:sha-<short>`.

- [ ] **Step 1: CI changes** — `GHCR_IMAGE: jfow91/spoolman-sso`; remove the legacy `client` build and the `test-frontend` (legacy) job; keep `test-frontend-v2`; ensure `publish-images` runs on pushes to the default branch of the fork and tags `latest`; disable upstream-only workflows (`apidocs.yml`, `sponsors.yml`, `sponsor-label.yml`, `issue-manager.yml`, `weblate-automerge.yml`, `trigger-release.yml`) by deleting them.
- [ ] **Step 2: Docs** — `pocketid-setup.md`: create OIDC client in PocketID (name Spoolman, callback `https://<host>/auth/callback`, PKCE on), create group `spoolman_admins` and add yourself, restrict the client to allowed groups, copy client ID/secret; env var table from Global Constraints; first sign-in and "Claim existing data"; creating a key and pasting `https://<host>/k/<key>/` into a bridge. `docker-compose.example.yml` with all env vars and a data volume. README top section: what this fork adds, link to docs, upstream credit.
- [ ] **Step 3: Verify** — push the branch, open PR 1 into `jfow91/Spoolman-SSO` default branch; Expected: CI green on all four DB integration jobs and frontend v2 job.
- [ ] **Step 4: Commit** `ci: publish fork image; docs: PocketID setup`
