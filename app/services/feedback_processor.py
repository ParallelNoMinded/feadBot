"""
Service for processing completed feedback sessions.
"""

import re

import structlog
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter
from app.adapters.telegram.adapter import TelegramAdapter
from app.adapters.max.adapter import MaxAdapter
from app.config.settings import settings
from app.core.db import AsyncSessionFactory
from app.core.state import InMemoryState
from app.repositories.feedback_pg import FeedbackPGRepository
from app.services.base import BaseService
from app.services.llm.llm_pool import llm_pool
from shared_models import (
    AnalysisResult,
    Comment,
    Feedback,
    FeedbackComment,
    Hotel,
    Role,
    Scenario,
    Sentiment,
    User,
    UserHotel,
    Zone,
    RoleEnum,
)
from shared_models import AnalysisStatus
from shared_models.constants import ChannelType
from app.config.messages import (
    NOTIFICATION_MESSAGE,
    POSITIVE_FEEDBACK_MESSAGE,
    NEGATIVE_FEEDBACK_MESSAGE,
    NEUTRAL_FEEDBACK_MESSAGE,
)

from app.utils.hotel_timezone import convert_to_timezone

logger = structlog.get_logger(__name__)

# Module-level constant for sentiment mapping - most efficient approach
SENTIMENT_MAPPING = {
    "positive": Sentiment.POSITIVE,
    "negative": Sentiment.NEGATIVE,
    "neutral": Sentiment.NEUTRAL,
}


def convert_sentiment_string_to_enum(sentiment_str: str) -> Sentiment:
    """
    Convert sentiment string to Sentiment enum.

    Args:
        sentiment_str: String representation of sentiment

    Returns:
        Sentiment enum value (POSITIVE, NEGATIVE, or NEUTRAL)
    """
    return SENTIMENT_MAPPING.get(sentiment_str.lower(), Sentiment.NEUTRAL)


class FeedbackProcessorService(BaseService):
    """Service for processing completed feedback sessions."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.llm_pool = llm_pool
        self.feedback_repo = FeedbackPGRepository(session)
        self.settings = settings

    async def process_feedback_session_relevant(
        self, feedback_id: str, state: InMemoryState, adapter: ChannelAdapter = None
    ) -> tuple[Feedback, str, str, Sentiment] | None:
        """
        Quick processing of feedback session - only sends response to user.

        Args:
            feedback_id: ID of the feedback to process
        """
        try:
            # Get feedback with all related data
            feedback = await self.feedback_repo.get_by_id(feedback_id)
            if not feedback:
                logger.warning("feedback.not_found", feedback_id=feedback_id)
                return

            # Create initial analysis result with RELEVANT status
            await self._create_or_update_analysis_result(feedback_id=feedback_id, status=AnalysisStatus.RELEVANT)

            # Get rating from feedback
            rating = feedback.rating

            # Get all comments for this feedback
            comments = await self._get_feedback_comments(feedback_id)

            logger.info(
                f"feedback.quick.processing.started feedback_id={feedback_id} rating={rating} comments_count={len(comments)} ",
            )

            # Combine rating and comments into a single input for analysis
            all_comments = "\n".join([comment.comment for comment in comments])

            logger.info(f"all_comments={all_comments}")
            logger.info(f"Rating={rating}")

            # Truncate comments to the maximum length
            all_comments_preprocessed = self.truncate_text(
                all_comments, max_length=self.settings.TELEGRAM_MESSAGE_MAX_LENGTH
            )

            # Create combined input with rating and comments
            combined_input = f"""
                <Rating>{rating}</Rating>
                <Comments>{all_comments_preprocessed}</Comments>
            """

            if not all_comments.strip():
                logger.warning("feedback.no_comments", feedback_id=feedback_id)

                sentiment = self._get_sentiment_from_rating(rating)

                await self._create_or_update_analysis_result(
                    feedback_id=feedback_id,
                    status=AnalysisStatus.COMPLETED,
                    relevance=False,
                    sentiment=sentiment,
                )
                await self._send_analysis_response(feedback, sentiment=sentiment, adapter=adapter)
                return None

            session_id = str(uuid4())

            # Perform quick LLM analysis - only sends response to user
            sentiment = await self._analyze_with_llm_relevant(
                feedback, combined_input, session_id, rating, state, adapter
            )

            if sentiment is None or sentiment in [
                Sentiment.POSITIVE,
                Sentiment.NEUTRAL,
            ]:
                await self._create_or_update_analysis_result(feedback_id=feedback_id, status=AnalysisStatus.COMPLETED)

                return None

            logger.info(f"feedback.quick.processing.completed feedback_id={feedback_id}")

            return feedback, combined_input, session_id, sentiment

        except Exception as e:
            logger.error("feedback.quick.processing.error", feedback_id=feedback_id, error=str(e))
            return None

    async def process_feedback_session_background(
        self,
        feedback: Feedback,
        combined_input: str,
        session_id: str,
        sentiment: Sentiment,
        state: InMemoryState,
        is_new_feedback: bool = True,
    ) -> None:
        """
        Background processing of feedback session - runs full analysis without blocking user.

        Args:
            feedback_id: ID of the feedback to process
        """
        logger.info(f"feedback.full.processing.started feedback_id={feedback.id} sentiment={sentiment.value}")

        # Create a new session for background processing to avoid transaction conflicts
        async with AsyncSessionFactory() as bg_session:
            # Update analysis result with ANALYSIS status before detailed analysis
            bg_processor = FeedbackProcessorService(bg_session)

            # Create new instance with background session
            analysis_result = await bg_processor._analyze_with_llm_detailed(
                feedback, combined_input, session_id, sentiment, state, is_new_feedback,
            )

            if analysis_result:
                # Update analysis result with COMPLETED status and final data
                await bg_processor._create_or_update_analysis_result(
                    feedback_id=feedback.id,
                    status=AnalysisStatus.COMPLETED,
                    root_causes=analysis_result.root_causes,
                    recommendation=analysis_result.recommendation,
                )

                logger.info(
                    "feedback.full.analysis.completed",
                    extra={"feedback_id": feedback.id},
                )

    async def _get_feedback_comments(self, feedback_id: str) -> list[Comment]:
        """
        Get all comments for a feedback.

        Args:
            feedback_id: ID of the feedback to get

        Returns:
            List of Comment objects or None if not found
        """
        result = await self.session.execute(
            select(Comment)
            .join(FeedbackComment, Comment.id == FeedbackComment.comment_id)
            .where(FeedbackComment.feedback_id == feedback_id)
            .order_by(Comment.created_at)
        )
        return list(result.scalars().all())

    def truncate_text(self, text: str, max_length: int = 2000) -> str:
        """
        Truncate text to a maximum length.

        Args:
            text: Text to truncate
            max_length: Maximum allowed length (default: 2000)

        Returns:
            Truncated text with proper word boundaries
        """
        if len(text) <= max_length:
            return text

        truncated = text[:max_length]

        # Find last punctuation in truncated text
        punctuation_pattern = r"[.!?,:;]"
        punctuation_matches = list(re.finditer(punctuation_pattern, truncated))

        if punctuation_matches:
            # Truncate by last punctuation
            last_punctuation = punctuation_matches[-1]
            return truncated[: last_punctuation.end()].strip()

        # If no punctuation, find last word
        # Split by spaces and take all words except the last one (which may be truncated)
        words = truncated.split()
        if len(words) > 1:
            return " ".join(words[:-1]).strip()

        # If only one word, return as is
        return truncated.strip()

    def _get_sentiment_from_rating(self, rating: int) -> Sentiment:
        """
        Get sentiment from rating.
        """
        if rating == 3:
            return Sentiment.NEUTRAL
        if rating >= 4:
            return Sentiment.POSITIVE
        if rating <= 2:
            return Sentiment.NEGATIVE

    async def _analyze_with_llm_relevant(
        self,
        feedback: Feedback,
        combined_input: str,
        session_id: str,
        rating: int,
        state: InMemoryState = None,
        adapter: ChannelAdapter = None,
    ) -> Sentiment | None:
        """
        Quick analysis for immediate user response - only checks relevance and sends response.
        Uses separate API keys for relevance and sentiment analysis to improve performance.

        Args:
            feedback: The feedback object
            combined_input: Combined rating and comments text
            session_id: Session ID for LLM calls
            rating: User rating for additional context
        """
        try:
            # Optimized sequential processing: relevance first, then sentiment if relevant
            (
                is_relevant,
                sentiment_str,
                relevance_service,
                _,
            ) = await self.llm_pool.process_relevance_and_sentiment_optimized(
                user_input=combined_input, rating=rating, session_id=session_id
            )

            # If not relevant, send message and return None
            if not is_relevant:
                logger.info("Feedback is not relevant to hotel zones", feedback_id=feedback.id)
                await self._create_or_update_analysis_result(
                    feedback_id=feedback.id,
                    status=AnalysisStatus.COMPLETED,
                    relevance=False,
                )
                await self._send_analysis_response(feedback, is_relevant=False, adapter=adapter)
                # Flush only the relevance service used
                if relevance_service:
                    await relevance_service.flush_langfuse()
                return None

            # Convert string sentiment to enum
            sentiment = convert_sentiment_string_to_enum(sentiment_str)

            await self._create_or_update_analysis_result(
                feedback_id=feedback.id,
                status=AnalysisStatus.ANALYSIS,
                relevance=True,
                sentiment=sentiment,
            )
            # Send response message to user immediately
            await self._send_analysis_response(feedback, sentiment=sentiment, is_relevant=True, adapter=adapter)

            logger.info(f"feedback.sentiment.response feedback_id={feedback.id} sentiment={sentiment.value}")

            return sentiment

        except Exception as e:
            logger.error("llm.quick.analysis.error", feedback_id=feedback.id, error=str(e))
            # Send fallback message on error
            await self._send_analysis_response(feedback, is_relevant=False, adapter=adapter)
            return Sentiment.NEUTRAL

    async def _analyze_with_llm_detailed(
        self,
        feedback: Feedback,
        combined_input: str,
        session_id: str,
        sentiment: Sentiment,
        state: InMemoryState,
        is_new_feedback: bool = True,
    ) -> AnalysisResult | None:
        """
        Detailed analysis for background processing - uses already obtained sentiment.

        Args:
            feedback: The feedback object
            combined_input: Combined rating and comments text
            session_id: Session ID for LLM calls
            sentiment: Already obtained sentiment from quick analysis

        Returns:
            AnalysisResult object or None if analysis fails
        """
        try:
            # Get criteria from scenarios table based on hotel_id and zone_id
            criteria = await self._get_criteria_from_scenario(feedback)

            # Get zone name for category
            zone_info = await self._get_feedback_with_zone_name(feedback.id)
            category = zone_info["zone_name"]

            # Use default criteria if no scenario found
            if not criteria:
                logger.warning("using.default.criteria", feedback_id=feedback.id)

            tags, recommendation, analysis_service = await self.llm_pool.analyze_review(
                user_input=combined_input,
                category=category,
                criteria=criteria,
                session_id=session_id,
            )

            # Create temporary analysis result object for return (will be updated in calling method)
            analysis_result = AnalysisResult(
                feedback_id=feedback.id,
                sentiment=sentiment,
                root_causes=tags,
                recommendation=recommendation,
            )

            if is_new_feedback:
                await self._send_manager_notifications(str(feedback.id), category, tags, state)

            # Flush only the analysis service used
            if analysis_service:
                await analysis_service.flush_langfuse()

            return analysis_result

        except Exception as e:
            logger.error("llm.detailed.analysis.error", feedback_id=feedback.id, error=str(e))
            return None

    async def _get_feedback_with_zone_name(self, feedback_id: str) -> dict | None:
        """
        Get feedback with zone name.

        Args:

            feedback_id: ID of the feedback to retrieve
        Returns:

            Dictionary with feedback data and zone name or None if not found

        """

        return await self.feedback_repo.get_feedback_with_zone_name(feedback_id)

    async def _get_managers_for_feedback_hotel(self, feedback_id: str) -> list[dict]:
        """
        Get all managers for the hotel associated with the feedback.

        Args:
            feedback_id: ID of the feedback to get managers for

        Returns:
            List of dictionaries with manager info:
            {'external_user_id': str, 'role': str, 'hotel_name': str, 'hotel_code': str}
            Returns an empty list if no managers found.
        """
        try:
            hotel_subquery = (
                select(UserHotel.hotel_id)
                .join(Feedback, Feedback.user_stay_id == UserHotel.id)
                .where(Feedback.id == feedback_id)
                .scalar_subquery()
            )

            result = await self.session.execute(
                select(
                    User.external_user_id,
                    User.channel_type,
                    Role.name.label("role_name"),
                    Hotel.name.label("hotel_name"),
                    Hotel.short_name.label("hotel_code"),
                )
                .join(UserHotel, User.id == UserHotel.user_id)
                .join(Role, UserHotel.role_id == Role.id)
                .join(Hotel, UserHotel.hotel_id == Hotel.id)
                .where(
                    UserHotel.hotel_id == hotel_subquery,
                    Role.name == RoleEnum.MANAGER.value,
                    UserHotel.close.is_(None),  # Only active assignments
                )
            )
            managers_result = result.all()
            if not managers_result:
                logger.warning(f"No managers found for feedback {feedback_id}")
                return []

            managers = []
            for row in managers_result:
                managers.append(
                    {
                        "external_user_id": str(row.external_user_id),
                        "channel_type": row.channel_type,
                        "role": row.role_name,
                        "hotel_name": row.hotel_name,
                        "hotel_code": row.hotel_code,
                    }
                )

            logger.info(f"Found {len(managers)} managers for feedback {feedback_id}")
            return managers

        except Exception as e:
            logger.error(f"Error getting managers for feedback {feedback_id}: {e}")
            return []

    async def _send_manager_notifications(
        self,
        feedback_id: str,
        zone_name: str,
        root_causes: list[str],
        state: InMemoryState,
    ) -> None:
        """
        Send notifications to all managers of the hotel about the feedback.

        Args:
            feedback_id: ID of the feedback
            zone_name: Name of the zone where feedback was given
            root_causes: List of root causes identified by LLM analysis
            state: InMemoryState for managing UI messages
        """

        # Get managers for this feedback's hotel
        managers = await self._get_managers_for_feedback_hotel(feedback_id)
        if not managers:
            logger.info(f"No managers found for feedback {feedback_id}")
            return

        try:
            result = await self.session.execute(
                select(
                    Zone.name,
                    Hotel.name,
                    Hotel.short_name,
                    User.phone_number,
                    Feedback.updated_at,
                    Hotel.timezone,
                )
                .select_from(Feedback)
                .join(UserHotel, Feedback.user_stay_id == UserHotel.id)
                .join(Hotel, UserHotel.hotel_id == Hotel.id)
                .join(Zone, Feedback.zone_id == Zone.id)
                .join(User, UserHotel.user_id == User.id)
                .where(Feedback.id == feedback_id)
            )
            row = result.first()

            if not row:
                logger.warning(f"Feedback info not found for notification: {feedback_id}")
                return

            (
                zone_name_from_db,
                hotel_name,
                hotel_code,
                guest_phone,
                updated_at,
                hotel_timezone,
            ) = row

        except Exception as e:
            logger.error(f"Error getting feedback info for notification {feedback_id}: {e}")
            return

        root_causes_text = (
            "\n• ".join(cause.replace("_", " ") for cause in root_causes) if root_causes else "Не определены"
        )

        feedback_created_date = convert_to_timezone(updated_at, hotel_timezone)

        notification_message = NOTIFICATION_MESSAGE.format(
            hotel_name=hotel_name,
            zone_name=zone_name_from_db,
            phone_number=guest_phone,
            created_at=feedback_created_date.strftime("%d.%m.%Y %H:%M"),
            root_causes_text=root_causes_text,
        )

        try:
            # Send notification to each manager
            for manager in managers:
                try:
                    # Get channel_type for this manager and create appropriate adapter
                    channel_type = manager.get("channel_type")
                    if channel_type == ChannelType.MAX:
                        adapter = MaxAdapter()
                    else:
                        # Default to Telegram if channel_type is None or TELEGRAM
                        adapter = TelegramAdapter()

                    # Create keyboard using the appropriate adapter
                    keyboard = adapter.manager_notification_keyboard(hotel_code, feedback_id)

                    await self.send_and_remember_message(
                        manager["external_user_id"],
                        notification_message,
                        adapter,
                        state,
                        inline_keyboard=keyboard,
                    )
                    logger.info(
                        f"Notification sent to manager "
                        f"{manager['external_user_id']} ({manager['role']}, "
                        f"channel={channel_type}) for feedback {feedback_id}"
                    )
                except Exception as e:
                    logger.error(f"Failed to send notification to manager {manager['external_user_id']}: {e}")

        except Exception as e:
            logger.error(f"Error sending manager notifications for feedback {feedback_id}: {e}")

    async def _get_user_id_from_feedback(self, feedback: Feedback) -> str | None:
        """
        Get external_user_id (Telegram chat_id) from feedback through user_stay_id -> UserHotel.user_id -> User.external_user_id.

        Args:
            feedback: The feedback object to get user ID for
        Returns:

            External user ID (Telegram chat_id) or None if not found
        """

        try:
            result = await self.session.execute(
                select(User.external_user_id)
                .join(UserHotel, User.id == UserHotel.user_id)
                .where(UserHotel.id == feedback.user_stay_id)
            )

            external_user_id = result.scalar()

            return str(external_user_id) if external_user_id else None

        except Exception as e:
            logger.error("failed.to.get.user_id", feedback_id=feedback.id, error=str(e))

            return None

    async def _get_channel_type_from_user_id(self, user_id: str) -> ChannelType | None:
        """
        Get channel_type from User table by external_user_id.

        Args:
            user_id: External user ID (external_user_id)
        Returns:
            ChannelType (TELEGRAM or MAX) or None if not found
        """
        try:
            result = await self.session.execute(
                select(User.channel_type).where(User.external_user_id == user_id)
            )
            channel_type = result.scalar()
            return channel_type
        except Exception as e:
            logger.error(
                f"failed.to.get.channel_type: user_id={user_id}, error={e}"
            )
            return None

    async def _get_criteria_from_scenario(self, feedback: Feedback) -> str | None:
        """
        Get criteria from scenarios table based on hotel_id and zone_id.

        Args:

            feedback: The feedback object to get criteria for

        Returns:
            Scenario prompt string or None if no scenario found

        """

        try:
            # First get hotel_id from user_stay_id -> UserHotel.hotel_id
            hotel_result = await self.session.execute(
                select(UserHotel.hotel_id).where(UserHotel.id == feedback.user_stay_id)
            )

            hotel_id = hotel_result.scalar()

            if not hotel_id:
                logger.warning("no.hotel_id.found", feedback_id=feedback.id)
                return None

            # Get scenario prompt for this hotel-zone combination
            scenario_result = await self.session.execute(
                select(Scenario.prompt).where(Scenario.hotel_id == hotel_id, Scenario.zone_id == feedback.zone_id)
            )

            prompt = scenario_result.scalar()

            if prompt:
                logger.info(
                    "scenario.criteria.found",
                    feedback_id=feedback.id,
                    hotel_id=hotel_id,
                    zone_id=feedback.zone_id,
                )
                return prompt

            logger.warning(
                "no.scenario.found",
                feedback_id=feedback.id,
                hotel_id=hotel_id,
                zone_id=feedback.zone_id,
            )

        except Exception as e:
            logger.error("failed.to.get.criteria", feedback_id=feedback.id, error=str(e))

        return None

    async def _check_analysis_result(self, feedback_id: str) -> AnalysisResult | None:
        """
        Check if analysis result already exists.
        """
        existing_result = await self.session.execute(
            select(AnalysisResult).where(AnalysisResult.feedback_id == feedback_id)
        )
        return existing_result.scalars().first()

    async def _create_or_update_analysis_result(
        self,
        feedback_id: str,
        status: AnalysisStatus,
        relevance: bool = None,
        sentiment: Sentiment = None,
        root_causes: list[str] = None,
        recommendation: str = None,
    ) -> AnalysisResult:
        """
        Create or update AnalysisResult record with specified status.

        Args:
            feedback_id: ID of the feedback
            status: Analysis status to set
            sentiment: Sentiment (optional)
            root_causes: Root causes (optional)
            recommendation: Recommendation (optional)

        Returns:
            AnalysisResult object
        """
        # Check if analysis result already exists
        analysis_result = await self._check_analysis_result(feedback_id)

        if analysis_result:
            # Update existing record
            analysis_result.status = status
            if relevance is not None:
                analysis_result.relevance = relevance
            if sentiment is not None:
                analysis_result.sentiment = sentiment
            if root_causes is not None:
                analysis_result.root_causes = root_causes
            if recommendation is not None:
                analysis_result.recommendation = recommendation
        else:
            # Create new record
            analysis_result = AnalysisResult(
                feedback_id=feedback_id,
                status=status,
                sentiment=sentiment,
                root_causes=root_causes,
                recommendation=recommendation,
            )
            self.session.add(analysis_result)

        await self.session.commit()
        return analysis_result

    async def _send_analysis_response(
        self,
        feedback: Feedback,
        sentiment: Sentiment | None = None,
        is_relevant: bool = True,
        adapter: ChannelAdapter = None,
    ) -> None:
        """
        Send appropriate response message based on analysis results.

        Args:

            feedback: The feedback object
            sentiment: Detected sentiment (optional)
            is_relevant: Whether the feedback is relevant to hotel zones
        """

        user_id = await self._get_user_id_from_feedback(feedback)

        if not user_id:
            logger.warning("cannot.send.message.no.user_id", feedback_id=feedback.id)
            return

        if not is_relevant:
            message = NEUTRAL_FEEDBACK_MESSAGE
        elif sentiment == Sentiment.NEGATIVE:
            message = NEGATIVE_FEEDBACK_MESSAGE
        else:  # POSITIVE or NEUTRAL
            message = POSITIVE_FEEDBACK_MESSAGE

        channel_type = await self._get_channel_type_from_user_id(user_id)
        if channel_type == ChannelType.MAX:
            adapter_to_use = MaxAdapter()
        else:
            adapter_to_use = TelegramAdapter()

        try:
            await self.send_message(user_id, message, adapter_to_use)
        except Exception as e:
            logger.error(
                f"Error sending analysis response: {e}, "
                f"feedback_id={feedback.id}, user_id={user_id}"
            )
