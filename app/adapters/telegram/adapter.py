import json
import logging
from io import BytesIO

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

from app.adapters.channel import ChannelAdapter, IncomingMessage
from shared_models import Hotel, Role
from shared_models import RoleEnum
from app.config.messages import (
    MAIN_MENU_BUTTON,
    MAIN_MENU_USER_ABOUT_BOT_BUTTON,
    MAIN_MENU_USER_ADD_TO_PREVIOUS_FEEDBACK_BUTTON,
    MAIN_MENU_USER_LEAVE_FEEDBACK_BUTTON,
    MAIN_MENU_USER_HELP_BUTTON,
    USER_FEEDBACK_COMPLETION_BUTTON,
    USER_FEEDBACK_ADDITION_COMPLETION_BUTTON,
    MANAGER_MENU_REPORT_BUTTON,
    MANAGER_MENU_NEGATIVE_FEEDBACKS_BUTTON,
    MANAGER_MENU_QR_BUTTON,
    MANAGER_MENU_PROMPTS_BUTTON,
    ADMIN_MENU_USER_MANAGEMENT_BUTTON,
    ADMIN_MENU_BRANCH_MANAGEMENT_BUTTON,
    ADMIN_USER_MANAGEMENT_LIST_USERS_BUTTON,
    ADMIN_USER_MANAGEMENT_EDIT_USER_BUTTON,
    ADMIN_USER_MANAGEMENT_ADD_USER_BUTTON,
    ADMIN_BRANCH_MANAGEMENT_SELECT_BRANCH_BUTTON,
    ADMIN_BRANCH_MANAGEMENT_ADD_BRANCH_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_SELECT_ZONE_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_NAME_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_DESCRIPTION_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_BACK_BUTTON,
    ADMIN_USER_MANAGEMENT_PHONE_NUMBER_BUTTON,
    BACK_BUTTON,
    MANAGER_MENU_LIST_FEEDBACKS_BUTTON,
    LOAD_REPORT_FOR_ALL_HOTELS_BUTTON,
    REPORT_PERIOD_WEEK_BUTTON,
    REPORT_PERIOD_MONTH_BUTTON,
    REPORT_PERIOD_HALF_YEAR_BUTTON,
    REPORT_PERIOD_YEAR_BUTTON,
    REPORT_PERIOD_CUSTOM_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_ADD_ZONE_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_NAME_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_DESCRIPTION_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_ADULT_BUTTON,
    ADMIN_HOTEL_MANAGEMENT_DELETE_ZONE_BUTTON,
    MANAGER_MENU_FEEDBACKS_BUTTON,
    SHARE_PHONE_NUMBER_BUTTON,
    CONSENT_APPROVE_MESSAGE,
    CONSENT_REJECT_MESSAGE,
)
from app.config.settings import get_settings
from shared_models import FeedbackStatus

logger = logging.getLogger(__name__)


class TelegramAdapter(ChannelAdapter):
    channel_name = "telegram"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.base_url = f"https://api.telegram.org/bot{self.settings.TELEGRAM_BOT_TOKEN}"
        self.bot = Bot(token=self.settings.TELEGRAM_BOT_TOKEN, request=HTTPXRequest())

    def parse_payload(self, payload: bytes) -> dict | None:
        try:
            data = json.loads(payload.decode("utf-8"))
        except Exception as e:
            logger.error("Failed to parse telegram webhook", error=str(e))
            return None
        return data

    async def parse_webhook(
        self, payload: bytes, headers: dict[str, str]
    ) -> IncomingMessage | None:
        # decode payload

        data = self.parse_payload(payload)
        if not data:
            return None

        # Creates an Update object from the data dictionary received from the Telegram webhook.
        update = Update.de_json(data, self.bot)

        message = (
            update.callback_query.message if update.callback_query else update.message
        )
        if not message:
            return None

        user_id = str(message.chat.id if message.chat else message.from_user.id)

        text = (
            str(update.callback_query.data)
            if update.callback_query
            else str(message.text)
        )

        # Handle document caption as text
        if not text or text == "None":
            if message.document and message.caption:
                text = str(message.caption)
            elif message.photo and message.caption:
                text = str(message.caption)
            logger.info(f"telegram.webhook.received.caption: {text}")

        # Handle contact sharing
        contact_phone = None
        if message.contact:
            contact_phone = message.contact.phone_number.replace("+", "")
            logger.info(f"telegram.webhook.received.contact: {contact_phone}")

        logger.info(f"telegram.webhook.received.text: {text}")

        # parse /start payload (deep-linking)
        start_payload = None
        if text and text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                start_payload = parts[1]
                text = "/start"

        if text and text.startswith("/") and text != "/start":
            text = "/start"

        # media
        media_tokens = []
        media_url = None
        if message.photo:
            photos = list(message.photo)  # type: ignore[union-attr]
            # Collect all photo file_ids, sorted by size (largest first)
            media_tokens = [
                photo.file_id
                for photo in sorted(
                    photos, key=lambda p: p.file_size or 0, reverse=True
                )
            ]
            data.setdefault("_parsed", {})["media_kind"] = "image"
            data.setdefault("_parsed", {})["media_count"] = len(media_tokens)
        if message.voice:
            media_tokens = [message.voice.file_id]  # type: ignore[union-attr]
            data.setdefault("_parsed", {})["media_kind"] = "audio"
            data.setdefault("_parsed", {})["media_count"] = 1
        if message.audio:
            media_tokens = [message.audio.file_id]  # type: ignore[union-attr]
            data.setdefault("_parsed", {})["media_kind"] = "audio"
            data.setdefault("_parsed", {})["media_count"] = 1
            data.setdefault("_parsed", {})["audio_info"] = {
                "file_name": message.audio.file_name,
                "mime_type": message.audio.mime_type,
                "file_size": message.audio.file_size,
                "duration": message.audio.duration,
            }
        if message.video:
            media_tokens = [message.video.file_id]  # type: ignore[union-attr]
            data.setdefault("_parsed", {})["media_kind"] = "video"
            data.setdefault("_parsed", {})["media_count"] = 1
            data.setdefault("_parsed", {})["video_info"] = {
                "file_name": message.video.file_name,
                "mime_type": message.video.mime_type,
                "file_size": message.video.file_size,
                "duration": message.video.duration,
                "width": message.video.width,
                "height": message.video.height,
            }
        if message.document:
            media_tokens = [message.document.file_id]  # type: ignore[union-attr]
            data.setdefault("_parsed", {})["media_kind"] = "document"
            data.setdefault("_parsed", {})["media_count"] = 1
            data.setdefault("_parsed", {})["document_info"] = {
                "file_name": message.document.file_name,
                "mime_type": message.document.mime_type,
                "file_size": message.document.file_size,
            }

        callback_id = update.callback_query.id if update.callback_query else None  # type: ignore[union-attr]
        rating = None
        hotel_code = None
        zone_code = None
        if update.callback_query and update.callback_query.data and "_RATE_" in text:
            try:
                parts = text.split("_RATE_")
                if len(parts) == 2:
                    hotel_zone = parts[0]
                    rating_str = parts[1]
                    # Split hotel and zone
                    hotel_zone_parts = hotel_zone.split("_")
                    hotel_code = hotel_zone_parts[0]
                    zone_code = hotel_zone_parts[1]
                    rating = int(rating_str)
            except (ValueError, IndexError):
                rating = None
                hotel_code = None
                zone_code = None

        data["update_id"] = update.update_id
        if start_payload:
            data.setdefault("_parsed", {})["start_payload"] = start_payload
        if hotel_code:
            data.setdefault("_parsed", {})["hotel_code"] = hotel_code
        if zone_code:
            data.setdefault("_parsed", {})["zone_code"] = zone_code
        if media_tokens:
            data.setdefault("_parsed", {})["media_tokens"] = media_tokens
        media_kind = (data.get("_parsed", {}) or {}).get("media_kind") if data else None

        return IncomingMessage(
            channel=self.channel_name,
            user_id=user_id,
            text=text,
            rating=rating,
            media_url=media_url,
            media_token=media_tokens[0] if media_tokens else None,
            media_kind=media_kind,
            payload=data,
            callback_id=callback_id,
            contact_phone=contact_phone,
        )

    async def send_message(
        self,
        user_id: str,
        text: str,
        buttons: list[list[str]] | None = None,
        inline_keyboard: dict | None = None,
        reply_markup: dict | None = None,
    ) -> int | None:
        km = None
        if buttons:
            km = ReplyKeyboardMarkup(
                buttons, resize_keyboard=True, one_time_keyboard=True
            )
        if inline_keyboard and inline_keyboard.get("inline_keyboard"):
            rows: list[list[InlineKeyboardButton]] = []
            for row in inline_keyboard.get("inline_keyboard", []):
                btn_row: list[InlineKeyboardButton] = []
                for b in row:
                    btn_text = b.get("text", "")
                    if b.get("callback_data") is not None:
                        btn_row.append(
                            InlineKeyboardButton(
                                text=btn_text, callback_data=b.get("callback_data")
                            )
                        )
                    elif b.get("url"):
                        btn_row.append(
                            InlineKeyboardButton(text=btn_text, url=b.get("url"))
                        )
                    else:
                        # skip invalid inline button to avoid BadRequest
                        continue
                if btn_row:
                    rows.append(btn_row)
            km = InlineKeyboardMarkup(rows)
        if reply_markup:
            if reply_markup.get("remove_keyboard"):
                km = ReplyKeyboardRemove()
            elif reply_markup.get("inline_keyboard"):
                rows: list[list[InlineKeyboardButton]] = []
                for row in reply_markup.get("inline_keyboard", []):
                    btn_row: list[InlineKeyboardButton] = []
                    for b in row:
                        btn_text = b.get("text", "")
                        if b.get("callback_data") is not None:
                            btn_row.append(
                                InlineKeyboardButton(
                                    text=btn_text, callback_data=b.get("callback_data")
                                )
                            )
                        elif b.get("url"):
                            btn_row.append(
                                InlineKeyboardButton(text=btn_text, url=b.get("url"))
                            )
                        else:
                            continue
                    if btn_row:
                        rows.append(btn_row)
                km = InlineKeyboardMarkup(rows)
            elif reply_markup.get("keyboard"):
                kb_rows: list[list[KeyboardButton]] = []
                for row in reply_markup.get("keyboard", []):
                    kb_row: list[KeyboardButton] = []
                    for btn in row:
                        if isinstance(btn, dict):
                            kb_row.append(
                                KeyboardButton(
                                    text=btn.get("text", ""),
                                    request_contact=bool(btn.get("request_contact")),
                                    request_location=bool(btn.get("request_location")),
                                )
                            )
                        else:
                            kb_row.append(KeyboardButton(text=str(btn)))
                    kb_rows.append(kb_row)
                km = ReplyKeyboardMarkup(
                    kb_rows,
                    resize_keyboard=reply_markup.get("resize_keyboard", True),
                    one_time_keyboard=reply_markup.get("one_time_keyboard", False),
                    input_field_placeholder=reply_markup.get("input_field_placeholder"),
                    is_persistent=reply_markup.get("is_persistent", False),
                    selective=reply_markup.get("selective", False),
                )
        m = await self.bot.send_message(
            chat_id=int(user_id), text=text, reply_markup=km, parse_mode=ParseMode.HTML
        )
        return m.message_id

    async def answer_callback(
        self, callback_query_id: str, text: str | None = None
    ) -> None:
        try:
            await self.bot.answer_callback_query(callback_query_id, text=text or "")
        except Exception as e:
            logger.error(f"Failed to answer callback query {callback_query_id}: {e}")

    async def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str | None = None,
        inline_keyboard: dict | None = None,
    ) -> bool:
        """Edit an existing message with new text and keyboard"""
        try:
            if text is None:
                return await self.edit_message_reply_markup(
                    chat_id, message_id, inline_keyboard
                )
            
            km = None
            if inline_keyboard and inline_keyboard.get("inline_keyboard"):
                rows: list[list[InlineKeyboardButton]] = []
                for row in inline_keyboard.get("inline_keyboard", []):
                    btn_row: list[InlineKeyboardButton] = []
                    for b in row:
                        btn_text = b.get("text", "")
                        if b.get("callback_data") is not None:
                            btn_row.append(
                                InlineKeyboardButton(
                                    text=btn_text, callback_data=b.get("callback_data")
                                )
                            )
                        elif b.get("url"):
                            btn_row.append(
                                InlineKeyboardButton(text=btn_text, url=b.get("url"))
                            )
                        else:
                            continue
                    if btn_row:
                        rows.append(btn_row)
                km = InlineKeyboardMarkup(rows)

            await self.bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=message_id,
                text=text,
                reply_markup=km,
                parse_mode=ParseMode.HTML,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to edit message {message_id} in chat {chat_id}: {e}")
            return False

    async def edit_message_reply_markup(
        self,
        chat_id: str,
        message_id: int,
        inline_keyboard: dict | None = None,
    ) -> bool:
        """Edit only the reply markup (keyboard) of an existing message"""
        try:
            km = None
            if inline_keyboard and inline_keyboard.get("inline_keyboard"):
                rows: list[list[InlineKeyboardButton]] = []
                for row in inline_keyboard.get("inline_keyboard", []):
                    btn_row: list[InlineKeyboardButton] = []
                    for b in row:
                        btn_text = b.get("text", "")
                        if b.get("callback_data") is not None:
                            btn_row.append(
                                InlineKeyboardButton(
                                    text=btn_text, callback_data=b.get("callback_data")
                                )
                            )
                        elif b.get("url"):
                            btn_row.append(
                                InlineKeyboardButton(text=btn_text, url=b.get("url"))
                            )
                        else:
                            continue
                    if btn_row:
                        rows.append(btn_row)
                km = InlineKeyboardMarkup(rows)

            await self.bot.edit_message_reply_markup(
                chat_id=int(chat_id), message_id=message_id, reply_markup=km
            )
            return True
        except Exception as e:
            logger.error(
                f"Failed to edit message reply markup {message_id} in chat {chat_id}: {e}"
            )
            return False

    @staticmethod
    def rating_keyboard(
        hotel_short_name: str, zone_short_name: str, current_rating: int = None
    ) -> dict:
        # Inline keyboard with emojis 1-5
        buttons = [
            [
                {
                    "text": f"✅⭐️{i}" if current_rating == i else f"⭐️{i}",
                    "callback_data": f"{hotel_short_name}_{zone_short_name}_RATE_{i}",
                }
                for i in range(1, 6)
            ],
        ]
        return {"inline_keyboard": buttons}

    @staticmethod
    def thumbs_keyboard(
        hotel_short_name: str, zone_short_name: str, current_rating: int = None
    ) -> dict:
        buttons = [
            [
                {
                    "text": "✅👍" if current_rating == 5 else "👍",
                    "callback_data": f"{hotel_short_name}_{zone_short_name}_THUMB_UP",
                },
                {
                    "text": "✅👎" if current_rating == 1 else "👎",
                    "callback_data": f"{hotel_short_name}_{zone_short_name}_THUMB_DOWN",
                },
            ],
        ]
        return {"inline_keyboard": buttons}

    @staticmethod
    def main_menu_keyboard(hotel_code: str, last_feedback_id: str = None) -> dict:
        keyboard = [
            [
                {
                    "text": MAIN_MENU_USER_LEAVE_FEEDBACK_BUTTON,
                    "callback_data": f"{hotel_code}_LEAVE_FEEDBACK",
                }
            ],
        ]

        # Add "Дополнить предыдущий отзыв" button if user has previous feedback
        if last_feedback_id:
            keyboard.append(
                [
                    {
                        "text": MAIN_MENU_USER_ADD_TO_PREVIOUS_FEEDBACK_BUTTON,
                        "callback_data": f"LASTFEEDBACK_{last_feedback_id}",
                    }
                ]
            )

        # Add help and info buttons
        keyboard.extend(
            [
                [
                    {
                        "text": MAIN_MENU_USER_ABOUT_BOT_BUTTON,
                        "callback_data": f"{hotel_code}_ABOUT_BOT",
                    }
                ],
                [
                    {
                        "text": MAIN_MENU_USER_HELP_BUTTON,
                        "callback_data": f"{hotel_code}_HELP",
                    }
                ],
            ]
        )

        return {"inline_keyboard": keyboard}

    @staticmethod
    def compose_feedback_keyboard(hotel_code: str) -> dict:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": USER_FEEDBACK_COMPLETION_BUTTON,
                        "callback_data": f"{hotel_code}_MENU",
                    }
                ],
            ]
        }

    @staticmethod
    def compose_feedback_addition_keyboard(hotel_code: str) -> dict:
        return {
            "inline_keyboard": [
                [
                    {
                        "text": USER_FEEDBACK_ADDITION_COMPLETION_BUTTON,
                        "callback_data": f"{hotel_code}_MENU",
                    }
                ],
            ]
        }

    @staticmethod
    def manager_menu_keyboard(hotel_code: str) -> dict:
        rows = [
            [
                {
                    "text": MANAGER_MENU_REPORT_BUTTON,
                    "callback_data": f"{hotel_code}_MGR_REPORTS",
                }
            ],
            [
                {
                    "text": MANAGER_MENU_NEGATIVE_FEEDBACKS_BUTTON,
                    "callback_data": f"{hotel_code}_MGR_NEGATIVE_FEEDBACKS",
                }
            ],
            [{"text": MANAGER_MENU_QR_BUTTON, "callback_data": f"{hotel_code}_MGR_QR"}],
            [
                {
                    "text": MANAGER_MENU_PROMPTS_BUTTON,
                    "callback_data": f"{hotel_code}_MGR_PROMPTS",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_menu_keyboard() -> dict:
        rows = [
            [
                {
                    "text": ADMIN_MENU_USER_MANAGEMENT_BUTTON,
                    "callback_data": "ADMIN_USER_MANAGEMENT",
                }
            ],
            [
                {
                    "text": ADMIN_MENU_BRANCH_MANAGEMENT_BUTTON,
                    "callback_data": "ADMIN_BRANCH_MANAGEMENT",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_user_management_keyboard() -> dict:
        rows = [
            [
                {
                    "text": ADMIN_USER_MANAGEMENT_LIST_USERS_BUTTON,
                    "callback_data": "ADMIN_LIST_USERS",
                }
            ],
            [
                {
                    "text": ADMIN_USER_MANAGEMENT_EDIT_USER_BUTTON,
                    "callback_data": "ADMIN_EDIT_USER",
                }
            ],
            [
                {
                    "text": ADMIN_USER_MANAGEMENT_ADD_USER_BUTTON,
                    "callback_data": "ADMIN_ADD_USER",
                }
            ],
            [{"text": MAIN_MENU_BUTTON, "callback_data": "ADMIN_MAIN_MENU"}],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_branch_management_keyboard() -> dict:
        rows = [
            [
                {
                    "text": ADMIN_BRANCH_MANAGEMENT_SELECT_BRANCH_BUTTON,
                    "callback_data": "ADMIN_SELECT_BRANCH",
                }
            ],
            [
                {
                    "text": ADMIN_BRANCH_MANAGEMENT_ADD_BRANCH_BUTTON,
                    "callback_data": "ADMIN_ADD_BRANCH",
                }
            ],
            [{"text": MAIN_MENU_BUTTON, "callback_data": "ADMIN_MAIN_MENU"}],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_hotel_management_keyboard(hotel_code: str) -> dict:
        """Create keyboard for hotel management"""
        rows = [
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_SELECT_ZONE_BUTTON,
                    "callback_data": f"ADMIN_SELECT_ZONE_{hotel_code}",
                }
            ],
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_NAME_BUTTON,
                    "callback_data": f"ADMIN_EDIT_HOTEL_NAME_{hotel_code}",
                }
            ],
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_DESCRIPTION_BUTTON,
                    "callback_data": f"ADMIN_EDIT_HOTEL_DESCRIPTION_{hotel_code}",
                }
            ],
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_BACK_BUTTON,
                    "callback_data": "ADMIN_SELECT_BRANCH",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_select_branch_keyboard(
        hotels: list, page: int = 1, has_next: bool = False
    ) -> dict:
        """Create keyboard for branch selection with pagination"""
        rows = []

        # Add hotel buttons (max 10 per page)
        for hotel in hotels:
            hotel_text = f"🏨 {hotel['name']} ({hotel['code']})"
            callback_data = f"ADMIN_SELECTED_BRANCH_{hotel['code']}"
            rows.append([{"text": hotel_text, "callback_data": callback_data}])

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            prev_callback = f"ADMIN_SELECT_BRANCH_PAGE_{page - 1}"
            pagination_row.append({"text": "⬅️", "callback_data": prev_callback})

        if has_next:
            next_callback = f"ADMIN_SELECT_BRANCH_PAGE_{page + 1}"
            pagination_row.append({"text": "➡️", "callback_data": next_callback})

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        back_callback = "ADMIN_BRANCH_MANAGEMENT"
        rows.append([{"text": "🔙 Назад", "callback_data": back_callback}])

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_requests_keyboard(
        requests: list, page: int = 1, has_next: bool = False, status: str = "pending"
    ) -> dict:
        """Create keyboard for admin requests with pagination"""
        rows = []

        # Add request buttons (max 5 per page)
        for request in requests:
            phone = request.get("phone_number", "")

            button_text = f"{ADMIN_USER_MANAGEMENT_PHONE_NUMBER_BUTTON}{phone}"
            rows.append(
                [
                    {
                        "text": button_text,
                        "callback_data": f"ADMIN_REQUEST_DETAIL_{request.get('id')}",
                    }
                ]
            )

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            pagination_row.append(
                {
                    "text": "⬅️",
                    "callback_data": f"ADMIN_{status.upper()}_REQUESTS_PAGE_{page - 1}",
                }
            )

        if has_next:
            pagination_row.append(
                {
                    "text": "➡️",
                    "callback_data": f"ADMIN_{status.upper()}_REQUESTS_PAGE_{page + 1}",
                }
            )

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        rows.append([{"text": MAIN_MENU_BUTTON, "callback_data": "ADMIN_MAIN_MENU"}])

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_hotel_selection_keyboard(hotels: list[Hotel]) -> dict:
        """Create keyboard for hotel selection when adding user"""
        rows = []

        for hotel in hotels:
            rows.append(
                [
                    {
                        "text": f"🏨 {hotel.name} ({hotel.short_name})",
                        "callback_data": f"ADMIN_SELECT_HOTEL_{hotel.short_name}",
                    }
                ]
            )

        rows.append([{"text": BACK_BUTTON, "callback_data": "ADMIN_USER_MANAGEMENT"}])

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_role_selection_keyboard(roles: list[Role], hotel_code: str) -> dict:
        """Create keyboard for role selection when adding user"""
        rows = []

        for role in roles:
            rows.append(
                [
                    {
                        "text": f"👤 {role.name}",
                        "callback_data": f"ADMIN_SELECT_ROLE_{hotel_code}_{role.name}",
                    }
                ]
            )

        rows.append([{"text": BACK_BUTTON, "callback_data": "ADMIN_ADD_USER"}])

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_channel_selection_keyboard(hotel_code: str) -> dict:
        """Create keyboard for channel selection when adding user"""
        rows = [
            [
                {
                    "text": "📱 Telegram",
                    "callback_data": f"ADMIN_SELECT_CHANNEL_{hotel_code}_TELEGRAM",
                }
            ],
            [
                {
                    "text": "💬 MAX",
                    "callback_data": f"ADMIN_SELECT_CHANNEL_{hotel_code}_MAX",
                }
            ],
            [
                {
                    "text": BACK_BUTTON,
                    "callback_data": f"ADMIN_SELECT_HOTEL_{hotel_code}",
                }
            ],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def admin_hotels_list_keyboard(
        hotels: list, page: int = 1, has_next: bool = False
    ) -> dict:
        """Create keyboard for hotels list with pagination"""
        rows = []

        # Add hotel buttons (max 10 per page)
        for hotel in hotels:
            hotel_text = f"🏨 {hotel['name']} ({hotel['code']})"
            callback_data = f"ADMIN_HOTEL_USERS_{hotel['code']}"
            rows.append([{"text": hotel_text, "callback_data": callback_data}])

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            prev_callback = f"ADMIN_HOTELS_LIST_PAGE_{page - 1}"
            pagination_row.append({"text": "⬅️", "callback_data": prev_callback})

        if has_next:
            next_callback = f"ADMIN_HOTELS_LIST_PAGE_{page + 1}"
            pagination_row.append({"text": "➡️", "callback_data": next_callback})

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        back_callback = "ADMIN_USER_MANAGEMENT"
        rows.append([{"text": BACK_BUTTON, "callback_data": back_callback}])

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_hotel_users_keyboard(
        users: list, hotel_code: str, page: int = 1, has_next: bool = False
    ) -> dict:
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
            callback_data = f"ADMIN_USER_DETAIL_{user['id']}"
            rows.append([{"text": user_text, "callback_data": callback_data}])

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            prev_callback = f"ADMIN_HOTEL_USERS_PAGE_{hotel_code}_{page - 1}"
            pagination_row.append({"text": "⬅️", "callback_data": prev_callback})

        if has_next:
            next_callback = f"ADMIN_HOTEL_USERS_PAGE_{hotel_code}_{page + 1}"
            pagination_row.append({"text": "➡️", "callback_data": next_callback})

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        back_callback = "ADMIN_LIST_USERS"
        rows.append([{"text": BACK_BUTTON, "callback_data": back_callback}])

        return {"inline_keyboard": rows}

    @staticmethod
    def negative_feedbacks_keyboard(
        feedbacks: list, hotel_code: str, page: int = 1, has_next: bool = False
    ) -> dict:
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
                        "text": button_text,
                        "callback_data": f"{hotel_code}_MGR_FEEDBACK_{feedback.get('id')}",
                    }
                ]
            )

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            pagination_row.append(
                {
                    "text": "⬅️",
                    "callback_data": f"{hotel_code}_MGR_NEGATIVE_FEEDBACKS_PAGE_{page - 1}",
                }
            )

        if has_next:
            pagination_row.append(
                {
                    "text": "➡️",
                    "callback_data": f"{hotel_code}_MGR_NEGATIVE_FEEDBACKS_PAGE_{page + 1}",
                }
            )

        if pagination_row:
            rows.append(pagination_row)

        # Add back button
        rows.append([{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}])

        return {"inline_keyboard": rows}

    @staticmethod
    def zones_prompts_keyboard(zones: list, hotel_code: str) -> dict:
        """Create keyboard for zone prompts selection"""
        rows = []

        for zone in zones:
            zone_code = zone.get("code", "")
            zone_name = zone.get("name", "Неизвестная зона")
            rows.append(
                [
                    {
                        "text": f"{zone_name}",
                        "callback_data": f"{hotel_code}_MGR_PROMPT_ZONE_{zone_code}",
                    }
                ]
            )

        rows.append([{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}])

        return {"inline_keyboard": rows}

    async def delete_message(self, chat_id: str, message_id: int) -> None:
        try:
            await self.bot.delete_message(chat_id=int(chat_id), message_id=message_id)
        except Exception:
            logger.error(
                "Failed to delete message",
                extra={"chat_id": chat_id, "message_id": message_id},
            )
            pass

    async def send_document_bytes(
        self,
        user_id: str,
        filename: str,
        data: bytes,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> int | None:
        bio = BytesIO(data)
        bio.name = filename
        m = await self.bot.send_document(
            chat_id=int(user_id),
            document=bio,
            caption=caption or "",
            reply_markup=reply_markup,
        )
        return m.message_id

    async def send_media_group_bytes(
        self,
        user_id: str,
        items: list[dict],
        caption: str | None = None,
    ) -> list[int] | None:
        """
        Send up to 10 media items as a single album (photo/audio/document supported).
        items: [{"kind": "image|audio|document", "filename": str, "data": bytes}]
        """
        media: list = []
        for idx, it in enumerate(items[:10]):
            bio = BytesIO(it.get("data") or b"")
            bio.name = it.get("filename") or "file.bin"
            kind = (it.get("kind") or "document").lower()
            item_caption = caption if idx == 0 else None
            if kind == "image":
                media.append(InputMediaPhoto(media=bio, caption=item_caption))
            elif kind == "audio":
                media.append(InputMediaAudio(media=bio, caption=item_caption))
            else:
                media.append(InputMediaDocument(media=bio, caption=item_caption))

        if not media:
            return None
        messages = await self.bot.send_media_group(chat_id=int(user_id), media=media)
        return [m.message_id for m in messages]

    async def download_file_bytes(self, file_id: str) -> bytes | None:
        try:
            f = await self.bot.get_file(file_id)
            ba = await f.download_as_bytearray()
        except Exception:
            return None

        return bytes(ba)

    async def get_file_url(self, file_id: str) -> str | None:
        """Get direct URL to file from Telegram API"""
        try:
            file = await self.bot.get_file(file_id)
            return file.file_path
        except Exception as e:
            logger.error(f"Failed to get file URL for {file_id}: {e}")
            return None

    @staticmethod
    def create_status_keyboard(
        feedback_id: str, hotel_code: str, current_status
    ) -> dict:
        """Create keyboard with status buttons"""

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
                status_row.append({"text": f"✅ {label}", "callback_data": "disabled"})
            else:
                # Other statuses - show as clickable buttons
                status_row.append(
                    {
                        "text": label,
                        "callback_data": f"{hotel_code}_MGR_STATUS_{feedback_id}_{status.value}",
                    }
                )

        keyboard = [
            status_row,
            [
                {
                    "text": MANAGER_MENU_LIST_FEEDBACKS_BUTTON,
                    "callback_data": f"{hotel_code}_MGR_NEGATIVE_FEEDBACKS",
                }
            ],
            [{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}],
        ]

        return {"inline_keyboard": keyboard}

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
                        "text": f"{hotel_name}",
                        "callback_data": f"{hotel_code}_MGR_REPORT_HOTEL_{hotel_code_item}",
                    }
                ]
            )

        if len(hotels) > 1:
            rows.append(
                [
                    {
                        "text": LOAD_REPORT_FOR_ALL_HOTELS_BUTTON,
                        "callback_data": f"{hotel_code}_MGR_REPORT_ALL",
                    }
                ]
            )

        rows.append([{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}])

        return {"inline_keyboard": rows}

    @staticmethod
    def report_period_keyboard(hotel_code: str, hotel_short_name: str = "") -> dict:
        """Create keyboard for report period selection (horizontal buttons)"""

        period_buttons = [
            {
                "text": REPORT_PERIOD_WEEK_BUTTON,
                "callback_data": f"{hotel_code}_MGR_REPORT_WEEK_{hotel_short_name}",
            },
            {
                "text": REPORT_PERIOD_MONTH_BUTTON,
                "callback_data": f"{hotel_code}_MGR_REPORT_MONTH_{hotel_short_name}",
            },
            {
                "text": REPORT_PERIOD_HALF_YEAR_BUTTON,
                "callback_data": f"{hotel_code}_MGR_REPORT_HALF-YEAR_{hotel_short_name}",
            },
            {
                "text": REPORT_PERIOD_YEAR_BUTTON,
                "callback_data": f"{hotel_code}_MGR_REPORT_YEAR_{hotel_short_name}",
            },
        ]

        rows = [
            period_buttons,
            [
                {
                    "text": REPORT_PERIOD_CUSTOM_BUTTON,
                    "callback_data": f"{hotel_code}_MGR_REPORT_CUSTOM_{hotel_short_name}",
                }
            ],
            [{"text": BACK_BUTTON, "callback_data": f"{hotel_code}_MGR_REPORTS"}],
        ]

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_zones_list_keyboard(
        zones: list, hotel_code: str, page: int = 1, has_next: bool = False
    ) -> dict:
        """Create keyboard for zones list with pagination"""
        rows = []

        # Add zone buttons (max 5 per page)
        for zone in zones:
            adult_emoji = "🔞" if zone.get("is_adult", False) else "👶"
            disabled_emoji = "❌" if zone.get("disabled_at") else "✅"
            zone_text = (
                f"{adult_emoji} {zone['name']} ({zone['short_name']}) {disabled_emoji}"
            )
            callback_data = f"ADMIN_EDIT_ZONE_{zone['id']}"
            rows.append([{"text": zone_text, "callback_data": callback_data}])

        # Add pagination buttons
        pagination_row = []
        if page > 1:
            prev_callback = f"ADMIN_SELECT_ZONE_PAGE_{hotel_code}_{page - 1}"
            pagination_row.append({"text": "⬅️", "callback_data": prev_callback})

        if has_next:
            next_callback = f"ADMIN_SELECT_ZONE_PAGE_{hotel_code}_{page + 1}"
            pagination_row.append({"text": "➡️", "callback_data": next_callback})

        if pagination_row:
            rows.append(pagination_row)

        # Add "Add Zone" button
        add_zone_callback = f"ADMIN_ADD_ZONE_{hotel_code}"
        rows.append(
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_ADD_ZONE_BUTTON,
                    "callback_data": add_zone_callback,
                }
            ]
        )

        # Add back button
        back_callback = f"ADMIN_SELECTED_BRANCH_{hotel_code}"
        rows.append([{"text": BACK_BUTTON, "callback_data": back_callback}])

        return {"inline_keyboard": rows}

    @staticmethod
    def admin_zone_edit_keyboard(zone_id: str, hotel_code: str) -> dict:
        """Create keyboard for zone editing"""
        rows = [
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_NAME_BUTTON,
                    "callback_data": f"ADMIN_EDIT_ZONE_NAME_{zone_id}",
                }
            ],
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_DESCRIPTION_BUTTON,
                    "callback_data": f"ADMIN_EDIT_ZONE_DESCRIPTION_{zone_id}",
                }
            ],
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_EDIT_ZONE_ADULT_BUTTON,
                    "callback_data": f"ADMIN_EDIT_ZONE_ADULT_{zone_id}",
                }
            ],
            [
                {
                    "text": ADMIN_HOTEL_MANAGEMENT_DELETE_ZONE_BUTTON,
                    "callback_data": f"ADMIN_DELETE_ZONE_{zone_id}",
                }
            ],
            [{"text": BACK_BUTTON, "callback_data": f"ADMIN_SELECT_ZONE_{hotel_code}"}],
        ]
        return {"inline_keyboard": rows}

    @staticmethod
    def manager_notification_keyboard(
        hotel_code: str, negative_feedback_id: str
    ) -> dict:
        """Create keyboard for manager notifications with link to most negative feedback"""
        return {
            "inline_keyboard": [
                [
                    {
                        "text": MANAGER_MENU_FEEDBACKS_BUTTON,
                        "callback_data": f"{hotel_code}_MGR_FEEDBACK_{negative_feedback_id}",
                    }
                ]
            ]
        }

    @staticmethod
    def create_phone_keyboard() -> dict:
        """Create phone sharing keyboard."""
        return {
            "keyboard": [
                [{"text": SHARE_PHONE_NUMBER_BUTTON, "request_contact": True}]
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True,
            "selective": True,
        }

    @staticmethod
    def create_hotels_selection_keyboard(hotels: list[Hotel]) -> dict:
        """Create hotels selection keyboard."""
        return {
            "inline_keyboard": [
                [
                    {"text": hotel.name, "callback_data": f"HOTEL_{hotel.short_name}"}
                    for hotel in hotels
                ]
            ]
        }

    @staticmethod
    def create_consent_keyboard(hotel_code: str) -> dict:
        """Create consent keyboard."""
        return {
            "inline_keyboard": [
                [
                    {
                        "text": CONSENT_APPROVE_MESSAGE,
                        "callback_data": f"{hotel_code}_CONSENT_YES",
                    },
                    {
                        "text": CONSENT_REJECT_MESSAGE,
                        "callback_data": f"{hotel_code}_CONSENT_NO",
                    },
                ]
            ]
        }
