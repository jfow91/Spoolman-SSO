"""Spools, settings, extra fields, search and export are confined to the caller's own account."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.access.principal import Principal
from spoolman.access.scope import Level, Scope, scope_for_user
from spoolman.api.v1 import export as export_api
from spoolman.api.v1 import setting as setting_api
from spoolman.database import filament, models, search, spool
from spoolman.database import setting as db_setting
from spoolman.database.extra_field_query import find_extra_field_values
from spoolman.exceptions import AccessDeniedError, ItemNotFoundError
from spoolman.extra_field_registry import extra_field_cache
from spoolman.extra_fields import (
    EntityType,
    ExtraField,
    ExtraFieldType,
    add_or_update_extra_field,
    delete_extra_field,
    get_extra_fields,
)
from spoolman.settings import SETTINGS, parse_setting
from tests.support.dbsession import make_user


@pytest.fixture(autouse=True)
def _empty_extra_field_cache():  # noqa: ANN202
    # Every test's in-memory database reuses user ids, so a cached registry would leak between tests.
    extra_field_cache.clear()
    yield
    extra_field_cache.clear()


async def _two(db: AsyncSession) -> tuple[Scope, Scope]:
    a = await make_user(db, "a", "A")
    b = await make_user(db, "b", "B")
    return scope_for_user(a.id), scope_for_user(b.id)


async def _spool(db: AsyncSession, scope: Scope, **kwargs):  # noqa: ANN003, ANN202
    f = await filament.create(db=db, scope=scope, name="Acme PLA", density=1.24, diameter=1.75, weight=1000)
    return await spool.create(db=db, scope=scope, filament_id=f.id, spool_weight=200, **kwargs)


def _admin(scope: Scope) -> Scope:
    return Scope(
        Principal(scope.account_id, "", is_admin=True, via="session", key_level=None), scope.account_id, Level.OWNER
    )


@pytest.mark.asyncio
async def test_spool_isolated(db: AsyncSession):
    a, b = await _two(db)
    s = await _spool(db, a, location="Shelf A", lot_nr="LOT-A")
    assert s.owner_id == a.account_id

    assert [x.id for x in (await spool.find(db=db, scope=a))[0]] == [s.id]
    assert (await spool.find(db=db, scope=b))[0] == []
    for group_by in ("filament", "vendor", "location"):
        assert (await spool.find_groups(db=db, scope=b, group_by=group_by)).groups == []
        assert len((await spool.find_groups(db=db, scope=a, group_by=group_by)).groups) == 1
    assert (await spool.find_groups(db=db, scope=b, group_by="filament", include_empty=True)).groups == []
    assert await spool.find_locations(db=db, scope=b) == []
    assert await spool.find_lot_numbers(db=db, scope=b) == []
    assert await spool.find_locations(db=db, scope=a) == ["Shelf A"]
    assert await spool.find_lot_numbers(db=db, scope=a) == ["LOT-A"]

    with pytest.raises(ItemNotFoundError):
        await spool.get_by_id(db, b, s.id)
    with pytest.raises(ItemNotFoundError):
        await spool.update(db=db, scope=b, spool_id=s.id, data={"comment": "x"})
    with pytest.raises(ItemNotFoundError):
        await spool.use_weight(db, b, s.id, 10)
    with pytest.raises(ItemNotFoundError):
        await spool.use_length(db, b, s.id, 10)
    with pytest.raises(ItemNotFoundError):
        await spool.measure(db, b, s.id, 500)
    await spool.rename_location(db=db, scope=b, current_name="Shelf A", new_name="Stolen")
    with pytest.raises(ItemNotFoundError):
        await spool.delete(db, b, s.id)

    fresh = await spool.get_by_id(db, a, s.id)
    assert fresh.used_weight == 0
    assert fresh.location == "Shelf A"


@pytest.mark.asyncio
async def test_spool_with_other_accounts_filament_rejected(db: AsyncSession):
    a, b = await _two(db)
    f = await filament.create(db=db, scope=a, density=1.24, diameter=1.75)
    with pytest.raises(ItemNotFoundError):
        await spool.create(db=db, scope=b, filament_id=f.id)
    s = await _spool(db, b)
    with pytest.raises(ItemNotFoundError):
        await spool.update(db=db, scope=b, spool_id=s.id, data={"filament_id": f.id})


@pytest.mark.asyncio
async def test_new_user_settings_defaults(db: AsyncSession):
    a, _ = await _two(db)
    for key, definition in SETTINGS.items():
        response = await setting_api.get(db=db, scope=a, key=key)
        assert response.value == definition.default, key
        assert response.is_set is False, key
    all_settings = await setting_api.find(db=db, scope=a)
    assert {k: v.value for k, v in all_settings.items()} == {k: d.default for k, d in SETTINGS.items()}
    for entity_type in EntityType:
        assert await get_extra_fields(db, a, entity_type) == []


@pytest.mark.asyncio
async def test_settings_per_account(db: AsyncSession):
    a, b = await _two(db)
    currency = parse_setting("currency")
    await db_setting.update(db=db, scope=a, definition=currency, value=json.dumps("AUD"))
    assert (await db_setting.get(db, a, currency)).value == json.dumps("AUD")
    assert (await db_setting.get(db, b, currency)).value == json.dumps("EUR")
    assert (await setting_api.get(db=db, scope=b, key="currency")).is_set is False
    assert "currency" not in [s.key for s in await db_setting.get_all(db, b)]


@pytest.mark.asyncio
async def test_extra_field_definitions_per_account(db: AsyncSession):
    a, b = await _two(db)
    field = ExtraField(key="brand", name="Brand", field_type=ExtraFieldType.text, entity_type=EntityType.spool)
    await add_or_update_extra_field(db, a, EntityType.spool, field)
    await add_or_update_extra_field(db, b, EntityType.spool, field)
    await _spool(db, a, extra={"brand": json.dumps("A brand")})
    sb = await _spool(db, b, extra={"brand": json.dumps("B brand")})

    assert await find_extra_field_values(db=db, scope=b, entity_type=EntityType.spool, field_key="brand") == ["B brand"]

    # Deleting the definition in A clears A's values only.
    await delete_extra_field(db, a, EntityType.spool, "brand")
    assert await get_extra_fields(db, a, EntityType.spool) == []
    assert [f.key for f in await get_extra_fields(db, b, EntityType.spool)] == ["brand"]
    assert (await db.execute(select(models.SpoolField.spool_id))).scalars().all() == [sb.id]


@pytest.mark.asyncio
async def test_search_scoped(db: AsyncSession):
    a, b = await _two(db)
    s = await _spool(db, a, location="Acme shelf")
    result = await search.search(db=db, scope=b, query="Acme")
    assert (result.spools, result.filaments, result.vendors) == ([], [], [])
    assert (await search.search(db=db, scope=b, query=str(s.id))).spools == []
    hits = await search.search(db=db, scope=a, query="Acme")
    assert [m.spool.id for m in hits.spools] == [s.id]
    assert len(hits.filaments) == 1


@pytest.mark.asyncio
async def test_export_scoped(db: AsyncSession):
    a, b = await _two(db)
    sa = await _spool(db, a, comment="mine")
    await _spool(db, b, comment="theirs")

    as_json = await export_api.export_spools(db=db, scope=a, fmt=export_api.ExportFormat.JSON)
    rows = json.loads(as_json.body)
    assert [r["id"] for r in rows] == [sa.id]

    as_csv = (await export_api.export_spools(db=db, scope=a, fmt=export_api.ExportFormat.CSV)).body.decode()
    assert "mine" in as_csv
    assert "theirs" not in as_csv


@pytest.mark.asyncio
async def test_base_url_admin_only(db: AsyncSession):
    a, b = await _two(db)
    with pytest.raises(AccessDeniedError):
        await setting_api.update(db=db, scope=a, key="base_url", body=json.dumps("http://x"))

    response = await setting_api.update(db=db, scope=_admin(a), key="base_url", body=json.dumps("http://x"))
    assert response.value == json.dumps("http://x")
    # App-wide: every account reads the same value.
    assert (await setting_api.get(db=db, scope=b, key="base_url")).value == json.dumps("http://x")
    assert (await setting_api.find(db=db, scope=b))["base_url"].value == json.dumps("http://x")
    assert await db_setting.get_all(db, a) == []
