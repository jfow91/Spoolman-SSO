"""Accounts created and refreshed from PocketID ID-token claims."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.database import models


def utcnow() -> datetime:
    """Naive UTC now, the form the database columns use."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


async def upsert_from_claims(db: AsyncSession, claims: dict, admin_group: str) -> models.User:
    """Create or update the user keyed on `sub`; name, email and admin flag follow the latest login."""
    sub = claims["sub"]
    if sub == models.UNCLAIMED_SUB:
        raise ValueError("The placeholder owner account cannot sign in")
    now = utcnow()
    user = (await db.execute(select(models.User).where(models.User.oidc_sub == sub))).scalar_one_or_none()
    if user is None:
        user = models.User(oidc_sub=sub, disabled=False, created=now)
        db.add(user)
    user.display_name = claims.get("name") or claims.get("preferred_username") or sub
    user.email = claims.get("email")
    user.is_admin = admin_group in (claims.get("groups") or [])
    user.last_login = now
    await db.flush()
    return user
