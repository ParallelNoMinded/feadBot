import asyncio
from typing import Dict

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.core.state import InMemoryState
from app.services.base import BaseService
from app.services.feedback_processor import FeedbackProcessorService
from app.services.ui_message import UIMessageService
from shared_models import FeedbackStatus

logger = structlog.get_logger(__name__)


class MenuService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.feedback_processor = FeedbackProcessorService(session)
        self.ui_message = UIMessageService(session)

    async def handle_menu_request(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> None:
        logger.info(f"telegram.webhook.received.menu: {msg.text}")

        # Extract hotel code from message
        hotel_code = msg.text.split("_", 1)[0]
        logger.info(f"telegram.webhook.received.hotel_code: {hotel_code}")

        # Clear feedback messages if coming from feedback completion
        active_fs = state.get_feedback_session(msg.channel, msg.user_id)
        if active_fs:
            await self.ui_message.clear_feedback_messages(msg, adapter, state)

        # Clear all media messages when going to main menu
        await self._clear_all_user_media_messages(msg.user_id, adapter, state)

        # Process active feedback session if exists and check if feedback was
        # just completed
        await self._process_active_feedback_session(msg, state, adapter)

        # Get hotel description
        title = await self.get_hotel_description(hotel_code, msg.user_id)

        # Determine menu type and get appropriate keyboard
        keyboard = await self._get_menu_keyboard(msg.user_id, hotel_code, adapter)

        # Send menu message and remember it in state
        await self.send_and_remember_message(
            msg.user_id,
            title,
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        state.clear_compose_prompt(msg.user_id)

        # Answer callback to show checkmark
        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Меню открыто")

        return {"ok": "true"}

    async def _process_active_feedback_session(
        self, msg: IncomingMessage, state: InMemoryState, adapter: ChannelAdapter
    ) -> None:
        """Process feedback in background task. Returns True if just completed."""
        current_session = state.get_feedback_session(msg.channel, msg.user_id)

        if current_session and current_session.get("active_feedback_id"):
            feedback_id = current_session.get("active_feedback_id")
            logger.info(f"feedback.session.ended_by_menu: feedback_id={feedback_id}")

            # Disable rating UI before ending session
            await self._disable_rating_ui_if_exists(msg, state, current_session, adapter)

            # End feedback session immediately to prevent duplicate processing
            state.end_feedback_session(msg.channel, msg.user_id)

            # Process feedback in background
            result = await self.feedback_processor.process_feedback_session_relevant(feedback_id, state, adapter)

            if result is not None:
                feedback, combined_input, session_id, sentiment = result
                is_new_feedback = current_session.get("is_new_feedback", True)
                asyncio.create_task(
                    self.feedback_processor.process_feedback_session_background(
                        feedback,
                        combined_input,
                        session_id,
                        sentiment,
                        state,
                        is_new_feedback,
                    )
                )

                try:
                    feedback.status = FeedbackStatus.OPENED
                    await self.session.commit()
                except Exception:
                    await self.session.rollback()

    async def _disable_rating_ui_if_exists(
        self,
        msg: IncomingMessage,
        state: InMemoryState,
        current_session: dict,
        adapter: ChannelAdapter,
    ) -> None:
        """Disable rating UI if it exists for the current feedback session"""
        try:
            # Get rating message ID from state
            rating_message_id = state.get_rating_message_id(msg.channel, msg.user_id)
            if not rating_message_id:
                return

            # Get hotel and zone from current session
            hotel_code = current_session.get("hotel")
            zone_code = current_session.get("zone")
            current_rating = current_session.get("rating")

            if not hotel_code or not zone_code:
                return

            # Disable the rating UI
            await self.ui_message.disable_rating_ui(
                msg,
                adapter,
                state,
                hotel_code,
                zone_code,
                current_rating,
                rating_message_id,
            )

        except Exception as e:
            logger.error(f"Error disabling rating UI: {e}")

    async def _get_menu_keyboard(self, user_id: str, hotel_code: str, adapter: ChannelAdapter) -> Dict:
        """Get appropriate menu keyboard based on user role and feedback completion status"""
        try:
            # Check if user is admin first (admin has priority over manager)
            admin = await self.admin_repo.get_by_telegram_id(user_id)
            if admin:
                return adapter.admin_menu_keyboard()

            # Check if user is a manager
            mgr = await self.manager_repo.get_by_telegram_id(user_id, hotel_code)
            if mgr:
                return adapter.manager_menu_keyboard(hotel_code)
        except Exception as e:
            logger.error(f"Error getting menu keyboard: {e}")

        # Check if user has previous feedback
        last_feedback_id = await self.user_repo.get_last_feedback_id(user_id)

        return adapter.main_menu_keyboard(hotel_code, last_feedback_id=last_feedback_id)

    async def _clear_all_user_media_messages(
        self,
        user_id: str,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> None:
        """Clear all media messages for a user"""
        try:
            # Get all media message IDs for this user
            media_message_ids = state.clear_all_user_media_messages(user_id)

            if not media_message_ids:
                logger.info(f"No media messages found for user {user_id}")
                return

            # Delete all media messages
            deleted_count = 0
            for message_id in media_message_ids:
                try:
                    await adapter.delete_message(user_id, message_id)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete media message {message_id}: {e}")

            # Remove deleted media messages from UI messages list
            if deleted_count > 0:
                ui_messages = state.get_ui_messages(user_id)
                remaining_messages = [msg_id for msg_id in ui_messages if msg_id not in media_message_ids]

                # Update state with remaining messages
                state.take_ui_messages(user_id)  # Clear all
                for msg_id in remaining_messages:
                    state.remember_ui_message(user_id, msg_id)

            logger.info(f"Cleared {deleted_count} media messages for user {user_id}")

        except Exception as e:
            logger.error(f"Error clearing all user media messages: {e}")
