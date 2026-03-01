"""add pms_incoming_status

Revision ID: c6b209830a2d
Revises: 835fad01b459
Create Date: 2025-12-15 12:50:58.778948

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6b209830a2d'
down_revision = '835fad01b459'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('reservations', sa.Column('pms_incoming_status', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('reservations', 'pms_incoming_status')
