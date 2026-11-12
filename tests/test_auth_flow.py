"""End-to-end tests for PocketID sign-in, users and sessions, against the fake OIDC provider."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import PlainTextResponse

from spoolman.auth import routes, sessions
from spoolman.auth.config import discover, get_auth, load_oidc_config, set_auth
from spoolman.auth.users import upsert_from_claims
from spoolman.database import models
from spoolman.database.database import get_db_session
from tests.support.dbsession import make_user
from tests.support.fake_oidc import CLIENT_ID, CLIENT_SECRET, make_app

ISSUER = "http://fake-oidc"
OIDC = httpx.ASGITransport(app=make_app(ISSUER))
MakeClient = Callable[..., Awaitable[httpx.AsyncClient]]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _spa(scope, receive, send) -> None:  # noqa: ANN001
    """Stands in for the SPA mount in main.py, which swallows every path below the base path."""
    await PlainTextResponse("spa", 404)(scope, receive, send)


@pytest_asyncio.fixture
async def make_client(
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator:
    """Configure auth from env, build an app wired like main.py and return a client factory."""
    clients: list[httpx.AsyncClient] = []

    async def factory(
        public_url: str = "http://spoolman.test",
        base_path: str = "",
        app_url: str = "http://spoolman.test",
    ) -> httpx.AsyncClient:
        monkeypatch.setenv("SPOOLMAN_OIDC_ISSUER", ISSUER)
        monkeypatch.setenv("SPOOLMAN_OIDC_CLIENT_ID", CLIENT_ID)
        monkeypatch.setenv("SPOOLMAN_OIDC_CLIENT_SECRET", CLIENT_SECRET)
        monkeypatch.setenv("SPOOLMAN_PUBLIC_URL", public_url)
        monkeypatch.setenv("SPOOLMAN_SESSION_SECRET", "s" * 32)
        monkeypatch.setenv("SPOOLMAN_BASE_PATH", base_path)
        cfg = load_oidc_config()
        async with httpx.AsyncClient(transport=OIDC) as http:
            set_auth(cfg, await discover(cfg, http, timeout_s=0))
        monkeypatch.setattr(routes, "http_transport", OIDC)

        # Same order as main.py: the auth router before the catch-all SPA mount.
        app = FastAPI()
        app.include_router(routes.router, prefix=cfg.base_path + "/auth")
        app.mount(cfg.base_path, _spa)

        async def _db() -> AsyncGenerator[AsyncSession, None]:
            yield db

        app.dependency_overrides[get_db_session] = _db
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=app_url)
        clients.append(client)
        return client

    yield factory
    for c in clients:
        await c.aclose()


def _cookies(r: httpx.Response) -> SimpleCookie:
    jar = SimpleCookie()
    for header in r.headers.get_list("set-cookie"):
        jar.load(header)
    return jar


async def _get(client: httpx.AsyncClient, url: str, cookie: str | None = None, **kw) -> httpx.Response:  # noqa: ANN003
    """GET with exactly the given cookie header (the client jar would drop Secure cookies over http)."""
    client.cookies.clear()
    return await client.get(url, headers={"cookie": cookie} if cookie else {}, **kw)


async def _authorize(
    client: httpx.AsyncClient,
    base: str = "",
    nxt: str = "/spools",
    tamper: str = "",
    **identity: str,
) -> tuple[str, str, str]:
    """Run /auth/login and the fake /authorize. Returns (callback path+query, login cookie, authorize URL)."""
    r = await _get(client, f"{base}/auth/login", params={"next": nxt})
    assert r.status_code == 302, r.text
    login_cookie = f"spoolman_login={_cookies(r)['spoolman_login'].value}"
    authorize_url = r.headers["location"]
    async with httpx.AsyncClient(transport=OIDC) as oidc:
        r = await oidc.get(f"{authorize_url}&{urlencode(identity)}".replace("nonce=", tamper or "nonce="))
    assert r.status_code == 302
    cb = urlparse(r.headers["location"])
    return f"{cb.path}?{cb.query}", login_cookie, authorize_url


async def _login(client: httpx.AsyncClient, base: str = "", nxt: str = "/spools", **identity: str) -> httpx.Response:
    callback, login_cookie, _ = await _authorize(client, base, nxt, **identity)
    return await _get(client, callback, login_cookie)


async def _users(db: AsyncSession) -> list[models.User]:
    return list((await db.execute(select(models.User))).scalars())


@pytest.mark.asyncio
async def test_full_login_sets_cookie_and_creates_user(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client()
    r = await _login(client, login_sub="u1", login_name="Ada", login_email="ada@x.test")
    assert r.status_code == 302
    assert r.headers["location"] == "/spools"
    morsel = _cookies(r)["spoolman_session"]
    assert morsel["httponly"]
    assert morsel["samesite"].lower() == "lax"
    assert morsel["path"] == "/"
    assert not morsel["secure"]
    users = await _users(db)
    assert [(u.oidc_sub, u.display_name, u.email) for u in users] == [("u1", "Ada", "ada@x.test")]
    assert (await sessions.resolve_session(db, morsel.value)).id == users[0].id


@pytest.mark.asyncio
async def test_second_login_updates_name_and_admin(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client()
    await _login(client, login_sub="u1", login_name="Ada", login_groups="spoolman_admins,other")
    (user,) = await _users(db)
    assert user.is_admin is True
    await _login(client, login_sub="u1", login_name="Ada L", login_groups="other")
    (user,) = await _users(db)
    assert (user.display_name, user.is_admin) == ("Ada L", False)


@pytest.mark.asyncio
async def test_login_without_email_or_groups_claim(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client()
    r = await _login(client, login_sub="u2")
    assert r.status_code == 302
    (user,) = await _users(db)
    assert (user.display_name, user.email, user.is_admin) == ("u2", None, False)


@pytest.mark.asyncio
async def test_callback_rejects_bad_state(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client()
    callback, login_cookie, _ = await _authorize(client, login_sub="u1")
    code = parse_qs(urlparse(callback).query)["code"][0]
    r = await _get(client, "/auth/callback", login_cookie, params={"code": code, "state": "forged"})
    assert r.status_code == 400
    assert (await _get(client, callback)).status_code == 400  # no login cookie at all
    assert await _users(db) == []


@pytest.mark.asyncio
async def test_callback_rejects_replayed_code(make_client: MakeClient) -> None:
    client = await make_client()
    callback, login_cookie, _ = await _authorize(client, login_sub="u1")
    assert (await _get(client, callback, login_cookie)).status_code == 302
    r = await _get(client, callback, login_cookie)
    assert r.status_code == 400
    assert "spoolman_session" not in _cookies(r)


@pytest.mark.asyncio
@pytest.mark.parametrize("nxt", ["//evil.com", "https://evil.com", "/\\evil.com", "evil"])
async def test_open_redirect_blocked(make_client: MakeClient, nxt: str) -> None:
    client = await make_client()
    r = await _login(client, nxt=nxt, login_sub="u1")
    assert r.status_code == 302
    assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_redirect_uri_uses_public_url_behind_proxy(make_client: MakeClient) -> None:
    client = await make_client(public_url="https://s.lab8.au", app_url="http://internal:7912")
    callback, login_cookie, authorize_url = await _authorize(client, login_sub="u1")
    assert parse_qs(urlparse(authorize_url).query)["redirect_uri"] == ["https://s.lab8.au/auth/callback"]
    r = await _get(client, callback, login_cookie)
    assert r.status_code == 302
    assert _cookies(r)["spoolman_session"]["secure"]


@pytest.mark.asyncio
async def test_base_path_login(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client(base_path="/spoolman")
    callback, login_cookie, authorize_url = await _authorize(client, "/spoolman", "/spoolman/spools", login_sub="u1")
    assert parse_qs(urlparse(authorize_url).query)["redirect_uri"] == ["http://spoolman.test/spoolman/auth/callback"]
    assert callback.startswith("/spoolman/auth/callback?")
    r = await _get(client, callback, login_cookie)
    assert r.status_code == 302
    assert r.headers["location"] == "/spoolman/spools"
    assert _cookies(r)["spoolman_session"]["path"] == "/spoolman"
    assert len(await _users(db)) == 1


@pytest.mark.asyncio
async def test_disabled_user_session_rejected(db: AsyncSession) -> None:
    user = await make_user(db, "u1")
    token = await sessions.create_session(db, user.id)
    assert (await sessions.resolve_session(db, token)).id == user.id
    user.disabled = True
    assert await sessions.resolve_session(db, token) is None


@pytest.mark.asyncio
async def test_expired_session_rejected(db: AsyncSession) -> None:
    user = await make_user(db, "u1")
    token = await sessions.create_session(db, user.id)
    row = (await db.execute(select(models.Session))).scalar_one()
    row.expires = _now() - timedelta(seconds=1)
    assert await sessions.resolve_session(db, token) is None
    assert await sessions.resolve_session(db, "not-a-token") is None


@pytest.mark.asyncio
async def test_session_expiry_slides_after_an_hour(db: AsyncSession) -> None:
    user = await make_user(db, "u1")
    token = await sessions.create_session(db, user.id)
    row = (await db.execute(select(models.Session))).scalar_one()
    stale = _now() - timedelta(hours=2)
    row.last_seen, row.expires = stale, stale + timedelta(days=1)
    assert await sessions.resolve_session(db, token) is not None
    assert row.last_seen > stale
    assert row.expires > _now() + timedelta(days=29)


@pytest.mark.asyncio
async def test_logout_deletes_session_and_redirects_to_end_session(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client()
    token = _cookies(await _login(client, login_sub="u1"))["spoolman_session"].value
    (user,) = await _users(db)
    other = await sessions.create_session(db, user.id)
    r = await _get(client, "/auth/logout", f"spoolman_session={token}")
    assert r.status_code == 302
    assert r.headers["location"] == f"{ISSUER}/end-session"
    cleared = _cookies(r)["spoolman_session"]
    assert cleared.value == ""
    assert cleared["max-age"] == "0"
    assert await sessions.resolve_session(db, token) is None
    assert await sessions.resolve_session(db, other) is not None  # only this browser is signed out
    client.cookies.clear()
    assert (await client.post("/auth/logout")).status_code == 302  # POST, and no cookie, both fine


@pytest.mark.asyncio
async def test_delete_user_sessions(db: AsyncSession) -> None:
    user = await make_user(db, "u1")
    tokens = [await sessions.create_session(db, user.id) for _ in range(2)]
    await sessions.delete_user_sessions(db, user.id)
    assert [await sessions.resolve_session(db, t) for t in tokens] == [None, None]


@pytest.mark.asyncio
async def test_unclaimed_sub_cannot_sign_in(db: AsyncSession) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        await upsert_from_claims(db, {"sub": models.UNCLAIMED_SUB}, "spoolman_admins")


@pytest.mark.asyncio
async def test_disabled_user_cannot_sign_in(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client()
    user = await make_user(db, "u1")
    user.disabled = True
    r = await _login(client, login_sub="u1")
    assert r.status_code == 403
    assert "spoolman_session" not in _cookies(r)


@pytest.mark.asyncio
async def test_id_token_with_wrong_nonce_rejected(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client()
    callback, login_cookie, _ = await _authorize(client, tamper="nonce=forged", login_sub="u1")
    assert (await _get(client, callback, login_cookie)).status_code == 400
    assert await _users(db) == []


@pytest.mark.asyncio
async def test_id_token_from_other_issuer_rejected(make_client: MakeClient, db: AsyncSession) -> None:
    client = await make_client()
    cfg, provider = get_auth()
    set_auth(cfg, replace(provider, issuer="http://other-issuer"))
    callback, login_cookie, _ = await _authorize(client, login_sub="u1")
    assert (await _get(client, callback, login_cookie)).status_code == 400
    assert await _users(db) == []
