"""Account endpoints: who am I, personal keys, claiming pre-multi-user data, and user administration."""

# ruff: noqa: D103

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.access.principal import Principal, get_principal, require_session
from spoolman.access.scope import owned, scope_for_user
from spoolman.auth import keys, sessions
from spoolman.database import models
from spoolman.database.database import get_db_session
from spoolman.exceptions import AccessDeniedError, ItemNotFoundError

router = APIRouter(tags=["account"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]

_CLAIM_TABLES = {
    "vendor": models.Vendor,
    "filament": models.Filament,
    "spool": models.Spool,
    "tag": models.Tag,
    "setting": models.Setting,
}


class Me(BaseModel):
    id: int
    display_name: str
    email: str | None
    is_admin: bool
    via: Literal["session", "key"]
    can_claim: bool


class KeyOut(BaseModel):
    id: int
    name: str
    prefix: str
    level: Literal["edit", "view"]
    created: datetime
    last_used: datetime | None


class KeyCreated(KeyOut):
    key: str


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    level: Literal["edit", "view"]


class UserOut(BaseModel):
    id: int
    display_name: str
    email: str | None
    is_admin: bool
    disabled: bool
    last_login: datetime | None


def require_admin(principal: Annotated[Principal, Depends(require_session)]) -> Principal:
    if not principal.is_admin:
        raise AccessDeniedError("Admin only")
    return principal


async def _unclaimed(db: AsyncSession) -> models.User | None:
    stmt = select(models.User).where(models.User.oidc_sub == models.UNCLAIMED_SUB)
    return (await db.execute(stmt)).scalar_one_or_none()


@router.get("/me")
async def get_me(db: DbSession, principal: Annotated[Principal, Depends(get_principal)]) -> Me:
    user = await db.get(models.User, principal.user_id)
    can_claim = False
    if principal.is_admin and principal.via == "session" and (un := await _unclaimed(db)) is not None:
        scope = scope_for_user(un.id)
        for model in _CLAIM_TABLES.values():
            if (await db.execute(select(exists(owned(select(model), model, scope))))).scalar():
                can_claim = True
                break
    return Me(
        id=principal.user_id,
        display_name=principal.display_name,
        email=user.email if user else None,
        is_admin=principal.is_admin,
        via=principal.via,
        can_claim=can_claim,
    )


@router.post("/me/claim", responses={409: {"description": "Nothing to claim"}})
async def claim(db: DbSession, admin: Annotated[Principal, Depends(require_admin)]) -> dict[str, int]:
    """Move everything the pre-multi-user placeholder account owns to the caller, in one transaction."""
    un = await _unclaimed(db)
    if un is None:
        raise HTTPException(409, "Nothing to claim")
    src, dst = scope_for_user(un.id), scope_for_user(admin.user_id)
    # The caller's own tag/setting wins over an unclaimed duplicate; drop the duplicate so the move cannot collide.
    for model, col in ((models.Tag, models.Tag.uid), (models.Setting, models.Setting.key)):
        mine = owned(select(col), model, dst)
        await db.execute(owned(delete(model).where(col.in_(mine)), model, src))
    counts = {}
    for name, model in _CLAIM_TABLES.items():
        result = await db.execute(owned(update(model), model, src).values(owner_id=admin.user_id))
        counts[name] = result.rowcount  # type: ignore[attr-defined]
    if not any(counts.values()):
        raise HTTPException(409, "Nothing to claim")
    return counts


@router.get("/keys")
async def list_my_keys(db: DbSession, principal: Annotated[Principal, Depends(require_session)]) -> list[KeyOut]:
    rows = await keys.list_keys(db, principal.user_id)
    return [KeyOut.model_validate(r, from_attributes=True) for r in rows if not r.revoked]


@router.post("/keys", status_code=201)
async def create_my_key(
    body: KeyIn,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_session)],
) -> KeyCreated:
    row, raw = await keys.create_key(db, principal.user_id, body.name, body.level)
    return KeyCreated(**KeyOut.model_validate(row, from_attributes=True).model_dump(), key=raw)


@router.delete("/keys/{key_id}", status_code=204)
async def delete_my_key(
    key_id: int,
    db: DbSession,
    principal: Annotated[Principal, Depends(require_session)],
) -> Response:
    await keys.revoke_key(db, principal.user_id, key_id)
    return Response(status_code=204)


async def _target(db: AsyncSession, user_id: int) -> models.User:
    user = await db.get(models.User, user_id)
    if user is None or user.oidc_sub == models.UNCLAIMED_SUB:
        raise ItemNotFoundError(f"No user with ID {user_id} found.")
    return user


async def _cut_access(db: AsyncSession, user_id: int) -> None:
    await sessions.delete_user_sessions(db, user_id)
    await db.execute(update(models.ApiKey).where(models.ApiKey.user_id == user_id).values(revoked=True))


@router.get("/admin/users")
async def list_users(db: DbSession, _admin: Annotated[Principal, Depends(require_admin)]) -> list[UserOut]:
    stmt = select(models.User).where(models.User.oidc_sub != models.UNCLAIMED_SUB).order_by(models.User.id)
    return [UserOut.model_validate(u, from_attributes=True) for u in (await db.execute(stmt)).scalars()]


@router.post("/admin/users/{user_id}/disable", status_code=204)
async def disable_user(user_id: int, db: DbSession, admin: Annotated[Principal, Depends(require_admin)]) -> Response:
    user = await _target(db, user_id)
    if user.id == admin.user_id:
        raise HTTPException(400, "You cannot disable yourself")
    user.disabled = True
    await _cut_access(db, user.id)
    return Response(status_code=204)


@router.post("/admin/users/{user_id}/enable", status_code=204)
async def enable_user(user_id: int, db: DbSession, _admin: Annotated[Principal, Depends(require_admin)]) -> Response:
    (await _target(db, user_id)).disabled = False
    return Response(status_code=204)


@router.post("/admin/users/{user_id}/revoke-access", status_code=204)
async def revoke_access(user_id: int, db: DbSession, _admin: Annotated[Principal, Depends(require_admin)]) -> Response:
    await _cut_access(db, (await _target(db, user_id)).id)
    return Response(status_code=204)
