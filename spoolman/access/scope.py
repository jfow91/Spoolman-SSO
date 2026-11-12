"""Which account a request acts on, and at what level. The only place an owner filter is written."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from fastapi import Depends, HTTPException
from sqlalchemy import Select

from spoolman.access.principal import Principal, get_principal
from spoolman.database.models import Base
from spoolman.exceptions import ItemNotFoundError


class Level(IntEnum):
    VIEW = 1
    EDIT = 2
    OWNER = 3


@dataclass(frozen=True)
class Scope:
    principal: Principal
    account_id: int
    level: Level


def _scope(principal: Principal) -> Scope:
    level = Level.OWNER if principal.via == "session" else Level.EDIT if principal.key_level == "edit" else Level.VIEW
    return Scope(principal, principal.user_id, level)


def get_scope(principal: Principal = Depends(get_principal)) -> Scope:  # noqa: B008
    """FastAPI dependency (HTTP and websocket): the account this request acts on, and at what level."""
    return _scope(principal)


def scope_for_user(user_id: int) -> Scope:
    """Owner-level scope for a user, for tests and internal jobs."""
    return Scope(Principal(user_id, "", is_admin=False, via="session", key_level=None), user_id, Level.OWNER)


def require_level(min_level: Level) -> Callable[..., Scope]:
    """Dependency factory: refuse (403) a scope below `min_level`."""

    def dependency(scope: Scope = Depends(get_scope)) -> Scope:  # noqa: B008
        if scope.level < min_level:
            raise HTTPException(403, detail={"message": "View only"})
        return scope

    return dependency


def owned(stmt: Select, model: type[Base], scope: Scope) -> Select:
    return stmt.where(model.owner_id == scope.account_id)  # type: ignore[attr-defined]


def ensure_same_account(scope: Scope, *rows: Base | None) -> None:
    """Raise ItemNotFoundError if any row belongs to another account (None rows are skipped)."""
    for row in rows:
        if row is not None and row.owner_id != scope.account_id:  # type: ignore[attr-defined]
            raise ItemNotFoundError(f"No {type(row).__name__.lower()} with ID {row.id} found.")  # type: ignore[attr-defined]
