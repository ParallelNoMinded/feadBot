"""added new status

Revision ID: dbbad623be85
Revises: bcd314e48d9b
Create Date: 2025-11-14 11:30:00.813545

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "dbbad623be85"
down_revision = "bcd314e48d9b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new value to existing enum
    op.execute("ALTER TYPE reservationstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")


def downgrade() -> None:
    pass
