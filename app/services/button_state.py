from typing import Dict, Optional, Set

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import IncomingMessage
from app.adapters.channel import ChannelAdapter
from app.services.base import BaseService
from app.config.messages import USER_FEEDBACK_COMPLETION_BUTTON
from shared_models import Zone

logger = structlog.get_logger(__name__)

NAVIGATION_ACTION_PATTERNS = frozenset(
    [
        "MGR_QR",
        "_MENU",
        "_ABOUT_BOT",
        "_LEAVE_FEEDBACK",
        "_HELP",
        "_CONSENT_YES",
        "_CONSENT_NO",
        "LASTFEEDBACK_",
        "MGR_NEGATIVE_FEEDBACKS",
        "MGR_REPORTS",
        "MGR_FEEDBACK_",
        "MGR_PROMPTS",
        "MGR_PROMPT_ZONE_",
        "MGR_RESET_PROMPT_",
        "MGR_EDIT_PROMPT_",
        "MGR_REPORT_HOTEL_",
        "MGR_REPORT_WEEK_",
        "MGR_REPORT_MONTH_",
        "MGR_REPORT_HALF-YEAR_",
        "MGR_REPORT_YEAR_",
        "MGR_REPORT_CUSTOM",
        "_SPECIAL_ZONE_",
        "ADMIN_",
    ]
)


class ButtonStateService(BaseService):
    """Service for managing button states and updates"""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        # Track pressed buttons per user:
        # {user_id: {message_id: {callback_data: pressed}}}
        self.pressed_buttons: Dict[str, Dict[int, Set[str]]] = {}

    def mark_button_pressed(self, user_id: str, message_id: int, callback_data: str) -> None:
        """Mark a button as pressed"""
        if user_id not in self.pressed_buttons:
            self.pressed_buttons[user_id] = {}
        if message_id not in self.pressed_buttons[user_id]:
            self.pressed_buttons[user_id][message_id] = set()
        self.pressed_buttons[user_id][message_id].add(callback_data)

    def is_button_pressed(self, user_id: str, message_id: int, callback_data: str) -> bool:
        """Check if a button was pressed"""
        return (
            user_id in self.pressed_buttons
            and message_id in self.pressed_buttons[user_id]
            and callback_data in self.pressed_buttons[user_id][message_id]
        )

    def is_rating_button(self, callback_data: str) -> bool:
        """Check if this is a rating button (RATE_ or THUMB_)"""
        return "_RATE_" in callback_data or "_THUMB_" in callback_data

    def update_button_text(self, text: str, callback_data: str, is_pressed: bool) -> str:
        """Update button text to show pressed state"""
        if is_pressed and not self.is_rating_button(callback_data):
            if not text.startswith("✅"):
                return f"✅ {text}"
        return text

    def make_button_inactive(self, callback_data: str, is_pressed: bool) -> Optional[str]:
        """Make button inactive by changing callback_data to 'disabled'"""
        if is_pressed and not self.is_rating_button(callback_data):
            return "disabled"
        return callback_data

    async def update_message_buttons(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        message_id: int,
        original_keyboard: dict,
    ) -> bool:
        """Update message buttons to show pressed state"""
        try:
            # Get the original keyboard structure
            if not original_keyboard.get("inline_keyboard"):
                return False

            # Check if any button was pressed for this message
            any_button_pressed = False
            if msg.user_id in self.pressed_buttons and message_id in self.pressed_buttons[msg.user_id]:
                any_button_pressed = len(self.pressed_buttons[msg.user_id][message_id]) > 0

            # Create updated keyboard
            updated_keyboard = {"inline_keyboard": []}

            for row in original_keyboard["inline_keyboard"]:
                updated_row = []
                for button in row:
                    callback_data = button.get("callback_data", "")
                    text = button.get("text", "")

                    # Check if this specific button was pressed
                    is_pressed = self.is_button_pressed(msg.user_id, message_id, callback_data)

                    # If any button was pressed, deactivate all buttons except
                    # the pressed one
                    if any_button_pressed and not is_pressed:
                        # Deactivate this button
                        updated_text = text  # Keep original text
                        updated_callback_data = "disabled"
                    else:
                        # Update button text and callback_data normally
                        updated_text = self.update_button_text(text, callback_data, is_pressed)
                        updated_callback_data = self.make_button_inactive(callback_data, is_pressed)

                    updated_button = {
                        "text": updated_text,
                        "callback_data": updated_callback_data,
                    }

                    # Preserve URL buttons
                    if button.get("url"):
                        updated_button["url"] = button["url"]

                    updated_row.append(updated_button)

                updated_keyboard["inline_keyboard"].append(updated_row)

            # Get original message text from callback query
            callback_query = msg.payload.get("callback_query", {})
            original_message = callback_query.get("message", {})
            original_text = original_message.get("text", "")

            # Edit the message with updated keyboard
            success = await adapter.edit_message(
                msg.user_id,
                message_id,
                original_text,  # Keep original text
                inline_keyboard=updated_keyboard,
            )

            return success

        except Exception as e:
            logger.error(f"Error updating message buttons: {e}")
            return False

    async def update_message_with_selection_text(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        message_id: int,
        original_keyboard: dict,
    ) -> bool:
        """Update message to show selection text and remove buttons"""
        try:
            # Get the original keyboard structure
            if not original_keyboard.get("inline_keyboard"):
                return False

            # Check if any button was pressed for this message
            any_button_pressed = False
            pressed_callback_data = None
            if msg.user_id in self.pressed_buttons and message_id in self.pressed_buttons[msg.user_id]:
                pressed_buttons = self.pressed_buttons[msg.user_id][message_id]
                any_button_pressed = len(pressed_buttons) > 0
                if any_button_pressed:
                    # Get first pressed button
                    pressed_callback_data = list(pressed_buttons)[0]

            if not any_button_pressed:
                return False

            # Check if this is a navigation action that should delete the message
            # instead of editing it (e.g., menu navigation, QR generation, etc.)
            is_navigation_action = any(pattern in pressed_callback_data for pattern in NAVIGATION_ACTION_PATTERNS)
            if is_navigation_action:
                # Delete the message for QR generation action
                success = await adapter.delete_message(msg.user_id, message_id)
            else:
                # Generate selection text based on pressed button
                selection_text = await self._generate_selection_text(pressed_callback_data)

                # Use only selection text as the new message
                new_text = selection_text

                # Edit the message with new text and no keyboard
                success = await adapter.edit_message(
                    msg.user_id,
                    message_id,
                    new_text,
                    inline_keyboard=None,  # Remove all buttons
                )

            return success

        except Exception as e:
            logger.error(f"Error updating message with selection text: {e}")
            return False

    async def _generate_selection_text(self, callback_data: str) -> str:
        """Generate selection text based on callback data"""

        if not callback_data:
            return ""

        # Handle hotel selection
        if callback_data.startswith("HOTEL_"):
            hotel_short_name = callback_data.split("_", 1)[-1]
            hotel_name = await self.get_hotel_name_by_short_name(hotel_short_name)
            return f'Выбран отель "{hotel_name}"'

        # Handle feedback actions
        if callback_data.endswith("_LEAVE_FEEDBACK"):
            return 'Выбрано действие "Оставить отзыв"'

        # Handle help actions
        if callback_data.endswith("_ABOUT_BOT"):
            return 'Выбрано действие "О боте"'

        if callback_data.endswith("_HELP"):
            return 'Выбрано действие "Помощь"'

        # Handle consent actions
        if callback_data.endswith("_CONSENT_YES"):
            return 'Выбрано действие "Согласие на обработку данных"'

        if callback_data.endswith("_CONSENT_NO"):
            return 'Выбрано действие "Отказ от обработки данных"'

        # Handle last feedback
        if callback_data.startswith("LASTFEEDBACK_"):
            return 'Выбрано действие "Дополнить предыдущий отзыв"'

        # Handle rating buttons
        if "_RATE_" in callback_data:
            rating = callback_data.split("_RATE_")[-1]
            return f'Выбрана оценка "{rating} звезд"'

        if "_THUMB_UP" in callback_data:
            return 'Выбрана оценка "👍"'

        if "_THUMB_DOWN" in callback_data:
            return 'Выбрана оценка "👎"'

        # MGR_STATUS_ - don't show selection text, let callback handler edit message directly
        if "MGR_STATUS_" in callback_data:
            return ""

        # Handle manager actions
        if "_MGR_" in callback_data:
            if "MGR_REPORT_WEEK_" in callback_data:
                # Extract hotel short_name from callback_data like
                # "ALN_MGR_REPORT_WEEK_HOTEL"
                parts = callback_data.split("_MGR_REPORT_WEEK_")
                if len(parts) == 2:
                    hotel_short_name = parts[0]
                    hotel_name = await self.get_hotel_name_by_short_name(hotel_short_name)
                    return f'Выбран период "неделя" для отчета по отелю "{hotel_name}"'
            elif "MGR_REPORT_MONTH_" in callback_data:
                # Extract hotel short_name from callback_data like
                # "ALN_MGR_REPORT_MONTH_HOTEL"
                parts = callback_data.split("_MGR_REPORT_MONTH_")
                if len(parts) == 2:
                    hotel_short_name = parts[0]
                    hotel_name = await self.get_hotel_name_by_short_name(hotel_short_name)
                    return f'Выбран период "месяц" для отчета по отелю "{hotel_name}"'
            elif "MGR_REPORT_HALF-YEAR_" in callback_data:
                # Extract hotel short_name from callback_data like
                # "ALN_MGR_REPORT_HALF-YEAR_HOTEL"
                parts = callback_data.split("_MGR_REPORT_HALF-YEAR_")
                if len(parts) == 2:
                    hotel_short_name = parts[0]
                    hotel_name = await self.get_hotel_name_by_short_name(hotel_short_name)
                    return f'Выбран период "полгода" для отчета по отелю "{hotel_name}"'
            elif "MGR_REPORT_YEAR_" in callback_data:
                # Extract hotel short_name from callback_data like
                # "ALN_MGR_REPORT_YEAR_HOTEL"
                parts = callback_data.split("_MGR_REPORT_YEAR_")
                if len(parts) == 2:
                    hotel_short_name = parts[0]
                    hotel_name = await self.get_hotel_name_by_short_name(hotel_short_name)
                    return f'Выбран период "год" для отчета по отелю "{hotel_name}"'

            elif "MGR_EDIT_PROMPT_" in callback_data:
                # Extract hotel_code and zone_code from callback_data like
                # "ALN_MGR_EDIT_PROMPT_AMG"
                parts = callback_data.split("_MGR_EDIT_PROMPT_")
                if len(parts) == 2:
                    hotel_code = parts[0]
                    zone_code = parts[1]
                    hotel_name = await self.get_hotel_name_by_short_name(hotel_code)
                    zone_name = await self.get_zone_name_by_codes(hotel_code, zone_code)
                    return f'Выбрано действие "редактирование инструкции" для отеля "{hotel_name}" и зоны "{zone_name}"'

        # Default fallback
        return f'Выбрано действие "{callback_data}"'

    def _get_zone_name_from_keyboard(self, zone_code: str, original_keyboard: dict) -> str:
        """Get zone name from original keyboard by zone code"""
        try:
            for row in original_keyboard.get("inline_keyboard", []):
                for button in row:
                    callback_data = button.get("callback_data", "")
                    if callback_data.endswith(f"_SPECIAL_ZONE_{zone_code}"):
                        return button.get("text", zone_code)
        except Exception:
            pass
        return zone_code

    async def handle_button_click(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        message_id: int,
        callback_data: str,
        original_keyboard: dict,
    ) -> None:
        """Handle button click by marking it as pressed and updating message"""
        try:
            # Mark button as pressed
            self.mark_button_pressed(msg.user_id, message_id, callback_data)

            # Check if this is a rating button - use old logic for rating
            if self.is_rating_button(callback_data):
                # Update the message to show pressed state (keep buttons)
                await self.update_message_buttons(msg, adapter, message_id, original_keyboard)
            else:
                # For non-rating buttons, show selection text and remove
                await self.update_message_with_selection_text(msg, adapter, message_id, original_keyboard)

            logger.info(
                f"Button marked as pressed: user_id={msg.user_id}, "
                f"message_id={message_id}, callback_data={callback_data}"
            )

        except Exception as e:
            logger.error(f"Error handling button click: {e}")

    def clear_user_buttons(self, user_id: str) -> None:
        """Clear all pressed buttons for a user"""
        self.pressed_buttons.pop(user_id, None)

    async def get_hotel_name_by_short_name(self, short_name: str) -> str:
        """Get hotel name by short_name"""
        try:
            hotel = await self.catalog_repo.get_hotel_by_code(short_name)
            return hotel.name if hotel else short_name
        except Exception as e:
            logger.error(f"Error getting hotel name for {short_name}: {e}")
            return short_name

    async def get_zone_name_by_codes(self, hotel_code: str, zone_code: str) -> str:
        """Get zone name by hotel_code and zone_code"""
        try:
            # First get hotel by short_name
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)

            if not hotel:
                return zone_code

            # Then get zone by hotel_id and zone short_name
            zone_result = await self.session.execute(
                select(Zone).where(
                    Zone.hotel_id == hotel.id,
                    Zone.short_name == zone_code,
                ),
            )
            zone = zone_result.scalars().first()
            return zone.name if zone else zone_code
        except Exception as e:
            logger.error(f"Error getting zone name for {hotel_code}/{zone_code}: {e}")
            return zone_code

    async def update_feedback_message_if_needed(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        message_id: int,
        hotel_code: str,
    ) -> bool:
        """Update feedback message buttons if any buttons were pressed"""
        try:
            callback_data = f"{hotel_code}_MENU"
            is_pressed = self.is_button_pressed(msg.user_id, message_id, callback_data)

            if not is_pressed:
                return False  # No need to update

            # Create original keyboard structure for update_message_buttons
            original_keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": USER_FEEDBACK_COMPLETION_BUTTON,
                            "callback_data": callback_data,
                        }
                    ],
                ]
            }

            # Use the same logic as update_message_buttons
            return await self.update_message_buttons(msg, adapter, message_id, original_keyboard)

        except Exception as e:
            logger.error(f"Error updating feedback message: {e}")
            return False
