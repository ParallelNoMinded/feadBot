"""
Message routing service for different types of Telegram messages.
Provides clean separation of routing logic from webhook handling.
"""

from typing import Dict, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.config.settings import settings
from app.core.state import InMemoryState
from app.services.base import BaseService
from app.services.callback import CallbackService
from app.services.command import CommandService
from app.services.registration import RegistrationService
from app.services.webhook_processing import WebhookProcessingService

logger = structlog.get_logger(__name__)


class MessageRouter(BaseService):
    """Routes different types of messages to appropriate handlers."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.command_service = CommandService(session)
        self.callback_service = CallbackService(session, settings)
        self.webhook_processor = WebhookProcessingService(session)

    async def route_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        ctx: Dict,
        parsed_early: Dict,
    ) -> Dict[str, str]:
        """
        Route message to appropriate handler based on message type.

        Args:
            msg: Parsed incoming message
            adapter: Telegram adapter instance
            state: In-memory state manager
            ctx: Message context
            parsed_early: Early parsed payload data

        Returns:
            Response dictionary
        """
        # Get active feedback session
        active_fs = state.get_feedback_session(msg.channel, msg.user_id)

        # Route based on message type
        if msg.contact_phone:
            return await self._route_contact_message(msg, adapter, state)

        elif msg.callback_id and msg.payload:
            return await self._route_callback_message(msg, adapter, state, parsed_early, active_fs)

        elif msg.text == "/start":
            # Clear buttons for guest users when they send /start outside
            await self._clear_buttons_for_guest_if_needed(msg, adapter, state, active_fs)
            return await self._route_start_command(msg, adapter, state, ctx)

        elif self._is_feedback_message(msg):
            return await self._route_feedback_message(msg, adapter, state, active_fs, ctx, parsed_early)

        else:
            logger.warning("Unknown message type", user_id=msg.user_id, msg_type=type(msg).__name__)
            return {"ok": "true"}

    def _is_feedback_message(self, msg: IncomingMessage) -> bool:
        """Check if message is for feedback processing."""
        return (msg.text or msg.media_token) and not msg.callback_id

    async def _route_contact_message(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Route contact sharing message."""
        logger.info(
            "Processing contact phone for registration",
            user_id=msg.user_id,
            phone=msg.contact_phone,
        )

        # Get hotel context from registration state or selected hotel
        reg_state = state.get_registration(msg.channel, msg.user_id)
        hotel_code = None

        if reg_state and reg_state.get("context"):
            ctx = reg_state.get("context", {})
            target = ctx.get("target_hotel")
            resume = ctx.get("resume", {}).get("hotel")
            hotel_code = target or resume

        # Fallback to selected hotel if not in registration context
        if not hotel_code:
            hotel_code = state.get_selected_hotel(msg.user_id)

        logger.info(
            "Contact processing with hotel context",
            user_id=msg.user_id,
            hotel_code=hotel_code,
            has_reg_state=bool(reg_state),
        )

        # Create registration service with adapter
        registration_service = RegistrationService(self.session, adapter)
        await registration_service.handle_contact(msg.user_id, msg.contact_phone, hotel_code)
        await self.session.commit()
        return {"ok": "true"}

    async def _route_callback_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        parsed_early: Dict,
        active_fs: Optional[Dict],
    ) -> Dict[str, str]:
        """Route callback query message."""
        return await self.callback_service.handle_callback(msg, adapter, state, parsed_early, active_fs)

    async def _route_start_command(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        ctx: Dict,
    ) -> Dict[str, str]:
        """Route start command message."""
        return await self.command_service.handle_start_command(msg, adapter, state, ctx)

    async def _route_feedback_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        active_fs: Optional[Dict],
        ctx: Dict,
        parsed_early: Dict,
    ) -> Dict[str, str]:
        """Route feedback session message."""
        has_rating = active_fs.get("rating") if active_fs else None
        logger.info(f"feedback.session.check: active_fs={active_fs}, " f"has_rating={has_rating}")
        return await self.webhook_processor.process_feedback_session(msg, adapter, state, active_fs, ctx, parsed_early)

    async def _clear_buttons_for_guest_if_needed(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        active_fs: Optional[Dict],
    ) -> None:
        """Clear buttons from previous messages for guest users"""
        try:
            # Only clear buttons if there's no active feedback session
            if active_fs:
                return

            # Clear buttons from UI messages
            await self.clear_ui_message_buttons(msg.user_id, adapter, state)
            logger.info(f"Cleared buttons for guest user {msg.user_id} outside feedback session")

        except Exception as e:
            logger.error(f"Error clearing buttons for guest user: {e}")
