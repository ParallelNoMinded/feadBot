"""update_user_1134951744_to_admin_role

Revision ID: d62b1b23cb80
Revises: 5b5f36104472
Create Date: 2025-10-21 14:19:21.371494

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d62b1b23cb80"
down_revision = "5b5f36104472"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE user_hotel
        SET role_id = '550e8400-e29b-41d4-a716-446655440005'
        WHERE user_id = (
            SELECT id FROM users WHERE external_user_id = '1134951744'
        )
        """
    )


def downgrade() -> None:
    pass
