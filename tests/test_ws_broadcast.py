"""Tests for SubscriptionTree's broadcast isolation.

Regression cover for a bug that predates the scan relay but is easiest to hit through it: one
subscriber that could not be written to used to abort the whole broadcast, so every other
subscriber in the same pool silently received nothing. On the shared tree that meant a browser
tab closing could stop Moonraker hearing about a spool.
"""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketState

from spoolman.access.scope import scope_for_user
from spoolman.api.v1.models import Event
from spoolman.database import filament, setting, spool
from spoolman.settings import parse_setting
from spoolman.ws import SubscriptionTree, websocket_manager
from tests.support.dbsession import make_user

OWNER = 1
SCOPE = scope_for_user(OWNER)


def _event() -> Event:
    """Build the smallest thing SubscriptionTree.send will serialize."""
    return Event(
        type="updated",
        resource="test",
        date=datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc),
        payload=Event.model_construct(),
    )


class FakeWebSocket:
    """A websocket that records what it was sent, and can fail or claim to be disconnected."""

    def __init__(self, *, fail: bool = False, disconnected: bool = False) -> None:
        """Initialize a connected socket, optionally one that fails or is already gone."""
        self.sent: list[str] = []
        self.fail = fail
        state = WebSocketState.DISCONNECTED if disconnected else WebSocketState.CONNECTED
        self.client_state = state
        self.application_state = state
        self.client = None

    async def send_text(self, text: str) -> None:
        """Record the frame, or blow up the way a closing socket does."""
        if self.fail:
            # What starlette raises for a socket that started closing after the state check.
            raise RuntimeError("connection is closing")
        self.sent.append(text)


@pytest.mark.asyncio
async def test_a_failing_subscriber_does_not_starve_the_others():
    """The bug: the send raised, the loop aborted, and everyone after it got nothing."""
    tree = SubscriptionTree()
    healthy_before, broken, healthy_after = FakeWebSocket(), FakeWebSocket(fail=True), FakeWebSocket()
    for ws in (healthy_before, broken, healthy_after):
        tree.add(("spool",), ws, SCOPE)

    await tree.send(("spool",), _event(), OWNER)

    assert len(healthy_before.sent) == 1
    assert len(healthy_after.sent) == 1


@pytest.mark.asyncio
async def test_a_failing_subscriber_is_dropped():
    """Dropped rather than left to fail again on every subsequent event."""
    tree = SubscriptionTree()
    broken, healthy = FakeWebSocket(fail=True), FakeWebSocket()
    tree.add(("spool",), broken, SCOPE)
    tree.add(("spool",), healthy, SCOPE)

    await tree.send(("spool",), _event(), OWNER)
    await tree.send(("spool",), _event(), OWNER)

    assert len(healthy.sent) == 2
    assert tree.children["spool"].subscribers.keys() == {healthy}


@pytest.mark.asyncio
async def test_a_disconnected_subscriber_is_dropped_from_its_own_pool():
    """The drop has to happen on the node being walked, not wherever the remaining path points."""
    tree = SubscriptionTree()
    dead, healthy = FakeWebSocket(disconnected=True), FakeWebSocket()
    # Both subscribe at the ROOT, so send() meets them while `path` still has ("spool",) left
    # to walk. Removing via that path would look inside the "spool" child instead.
    tree.add((), dead, SCOPE)
    tree.add((), healthy, SCOPE)

    await tree.send(("spool",), _event(), OWNER)

    assert tree.subscribers.keys() == {healthy}
    assert len(healthy.sent) == 1


@pytest.mark.asyncio
async def test_broadcast_still_propagates_down_the_tree_after_a_failure():
    """A failure at one level must not cost the levels below it their event."""
    tree = SubscriptionTree()
    broken_root, deep = FakeWebSocket(fail=True), FakeWebSocket()
    tree.add((), broken_root, SCOPE)
    tree.add(("spool", "1"), deep, SCOPE)

    await tree.send(("spool", "1"), _event(), OWNER)

    assert len(deep.sent) == 1


async def _two_accounts_and_root_sockets(db: AsyncSession):  # noqa: ANN202
    a = scope_for_user((await make_user(db, "a", "A")).id)
    b = scope_for_user((await make_user(db, "b", "B")).id)
    await db.commit()
    ws_a, ws_b = FakeWebSocket(), FakeWebSocket()
    websocket_manager.connect((), ws_a, a)
    websocket_manager.connect((), ws_b, b)
    return a, ws_a, ws_b


@pytest.mark.asyncio
async def test_event_not_delivered_to_other_account(db: AsyncSession):
    """A root subscriber of account B hears nothing about account A's spool."""
    a, ws_a, ws_b = await _two_accounts_and_root_sockets(db)
    try:
        f = await filament.create(db=db, scope=a, density=1.24, diameter=1.75)
        s = await spool.create(db=db, scope=a, filament_id=f.id)
        await spool.delete(db, a, s.id)
        await setting.update(db=db, scope=a, definition=parse_setting("currency"), value='"SEK"')
    finally:
        websocket_manager.disconnect((), ws_a)
        websocket_manager.disconnect((), ws_b)
    assert ws_b.sent == []
    assert len(ws_a.sent) == 4  # filament added, spool added, spool deleted, setting updated


@pytest.mark.asyncio
async def test_event_delivered_to_owner(db: AsyncSession):
    """The owning account's root subscriber hears every change, including the delete."""
    a, ws_a, ws_b = await _two_accounts_and_root_sockets(db)
    try:
        f = await filament.create(db=db, scope=a, density=1.24, diameter=1.75)
        s = await spool.create(db=db, scope=a, filament_id=f.id)
        await spool.delete(db, a, s.id)
    finally:
        websocket_manager.disconnect((), ws_a)
        websocket_manager.disconnect((), ws_b)
    assert [json.loads(m)["type"] for m in ws_a.sent] == ["added", "added", "deleted"]
