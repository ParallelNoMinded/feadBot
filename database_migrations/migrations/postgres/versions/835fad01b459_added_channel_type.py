"""added channel type

Revision ID: 835fad01b459
Revises: 31678cd83fcc
Create Date: 2025-12-04 12:16:28.417633

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "835fad01b459"
down_revision = "31678cd83fcc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE channeltype AS ENUM ('TELEGRAM', 'MAX')")
    op.add_column(
        "users",
        sa.Column(
            "channel_type",
            sa.Enum("TELEGRAM", "MAX", name="channeltype"),
            nullable=True,
        ),
    )
    op.create_index(
        op.f("ix_users_channel_type"), "users", ["channel_type"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_channel_type"), table_name="users")
    op.drop_column("users", "channel_type")
