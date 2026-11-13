"""Tags and the scan relay are confined to the scanning account."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.access.scope import Scope, scope_for_user
from spoolman.api.v1 import tag as tag_api
from spoolman.api.v1.models import TagScan
from spoolman.database import filament, spool, tag
from spoolman.exceptions import ItemNotFoundError
from spoolman.scanrelay import ScanRelay
from spoolman.tags.decode import DecodedTag
from spoolman.ws import scan_websocket_manager
from tests.support.dbsession import make_user
from tests.test_ws_broadcast import FakeWebSocket

UID = "04A2B3C4D5E6F7"


async def _two(db: AsyncSession) -> tuple[Scope, Scope]:
    a = await make_user(db, "a", "A")
    b = await make_user(db, "b", "B")
    await db.commit()
    return scope_for_user(a.id), scope_for_user(b.id)


async def _spool(db: AsyncSession, scope: Scope):  # noqa: ANN202
    f = await filament.create(db=db, scope=scope, density=1.24, diameter=1.75)
    return await spool.create(db=db, scope=scope, filament_id=f.id)


@pytest.mark.asyncio
async def test_same_uid_two_accounts_resolve_to_own_spool(db: AsyncSession):
    a, b = await _two(db)
    sa, sb = await _spool(db, a), await _spool(db, b)
    await tag.link_spool(db=db, scope=a, spool_id=sa.id, uid=UID)
    await tag.link_spool(db=db, scope=b, spool_id=sb.id, uid=UID)  # no conflict across accounts
    assert (await tag.find_by_uid(db, a, UID)).id == sa.id
    assert (await tag.find_by_uid(db, b, UID)).id == sb.id


@pytest.mark.asyncio
async def test_scan_unknown_to_scanner_even_if_other_account_has_it(db: AsyncSession):
    a, b = await _two(db)
    await tag.link_spool(db=db, scope=a, spool_id=(await _spool(db, a)).id, uid=UID)
    assert await tag.find_by_uid(db, b, UID) is None
    assert await tag.find_spool_by_uid(db, b, UID) is None
    with pytest.raises(ItemNotFoundError):
        await tag.unlink_spool(db=db, scope=b, spool_id=(await _spool(db, a)).id, uid=UID)


@pytest.mark.asyncio
async def test_autocreate_creates_vendor_filament_spool_in_scope(db: AsyncSession):
    a, b = await _two(db)
    decoded = DecodedTag(material="PLA", name="X", vendor_name="Acme", diameter=1.75, density=1.24, weight=1000.0)
    created = await tag.create_spool_from_decoded_tag(
        db=db, scope=a, uid=UID, tag_format="openprinttag", decoded=decoded
    )
    assert created.owner_id == a.account_id
    assert created.filament.owner_id == a.account_id
    assert created.filament.vendor.owner_id == a.account_id
    # b scanning the same uid with create is not blocked by a's tag, and gets its own rows
    other = await tag.create_spool_from_decoded_tag(db=db, scope=b, uid=UID, tag_format="openprinttag", decoded=decoded)
    assert other.id != created.id
    assert other.filament.vendor.owner_id == b.account_id


@pytest.mark.asyncio
async def test_relay_not_delivered_cross_account():
    a, b = scope_for_user(1), scope_for_user(2)
    ws_a, ws_a_reader, ws_b, ws_b_reader = (FakeWebSocket() for _ in range(4))
    pools = [
        (tag_api._pool(a), ws_a),  # noqa: SLF001
        (tag_api._pool(a, "ip-1"), ws_a_reader),  # noqa: SLF001
        (tag_api._pool(b), ws_b),  # noqa: SLF001
        (tag_api._pool(b, "ip-1"), ws_b_reader),  # noqa: SLF001
    ]
    for pool, ws in pools:
        scan_websocket_manager.connect(pool, ws, scope_for_user(int(pool[0])))
    try:
        await tag_api._broadcast(a, TagScan(uid=UID, reader_id="ip-1", created=False))  # noqa: SLF001
    finally:
        for pool, ws in pools:
            scan_websocket_manager.disconnect(pool, ws)
    assert len(ws_a.sent) == 1
    assert len(ws_a_reader.sent) == 1
    assert ws_b.sent == []
    assert ws_b_reader.sent == []


def test_readers_and_debounce_are_per_account():
    relay = ScanRelay()
    relay.register(1, "ip-1", "A reader", UID)
    assert relay.readers(2) == []
    assert relay.should_broadcast(1, UID, "ip-1", None) is True
    assert relay.should_broadcast(2, UID, "ip-1", None) is True  # not debounced by account 1's scan
    relay.register(2, "ip-1", "B reader")
    assert [r.name for r in relay.readers(1)] == ["A reader"]
