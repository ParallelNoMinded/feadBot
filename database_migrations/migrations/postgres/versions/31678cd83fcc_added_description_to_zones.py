"""added description to zones

Revision ID: 31678cd83fcc
Revises: ecf1d0d2b72b
Create Date: 2025-11-18 12:22:42.540554

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "31678cd83fcc"
down_revision = "ecf1d0d2b72b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("zones", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("zones", "description")
