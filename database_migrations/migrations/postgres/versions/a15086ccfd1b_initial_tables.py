"""initial tables

Revision ID: a15086ccfd1b
Revises:
Create Date: 2025-09-18 12:34:21.400336

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a15086ccfd1b"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_type", sa.Enum("IMAGE", "VIDEO", "AUDIO", "DOCUMENT", name="mediatype"), nullable=False),
        sa.Column("s3_url", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_comments_created_at", "comments", ["id", "created_at"], unique=False)
    op.create_index(op.f("ix_comments_created_at"), "comments", ["created_at"], unique=False)
    op.create_table(
        "hotels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("short_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_hotels_short_name"), "hotels", ["short_name"], unique=False)
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_roles_name"), "roles", ["name"], unique=True)
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_user_id", sa.Text(), nullable=False),
        sa.Column("phone_number", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_external_user_id"), "users", ["external_user_id"], unique=True)
    op.create_index(op.f("ix_users_phone_number"), "users", ["phone_number"], unique=False)
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("hotel_id", sa.Uuid(), nullable=False),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["hotel_id"],
            ["hotels.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reports_created_at"), "reports", ["created_at"], unique=False)
    op.create_index(op.f("ix_reports_hotel_id"), "reports", ["hotel_id"], unique=False)
    op.create_table(
        "user_hotel",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("hotel_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("room_number", sa.Text(), nullable=True),
        sa.Column("open", sa.Date(), nullable=False),
        sa.Column("close", sa.Date(), nullable=True),
        sa.Column("external_pms_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["hotel_id"],
            ["hotels.id"],
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_pms_id"),
    )
    op.create_index(op.f("ix_user_hotel_hotel_id"), "user_hotel", ["hotel_id"], unique=False)
    op.create_index(op.f("ix_user_hotel_role_id"), "user_hotel", ["role_id"], unique=False)
    op.create_index(op.f("ix_user_hotel_user_id"), "user_hotel", ["user_id"], unique=False)
    op.create_table(
        "zones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hotel_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("short_name", sa.Text(), nullable=False),
        sa.Column("is_adult", sa.Boolean(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["hotel_id"],
            ["hotels.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_zones_disabled_at"), "zones", ["disabled_at"], unique=False)
    op.create_index(op.f("ix_zones_hotel_id"), "zones", ["hotel_id"], unique=False)
    op.create_index(op.f("ix_zones_short_name"), "zones", ["short_name"], unique=False)
    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_stay_id", sa.Uuid(), nullable=False),
        sa.Column("zone_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column(
            "status", sa.Enum("OPENED", "IN_PROGRESS", "SOLVED", "REJECTED", name="feedbackstatus"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_stay_id"],
            ["user_hotel.id"],
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zones.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_feedbacks_created_at"), "feedbacks", ["created_at"], unique=False)
    op.create_index(op.f("ix_feedbacks_status"), "feedbacks", ["status"], unique=False)
    op.create_index(op.f("ix_feedbacks_user_stay_id"), "feedbacks", ["user_stay_id"], unique=False)
    op.create_index(op.f("ix_feedbacks_zone_id"), "feedbacks", ["zone_id"], unique=False)
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hotel_id", sa.Uuid(), nullable=False),
        sa.Column("zone_id", sa.Uuid(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("default_prompt", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["hotel_id"],
            ["hotels.id"],
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["zones.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_scenarios_hotel_id"), "scenarios", ["hotel_id"], unique=False)
    op.create_index(op.f("ix_scenarios_zone_id"), "scenarios", ["zone_id"], unique=False)
    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("sentiment", sa.Enum("POSITIVE", "NEUTRAL", "NEGATIVE", name="sentiment"), nullable=True),
        sa.Column("root_causes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.Enum("RELEVANT", "SENTIMENT", "ANALYSIS", "COMPLETED", name="analysisstatus"), nullable=False
        ),
        sa.Column("relevance", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["feedback_id"],
            ["feedbacks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_analysis_results_created_at"), "analysis_results", ["created_at"], unique=False)
    op.create_index(op.f("ix_analysis_results_feedback_id"), "analysis_results", ["feedback_id"], unique=False)
    op.create_index(op.f("ix_analysis_results_sentiment"), "analysis_results", ["sentiment"], unique=False)
    op.create_index(op.f("ix_analysis_results_status"), "analysis_results", ["status"], unique=False)
    op.create_table(
        "feedback_attachments",
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["attachments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["feedback_id"],
            ["feedbacks.id"],
        ),
        sa.PrimaryKeyConstraint("feedback_id", "attachment_id"),
    )
    op.create_table(
        "feedback_comments",
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["comments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["feedback_id"],
            ["feedbacks.id"],
        ),
        sa.PrimaryKeyConstraint("feedback_id", "comment_id"),
    )
    op.create_table(
        "feedback_status_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.Enum("OPENED", "IN_PROGRESS", "SOLVED", "REJECTED", name="feedbackstatus"), nullable=False
        ),
        sa.Column("changed_by", sa.Uuid(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["changed_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["feedback_id"],
            ["feedbacks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_feedback_status_history_changed_at"), "feedback_status_history", ["changed_at"], unique=False
    )
    op.create_index(
        op.f("ix_feedback_status_history_feedback_id"), "feedback_status_history", ["feedback_id"], unique=False
    )

    # Insert test data
    # Test hotel
    op.execute(
        """
        INSERT INTO hotels (id, name, short_name, description, timezone)
        VALUES (
            '550e8400-e29b-41d4-a716-446655440000',
            'Alean',
            'ALN',
            'Роскошный отель в центре города с современными удобствами и отличным сервисом',
            'Europe/Moscow'
        )
    """
    )

    # Test zones for the hotel
    op.execute(
        """
        INSERT INTO zones (id, hotel_id, name, short_name, is_adult, disabled_at)
        VALUES
        (
            '550e8400-e29b-41d4-a716-446655440001',
            '550e8400-e29b-41d4-a716-446655440000',
            'Анимация (мастер-классы), активы общие',
            'AMG',
            true,
            NULL
        ),
        (
            '550e8400-e29b-41d4-a716-446655440002',
            '550e8400-e29b-41d4-a716-446655440000',
            'Активы персональные (яхтинг, академия)',
            'APK',
            true,
            NULL
        ),
        (
            '550e8400-e29b-41d4-a716-446655440003',
            '550e8400-e29b-41d4-a716-446655440000',
            'Анимация (детские клубы)',
            'AND',
            false,
            NULL
        )
    """
    )

    # Insert roles
    op.execute(
        """
        INSERT INTO roles (id, name)
        VALUES
        (
            '550e8400-e29b-41d4-a716-446655440002',
            'Гость'
        ),
        (
            '550e8400-e29b-41d4-a716-446655440003',
            'Менеджер'
        ),
        (
            '550e8400-e29b-41d4-a716-446655440004',
            'Руководитель сети'
        ),
        (
            '550e8400-e29b-41d4-a716-446655440005',
            'Администратор'
        )
    """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_feedback_status_history_feedback_id"), table_name="feedback_status_history")
    op.drop_index(op.f("ix_feedback_status_history_changed_at"), table_name="feedback_status_history")
    op.drop_table("feedback_status_history")
    op.drop_table("feedback_comments")
    op.drop_table("feedback_attachments")
    op.drop_index(op.f("ix_analysis_results_status"), table_name="analysis_results")
    op.drop_index(op.f("ix_analysis_results_sentiment"), table_name="analysis_results")
    op.drop_index(op.f("ix_analysis_results_feedback_id"), table_name="analysis_results")
    op.drop_index(op.f("ix_analysis_results_created_at"), table_name="analysis_results")
    op.drop_table("analysis_results")
    op.drop_index(op.f("ix_scenarios_zone_id"), table_name="scenarios")
    op.drop_index(op.f("ix_scenarios_hotel_id"), table_name="scenarios")
    op.drop_table("scenarios")
    op.drop_index(op.f("ix_feedbacks_zone_id"), table_name="feedbacks")
    op.drop_index(op.f("ix_feedbacks_user_stay_id"), table_name="feedbacks")
    op.drop_index(op.f("ix_feedbacks_status"), table_name="feedbacks")
    op.drop_index(op.f("ix_feedbacks_created_at"), table_name="feedbacks")
    op.drop_table("feedbacks")
    op.drop_index(op.f("ix_zones_short_name"), table_name="zones")
    op.drop_index(op.f("ix_zones_hotel_id"), table_name="zones")
    op.drop_index(op.f("ix_zones_disabled_at"), table_name="zones")
    op.drop_table("zones")
    op.drop_index(op.f("ix_user_hotel_user_id"), table_name="user_hotel")
    op.drop_index(op.f("ix_user_hotel_role_id"), table_name="user_hotel")
    op.drop_index(op.f("ix_user_hotel_hotel_id"), table_name="user_hotel")
    op.drop_table("user_hotel")
    op.drop_index(op.f("ix_reports_hotel_id"), table_name="reports")
    op.drop_index(op.f("ix_reports_created_at"), table_name="reports")
    op.drop_table("reports")
    op.drop_index(op.f("ix_users_phone_number"), table_name="users")
    op.drop_index(op.f("ix_users_external_user_id"), table_name="users")
    op.drop_table("users")
    op.drop_index(op.f("ix_roles_name"), table_name="roles")
    op.drop_table("roles")
    op.drop_index(op.f("ix_hotels_short_name"), table_name="hotels")
    op.drop_table("hotels")
    op.drop_index(op.f("ix_comments_created_at"), table_name="comments")
    op.drop_index("idx_comments_created_at", table_name="comments")
    op.drop_table("comments")
    op.drop_table("attachments")
