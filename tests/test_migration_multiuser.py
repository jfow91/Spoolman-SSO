"""Tests for the multi-user migrations (users, ownership columns, per-account settings and tags)."""

from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from spoolman.database import models
from tests.support.dbsession import make_user

PRE_MULTIUSER = "fe4970567bb3"
NOW = datetime(2026, 1, 1)  # noqa: DTZ001 - stored naive, as UTC
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture
def migrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Callable[[str, str], None], sa.Engine]]:
    """Return (run, engine): run(cmd, rev) drives alembic against a temp-file SQLite database."""
    monkeypatch.setenv("SPOOLMAN_DIR_DATA", str(tmp_path))
    monkeypatch.setenv("SPOOLMAN_DB_TYPE", "sqlite")
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'spoolman.db'}")

    def run(cmd: str, rev: str) -> None:
        getattr(command, cmd)(cfg, rev)

    yield run, engine
    engine.dispose()


def _seed_legacy_rows(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO vendor (id, registered, name) VALUES (1, :now, 'V')"), {"now": NOW})
        conn.execute(
            sa.text(
                "INSERT INTO filament (id, registered, vendor_id, density, diameter) VALUES (1, :now, 1, 1.24, 1.75)",
            ),
            {"now": NOW},
        )
        conn.execute(
            sa.text("INSERT INTO spool (id, registered, filament_id, used_weight) VALUES (1, :now, 1, 0)"),
            {"now": NOW},
        )
        conn.execute(
            sa.text("INSERT INTO tag (id, uid, target_type, spool_id, added) VALUES (1, 'U1', 'spool', 1, :now)"),
            {"now": NOW},
        )
        conn.execute(
            sa.text("INSERT INTO setting (key, value, last_updated) VALUES ('currency', '\"EUR\"', :now)"),
            {"now": NOW},
        )
        conn.execute(
            sa.text("INSERT INTO setting (key, value, last_updated) VALUES ('base_url', '\"http://x\"', :now)"),
            {"now": NOW},
        )


def test_upgrade_populated_db_assigns_unclaimed_owner(migrate: tuple[Callable[[str, str], None], sa.Engine]):
    run, engine = migrate
    run("upgrade", PRE_MULTIUSER)
    _seed_legacy_rows(engine)

    run("upgrade", "head")

    with engine.connect() as conn:
        users = conn.execute(sa.text('SELECT id, oidc_sub, disabled FROM "user"')).all()
        assert len(users) == 1
        user_id, sub, disabled = users[0]
        assert sub == models.UNCLAIMED_SUB
        assert disabled
        for table in ("vendor", "filament", "spool", "tag", "setting"):
            owners = conn.execute(sa.text(f"SELECT owner_id FROM {table}")).scalars().all()  # noqa: S608
            assert owners
            assert set(owners) == {user_id}, table
        assert conn.execute(sa.text("SELECT key FROM setting")).scalars().all() == ["currency"]
        assert conn.execute(sa.text("SELECT key, value FROM app_setting")).all() == [("base_url", '"http://x"')]


def test_upgrade_empty_db_creates_unclaimed_user_only(migrate: tuple[Callable[[str, str], None], sa.Engine]):
    run, engine = migrate
    run("upgrade", "head")

    with engine.connect() as conn:
        assert conn.execute(sa.text('SELECT oidc_sub FROM "user"')).scalars().all() == [models.UNCLAIMED_SUB]
        for table in ("vendor", "filament", "spool", "tag", "setting", "app_setting", "session", "api_key"):
            assert conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0, table  # noqa: S608


def test_downgrade_roundtrip(migrate: tuple[Callable[[str, str], None], sa.Engine]):
    run, engine = migrate
    run("upgrade", PRE_MULTIUSER)
    _seed_legacy_rows(engine)
    run("upgrade", "head")

    run("downgrade", PRE_MULTIUSER)
    with engine.connect() as conn:
        assert sorted(conn.execute(sa.text("SELECT key FROM setting")).scalars().all()) == ["base_url", "currency"]
        assert "owner_id" not in {c["name"] for c in sa.inspect(conn).get_columns("vendor")}
        assert not sa.inspect(conn).has_table("user")

    run("upgrade", "head")
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT key FROM app_setting")).scalars().all() == ["base_url"]


def _tag(owner: models.User, uid: str = "04AABB") -> models.Tag:
    return models.Tag(owner_id=owner.id, uid=uid, target_type="location", target_value="Shelf", added=NOW)


@pytest.mark.asyncio
async def test_same_tag_uid_allowed_in_two_accounts(db: AsyncSession):
    alice = await make_user(db, sub="alice")
    bob = await make_user(db, sub="bob")
    db.add_all([_tag(alice), _tag(bob)])
    await db.commit()

    db.add(_tag(alice))
    with pytest.raises(IntegrityError):
        await db.commit()
