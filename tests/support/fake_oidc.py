"""Minimal fake PocketID (OIDC) provider for tests. Auto-approves /authorize; not for production."""

import base64
import hashlib
import os
import secrets
import time
from urllib.parse import parse_qsl, unquote, urlencode

from authlib.jose import JsonWebKey, jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

CLIENT_ID = "spoolman"
CLIENT_SECRET = "test-secret"  # noqa: S105 -- test-only fake secret

_KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "fake-oidc-key"})


def make_app(issuer: str) -> Starlette:  # noqa: C901
    codes: dict[str, dict] = {}  # code -> authorize params + identity; popped on use (single-use)

    async def discovery(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token",
                "jwks_uri": f"{issuer}/jwks",
                "end_session_endpoint": f"{issuer}/end-session",
                "id_token_signing_alg_values_supported": ["RS256"],
            }
        )

    async def jwks(_: Request) -> JSONResponse:
        return JSONResponse({"keys": [_KEY.as_dict(is_private=False)]})

    async def authorize(request: Request) -> RedirectResponse:
        q = dict(request.query_params)
        code = secrets.token_urlsafe(16)
        codes[code] = q
        return RedirectResponse(f"{q['redirect_uri']}?{urlencode({'code': code, 'state': q.get('state', '')})}", 302)

    async def token(request: Request) -> JSONResponse:
        # parse_qsl, not request.form(): avoids a python-multipart dependency
        form = dict(parse_qsl((await request.body()).decode()))
        cid, secret = form.get("client_id"), form.get("client_secret")
        if request.headers.get("authorization", "").startswith("Basic "):
            cid, _, secret = base64.b64decode(request.headers["authorization"][6:]).decode().partition(":")
            cid, secret = unquote(cid), unquote(secret)
        if cid != CLIENT_ID or secret != CLIENT_SECRET:
            return JSONResponse({"error": "invalid_client"}, 401)
        q = codes.pop(str(form.get("code")), None)
        if q is None or form.get("redirect_uri") != q["redirect_uri"]:
            return JSONResponse({"error": "invalid_grant"}, 400)
        digest = hashlib.sha256(str(form.get("code_verifier", "")).encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != q.get("code_challenge"):
            return JSONResponse({"error": "invalid_grant", "error_description": "PKCE"}, 400)
        now = int(time.time())
        claims = {"iss": issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 300, "sub": q.get("login_sub", "fake-sub")}
        if "nonce" in q:
            claims["nonce"] = q["nonce"]
        for claim in ("name", "email"):
            if f"login_{claim}" in q:
                claims[claim] = q[f"login_{claim}"]
        if "login_groups" in q:
            claims["groups"] = q["login_groups"].split(",")
        id_token = jwt.encode({"alg": "RS256", "kid": _KEY.as_dict()["kid"]}, claims, _KEY).decode()
        return JSONResponse(
            {"access_token": secrets.token_urlsafe(16), "token_type": "Bearer", "expires_in": 300, "id_token": id_token}
        )

    async def end_session(_: Request) -> PlainTextResponse:
        return PlainTextResponse("logged out")

    return Starlette(
        routes=[
            Route("/.well-known/openid-configuration", discovery),
            Route("/authorize", authorize),
            Route("/token", token, methods=["POST"]),
            Route("/jwks", jwks),
            Route("/end-session", end_session),
        ]
    )


app = make_app(os.environ.get("FAKE_OIDC_ISSUER", "http://fake-oidc:9000"))
