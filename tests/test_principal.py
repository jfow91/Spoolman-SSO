"""Tests for the principal middleware: who is calling (session cookie or key), and sign-in enforcement."""

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Any

import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import PlainTextResponse

from spoolman import security
from spoolman.access import principal as access
from spoolman.access.principal import Principal, PrincipalMiddleware, get_principal
from spoolman.auth import config, keys, sessions
from spoolman.auth.config import OidcConfig, OidcProvider
from spoolman.auth.routes import session_cookie_header
from spoolman.auth.users import utcnow
from spoolman.database import models
from tests.support.dbsession import make_user

HOST = "http://spoolman.test"


def _app(base: str) -> FastAPI:
    """App wired like main.py: /api/v1 is a mounted sub-app, the SPA a catch-all mount at the base."""
    api = FastAPI()

    @api.api_route("/echo", methods=["GET", "POST"])
    async def _echo(request: Request, p: Annotated[Principal, Depends(get_principal)]) -> dict[str, Any]:
        return {"path": request.scope["path"], "via": p.via, "user_id": p.user_id, "level": p.key_level}

    @api.get("/keys")
    @api.get("/keys/{key_id}")
    @api.get("/admin/users")
    @api.post("/me/claim")
    async def _forbidden_for_keys() -> dict[str, bool]:
        return {"reached": True}

    @api.get("/health")
    async def _health() -> dict[str, str]:
        return {"status": "healthy"}

    @api.websocket("/")
    async def _ws(websocket: WebSocket) -> None:
        await websocket.accept()
        p = get_principal(websocket)
        await websocket.send_json({"path": websocket.scope["path"], "via": p.via})
        await websocket.close()

    async def _spa(scope, receive, send) -> None:  # noqa: ANN001
        await PlainTextResponse("page")(scope, receive, send)

    app = FastAPI()
    app.mount(base + "/api/v1", api)
    app.mount(base, _spa)
    # Same order as main.py: the principal middleware wraps the origin guard.
    app.add_middleware(security.TrustedOriginMiddleware)
    app.add_middleware(PrincipalMiddleware)
    return app


Setup = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest_asyncio.fixture
async def setup(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[Setup, None]:
    monkeypatch.delenv("SPOOLMAN_CORS_ORIGIN", raising=False)
    monkeypatch.delenv("SPOOLMAN_DEBUG_MODE", raising=False)

    @asynccontextmanager
    async def _session() -> AsyncIterator[AsyncSession]:
        yield db
        await db.commit()

    monkeypatch.setattr(access, "db_session", _session)
    clients: list[httpx.AsyncClient] = []

    async def factory(base: str = "") -> httpx.AsyncClient:
        cfg = OidcConfig(
            "i", "c", "s", HOST, base, b"s" * 32, "admins", HOST + base + "/auth/callback", cookie_secure=False
        )
        monkeypatch.setattr(config, "_auth", (cfg, OidcProvider("a", "t", "j", None, "i")))
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_app(base)), base_url=HOST)
        clients.append(client)
        return client

    yield factory
    for c in clients:
        await c.aclose()


async def _user_with_key(db: AsyncSession, level: str = "edit") -> tuple[models.User, str]:
    user = await make_user(db)
    _, raw = await keys.create_key(db, user.id, "printer", level)  # type: ignore[arg-type]
    await db.commit()
    return user, raw


async def _session_cookie(db: AsyncSession, user: models.User | None = None) -> str:
    user = user or await make_user(db)
    token = await sessions.create_session(db, user.id)
    await db.commit()
    return token


async def _asgi(client: httpx.AsyncClient, kind: str, path: str, headers: dict[str, str] | None = None) -> list[dict]:
    """Drive one request straight through the ASGI app (httpx would resolve dot segments) and return what it sent."""
    app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
    raw_headers = [(b"host", b"spoolman.test")] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": kind,
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": raw_headers,
        "scheme": "ws" if kind == "websocket" else "http",
        "server": ("spoolman.test", 80),
        "client": ("127.0.0.1", 1),
        "subprotocols": [],
        "asgi": {"version": "3.0"},
    }
    first = {"type": "websocket.connect"} if kind == "websocket" else {"type": "http.request", "body": b""}
    incoming = [first]
    sent: list[dict] = []

    async def receive() -> dict:
        return incoming.pop(0) if incoming else {"type": f"{kind}.disconnect", "code": 1000}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


async def _ws(client: httpx.AsyncClient, path: str, headers: dict[str, str] | None = None) -> list[dict]:
    return await _asgi(client, "websocket", path, headers)


async def _status(client: httpx.AsyncClient, path: str, headers: dict[str, str] | None = None) -> int:
    """Status of a GET sent with the path exactly as given."""
    return (await _asgi(client, "http", path, headers))[0]["status"]


# --- no principal -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_auth_api_401(setup: Setup) -> None:
    client = await setup()
    r = await client.get("/api/v1/echo")
    assert r.status_code == 401
    assert r.json() == {"message": "Not signed in"}


@pytest.mark.asyncio
async def test_no_auth_page_redirects_with_next(setup: Setup) -> None:
    client = await setup()
    r = await client.get("/spools/show/3?x=1")
    assert r.status_code == 302
    assert r.headers["location"] == "/auth/login?next=%2Fspools%2Fshow%2F3%3Fx%3D1"


@pytest.mark.asyncio
async def test_no_auth_page_redirects_with_base_path(setup: Setup) -> None:
    client = await setup("/spoolman")
    r = await client.get("/spoolman/spools")
    assert r.status_code == 302
    assert r.headers["location"] == "/spoolman/auth/login?next=%2Fspoolman%2Fspools"


@pytest.mark.asyncio
async def test_no_auth_ws_closed_4401(setup: Setup) -> None:
    client = await setup()
    sent = await _ws(client, "/api/v1/")
    assert sent == [{"type": "websocket.close", "code": 4401}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/auth/login", "/api/v1/health", "/config.js", "/_app/immutable/x.js", "/manifest.webmanifest", "/pwa-64x64.png"],
)
async def test_public_paths_need_no_principal(setup: Setup, path: str) -> None:
    client = await setup("/spoolman")
    r = await client.get("/spoolman" + path)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/auth/login", "/_app/x.js", "/auth/../api/v1/echo", "/_app/../spools"])
async def test_public_prefixes_do_not_leak_outside_the_base_path_or_via_dot_segments(setup: Setup, path: str) -> None:
    client = await setup("/spoolman")
    path = path if ".." not in path else "/spoolman" + path
    assert await _status(client, path) in (302, 401)


# --- session cookie -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_cookie_resolves_principal(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    token = await _session_cookie(db)
    r = await client.get("/api/v1/echo", headers={"cookie": f"spoolman_session={token}"})
    assert r.status_code == 200
    assert r.json()["via"] == "session"
    assert "set-cookie" not in r.headers  # not slid: no refresh


@pytest.mark.asyncio
async def test_slid_session_resends_cookie(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    token = await _session_cookie(db)
    row = await db.get(models.Session, sessions._hash(token))  # noqa: SLF001
    assert row is not None
    row.last_seen = utcnow() - timedelta(hours=2)
    await db.commit()
    r = await client.get("/api/v1/echo", headers={"cookie": f"spoolman_session={token}"})
    assert r.status_code == 200
    assert r.headers.get_list("set-cookie") == [session_cookie_header(config.get_auth()[0], token)]
    await db.refresh(row)
    assert row.last_seen > utcnow() - timedelta(minutes=1)  # committed by the middleware


@pytest.mark.asyncio
async def test_invalid_cookie_is_not_signed_in(setup: Setup) -> None:
    client = await setup()
    r = await client.get("/api/v1/echo", headers={"cookie": "spoolman_session=nope"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_session_can_open_pages(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    token = await _session_cookie(db)
    r = await client.get("/spools", headers={"cookie": f"spoolman_session={token}"})
    assert (r.status_code, r.text) == (200, "page")


# --- keys ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_key_in_url_prefix(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    user, raw = await _user_with_key(db, "view")
    r = await client.get(f"/k/{raw}/api/v1/echo")
    assert r.status_code == 200
    assert r.json() == {"path": "/api/v1/echo", "via": "key", "user_id": user.id, "level": "view"}


@pytest.mark.asyncio
async def test_key_in_url_prefix_with_base_path(setup: Setup, db: AsyncSession) -> None:
    client = await setup("/spoolman")
    _, raw = await _user_with_key(db)
    r = await client.get(f"/spoolman/k/{raw}/api/v1/echo")
    assert r.status_code == 200
    assert r.json()["path"] == "/spoolman/api/v1/echo"
    assert r.json()["via"] == "key"


@pytest.mark.asyncio
async def test_key_bearer_header(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    _, raw = await _user_with_key(db)
    r = await client.get("/api/v1/echo", headers={"authorization": f"Bearer {raw}"})
    assert (r.status_code, r.json()["via"], r.json()["level"]) == (200, "key", "edit")


@pytest.mark.asyncio
async def test_key_x_api_key_header(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    _, raw = await _user_with_key(db)
    r = await client.get("/api/v1/echo", headers={"x-api-key": raw})
    assert (r.status_code, r.json()["via"]) == (200, "key")


@pytest.mark.asyncio
async def test_key_ws_prefix(setup: Setup, db: AsyncSession) -> None:
    client = await setup("/spoolman")
    _, raw = await _user_with_key(db)
    sent = await _ws(client, f"/spoolman/k/{raw}/api/v1/")
    assert sent[0]["type"] == "websocket.accept"
    assert '"via":"key"' in sent[1]["text"]
    assert '"path":"/spoolman/api/v1/"' in sent[1]["text"]


@pytest.mark.asyncio
async def test_key_cannot_open_pages(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    _, raw = await _user_with_key(db)
    assert (await client.get(f"/k/{raw}/spools")).status_code == 403
    assert (await client.get("/spools", headers={"x-api-key": raw})).status_code == 403
    assert (await client.get("/config.js", headers={"x-api-key": raw})).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/keys",
        "/api/v1/keys/",
        "/api/v1/keys/1",
        "/api/v1//keys",
        "/api/v1/./keys",
        "/api/v1/keys/1/..",
        "/api/v1/admin/users",
        "/api/v1/me/claim",
        "/auth/login",
    ],
)
async def test_key_cannot_manage_keys(setup: Setup, db: AsyncSession, path: str) -> None:
    client = await setup()
    _, raw = await _user_with_key(db)
    assert await _status(client, path, {"authorization": f"Bearer {raw}"}) == 403
    assert await _status(client, f"/k/{raw}{path}") == 403


@pytest.mark.asyncio
async def test_key_cannot_open_ws_outside_api(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    _, raw = await _user_with_key(db)
    sent = await _ws(client, f"/k/{raw}/socket")
    assert sent == [{"type": "websocket.close", "code": 4403}]


@pytest.mark.asyncio
async def test_bad_key_401_not_fallthrough_to_cookie(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    cookie = f"spoolman_session={await _session_cookie(db)}"
    for path, headers in [
        ("/api/v1/echo", {"x-api-key": "bogus"}),
        ("/api/v1/echo", {"authorization": "Bearer bogus"}),
        ("/k/bogus/api/v1/echo", {}),
        ("/k//api/v1/echo", {}),
        ("/k/bogus/spools", {}),  # pages too: 401, not a redirect and not the cookie
    ]:
        r = await client.get(path, headers={"cookie": cookie, **headers})
        assert r.status_code == 401, path
    sent = await _ws(client, "/k/bogus/api/v1/", {"cookie": cookie})
    assert sent == [{"type": "websocket.close", "code": 4401}]


@pytest.mark.asyncio
async def test_revoked_key_401(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    user, raw = await _user_with_key(db)
    (row,) = await keys.list_keys(db, user.id)
    await keys.revoke_key(db, user.id, row.id)
    await db.commit()
    assert (await client.get("/api/v1/echo", headers={"x-api-key": raw})).status_code == 401


# --- CSRF ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_still_enforced_for_cookie_post_cross_origin(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    cookie = f"spoolman_session={await _session_cookie(db)}"
    r = await client.post("/api/v1/echo", headers={"cookie": cookie, "origin": "https://evil.example"})
    assert r.status_code == 403
    r = await client.post("/api/v1/echo", headers={"cookie": cookie, "origin": HOST})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_csrf_not_applied_to_key_post(setup: Setup, db: AsyncSession) -> None:
    client = await setup()
    _, raw = await _user_with_key(db)
    r = await client.post("/api/v1/echo", headers={"x-api-key": raw, "origin": "https://evil.example"})
    assert r.status_code == 200
