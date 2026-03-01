"""add CREATED to feedbackstatus

Revision ID: b3a2f1d0c9e8
Revises: a15086ccfd1b
Create Date: 2025-10-05 00:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "b3a2f1d0c9e8"
down_revision = "a15086ccfd1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new enum label to existing PostgreSQL type
    op.execute("ALTER TYPE feedbackstatus ADD VALUE 'CREATED'")


def downgrade() -> None:
    pass
