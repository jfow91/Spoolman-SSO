"""multiuser keys.

Revision ID: d4e5f6a7b8c9
Revises: b2c3d4e5f6a7
Create Date: 2026-11-12 12:03:00.000000
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


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
    """Scope setting keys and tag UIDs per account.

    Its own revision because cockroachdb refuses a primary key change in a transaction that
    also changes other things on the same table (the NOT NULL in b2c3d4e5f6a7).
    """
    _set_setting_pk(["owner_id", "key"])

    # The composite index also serves every uid lookup, which are all per account. No plain
    # ix_tag_uid is recreated: cockroachdb cannot reuse an index name dropped in the same transaction.
    op.drop_index("ix_tag_uid", table_name="tag")
    op.create_index("ix_tag_owner_id_uid", "tag", ["owner_id", "uid"], unique=True)


def downgrade() -> None:
    """Perform the downgrade.

    Fails, rather than deleting rows, if two accounts hold the same tag UID or setting key:
    the old schema cannot represent that.
    """
    op.drop_index("ix_tag_owner_id_uid", table_name="tag")
    op.create_index("ix_tag_uid", "tag", ["uid"], unique=True)

    _set_setting_pk(["key"])
