import html
import json
import asyncio
import re
import httpx
import structlog

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.config.messages import (
    ADMIN_MENU_BRANCH_MANAGEMENT_BUTTON,
    ADMIN_MENU_USER_MANAGEMENT_BUTTON,
    ADMIN_USER_MANAGEMENT_LIST_USERS_BUTTON,
    ADMIN_USER_MANAGEMENT_EDIT_USER_BUTTON,
    ADMIN_USER_MANAGEMENT_ADD_USER_BUTTON,
    ADMIN_BRANCH_MANAGEMENT_SELECT_BRANCH_BUTTON,
    ADMIN_BRANCH_MANAGEMENT_ADD_BRANCH_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_SELECT_ZONE_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_NAME_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_DESCRIPTION_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_BACK_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_ADD_ZONE_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_NAME_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_DESCRIPTION_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_ADULT_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_DELETE_ZONE_BUTTON,
    ADMIN_USER_MANAGEMENT_PHONE_NUMBER_BUTTON,
    BACK_BUTTON,
    CONSENT_APPROVE_MESSAGE,
    CONSENT_REJECT_MESSAGE,
    LOAD_REPORT_FOR_ALL_HOTELS_BUTTON,
    MAIN_MENU_BUTTON,
    MAIN_MENU_USER_ABOUT_BOT_BUTTON,
    MAIN_MENU_USER_ADD_TO_PREVIOUS_FEEDBACK_BUTTON,
    MAIN_MENU_USER_HELP_BUTTON,
    MAIN_MENU_USER_LEAVE_FEEDBACK_BUTTON,
    MANAGER_MENU_FEEDBACKS_BUTTON,
    MANAGER_MENU_LIST_FEEDBACKS_BUTTON,
    MANAGER_MENU_NEGATIVE_FEEDBACKS_BUTTON,
    MANAGER_MENU_PROMPTS_BUTTON,
    MANAGER_MENU_QR_BUTTON,
    MANAGER_MENU_REPORT_BUTTON,
    REPORT_PERIOD_CUSTOM_BUTTON,
    REPORT_PERIOD_HALF_YEAR_BUTTON,
    REPORT_PERIOD_MONTH_BUTTON,
    REPORT_PERIOD_WEEK_BUTTON,
    REPORT_PERIOD_YEAR_BUTTON,
    SHARE_PHONE_NUMBER_BUTTON,
    USER_FEEDBACK_ADDITION_COMPLETION_BUTTON,
    USER_FEEDBACK_COMPLETION_BUTTON,
)
from app.config.settings import get_settings
from shared_models import FeedbackStatus, Hotel, Role
from shared_models.constants import RoleEnum

logger = structlog.get_logger(__name__)


class MaxAdapter(ChannelAdapter):
    """Adapter for MAX messenger API."""

    channel_name = "max"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.bot_token = self.settings.MAX_BOT_TOKEN
        self.api_url = self.settings.MAX_API_URL

    @staticmethod
    def _strip_html_tags(text: str | None) -> str | None:
        """
        Remove HTML tags from text while preserving link targets.
        MAX messenger doesn't support HTML formatting, so we convert
        <a href="...">text</a> into "text (url)" and strip the rest.

        Args:
            text: Text that may contain HTML tags

        Returns:
            Plain text with links preserved, or None if input was None
        """
        if text is None:
            return None

        def _replace_link(match: re.Match) -> str:
            href = match.group(1)
            inner = (match.group(2) or "").strip() or href
            return f"{inner} ({href})"

        text = re.sub(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            _replace_link,
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        text = re.sub(r"<[^>]+>", "", text)
        # Decode HTML entities (e.g., &nbsp; -> space, &amp; -> &)
        text = html.unescape(text)
        return text

    def parse_payload(self, payload: bytes) -> dict | None:
        """Parse raw payload bytes into dictionary."""
        try:
            data = json.loads(payload.decode("utf-8"))
            return data
        except Exception as e:
            logger.error("max.parse_payload.error", error=str(e))
            return None

    async def parse_webhook(self, payload: bytes, headers: dict[str, str]) -> IncomingMessage | None:
        """
        Parse incoming webhook from MAX messenger.
        """
        data = self.parse_payload(payload)
        if not data:
            logger.warning("max.parse_webhook.invalid_payload")
            return None

        update_type = data.get("update_type")

        logger.info(
            "max.webhook.update_type_detected",
            update_type=update_type,
            has_message=bool(data.get("message")),
            has_callback=bool(data.get("callback")),
        )

        # Handle bot_started (deep link /start command)
        if update_type == "bot_started":
            return await self._parse_bot_started(data)

        # Handle regular messages
        if update_type == "message_created":
            return await self._parse_message(data)

        # Handle callback queries (button presses)
        elif update_type == "message_callback" or "callback" in data:
            logger.info("max.webhook.processing_callback", callback_data=data.get("callback"))
            return await self._parse_callback(data)

        else:
            logger.warning(
                "max.webhook.unknown_update_type",
                update_type=update_type,
                data_keys=list(data.keys()),
            )
            return None

    async def _parse_message(self, data: dict) -> IncomingMessage | None:
        """Parse regular message from MAX webhook."""
        message = data.get("message", {})
        sender = message.get("sender", {})
        body = message.get("body", {})

        user_id = str(sender.get("user_id", ""))
        if not user_id:
            logger.warning("max.parse_message.no_user_id")
            return None

        text = body.get("text") or None

        # Store original data in payload for potential future use
        payload_data = {
            "_parsed": {
                "mid": body.get("mid"),
                "seq": body.get("seq"),
                "timestamp": message.get("timestamp"),
                "user_locale": data.get("user_locale"),
            }
        }

        # Handle media attachments and contact if present
        media_token = None
        media_kind = None
        contact_phone = None
        attachments = body.get("attachments", [])

        if attachments:
            for attachment in attachments:
                attachment_type = attachment.get("type")
                attachment_payload = attachment.get("payload", {})

                if attachment_type == "contact":
                    # Handle contact sharing (phone number)
                    vcf_info = attachment_payload.get("vcf_info", "")
                    max_info = attachment_payload.get("max_info", {})

                    # Extract phone from vCard format
                    # Format: "TEL;TYPE=cell:79807029138"
                    import re

                    phone_match = re.search(r"TEL[^:]*:(\+?\d+)", vcf_info)
                    if phone_match:
                        contact_phone = phone_match.group(1)
                        # Remove + if present
                        contact_phone = contact_phone.replace("+", "")
                        logger.info(
                            "max.parse_message.contact_extracted",
                            phone=contact_phone,
                            vcf_info=vcf_info[:100],
                        )

                    payload_data["_parsed"]["contact_info"] = max_info

                elif attachment_type == "image":
                    media_kind = "image"
                    media_token = attachment_payload.get("file_id") or attachment_payload.get("url")
                elif attachment_type == "audio" or attachment_type == "voice":
                    media_kind = "audio"
                    media_token = attachment_payload.get("file_id") or attachment_payload.get("url")
                elif attachment_type == "video":
                    media_kind = "video"
                    media_token = attachment_payload.get("file_id") or attachment_payload.get("url")
                elif attachment_type == "file":
                    media_kind = "document"
                    media_token = attachment_payload.get("file_id") or attachment_payload.get("url")

            payload_data["_parsed"]["attachments_count"] = len(attachments)

        return IncomingMessage(
            channel="max",
            user_id=user_id,
            text=text,
            media_token=media_token,
            media_kind=media_kind,
            payload=payload_data,
            callback_id=None,
            contact_phone=contact_phone,
        )

    async def _parse_bot_started(self, data: dict) -> IncomingMessage | None:
        """Parse bot_started event (deep link /start command) from MAX webhook."""
        user = data.get("user", {})
        user_id = str(user.get("user_id", ""))
        if not user_id:
            logger.warning("max.parse_bot_started.no_user_id")
            return None

        # Get payload from bot_started event (deep link parameters)
        payload_str = data.get("payload", "")

        # Store original data in payload for potential future use
        payload_data = {
            "_parsed": {
                "timestamp": data.get("timestamp"),
                "user_locale": data.get("user_locale"),
                "start_payload": payload_str,  # Store deep link payload
            }
        }

        # Parse payload to extract hotel and zone if present
        # Format: "hotel=ALN=zone=AND"
        hotel_code = None
        zone_code = None
        if payload_str:
            # Parse format: hotel=CODE=zone=CODE
            parts = payload_str.split("=")
            for i, part in enumerate(parts):
                if part == "hotel" and i + 1 < len(parts):
                    hotel_code = parts[i + 1]
                elif part == "zone" and i + 1 < len(parts):
                    zone_code = parts[i + 1]

        if hotel_code:
            payload_data["_parsed"]["hotel_code"] = hotel_code
        if zone_code:
            payload_data["_parsed"]["zone_code"] = zone_code

        # Create context similar to Telegram /start command
        if payload_str:
            payload_data.setdefault("_context", {})
            if hotel_code:
                payload_data["_context"]["hotel"] = hotel_code
            if zone_code:
                payload_data["_context"]["zone"] = zone_code

        # Set text to /start to trigger start command handler
        text = "/start"

        logger.info(
            "max.parse_bot_started",
            user_id=user_id,
            payload=payload_str,
            hotel_code=hotel_code,
            zone_code=zone_code,
        )

        return IncomingMessage(
            channel="max",
            user_id=user_id,
            text=text,
            payload=payload_data,
            callback_id=None,
        )

    async def _parse_callback(self, data: dict) -> IncomingMessage | None:
        """Parse callback query (button press) from MAX webhook."""
        callback = data.get("callback", {})
        message = data.get("message", {})
        user = callback.get("user", {})

        user_id = str(user.get("user_id", ""))
        if not user_id:
            logger.warning("max.parse_callback.no_user_id")
            return None

        callback_id = callback.get("callback_id", "")
        callback_data = callback.get("payload", "")

        # Extract message_id and reply_markup from the message
        message_body = message.get("body", {})
        message_id = message_body.get("mid")
        message_attachments = message_body.get("attachments", [])

        # Extract inline_keyboard from attachments and convert to Telegram format
        inline_keyboard = {}
        for attachment in message_attachments:
            if attachment.get("type") == "inline_keyboard":
                max_keyboard = attachment.get("payload", {})
                max_buttons = max_keyboard.get("buttons", [])

                telegram_buttons = []
                for row in max_buttons:
                    telegram_row = []
                    for btn in row:
                        if isinstance(btn, dict):
                            converted_btn = {"text": btn.get("text", "")}
                            if btn.get("payload"):
                                converted_btn["callback_data"] = btn["payload"]
                            elif btn.get("url"):
                                converted_btn["url"] = btn["url"]
                            telegram_row.append(converted_btn)
                    telegram_buttons.append(telegram_row)

                inline_keyboard = {"inline_keyboard": telegram_buttons}

        # Parse hotel_code and zone_code from callback data
        # Format: "ALN_AMG_RATE_4" or "ALN_AMG_THUMB_UP"
        hotel_code = None
        zone_code = None
        rating = None

        if "_RATE_" in callback_data:
            try:
                parts = callback_data.split("_RATE_")
                if len(parts) == 2:
                    hotel_zone = parts[0]
                    rating_str = parts[1]
                    # Split hotel and zone
                    hotel_zone_parts = hotel_zone.split("_")
                    if len(hotel_zone_parts) >= 2:
                        hotel_code = hotel_zone_parts[0]
                        zone_code = hotel_zone_parts[1]
                        rating = int(rating_str)
            except (ValueError, IndexError):
                pass
        elif "_THUMB_" in callback_data:
            try:
                # Format: "ALN_AMG_THUMB_UP"
                parts = callback_data.split("_THUMB_")
                if len(parts) == 2:
                    hotel_zone = parts[0]
                    hotel_zone_parts = hotel_zone.split("_")
                    if len(hotel_zone_parts) >= 2:
                        hotel_code = hotel_zone_parts[0]
                        zone_code = hotel_zone_parts[1]
            except (ValueError, IndexError):
                pass

        payload_data = {
            "_parsed": {
                "callback_id": callback_id,
                "timestamp": data.get("timestamp"),
                "user_locale": data.get("user_locale"),
            },
            # Add callback_query for compatibility with CallbackService
            "callback_query": {
                "data": callback_data,
                "id": callback_id,
                # Add message info for button state handling
                "message": {
                    "message_id": message_id,
                    "reply_markup": inline_keyboard if inline_keyboard else {},
                },
            },
        }

        # Add hotel_code and zone_code if parsed
        if hotel_code:
            payload_data["_parsed"]["hotel_code"] = hotel_code
        if zone_code:
            payload_data["_parsed"]["zone_code"] = zone_code

        return IncomingMessage(
            channel="max",
            user_id=user_id,
            # Callback data is treated as text (like in Telegram)
            text=callback_data,
            callback_id=callback_id,
            payload=payload_data,
            rating=rating,
        )

    async def send_message(
        self,
        user_id: str,
        text: str,
        buttons: list[list[str]] | None = None,
        inline_keyboard: dict | None = None,
        reply_markup: dict | None = None,
    ) -> str | int | None:
        """
        Send message to user via MAX messenger.

        Args:
            user_id: MAX user ID
            text: Message text
            buttons: Optional keyboard buttons (simple list of rows)
            inline_keyboard: Optional inline keyboard (dict format)
            reply_markup: Optional reply keyboard (dict format)

        Returns:
            Message ID if successful, None otherwise
        """
        if not self.bot_token:
            logger.error("max.send_message.no_token")
            return None

        # MAX API format:
        # https://platform-api.max.ru/messages?user_id={user_id}
        url = f"{self.api_url}?user_id={user_id}"

        text = self._strip_html_tags(text)

        # Build message payload in MAX format
        # Based on official API documentation
        payload: dict = {"text": text}

        # Add keyboard if provided
        # Priority: inline_keyboard > reply_markup > buttons
        keyboard_buttons = None

        if inline_keyboard and inline_keyboard.get("inline_keyboard"):
            # Convert inline_keyboard buttons to MAX format
            keyboard_buttons = []
            for row in inline_keyboard["inline_keyboard"]:
                button_row = []
                for btn in row:
                    if isinstance(btn, dict):
                        # Check if already in MAX format
                        if "type" in btn and "payload" in btn:
                            button_row.append(btn)
                        # Convert Telegram format (callback_data) to MAX
                        elif "callback_data" in btn:
                            button_row.append(
                                {
                                    "type": "callback",
                                    "text": btn.get("text", ""),
                                    "payload": btn["callback_data"],
                                }
                            )
                        elif "url" in btn:
                            button_row.append(
                                {
                                    "type": "link",
                                    "text": btn.get("text", ""),
                                    "url": btn["url"],
                                }
                            )
                        else:
                            # Fallback: use text as payload
                            btn_text = btn.get("text", "")
                            button_row.append(
                                {
                                    "type": "callback",
                                    "text": btn_text,
                                    "payload": btn_text,
                                }
                            )
                if button_row:
                    keyboard_buttons.append(button_row)
        elif reply_markup and reply_markup.get("inline_keyboard"):
            # Convert reply_markup inline_keyboard to MAX format
            keyboard_buttons = []
            for row in reply_markup["inline_keyboard"]:
                button_row = []
                for btn in row:
                    if isinstance(btn, dict):
                        # Check if already in MAX format
                        if "type" in btn and "payload" in btn:
                            button_row.append(btn)
                        # Handle request_contact button
                        elif "type" in btn and btn.get("type") == "request_contact":
                            button_row.append({"type": "request_contact", "text": btn.get("text", "")})
                        # Convert Telegram format (callback_data) to MAX
                        elif "callback_data" in btn:
                            button_row.append(
                                {
                                    "type": "callback",
                                    "text": btn.get("text", ""),
                                    "payload": btn["callback_data"],
                                }
                            )
                        elif "url" in btn:
                            button_row.append(
                                {
                                    "type": "link",
                                    "text": btn.get("text", ""),
                                    "url": btn["url"],
                                }
                            )
                        else:
                            btn_text = btn.get("text", "")
                            button_row.append(
                                {
                                    "type": "callback",
                                    "text": btn_text,
                                    "payload": btn_text,
                                }
                            )
                if button_row:
                    keyboard_buttons.append(button_row)
        elif reply_markup and reply_markup.get("keyboard"):
            # Convert simple keyboard to inline keyboard format
            keyboard_buttons = []
            for row in reply_markup["keyboard"]:
                button_row = []
                for btn in row:
                    if isinstance(btn, dict):
                        # Check if it's already in MAX format
                        if "type" in btn and "payload" in btn:
                            button_row.append(btn)
                        elif "request_contact" in btn:
                            button_row.append({"type": "request_contact", "text": btn.get("text", "")})
                        else:
                            btn_text = btn.get("text", btn)
                            button_row.append(
                                {
                                    "type": "callback",
                                    "text": btn_text,
                                    "payload": btn_text,
                                }
                            )
                    else:
                        btn_text = btn
                        button_row.append({"type": "callback", "text": btn_text, "payload": btn_text})
                keyboard_buttons.append(button_row)
        elif buttons:
            # Convert simple button matrix to MAX inline keyboard format
            keyboard_buttons = []
            for row in buttons:
                button_row = []
                for button_text in row:
                    button_row.append(
                        {
                            "type": "callback",
                            "text": button_text,
                            "payload": button_text,
                        }
                    )
                keyboard_buttons.append(button_row)

        # Add inline keyboard as attachment if present
        if keyboard_buttons:
            payload["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": keyboard_buttons}}]

        try:
            # Add authorization header with access token
            headers = {
                "Authorization": self.bot_token,
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient() as client:
                logger.info(
                    "max.send_message.request",
                    url=url,
                    payload=payload,
                    headers_preview={
                        "Authorization": f"{self.bot_token[:10]}...",
                        "Content-Type": headers["Content-Type"],
                    },
                )
                response = await client.post(url, json=payload, headers=headers, timeout=30.0)

                # Log response details before raising error
                logger.info(
                    "max.send_message.response",
                    status_code=response.status_code,
                    response_text=response.text[:500],
                )

                response.raise_for_status()
                result = response.json()
                logger.info("max.send_message.success", user_id=user_id, response=result)
                # Extract message_id (mid) from MAX API response
                # Response structure: {"message": {"body": {"mid": "..."}}}
                message = result.get("message", {})
                body = message.get("body", {})
                mid = body.get("mid")
                return mid
        except httpx.HTTPStatusError as e:
            logger.error(
                "max.send_message.http_error",
                error=str(e),
                status_code=e.response.status_code,
                response_text=e.response.text[:500],
                user_id=user_id,
            )
            return None
        except httpx.HTTPError as e:
            logger.error("max.send_message.http_error", error=str(e), user_id=user_id)
            return None
        except Exception as e:
            logger.error("max.send_message.error", error=str(e), user_id=user_id)
            return None

    async def delete_message(self, chat_id: str, message_id: str | int) -> bool:
        """
        Delete a message in MAX.
        MAX API uses DELETE /messages?message_id={mid}
        """
        try:
            if not self.bot_token:
                logger.error("max.delete_message.no_token")
                return False

            # MAX API: DELETE /messages?message_id={mid}
            url = f"{self.api_url}?message_id={message_id}"
            headers = {
                "Authorization": self.bot_token,
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient() as client:
                response = await client.delete(url, headers=headers, timeout=30.0)
                response.raise_for_status()
                logger.info("max.delete_message.success", message_id=message_id)
                return True

        except httpx.HTTPError as e:
            logger.error("max.delete_message.http_error", error=str(e), message_id=message_id)
            return False
        except Exception as e:
            logger.error("max.delete_message.error", error=str(e), message_id=message_id)
            return False

    async def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        """
        Answer callback query (acknowledge button press).
        """
        try:
            url = f"https://platform-api.max.ru/answers?callback_id={callback_query_id}"
            headers = {
                "Authorization": self.bot_token,
                "Content-Type": "application/json",
            }

            data = {}
            if text:
                # Strip HTML tags from text (MAX doesn't support HTML)
                text = self._strip_html_tags(text)
                data["notification"] = text

            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=data)
                response.raise_for_status()
                logger.info("max.answer_callback.success", callback_id=callback_query_id)
                return True
        except httpx.HTTPError as e:
            logger.error(
                "max.answer_callback.http_error",
                error=str(e),
                callback_id=callback_query_id,
            )
            return False
        except Exception as e:
            logger.error("max.answer_callback.error", error=str(e), callback_id=callback_query_id)
            return False

    async def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str | None = None,
        inline_keyboard: dict | None = None,
    ) -> bool:
        """
        Edit an existing message with new text and keyboard.

        Similar to Telegram's editMessageText.
        If text is None or empty, only keyboard will be updated.
        """
        try:
            # MAX API: PUT /messages
            if not self.bot_token:
                logger.error("max.edit_message.no_token")
                return False

            # MAX API: PUT /messages?message_id={mid}
            # Use message_id as query parameter, not path parameter
            url = f"{self.api_url}?message_id={message_id}"

            payload: dict = {}

            if text:
                # Strip HTML tags from text (MAX doesn't support HTML)
                text = self._strip_html_tags(text)
                payload["text"] = text

            # Handle keyboard attachments
            if inline_keyboard is None:
                # Explicitly remove all attachments when inline_keyboard=None
                payload["attachments"] = []
            elif (
                inline_keyboard
                and inline_keyboard.get("inline_keyboard")
                and len(inline_keyboard.get("inline_keyboard", [])) > 0
            ):
                # Convert inline_keyboard buttons to MAX format
                keyboard_buttons = []
                for row in inline_keyboard["inline_keyboard"]:
                    button_row = []
                    for btn in row:
                        if isinstance(btn, dict):
                            # Check if already in MAX format
                            if "type" in btn and "payload" in btn:
                                button_row.append(btn)
                            # Convert Telegram format to MAX
                            elif "callback_data" in btn:
                                button_row.append(
                                    {
                                        "type": "callback",
                                        "text": btn.get("text", ""),
                                        "payload": btn["callback_data"],
                                    }
                                )
                            elif "url" in btn:
                                button_row.append(
                                    {
                                        "type": "link",
                                        "text": btn.get("text", ""),
                                        "url": btn["url"],
                                    }
                                )
                            else:
                                btn_text = btn.get("text", "")
                                button_row.append(
                                    {
                                        "type": "callback",
                                        "text": btn_text,
                                        "payload": btn_text,
                                    }
                                )
                    if button_row:
                        keyboard_buttons.append(button_row)

                if keyboard_buttons:
                    payload["attachments"] = [
                        {
                            "type": "inline_keyboard",
                            "payload": {"buttons": keyboard_buttons},
                        }
                    ]
                else:
                    payload["attachments"] = []
            else:
                payload["attachments"] = []

            headers = {
                "Authorization": self.bot_token,
                "Content-Type": "application/json",
            }

            logger.info("max.edit_message.request", url=url, payload=payload)

            async with httpx.AsyncClient() as client:
                response = await client.put(url, json=payload, headers=headers, timeout=30.0)

                logger.info(
                    "max.edit_message.response",
                    status_code=response.status_code,
                    response_text=response.text[:500],
                )

                response.raise_for_status()
                logger.info("max.edit_message.success", message_id=message_id)
                return True

        except httpx.HTTPError as e:
            logger.error("max.edit_message.http_error", error=str(e), message_id=message_id)
            return False
        except Exception as e:
            logger.error("max.edit_message.error", error=str(e), message_id=message_id)
            return False

    async def edit_message_reply_markup(
        self,
        chat_id: str,
        message_id: int,
        inline_keyboard: dict | None = None,
    ) -> bool:
        """
        Edit only the reply markup (keyboard) of an existing message.

        For MAX, we'll use the same edit_message method.
        """
        return await self.edit_message(chat_id, message_id, None, inline_keyboard)

    @staticmethod
    def main_menu_keyboard(hotel_code: str, last_feedback_id: str = None) -> dict:
        """Create main menu keyboard for MAX."""
        keyboard = [
            [
                {
                    "type": "callback",
                    "text": MAIN_MENU_USER_LEAVE_FEEDBACK_BUTTON,
                    "payload": f"{hotel_code}_LEAVE_FEEDBACK",
                }
            ],
        ]

        if last_feedback_id:
            keyboard.append(
                [
                    {
                        "type": "callback",
                        "text": MAIN_MENU_USER_ADD_TO_PREVIOUS_FEEDBACK_BUTTON,
                        "payload": f"LASTFEEDBACK_{last_feedback_id}",
                    }
                ]
            )

        # Add help and info buttons
        keyboard.extend(
            [
                [
                    {
                        "type": "callback",
                        "text": MAIN_MENU_USER_ABOUT_BOT_BUTTON,
                        "payload": f"{hotel_code}_ABOUT_BOT",
                    }
                ],
                [
                    {
                        "type": "callback",
                        "text": MAIN_MENU_USER_HELP_BUTTON,
                        "payload": f"{hotel_code}_HELP",
                    }
                ],
            ]
        )

        return {"inline_keyboard": keyboard}

    @staticmethod
    def create_phone_keyboard() -> dict:
        """Create phone sharing keyboard for MAX."""
        # MAX uses inline keyboard with request_contact type button
        return {"inline_keyboard": [[{"type": "request_contact", "text": SHARE_PHONE_NUMBER_BUTTON}]]}

    @staticmethod
    def create_hotels_selection_keyboard(hotels: list[Hotel]) -> dict:
        """Create hotels selection keyboard for MAX."""
        return {
            "inline_keyboard": [
                [
                    {
                        "type": "callback",
                        "text": hotel.name,
                        "payload": f"HOTEL_{hotel.short_name}",
                    }
                    for hotel in hotels
                ]
            ]
        }

    @staticmethod
    def create_consent_keyboard(hotel_code: str) -> dict:
        """Create consent keyboard for MAX."""
        privacy_url = (
            "https://aleancollection.ru/upload/realweb.api.config/5b5/"
            "jz6q157gpojdu7rqw0fs4hkpf15j7l79/%D0%9F%D0%BE%D0%BB%D0%B8%D1%82%D0%B8%D0%BA%D0%B0%20"
            "%D0%BE%D0%B1%D1%80%D0%B0%D0%B1%D0%BE%D1%82%D0%BA%D0%B8%20%D0%BF%D0%B5%D1%80%D1%81%D0%BE%D0%BD%D0%B0%D0%BB%D1%8C%D0%BD%D1%8B%D1%85%20"
            "%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D1%85%20%D0%B2%20%D0%9E%D0%9E%D0%9E%20_%D0%90%D1%80%D0%B1%D0%B0%D1%82%20%D0%9E%D1%82%D0%B5%D0%BB%D1%8C%20"
            "%D0%9C%D0%B5%D0%BD%D0%B5%D0%B4%D0%B6%D0%BC%D0%B5%D0%BD%D1%82.pdf"
        )
        return {
            "inline_keyboard": [
                [
                    {
                        "type": "link",
                        "text": "Политика обработки данных",
                        "url": privacy_url,
                    }
                ],
                [
                    {
                        "type": "callback",
                        "text": CONSENT_APPROVE_MESSAGE,
                        "payload": f"{hotel_code}_CONSENT_YES",
                    },
                    {
                        "type": "callback",
                        "text": CONSENT_REJECT_MESSAGE,
                        "payload": f"{hotel_code}_CONSENT_NO",
                    },
                ]
            ]
        }

    @staticmethod
    def rating_keyboard(hotel_short_name: str, zone_short_name: str, current_rating: int = None) -> dict:
        """
        Create rating keyboard with stars 1-5 for MAX.
        """
        buttons = [
            [
                {
                    "type": "callback",
                    "text": (f"✅⭐️{i}" if current_rating == i else f"⭐️{i}"),
                    "payload": (f"{hotel_short_name}_{zone_short_name}_RATE_{i}"),
                }
                for i in range(1, 6)
            ],
        ]
        return {"inline_keyboard": buttons}

    @staticmethod
    def thumbs_keyboard(hotel_short_name: str, zone_short_name: str, current_rating: int = None) -> dict:
        """
        Create thumbs up/down keyboard for MAX.
        """
        buttons = [
            [
                {
                    "type": "callback",
                    "text": "✅👍" if current_rating == 5 else "👍",
                    "payload": (f"{hotel_short_name}_{zone_short_name}_THUMB_UP"),
                },
                {
                    "type": "callback",
                    "text": "✅👎" if current_rating == 1 else "👎",
                    "payload": (f"{hotel_short_name}_{zone_short_name}_THUMB_DOWN"),
                },
            ],
        ]
        return {"inline_keyboard": buttons}

    @staticmethod
    def compose_feedback_keyboard(hotel_code: str) -> dict:
        """
        Create feedback composition keyboard for MAX.
        Button to finish feedback and return to main menu.
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "type": "callback",
                        "text": USER_FEEDBACK_COMPLETION_BUTTON,
                        "payload": f"{hotel_code}_MENU",
                    }
                ],
            ]
        }

    @staticmethod
    def compose_feedback_addition_keyboard(hotel_code: str) -> dict:
        """
        Create feedback addition keyboard for MAX.
        Button to finish adding to feedback and return to main menu.
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "type": "callback",
                        "text": USER_FEEDBACK_ADDITION_COMPLETION_BUTTON,
                        "payload": f"{hotel_code}_MENU",
                    }
                ],
            ]
        }

    @staticmethod
    def manager_menu_keyboard(hotel_code: str) -> dict:
        """
        Create manager menu keyboard for MAX.
        """
        rows = [
            [
                {
                    "type": "callback",
                    "text": MANAGER_MENU_REPORT_BUTTON,
                    "payload": f"{hotel_code}_MGR_REPORTS",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": MANAGER_MENU_NEGATIVE_FEEDBACKS_BUTTON,
                    "payload": f"{hotel_code}_MGR_NEGATIVE_FEEDBACKS",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": MANAGER_MENU_QR_BUTTON,
                    "payload": f"{hotel_code}_MGR_QR",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": MANAGER_MENU_PROMPTS_BUTTON,
                    "payload": f"{hotel_code}_MGR_PROMPTS",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def manager_hotels_keyboard(hotels: list, hotel_code: str) -> dict:
        """Create keyboard for manager hotel selection for reports"""
        rows = []

        for hotel in hotels:
            hotel_name = hotel.get("name", "Неизвестный отель")
            hotel_code_item = hotel.get("code", "")
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": f"{hotel_name}",
                        "payload": f"{hotel_code}_MGR_REPORT_HOTEL_{hotel_code_item}",
                    }
                ]
            )

        if len(hotels) > 1:
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": LOAD_REPORT_FOR_ALL_HOTELS_BUTTON,
                        "payload": f"{hotel_code}_MGR_REPORT_ALL",
                    }
                ]
            )

        rows.append(
            [
                {
                    "type": "callback",
                    "text": MAIN_MENU_BUTTON,
                    "payload": f"{hotel_code}_MENU",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def report_period_keyboard(hotel_code: str, hotel_short_name: str = "") -> dict:
        """Create keyboard for report period selection (horizontal buttons)"""

        period_buttons = [
            {
                "type": "callback",
                "text": REPORT_PERIOD_WEEK_BUTTON,
                "payload": f"{hotel_code}_MGR_REPORT_WEEK_{hotel_short_name}",
            },
            {
                "type": "callback",
                "text": REPORT_PERIOD_MONTH_BUTTON,
                "payload": f"{hotel_code}_MGR_REPORT_MONTH_{hotel_short_name}",
            },
            {
                "type": "callback",
                "text": REPORT_PERIOD_HALF_YEAR_BUTTON,
                "payload": f"{hotel_code}_MGR_REPORT_HALF-YEAR_{hotel_short_name}",
            },
            {
                "type": "callback",
                "text": REPORT_PERIOD_YEAR_BUTTON,
                "payload": f"{hotel_code}_MGR_REPORT_YEAR_{hotel_short_name}",
            },
        ]

        rows = [
            period_buttons,
            [
                {
                    "type": "callback",
                    "text": REPORT_PERIOD_CUSTOM_BUTTON,
                    "payload": f"{hotel_code}_MGR_REPORT_CUSTOM_{hotel_short_name}",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": BACK_BUTTON,
                    "payload": f"{hotel_code}_MGR_REPORTS",
                }
            ],
        ]

        return {"inline_keyboard": rows}

    async def send_document_bytes(
        self,
        user_id: str,
        filename: str,
        data: bytes,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> int | None:
        """
        Send document bytes to MAX user.
        MAX API requires:
        1. Get presigned upload URL via POST /uploads?type=file
        2. Upload file to presigned URL (multipart/form-data, no Authorization)
        3. Send message with attachment payload from upload response
        """
        try:
            if not self.bot_token:
                logger.error("max.send_document.no_token")
                return None

            async with httpx.AsyncClient() as client:
                headers = {"Authorization": self.bot_token}

                # Step 1: Get presigned upload URL
                upload_url_request = "https://platform-api.max.ru/uploads?type=file"

                logger.info(
                    "max.upload_init.request",
                    url=upload_url_request,
                    filename=filename,
                    size=len(data),
                )

                upload_init_response = await client.post(upload_url_request, headers=headers, timeout=30.0)
                upload_init_response.raise_for_status()
                upload_init = upload_init_response.json()

                logger.info(
                    "max.upload_init.response",
                    status_code=upload_init_response.status_code,
                    result=upload_init,
                )

                presigned_url = upload_init.get("url")
                if not presigned_url:
                    logger.error("max.upload_init.no_url", response=upload_init)
                    await self.send_message(
                        user_id,
                        f"📄 {caption or filename}\n\n"
                        "⚠️ Не удалось получить URL для загрузки файла. "
                        "Пожалуйста, свяжитесь с поддержкой.",
                    )
                    return None

                # Step 2: Upload file to presigned URL
                # MAX API expects field name "data" in multipart/form-data
                # httpx will automatically set Content-Type with boundary
                files = {
                    "data": (
                        filename,
                        data,
                        (
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            if filename.endswith(".xlsx")
                            else "application/octet-stream"
                        ),
                    )
                }

                logger.info(
                    "max.upload_file.request",
                    presigned_url=presigned_url[:100],
                    filename=filename,
                    field_name="data",
                    file_size=len(data),
                )

                # Upload to presigned URL
                # httpx will automatically set Content-Type: multipart/form-data with boundary
                # Do NOT set any headers manually - let httpx handle multipart encoding
                upload_response = await client.post(presigned_url, files=files, timeout=60.0)
                upload_response.raise_for_status()
                upload_result = upload_response.json()

                logger.info(
                    "max.upload_file.response",
                    status_code=upload_response.status_code,
                    result=upload_result,
                )

                # Step 3: Send message with file attachment
                # Use full payload from upload_result
                message_url = f"{self.api_url}?user_id={user_id}"

                # Strip HTML tags from caption (MAX doesn't support HTML)
                clean_caption = self._strip_html_tags(caption) if caption else None

                payload = {
                    "text": clean_caption or filename,
                    "attachments": [{"type": "file", "payload": upload_result}],  # Full payload from upload
                }

                # Add reply markup if provided
                if reply_markup:
                    converted_keyboard = []
                    for row in reply_markup.get("inline_keyboard", []):
                        converted_row = []
                        for btn in row:
                            if "callback_data" in btn:
                                converted_row.append(
                                    {
                                        "type": "callback",
                                        "text": btn.get("text", ""),
                                        "payload": btn["callback_data"],
                                    }
                                )
                            elif "url" in btn:
                                converted_row.append(
                                    {
                                        "type": "link",
                                        "text": btn.get("text", ""),
                                        "url": btn["url"],
                                    }
                                )
                        if converted_row:
                            converted_keyboard.append(converted_row)

                    if converted_keyboard:
                        payload["attachments"].append(
                            {
                                "type": "inline_keyboard",
                                "payload": {"buttons": converted_keyboard},
                            }
                        )

                message_headers = {
                    "Authorization": self.bot_token,
                    "Content-Type": "application/json",
                }

                # Retry logic: wait for file to be processed
                max_retries = 3
                retry_delay = 2.0  # Start with 2 seconds
                mid = None

                for attempt in range(max_retries):
                    if attempt > 0:
                        wait_time = retry_delay * attempt
                        logger.info(
                            "max.send_document.retry",
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            wait_time=wait_time,
                        )
                        await asyncio.sleep(wait_time)

                    logger.info(
                        "max.send_document.request",
                        url=message_url,
                        payload_preview=str(payload)[:200],
                        attempt=attempt + 1,
                    )

                    try:
                        message_response = await client.post(
                            message_url,
                            headers=message_headers,
                            json=payload,
                            timeout=30.0,
                        )
                        message_response.raise_for_status()
                        message_result = message_response.json()

                        logger.info(
                            "max.send_document.response",
                            status_code=message_response.status_code,
                            result=str(message_result)[:200],
                        )

                        # Extract message ID
                        message = message_result.get("message", {})
                        body = message.get("body", {})
                        mid = body.get("mid")

                        return mid

                    except httpx.HTTPStatusError as e:
                        if e.response is not None:
                            try:
                                error_data = e.response.json()
                                error_code = error_data.get("code", "")
                                if error_code == "attachment.not.ready" and attempt < max_retries - 1:
                                    # File is not ready yet, will retry
                                    logger.warning(
                                        "max.send_document.file_not_ready",
                                        attempt=attempt + 1,
                                        error=error_data,
                                    )
                                    continue
                            except Exception:
                                logger.error(
                                    "max.send_document.error",
                                    error=str(e),
                                    exc_info=True,
                                )

                        raise
                return None

        except httpx.HTTPError as e:
            response_text = ""
            if hasattr(e, "response") and e.response is not None:
                try:
                    response_text = e.response.text[:500]
                except Exception:
                    pass

            logger.error(
                "max.send_document.http_error",
                error=str(e),
                response_text=response_text,
            )
            # Fallback: send error message
            await self.send_message(
                user_id,
                f"📄 {caption or filename}\n\n" "⚠️ Ошибка при отправке файла. Попробуйте позже.",
            )
            return None
        except Exception as e:
            logger.error("max.send_document.error", error=str(e), exc_info=True)
            return None

    async def send_media_group_bytes(
        self,
        user_id: str,
        items: list[dict],
        caption: str | None = None,
    ) -> list[int] | None:
        """
        Send multiple media items to MAX user.
        MAX doesn't support media groups like Telegram, so we send images individually.
        items: [{"kind": "image|audio|document", "filename": str, "data": bytes}]
        Returns list of message IDs.
        """
        if not items:
            return None

        try:
            if not self.bot_token:
                logger.error("max.send_media_group.no_token")
                return None

            message_ids = []
            async with httpx.AsyncClient() as client:
                headers = {"Authorization": self.bot_token}

                # Send each item individually
                for idx, item in enumerate(items[:10]):  # MAX limit similar to Telegram
                    try:
                        kind = (item.get("kind") or "image").lower()
                        filename = item.get("filename") or "file.bin"
                        data = item.get("data") or b""

                        # Determine upload type based on media kind
                        if kind == "image":
                            upload_type = "image"
                        elif kind == "audio":
                            upload_type = "audio"
                        elif kind == "video":
                            upload_type = "video"
                        else:
                            upload_type = "file"

                        # Step 1: Get presigned upload URL
                        upload_url_request = f"https://platform-api.max.ru/uploads?type={upload_type}"
                        upload_init_response = await client.post(upload_url_request, headers=headers, timeout=30.0)
                        upload_init_response.raise_for_status()
                        upload_init = upload_init_response.json()

                        presigned_url = upload_init.get("url")
                        if not presigned_url:
                            logger.error(f"max.send_media_group.no_url for {kind}")
                            continue

                        # Step 2: Upload file to presigned URL
                        files = {
                            "data": (
                                filename,
                                data,
                                ("image/png" if kind == "image" else "application/octet-stream"),
                            )
                        }

                        upload_response = await client.post(presigned_url, files=files, timeout=60.0)
                        upload_response.raise_for_status()
                        upload_result = upload_response.json()

                        # Step 3: Send message with attachment
                        message_url = f"{self.api_url}?user_id={user_id}"

                        # Use caption only for first item
                        item_caption = caption if idx == 0 else None
                        clean_caption = self._strip_html_tags(item_caption) if item_caption else None

                        payload = {
                            "text": clean_caption or filename,
                            "attachments": [{"type": upload_type, "payload": upload_result}],
                        }

                        # Retry logic for file readiness
                        max_retries = 3
                        retry_delay = 2.0
                        mid = None

                        for attempt in range(max_retries):
                            if attempt > 0:
                                await asyncio.sleep(retry_delay * attempt)

                            message_headers = {
                                "Authorization": self.bot_token,
                                "Content-Type": "application/json",
                            }
                            message_response = await client.post(
                                message_url,
                                headers=message_headers,
                                json=payload,
                                timeout=30.0,
                            )
                            message_response.raise_for_status()
                            message_result = message_response.json()

                            message = message_result.get("message", {})
                            body = message.get("body", {})
                            mid = body.get("mid")

                            if mid:
                                message_ids.append(mid)
                                break

                            # Check if file is not ready
                            try:
                                error_data = message_response.json()
                                error_code = error_data.get("code", "")
                                if error_code == "attachment.not.ready" and attempt < max_retries - 1:
                                    continue
                            except Exception:
                                pass

                    except Exception as e:
                        logger.error(f"max.send_media_group.item_error: {e}", item_index=idx)
                        continue

            return message_ids if message_ids else None

        except Exception as e:
            logger.error("max.send_media_group.error", error=str(e), exc_info=True)
            return None

    @staticmethod
    def create_status_keyboard(feedback_id: str, hotel_code: str, current_status) -> dict:
        """Create keyboard with status buttons for MAX"""
        status_options = [
            (FeedbackStatus.OPENED, "Открыто"),
            (FeedbackStatus.IN_PROGRESS, "В работе"),
            (FeedbackStatus.SOLVED, "Решено"),
            (FeedbackStatus.REJECTED, "Отклонено"),
        ]

        # Create horizontal row of status buttons
        status_row = []
        for status, label in status_options:
            if status == current_status:
                # Current status - show as disabled button
                status_row.append({"type": "callback", "text": f"✅ {label}", "payload": "disabled"})
            else:
                # Other statuses - show as clickable buttons
                status_row.append(
                    {
                        "type": "callback",
                        "text": label,
                        "payload": f"{hotel_code}_MGR_STATUS_{feedback_id}_{status.value}",
                    }
                )

        keyboard = [
            status_row,
            [
                {
                    "type": "callback",
                    "text": MANAGER_MENU_LIST_FEEDBACKS_BUTTON,
                    "payload": f"{hotel_code}_MGR_NEGATIVE_FEEDBACKS",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": MAIN_MENU_BUTTON,
                    "payload": f"{hotel_code}_MENU",
                }
            ],
        ]

        return {"inline_keyboard": keyboard}

    @staticmethod
    def zones_prompts_keyboard(zones: list, hotel_code: str) -> dict:
        """Create keyboard for zone prompts selection for MAX"""
        rows = []

        for zone in zones:
            zone_code = zone.get("code", "")
            zone_name = zone.get("name", "Неизвестная зона")
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": f"{zone_name}",
                        "payload": f"{hotel_code}_MGR_PROMPT_ZONE_{zone_code}",
                    }
                ]
            )

        rows.append(
            [
                {
                    "type": "callback",
                    "text": MAIN_MENU_BUTTON,
                    "payload": f"{hotel_code}_MENU",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def negative_feedbacks_keyboard(feedbacks: list, hotel_code: str, page: int = 1, has_next: bool = False) -> dict:
        """Create keyboard for negative feedbacks with pagination"""
        rows = []

        # Add feedback buttons (max 5 per page)
        for feedback in feedbacks:
            rating = feedback.get("rating", 0)
            zone_name = feedback.get("zone_name", "Неизвестная зона")

            button_text = f"{'⭐' * rating} {zone_name}"
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": button_text,
                        "payload": f"{hotel_code}_MGR_FEEDBACK_{feedback.get('id')}",
                    }
                ]
            )

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "⬅️",
                    "payload": f"{hotel_code}_MGR_NEGATIVE_FEEDBACKS_PAGE_{page - 1}",
                }
            )

        if has_next:
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "➡️",
                    "payload": f"{hotel_code}_MGR_NEGATIVE_FEEDBACKS_PAGE_{page + 1}",
                }
            )

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        rows.append(
            [
                {
                    "type": "callback",
                    "text": MAIN_MENU_BUTTON,
                    "payload": f"{hotel_code}_MENU",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def manager_notification_keyboard(hotel_code: str, negative_feedback_id: str) -> dict:
        """Create keyboard for manager notifications with link to most negative feedback"""
        return {
            "inline_keyboard": [
                [
                    {
                        "type": "callback",
                        "text": MANAGER_MENU_FEEDBACKS_BUTTON,
                        "payload": f"{hotel_code}_MGR_FEEDBACK_{negative_feedback_id}",
                    }
                ]
            ]
        }

    @staticmethod
    def admin_menu_keyboard() -> dict:
        """
        Create admin menu keyboard for MAX.
        """
        rows = [
            [
                {
                    "type": "callback",
                    "text": ADMIN_MENU_USER_MANAGEMENT_BUTTON,
                    "payload": "ADMIN_USER_MANAGEMENT",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_MENU_BRANCH_MANAGEMENT_BUTTON,
                    "payload": "ADMIN_BRANCH_MANAGEMENT",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_user_management_keyboard() -> dict:
        """Create keyboard for admin user management menu"""
        rows = [
            [
                {
                    "type": "callback",
                    "text": ADMIN_USER_MANAGEMENT_LIST_USERS_BUTTON,
                    "payload": "ADMIN_LIST_USERS",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_USER_MANAGEMENT_EDIT_USER_BUTTON,
                    "payload": "ADMIN_EDIT_USER",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_USER_MANAGEMENT_ADD_USER_BUTTON,
                    "payload": "ADMIN_ADD_USER",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": MAIN_MENU_BUTTON,
                    "payload": "ADMIN_MAIN_MENU",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_branch_management_keyboard() -> dict:
        """Create keyboard for admin branch management menu"""
        rows = [
            [
                {
                    "type": "callback",
                    "text": ADMIN_BRANCH_MANAGEMENT_SELECT_BRANCH_BUTTON,
                    "payload": "ADMIN_SELECT_BRANCH",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_BRANCH_MANAGEMENT_ADD_BRANCH_BUTTON,
                    "payload": "ADMIN_ADD_BRANCH",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": MAIN_MENU_BUTTON,
                    "payload": "ADMIN_MAIN_MENU",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_select_branch_keyboard(hotels: list, page: int = 1, has_next: bool = False) -> dict:
        """Create keyboard for branch selection with pagination"""
        rows = []

        # Add hotel buttons (max 10 per page)
        for hotel in hotels:
            hotel_text = f"🏨 {hotel['name']} ({hotel['code']})"
            payload = f"ADMIN_SELECTED_BRANCH_{hotel['code']}"
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": hotel_text,
                        "payload": payload,
                    }
                ]
            )

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            prev_payload = f"ADMIN_SELECT_BRANCH_PAGE_{page - 1}"
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "⬅️",
                    "payload": prev_payload,
                }
            )

        if has_next:
            next_payload = f"ADMIN_SELECT_BRANCH_PAGE_{page + 1}"
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "➡️",
                    "payload": next_payload,
                }
            )

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        rows.append(
            [
                {
                    "type": "callback",
                    "text": "🔙 Назад",
                    "payload": "ADMIN_BRANCH_MANAGEMENT",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_requests_keyboard(requests: list, page: int = 1, has_next: bool = False, status: str = "pending") -> dict:
        """Create keyboard for admin requests with pagination"""
        rows = []

        # Add request buttons (max 5 per page)
        for request in requests:
            phone = request.get("phone_number", "")
            button_text = f"{ADMIN_USER_MANAGEMENT_PHONE_NUMBER_BUTTON}{phone}"
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": button_text,
                        "payload": f"ADMIN_REQUEST_DETAIL_{request.get('id')}",
                    }
                ]
            )

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "⬅️",
                    "payload": f"ADMIN_{status.upper()}_REQUESTS_PAGE_{page - 1}",
                }
            )

        if has_next:
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "➡️",
                    "payload": f"ADMIN_{status.upper()}_REQUESTS_PAGE_{page + 1}",
                }
            )

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        rows.append(
            [
                {
                    "type": "callback",
                    "text": MAIN_MENU_BUTTON,
                    "payload": "ADMIN_MAIN_MENU",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_hotel_selection_keyboard(hotels: list[Hotel]) -> dict:
        """Create keyboard for hotel selection when adding user"""
        rows = []

        for hotel in hotels:
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": f"🏨 {hotel.name} ({hotel.short_name})",
                        "payload": f"ADMIN_SELECT_HOTEL_{hotel.short_name}",
                    }
                ]
            )

        rows.append(
            [
                {
                    "type": "callback",
                    "text": BACK_BUTTON,
                    "payload": "ADMIN_USER_MANAGEMENT",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_role_selection_keyboard(roles: list[Role], hotel_code: str) -> dict:
        """Create keyboard for role selection when adding user"""
        rows = []

        for role in roles:
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": f"👤 {role.name}",
                        "payload": f"ADMIN_SELECT_ROLE_{hotel_code}_{role.name}",
                    }
                ]
            )

        rows.append(
            [
                {
                    "type": "callback",
                    "text": BACK_BUTTON,
                    "payload": "ADMIN_ADD_USER",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_channel_selection_keyboard(hotel_code: str) -> dict:
        """Create keyboard for channel selection when adding user"""
        rows = [
            [
                {
                    "type": "callback",
                    "text": "📱 Telegram",
                    "payload": f"ADMIN_SELECT_CHANNEL_{hotel_code}_TELEGRAM",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": "💬 MAX",
                    "payload": f"ADMIN_SELECT_CHANNEL_{hotel_code}_MAX",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": BACK_BUTTON,
                    "payload": f"ADMIN_SELECT_HOTEL_{hotel_code}",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_hotels_list_keyboard(hotels: list, page: int = 1, has_next: bool = False) -> dict:
        """Create keyboard for hotels list with pagination"""
        rows = []

        # Add hotel buttons (max 10 per page)
        for hotel in hotels:
            hotel_text = f"🏨 {hotel['name']} ({hotel['code']})"
            payload = f"ADMIN_HOTEL_USERS_{hotel['code']}"
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": hotel_text,
                        "payload": payload,
                    }
                ]
            )

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            prev_payload = f"ADMIN_HOTELS_LIST_PAGE_{page - 1}"
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "⬅️",
                    "payload": prev_payload,
                }
            )

        if has_next:
            next_payload = f"ADMIN_HOTELS_LIST_PAGE_{page + 1}"
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "➡️",
                    "payload": next_payload,
                }
            )

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        rows.append(
            [
                {
                    "type": "callback",
                    "text": BACK_BUTTON,
                    "payload": "ADMIN_USER_MANAGEMENT",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_hotel_users_keyboard(users: list, hotel_code: str, page: int = 1, has_next: bool = False) -> dict:
        """Create keyboard for hotel users list with pagination"""
        rows = []

        # Add user buttons (max 10 per page)
        for user in users:
            role_emoji = "👤"
            if user["role_name"] == RoleEnum.MANAGER.value:
                role_emoji = "👤"
            elif user["role_name"] == RoleEnum.ADMIN.value:
                role_emoji = "🔧"

            user_text = f"{role_emoji} {user['phone_number']} ({user['role_name']})"
            payload = f"ADMIN_USER_DETAIL_{user['id']}"
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": user_text,
                        "payload": payload,
                    }
                ]
            )

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            prev_payload = f"ADMIN_HOTEL_USERS_PAGE_{hotel_code}_{page - 1}"
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "⬅️",
                    "payload": prev_payload,
                }
            )

        if has_next:
            next_payload = f"ADMIN_HOTEL_USERS_PAGE_{hotel_code}_{page + 1}"
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "➡️",
                    "payload": next_payload,
                }
            )

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        rows.append(
            [
                {
                    "type": "callback",
                    "text": BACK_BUTTON,
                    "payload": "ADMIN_LIST_USERS",
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_hotel_management_keyboard(hotel_code: str) -> dict:
        """Create keyboard for hotel management"""
        rows = [
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_SELECT_ZONE_BUTTON,
                    "payload": f"ADMIN_SELECT_ZONE_{hotel_code}",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_NAME_BUTTON,
                    "payload": f"ADMIN_EDIT_HOTEL_NAME_{hotel_code}",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_DESCRIPTION_BUTTON,
                    "payload": f"ADMIN_EDIT_HOTEL_DESCRIPTION_{hotel_code}",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_BACK_BUTTON,
                    "payload": "ADMIN_SELECT_BRANCH",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_zones_list_keyboard(zones: list, hotel_code: str, page: int = 1, has_next: bool = False) -> dict:
        """Create keyboard for zones list with pagination"""
        rows = []

        # Add zone buttons (max 5 per page)
        for zone in zones:
            adult_emoji = "🔞" if zone.get("is_adult", False) else "👶"
            disabled_emoji = "❌" if zone.get("disabled_at") else "✅"
            zone_text = f"{adult_emoji} {zone['name']} ({zone['short_name']}) {disabled_emoji}"
            payload = f"ADMIN_EDIT_ZONE_{zone['id']}"
            rows.append(
                [
                    {
                        "type": "callback",
                        "text": zone_text,
                        "payload": payload,
                    }
                ]
            )

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            prev_payload = f"ADMIN_SELECT_ZONE_PAGE_{hotel_code}_{page - 1}"
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "⬅️",
                    "payload": prev_payload,
                }
            )

        if has_next:
            next_payload = f"ADMIN_SELECT_ZONE_PAGE_{hotel_code}_{page + 1}"
            pagination_row.append(
                {
                    "type": "callback",
                    "text": "➡️",
                    "payload": next_payload,
                }
            )

        if pagination_row:
            rows.append(pagination_row)

        # Add "Add Zone" button
        add_zone_payload = f"ADMIN_ADD_ZONE_{hotel_code}"
        rows.append(
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_ADD_ZONE_BUTTON,
                    "payload": add_zone_payload,
                }
            ]
        )

        # Add back button
        back_payload = f"ADMIN_SELECTED_BRANCH_{hotel_code}"
        rows.append(
            [
                {
                    "type": "callback",
                    "text": BACK_BUTTON,
                    "payload": back_payload,
                }
            ]
        )

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_zone_edit_keyboard(zone_id: str, hotel_code: str) -> dict:
        """Create keyboard for zone editing"""
        rows = [
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_NAME_BUTTON,
                    "payload": f"ADMIN_EDIT_ZONE_NAME_{zone_id}",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_DESCRIPTION_BUTTON,
                    "payload": f"ADMIN_EDIT_ZONE_DESCRIPTION_{zone_id}",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_ADULT_BUTTON,
                    "payload": f"ADMIN_EDIT_ZONE_ADULT_{zone_id}",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": ADMIN_HOTEL_MANAGEMENT_DELETE_ZONE_BUTTON,
                    "payload": f"ADMIN_DELETE_ZONE_{zone_id}",
                }
            ],
            [
                {
                    "type": "callback",
                    "text": BACK_BUTTON,
                    "payload": f"ADMIN_SELECT_ZONE_{hotel_code}",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    async def download_file_bytes(self, file_token: str) -> bytes | None:
        """
        Download file from MAX by token/URL/file_id.

        MAX can provide either:
        - Direct URL (e.g., https://i.oneme.ru/i?r=...)
        - File ID or token that needs to be fetched via API

        Args:
            file_token: File URL, file_id, or token from MAX attachment

        Returns:
            File bytes or None if download fails
        """
        try:
            if not file_token:
                logger.error("max.download_file_bytes.empty_token")
                return None

            async with httpx.AsyncClient() as client:
                # Check if it's a direct URL
                if file_token.startswith("http://") or file_token.startswith("https://"):
                    # Direct URL - download without authorization
                    logger.info("max.download_file_bytes.direct_url", url=file_token[:100])
                    response = await client.get(file_token, timeout=30.0, follow_redirects=True)
                    response.raise_for_status()
                    return response.content
                else:
                    # File ID or token - use MAX API
                    # Try GET /uploads/{file_id} endpoint
                    url = f"https://platform-api.max.ru/uploads/{file_token}"
                    headers = {"Authorization": self.bot_token}

                    logger.info(
                        "max.download_file_bytes.api_request",
                        file_token=file_token[:50],
                    )
                    response = await client.get(url, headers=headers, timeout=30.0, follow_redirects=True)
                    response.raise_for_status()
                    return response.content

        except httpx.HTTPStatusError as e:
            logger.error(
                "max.download_file_bytes.http_error",
                status_code=e.response.status_code,
                response_text=e.response.text[:200],
                file_token=file_token[:50],
            )
            return None
        except Exception as e:
            logger.error(
                "max.download_file_bytes.error",
                error=str(e),
                file_token=file_token[:50],
            )
            return None
