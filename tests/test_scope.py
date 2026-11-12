from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.access.principal import Principal
from spoolman.access.scope import Level, ensure_same_account, get_scope, owned, require_level, scope_for_user
from spoolman.database.models import Vendor
from spoolman.exceptions import ItemNotFoundError
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
    view = get_scope(Principal(1, "x", False, "key", "view"))
    with pytest.raises(HTTPException) as exc:
        require_level(Level.EDIT)(view)
    assert exc.value.status_code == 403
    assert exc.value.detail == {"message": "View only"}
    assert require_level(Level.VIEW)(view) is view
    assert require_level(Level.EDIT)(scope_for_user(1)).level == Level.OWNER


def test_ensure_same_account_raises_not_found() -> None:
    scope = scope_for_user(1)
    ensure_same_account(scope, _vendor(1, "ok"), None)
    with pytest.raises(ItemNotFoundError):
        ensure_same_account(scope, _vendor(1, "ok"), _vendor(2, "other"))
