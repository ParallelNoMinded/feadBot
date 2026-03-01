from typing import Dict, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter
from app.config.messages import MANAGER_MENU_MESSAGE, ADMIN_MENU_MESSAGE
from app.core.state import InMemoryState
from app.repositories.admin import AdminRepository
from app.repositories.catalog import CatalogRepository
from app.repositories.managers import ManagerRepository
from app.repositories.user import UserRepository
from app.repositories.roles import RolesRepository
from app.repositories.user_hotel import UserHotelRepository

logger = structlog.get_logger(__name__)


class BaseService:
    """Base service with common dependencies and utilities"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.manager_repo = ManagerRepository(session)
        self.admin_repo = AdminRepository(session)
        self.catalog_repo = CatalogRepository(session)
        self.roles_repo = RolesRepository(session)
        self.user_repo = UserRepository(session)
        self.user_hotel_repo = UserHotelRepository(session)

    async def get_hotel_description(self, hotel_code: str, user_id: str) -> str:
        """Get hotel title by code, with special message for managers"""
        try:
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            hotel_name = hotel.name if hotel else "Главное меню"

            # Check if user is admin first (admin has priority over manager)
            admin = await self.admin_repo.get_by_telegram_id(user_id)
            if admin:
                return ADMIN_MENU_MESSAGE.format(hotel_name=hotel_name)

            # Check if user is a manager
            mgr = await self.manager_repo.get_by_telegram_id(user_id, hotel_code)
            if mgr:
                return MANAGER_MENU_MESSAGE.format(hotel_name=hotel_name)

            return hotel.description
        except Exception as e:
            logger.error(f"Error getting hotel title: {e}")
            return "Главное меню"

    async def get_manager_menu_keyboard(self, adapter: ChannelAdapter, hotel_code: str, user_id: str):
        """Get appropriate menu keyboard based on user role"""
        try:
            mgr = await self.manager_repo.get_by_telegram_id(user_id, hotel_code)
            if mgr:
                return adapter.manager_menu_keyboard(hotel_code)
            else:
                # Get last feedback ID for regular users to show
                # "Дополнить предыдущий отзыв" button
                last_feedback_id = await self.user_repo.get_last_feedback_id(user_id)
                return adapter.main_menu_keyboard(hotel_code, last_feedback_id=last_feedback_id)
        except Exception as e:
            logger.error(f"Error getting menu keyboard: {e}")
            return adapter.main_menu_keyboard(hotel_code)

    async def clear_ui_messages(self, user_id: str, adapter: ChannelAdapter, state: InMemoryState) -> None:
        """Clear all UI messages for user"""
        try:
            for mid in state.take_ui_messages(user_id):
                await adapter.delete_message(user_id, mid)
        except Exception as e:
            logger.error(f"Error clearing UI messages: {e}")

    async def clear_editing_prompt_message(self, user_id: str, adapter: ChannelAdapter, state: InMemoryState) -> None:
        """Clear only the editing prompt message for user"""
        try:
            message_id = state.get_editing_prompt_message_id(user_id)
            if message_id:
                await adapter.delete_message(user_id, message_id)
                state.clear_editing_prompt_message_id(user_id)
        except Exception as e:
            logger.error(f"Error clearing editing prompt message: {e}")

    async def clear_ui_message_buttons(self, user_id: str, adapter: ChannelAdapter, state: InMemoryState) -> None:
        """Clear buttons from all UI messages for user, keeping text"""
        try:
            ui_message_ids = state.get_ui_messages(user_id)
            logger.info(f"Clearing buttons from {len(ui_message_ids)} UI messages for user {user_id}")

            for message_id in ui_message_ids:
                try:
                    # Edit message to remove inline keyboard (keep text)
                    success = await adapter.edit_message_reply_markup(user_id, message_id, inline_keyboard={})
                    if success:
                        logger.info(f"Successfully removed buttons from message {message_id}")
                    else:
                        logger.warning(f"Failed to remove buttons from message {message_id}")
                except Exception as e:
                    # Message might not exist or already edited, continue
                    logger.debug(f"Could not edit message {message_id}: {e}")

            logger.info(f"Cleared buttons from {len(ui_message_ids)} UI messages for user {user_id}")
        except Exception as e:
            logger.error(f"Error clearing UI message buttons: {e}")

    async def send_message(self, user_id: str, text: str, adapter: ChannelAdapter) -> Optional[str]:
        """Send message"""
        try:
            message_id = await adapter.send_message(user_id, text)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return None

        return message_id

    async def send_and_remember_message(
        self,
        user_id: str,
        text: str,
        adapter: ChannelAdapter,
        state: InMemoryState,
        reply_markup: Optional[Dict] = None,
        inline_keyboard: Optional[Dict] = None,
    ) -> Optional[str | int]:
        """Send message and remember it in state"""
        try:
            message_id = await adapter.send_message(
                user_id,
                text,
                reply_markup=reply_markup,
                inline_keyboard=inline_keyboard,
            )
            if message_id:
                state.remember_ui_message(user_id, message_id)
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return None

        return message_id

    async def edit_and_remember_message(
        self,
        user_id: str,
        message_id: int,
        text: str,
        adapter: ChannelAdapter,
        state: InMemoryState,
        inline_keyboard: Optional[Dict] = None,
    ) -> bool:
        """Edit existing message and remember it in state"""
        try:
            success = await adapter.edit_message(user_id, message_id, text, inline_keyboard=inline_keyboard)
            if success:
                # Update the remembered message ID if it's in the UI messages
                state.remember_ui_message(user_id, message_id)
            return success
        except Exception as e:
            logger.error(f"Error editing message: {e}")
            return False
