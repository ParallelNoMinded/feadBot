from typing import Dict

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.core.state import InMemoryState
from app.services.base import BaseService
from app.services.menu import MenuService
from app.config.messages import (
    MAX_AVAILABLE_RATING_MESSAGE,
    CHOOSE_HOTEL_MESSAGE,
    MAIN_MENU_BUTTON,
    SELECT_ZONE_FOR_FEEDBACK,
)

logger = structlog.get_logger(__name__)


class FeedbackService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.menu_service = MenuService(session)

    async def handle_feedback_request(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        can_leave_feedback: bool,
    ) -> None:
        logger.info(f"telegram.webhook.received.feedback: {msg.text}")

        hotel_code = msg.text.split("_LEAVE_FEEDBACK", 1)[0]

        manager = await self.manager_repo.get_by_telegram_id(msg.user_id, hotel_code)
        if manager:
            return await self.menu_service.handle_menu_request(msg, adapter, state)

        if not can_leave_feedback:
            return await self._show_limit_message(msg, adapter, state, hotel_code)

        # Validate active stay
        if not await self._validate_active_stay(msg.user_id, hotel_code):
            return await self._show_hotel_selection(msg, adapter, state)

        # Show zones selection
        return await self._show_zones_selection(msg, adapter, state, hotel_code)

    async def _validate_active_stay(self, user_id: str, hotel_code: str) -> bool:
        """Validate if user has active stay in hotel"""
        try:
            # Get user
            user = await self.user_repo.get_by_telegram_id(user_id)

            if not user:
                return False

            # Get hotel
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                return False

            # Check active stay
            stay = await self.user_hotel_repo.get_active_stay(user.id, hotel.id)
            return stay is not None

        except Exception as e:
            logger.error(f"Error validating active stay: {e}")
            return False

    async def _show_limit_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> Dict[str, str]:
        """Show feedback limit reached message"""
        # Don't clear UI messages - keep history visible

        # Get appropriate menu
        keyboard = adapter.main_menu_keyboard(hotel_code)

        # Send limit message
        m_id = await adapter.send_message(
            msg.user_id,
            MAX_AVAILABLE_RATING_MESSAGE,
            reply_markup=keyboard,
        )

        if m_id:
            state.remember_ui_message(msg.user_id, m_id)

        await adapter.answer_callback(msg.callback_id, "Лимит отзывов достигнут")

        return {"ok": "true"}

    async def _show_hotel_selection(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Show hotel selection list"""
        try:
            await self.clear_ui_message_buttons(msg.user_id, adapter, state)

            hotels = await self.catalog_repo.list_hotels()
            m2 = await adapter.send_message(
                msg.user_id,
                CHOOSE_HOTEL_MESSAGE,
                inline_keyboard={
                    "inline_keyboard": [
                        [
                            {
                                "text": hotel.name,
                                "callback_data": f"HOTEL_{hotel.short_name}",
                            }
                            for hotel in hotels
                        ]
                    ]
                },
            )
            if m2:
                state.remember_ui_message(msg.user_id, m2)
            await adapter.answer_callback(msg.callback_id, "Успешно")
        except Exception as e:
            logger.error(f"Error showing hotel selection: {e}")

        return {"ok": "true"}

    async def _show_zones_selection(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> Dict[str, str]:
        """Show zones selection for feedback"""
        try:
            # Don't clear UI messages - keep history visible

            # Get zones
            zones = await self._hotel_zones(hotel_code)
            zones_buttons = [
                [{"text": label, "callback_data": f"{hotel_code}_SPECIAL_ZONE_{code}"}] for code, label in zones
            ]
            zones_buttons.append([{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}])

            # Send zones selection
            m_id = await adapter.send_message(
                msg.user_id,
                SELECT_ZONE_FOR_FEEDBACK,
                inline_keyboard={"inline_keyboard": zones_buttons},
            )
            if m_id:
                state.remember_ui_message(msg.user_id, m_id)

            await adapter.answer_callback(msg.callback_id, "Успешно")
        except Exception as e:
            logger.error(f"Error showing zones selection: {e}")

        return {"ok": "true"}

    async def _hotel_zones(self, hotel: str | None) -> list[tuple[str, str]]:
        if not hotel:
            return []
        return await self.catalog_repo.list_zones_for_hotel_code(hotel)
