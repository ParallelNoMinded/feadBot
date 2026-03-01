"""add_pms_reservations_table_and_user_pms_reservation

Revision ID: bcd314e48d9b
Revises: d62b1b23cb80
Create Date: 2025-10-27 15:53:17.040318

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "bcd314e48d9b"
down_revision = "d62b1b23cb80"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("arrival_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("departure_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("phone_numbers", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("hotel", sa.Text(), nullable=False),
        sa.Column("status", sa.Enum("CHECKED_OUT", "IN_HOUSE", "INHOUSE", name="reservationstatus"), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reservations_arrival_date"), "reservations", ["arrival_date"], unique=False)
    op.create_index(op.f("ix_reservations_created_at"), "reservations", ["created_at"], unique=False)
    op.create_index(op.f("ix_reservations_departure_date"), "reservations", ["departure_date"], unique=False)
    op.create_index(op.f("ix_reservations_hotel"), "reservations", ["hotel"], unique=False)
    op.create_index(op.f("ix_reservations_phone_numbers"), "reservations", ["phone_numbers"], unique=False)
    op.create_index(op.f("ix_reservations_status"), "reservations", ["status"], unique=False)
    op.create_table(
        "reservation_users",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["reservations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("user_id", "reservation_id"),
    )
    op.create_index(op.f("ix_reservation_users_reservation_id"), "reservation_users", ["reservation_id"], unique=False)
    op.create_index(op.f("ix_reservation_users_user_id"), "reservation_users", ["user_id"], unique=False)
    op.drop_constraint(op.f("user_hotel_external_pms_id_key"), "user_hotel", type_="unique")
    op.alter_column(
        "user_hotel",
        "external_pms_id",
        existing_type=sa.TEXT(),
        type_=sa.Uuid(),
        existing_nullable=True,
        postgresql_using="external_pms_id::uuid",
    )
    op.create_index(op.f("ix_user_hotel_external_pms_id"), "user_hotel", ["external_pms_id"], unique=False)
    op.create_foreign_key("user_hotel_external_pms_id_fkey", "user_hotel", "reservations", ["external_pms_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("user_hotel_external_pms_id_fkey", "user_hotel", type_="foreignkey")
    op.drop_index(op.f("ix_user_hotel_external_pms_id"), table_name="user_hotel")
    op.alter_column(
        "user_hotel",
        "external_pms_id",
        existing_type=sa.Uuid(),
        type_=sa.TEXT(),
        existing_nullable=True,
        postgresql_using="external_pms_id::text",
    )
    op.create_unique_constraint(op.f("user_hotel_external_pms_id_key"), "user_hotel", ["external_pms_id"])
    op.drop_index(op.f("ix_reservation_users_user_id"), table_name="reservation_users")
    op.drop_index(op.f("ix_reservation_users_reservation_id"), table_name="reservation_users")
    op.drop_table("reservation_users")
    op.drop_index(op.f("ix_reservations_status"), table_name="reservations")
    op.drop_index(op.f("ix_reservations_phone_numbers"), table_name="reservations")
    op.drop_index(op.f("ix_reservations_hotel"), table_name="reservations")
    op.drop_index(op.f("ix_reservations_departure_date"), table_name="reservations")
    op.drop_index(op.f("ix_reservations_created_at"), table_name="reservations")
    op.drop_index(op.f("ix_reservations_arrival_date"), table_name="reservations")
    op.drop_table("reservations")
