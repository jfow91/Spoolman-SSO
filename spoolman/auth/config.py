"""OIDC configuration, provider discovery and the startup-time holder for both."""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from spoolman import env

logger = logging.getLogger(__name__)

MIN_SESSION_SECRET_BYTES = 32
MIN_METRICS_TOKEN_CHARS = 32


class AuthConfigError(Exception):
    """Raised when OIDC configuration is missing/invalid or the provider cannot be discovered."""


@dataclass(frozen=True)
class OidcConfig:
    """Validated OIDC settings."""

    issuer: str
    client_id: str
    client_secret: str
    public_url: str
    base_path: str
    session_secret: bytes
    admin_group: str
    redirect_uri: str
    cookie_secure: bool
    # Bearer token for the Prometheus scraper; None disables /metrics.
    metrics_token: str | None = None


@dataclass(frozen=True)
class OidcProvider:
    """Endpoints from the provider's discovery document."""

    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None
    issuer: str


def _read(name: str, *, file_variant: bool = False) -> str | None:
    """Read an env var; with file_variant, NAME_FILE (if set) wins and is stripped of trailing newlines."""
    if file_variant and (path := os.getenv(f"{name}_FILE")):
        try:
            return Path(path).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise AuthConfigError(f"{name}_FILE could not be read: {exc}") from exc
    return os.getenv(name) or None


def load_oidc_config() -> OidcConfig:
    """Load and validate OIDC config from the environment, reporting every problem at once."""
    values = {
        "SPOOLMAN_OIDC_ISSUER": _read("SPOOLMAN_OIDC_ISSUER"),
        "SPOOLMAN_OIDC_CLIENT_ID": _read("SPOOLMAN_OIDC_CLIENT_ID"),
        "SPOOLMAN_OIDC_CLIENT_SECRET": _read("SPOOLMAN_OIDC_CLIENT_SECRET", file_variant=True),
        "SPOOLMAN_PUBLIC_URL": _read("SPOOLMAN_PUBLIC_URL"),
        "SPOOLMAN_SESSION_SECRET": _read("SPOOLMAN_SESSION_SECRET", file_variant=True),
    }
    problems = [f"{k} is required" for k, v in values.items() if not v]
    secret = values["SPOOLMAN_SESSION_SECRET"]
    if secret and len(secret.encode()) < MIN_SESSION_SECRET_BYTES:
        problems.append(f"SPOOLMAN_SESSION_SECRET must be at least {MIN_SESSION_SECRET_BYTES} bytes")
    metrics_token = _read("SPOOLMAN_METRICS_TOKEN", file_variant=True)
    if metrics_token and len(metrics_token) < MIN_METRICS_TOKEN_CHARS:
        problems.append(f"SPOOLMAN_METRICS_TOKEN must be at least {MIN_METRICS_TOKEN_CHARS} characters")
    if problems:
        raise AuthConfigError("Invalid auth configuration: " + "; ".join(problems))

    public_url = values["SPOOLMAN_PUBLIC_URL"].rstrip("/")  # type: ignore[union-attr]
    base_path = env.get_base_path()
    return OidcConfig(
        issuer=values["SPOOLMAN_OIDC_ISSUER"],  # type: ignore[arg-type]
        client_id=values["SPOOLMAN_OIDC_CLIENT_ID"],  # type: ignore[arg-type]
        client_secret=values["SPOOLMAN_OIDC_CLIENT_SECRET"],  # type: ignore[arg-type]
        public_url=public_url,
        base_path=base_path,
        session_secret=secret.encode(),  # type: ignore[union-attr]
        admin_group=os.getenv("SPOOLMAN_OIDC_ADMIN_GROUP") or "spoolman_admins",
        redirect_uri=f"{public_url}{base_path}/auth/callback",
        # Only plain-http deployments (e.g. LAN testing) get a non-Secure cookie.
        cookie_secure=not public_url.lower().startswith("http://"),
        metrics_token=metrics_token or None,
    )


async def discover(
    cfg: OidcConfig,
    http: httpx.AsyncClient,
    *,
    timeout_s: float = 60.0,
    interval_s: float = 2.0,
) -> OidcProvider:
    """Fetch the discovery document, retrying until timeout_s (the IdP may still be booting)."""
    url = cfg.issuer.rstrip("/") + "/.well-known/openid-configuration"
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            resp = await http.get(url)
            resp.raise_for_status()
            doc = resp.json()
            return OidcProvider(
                authorization_endpoint=doc["authorization_endpoint"],
                token_endpoint=doc["token_endpoint"],
                jwks_uri=doc["jwks_uri"],
                end_session_endpoint=doc.get("end_session_endpoint"),
                issuer=doc["issuer"],
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:  # noqa: PERF203 -- retry until the deadline
            if time.monotonic() >= deadline:
                raise AuthConfigError(f"OIDC discovery failed at {url}: {exc!r}") from exc
            logger.warning("OIDC discovery at %s not ready (%r), retrying...", url, exc)
            await asyncio.sleep(interval_s)


_auth: tuple[OidcConfig, OidcProvider] | None = None


def set_auth(cfg: OidcConfig, provider: OidcProvider) -> None:
    """Install the config/provider used by get_auth() (startup, or tests)."""
    global _auth  # noqa: PLW0603
    _auth = (cfg, provider)


def get_auth() -> tuple[OidcConfig, OidcProvider]:
    """Return the config/provider installed at startup."""
    if _auth is None:
        raise RuntimeError("Auth has not been initialised")
    return _auth
