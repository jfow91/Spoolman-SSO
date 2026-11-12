"""Personal printer keys. The database holds only the SHA-256 of each key; the raw key is shown once."""

import hashlib
import secrets
from datetime import timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.auth.users import utcnow
from spoolman.database import models
from spoolman.exceptions import ItemNotFoundError

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
KEY_LENGTH = 43  # base62 digits needed for 32 bytes
LAST_USED_AFTER = timedelta(seconds=60)


def generate_key() -> str:
    """Return 32 random bytes as 43 base62 characters."""
    n = int.from_bytes(secrets.token_bytes(32), "big")
    digits = ""
    while n:
        n, r = divmod(n, 62)
        digits = ALPHABET[r] + digits
    return digits.rjust(KEY_LENGTH, "0")


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def create_key(
    db: AsyncSession,
    user_id: int,
    name: str,
    level: Literal["edit", "view"],
) -> tuple[models.ApiKey, str]:
    """Create a key and return (row, raw key). The raw key cannot be recovered later."""
    raw = generate_key()
    row = models.ApiKey(
        user_id=user_id,
        name=name,
        prefix=raw[:8],
        hash=hash_key(raw),
        level=level,
        created=utcnow(),
        revoked=False,
    )
    db.add(row)
    await db.flush()
    return row, raw


async def verify_key(db: AsyncSession, raw: str) -> tuple[models.User, models.ApiKey] | None:
    """Return (user, key), or None if the key is unknown, revoked or its user disabled."""
    key = (await db.execute(select(models.ApiKey).where(models.ApiKey.hash == hash_key(raw)))).scalar_one_or_none()
    if key is None or key.revoked:
        return None
    user = await db.get(models.User, key.user_id)
    if user is None or user.disabled:
        return None
    now = utcnow()
    # Write at most once a minute per key, not on every request.
    if key.last_used is None or key.last_used < now - LAST_USED_AFTER:
        key.last_used = now
        await db.flush()
    return user, key


async def list_keys(db: AsyncSession, user_id: int) -> list[models.ApiKey]:
    result = await db.execute(select(models.ApiKey).where(models.ApiKey.user_id == user_id).order_by(models.ApiKey.id))
    return list(result.scalars())


async def revoke_key(db: AsyncSession, user_id: int, key_id: int) -> None:
    key = await db.get(models.ApiKey, key_id)
    if key is None or key.user_id != user_id:
        raise ItemNotFoundError(f"No key with ID {key_id} found.")
    key.revoked = True
    await db.flush()
