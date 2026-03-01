from typing import Dict, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.config.messages import (
    COMPOSE_PROMPT_MESSAGE,
    FEEDBACK_RESPONSE_AFTER_ADDITIONAL_MESSAGE,
    FEEDBACK_RESPONSE_AFTER_FIRST_MESSAGE,
    MAIN_MENU_BUTTON,
    NEGATIVE_THUMB_RATING_MESSAGE,
    POSITIVE_THUMB_RATING_MESSAGE,
    RATING_MESSAGE,
    RATING_REQUEST_MESSAGE,
    RATING_REQUEST_MESSAGE_ZONE,
    SELECT_ZONE_FOR_FEEDBACK,
)
from app.config.settings import settings
from app.core.state import InMemoryState
from app.services.base import BaseService
from app.services.button_state import ButtonStateService

logger = structlog.get_logger(__name__)


class UIMessageService(BaseService):
    """Service for managing UI messages and interactions"""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.button_state = ButtonStateService(session)

    async def show_rating_ui(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
        zone_code: str,
        current_rating: int = None,
    ) -> Dict[str, str]:
        """Show rating UI for hotel and zone"""
        try:
            # Get zone entity to determine keyboard type
            z_ent = await self.catalog_repo.get_zone_by_code(
                hotel_code,
                zone_code,
            )
            kb = adapter.rating_keyboard(
                hotel_code,
                zone_code,
                current_rating,
            )
            if z_ent and not z_ent.is_adult:
                kb = adapter.thumbs_keyboard(
                    hotel_code,
                    zone_code,
                    current_rating,
                )

            logger.info(
                "ui.rating.show",
                hotel=hotel_code,
                zone=zone_code,
                current_rating=current_rating,
            )

            # Set state first
            state.set_selected_hotel(msg.user_id, hotel_code)
            await state.start_feedback_session(
                msg.channel,
                msg.user_id,
                hotel=hotel_code,
                zone=zone_code,
                rating=current_rating,
                is_new_feedback=True,
            )

            message_text = RATING_REQUEST_MESSAGE
            if z_ent:
                message_text = RATING_REQUEST_MESSAGE_ZONE.format(zone_name=z_ent.name)
                if z_ent.description is not None:
                    message_text = z_ent.description

            message_id = await self.send_and_remember_message(
                msg.user_id,
                message_text,
                adapter,
                state,
                inline_keyboard=kb,
            )

            # Save the rating message ID for future editing
            if message_id:
                state.set_rating_message_id(
                    msg.channel,
                    msg.user_id,
                    message_id,
                )

        except Exception as e:
            logger.error(f"Error showing rating UI: {e}")

        return {"ok": "false"}

    async def edit_instruction_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        new_text: str,
    ) -> bool:
        """Edit existing instruction message to remove button"""
        try:
            instruction_message_id = state.get_instruction_message_id(msg.channel, msg.user_id)
            if not instruction_message_id:
                logger.warning("No instruction message ID found for user", user_id=msg.user_id)
                return False

            logger.info(f"ui.instruction.edit: user_id={msg.user_id}, message_id={instruction_message_id}")

            # Edit the existing message without keyboard
            success = await adapter.edit_message(
                msg.user_id,
                instruction_message_id,
                new_text,
                inline_keyboard=None,  # Remove keyboard
            )

            if success:
                logger.info("Instruction message edited successfully")
            else:
                logger.error("Failed to edit instruction message")

            return success

        except Exception as e:
            logger.error(f"Error editing instruction message: {e}")
            return False

    async def edit_rating_ui(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        hotel_code: str,
        zone_code: str,
        current_rating: int,
        message_id: int,
    ) -> bool:
        """Edit existing rating UI message with new rating"""
        try:
            # Get zone entity to determine keyboard type
            z_ent = await self.catalog_repo.get_zone_by_code(
                hotel_code,
                zone_code,
            )
            kb = adapter.rating_keyboard(
                hotel_code,
                zone_code,
                current_rating,
            )
            if z_ent and not z_ent.is_adult:
                kb = adapter.thumbs_keyboard(
                    hotel_code,
                    zone_code,
                    current_rating,
                )

            logger.info(
                "ui.rating.edit",
                hotel=hotel_code,
                zone=zone_code,
                current_rating=current_rating,
                message_id=message_id,
            )

            # Edit the existing message
            message_text = RATING_REQUEST_MESSAGE
            if z_ent:
                message_text = RATING_REQUEST_MESSAGE_ZONE.format(zone_name=z_ent.name)
                if z_ent.description is not None:
                    message_text = z_ent.description

            success = await adapter.edit_message(
                msg.user_id,
                message_id,
                message_text,
                inline_keyboard=kb,
            )

            return success

        except Exception as e:
            logger.error(f"Error editing rating UI: {e}")
            return False

    def _format_rating_result_text(self, zone_name: str, current_rating: int, is_adult: bool) -> str:
        """Format rating result text based on rating type and value"""
        if is_adult:
            # Star rating (1-5 stars)
            stars = "⭐" * current_rating
            return RATING_MESSAGE.format(zone_name=zone_name, stars=stars, rating=current_rating)
        else:
            # Thumb rating
            if current_rating == 5:
                return POSITIVE_THUMB_RATING_MESSAGE.format(zone_name=zone_name)
            return NEGATIVE_THUMB_RATING_MESSAGE.format(zone_name=zone_name)

    async def show_rating_result_ui(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
        zone_code: str,
        current_rating: int = None,
        message_id: int = None,
    ) -> bool:
        """Show rating result UI with selected rating instead of disabled buttons"""
        try:
            # Get zone entity to determine rating type
            z_ent = await self.catalog_repo.get_zone_by_code(
                hotel_code,
                zone_code,
            )

            # Get zone name
            zone_name = "зоне"
            if z_ent and getattr(z_ent, "name", None):
                zone_name = z_ent.name

            # Format message text with rating result
            message_text = self._format_rating_result_text(zone_name, current_rating, z_ent.is_adult if z_ent else True)

            logger.info(
                "ui.rating.result",
                hotel=hotel_code,
                zone=zone_code,
                current_rating=current_rating,
                message_id=message_id,
            )

            # Edit the existing message without keyboard (remove buttons)
            success = await adapter.edit_message(
                msg.user_id,
                message_id,
                message_text,
                inline_keyboard=None,  # Remove keyboard completely
            )

            return success

        except Exception as e:
            logger.error(f"Error showing rating result UI: {e}")
            return False

    async def disable_rating_ui(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
        zone_code: str,
        current_rating: int = None,
        message_id: int = None,
    ) -> bool:
        """Show rating result instead of disabled buttons"""
        return await self.show_rating_result_ui(msg, adapter, state, hotel_code, zone_code, current_rating, message_id)

    async def show_hotel_menu(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> Dict[str, str]:
        """Show hotel menu"""
        try:
            # Get hotel title
            description = await self.get_hotel_description(hotel_code, msg.user_id)

            # Get appropriate keyboard
            keyboard = await self.get_manager_menu_keyboard(
                adapter,
                hotel_code,
                msg.user_id,
            )

            # Send message
            await self.send_and_remember_message(
                msg.user_id,
                description,
                adapter,
                state,
                reply_markup=keyboard,
            )

            # Set selected hotel
            state.set_selected_hotel(msg.user_id, hotel_code)

        except Exception as e:
            logger.error(f"Error showing hotel menu: {e}")

        return {"ok": "false"}

    async def show_zones_selection(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
        callback_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Show zones selection for feedback"""
        try:
            # Get zones
            zones = await self.catalog_repo.list_zones_for_hotel_code(
                hotel_code,
            )
            zones_buttons = [
                [
                    {
                        "text": label,
                        "callback_data": f"{hotel_code}_ZONE_{code}",
                    }
                ]
                for code, label in zones
            ]
            zones_buttons.append(
                [
                    {
                        "text": MAIN_MENU_BUTTON,
                        "callback_data": f"{hotel_code}_MENU",
                    }
                ]
            )

            # Send zones selection
            await self.send_and_remember_message(
                msg.user_id,
                SELECT_ZONE_FOR_FEEDBACK,
                adapter,
                state,
                inline_keyboard={"inline_keyboard": zones_buttons},
            )

            # Answer callback if provided
            if callback_id:
                await adapter.answer_callback(callback_id, "Зона выбрана")

        except Exception as e:
            logger.error(f"Error showing zones selection: {e}")

        return {"ok": "false"}

    async def show_compose_prompt(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> None:
        """Show compose prompt for feedback"""
        if not state.has_compose_prompt_shown(msg.user_id):
            message_id = await self.send_and_remember_message(
                msg.user_id,
                COMPOSE_PROMPT_MESSAGE.format(max_messages=settings.MAX_FEEDBACK_MESSAGES),
                adapter,
                state,
                reply_markup=adapter.compose_feedback_keyboard(hotel_code),
            )
            if message_id:
                state.add_feedback_message_id(msg.channel, msg.user_id, message_id)
                # Check if button was already pressed and update if needed
                await self.button_state.update_feedback_message_if_needed(msg, adapter, message_id, hotel_code)
            state.mark_compose_prompt_shown(msg.user_id)

    async def clear_and_show_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        text: str,
        reply_markup: Optional[Dict] = None,
        inline_keyboard: Optional[Dict] = None,
    ) -> None:
        """Clear UI messages and show new message"""

        await self.clear_ui_messages(msg.user_id, adapter, state)
        await self.send_and_remember_message(
            msg.user_id,
            text,
            adapter,
            state,
            reply_markup=reply_markup,
            inline_keyboard=inline_keyboard,
        )

    async def clear_feedback_messages(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> None:
        """Clear feedback-related messages and remove buttons from instruction messages"""
        try:
            feedback_message_ids = state.clear_feedback_message_ids(msg.channel, msg.user_id)
            for message_id in feedback_message_ids:
                await adapter.delete_message(msg.user_id, message_id)

            instruction_message_id = state.get_instruction_message_id(msg.channel, msg.user_id)
            if instruction_message_id:
                try:
                    await adapter.edit_message_reply_markup(
                        msg.user_id,
                        instruction_message_id,
                        inline_keyboard={},
                    )
                    logger.info(f"Removed buttons from instruction message " f"{instruction_message_id}")
                except Exception as e:
                    logger.info(f"Could not remove buttons from instruction message " f"{instruction_message_id}: {e}")
        except Exception as e:
            logger.error(f"Error clearing feedback messages: {e}")

    async def clear_rating_ui_messages(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> None:
        """Edit previous UI messages to remove buttons, keeping text"""
        try:
            # Get all UI messages and edit them to remove buttons
            ui_message_ids = state.get_ui_messages(msg.user_id)
            logger.info(
                f"Attempting to edit {len(ui_message_ids)} UI messages for user {msg.user_id}: {ui_message_ids}"
            )

            for message_id in ui_message_ids:
                try:
                    # Edit message to remove inline keyboard (keep text)
                    success = await adapter.edit_message_reply_markup(msg.user_id, message_id, inline_keyboard={})
                    if success:
                        logger.info(f"Successfully removed buttons from message {message_id}")
                    else:
                        logger.warning(f"Failed to remove buttons from message {message_id}")
                except Exception as e:
                    # Message might not exist or already edited, continue
                    logger.debug(f"Could not edit message {message_id}: {e}")

            # Clear UI messages from state after editing
            state.take_ui_messages(msg.user_id)

            # Clear rating message ID from state
            state.clear_rating_message_id(msg.channel, msg.user_id)

            logger.info(f"Edited {len(ui_message_ids)} UI messages to remove buttons for user {msg.user_id}")
        except Exception as e:
            logger.error(f"Error editing rating UI messages: {e}")

    async def send_feedback_response_after_first_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> None:
        """Send response message after user sends first message"""
        # Clear previous feedback messages and send new one
        await self.clear_feedback_messages(msg, adapter, state)
        message_id = await self.send_and_remember_message(
            msg.user_id,
            FEEDBACK_RESPONSE_AFTER_FIRST_MESSAGE,
            adapter,
            state,
            reply_markup=adapter.compose_feedback_keyboard(hotel_code),
        )
        if message_id:
            state.add_feedback_message_id(msg.channel, msg.user_id, message_id)
            # Check if button was already pressed and update if needed
            await self.button_state.update_feedback_message_if_needed(msg, adapter, message_id, hotel_code)

    async def send_feedback_response_after_additional_message(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> None:
        """Send response message after user sends additional message"""
        # Clear previous feedback messages and send new one
        await self.clear_feedback_messages(msg, adapter, state)
        message_id = await self.send_and_remember_message(
            msg.user_id,
            FEEDBACK_RESPONSE_AFTER_ADDITIONAL_MESSAGE,
            adapter,
            state,
            reply_markup=adapter.compose_feedback_keyboard(hotel_code),
        )
        if message_id:
            state.add_feedback_message_id(msg.channel, msg.user_id, message_id)
            # Check if button was already pressed and update if needed
            await self.button_state.update_feedback_message_if_needed(msg, adapter, message_id, hotel_code)
