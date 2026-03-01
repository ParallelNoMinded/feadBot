from typing import Optional
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter
from app.core.state import STATE
from app.services.base import BaseService
from app.services.pms_user_sync import PMSUserSyncService
from shared_models.constants import ChannelType
from app.config.messages import (
    SHARE_PHONE_NUMBER_BUTTON,
    WELCOME_MESSAGE,
    SELECT_HOTEL_FOR_REGISTRATION_MESSAGE,
    SHARE_PHONE_NUMBER_MESSAGE,
    NO_HOTEL_FOR_REGISTRATION_ERROR_MESSAGE,
    NO_DATA_COMPLETE_MESSAGE,
    CHOOSE_HOTEL_MESSAGE,
    NO_HOTEL_DETECTION_ERROR_MESSAGE,
    WELCOME_MESSAGE_NO_HOTEL_NAME,
    NO_CONSENT_MESSAGE,
    CONSENT_REQUEST_MESSAGE,
    SUCCESS_REGISTRATION_MESSAGE,
    DEFAULT_WELCOME_MESSAGE,
    NO_RESERVATION_MESSAGE,
    INVALID_RESERVATION_STATUS_MESSAGE,
)

logger = structlog.get_logger(__name__)


class RegistrationService(BaseService):
    def __init__(self, session: AsyncSession, adapter: ChannelAdapter):
        super().__init__(session)
        self.adapter = adapter
        self.channel = adapter.channel_name

    async def _send(
        self,
        user_id: str,
        text: str,
        *,
        inline_keyboard: dict | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        try:
            logger.info(f"Sending registration message to user {user_id}, " f"channel={self.channel}")
            mid = await self.adapter.send_message(
                user_id,
                text,
                inline_keyboard=inline_keyboard,
                reply_markup=reply_markup,
            )
            if mid:
                STATE.remember_ui_message(user_id, mid)
                logger.info(f"Registration message sent successfully to user {user_id}, " f"message_id={mid}")
            else:
                logger.error(f"Failed to send registration message to user {user_id}: " f"send_message returned None")
        except Exception as e:
            logger.error(
                f"Error sending registration message to user {user_id}: {e}",
                exc_info=True,
            )

    async def _get_hotel_title(self, hotel_code: str) -> str:
        """Get hotel title by code"""
        try:
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code.upper())
            return hotel.name if hotel else hotel_code
        except Exception:
            return hotel_code

    async def start(self, user_id: str, resume_context: dict | None = None) -> None:
        # Initialize in-memory state
        st = STATE.set_registration(self.channel, user_id, step="ask_phone")

        await self.clear_ui_message_buttons(user_id, self.adapter, STATE)

        # Seed from globally selected hotel if resume_context is not provided
        if resume_context is None:
            try:
                sel = STATE.get_selected_hotel(user_id)
            except Exception:
                sel = None
            if sel:
                resume_context = {"hotel": sel}

        # Store hotel information in registration context
        if resume_context and resume_context.get("hotel"):
            await self._start_with_hotel_context(user_id, resume_context, st)
        else:
            await self._start_without_hotel_context(user_id)

    async def _start_with_hotel_context(self, user_id: str, resume_context: dict, st: dict) -> None:
        ctx = st.get("context") or {}
        ctx["resume"] = resume_context
        ctx["target_hotel"] = resume_context["hotel"]  # Store target hotel
        STATE.set_registration(self.channel, user_id, context=ctx)

        hotel_code = resume_context["hotel"]
        hotel_title = await self._get_hotel_title(hotel_code)

        text = WELCOME_MESSAGE.format(hotel_name=hotel_title, share_phone_number_button=SHARE_PHONE_NUMBER_BUTTON)
        await self._send(
            user_id,
            text,
            reply_markup=self.adapter.create_phone_keyboard(),
        )

    async def _start_without_hotel_context(self, user_id: str) -> None:
        """Start registration without hotel context - show hotel selection."""
        hotels = await self.catalog_repo.list_hotels()
        await self._send(
            user_id,
            SELECT_HOTEL_FOR_REGISTRATION_MESSAGE,
            reply_markup=self.adapter.create_hotels_selection_keyboard(hotels),
        )

    async def handle_message(self, user_id: str, text: str) -> Optional[str]:
        st = STATE.upsert_registration(self.channel, user_id)
        ctx = st.get("context") or {}

        step = st.get("step") or "ask_phone"

        logger.info(f"step: {step}")
        logger.info(f"ctx: {ctx}")
        logger.info(f"text: {text}")

        if step == "ask_phone":
            # This step is handled by handle_contact method
            # If we reach here, user sent text instead of contact
            await self._send(user_id, SHARE_PHONE_NUMBER_MESSAGE)
            return step

        return step

    async def handle_contact(self, user_id: str, phone_number: str, hotel_code: str | None = None) -> Optional[str]:
        """Handle phone number contact from user"""
        st = STATE.upsert_registration(self.channel, user_id)
        ctx = st.get("context") or {}

        # Get hotel_code from context if not provided
        if not hotel_code:
            target_hotel = ctx.get("target_hotel")
            resume_hotel = ctx.get("resume", {}).get("hotel")
            hotel_code = target_hotel or resume_hotel

        logger.info(
            "Processing contact with hotel context",
            user_id=user_id,
            phone_number=phone_number,
            hotel_code=hotel_code,
            registration_context=ctx,
            registration_step=st.get("step"),
        )

        if not hotel_code:
            logger.error(
                "No hotel context found for contact processing",
                user_id=user_id,
                registration_context=ctx,
                selected_hotel=STATE.get_selected_hotel(user_id),
            )
            await self._send(user_id, NO_HOTEL_FOR_REGISTRATION_ERROR_MESSAGE)
            return None

        step = st.get("step") or "ask_phone"

        if step == "ask_phone":
            # Store phone number in context
            ctx["phone_number"] = phone_number
            # Ensure hotel_code is preserved in context
            ctx["target_hotel"] = hotel_code
            STATE.set_registration(self.channel, user_id, step="ask_consent", context=ctx)

            # Get hotel title for personalized message
            hotel_title = await self._get_hotel_title(hotel_code)

            await self._send(
                user_id,
                CONSENT_REQUEST_MESSAGE.format(hotel_title=hotel_title),
                inline_keyboard=self.adapter.create_consent_keyboard(hotel_code),
            )
            return "ask_consent"

        return step

    async def handle_callback(self, user_id: str, data: str) -> Optional[str]:
        st = STATE.upsert_registration(self.channel, user_id)
        ctx = st.get("context") or {}
        resume = ctx.get("resume") or {}
        hotel_code = resume.get("hotel") or STATE.get_selected_hotel(user_id)

        if data == f"{hotel_code}_CONSENT_YES":
            # Verify collected data exists
            phone_number = ctx.get("phone_number")
            if not phone_number:
                await self._send(user_id, NO_DATA_COMPLETE_MESSAGE)
                # Redirect to hotel selection
                hotels = await self.catalog_repo.list_hotels()
                keyboard = self.adapter.create_hotels_selection_keyboard(hotels)
                await self._send(
                    user_id,
                    CHOOSE_HOTEL_MESSAGE,
                    inline_keyboard=keyboard,
                )
                STATE.set_registration(self.channel, user_id, step="ask_hotel", context={})
                return "ask_hotel"

            # Resolve hotel
            resume = ctx.get("resume", {})
            hotel_code = resume.get("hotel") or STATE.get_selected_hotel(user_id)
            h = await self.catalog_repo.get_hotel_by_code(hotel_code.upper()) if hotel_code else None
            if not h:
                await self._send(user_id, NO_HOTEL_DETECTION_ERROR_MESSAGE)
                STATE.set_registration(self.channel, user_id, step="ask_phone", context={})
                # Resend phone request
                text = WELCOME_MESSAGE_NO_HOTEL_NAME.format(share_phone_number_button=SHARE_PHONE_NUMBER_BUTTON)
                await self._send(
                    user_id,
                    text,
                    reply_markup=self.adapter.create_phone_keyboard(),
                )
                return "ask_phone"

            # Upsert user with phone number
            channel_type = ChannelType.TELEGRAM if self.channel == "telegram" else ChannelType.MAX
            await self.user_repo.upsert_telegram_guest(user_id, phone_number=phone_number, channel_type=channel_type)

            pms_service = PMSUserSyncService(self.session)
            sync_result = await pms_service.sync_user_on_registration(user_id, phone_number, h)

            if sync_result and sync_result.get("reservation_found"):
                if sync_result.get("hotel_not_found"):
                    logger.warning(
                        "PMS reservation found but hotel not in catalog",
                        user_id=user_id,
                        pms_hotel_name=sync_result.get("pms_hotel_name"),
                    )
                    await self._send(user_id, NO_RESERVATION_MESSAGE)
                    STATE.clear_registration(self.channel, user_id)
                    # Redirect to hotel selection
                    hotels = await self.catalog_repo.list_hotels()
                    keyboard = self.adapter.create_hotels_selection_keyboard(hotels)
                    await self._send(
                        user_id,
                        CHOOSE_HOTEL_MESSAGE,
                        inline_keyboard=keyboard,
                    )
                    STATE.set_registration(self.channel, user_id, step="ask_hotel", context={})
                    return "ask_hotel"
                elif sync_result.get("invalid_status"):
                    status = sync_result.get("reservation_status", "Неизвестен")
                    text = INVALID_RESERVATION_STATUS_MESSAGE.format(status=status)
                    await self._send(user_id, text)
                    STATE.clear_registration(self.channel, user_id)
                    # Redirect to hotel selection
                    hotels = await self.catalog_repo.list_hotels()
                    keyboard = self.adapter.create_hotels_selection_keyboard(hotels)
                    await self._send(
                        user_id,
                        CHOOSE_HOTEL_MESSAGE,
                        inline_keyboard=keyboard,
                    )
                    STATE.set_registration(self.channel, user_id, step="ask_hotel", context={})
                    return "ask_hotel"
                else:
                    logger.info(
                        "User synced with PMS reservation",
                        user_id=user_id,
                        hotel_id=str(h.id),
                        room_number=sync_result.get("room_number"),
                    )
            else:
                logger.warning(
                    "PMS reservation not found",
                    user_id=user_id,
                    phone_number=phone_number,
                    hotel_code=hotel_code,
                )
                await self._send(user_id, NO_RESERVATION_MESSAGE)
                STATE.clear_registration(self.channel, user_id)
                # Redirect to hotel selection
                hotels = await self.catalog_repo.list_hotels()
                keyboard = self.adapter.create_hotels_selection_keyboard(hotels)
                await self._send(
                    user_id,
                    CHOOSE_HOTEL_MESSAGE,
                    inline_keyboard=keyboard,
                )
                STATE.set_registration(self.channel, user_id, step="ask_hotel", context={})
                return "ask_hotel"

            # Complete and resume flow
            await self._complete_and_resume(user_id, hotel_code)
            # Clear in-memory registration state
            STATE.clear_registration(self.channel, user_id)
            return "completed"

        if data == f"{hotel_code}_CONSENT_NO":
            await self._send(
                user_id,
                NO_CONSENT_MESSAGE,
            )

            hotel_title = await self._get_hotel_title(hotel_code)
            await self._send(
                user_id,
                CONSENT_REQUEST_MESSAGE.format(hotel_title=hotel_title),
                inline_keyboard=self.adapter.create_consent_keyboard(hotel_code),
            )

            STATE.set_registration(self.channel, user_id, step="ask_consent")
            return "ask_consent"

        return None

    async def _complete_and_resume(
        self,
        user_id: str,
        hotel_code: Optional[str],
    ) -> None:
        success_text = SUCCESS_REGISTRATION_MESSAGE

        greet_text = DEFAULT_WELCOME_MESSAGE
        if hotel_code:
            h = await self.catalog_repo.get_hotel_by_code(hotel_code.upper())
            if h:
                greet_text = str(h.description)

        # remember selected hotel in state
        STATE.set_selected_hotel(user_id, hotel_code)

        main_menu_kb = self.adapter.main_menu_keyboard(hotel_code)

        # Send success message and remove reply keyboard
        await self._send(user_id, success_text, reply_markup={"remove_keyboard": True})

        # Send hotel description with main menu
        await self._send(
            user_id,
            greet_text,
            inline_keyboard=main_menu_kb,
        )
