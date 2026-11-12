"""Resolve the principal (session cookie or personal key) for every request, and enforce sign-in.

Runs as an outer ASGI middleware so no route, mount or websocket can be reached without passing it.
"""

import posixpath
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

from fastapi import Depends, HTTPException
from starlette._utils import get_route_path
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from spoolman.auth import keys, sessions
from spoolman.auth.config import get_auth
from spoolman.auth.routes import SESSION_COOKIE, session_cookie_header
from spoolman.database.database import get_db_session
from spoolman.security import WS_CLOSE_FORBIDDEN

WS_CLOSE_UNAUTHENTICATED = 4401

# Short-lived session for resolving the principal: commits (slid expiry, key last_used), rolls back on
# error. Module-level so tests can point it at their own engine.
db_session = asynccontextmanager(get_db_session)

_PUBLIC_EXACT = frozenset(
    {
        "/api/v1/health",
        "/config.js",
        # Browsers fetch the web app manifest (and the icons it lists) without cookies, so these
        # would otherwise redirect to sign-in and break installing the app.
        "/manifest.webmanifest",
        "/pwa-64x64.png",
        "/pwa-192x192.png",
        "/pwa-512x512.png",
        "/maskable-icon-512x512.png",
    },
)
_PUBLIC_PREFIXES = ("/auth/", "/_app/")
_KEY_FORBIDDEN_PREFIXES = ("/api/v1/keys", "/api/v1/admin/", "/auth/")
_KEY_FORBIDDEN_EXACT = frozenset({"/api/v1/admin", "/api/v1/me/claim", "/auth"})


@dataclass(frozen=True)
class Principal:
    """The signed-in account behind a request, and how it proved who it is."""

    user_id: int
    display_name: str
    is_admin: bool
    via: Literal["session", "key"]
    key_level: Literal["edit", "view"] | None


def _forms(path: str) -> tuple[str, str]:
    """Return the path as routing matches it (slashes collapsed), and with its dot segments resolved.

    Every access decision must hold for both, so neither `//` nor `..` can steer a path past a check.
    """
    collapsed = re.sub(r"/+", "/", path)
    resolved = posixpath.normpath(collapsed)
    # normpath drops a trailing slash; keep it, so "/api/v1/" stays under "/api/v1/".
    return collapsed, resolved + "/" if collapsed.endswith("/") and resolved != "/" else resolved


def _is_public(path: str) -> bool:
    return all(p in _PUBLIC_EXACT or p.startswith(_PUBLIC_PREFIXES) for p in _forms(path))


def _key_may_reach(path: str) -> bool:
    return all(
        p.startswith("/api/v1/")
        and not p.startswith(_KEY_FORBIDDEN_PREFIXES)
        and p not in _KEY_FORBIDDEN_EXACT
        and not p.startswith("/api/v1/me/claim/")
        for p in _forms(path)
    )


def _is_api(path: str) -> bool:
    return any(p == "/api" or p.startswith("/api/") for p in _forms(path))


class PrincipalMiddleware:
    """Set ``scope["state"]["principal"]`` and refuse requests that may not proceed."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap the given ASGI application."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:  # noqa: C901
        """Resolve the principal, then pass through, refuse, or redirect to sign-in."""
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        cfg, _ = get_auth()
        base = cfg.base_path
        route_path = get_route_path(scope)
        under_base = not base or route_path == base or route_path.startswith(base + "/")
        # Outside the base path nothing is public or API; it is treated as a page.
        rel = route_path[len(base) :] if under_base else ""

        conn = HTTPConnection(scope)
        prefix_key = under_base and rel.startswith("/k/")
        if prefix_key:
            raw_key, _, rest = rel[3:].partition("/")
            rel = "/" + rest
        else:
            raw_key = _header_key(conn)
        principal, slid_token = await _resolve(conn, raw_key)

        if raw_key is not None:
            # A presented key that fails never falls back to the cookie.
            if principal is None:
                await _refuse(scope, receive, send, 401, WS_CLOSE_UNAUTHENTICATED)
                return
            if not _key_may_reach(rel):
                await _refuse(scope, receive, send, 403, WS_CLOSE_FORBIDDEN)
                return
        elif principal is None and not (under_base and _is_public(rel)):
            if scope["type"] == "websocket" or (under_base and _is_api(rel)):
                await _refuse(scope, receive, send, 401, WS_CLOSE_UNAUTHENTICATED)
                return
            nxt = scope["path"] + ("?" + conn.url.query if conn.url.query else "")
            await RedirectResponse(f"{base}/auth/login?{urlencode({'next': nxt})}", 302)(scope, receive, send)
            return

        scope = {**scope, "state": {**scope.get("state", {}), "principal": principal}}
        if prefix_key:
            # Routes are registered with the base path in them, so the remainder goes back under it.
            scope["path"] = scope.get("root_path", "") + base + rel
            if raw_path := scope.get("raw_path"):
                # The key is base62 (it verified), so it appears verbatim in the raw path.
                scope["raw_path"] = raw_path.replace(f"/k/{raw_key}".encode(), b"", 1)

        if slid_token is not None and scope["type"] == "http":
            cookie = session_cookie_header(cfg, slid_token).encode("latin-1")

            async def send_with_cookie(message: Message) -> None:
                if message["type"] == "http.response.start":
                    message = {**message, "headers": [*message.get("headers", []), (b"set-cookie", cookie)]}
                await send(message)

            await self.app(scope, receive, send_with_cookie)
            return
        await self.app(scope, receive, send)


def _header_key(conn: HTTPConnection) -> str | None:
    """Return the key from ``Authorization: Bearer`` or ``X-Api-Key``, or None if neither was sent."""
    auth = conn.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return conn.headers["x-api-key"].strip() if "x-api-key" in conn.headers else None


async def _resolve(conn: HTTPConnection, raw_key: str | None) -> tuple[Principal | None, str | None]:
    """Return (principal, session token to re-send because its expiry slid)."""
    async with db_session() as db:
        if raw_key is not None:
            found = await keys.verify_key(db, raw_key) if raw_key else None
            if found is None:
                return None, None
            user, key = found
            return Principal(user.id, user.display_name, user.is_admin, "key", key.level), None  # type: ignore[arg-type]
        token = conn.cookies.get(SESSION_COOKIE)
        resolved = await sessions.resolve_session(db, token) if token else None
        if resolved is None:
            return None, None
        user, slid = resolved
        return Principal(user.id, user.display_name, user.is_admin, "session", None), token if slid else None


async def _refuse(scope: Scope, receive: Receive, send: Send, status: int, ws_code: int) -> None:
    if scope["type"] == "websocket":
        # The ASGI server expects the connect message to be consumed before a close is sent.
        await receive()
        await send({"type": "websocket.close", "code": ws_code})
        return
    message = "Not signed in" if status == 401 else "Forbidden"  # noqa: PLR2004
    await JSONResponse({"message": message}, status)(scope, receive, send)


def get_principal(request: HTTPConnection) -> Principal:
    """FastAPI dependency: the request's principal. Works for HTTP routes and websockets."""
    principal = request.scope.get("state", {}).get("principal")
    if principal is None:
        raise HTTPException(401, "Not signed in")
    return principal


def require_session(principal: Principal = Depends(get_principal)) -> Principal:  # noqa: B008
    """FastAPI dependency: refuse personal keys (403); only a browser session may continue."""
    if principal.via == "key":
        raise HTTPException(403, "Not available to personal keys")
    return principal
