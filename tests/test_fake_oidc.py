import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest
from authlib.jose import JsonWebKey, jwt
from starlette.testclient import TestClient

from tests.support.fake_oidc import CLIENT_ID, CLIENT_SECRET, make_app

ISSUER = "http://fake-oidc:9000"
VERIFIER = "v" * 50
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


@pytest.fixture
def client():
    return TestClient(make_app(ISSUER), follow_redirects=False)


def _login(client, **identity):
    params = {
        "client_id": CLIENT_ID, "redirect_uri": "http://app/cb", "state": "st", "nonce": "n1",
        "code_challenge": CHALLENGE, "code_challenge_method": "S256", **identity,
    }
    r = client.get("/authorize", params=params)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["state"] == ["st"]
    return q["code"][0]


def _token(client, code, verifier=VERIFIER):
    return client.post("/token", data={
        "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
        "redirect_uri": "http://app/cb", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
    })


def _claims(client, body):
    keys = JsonWebKey.import_key_set(client.get("/jwks").json())
    return jwt.decode(body["id_token"], keys, claims_options={"iss": {"value": ISSUER}})


def test_discovery_doc_fields(client):
    d = client.get("/.well-known/openid-configuration").json()
    assert d["issuer"] == ISSUER
    for k, p in [("authorization_endpoint", "authorize"), ("token_endpoint", "token"),
                 ("jwks_uri", "jwks"), ("end_session_endpoint", "end-session")]:
        assert d[k] == f"{ISSUER}/{p}"


def test_token_rejects_wrong_verifier(client):
    assert _token(client, _login(client, login_sub="u1"), verifier="w" * 50).status_code == 400


def test_code_is_single_use(client):
    code = _login(client, login_sub="u1")
    assert _token(client, code).status_code == 200
    assert _token(client, code).status_code == 400


def test_id_token_has_nonce_and_claims(client):
    code = _login(client, login_sub="u1", login_name="Ann", login_email="a@x.io", login_groups="g1,g2")
    r = _token(client, code)
    assert r.status_code == 200
    c = _claims(client, r.json())
    c.validate()
    assert (c["sub"], c["name"], c["email"], c["groups"], c["nonce"]) == ("u1", "Ann", "a@x.io", ["g1", "g2"], "n1")
    assert c["aud"] == CLIENT_ID


def test_omitted_email_absent_from_token(client):
    c = _claims(client, _token(client, _login(client, login_sub="u1")).json())
    assert "email" not in c and "groups" not in c and "name" not in c


def test_client_secret_basic(client):
    code = _login(client, login_sub="u1")
    r = client.post("/token", auth=(CLIENT_ID, CLIENT_SECRET), data={
        "grant_type": "authorization_code", "code": code, "code_verifier": VERIFIER, "redirect_uri": "http://app/cb"})
    assert r.status_code == 200
