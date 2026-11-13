"""Tests for API key generation, hashing and verification."""

import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.auth import keys
from spoolman.exceptions import ItemNotFoundError
from tests.support.dbsession import make_user


def test_key_format():
    assert re.fullmatch(r"[0-9A-Za-z]{43}", keys.generate_key())


@pytest.mark.asyncio
async def test_hash_stored_not_raw(db: AsyncSession) -> None:
    user = await make_user(db)
    row, raw = await keys.create_key(db, user.id, "p", "edit")
    assert row.hash == keys.hash_key(raw) != raw
    assert row.prefix == raw[:8]


@pytest.mark.asyncio
async def test_verify_roundtrip(db: AsyncSession) -> None:
    user = await make_user(db)
    row, raw = await keys.create_key(db, user.id, "p", "view")
    found = await keys.verify_key(db, raw)
    assert found is not None
    assert found[0].id == user.id
    assert found[1].id == row.id
    assert await keys.verify_key(db, "nope") is None


@pytest.mark.asyncio
async def test_revoked_key_rejected(db: AsyncSession) -> None:
    user = await make_user(db)
    row, raw = await keys.create_key(db, user.id, "p", "edit")
    await keys.revoke_key(db, user.id, row.id)
    assert await keys.verify_key(db, raw) is None


@pytest.mark.asyncio
async def test_disabled_user_key_rejected(db: AsyncSession) -> None:
    user = await make_user(db)
    _, raw = await keys.create_key(db, user.id, "p", "edit")
    user.disabled = True
    await db.flush()
    assert await keys.verify_key(db, raw) is None


@pytest.mark.asyncio
async def test_last_used_throttled(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    user = await make_user(db)
    _, raw = await keys.create_key(db, user.id, "p", "edit")
    t0 = datetime(2026, 1, 1, 12, 0, 0)  # noqa: DTZ001 - stored naive, as UTC
    monkeypatch.setattr(keys, "utcnow", lambda: t0)
    assert (await keys.verify_key(db, raw))[1].last_used == t0
    monkeypatch.setattr(keys, "utcnow", lambda: t0 + timedelta(seconds=1))
    assert (await keys.verify_key(db, raw))[1].last_used == t0
    monkeypatch.setattr(keys, "utcnow", lambda: t0 + timedelta(seconds=61))
    assert (await keys.verify_key(db, raw))[1].last_used == t0 + timedelta(seconds=61)


@pytest.mark.asyncio
async def test_revoke_other_users_key_not_found(db: AsyncSession) -> None:
    a = await make_user(db, "a")
    b = await make_user(db, "b")
    row, _ = await keys.create_key(db, a.id, "p", "edit")
    with pytest.raises(ItemNotFoundError):
        await keys.revoke_key(db, b.id, row.id)
    assert [k.id for k in await keys.list_keys(db, a.id)] == [row.id]
