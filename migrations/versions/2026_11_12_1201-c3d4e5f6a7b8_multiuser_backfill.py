"""multiuser backfill.

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-11-12 12:01:00.000000
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

OWNED_TABLES = ("vendor", "filament", "spool", "tag", "setting")
# A copy of models.UNCLAIMED_SUB: a migration must not change when the models do.
UNCLAIMED_SUB = "__unclaimed__"

user = sa.table(
    "user",
    sa.column("id", sa.Integer),
    sa.column("oidc_sub", sa.String),
    sa.column("display_name", sa.String),
    sa.column("is_admin", sa.Boolean),
    sa.column("disabled", sa.Boolean),
    sa.column("created", sa.DateTime),
)
setting = sa.table(
    "setting",
    sa.column("owner_id", sa.Integer),
    sa.column("key", sa.String),
    sa.column("value", sa.Text),
    sa.column("last_updated", sa.DateTime),
)
app_setting = sa.table(
    "app_setting",
    sa.column("key", sa.String),
    sa.column("value", sa.Text),
    sa.column("last_updated", sa.DateTime),
)


def _unclaimed_id() -> int:
    return op.get_bind().execute(sa.select(user.c.id).where(user.c.oidc_sub == UNCLAIMED_SUB)).scalar_one()


def upgrade() -> None:
    """Give every existing row to a disabled placeholder account and move base_url app-wide.

    Data only; see a1b2c3d4e5f6 for why this is its own revision.
    """
    op.execute(
        sa.insert(user).values(
            oidc_sub=UNCLAIMED_SUB,
            display_name="Unclaimed data",
            is_admin=False,
            disabled=True,
            created=datetime.utcnow().replace(microsecond=0),
        ),
    )
    owner_id = _unclaimed_id()
    for name in OWNED_TABLES:
        op.execute(sa.table(name, sa.column("owner_id", sa.Integer)).update().values(owner_id=owner_id))

    op.execute(
        sa.insert(app_setting).from_select(
            ["key", "value", "last_updated"],
            sa.select(setting.c.key, setting.c.value, setting.c.last_updated).where(setting.c.key == "base_url"),
        ),
    )
    op.execute(setting.delete().where(setting.c.key == "base_url"))


def downgrade() -> None:
    """Move base_url back into the setting table, owned by the placeholder account."""
    op.execute(
        sa.insert(setting).from_select(
            ["owner_id", "key", "value", "last_updated"],
            sa.select(
                sa.literal(_unclaimed_id(), sa.Integer),
                app_setting.c.key,
                app_setting.c.value,
                app_setting.c.last_updated,
            ).where(app_setting.c.key == "base_url"),
        ),
    )
    op.execute(app_setting.delete().where(app_setting.c.key == "base_url"))
