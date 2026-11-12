"""Server-side browser sessions. The cookie holds a random token; the database holds only its SHA-256."""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.auth.users import utcnow
from spoolman.database import models

SESSION_LIFETIME = timedelta(days=30)
SLIDE_AFTER = timedelta(hours=1)


def _hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def create_session(db: AsyncSession, user_id: int) -> str:
    """Create a session and return the raw cookie token."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(models.Session(id=_hash(token), user_id=user_id, created=now, expires=now + SESSION_LIFETIME, last_seen=now))
    await db.flush()
    return token


async def resolve_session(db: AsyncSession, raw_token: str) -> models.User | None:
    """Return the session's user, or None if the session is missing, expired or its user disabled."""
    session = await db.get(models.Session, _hash(raw_token))
    now = utcnow()
    if session is None or session.expires <= now:
        return None
    user = await db.get(models.User, session.user_id)
    if user is None or user.disabled:
        return None
    # Write at most once an hour per session, not on every request.
    if session.last_seen < now - SLIDE_AFTER:
        session.last_seen = now
        session.expires = now + SESSION_LIFETIME
        await db.flush()
    return user


async def delete_session(db: AsyncSession, raw_token: str) -> None:
    """Delete one session (sign out this browser)."""
    await db.execute(delete(models.Session).where(models.Session.id == _hash(raw_token)))


async def delete_user_sessions(db: AsyncSession, user_id: int) -> None:
    """Delete every session of a user."""
    await db.execute(delete(models.Session).where(models.Session.user_id == user_id))
