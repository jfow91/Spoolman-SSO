"""multiuser.

Revision ID: a1b2c3d4e5f6
Revises: fe4970567bb3
Create Date: 2026-11-12 12:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "fe4970567bb3"
branch_labels = None
depends_on = None

OWNED_TABLES = ("vendor", "filament", "spool", "tag", "setting")


def upgrade() -> None:
    """Create the account tables and add a nullable owner_id to every owned table.

    Schema only. CockroachDB runs each revision in its own transaction and refuses to mix
    schema changes and writes in one (see 304a32906234), so the multi-user change is split
    into this DDL, the backfill (c3d4e5f6a7b8), the NOT NULL and foreign keys (b2c3d4e5f6a7),
    then the setting primary key and tag index swaps (d4e5f6a7b8c9).
    """
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("oidc_sub", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("last_login", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_user"),
        sa.UniqueConstraint("oidc_sub", name="uq_user_oidc_sub"),
    )
    op.create_index("ix_user_id", "user", ["id"], unique=False)

    op.create_table(
        "session",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("expires", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_session_user_id_user"),
        sa.PrimaryKeyConstraint("id", name="pk_session"),
    )
    op.create_index("ix_session_user_id", "session", ["user_id"], unique=False)

    op.create_table(
        "api_key",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=8), nullable=False),
        sa.Column("created", sa.DateTime(), nullable=False),
        sa.Column("last_used", sa.DateTime(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_api_key_user_id_user"),
        sa.PrimaryKeyConstraint("id", name="pk_api_key"),
        sa.UniqueConstraint("hash", name="uq_api_key_hash"),
    )
    op.create_index("ix_api_key_id", "api_key", ["id"], unique=False)
    op.create_index("ix_api_key_user_id", "api_key", ["user_id"], unique=False)

    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("last_updated", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_app_setting"),
    )

    for table in OWNED_TABLES:
        op.add_column(table, sa.Column("owner_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Perform the downgrade."""
    for table in OWNED_TABLES:
        with op.batch_alter_table(table) as batch:
            # Created by b2c3d4e5f6a7, dropped here: cockroachdb refuses to drop it in the same
            # transaction as the foreign key that uses it.
            batch.drop_index(f"ix_{table}_owner_id")
            batch.drop_column("owner_id")

    # Indexes go with their tables; MySQL refuses to drop an index a foreign key still uses.
    op.drop_table("app_setting")
    op.drop_table("api_key")
    op.drop_table("session")
    op.drop_table("user")
