"""Route isolation sweep: user B must never see or change user A's data, on any route or websocket.

Every route in the OpenAPI document is called as B with A's ids and names substituted in. A route
that names one of A's records by id must refuse (404/403/401). A route that only works inside the
caller's own account (lists, settings, field definitions, renames) may succeed, but its response
must not carry any of A's data. Afterwards A's records are re-read as A and must be unchanged.

A's records each carry a unique marker string (vendor name, filament name, spool comment, ...), so
any serialised A record in a response is caught by a substring search, whatever the route nests.
"""

import asyncio
import contextlib
import json
import uuid
from typing import Any

import httpx
import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .conftest import BASE_URL, login

# Path parameters that name one specific record. A route carrying one must refuse B outright.
ID_PARAMS = {"{vendor_id}", "{filament_id}", "{spool_id}", "{uid}", "{key_id}", "{user_id}"}

# Routes that only read or write the caller's own account: B may succeed, but must see none of A.
ALLOWED_CROSS_ACCOUNT_OK = {
    ("GET", "/filament"),
    ("POST", "/filament"),
    ("GET", "/spool"),
    ("POST", "/spool"),
    ("GET", "/spool/group"),
    ("PATCH", "/spool/field/{field}"),
    ("GET", "/vendor"),
    ("POST", "/vendor"),
    ("GET", "/setting/"),
    ("GET", "/setting/{key}"),
    ("POST", "/setting/{key}"),
    ("GET", "/field/{entity_type}"),
    ("GET", "/field/{entity_type}/{key}/values"),
    ("POST", "/field/{entity_type}/{key}"),
    ("DELETE", "/field/{entity_type}/{key}"),
    ("GET", "/material"),
    ("GET", "/article-number"),
    ("GET", "/lot-number"),
    ("GET", "/location"),
    ("PATCH", "/location/{location}"),
    ("GET", "/export/spools"),
    ("GET", "/export/filaments"),
    ("GET", "/export/vendors"),
    ("GET", "/search"),
    ("POST", "/tag/scan"),
    ("GET", "/tag/reader"),
}

# Routes holding no account data at all, or describing only the caller (and admin-only ones B lacks).
PUBLIC_OR_SELF_ROUTES = {
    ("GET", "/info"),
    ("GET", "/health"),
    ("POST", "/backup"),
    ("GET", "/external/filament"),
    ("GET", "/external/filament/search"),
    ("GET", "/external/material"),
    ("GET", "/me"),
    ("POST", "/me/claim"),
    ("GET", "/keys"),
    ("POST", "/keys"),
    ("GET", "/admin/users"),
}

ACCOUNT_LOCAL = ALLOWED_CROSS_ACCOUNT_OK | PUBLIC_OR_SELF_ROUTES
DENIED = {401, 403, 404}

# Bodies whose validity the schema cannot express; without them the route answers 400 before it looks up A's record.
BODIES = {("PUT", "/spool/{spool_id}/use"): {"use_weight": 1}}
METHODS = {"get", "post", "put", "patch", "delete"}

# Long enough to absorb a slow container, short enough to fail rather than hang.
RECV_TIMEOUT_S = 10.0
QUIET_PERIOD_S = 1.0


def _ok(response: httpx.Response) -> dict[str, Any]:
    request = response.request
    assert response.is_success, f"{request.method} {request.url}: {response.status_code} {response.text}"
    return response.json() if response.content else {}


@pytest.fixture(scope="module")
def a() -> dict[str, Any]:
    """User A and one of everything: vendor, filament, spool with extra field, location and tag, setting."""
    marker = f"sweepa{uuid.uuid4().hex[:10]}"
    session = login("sweep-a", name=f"{marker}-user")
    created = _ok(session.post(f"{BASE_URL}/api/v1/keys", json={"name": f"{marker}-key", "level": "edit"}))
    user_id = _ok(session.get(f"{BASE_URL}/api/v1/me"))["id"]
    api = httpx.Client(base_url=f"{BASE_URL}/k/{created['key']}/api/v1", timeout=30)

    field_key = f"f{uuid.uuid4().hex[:8]}"
    _ok(api.post(f"/field/spool/{field_key}", json={"name": f"{marker}-field", "field_type": "text"}))
    vendor = _ok(api.post("/vendor", json={"name": f"{marker}-vendor"}))
    filament = _ok(
        api.post(
            "/filament",
            json={"name": f"{marker}-filament", "vendor_id": vendor["id"], "density": 1.24, "diameter": 1.75},
        ),
    )
    location = f"{marker}-loc"
    spool = _ok(
        api.post(
            "/spool",
            json={
                "filament_id": filament["id"],
                "comment": f"{marker}-spool",
                "location": location,
                "extra": {field_key: json.dumps(f"{marker}-extra")},
            },
        ),
    )
    uid = uuid.uuid4().hex[:14].upper()
    _ok(api.post(f"/spool/{spool['id']}/tag", json={"uid": uid}))
    _ok(api.post("/setting/locations", json=json.dumps([location])))

    yield {
        "api": api,
        "key": created["key"],
        "marker": marker,
        "markers": [
            f"{marker}-{what}" for what in ("user", "key", "field", "vendor", "filament", "loc", "spool", "extra")
        ]
        + [created["key"]],
        "location": location,
        "field_key": field_key,
        "uid": uid,
        "params": {
            "{vendor_id}": vendor["id"],
            "{filament_id}": filament["id"],
            "{spool_id}": spool["id"],
            "{uid}": uid,
            "{key_id}": created["id"],
            "{user_id}": user_id,
            "{entity_type}": "spool",
            "{field}": "location",
            "{location}": location,
        },
    }
    api.close()
    session.close()


@pytest.fixture(scope="module")
def b_session() -> httpx.Client:
    client = login("sweep-b")
    yield client
    client.close()


@pytest.fixture(scope="module")
def b_key(b_session: httpx.Client) -> str:
    created = _ok(b_session.post(f"{BASE_URL}/api/v1/keys", json={"name": "sweep-b-edit", "level": "edit"}))
    return created["key"]


@pytest.fixture(scope="module", params=["key", "session"])
def b(request: pytest.FixtureRequest, b_session: httpx.Client, b_key: str) -> httpx.Client:
    """User B, once through a personal key and once through a browser session."""
    if request.param == "session":
        b_session.base_url = f"{BASE_URL}/api/v1"
        yield b_session
        return
    with httpx.Client(base_url=f"{BASE_URL}/k/{b_key}/api/v1", timeout=30) as client:
        yield client


@pytest.fixture(scope="module")
def spec(a: dict[str, Any]) -> dict[str, Any]:
    return _ok(a["api"].get("/openapi.json"))


def _routes(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, op)
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if method in METHODS
    ]


def _sample(schema: dict[str, Any], spec: dict[str, Any], a: dict[str, Any], name: str = "") -> Any:  # noqa: ANN401, C901, PLR0911
    """Minimal valid value for a schema. Fields that reference a record point at A's."""
    if "$ref" in schema:
        return _sample(spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]], spec, a, name)
    by_name = {
        "vendor_id": a["params"]["{vendor_id}"],
        "filament_id": a["params"]["{filament_id}"],
        "spool_id": a["params"]["{spool_id}"],
        "uid": a["uid"],
        "value": a["location"],  # the rename endpoints: try to rename A's value
    }
    if name in by_name:
        return by_name[name]
    for alt in schema.get("anyOf", []) + schema.get("allOf", []):
        if alt.get("type") != "null":
            return _sample(alt, spec, a, name)
    if "enum" in schema:
        return schema["enum"][0]
    match schema.get("type"):
        case "object":
            props = schema.get("properties", {})
            return {k: _sample(props[k], spec, a, k) for k in schema.get("required", [])}
        case "array":
            return []
        case "integer":
            return 1
        case "number":
            return 1.0
        case "boolean":
            return False
    # B's free text is A's bare marker prefix: it finds A's records in a search, yet never equals a full marker.
    return a["marker"]


def _path_for(path: str, a: dict[str, Any]) -> str:
    key = "locations" if path.startswith("/setting/") else a["field_key"]
    for param, value in {**a["params"], "{key}": key}.items():
        path = path.replace(param, str(value))
    assert "{" not in path, f"unsubstituted parameter in {path}"
    return path


def _request(client: httpx.Client, method: str, path: str, op: dict[str, Any], spec: dict, a: dict) -> httpx.Response:
    params = {
        p["name"]: _sample(p.get("schema", {}), spec, a)
        for p in op.get("parameters", [])
        if p["in"] == "query" and p.get("required")
    }
    kwargs: dict[str, Any] = {"params": params}
    if "requestBody" in op:
        schema = op["requestBody"]["content"]["application/json"]["schema"]
        kwargs["json"] = BODIES.get((method, path)) or _sample(schema, spec, a)
    return client.request(method, _path_for(path, a), **kwargs)


def _snapshot(a: dict[str, Any]) -> dict[str, Any]:
    api, p = a["api"], a["params"]
    return {
        "vendor": _ok(api.get(f"/vendor/{p['{vendor_id}']}")),
        "filament": _ok(api.get(f"/filament/{p['{filament_id}']}")),
        "spool": _ok(api.get(f"/spool/{p['{spool_id}']}")),
        "fields": _ok(api.get("/field/spool")),
        "locations": _ok(api.get("/setting/locations")),
        "me": _ok(api.get("/me")),
    }


def test_every_route_classified(spec: dict[str, Any]):
    """A new upstream route must be put on one side or the other before this sweep passes."""
    unclassified = [
        (method, path)
        for method, path, _ in _routes(spec)
        if not any(p in path for p in ID_PARAMS)
        and (method, path) not in ALLOWED_CROSS_ACCOUNT_OK
        and (method, path) not in PUBLIC_OR_SELF_ROUTES
    ]
    assert not unclassified, f"classify these routes in test_isolation_sweep.py: {unclassified}"


def test_b_cannot_reach_a(a: dict[str, Any], b: httpx.Client, spec: dict[str, Any]):
    before = _snapshot(a)
    failures = []
    for method, path, op in _routes(spec):
        response = _request(b, method, path, op, spec, a)
        sent = str(response.request.url) + response.request.content.decode()
        leaked = [m for m in a["markers"] if m in response.text and m not in sent]
        if leaked:
            failures.append(f"{method} {path}: {response.status_code} leaks {leaked}")
        elif (method, path) not in ACCOUNT_LOCAL and response.status_code not in DENIED:
            failures.append(f"{method} {path}: {response.status_code} {response.text[:200]}")
    assert not failures, "\n".join(failures)
    assert _snapshot(a) == before


def _ws_url(key: str, path: str) -> str:
    return f"{BASE_URL.replace('http://', 'ws://', 1)}/k/{key}/api/v1{path}"


def _trigger(a: dict[str, Any], kind: str, reader_id: str) -> None:
    api, p, fresh = a["api"], a["params"], uuid.uuid4().hex[:8]
    match kind:
        case "vendor":
            _ok(api.patch(f"/vendor/{p['{vendor_id}']}", json={"comment": fresh}))
        case "filament":
            _ok(api.patch(f"/filament/{p['{filament_id}']}", json={"comment": fresh}))
        case "spool":
            _ok(api.patch(f"/spool/{p['{spool_id}']}", json={"lot_nr": fresh}))
        case "setting":
            _ok(api.post("/setting/currency", json=json.dumps(fresh[:3])))
        case "scan":
            _ok(api.post("/tag/scan", json={"uid": fresh.upper(), "reader_id": reader_id}))


# Every websocket route in spoolman/api/v1 (websockets are not in the OpenAPI document).
WEBSOCKETS = [
    ("/", "vendor"),
    ("/vendor", "vendor"),
    ("/vendor/{vendor_id}", "vendor"),
    ("/filament", "filament"),
    ("/filament/{filament_id}", "filament"),
    ("/spool", "spool"),
    ("/spool/{spool_id}", "spool"),
    ("/setting", "setting"),
    ("/setting/{key}", "setting"),
    ("/tag/scan", "scan"),
    ("/tag/scan/{reader_id}", "scan"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "kind"), WEBSOCKETS)
async def test_b_hears_nothing_of_a(a: dict[str, Any], b_key: str, path: str, kind: str):
    reader_id = f"sweep-{uuid.uuid4().hex[:8]}"
    path = path.replace("{key}", "currency").replace("{reader_id}", reader_id)
    path = _path_for(path, a)
    async with connect(_ws_url(a["key"], path)) as a_ws, connect(_ws_url(b_key, path)) as b_ws:
        # Subscriptions register right after the handshake; give the server a beat.
        await asyncio.sleep(0.2)
        _trigger(a, kind, reader_id)
        # A hearing its own change proves the trigger reaches this socket, so B's silence means something.
        assert json.loads(await asyncio.wait_for(a_ws.recv(), timeout=RECV_TIMEOUT_S))
        with contextlib.suppress(TimeoutError, ConnectionClosed):
            event = await asyncio.wait_for(b_ws.recv(), timeout=QUIET_PERIOD_S)
            pytest.fail(f"B heard A's change on {path}: {event}")
