from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.repositories.feedback_pg import FeedbackPGRepository
from shared_models import Feedback, Hotel, User, UserHotel
from shared_models.constants import ChannelType

logger = structlog.get_logger(__name__)


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.feedback_repo = FeedbackPGRepository(session)

    async def get_by_telegram_id(self, telegram_user_id: str) -> Optional[User]:
        result = await self.session.execute(select(User).where(User.external_user_id == telegram_user_id))
        return result.scalars().first()

    async def upsert_telegram_guest(self, telegram_user_id: str, phone_number: str, channel_type: ChannelType) -> User:
        user = await self.get_by_telegram_id(telegram_user_id)
        if user is None:
            user = User(
                external_user_id=telegram_user_id,
                phone_number=phone_number,
                channel_type=channel_type,
            )
            self.session.add(user)
        await self.session.flush()
        return user

    async def get_last_feedback_id(self, telegram_user_id: str) -> Optional[str]:
        """Get the most recent feedback ID for a user by telegram_id"""
        result = await self.session.execute(
            select(Feedback.id)
            .join(UserHotel, Feedback.user_stay_id == UserHotel.id)
            .join(User, UserHotel.user_id == User.id)
            .where(User.external_user_id == telegram_user_id)
            .order_by(Feedback.created_at.desc())
            .limit(1)
        )
        feedback_id = result.scalars().first()
        return str(feedback_id) if feedback_id else None

    async def can_user_leave_feedback(self, telegram_user_id: str) -> bool:
        """Check if user can leave feedback (max 5 per day)."""
        try:
            user = await self.get_by_telegram_id(telegram_user_id)
            if not user:
                return True

            feedback_count = await self.feedback_repo.count_feedbacks_today_by_user(str(user.id))
        except Exception as e:
            # Log error but allow feedback in case of database issues
            logger.error(f"Error checking if user can leave feedback: {e}")
            return False
        return feedback_count < settings.MAX_FEEDBACK_MESSAGES_PER_DAY

    async def get_active_hotel_code(self, telegram_user_id: str) -> Optional[str]:
        """Get active hotel code for user by telegram_id"""
        try:
            user = await self.get_by_telegram_id(telegram_user_id)
            if not user:
                return None

            # Get active stay with hotel information
            result = await self.session.execute(
                select(Hotel.short_name)
                .join(UserHotel, Hotel.id == UserHotel.hotel_id)
                .where(UserHotel.user_id == user.id, UserHotel.close.is_(None))
                .limit(1)
            )
            hotel_code = result.scalars().first()
            return hotel_code

        except Exception as e:
            logger.error(f"Error getting active hotel code: {e}")
            return None
