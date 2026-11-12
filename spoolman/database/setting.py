"""Helper functions for interacting with vendor database objects."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.access.scope import Scope, owned
from spoolman.api.v1.models import EventType, SettingEvent, SettingKV
from spoolman.database import models
from spoolman.exceptions import ItemNotFoundError
from spoolman.settings import SettingDefinition, parse_setting
from spoolman.ws import websocket_manager

SETTING_MAX_LENGTH = 2**16 - 1


async def update(
    *,
    db: AsyncSession,
    scope: Scope,
    definition: SettingDefinition,
    value: str,
) -> None:
    """Set a setting in the database."""
    if len(value) > SETTING_MAX_LENGTH:
        raise ValueError(f"Setting value is too big, max size is {SETTING_MAX_LENGTH} characters.")

    setting = models.Setting(
        owner_id=scope.account_id,
        key=definition.key,
        value=value,
        last_updated=datetime.utcnow().replace(microsecond=0),
    )
    await db.merge(setting)
    # Commit before notifying so the setting is durable and visible to subsequent
    # requests; post-commit notification must be the last, infallible step.
    await db.commit()
    await setting_changed(definition, value, EventType.UPDATED, scope.account_id)


async def _find(db: AsyncSession, scope: Scope, key: str) -> models.Setting | None:
    stmt = owned(select(models.Setting).where(models.Setting.key == key), models.Setting, scope)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get(db: AsyncSession, scope: Scope, definition: SettingDefinition) -> models.Setting:
    """Get a setting of the account. Unset, it is an unsaved row holding the default, with no `last_updated`."""
    setting = await _find(db, scope, definition.key)
    if setting is None:
        return models.Setting(owner_id=scope.account_id, key=definition.key, value=definition.default)
    return setting


async def get_all(db: AsyncSession, scope: Scope) -> list[models.Setting]:
    """Get all set settings of the account."""
    rows = await db.execute(owned(select(models.Setting), models.Setting, scope))
    return list(rows.scalars().all())


async def delete(db: AsyncSession, scope: Scope, definition: SettingDefinition) -> None:
    """Delete a setting of the account."""
    setting = await _find(db, scope, definition.key)
    if setting is None:
        raise ItemNotFoundError(f"Setting with key {definition.key} has not been set.")
    await db.delete(setting)
    # Commit before notifying so the deletion is durable and visible to subsequent
    # requests; post-commit notification must be the last, infallible step.
    await db.commit()
    await setting_changed(definition, None, EventType.DELETED, setting.owner_id)


async def get_app_setting(db: AsyncSession, key: str) -> str | None:
    """Get an app-wide setting (only `base_url`), or None if it has not been set."""
    row = await db.get(models.AppSetting, key)
    return None if row is None else row.value


async def set_app_setting(db: AsyncSession, key: str, value: str | None) -> None:
    """Set an app-wide setting (only `base_url`); None unsets it. The caller checks it is allowed."""
    if value is not None and len(value) > SETTING_MAX_LENGTH:
        raise ValueError(f"Setting value is too big, max size is {SETTING_MAX_LENGTH} characters.")
    row = await db.get(models.AppSetting, key)
    if value is None:
        if row is not None:
            await db.delete(row)
    else:
        await db.merge(models.AppSetting(key=key, value=value, last_updated=datetime.utcnow().replace(microsecond=0)))
    await db.commit()
    # owner None: base_url is app-wide, so every account hears about it.
    await setting_changed(parse_setting(key), value, EventType.DELETED if value is None else EventType.UPDATED, None)


async def setting_changed(
    definition: SettingDefinition,
    set_value: str | None,
    typ: EventType,
    owner_id: int | None,
) -> None:
    """Notify the owning account's websocket clients that a setting has changed (everyone's if owner_id is None)."""
    await websocket_manager.send(
        ("setting", str(definition.key)),
        SettingEvent(
            type=typ,
            resource="setting",
            date=datetime.utcnow(),
            payload=SettingKV.from_db(definition, set_value),
        ),
        owner_id,
    )
