from typing import Dict

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.core.state import InMemoryState
from app.repositories.admin import AdminRepository
from app.repositories.catalog import CatalogRepository
from app.repositories.managers import ManagerRepository
from app.repositories.user import UserRepository
from app.services.base import BaseService
from app.services.feedback_limit import FeedbackLimitService
from app.services.registration import RegistrationService
from app.services.ui_message import UIMessageService
from app.services.user_validation import UserValidationService
from app.config.messages import ADMIN_MENU_MESSAGE, RATING_MESSAGE

logger = structlog.get_logger(__name__)


class CommandService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.user_validation = UserValidationService(session)
        self.feedback_limit = FeedbackLimitService(session)
        self.ui_message = UIMessageService(session)
        self.user_repo = UserRepository(session)
        self.manager_repo = ManagerRepository(session)
        self.admin_repo = AdminRepository(session)
        self.catalog_repo = CatalogRepository(session)

    async def handle_start_command(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        ctx: Dict,
    ) -> Dict[str, str]:
        """Handle /start command with all its variations"""
        logger.info("start.ctx", context=ctx)

        # Check if user is admin - if yes, always show admin menu
        is_admin = await self.admin_repo.get_by_telegram_id(msg.user_id)
        if is_admin:
            logger.info(f"Admin detected in /start: {msg.user_id}")
            keyboard = adapter.admin_menu_keyboard()
            await self.send_and_remember_message(
                msg.user_id,
                ADMIN_MENU_MESSAGE,
                adapter,
                state,
                inline_keyboard=keyboard,
            )
            return {"ok": "true"}

        # Don't clear UI messages - keep history visible

        can_leave_feedback = await self.user_repo.can_user_leave_feedback(msg.user_id)

        # If deep-link provides both hotel and zone → open rating form immediately
        if ctx.get("hotel") and ctx.get("zone"):
            return await self._handle_hotel_zone_start(msg, adapter, state, ctx, can_leave_feedback)

        # If no hotel and no zone → show hotels list
        if not ctx.get("hotel") and not ctx.get("zone"):
            return await self._handle_no_context_start(msg, adapter, state)

        # If /start with hotel context → show hotel menu
        if ctx.get("hotel"):
            return await self._handle_hotel_only_start(msg, adapter, state, ctx)

        return {"ok": "true"}

    async def _handle_hotel_zone_start(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        ctx: Dict,
        can_leave_feedback: bool,
    ) -> Dict[str, str]:
        """Handle /start with both hotel and zone"""
        hotel_code = ctx.get("hotel")
        zone_code = ctx.get("zone")

        logger.info(
            "start.branch",
            kind="hotel+zone",
            hotel=hotel_code,
            zone=zone_code,
        )

        # Check registration
        registered = await self.user_validation.is_registered(msg.user_id, hotel_code)

        logger.info("start.hotel_zone.registration", registered=registered)

        if not registered:
            # Start registration with resume context
            st = state.upsert_registration(msg.channel, msg.user_id)
            c = st.get("context", {})
            c["resume"] = ctx
            state.set_registration(msg.channel, msg.user_id, context=c)
            registration_service = RegistrationService(self.session, adapter)
            await registration_service.start(msg.user_id, resume_context=ctx)
            return {"ok": "true"}

        is_manager = await self.manager_repo.get_by_telegram_id(msg.user_id, hotel_code)

        if is_manager:
            return await self.feedback_limit.show_hotels_list(msg, adapter, state)

        # Check feedback limit
        if not can_leave_feedback:
            return await self.feedback_limit.show_limit_message(msg, adapter, state, hotel_code)

        # Check if there's an active feedback session and handle zone transition
        await self._handle_zone_transition(msg, adapter, state, hotel_code)

        # Show rating UI immediately
        return await self.ui_message.show_rating_ui(msg, adapter, state, hotel_code, zone_code)

    async def _handle_no_context_start(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle /start without context - show hotels list"""
        logger.info("start.branch", extra={"kind": "no_context"})
        return await self.feedback_limit.show_hotels_list(msg, adapter, state)

    async def _handle_hotel_only_start(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        ctx: Dict,
    ) -> Dict[str, str]:
        """Handle /start with hotel context only"""
        hotel_code = ctx.get("hotel")
        logger.info(f"start.branch hotel_only.registered {hotel_code}")

        # Check registration
        if not await self.user_validation.is_registered(msg.user_id, hotel_code):
            st = state.upsert_registration(msg.channel, msg.user_id)
            ctx = st.get("context", {})
            ctx["resume"] = ctx
            state.set_registration(msg.channel, msg.user_id, context=ctx)
            registration_service = RegistrationService(self.session, adapter)
            await registration_service.start(msg.user_id, resume_context=ctx)
            return {"ok": "true"}

        # Show hotel menu
        state.set_selected_hotel(msg.user_id, hotel_code)
        return await self.ui_message.show_hotel_menu(msg, adapter, state, hotel_code)

    async def _handle_zone_transition(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> None:
        """Handle transition to new zone - close previous feedback sessions"""
        try:
            await self.ui_message.clear_rating_ui_messages(msg, adapter, state)

            state.clear_compose_prompt(msg.user_id)

            active_fs = state.get_feedback_session(msg.channel, msg.user_id)

            if active_fs and active_fs.get("rating"):
                previous_zone_code = active_fs.get("zone")
                previous_rating = active_fs.get("rating")

                zone_name = await self._get_zone_name(hotel_code, previous_zone_code)

                rating_text = self._format_rating_message(zone_name, previous_rating)

                await adapter.send_message(msg.user_id, rating_text)

                state.end_feedback_session(msg.channel, msg.user_id)

                logger.info(f"zone_transition.previous_session_closed: user_id={msg.user_id}")

        except Exception as e:
            logger.error(f"Error handling zone transition: {e}")

    async def _get_zone_name(self, hotel_code: str, zone_code: str) -> str:
        """Get zone name by codes"""
        try:
            zone = await self.catalog_repo.get_zone_by_code(hotel_code, zone_code)
            return zone.name if zone else zone_code
        except Exception as e:
            logger.error("command.get_zone_name.error", error=str(e))
            return zone_code

    def _format_rating_message(self, zone_name: str, rating: int) -> str:
        """Format rating message with stars"""
        stars = "⭐️" * rating
        return RATING_MESSAGE.format(zone_name=zone_name, stars=stars, rating=rating)
