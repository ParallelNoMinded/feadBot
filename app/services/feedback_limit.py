from typing import Dict, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import IncomingMessage, ChannelAdapter
from app.core.state import InMemoryState
from app.services.base import BaseService
from app.config.messages import MAX_AVAILABLE_RATING_MESSAGE, CHOOSE_HOTEL_MESSAGE

logger = structlog.get_logger(__name__)


class FeedbackLimitService(BaseService):
    """Service for handling feedback limits and related UI"""

    def __init__(self, session: AsyncSession):
        super().__init__(session)

    async def show_limit_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
        callback_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Show feedback limit reached message"""
        try:
            # Don't clear UI messages - keep history visible

            # Get appropriate menu keyboard
            keyboard = await self.get_manager_menu_keyboard(adapter, hotel_code, msg.user_id)

            # Send limit message
            await self.send_and_remember_message(
                msg.user_id,
                MAX_AVAILABLE_RATING_MESSAGE,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            # Answer callback if provided
            if callback_id:
                await adapter.answer_callback(callback_id, "Лимит отзывов достигнут")

            # Set selected hotel in state
            state.set_selected_hotel(msg.user_id, hotel_code)

        except Exception as e:
            logger.error(f"Error showing feedback limit message: {e}")

        return {"ok": "false"}

    async def show_hotels_list(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        callback_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Show hotels list for selection"""
        try:
            state.clear_registration(msg.channel, msg.user_id)

            await self.clear_ui_message_buttons(msg.user_id, adapter, state)

            # Get hotels list
            hotels = await self.catalog_repo.list_hotels()
            hotels_keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": hotel.name,
                            "callback_data": f"HOTEL_{hotel.short_name}",
                        }
                        for hotel in hotels
                    ]
                ]
            }

            # Send hotels list
            await self.send_and_remember_message(
                msg.user_id, CHOOSE_HOTEL_MESSAGE, adapter, state, inline_keyboard=hotels_keyboard
            )

            # Answer callback if provided
            if callback_id:
                await adapter.answer_callback(callback_id, "Отель")

        except Exception as e:
            logger.error(f"Error showing hotels list: {e}")

        return {"ok": "false"}
