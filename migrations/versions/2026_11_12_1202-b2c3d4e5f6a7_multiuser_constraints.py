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


def upgrade() -> None:
    """Make owner_id mandatory, indexed and a foreign key to user.

    Schema only; see a1b2c3d4e5f6 for why this is its own revision. The setting primary key
    swap is in d4e5f6a7b8c9: cockroachdb refuses a primary key change in a transaction that
    also changes other things on the same table.
    """
    for table in OWNED_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("owner_id", existing_type=sa.Integer(), nullable=False)
            batch.create_index(f"ix_{table}_owner_id", ["owner_id"], unique=False)
            batch.create_foreign_key(f"fk_{table}_owner_id_user", "user", ["owner_id"], ["id"])


def downgrade() -> None:
    """Perform the downgrade.

    ix_<table>_owner_id is dropped by a1b2c3d4e5f6: cockroachdb refuses to drop it in the
    same transaction that drops the foreign key using it.
    """
    for table in OWNED_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_owner_id_user", type_="foreignkey")
            batch.alter_column("owner_id", existing_type=sa.Integer(), nullable=True)
