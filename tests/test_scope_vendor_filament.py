"""Vendors and filaments are confined to the caller's own account."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.access.scope import Scope, scope_for_user
from spoolman.database import filament, vendor
from spoolman.database.tag import create_spool_from_decoded_tag
from spoolman.exceptions import ItemNotFoundError
from spoolman.tags.decode import DecodedTag
from tests.support.dbsession import make_user


async def _two(db: AsyncSession) -> tuple[Scope, Scope]:
    a = await make_user(db, "a", "A")
    b = await make_user(db, "b", "B")
    return scope_for_user(a.id), scope_for_user(b.id)


async def _filament(db: AsyncSession, scope: Scope, vendor_id: int | None = None):  # noqa: ANN202
    return await filament.create(db=db, scope=scope, density=1.24, diameter=1.75, vendor_id=vendor_id)


@pytest.mark.asyncio
async def test_vendor_isolated(db: AsyncSession):
    a, b = await _two(db)
    v = await vendor.create(db=db, scope=a, name="Acme")
    assert v.owner_id == a.account_id
    assert [x.id for x in (await vendor.find(db=db, scope=a))[0]] == [v.id]
    assert (await vendor.find(db=db, scope=b))[0] == []
    with pytest.raises(ItemNotFoundError):
        await vendor.get_by_id(db, b, v.id)
    with pytest.raises(ItemNotFoundError):
        await vendor.update(db=db, scope=b, vendor_id=v.id, data={"name": "x"})
    with pytest.raises(ItemNotFoundError):
        await vendor.delete(db, b, v.id)
    assert (await vendor.get_by_id(db, a, v.id)).name == "Acme"


@pytest.mark.asyncio
async def test_filament_isolated(db: AsyncSession):
    a, b = await _two(db)
    f = await _filament(db, a)
    assert f.owner_id == a.account_id
    assert (await filament.find(db=db, scope=b))[0] == []
    assert await filament.find_materials(db=db, scope=b) == []
    with pytest.raises(ItemNotFoundError):
        await filament.get_by_id(db, b, f.id)
    with pytest.raises(ItemNotFoundError):
        await filament.update(db=db, scope=b, filament_id=f.id, data={"name": "x"})
    with pytest.raises(ItemNotFoundError):
        await filament.delete(db, b, f.id)
    assert [x.id for x in (await filament.find(db=db, scope=a))[0]] == [f.id]


@pytest.mark.asyncio
async def test_filament_with_other_accounts_vendor_rejected(db: AsyncSession):
    a, b = await _two(db)
    v = await vendor.create(db=db, scope=a, name="Acme")
    with pytest.raises(ItemNotFoundError):
        await _filament(db, b, vendor_id=v.id)
    f = await _filament(db, b)
    with pytest.raises(ItemNotFoundError):
        await filament.update(db=db, scope=b, filament_id=f.id, data={"vendor_id": v.id})


@pytest.mark.asyncio
async def test_same_vendor_name_in_two_accounts_ok(db: AsyncSession):
    a, b = await _two(db)
    await vendor.create(db=db, scope=a, name="Acme")
    await vendor.create(db=db, scope=b, name="Acme")
    assert len((await vendor.find(db=db, scope=a))[0]) == 1


@pytest.mark.asyncio
async def test_externaldb_import_lands_in_scope(db: AsyncSession):
    # The only server-side path that creates catalog rows from outside data is tag auto-create.
    a, b = await _two(db)
    spool = await create_spool_from_decoded_tag(
        db=db, scope=a, uid="04A2B3C4D5E601", tag_format=None, decoded=DecodedTag(material="PLA", vendor_name="Acme")
    )
    assert spool.filament.owner_id == spool.filament.vendor.owner_id == a.account_id
    assert (await vendor.find(db=db, scope=b))[0] == []
