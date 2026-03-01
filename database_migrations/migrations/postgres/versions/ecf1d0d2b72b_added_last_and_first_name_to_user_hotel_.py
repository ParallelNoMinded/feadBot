"""Added last and first name to user_hotel table

Revision ID: ecf1d0d2b72b
Revises: dbbad623be85
Create Date: 2025-11-14 17:25:23.147678

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ecf1d0d2b72b"
down_revision = "dbbad623be85"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_hotel", sa.Column("last_name", sa.Text(), nullable=True))
    op.add_column("user_hotel", sa.Column("first_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_hotel", "first_name")
    op.drop_column("user_hotel", "last_name")
