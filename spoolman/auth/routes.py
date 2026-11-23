"""PocketID sign-in (OIDC authorization code + PKCE) and sign-out. Mounted at <base_path>/auth."""

import logging
import secrets
from typing import Annotated

import httpx
from authlib.common.errors import AuthlibBaseError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.auth.config import OidcConfig, OidcProvider, get_auth
from spoolman.auth.sessions import SESSION_LIFETIME, create_session, delete_session
from spoolman.auth.users import upsert_from_claims
from spoolman.database.database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

SESSION_COOKIE = "spoolman_session"
LOGIN_COOKIE = "spoolman_login"
LOGIN_MAX_AGE = 600
SCOPE = "openid profile email groups"

# Transport for calls to the provider; tests point it at the fake PocketID.
http_transport: httpx.AsyncBaseTransport | None = None


def _oauth(cfg: OidcConfig) -> AsyncOAuth2Client:
    return AsyncOAuth2Client(
        cfg.client_id,
        cfg.client_secret,
        scope=SCOPE,
        redirect_uri=cfg.redirect_uri,
        code_challenge_method="S256",
        transport=http_transport,
        timeout=10,
    )


def _serializer(cfg: OidcConfig) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(cfg.session_secret, salt=LOGIN_COOKIE)


def _cookie_args(cfg: OidcConfig) -> dict:
    return {"path": cfg.base_path or "/", "secure": cfg.cookie_secure, "httponly": True, "samesite": "lax"}


def session_cookie_header(cfg: OidcConfig, raw_token: str) -> str:
    """Full Set-Cookie value for the session cookie; login and the sliding refresh both use it."""
    resp = Response()
    resp.set_cookie(SESSION_COOKIE, raw_token, max_age=int(SESSION_LIFETIME.total_seconds()), **_cookie_args(cfg))
    return resp.headers["set-cookie"]


def _safe_next(nxt: str, cfg: OidcConfig) -> str:
    r"""Only same-origin paths; `//host` and `/\host` are protocol-relative URLs to browsers."""
    if nxt.startswith("/") and not nxt.startswith(("//", "/\\")):
        return nxt
    return cfg.base_path + "/"


def _verify_id_token(id_token: str, jwks: dict, cfg: OidcConfig, provider: OidcProvider, nonce: str) -> dict:
    """Check signature, iss (against the discovered issuer), aud, exp and nonce; return the claims."""
    # algorithms=None: joserfc's recommended set, which never includes "none".
    token = jwt.decode(id_token, KeySet.import_key_set(jwks))
    jwt.JWTClaimsRegistry(
        leeway=60,
        iss={"essential": True, "value": provider.issuer},
        aud={"essential": True, "value": cfg.client_id},
        exp={"essential": True},
        sub={"essential": True},
        nonce={"essential": True, "value": nonce},
    ).validate(token.claims)
    return token.claims


@router.get("/login")
async def login(nxt: Annotated[str, Query(alias="next")] = "") -> RedirectResponse:
    """Send the browser to PocketID, remembering state, nonce, PKCE verifier and `next` in a signed cookie."""
    cfg, provider = get_auth()
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    async with _oauth(cfg) as client:
        url, _ = client.create_authorization_url(
            provider.authorization_endpoint,
            state=state,
            nonce=nonce,
            code_verifier=verifier,
        )
    resp = RedirectResponse(url, 302)
    data = {"state": state, "nonce": nonce, "verifier": verifier, "next": _safe_next(nxt, cfg)}
    resp.set_cookie(LOGIN_COOKIE, _serializer(cfg).dumps(data), max_age=LOGIN_MAX_AGE, **_cookie_args(cfg))
    return resp


@router.get("/callback")
async def callback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    code: str = "",
    state: str = "",
) -> RedirectResponse:
    """Finish sign-in: redeem the code, validate the ID token, upsert the user and start a session."""
    cfg, provider = get_auth()
    try:
        pending = _serializer(cfg).loads(request.cookies.get(LOGIN_COOKIE, ""), max_age=LOGIN_MAX_AGE)
    except BadSignature as exc:
        raise HTTPException(400, "Sign-in expired or was not started here; please try again.") from exc
    if not code or not secrets.compare_digest(state, pending["state"]):
        raise HTTPException(400, "Sign-in state mismatch; please try again.")

    try:
        async with _oauth(cfg) as client:
            token = await client.fetch_token(provider.token_endpoint, code=code, code_verifier=pending["verifier"])
            # shortcut: JWKS fetched on every sign-in, add caching if sign-in volume ever matters.
            jwks = (await client.request("GET", provider.jwks_uri, withhold_token=True)).raise_for_status().json()
        claims = _verify_id_token(token["id_token"], jwks, cfg, provider, pending["nonce"])
        user = await upsert_from_claims(db, claims, cfg.admin_group)
    except (AuthlibBaseError, httpx.HTTPError, JoseError, KeyError, ValueError) as exc:
        logger.warning("Sign-in failed: %r", exc)
        raise HTTPException(400, "Sign-in failed; please try again.") from exc
    if user.disabled:
        raise HTTPException(403, "This account is disabled.")

    raw = await create_session(db, user.id)
    await db.commit()
    resp = RedirectResponse(pending["next"], 302)
    resp.headers.append("set-cookie", session_cookie_header(cfg, raw))
    resp.delete_cookie(LOGIN_COOKIE, **_cookie_args(cfg))
    return resp


@router.api_route("/logout", methods=["GET", "POST"])
async def logout(request: Request, db: Annotated[AsyncSession, Depends(get_db_session)]) -> RedirectResponse:
    """Delete this browser's session, clear the cookie and hand over to PocketID's end-session endpoint."""
    cfg, provider = get_auth()
    if raw := request.cookies.get(SESSION_COOKIE):
        await delete_session(db, raw)
        await db.commit()
    resp = RedirectResponse(provider.end_session_endpoint or cfg.base_path + "/", 302)
    resp.delete_cookie(SESSION_COOKIE, **_cookie_args(cfg))
    return resp
