"""multiuser constraints.

Revision ID: b2c3d4e5f6a7
Revises: c3d4e5f6a7b8
Create Date: 2026-11-12 12:02:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None

OWNED_TABLES = ("vendor", "filament", "spool", "tag", "setting")


def _set_setting_pk(columns: list[str]) -> None:
    """Replace the setting table's primary key; each backend needs its own form."""
    bind = op.get_bind()
    name = sa.inspect(bind).get_pk_constraint("setting")["name"] or "pk_setting"
    quote = bind.dialect.identifier_preparer.quote
    cols = ", ".join(quote(c) for c in columns)
    if bind.dialect.name == "sqlite":
        # The table is spelled out instead of reflected: the reflected column-level primary_key
        # flags would disagree with the new key, which SQLAlchemy warns will become an error.
        metadata = sa.MetaData()
        sa.Table("user", metadata, sa.Column("id", sa.Integer(), primary_key=True))
        setting = sa.Table(
            "setting",
            metadata,
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("last_updated", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["owner_id"], ["user.id"], name="fk_setting_owner_id_user"),
            sa.PrimaryKeyConstraint(*columns, name=name),
            sa.Index("ix_setting_key", "key"),
            sa.Index("ix_setting_owner_id", "owner_id"),
        )
        with op.batch_alter_table("setting", recreate="always", copy_from=setting):
            pass
    elif bind.dialect.name in ("mysql", "mariadb"):
        op.execute(f"ALTER TABLE setting DROP PRIMARY KEY, ADD PRIMARY KEY ({cols})")
    else:
        # postgres and cockroachdb. One statement: cockroachdb rejects a table left without a
        # primary key, and its ALTER PRIMARY KEY would keep a unique index on the old key.
        op.execute(
            f"ALTER TABLE setting DROP CONSTRAINT {quote(name)}, ADD CONSTRAINT {quote(name)} PRIMARY KEY ({cols})",
        )


def upgrade() -> None:
    """Make owner_id mandatory, and scope setting keys and tag UIDs per account.

    Schema only; see a1b2c3d4e5f6 for why this is its own revision.
    """
    for table in OWNED_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("owner_id", existing_type=sa.Integer(), nullable=False)
            batch.create_index(f"ix_{table}_owner_id", ["owner_id"], unique=False)
            batch.create_foreign_key(f"fk_{table}_owner_id_user", "user", ["owner_id"], ["id"])

    _set_setting_pk(["owner_id", "key"])

    op.drop_index("ix_tag_uid", table_name="tag")
    op.create_index("ix_tag_uid", "tag", ["uid"], unique=False)
    op.create_index("ix_tag_owner_id_uid", "tag", ["owner_id", "uid"], unique=True)


def downgrade() -> None:
    """Perform the downgrade.

    Fails, rather than deleting rows, if two accounts hold the same tag UID or setting key:
    the old schema cannot represent that.
    """
    op.drop_index("ix_tag_owner_id_uid", table_name="tag")
    op.drop_index("ix_tag_uid", table_name="tag")
    op.create_index("ix_tag_uid", "tag", ["uid"], unique=True)

    _set_setting_pk(["key"])

    for table in OWNED_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_owner_id_user", type_="foreignkey")
            batch.drop_index(f"ix_{table}_owner_id")
            batch.alter_column("owner_id", existing_type=sa.Integer(), nullable=True)
