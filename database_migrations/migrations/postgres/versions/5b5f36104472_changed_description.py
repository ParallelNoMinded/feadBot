"""changed description

Revision ID: 5b5f36104472
Revises: b3a2f1d0c9e8
Create Date: 2025-10-17 13:51:26.316392

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "5b5f36104472"
down_revision = "b3a2f1d0c9e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE hotels
        SET description = '🏨 Alean

🤖 Добро пожаловать в бот обратной связи!

📝 Здесь вы можете:
• Оставить отзыв о качестве обслуживания
• Дополнить предыдущий отзыв
• Получить помощь и информацию

Выберите действие из меню ниже:'
        WHERE id = '550e8400-e29b-41d4-a716-446655440000'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE hotels 
        SET description = 'Роскошный отель в центре города с современными удобствами и отличным сервисом'
        WHERE id = '550e8400-e29b-41d4-a716-446655440000'
        """
    )
