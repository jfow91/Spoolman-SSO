"""Tests for OIDC configuration loading and provider discovery."""

import httpx
import pytest

from spoolman.auth.config import AuthConfigError, discover, load_oidc_config

REQUIRED = [
    "SPOOLMAN_OIDC_ISSUER",
    "SPOOLMAN_OIDC_CLIENT_ID",
    "SPOOLMAN_OIDC_CLIENT_SECRET",
    "SPOOLMAN_PUBLIC_URL",
    "SPOOLMAN_SESSION_SECRET",
]
DISCOVERY = {
    "issuer": "http://fake-oidc",
    "authorization_endpoint": "http://fake-oidc/authorize",
    "token_endpoint": "http://fake-oidc/token",
    "jwks_uri": "http://fake-oidc/jwks",
}


@pytest.fixture
def full_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in [*REQUIRED, "SPOOLMAN_OIDC_CLIENT_SECRET_FILE", "SPOOLMAN_SESSION_SECRET_FILE", "SPOOLMAN_BASE_PATH"]:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("SPOOLMAN_OIDC_ISSUER", "http://fake-oidc")
    monkeypatch.setenv("SPOOLMAN_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOOLMAN_OIDC_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SPOOLMAN_PUBLIC_URL", "https://s.lab8.au")
    monkeypatch.setenv("SPOOLMAN_SESSION_SECRET", "x" * 32)


def test_missing_vars_listed_together(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in [*REQUIRED, "SPOOLMAN_OIDC_CLIENT_SECRET_FILE", "SPOOLMAN_SESSION_SECRET_FILE"]:
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(AuthConfigError) as e:
        load_oidc_config()
    for v in REQUIRED:
        assert v in str(e.value)


def test_short_session_secret_rejected(full_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOOLMAN_SESSION_SECRET", "x" * 31)
    with pytest.raises(AuthConfigError, match="32"):
        load_oidc_config()


def test_secret_file_variant(full_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # noqa: ANN001
    f = tmp_path / "secret"
    f.write_text("from-file\n")
    monkeypatch.setenv("SPOOLMAN_OIDC_CLIENT_SECRET_FILE", str(f))
    monkeypatch.setenv("SPOOLMAN_OIDC_CLIENT_SECRET", "ignored")
    assert load_oidc_config().client_secret == "from-file"


def test_redirect_uri_from_public_url_with_base_path(full_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOOLMAN_PUBLIC_URL", "https://s.lab8.au/")
    monkeypatch.setenv("SPOOLMAN_BASE_PATH", "/spoolman")
    cfg = load_oidc_config()
    assert cfg.redirect_uri == "https://s.lab8.au/spoolman/auth/callback"
    assert cfg.cookie_secure is True
    assert cfg.admin_group == "spoolman_admins"


def test_http_public_url_not_secure(full_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOOLMAN_PUBLIC_URL", "http://10.0.0.5:7912")
    assert load_oidc_config().cookie_secure is False


@pytest.mark.asyncio
async def test_discover_retries_until_up(full_env: None) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise httpx.ConnectError("down", request=request)
        assert request.url.path == "/.well-known/openid-configuration"
        return httpx.Response(200, json=DISCOVERY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = await discover(load_oidc_config(), client, timeout_s=5, interval_s=0.01)
    assert provider.token_endpoint.endswith("/token")
    assert provider.end_session_endpoint is None
    assert calls == 4


@pytest.mark.asyncio
async def test_discover_gives_up(full_env: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AuthConfigError, match="discovery"):
            await discover(load_oidc_config(), client, timeout_s=0.05, interval_s=0.01)
