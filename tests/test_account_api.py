"""Tests for the account API: me, keys, claim, admin users."""

from collections.abc import AsyncGenerator
from datetime import datetime

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.access.principal import Principal, get_principal
from spoolman.api.v1 import account, spool
from spoolman.api.v1.router import accessdeniederror_exception_handler, itemnotfounderror_exception_handler
from spoolman.auth import keys, sessions
from spoolman.database import models
from spoolman.database.database import get_db_session
from spoolman.exceptions import AccessDeniedError, ItemNotFoundError
from tests.support.dbsession import make_user

NOW = datetime.utcnow().replace(microsecond=0)


class Ctx:
    """The test app plus a switch for who is calling."""

    def __init__(self, client: httpx.AsyncClient, state: dict) -> None:
        self.client = client
        self.state = state

    def as_user(self, user: models.User, via: str = "session", level: str | None = None) -> None:
        self.state["p"] = Principal(user.id, user.display_name, user.is_admin, via, level)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def ctx(db: AsyncSession) -> AsyncGenerator[Ctx, None]:
    app = FastAPI()
    app.add_exception_handler(AccessDeniedError, accessdeniederror_exception_handler)
    app.add_exception_handler(ItemNotFoundError, itemnotfounderror_exception_handler)
    app.include_router(account.router)
    app.include_router(spool.router)
    state: dict = {}
    app.dependency_overrides[get_principal] = lambda: state["p"]

    async def _db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db_session] = _db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        yield Ctx(client, state)


async def _unclaimed(db: AsyncSession) -> models.User:
    return await make_user(db, models.UNCLAIMED_SUB, "Unclaimed")


def _vendor(owner: int, name: str = "v") -> models.Vendor:
    return models.Vendor(owner_id=owner, registered=NOW, name=name)


def _tag(owner: int, uid: str) -> models.Tag:
    return models.Tag(owner_id=owner, uid=uid, target_type="value", target_value="x", added=NOW)


def _setting(owner: int, key: str, value: str) -> models.Setting:
    return models.Setting(owner_id=owner, key=key, value=value, last_updated=NOW)


@pytest.mark.asyncio
async def test_me_session(db: AsyncSession, ctx: Ctx) -> None:
    admin = await make_user(db, "a", "Ann", admin=True)
    un = await _unclaimed(db)
    ctx.as_user(admin)
    body = (await ctx.client.get("/me")).json()
    assert body == {
        "id": admin.id,
        "display_name": "Ann",
        "email": None,
        "is_admin": True,
        "via": "session",
        "can_claim": False,
    }
    db.add(_vendor(un.id))
    await db.flush()
    assert (await ctx.client.get("/me")).json()["can_claim"] is True
    other = await make_user(db, "b", "Bob")
    ctx.as_user(other)
    assert (await ctx.client.get("/me")).json()["can_claim"] is False


@pytest.mark.asyncio
async def test_me_key(db: AsyncSession, ctx: Ctx) -> None:
    u = await make_user(db)
    ctx.as_user(u, "key", "view")
    r = await ctx.client.get("/me")
    assert r.status_code == 200
    assert r.json()["via"] == "key"


@pytest.mark.asyncio
async def test_claim_moves_all_rows_and_is_once(db: AsyncSession, ctx: Ctx) -> None:
    admin = await make_user(db, "a", admin=True)
    un = await _unclaimed(db)
    v = _vendor(un.id)
    f = models.Filament(owner_id=un.id, registered=NOW, density=1.0, diameter=1.75)
    db.add_all([v, f])
    await db.flush()
    db.add_all(
        [
            models.Spool(owner_id=un.id, registered=NOW, filament_id=f.id, used_weight=0),
            _tag(un.id, "T1"),
            _tag(admin.id, "T2"),
            _setting(un.id, "currency", "EUR"),
        ],
    )
    await db.flush()
    ctx.as_user(admin)
    r = await ctx.client.post("/me/claim")
    assert r.status_code == 200
    assert r.json() == {"vendor": 1, "filament": 1, "spool": 1, "tag": 1, "setting": 1}
    for model in (models.Vendor, models.Filament, models.Spool, models.Tag, models.Setting):
        count = select(func.count()).select_from(model).where(model.owner_id == un.id)
        assert (await db.execute(count)).scalar() == 0
    assert (await ctx.client.post("/me/claim")).status_code == 409


@pytest.mark.asyncio
async def test_claim_keeps_callers_setting_and_tag_on_collision(db: AsyncSession, ctx: Ctx) -> None:
    admin = await make_user(db, "a", admin=True)
    un = await _unclaimed(db)
    db.add_all(
        [
            _setting(admin.id, "currency", "AUD"),
            _setting(un.id, "currency", "EUR"),
            _setting(un.id, "other", "1"),
            _tag(admin.id, "SAME"),
            _tag(un.id, "SAME"),
            _tag(un.id, "NEW"),
        ],
    )
    await db.flush()
    ctx.as_user(admin)
    r = await ctx.client.post("/me/claim")
    assert r.status_code == 200
    assert r.json()["setting"] == 1
    assert r.json()["tag"] == 1
    settings = {s.key: s.value for s in (await db.execute(select(models.Setting))).scalars()}
    assert settings == {"currency": "AUD", "other": "1"}
    tags = (await db.execute(select(models.Tag.uid, models.Tag.owner_id))).all()
    assert sorted(tags) == [("NEW", admin.id), ("SAME", admin.id)]


@pytest.mark.asyncio
async def test_claim_requires_admin(db: AsyncSession, ctx: Ctx) -> None:
    user = await make_user(db, "b")
    un = await _unclaimed(db)
    db.add(_vendor(un.id))
    await db.flush()
    ctx.as_user(user)
    r = await ctx.client.post("/me/claim")
    assert r.status_code == 403
    assert "message" in r.json()


@pytest.mark.asyncio
async def test_key_crud(db: AsyncSession, ctx: Ctx) -> None:
    u = await make_user(db)
    ctx.as_user(u)
    r = await ctx.client.post("/keys", json={"name": "printer", "level": "edit"})
    assert r.status_code == 201
    kid = r.json()["id"]
    assert [k["name"] for k in (await ctx.client.get("/keys")).json()] == ["printer"]
    assert (await ctx.client.delete(f"/keys/{kid}")).status_code == 204
    assert (await ctx.client.delete(f"/keys/{kid + 99}")).status_code == 404
    assert (await ctx.client.post("/keys", json={"name": "", "level": "edit"})).status_code == 422
    assert (await ctx.client.post("/keys", json={"name": "x" * 65, "level": "edit"})).status_code == 422
    assert (await ctx.client.post("/keys", json={"name": "x", "level": "owner"})).status_code == 422
    ctx.as_user(u, "key", "edit")
    assert (await ctx.client.get("/keys")).status_code == 403


@pytest.mark.asyncio
async def test_key_raw_only_returned_on_create(db: AsyncSession, ctx: Ctx) -> None:
    u = await make_user(db)
    ctx.as_user(u)
    created = (await ctx.client.post("/keys", json={"name": "p", "level": "view"})).json()
    assert len(created["key"]) == 43
    listed = (await ctx.client.get("/keys")).json()[0]
    assert set(listed) == {"id", "name", "prefix", "level", "created", "last_used"}
    assert listed["prefix"] == created["key"][:8]


@pytest.mark.asyncio
async def test_admin_users_requires_admin(db: AsyncSession, ctx: Ctx) -> None:
    user = await make_user(db, "b", "Bob")
    admin = await make_user(db, "a", "Ann", admin=True)
    await _unclaimed(db)
    ctx.as_user(user)
    for method, path in [
        ("GET", "/admin/users"),
        ("POST", f"/admin/users/{admin.id}/disable"),
        ("POST", f"/admin/users/{admin.id}/enable"),
        ("POST", f"/admin/users/{admin.id}/revoke-access"),
    ]:
        r = await ctx.client.request(method, path)
        assert r.status_code == 403, path
        assert "message" in r.json()
    ctx.as_user(admin)
    listed = (await ctx.client.get("/admin/users")).json()
    assert sorted(u["display_name"] for u in listed) == ["Ann", "Bob"]
    assert set(listed[0]) == {"id", "display_name", "email", "is_admin", "disabled", "last_login"}


@pytest.mark.asyncio
async def test_disable_user_kills_sessions_and_keys(db: AsyncSession, ctx: Ctx) -> None:
    admin = await make_user(db, "a", admin=True)
    bob = await make_user(db, "b")
    token = await sessions.create_session(db, bob.id)
    _, raw = await keys.create_key(db, bob.id, "k", "edit")
    ctx.as_user(admin)
    assert (await ctx.client.post(f"/admin/users/{bob.id}/disable")).status_code == 204
    assert bob.disabled is True
    assert await db.get(models.Session, sessions._hash(token)) is None  # noqa: SLF001
    assert (await db.execute(select(models.ApiKey.revoked))).scalar() is True
    assert (await ctx.client.post(f"/admin/users/{bob.id}/enable")).status_code == 204
    assert bob.disabled is False
    assert await keys.verify_key(db, raw) is None  # keys stay revoked after enabling


@pytest.mark.asyncio
async def test_revoke_access_keeps_user_enabled(db: AsyncSession, ctx: Ctx) -> None:
    admin = await make_user(db, "a", admin=True)
    bob = await make_user(db, "b")
    token = await sessions.create_session(db, bob.id)
    _, raw = await keys.create_key(db, bob.id, "k", "edit")
    ctx.as_user(admin)
    assert (await ctx.client.post(f"/admin/users/{bob.id}/revoke-access")).status_code == 204
    assert not bob.disabled
    assert await sessions.resolve_session(db, token) is None
    assert await keys.verify_key(db, raw) is None


@pytest.mark.asyncio
async def test_admin_cannot_disable_self(db: AsyncSession, ctx: Ctx) -> None:
    admin = await make_user(db, "a", admin=True)
    ctx.as_user(admin)
    assert (await ctx.client.post(f"/admin/users/{admin.id}/disable")).status_code == 400


@pytest.mark.asyncio
async def test_admin_cannot_act_on_unclaimed(db: AsyncSession, ctx: Ctx) -> None:
    admin = await make_user(db, "a", admin=True)
    un = await _unclaimed(db)
    ctx.as_user(admin)
    assert (await ctx.client.post(f"/admin/users/{un.id}/disable")).status_code == 404
    assert (await ctx.client.post("/admin/users/9999/enable")).status_code == 404


@pytest.mark.asyncio
async def test_admin_cannot_read_other_accounts_spools(db: AsyncSession, ctx: Ctx) -> None:
    a = await make_user(db, "a")
    b = await make_user(db, "b", admin=True)
    f = models.Filament(owner_id=a.id, registered=NOW, density=1.0, diameter=1.75)
    db.add(f)
    await db.flush()
    s = models.Spool(owner_id=a.id, registered=NOW, filament_id=f.id, used_weight=0)
    db.add(s)
    await db.flush()
    ctx.as_user(a)
    assert (await ctx.client.get(f"/spool/{s.id}")).status_code == 200
    ctx.as_user(b)
    assert (await ctx.client.get(f"/spool/{s.id}")).status_code == 404
