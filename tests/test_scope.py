"""Tests for the scope helpers."""

from datetime import datetime
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.access.principal import Principal, get_principal
from spoolman.access.scope import Level, Scope, get_scope, owned, require_level, scope_for_user
from spoolman.api.v1.router import accessdeniederror_exception_handler
from spoolman.database.models import Vendor
from spoolman.exceptions import AccessDeniedError
from tests.support.dbsession import make_user


def _vendor(owner_id: int, name: str) -> Vendor:
    return Vendor(owner_id=owner_id, registered=datetime.utcnow(), name=name)


@pytest.mark.asyncio
async def test_owned_filters_rows(db: AsyncSession) -> None:
    a = await make_user(db, "a")
    b = await make_user(db, "b")
    db.add_all([_vendor(a.id, "va"), _vendor(b.id, "vb")])
    await db.flush()
    rows = (await db.execute(owned(select(Vendor), Vendor, scope_for_user(a.id)))).scalars().all()
    assert [v.name for v in rows] == ["va"]


def test_key_scope_levels() -> None:
    def p(via: str, level: str | None) -> Principal:
        return Principal(user_id=5, display_name="x", is_admin=False, via=via, key_level=level)  # type: ignore[arg-type]

    assert get_scope(p("session", None)).level == Level.OWNER
    assert get_scope(p("key", "edit")).level == Level.EDIT
    assert get_scope(p("key", "view")).level == Level.VIEW
    assert get_scope(p("key", "view")).account_id == 5


def test_require_level_blocks_view_key_on_write() -> None:
    view = get_scope(Principal(1, "x", is_admin=False, via="key", key_level="view"))
    with pytest.raises(AccessDeniedError, match="View only"):
        require_level(Level.EDIT)(view)
    assert require_level(Level.VIEW)(view) is view
    assert require_level(Level.EDIT)(scope_for_user(1)).level == Level.OWNER


def test_view_key_gets_403_message_body() -> None:
    app = FastAPI()
    app.add_exception_handler(AccessDeniedError, accessdeniederror_exception_handler)
    app.dependency_overrides[get_principal] = lambda: Principal(1, "x", is_admin=False, via="key", key_level="view")

    @app.post("/w")
    def write(_scope: Annotated[Scope, Depends(require_level(Level.EDIT))]) -> dict:
        return {}

    r = TestClient(app).post("/w")
    assert r.status_code == 403
    assert r.json() == {"message": "View only"}
