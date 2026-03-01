from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import LastFeedbackModel, AllFeedbacksModel
from shared_models import (
    Attachment,
    Comment,
    Feedback,
    FeedbackAttachment,
    FeedbackComment,
    FeedbackStatus,
    Hotel,
    MediaType,
    UserHotel,
    Zone,
)


logger = structlog.get_logger(__name__)


class FeedbackPGRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, *, user_stay_id, zone_id, rating: int) -> Feedback:
        now = datetime.now(timezone.utc)
        fb = Feedback(
            user_stay_id=user_stay_id,
            zone_id=zone_id,
            rating=rating,
            status=FeedbackStatus.CREATED,
            created_at=now,
            updated_at=now,
        )
        self.session.add(fb)
        await self.session.commit()
        return fb

    async def add_comment(self, feedback_id, text: str) -> Comment:
        cm = Comment(comment=text, created_at=datetime.now(timezone.utc))
        self.session.add(cm)
        await self.session.flush()
        link = FeedbackComment(feedback_id=feedback_id, comment_id=cm.id)
        self.session.add(link)
        await self.session.flush()
        return cm

    async def add_attachment(self, feedback_id, media_type: MediaType, s3_url: str) -> Attachment:
        at = Attachment(
            media_type=media_type,
            s3_url=s3_url,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.session.add(at)
        await self.session.flush()
        link = FeedbackAttachment(feedback_id=feedback_id, attachment_id=at.id)
        self.session.add(link)
        await self.session.flush()
        return at

    async def count_feedbacks_today_by_user(self, user_id: str) -> int:
        """Count feedbacks created today by user through their active stays."""
        today = date.today()

        # Get the single active stay for the user (there can be only one)
        active_stay = await self.session.execute(
            select(UserHotel.id).where(
                UserHotel.user_id == user_id,
                UserHotel.close.is_(None),  # Only active stay
            )
        )
        stay_id = active_stay.scalars().first()

        if not stay_id:
            return 0

        # Count feedbacks created today for this stay
        res = await self.session.execute(
            select(func.count(Feedback.id)).where(
                Feedback.user_stay_id == stay_id,
                func.date(Feedback.created_at) == today,
            )
        )
        return res.scalar() or 0

    async def get_feedback_with_last_comment(self, feedback_id: str) -> Optional[LastFeedbackModel]:
        """Get the last comment, zone name, rating, hotel name, hotel code,
        and feedback creation date for a feedback"""
        result = await self.session.execute(
            select(
                Comment.comment,
                Zone.name,
                Feedback.rating,
                Hotel.name,
                Hotel.short_name,
                Zone.is_adult,
                Feedback.created_at,
            )
            .outerjoin(FeedbackComment, Feedback.id == FeedbackComment.feedback_id)
            .outerjoin(Comment, FeedbackComment.comment_id == Comment.id)
            .join(Zone, Feedback.zone_id == Zone.id)
            .join(UserHotel, Feedback.user_stay_id == UserHotel.id)
            .join(Hotel, UserHotel.hotel_id == Hotel.id)
            .where(Feedback.id == feedback_id)
            .order_by(Comment.created_at.desc().nullslast())
            .limit(1)
        )
        row = result.first()
        if row:
            # comment, zone_name, rating, hotel_name, hotel_code, is_adult, created_at
            return LastFeedbackModel.model_validate(row)
        return None

    async def get_all_comments_and_zone(self, feedback_id: str) -> Optional[AllFeedbacksModel]:
        """Get all comments, zone name, rating, hotel name, hotel code,
        and feedback creation date for a feedback"""
        result = await self.session.execute(
            select(
                Comment.comment.label("comment"),
                Zone.name.label("zone"),
                Feedback.rating.label("rating"),
                Hotel.name.label("hotel_name"),
                Hotel.short_name.label("short_name"),
                Zone.is_adult.label("is_adult"),
                Feedback.created_at.label("created_at"),
                Hotel.timezone.label("timezone"),
            )
            .outerjoin(FeedbackComment, Feedback.id == FeedbackComment.feedback_id)
            .outerjoin(Comment, FeedbackComment.comment_id == Comment.id)
            .join(Zone, Feedback.zone_id == Zone.id)
            .join(UserHotel, Feedback.user_stay_id == UserHotel.id)
            .join(Hotel, UserHotel.hotel_id == Hotel.id)
            .where(Feedback.id == feedback_id)
            .order_by(Comment.created_at.asc().nullslast())
        )
        rows = result.all()

        if not rows:
            return None

        comments = [
            row._mapping["comment"]
            for row in rows
            if (
                row._mapping["comment"] is not None
                and row._mapping["comment"].strip()
                and row._mapping["comment"].strip().lower() != "none"
            )
        ]

        first_row = rows[0]._mapping

        feedback_data = {
            "comments": comments,
            "zone": first_row["zone"],
            "rating": first_row["rating"],
            "name": first_row["hotel_name"],
            "short_name": first_row["short_name"],
            "is_adult": first_row["is_adult"],
            "created_at": first_row["created_at"],
            "timezone": first_row["timezone"],
        }
        return AllFeedbacksModel.model_validate(feedback_data)

    async def get_negative_feedbacks_paginated(
        self, hotel_id: UUID, page: int = 1, per_page: int = 5
    ) -> tuple[list, bool]:
        """Get negative feedbacks (rating <= 2) with pagination"""
        offset = (page - 1) * per_page

        # Get feedbacks with zone and hotel info
        result = await self.session.execute(
            select(
                Feedback.id,
                Feedback.rating,
                Feedback.created_at,
                Zone.name.label("zone_name"),
                Hotel.name.label("hotel_name"),
            )
            .join(Zone, Feedback.zone_id == Zone.id)
            .join(UserHotel, Feedback.user_stay_id == UserHotel.id)
            .join(Hotel, UserHotel.hotel_id == Hotel.id)
            .where(
                UserHotel.hotel_id == hotel_id,
                Feedback.rating <= 2,
                Feedback.status.in_([FeedbackStatus.OPENED, FeedbackStatus.IN_PROGRESS]),
            )
            .order_by(Feedback.created_at.desc())
            .offset(offset)
            .limit(per_page + 1)  # Get one extra to check if there's next page
        )

        feedbacks = result.all()
        has_next = len(feedbacks) > per_page

        # Remove extra item if exists
        if has_next:
            feedbacks = feedbacks[:per_page]

        return [dict(feedback._mapping) for feedback in feedbacks], has_next

    async def get_by_id(self, feedback_id: str) -> Optional[Feedback]:
        """Get feedback by ID"""
        result = await self.session.execute(select(Feedback).where(Feedback.id == feedback_id))
        return result.scalars().first()

    async def get_feedback_with_zone_name(self, feedback_id: str) -> Optional[Feedback]:
        """
        Get feedback with zone name
        Args:
            feedback_id (str): Feedback ID

        Returns:
            Optional[Feedback]: _description_
        """
        result = await self.session.execute(
            select(Feedback, Zone.name).join(Zone, Feedback.zone_id == Zone.id).where(Feedback.id == feedback_id)
        )

        row = result.first()
        if not row:
            return None

        feedback, zone_name = row
        return {"feedback": feedback, "zone_name": zone_name}

    async def list_attachments_for_feedback(self, feedback_id: UUID) -> list[Attachment]:
        """Return all attachments linked to a feedback via FeedbackAttachment."""
        result = await self.session.execute(
            select(Attachment)
            .join(FeedbackAttachment, FeedbackAttachment.attachment_id == Attachment.id)
            .where(FeedbackAttachment.feedback_id == feedback_id)
            .order_by(Attachment.created_at.asc())
        )
        return list(result.scalars().all())
