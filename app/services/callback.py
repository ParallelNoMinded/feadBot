import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict

import structlog
from sqlalchemy import select as _select
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.config.settings import Settings
from app.core.state import InMemoryState
from app.models import FeedbackInfoModel
from app.models.constants import CallbackActions
from app.repositories.feedback_pg import FeedbackPGRepository
from app.services.admin_user import AdminUserService
from app.services.base import BaseService
from app.services.button_state import ButtonStateService
from app.services.feedback import FeedbackService
from app.services.feedback_limit import FeedbackLimitService
from app.services.menu import MenuService
from app.services.registration import RegistrationService
from app.services.reporting import ReportingService
from app.services.storage import S3Storage
from app.services.ui_message import UIMessageService
from app.services.user_validation import UserValidationService
from app.utils.qr import generate_qr_png_bytes
from shared_models import (
    AnalysisResult,
    Comment,
    Feedback,
    FeedbackComment,
    Hotel,
    Scenario,
    User,
    UserHotel,
    Zone,
    RoleEnum,
)
from shared_models import FeedbackStatus
from shared_models.constants import ChannelType
from app.utils.hotel_timezone import convert_to_timezone
from app.repositories.roles import RolesRepository
from app.config.messages import (
    ADMIN_MENU_MESSAGE,
    MAIN_MENU_BUTTON,
    MANAGER_MENU_SELECT_ZONE_FOR_QR_BUTTON,
    MANAGER_MENU_SELECT_HOTEL_FOR_REPORT_BUTTON,
    MANAGER_MENU_SELECT_ALL_HOTELS_FOR_REPORT_BUTTON,
    UNSUCCESSFULL_SEARCH_INFO_LAST_FEEDBACK_MESSAGE,
    FEEDBACK_ADDITION_CONTEXT_MESSAGE,
    FEEDBACK_ADDITION_COMMENT_DISPLAY_MESSAGE,
    FEEDBACK_ADDITION_NO_COMMENTS_DISPLAY_MESSAGE,
    MANAGER_MENU_NO_NEGATIVE_FEEDBACKS_MESSAGE,
    MANAGER_MENU_NEGATIVE_FEEDBACKS_PAGE_MESSAGE,
    MANAGER_MENU_FEEDBACK_MEDIA_FILES_MESSAGE,
    NO_ZONE_MESSAGE,
    MANAGER_MENU_SELECT_ZONE_FOR_PROMPTS_BUTTON,
    NO_ZONE_FOUND_MESSAGE,
    CANCEL_BUTTON,
    NO_HOTEL_OR_ZONE_FOUND_MESSAGE,
    NO_DEFAULT_VALUE_FOR_RESET_MESSAGE,
    INVALID_COMMAND_FORMAT_MESSAGE,
    NO_FEEDBACK_FOUND_MESSAGE,
    SHARE_PHONE_NUMBER_BUTTON,
    WELCOME_MESSAGE,
    WELCOME_MESSAGE_NO_HOTEL_NAME,
    CONSENT_MESSAGE,
    ABOUT_BOT_MESSAGE,
    HELP_MESSAGE,
    ADMIN_USER_MANAGEMENT_MENU_MESSAGE,
    ADMIN_BRANCH_MANAGEMENT_MENU_MESSAGE,
    NO_HOTELS_FOUND_MESSAGE,
    ADMIN_SELECT_BRANCH_MESSAGE,
    NO_HOTELS_LOAD_ERROR_MESSAGE,
    NO_HOTEL_FOUND_MESSAGE,
    NO_HOTEL_ADD_ERROR_MESSAGE,
    ADMIN_ADD_HOTEL_MESSAGE,
    NO_ZONE_DELETE_ERROR_MESSAGE,
    SUCCESS_ZONE_DELETE_MESSAGE,
    BACK_BUTTON,
    NO_ZONE_ADULT_CHANGE_ERROR_MESSAGE,
    SUCCESS_ZONE_ADULT_CHANGE_MESSAGE,
    FOR_ALL_AGES_MESSAGE,
    FOR_CHILDREN_MESSAGE,
    NO_ZONE_NAME_CHANGE_ERROR_MESSAGE,
    ACTIVE_MESSAGE,
    DISABLED_MESSAGE,
    EDIT_ZONE_MESSAGE,
    NO_ZONE_ADD_ERROR_MESSAGE,
    ADMIN_ADD_ZONE_MESSAGE,
    NO_HOTEL_FOUND_ERROR_MESSAGE,
    ERROR_LOADING_ZONE_MESSAGE,
    EDIT_ZONE_DESCRIPTION_MESSAGE,
    EDIT_ZONE_DESCRIPTION_INPUT_MESSAGE,
    ERROR_LOADING_ZONES_LIST_MESSAGE,
    ADMIN_ZONES_LIST_PAGE_MESSAGE,
    NO_ZONES_FOUND_MESSAGE,
    NO_ZONES_FOUND_ERROR_MESSAGE,
    ADMIN_HOTEL_MANAGEMENT_ADD_ZONE_BUTTON,
    NO_ZONE_EDIT_ERROR_MESSAGE,
    NO_USER_DELETE_ERROR_MESSAGE,
    ZONE_SUCCESSFULLY_DELETED_MESSAGE,
    NO_USER_FOUND_MESSAGE,
    ERROR_CHANGING_USER_STATUS_MESSAGE,
    USER_STATUS_CHANGED_MESSAGE,
    ERROR_LOADING_ROLES_MESSAGE,
    CHANGE_USER_ROLE_MESSAGE,
    ERROR_CHANGING_USER_ROLE_MESSAGE,
    SUCCESS_USER_ROLE_CHANGED_MESSAGE,
    ROLE_NOT_FOUND_MESSAGE,
    RESET_PROMPT_MESSAGE,
    EDIT_PROMPT_BUTTON,
    RESET_PROMPT_BUTTON,
    SUCCESS_PROMPT_UPDATING_MESSAGE,
    NO_EMPTY_PROMPT_MESSAGE,
    NO_EDITING_SESSION_FOUND_MESSAGE,
    EDIT_PROMPT_MESSAGE,
    ZONE_PROMPT_DESCRIPTION_MESSAGE,
    ERROR_REPORTING_MESSAGE,
    SELECT_PERIOD_FOR_REPORTING_MESSAGE,
    ERROR_SENDING_QR_CODE_MESSAGE,
    SUCCESS_QR_CODE_GENERATION_MESSAGE,
    NO_ADMIN_ACCESS_MESSAGE,
    ADMIN_CHANGE_USER_STATUS_MESSAGE,
    ADMIN_USER_INFO_MESSAGE,
    HANDLE_ADMIN_LIST_MESSAGE,
    CUSTOM_PERIOD_INPUT_MESSAGE,
    RATING_REQUEST_MESSAGE_ZONE,
)

logger = structlog.get_logger(__name__)


class CallbackService(BaseService):
    def __init__(self, session: AsyncSession, settings: Settings):
        super().__init__(session)
        self.settings = settings
        # RegistrationService will be created dynamically with adapter
        self.user_validation = UserValidationService(session)
        self.feedback_limit = FeedbackLimitService(session)
        self.ui_message = UIMessageService(session)
        self.reporting_service = ReportingService(session)
        self.feedback_repo = FeedbackPGRepository(session)
        self.menu_service = MenuService(session)
        self.feedback_service = FeedbackService(session)
        self.admin_user_service = AdminUserService(session)
        self.storage = S3Storage()
        self.button_state = ButtonStateService(session)
        self.roles_repo = RolesRepository(session)

    async def handle_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        parsed_early: dict,
        active_fs: Dict,
    ) -> Dict[str, str]:
        """Handle all callback types"""
        cq_data_full = msg.payload.get("callback_query", {}).get("data", "")
        msg_text = msg.text

        # Handle button state updates (mark as pressed and update UI)
        await self._handle_button_state_update(msg, adapter)

        if CallbackActions.ACCEPTED_CONSENT.value in cq_data_full:
            logger.info(
                "Processing registration callback",
                user_id=msg.user_id,
                callback_data=cq_data_full,
            )
            registration_service = RegistrationService(self.session, adapter)
            await registration_service.handle_callback(msg.user_id, cq_data_full)  # type: ignore[index]
            await self.session.commit()
            return {"ok": "true"}

        can_leave_feedback = await self.user_repo.can_user_leave_feedback(msg.user_id)

        if CallbackActions.ADMIN_MAIN_MENU in msg_text:
            logger.info(f"ADMIN_MAIN_MENU callback: {msg.text}")
            # Handle admin main menu - inline logic from
            # _handle_admin_main_menu_callback
            if not await self.check_admin_access(msg, adapter, state):
                return {"ok": "true"}

            keyboard = adapter.admin_menu_keyboard()
            await self.send_and_remember_message(
                msg.user_id,
                "🔧 <b>Панель администратора</b>\n\nВыберите действие:",
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Главное меню")

            return {"ok": "true"}

        if CallbackActions.ADMIN_HOTELS_LIST_PAGE in msg_text:
            logger.info(f"ADMIN_HOTELS_LIST_PAGE callback: {msg.text}")
            return await self._handle_admin_hotels_list_page_callback(msg, adapter, state)

        if CallbackActions.ADMIN_EDIT_ZONE_NAME in msg_text:
            logger.info(f"ADMIN_EDIT_ZONE_NAME callback: {msg.text}")
            return await self._handle_admin_edit_zone_name_callback(msg, adapter, state)

        if CallbackActions.ADMIN_EDIT_ZONE_DESCRIPTION in msg_text:
            logger.info(f"ADMIN_EDIT_ZONE_DESCRIPTION callback: {msg.text}")
            return await self._handle_admin_edit_zone_description_callback(msg, adapter, state)

        if CallbackActions.ADMIN_EDIT_ZONE_ADULT in msg_text:
            logger.info(f"ADMIN_EDIT_ZONE_ADULT callback: {msg.text}")
            return await self._handle_admin_edit_zone_adult_callback(msg, adapter, state)

        if CallbackActions.REDIRECT_TO_MENU in msg_text:
            return await self.menu_service.handle_menu_request(msg, adapter, state)

        # Handle menu button from report (needs special handling for button state)
        if "_MENU_FROM_REPORT" in msg_text:
            # Extract hotel code and create proper menu callback
            hotel_code = msg_text.split("_MENU_FROM_REPORT")[0]
            msg.text = f"{hotel_code}_MENU"
            return await self.menu_service.handle_menu_request(msg, adapter, state)

        if CallbackActions.LEAVE_FEEDBACK in msg_text:
            return await self.feedback_service.handle_feedback_request(
                msg,
                adapter,
                state,
                can_leave_feedback,
            )

        if CallbackActions.MGR_PROMPT_ZONE in msg_text:
            logger.info(f"MGR_PROMPT_ZONE callback: {msg.text}")
            return await self._handle_manager_prompt_zone_callback(msg, adapter, state)

        if CallbackActions.MGR_EDIT_PROMPT in msg_text:
            logger.info(f"MGR_EDIT_PROMPT callback: {msg.text}")
            return await self._handle_manager_edit_prompt_callback(msg, adapter, state)

        if CallbackActions.MGR_SAVE_PROMPT in msg_text:
            logger.info(f"MGR_SAVE_PROMPT callback: {msg.text}")
            return await self._handle_manager_save_prompt_callback(msg, adapter, state)

        if CallbackActions.MGR_RESET_PROMPT in msg_text:
            logger.info(f"MGR_RESET_PROMPT callback: {msg.text}")
            return await self._handle_manager_reset_prompt_callback(msg, adapter, state)

        if CallbackActions.MGR_STATUS in msg_text:
            logger.info(f"MGR_STATUS callback: {msg.text}")
            return await self._handle_manager_status_callback(msg, adapter, state)

        if CallbackActions.FEEDBACK_ZONE in msg_text:
            logger.info(f"FEEDBACK_ZONE callback: {msg.text}")
            return await self._handle_zone_callback(msg, adapter, state, can_leave_feedback)

        if CallbackActions.FEEDBACK_RATE in msg_text:
            logger.info(f"FEEDBACK_RATE callback: {msg.text}")
            return await self._handle_rate_callback(msg, adapter, state, parsed_early, active_fs)

        if CallbackActions.FEEDBACK_THUMB_UP in msg_text or CallbackActions.FEEDBACK_THUMB_DOWN in msg_text:
            logger.info(f"FEEDBACK_THUMB_UP or FEEDBACK_THUMB_DOWN callback: {msg.text}")
            return await self._handle_thumb_callback(msg, adapter, state, active_fs)

        if CallbackActions.MGR_QR_ZONE in msg_text:
            logger.info(f"MGR_QR_ZONE callback: {msg.text}")
            return await self._handle_manager_qr_zone_callback(msg, adapter, state)

        if CallbackActions.MGR_QR in msg_text:
            logger.info(f"MGR_QR callback: {msg.text}")
            return await self._handle_manager_qr_callback(msg, adapter, state)

        if CallbackActions.MGR_REPORT_ALL in msg_text:
            logger.info(f"MGR_REPORT_ALL callback: {msg.text}")
            return await self._handle_manager_report_all_hotels_callback(msg, adapter, state)

        if CallbackActions.MGR_REPORT_PERIOD in msg_text and "CUSTOM" in msg_text:
            logger.info(f"MGR_REPORT_CUSTOM callback: {msg.text}")
            return await self._handle_manager_report_custom_callback(msg, adapter, state)

        if CallbackActions.MGR_REPORT_PERIOD in msg_text and any(
            period in msg_text for period in ["WEEK", "MONTH", "HALF-YEAR", "YEAR"]
        ):
            logger.info(f"MGR_REPORT_PERIOD callback: {msg.text}")
            return await self._handle_manager_report_period_callback(msg, adapter, state)

        if CallbackActions.MGR_REPORTS in msg_text:
            logger.info(f"MGR_REPORTS callback: {msg.text}")
            return await self._handle_manager_reports_callback(msg, adapter, state)

        if CallbackActions.MGR_REPORT_HOTEL in msg_text:
            logger.info(f"MGR_REPORT_HOTEL callback: {msg.text}")
            return await self._handle_manager_report_hotel_callback(msg, adapter, state)

        if CallbackActions.MGR_NEGATIVE_FEEDBACKS_PAGE in msg_text:
            logger.info(f"MGR_NEGATIVE_FEEDBACKS_PAGE callback: {msg.text}")
            return await self._handle_manager_negative_feedbacks_page_callback(msg, adapter, state)

        if CallbackActions.MGR_NEGATIVE_FEEDBACKS in msg_text:
            logger.info(f"MGR_NEGATIVE_FEEDBACKS callback: {msg.text}")
            return await self._handle_manager_negative_feedbacks_callback(msg, adapter, state)

        if CallbackActions.MGR_FEEDBACK in msg_text:
            logger.info(f"MGR_FEEDBACK callback: {msg.text}")
            return await self._handle_manager_feedback_detail_callback(msg, adapter, state)

        if CallbackActions.MGR_PROMPTS in msg_text:
            logger.info(f"MGR_PROMPTS callback: {msg.text}")
            return await self._handle_manager_prompts_callback(msg, adapter, state)

        # Admin callbacks
        if CallbackActions.ADMIN_USER_MANAGEMENT in msg_text:
            logger.info(f"ADMIN_USER_MANAGEMENT callback: {msg.text}")
            return await self._handle_admin_user_management_callback(msg, adapter, state)

        if CallbackActions.ADMIN_BRANCH_MANAGEMENT in msg_text:
            logger.info(f"ADMIN_BRANCH_MANAGEMENT callback: {msg.text}")
            return await self._handle_admin_branch_management_callback(msg, adapter, state)

        if CallbackActions.ADMIN_SELECT_BRANCH in msg_text:
            logger.info(f"ADMIN_SELECT_BRANCH callback: {msg.text}")
            return await self._handle_admin_select_branch_callback(msg, adapter, state)

        if CallbackActions.ADMIN_SELECT_BRANCH_PAGE in msg_text:
            logger.info(f"ADMIN_SELECT_BRANCH_PAGE callback: {msg.text}")
            return await self._handle_admin_select_branch_page_callback(msg, adapter, state)

        if CallbackActions.ADMIN_SELECTED_BRANCH in msg_text:
            logger.info(f"ADMIN_SELECTED_BRANCH callback: {msg.text}")
            return await self._handle_admin_selected_branch_callback(msg, adapter, state)

        if CallbackActions.ADMIN_EDIT_HOTEL_NAME in msg_text:
            logger.info(f"ADMIN_EDIT_HOTEL_NAME callback: {msg.text}")
            return await self._handle_admin_edit_hotel_name_callback(msg, adapter, state)

        if CallbackActions.ADMIN_EDIT_HOTEL_DESCRIPTION in msg_text:
            logger.info(f"ADMIN_EDIT_HOTEL_DESCRIPTION callback: {msg.text}")
            return await self._handle_admin_edit_hotel_description_callback(msg, adapter, state)

        if CallbackActions.ADMIN_SELECT_ZONE in msg_text:
            logger.info(f"ADMIN_SELECT_ZONE callback: {msg.text}")
            return await self._handle_admin_select_zone_callback(msg, adapter, state)

        if CallbackActions.ADMIN_ADD_BRANCH in msg_text:
            logger.info(f"ADMIN_ADD_BRANCH callback: {msg.text}")
            return await self._handle_admin_add_branch_callback(msg, adapter, state)

        if CallbackActions.ADMIN_ADD_USER in msg_text:
            logger.info(f"ADMIN_ADD_USER callback: {msg.text}")
            return await self._handle_admin_add_user_callback(msg, adapter, state)

        if CallbackActions.ADMIN_LIST_USERS in msg_text:
            logger.info(f"ADMIN_LIST_USERS callback: {msg.text}")
            return await self._handle_admin_list_users_callback(msg, adapter, state)

        if CallbackActions.ADMIN_SELECT_HOTEL in msg_text:
            logger.info(f"ADMIN_SELECT_HOTEL callback: {msg.text}")
            return await self._handle_admin_select_hotel_callback(msg, adapter, state)

        if CallbackActions.ADMIN_SELECT_ROLE in msg_text:
            logger.info(f"ADMIN_SELECT_ROLE callback: {msg.text}")
            return await self._handle_admin_select_role_callback(msg, adapter, state)

        if CallbackActions.ADMIN_SELECT_CHANNEL in msg_text:
            logger.info(f"ADMIN_SELECT_CHANNEL callback: {msg.text}")
            return await self._handle_admin_select_channel_callback(msg, adapter, state)

        if CallbackActions.ADMIN_HOTEL_USERS in msg_text:
            logger.info(f"ADMIN_HOTEL_USERS callback: {msg.text}")
            return await self._handle_admin_hotel_users_callback(msg, adapter, state)

        if CallbackActions.ADMIN_HOTEL_USERS_PAGE in msg_text:
            logger.info(f"ADMIN_HOTEL_USERS_PAGE callback: {msg.text}")
            return await self._handle_admin_hotel_users_page_callback(msg, adapter, state)

        if CallbackActions.ADMIN_USER_DETAIL in msg_text:
            logger.info(f"ADMIN_USER_DETAIL callback: {msg.text}")
            return await self._handle_admin_user_detail_callback(msg, adapter, state)

        if CallbackActions.ADMIN_USER_DEACTIVATE in msg_text:
            logger.info(f"ADMIN_USER_DEACTIVATE callback: {msg.text}")
            return await self._handle_admin_user_deactivate_callback(msg, adapter, state)

        if CallbackActions.ADMIN_USER_BACK_TO_LIST in msg_text:
            logger.info(f"ADMIN_USER_BACK_TO_LIST callback: {msg.text}")
            return await self._handle_admin_user_back_to_list_callback(msg, adapter, state)

        if CallbackActions.ADMIN_EDIT_USER in msg_text:
            logger.info(f"ADMIN_EDIT_USER callback: {msg.text}")
            return await self._handle_admin_edit_user_callback(msg, adapter, state)

        if CallbackActions.ADMIN_EDIT_USER_HOTEL_STATUS in msg_text:
            logger.info(f"ADMIN_EDIT_HOTEL callback: {msg.text}")
            return await self._handle_admin_edit_user_hotel_status_callback(msg, adapter, state)

        if CallbackActions.ADMIN_CHANGE_USER_ROLE in msg_text:
            logger.info(f"ADMIN_CHANGE_USER_ROLE callback: {msg.text}")
            return await self._handle_admin_change_user_role_callback(msg, adapter, state)

        if CallbackActions.ADMIN_TOGGLE_USER_STATUS in msg_text:
            logger.info(f"ADMIN_TOGGLE_USER_STATUS callback: {msg.text}")
            return await self._handle_admin_toggle_user_status_callback(msg, adapter, state)

        if CallbackActions.ADMIN_DELETE_USER in msg_text:
            logger.info(f"ADMIN_DELETE_USER callback: {msg.text}")
            return await self._handle_admin_delete_user_callback(msg, adapter, state)

        if CallbackActions.ADMIN_CONFIRM_ROLE_CHANGE in msg_text:
            logger.info(f"ADMIN_CONFIRM_ROLE_CHANGE callback: {msg.text}")
            return await self._handle_admin_confirm_role_change_callback(msg, adapter, state)

        # Zone management callbacks
        if CallbackActions.ADMIN_SELECT_ZONE_PAGE in msg_text:
            logger.info(f"ADMIN_SELECT_ZONE_PAGE callback: {msg.text}")
            return await self._handle_admin_select_zone_page_callback(msg, adapter, state)

        if CallbackActions.ADMIN_EDIT_ZONE in msg_text:
            logger.info(f"ADMIN_EDIT_ZONE callback: {msg.text}")
            return await self._handle_admin_edit_zone_callback(msg, adapter, state)

        if CallbackActions.ADMIN_ADD_ZONE in msg_text:
            logger.info(f"ADMIN_ADD_ZONE callback: {msg.text}")
            return await self._handle_admin_add_zone_callback(msg, adapter, state)

        if CallbackActions.ADMIN_DELETE_ZONE in msg_text:
            logger.info(f"ADMIN_DELETE_ZONE callback: {msg.text}")
            return await self._handle_admin_delete_zone_callback(msg, adapter, state)

        if CallbackActions.LAST_FEEDBACK in msg_text:
            logger.info(f"LAST_FEEDBACK callback: {msg.text}")
            return await self._handle_last_feedback_callback(msg, adapter, state)

        if CallbackActions.HOTEL in msg_text:
            logger.info(f"HOTEL callback: {msg.text}")
            return await self._handle_hotel_callback(msg, adapter, state)

        if CallbackActions.ABOUT_BOT in msg_text:
            logger.info(f"ABOUT_BOT callback: {msg.text}")
            return await self._handle_about_bot_callback(msg, adapter, state)

        if CallbackActions.HELP in msg_text:
            logger.info(f"HELP callback: {msg.text}")
            return await self._handle_help_callback(msg, adapter, state)

        logger.info(
            "telegram.webhook.callback.unhandled",
            user_id=msg.user_id,
            msg_text=msg_text,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Кнопка неактивна")

        return {"ok": "true"}

    async def check_admin_access(self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState) -> bool:
        """Check if user has admin access. Returns True if access granted, False otherwise."""
        is_admin = await self.admin_repo.get_by_telegram_id(msg.user_id)
        if not is_admin:
            await self.send_and_remember_message(
                msg.user_id,
                NO_ADMIN_ACCESS_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Нет доступа")
            return False
        return True

    async def check_manager_access(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> bool:
        """Check if user has manager access. Returns True if access granted, False otherwise."""
        is_manager = await self.manager_repo.get_by_telegram_id(msg.user_id, hotel_code)
        if not is_manager:
            hotel_description = await self.get_hotel_description(hotel_code, msg.user_id)
            await self.send_and_remember_message(
                msg.user_id,
                hotel_description,
                adapter,
                state,
                inline_keyboard=adapter.main_menu_keyboard(hotel_code),
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Нет доступа")
            return False
        return True

    async def _handle_hotel_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle hotel selection callback"""

        hotel_code = msg.text.split("_", 1)[-1]
        state.set_selected_hotel(msg.user_id, hotel_code)

        # Check if user is already in registration process
        reg_state = state.get_registration(msg.channel, msg.user_id)
        if reg_state and reg_state.get("step") != "completed":
            state.clear_registration(msg.channel, msg.user_id)
            registration_service = RegistrationService(self.session, adapter)
            await registration_service.start(msg.user_id, resume_context={"hotel": hotel_code})
            await self.session.commit()
            return {"ok": "true"}

        # Check registration
        registered = await self.user_validation.is_registered(msg.user_id, hotel_code)
        if not registered:
            state.clear_registration(msg.channel, msg.user_id)
            # Start registration
            st = state.upsert_registration(msg.channel, msg.user_id)
            c = st.get("context") or {}
            c["resume"] = {"hotel": hotel_code}
            state.set_registration(msg.channel, msg.user_id, context=c)
            registration_service = RegistrationService(self.session, adapter)
            await registration_service.start(msg.user_id, resume_context={"hotel": hotel_code})
            await self.session.commit()

            await adapter.answer_callback(msg.callback_id, "Отель выбран")
            return {"ok": "true"}

        # Show hotel menu
        # Don't clear UI messages - keep history visible
        msg.text = f"{hotel_code}_MENU"
        is_manager = await self.manager_repo.get_by_telegram_id(msg.user_id, hotel_code)
        if is_manager:
            if is_manager.role == RoleEnum.MANAGER.value:
                return await self.menu_service.handle_menu_request(msg, adapter, state)

            # Handle admin main menu - inline logic from
            # _handle_admin_main_menu_callback
            if not await self.check_admin_access(msg, adapter, state):
                return {"ok": "true"}

            keyboard = adapter.admin_menu_keyboard()
            await self.send_and_remember_message(
                msg.user_id,
                ADMIN_MENU_MESSAGE,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, MAIN_MENU_BUTTON)

            return {"ok": "true"}

        # Get last feedback ID for user to show "Дополнить предыдущий отзыв" button
        last_feedback_id = await self.user_repo.get_last_feedback_id(msg.user_id)

        # Show hotel menu with all buttons using main_menu_keyboard
        main_menu_kb = adapter.main_menu_keyboard(hotel_code, last_feedback_id)

        hotel_description = await self.get_hotel_description(hotel_code, msg.user_id)

        await self.send_and_remember_message(
            msg.user_id, hotel_description, adapter, state, inline_keyboard=main_menu_kb
        )

        return {"ok": "true"}

    async def _handle_zone_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        can_leave_feedback: bool,
    ) -> Dict[str, str]:
        """Handle zone selection callback"""
        hotel_code, zone_code = msg.text.split("_SPECIAL_ZONE_", 1)
        logger.info(f"hotel_code: {hotel_code}, zone_code: {zone_code}")

        # If user is a manager, redirect to manager menu
        is_manager = await self.manager_repo.get_by_telegram_id(msg.user_id, hotel_code)
        if is_manager:
            logger.info(f"Manager trying to select zone, redirecting to menu: {msg.user_id}")
            msg.text = f"{hotel_code}_MENU"
            return await self.menu_service.handle_menu_request(msg, adapter, state)

        # Validate active stay
        active_ok = await self.user_validation.has_active_stay(msg.user_id, hotel_code)
        if not active_ok:
            return await self.feedback_limit.show_hotels_list(msg, adapter, state, msg.callback_id)

        # Check feedback limit
        if not can_leave_feedback:
            return await self.feedback_limit.show_limit_message(msg, adapter, state, hotel_code, msg.callback_id)

        # Show rating UI
        state.set_selected_hotel(msg.user_id, hotel_code)
        await state.start_feedback_session(
            msg.channel,
            msg.user_id,
            hotel=hotel_code,
            zone=zone_code,
            rating=None,
            is_new_feedback=True,
        )

        # Don't clear UI messages - keep history visible
        state.clear_compose_prompt(msg.user_id)

        # Show rating UI using UI service
        result = await self.ui_message.show_rating_ui(msg, adapter, state, hotel_code, zone_code)

        return result

    async def _handle_rate_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        parsed_early: dict,
        active_fs: Dict,
    ) -> Dict[str, str]:
        """Handle rate callback"""
        hotel_code = parsed_early.get("hotel_code")
        zone_code = parsed_early.get("zone_code")
        rating = int(msg.text.split("_RATE_")[-1])

        # If user is a manager, redirect to manager menu
        is_manager = await self.manager_repo.get_by_telegram_id(msg.user_id, hotel_code)
        if is_manager:
            logger.info(f"Manager trying to rate, redirecting to menu: {msg.user_id}")
            msg.text = f"{hotel_code}_MENU"
            return await self.menu_service.handle_menu_request(msg, adapter, state)

        if not rating:
            return {"ok": "true"}

        # Get active feedback session
        if not active_fs:
            logger.warning(f"No active feedback session for user {msg.user_id}")
            return await self.ui_message.show_hotel_menu(msg, adapter, state, hotel_code)

        # Record rating into in-memory session
        active_fs["rating"] = rating
        state.touch_feedback_session(msg.channel, msg.user_id)

        try:
            if hotel_code and zone_code:
                # Get user and hotel/zone entities
                user = await self.user_repo.get_by_telegram_id(msg.user_id)

                if user:
                    hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
                    zone = await self.catalog_repo.get_zone_by_code(hotel_code, zone_code)

                    if hotel and zone:
                        # Find active stay
                        stay = await self.user_hotel_repo.get_active_stay(user.id, hotel.id)

                        if stay:
                            # Create feedback
                            fb = await self.feedback_repo.create(user_stay_id=stay.id, zone_id=zone.id, rating=rating)

                            # Commit feedback immediately to get real ID
                            await self.session.commit()
                            logger.info(f"feedback.created_and_committed: {fb.id}")

                            # Set as active feedback for future comments/attachments
                            state.set_feedback_active_id(msg.channel, msg.user_id, str(fb.id))

                            logger.info(f"feedback.set_active: {fb.id}")

                            # Update rating UI to show selected rating by editing the existing message
                            rating_message_id = state.get_rating_message_id(msg.channel, msg.user_id)
                            if rating_message_id:
                                await self.ui_message.edit_rating_ui(
                                    msg,
                                    adapter,
                                    hotel_code,
                                    zone_code,
                                    rating,
                                    rating_message_id,
                                )
                            else:
                                # Fallback: show new message if rating message ID not found
                                await self.ui_message.show_rating_ui(msg, adapter, state, hotel_code, zone_code, rating)

                            # Show compose prompt if not shown yet
                            await self.ui_message.show_compose_prompt(msg, adapter, state, hotel_code)
        except Exception as e:
            logger.error(f"feedback.creation.error: {e}")

        return {"ok": "true"}

    async def _handle_thumb_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        active_fs: Dict,
    ) -> Dict[str, str]:
        """Handle thumb up/down callback"""
        parts = msg.text.split("_")
        hotel_code = parts[0]
        zone_code = parts[1]

        # If user is a manager, redirect to manager menu
        is_manager = await self.manager_repo.get_by_telegram_id(msg.user_id, hotel_code)
        if is_manager:
            logger.info(f"Manager trying to thumb rate, redirecting to menu: {msg.user_id}")
            msg.text = f"{hotel_code}_MENU"
            return await self.menu_service.handle_menu_request(msg, adapter, state)

        # Get active feedback session
        if not active_fs:
            logger.warning(f"No active feedback session for user {msg.user_id}")
            hotel_description = await self.get_hotel_description(hotel_code, msg.user_id)
            await self.send_and_remember_message(msg.user_id, hotel_description, adapter, state)
            return await self.ui_message.show_hotel_menu(msg, adapter, state, hotel_code)

        # Get hotel and zone
        hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
        zone = await self.catalog_repo.get_zone_by_code(hotel_code, zone_code)

        if not hotel or not zone:
            logger.error(f"Hotel or zone not found: {hotel_code}, {zone_code}")
            return {"ok": "true"}

        # Find active stay
        res = await self.session.execute(
            _select(UserHotel)
            .join(User, UserHotel.user_id == User.id)
            .where(
                User.external_user_id == msg.user_id,
                UserHotel.hotel_id == hotel.id,
                UserHotel.close.is_(None),
            )
        )
        stay = res.scalars().first()

        if not stay:
            logger.error(f"No active stay found for user {msg.user_id} in hotel {hotel_code}")
            return {"ok": "true"}

        # Create feedback
        rating = 5 if "THUMB_UP" in msg.text else 1
        fb = await self.feedback_repo.create(
            user_stay_id=stay.id,
            zone_id=zone.id,
            rating=rating,
        )

        # Commit feedback immediately to get real ID
        await self.session.commit()
        logger.info(f"feedback.created_and_committed: {fb.id}")

        # Update active feedback session with rating
        active_fs["rating"] = rating
        state.touch_feedback_session(msg.channel, msg.user_id)

        # Set as active feedback for future comments/attachments
        state.set_feedback_active_id(msg.channel, msg.user_id, str(fb.id))
        logger.info(f"feedback.set_active: {fb.id}")

        # Update rating UI to show selected rating by editing the existing message
        rating_message_id = state.get_rating_message_id(msg.channel, msg.user_id)
        if rating_message_id:
            await self.ui_message.edit_rating_ui(msg, adapter, hotel_code, zone_code, rating, rating_message_id)
        else:
            # Fallback: show new message if rating message ID not found
            await self.ui_message.show_rating_ui(msg, adapter, state, hotel_code, zone_code, rating)

        # Show compose prompt if not shown yet
        await self.ui_message.show_compose_prompt(msg, adapter, state, hotel_code)

        return {"ok": "true"}

    async def _handle_manager_qr_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle manager QR generation callback"""
        # Show zones list to generate QR deep-link per zone
        hotel_code = msg.text.split("_", 1)[0]

        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        # Don't clear UI messages - keep history visible
        # Show zones for selected hotel
        zones_buttons = [
            [{"text": label, "callback_data": f"{hotel_code}_{zone_code}_MGR_QR_ZONE"}]
            for zone_code, label in (await self.catalog_repo.list_zones_for_hotel_code(hotel_code))
        ]
        zones_buttons.append([{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}])

        await self.send_and_remember_message(
            msg.user_id,
            MANAGER_MENU_SELECT_ZONE_FOR_QR_BUTTON,
            adapter,
            state,
            inline_keyboard={"inline_keyboard": zones_buttons},
        )

        return {"ok": "true"}

    async def _handle_manager_qr_zone_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle manager QR zone generation callback"""
        hotel_code = msg.text.split("_", 1)[0]
        zone_code = msg.text.split("_", 2)[1]

        # Don't clear UI messages - keep history visible

        # Build deep link based on channel
        if msg.channel == "telegram":
            bot_user = self.settings.TELEGRAM_BOT_USERNAME
            url = f"https://t.me/{bot_user}?start=hotel={hotel_code}=zone={zone_code}"
        elif msg.channel == "max":
            max_bot_id = self.settings.MAX_BOT_ID
            url = f"https://max.ru/{max_bot_id}?start=hotel={hotel_code}=zone={zone_code}"
        else:
            await adapter.send_message(msg.user_id, "❌ Генерация QR-кода не поддерживается для этого канала")
            return {"ok": "true"}

        # Generate QR code
        png_bytes = generate_qr_png_bytes(url)

        # Get hotel and zone names for caption
        hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
        zone = await self.catalog_repo.get_zone_by_code(hotel_code, zone_code)

        hotel_name = hotel.name if hotel else hotel_code
        zone_name = zone.name if zone else zone_code

        caption = SUCCESS_QR_CODE_GENERATION_MESSAGE.format(hotel_name=hotel_name, zone_name=zone_name)

        # Send QR code
        try:
            message_id = await adapter.send_document_bytes(
                msg.user_id,
                filename=f"QR_{hotel_code}_{zone_code}.png",
                data=png_bytes,
                caption=caption,
                reply_markup=None,
            )

            # Remember the message for UI cleanup
            if message_id:
                state.remember_ui_message(msg.user_id, message_id)

            # Wait 1 second and return to main menu
            await asyncio.sleep(1)

            # Create message for menu service
            menu_msg = IncomingMessage(
                channel=msg.channel,
                user_id=msg.user_id,
                text=f"{hotel_code}_MENU",
                callback_id=None,
                payload={},
            )

            # Return to main menu
            await self.menu_service.handle_menu_request(menu_msg, adapter, state)

        except Exception as e:
            logger.error(f"Error sending QR code: {e}")
            await adapter.send_message(msg.user_id, ERROR_SENDING_QR_CODE_MESSAGE, reply_markup=None)

        return {"ok": "true"}

    async def _handle_manager_reports_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle manager reports callback - show hotel selection"""
        hotel_code = msg.text.split("_", 1)[0]

        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        # Don't clear UI messages - keep history visible

        # Get manager's accessible hotels
        hotel_codes = await self.manager_repo.list_hotels(msg.user_id)

        # Get full hotel information (name and code) for each accessible hotel
        hotels = []
        for hotel_code_item in hotel_codes:
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code_item)
            if hotel:
                hotels.append({"id": hotel.id, "code": hotel.short_name, "name": hotel.name})

        # Create keyboard with hotel selection
        keyboard = adapter.manager_hotels_keyboard(hotels, hotel_code)

        await self.send_and_remember_message(
            msg.user_id,
            MANAGER_MENU_SELECT_HOTEL_FOR_REPORT_BUTTON,
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Выберите отель")

        return {"ok": "true"}

    async def _handle_manager_report_hotel_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle manager report hotel selection callback"""
        logger.info("Manager report hotel callback")

        # Extract hotel code from callback data
        # Format: {hotel_code}_MGR_REPORT
        hotel_code = msg.text.split("_")[0]
        hotel_short_name = msg.text.split("_")[3]

        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        logger.info(f"Hotel code: {hotel_code}, hotel short name: {hotel_short_name}")

        # Don't clear UI messages - keep history visible

        # Show period selection for selected hotel
        keyboard = adapter.report_period_keyboard(hotel_code, hotel_short_name)

        # Get hotel name for display
        hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
        hotel_name = hotel.name if hotel else hotel_short_name

        await self.send_and_remember_message(
            msg.user_id,
            SELECT_PERIOD_FOR_REPORTING_MESSAGE.format(hotel_name=hotel_name),
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Выберите период")

        return {"ok": "true"}

    async def _handle_manager_report_all_hotels_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle manager report all hotels callback"""

        hotel_code = msg.text.split("_", 1)[0]

        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        # Don't clear UI messages - keep history visible

        # Show period selection for all hotels
        keyboard = adapter.report_period_keyboard(hotel_code, "ALL")

        await self.send_and_remember_message(
            msg.user_id,
            MANAGER_MENU_SELECT_ALL_HOTELS_FOR_REPORT_BUTTON,
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Выберите период")

        return {"ok": "true"}

    async def _handle_manager_report_period_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle manager report period selection callback"""

        # Parse callback data to extract period and hotel info
        # Format: {hotel_code}_MGR_REPORT_{hotel_short_name}_{PERIOD}
        parts = msg.text.split("_")

        hotel_code = parts[0]
        report_type = parts[4]  # HOTEL or ALL
        period = parts[3]  # WEEK, MONTH, HALF_YEAR, YEAR
        logger.info(f"Report type: {report_type}, period: {period}, hotel code: {hotel_code}")

        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        # Don't clear UI messages - keep history visible

        # Generate report based on period and scope
        end_date = datetime.now()
        if period == "WEEK":
            start_date = end_date - timedelta(days=7)
        elif period == "MONTH":
            start_date = end_date - timedelta(days=30)
        elif period == "HALF-YEAR":
            start_date = end_date - timedelta(days=180)
        elif period == "YEAR":
            start_date = end_date - timedelta(days=365)
        else:
            start_date = end_date - timedelta(days=30)  # Default to month

        try:
            # Get hotels for report
            if report_type == "ALL":
                # Get hotel codes and convert to hotel info
                hotel_codes = await self.manager_repo.list_hotels(msg.user_id)
            else:
                # Single hotel - get hotel info
                hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
                if not hotel:
                    await adapter.send_message(msg.user_id, NO_HOTEL_FOUND_MESSAGE)
                    return {"ok": "true"}
                hotel_codes = [hotel.short_name]

            # Generate XLSX report
            xlsx_bytes = await self.reporting_service.export_xlsx(
                hotels_scope=hotel_codes, date_from=start_date, date_to=end_date
            )

            hotel_info = await self.catalog_repo.get_hotel_by_code(hotel_code)

            scope_name = (
                "По всем доступным отелям"
                if report_type == "ALL"
                else f"Отель {hotel_info.name if hotel_info else hotel_code}"
            )

            # Format period with dates (same format as custom period)
            period_text = f"с {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')}"

            caption = f"✅ Отчет готов\n\n📊 Период: {period_text}\n🏨 {scope_name}"

            message_id = await adapter.send_document_bytes(
                msg.user_id,
                filename=f"feedback_report_{period}_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                data=xlsx_bytes,
                caption=caption,
            )

            # Remember the message for UI cleanup
            if message_id:
                state.remember_ui_message(msg.user_id, message_id)

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Отчет готов")

            # Small delay to ensure report message is sent
            await asyncio.sleep(1)
            menu_msg = IncomingMessage(
                channel=msg.channel,
                user_id=msg.user_id,
                text=f"{hotel_code}_MENU",
                callback_id=None,
                payload={},
            )
            await self.menu_service.handle_menu_request(menu_msg, adapter, state)

        except Exception as e:
            logger.error(f"Error generating report: {e}")
            await adapter.send_message(msg.user_id, ERROR_REPORTING_MESSAGE)
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_manager_report_custom_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle manager custom period selection callback"""

        # Parse callback data to extract hotel info
        # Format: {hotel_code}_MGR_REPORT_CUSTOM_{hotel_short_name}
        parts = msg.text.split("_")
        hotel_code = parts[0]
        hotel_short_name = parts[4] if len(parts) > 4 else ""

        logger.info(f"Custom report requested for hotel: {hotel_code}, short_name: {hotel_short_name}")

        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        # Store hotel info in state for later use when user inputs dates
        state.set_user_state(msg.user_id, "custom_report_hotel_code", hotel_code)
        state.set_user_state(msg.user_id, "custom_report_hotel_short_name", hotel_short_name)
        state.set_user_state(msg.user_id, "awaiting_custom_period", "true")

        # Send instruction message with back button
        keyboard = {"inline_keyboard": [[{"text": BACK_BUTTON, "callback_data": f"{hotel_code}_MGR_REPORTS"}]]}

        await self.send_and_remember_message(
            msg.user_id,
            CUSTOM_PERIOD_INPUT_MESSAGE,
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Введите даты")

        return {"ok": "true"}

    def _format_feedback_date(self, created_at: datetime, hotel_timezone: str) -> str:
        """Format feedback creation date for display"""
        if not created_at:
            return ""

        if created_at.tzinfo is None:
            raise ValueError(
                "created_at must be timezone-aware. "
                "Convert it using convert_to_timezone() before calling this method."
            )

        now = convert_to_timezone(datetime.now(timezone.utc), hotel_timezone)
        diff = now - created_at

        if diff.days == 0:
            return "сегодня"
        elif diff.days == 1:
            return "вчера"
        elif diff.days < 7:
            return f"{diff.days} дня назад"
        else:
            return created_at.strftime("%d.%m.%Y")

    def _split_text_into_blocks(self, text: str, max_length: int = 4000) -> list[str]:
        """Split text into blocks that fit within max_length"""
        if len(text) <= max_length:
            return [text]

        blocks = []
        current_block = ""

        # Split by lines first
        lines = text.split("\n")

        for line in lines:
            # If adding this line would exceed max_length, start a new block
            if len(current_block) + len(line) + 1 > max_length:
                if current_block:
                    blocks.append(current_block.strip())
                    current_block = ""

            # If single line is too long, split it
            if len(line) > max_length:
                if current_block:
                    blocks.append(current_block.strip())
                    current_block = ""

                # Split long line by words
                words = line.split(" ")
                for word in words:
                    if len(current_block) + len(word) + 1 > max_length:
                        if current_block:
                            blocks.append(current_block.strip())
                            current_block = ""
                    current_block += word + " "
            else:
                current_block += line + "\n"

        # Add remaining text
        if current_block.strip():
            blocks.append(current_block.strip())

        return blocks

    def _format_all_comments(self, comments: list[str]) -> str:
        """Format all comments into a single text without numbering"""
        if not comments:
            return ""

        valid_comments = [
            comment for comment in comments if comment and comment.strip() and comment.strip().lower() != "none"
        ]
        if not valid_comments:
            return ""

        # Simply concatenate comments with line breaks
        return "\n".join(f'"{comment}"' for comment in valid_comments)

    async def _handle_last_feedback_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle last feedback callback"""
        try:
            # Extract feedback_id from callback data
            feedback_id = msg.text.split("LASTFEEDBACK_")[-1]
            logger.info(f"Processing last feedback callback: {feedback_id}")

            # Get all comments and zone info
            result = await self.feedback_repo.get_all_comments_and_zone(feedback_id)

            if not result:
                await adapter.send_message(msg.user_id, UNSUCCESSFULL_SEARCH_INFO_LAST_FEEDBACK_MESSAGE)
                if msg.callback_id:
                    await adapter.answer_callback(msg.callback_id, "Ошибка")
                return {"ok": "true"}

            comments = result.comments
            zone_name = result.zone
            rating = result.rating
            hotel_name = result.name
            hotel_code = result.short_name
            is_adult = result.is_adult
            created_at = result.created_at
            hotel_timezone = result.timezone

            created_at = convert_to_timezone(created_at, hotel_timezone)

            if not zone_name or not rating:
                await adapter.send_message(msg.user_id, UNSUCCESSFULL_SEARCH_INFO_LAST_FEEDBACK_MESSAGE)
                if msg.callback_id:
                    await adapter.answer_callback(msg.callback_id, "Ошибка")
                return {"ok": "true"}

            # Don't clear UI messages - keep history visible

            # Send first message with rating, zone and hotel
            hotel_info = f" в отеле {hotel_name}" if hotel_name else ""
            date_info = self._format_feedback_date(created_at, hotel_timezone)
            date_text = f" оставлена {date_info}" if date_info else ""

            # Create keyboard with finish feedback addition button
            keyboard = adapter.compose_feedback_addition_keyboard(hotel_code)

            if not is_adult:
                rating_display = "👍" if rating == 5 else "👎"
            else:
                rating_display = f"⭐️{rating}"

            if comments:
                # Send context message with rating and zone info
                context_message = FEEDBACK_ADDITION_CONTEXT_MESSAGE.format(
                    rating_display=rating_display,
                    zone_name=zone_name,
                    hotel_info=hotel_info,
                    date_text=date_text,
                )

                await self.send_and_remember_message(
                    msg.user_id,
                    context_message,
                    adapter,
                    state,
                )

                # Format all comments
                formatted_comments = self._format_all_comments(comments)

                # Split comments into blocks if they're too long
                comment_blocks = self._split_text_into_blocks(formatted_comments)

                # Send each block as a separate message
                for i, block in enumerate(comment_blocks):
                    if i == len(comment_blocks) - 1:
                        # Last block - add instruction and button
                        comment_display = FEEDBACK_ADDITION_COMMENT_DISPLAY_MESSAGE.format(block=block)
                        instruction_message_id = await self.send_and_remember_message(
                            msg.user_id,
                            comment_display,
                            adapter,
                            state,
                            inline_keyboard=keyboard,
                        )
                        # Check if button was already pressed and update if needed
                        if instruction_message_id:
                            await self.button_state.update_feedback_message_if_needed(
                                msg, adapter, instruction_message_id, hotel_code
                            )
                    else:
                        # Not the last block - just send the comments
                        await self.send_and_remember_message(msg.user_id, f"💬 {block}", adapter, state)
            else:
                # No comments exist, just show rating and ask for new comment
                context_message = FEEDBACK_ADDITION_NO_COMMENTS_DISPLAY_MESSAGE.format(
                    rating_display=rating_display,
                    zone_name=zone_name,
                    hotel_info=hotel_info,
                    date_text=date_text,
                )

                # Save instruction message ID for future editing
                instruction_message_id = await self.send_and_remember_message(
                    msg.user_id,
                    context_message,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
                # Check if button was already pressed and update if needed
                if instruction_message_id:
                    await self.button_state.update_feedback_message_if_needed(
                        msg, adapter, instruction_message_id, hotel_code
                    )

            # Set this feedback as active for continuation
            await state.start_feedback_session(
                msg.channel,
                msg.user_id,
                hotel=hotel_code or "",
                zone=zone_name or "",
                rating=rating,
                active_feedback_id=feedback_id,
                is_new_feedback=False,
            )

            # Save instruction message ID for future editing (after session creation)
            if instruction_message_id:
                state.set_instruction_message_id(msg.channel, msg.user_id, instruction_message_id)
                logger.info(
                    f"feedback.addition.instruction_id_saved: user_id={msg.user_id}, "
                    f"instruction_message_id={instruction_message_id}"
                )
            else:
                logger.warning(f"feedback.addition.no_instruction_id: user_id={msg.user_id}")

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Отзыв загружен")

        except Exception as e:
            logger.error(f"Error handling last feedback callback: {e}")
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")
        return {"ok": "true"}

    async def _handle_manager_negative_feedbacks_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager negative feedbacks callback"""
        hotel_code = msg.text.split("_", 1)[0]

        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        try:
            await self._clear_all_user_media_messages(msg.user_id, adapter, state)

            # Get hotel ID
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                return await self.feedback_limit.show_hotels_list(msg, adapter, state, msg.callback_id)

            # Get negative feedbacks (first page)
            feedbacks, has_next = await self.feedback_repo.get_negative_feedbacks_paginated(
                hotel.id, page=1, per_page=5
            )

            if not feedbacks:
                await self.send_and_remember_message(
                    msg.user_id,
                    MANAGER_MENU_NO_NEGATIVE_FEEDBACKS_MESSAGE,
                    adapter,
                    state,
                    inline_keyboard=adapter.manager_menu_keyboard(hotel_code),
                )
            else:
                keyboard = adapter.negative_feedbacks_keyboard(feedbacks, hotel_code, page=1, has_next=has_next)
                await self.send_and_remember_message(
                    msg.user_id,
                    MANAGER_MENU_NEGATIVE_FEEDBACKS_PAGE_MESSAGE.format(page=1),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Отзывы загружены")

            return {"ok": "true"}

        except Exception as e:
            logger.error(f"Error handling manager negative feedbacks callback: {e}")
            if msg.callback_id:
                try:
                    await adapter.answer_callback(msg.callback_id, "Ошибка")
                except Exception:
                    pass
            return {"ok": "true"}

    async def _handle_manager_negative_feedbacks_page_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager negative feedbacks pagination callback"""
        parts = msg.text.split("_PAGE_")
        hotel_code = parts[0].replace("_MGR_NEGATIVE_FEEDBACKS", "")

        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        try:
            # Extract hotel_code and page from callback data
            # Format: GRN_MGR_NEGATIVE_FEEDBACKS_PAGE_2
            page = int(parts[1])

            # Don't clear UI messages - keep history visible

            # Get hotel ID
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                return await self.feedback_limit.show_hotels_list(msg, adapter, state, msg.callback_id)

            # Get negative feedbacks for the requested page
            feedbacks, has_next = await self.feedback_repo.get_negative_feedbacks_paginated(
                hotel.id, page=page, per_page=5
            )

            if not feedbacks:
                await self.send_and_remember_message(
                    msg.user_id,
                    MANAGER_MENU_NO_NEGATIVE_FEEDBACKS_MESSAGE,
                    adapter,
                    state,
                    inline_keyboard=adapter.manager_menu_keyboard(hotel_code),
                )
            else:
                keyboard = adapter.negative_feedbacks_keyboard(feedbacks, hotel_code, page=page, has_next=has_next)
                await self.send_and_remember_message(
                    msg.user_id,
                    MANAGER_MENU_NEGATIVE_FEEDBACKS_PAGE_MESSAGE.format(page=page),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Страница загружена")

            return {"ok": "true"}

        except Exception as e:
            logger.error(f"Error handling manager negative feedbacks page callback: {e}")
            if msg.callback_id:
                try:
                    await adapter.answer_callback(msg.callback_id, "Ошибка")
                except Exception:
                    pass
            return {"ok": "true"}

    async def _handle_manager_feedback_detail_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager feedback detail callback"""
        hotel_code = msg.text.split("_", 1)[0]
        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        try:
            # Extract feedback_id from callback data
            feedback_id = msg.text.split("MGR_FEEDBACK_")[-1]

            # Get feedback details
            feedback = await self.feedback_repo.get_by_id(feedback_id)
            if not feedback:
                await adapter.send_message(msg.user_id, "Отзыв не найден")
                return {"ok": "true"}

            # Get zone, hotel info and guest phone number
            result = await self.session.execute(
                _select(
                    Zone.name.label("zone_name"),
                    Hotel.name.label("hotel_name"),
                    Hotel.short_name.label("hotel_code"),
                    User.phone_number.label("guest_phone"),
                    Hotel.timezone.label("timezone"),
                )
                .select_from(Feedback)
                .join(UserHotel, Feedback.user_stay_id == UserHotel.id)
                .join(Hotel, UserHotel.hotel_id == Hotel.id)
                .join(Zone, Feedback.zone_id == Zone.id)
                .join(User, UserHotel.user_id == User.id)
                .where(Feedback.id == feedback.id)
            )
            row = result.first()

            if not row:
                await adapter.send_message(msg.user_id, "Информация об отзыве не найдена")
                return {"ok": "true"}

            info = FeedbackInfoModel.model_validate(row._mapping)

            # Get comments for this feedback
            comments_result = await self.session.execute(
                _select(Comment.comment, Comment.created_at)
                .join(FeedbackComment, Comment.id == FeedbackComment.comment_id)
                .where(FeedbackComment.feedback_id == feedback.id)
                .order_by(Comment.created_at)
            )
            comments = comments_result.all()

            # Get analysis result for this feedback
            analysis_result = await self.session.execute(
                _select(AnalysisResult.recommendation, AnalysisResult.root_causes).where(
                    AnalysisResult.feedback_id == feedback.id
                )
            )
            analysis_row = analysis_result.first()

            # Format feedback details
            status_text = {
                "OPENED": "Открыт",
                "IN_PROGRESS": "В работе",
                "SOLVED": "Решено",
                "REJECTED": "Отклонено",
            }.get(feedback.status.value.upper(), feedback.status.value.upper())

            recommendation = analysis_row[0] if analysis_row else ""
            # Format root_causes: handle both list and string formats
            raw_root_causes = analysis_row[1] if analysis_row and analysis_row[1] else None
            if raw_root_causes:
                if isinstance(raw_root_causes, list):
                    # If it's already a list, just format it
                    root_causes = ", ".join(str(cause).replace("_", " ").strip() for cause in raw_root_causes if cause)
                else:
                    # If it's a string, remove braces and format
                    cleaned = str(raw_root_causes).strip("{}")
                    root_causes = ", ".join(
                        cause.replace("_", " ").strip() for cause in cleaned.split(",") if cause.strip()
                    )
            else:
                root_causes = ""

            feedback_created_at = convert_to_timezone(feedback.created_at, info.timezone)
            feedback_created_at_formatted = feedback_created_at.strftime("%d.%m.%Y %H:%M")
            message = "<b>Детали отзыва</b>\n\n"
            message += f"<b>Отель:</b> {info.hotel_name}\n"
            message += f"<b>Зона:</b> {info.zone_name}\n"
            message += f"<b>Оценка:</b> {'⭐' * feedback.rating}\n"
            message += f"<b>Статус:</b> {status_text}\n"
            message += f"<b>Дата:</b> {feedback_created_at_formatted}\n\n"
            message += f"<b>Номер гостя:</b> {info.guest_phone}\n\n"

            if comments:
                message += "<b>Комментарий:</b>\n"
                for comment_text, _ in comments:
                    message += f"- {comment_text}\n"
                message += "\n"
            else:
                message += "<i>Комментариев нет</i>\n\n"

            message += f"<b>Рекомендация ИИ:</b>\n - {recommendation}\n\n"
            message += f"<b>Причины:</b>\n<i>{root_causes}</i>\n\n"

            # Load and send attachments (images/audio/documents) for this feedback
            try:
                attachments = await self.feedback_repo.list_attachments_for_feedback(feedback.id)

                # Separate images from others (send images via media groups in chunks of 10)
                image_items = []
                other_items = []
                for at in attachments:
                    # Download from S3
                    data = await self.storage.download_bytes(at.s3_url)
                    media_kind = at.media_type.name.lower()
                    if media_kind == "image":
                        image_items.append(
                            {
                                "kind": "image",
                                "filename": f"{feedback.id}_{at.id}.png",
                                "data": data,
                            }
                        )
                    else:
                        # Fallback to individual send for non-images
                        if media_kind == "audio":
                            fname = f"{feedback.id}.ogg"
                        elif media_kind == "video":
                            fname = f"{feedback.id}.mp4"
                        else:
                            fname = f"{feedback.id}.bin"
                        other_items.append((fname, data))

                # Send images in chunks of up to 10 using media groups
                if image_items:
                    caption = MANAGER_MENU_FEEDBACK_MEDIA_FILES_MESSAGE
                    for i in range(0, len(image_items), 10):
                        chunk = image_items[i : i + 10]
                        msg_ids = await adapter.send_media_group_bytes(msg.user_id, chunk, caption=caption)
                        if msg_ids:
                            for mid in msg_ids:
                                state.remember_ui_message(msg.user_id, mid)
                                state.add_feedback_media_message(msg.user_id, feedback.id, mid)

                # Send non-images individually
                for fname, data in other_items:
                    mid = await adapter.send_document_bytes(msg.user_id, fname, data)
                    if mid:
                        state.remember_ui_message(msg.user_id, mid)
                        state.add_feedback_media_message(msg.user_id, feedback.id, mid)

            except Exception:
                pass

            # Create keyboard with status buttons
            keyboard = adapter.create_status_keyboard(feedback.id, hotel_code, feedback.status)

            message_id = await self.send_and_remember_message(
                msg.user_id, message, adapter, state, inline_keyboard=keyboard
            )

            # Save the message ID for this specific feedback
            if message_id:
                state.set_feedback_detail_message_id(msg.user_id, feedback.id, message_id)

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Отзыв загружен")

            return {"ok": "true"}

        except Exception as e:
            logger.error(f"Error handling manager feedback detail callback: {e}")
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")
            return {"ok": "true"}

    async def _handle_manager_prompts_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager prompts callback"""
        hotel_code = msg.text.split("_", 1)[0]
        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        # Don't clear UI messages - keep history visible
        try:
            # Get hotel first to validate it exists
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                return await self.feedback_limit.show_hotels_list(msg, adapter, state, msg.callback_id)

            # Get zones for hotel
            zones = await self.catalog_repo.list_zones_for_hotel_code(hotel_code)

            if not zones:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_MESSAGE,
                    adapter,
                    state,
                    inline_keyboard=adapter.manager_menu_keyboard(hotel_code),
                )
            else:
                # Convert to dict format for keyboard
                zones_data = [{"code": code, "name": name} for code, name in zones]
                keyboard = adapter.zones_prompts_keyboard(zones_data, hotel_code)
                await self.send_and_remember_message(
                    msg.user_id,
                    MANAGER_MENU_SELECT_ZONE_FOR_PROMPTS_BUTTON,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            return {"ok": "true"}

        except Exception as e:
            logger.error(f"Error handling manager prompts callback: {e}")
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")
            return {"ok": "true"}

    async def _handle_manager_prompt_zone_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager prompt zone callback"""
        parts = msg.text.split("_")
        hotel_code = parts[0]
        zone_code = parts[-1]
        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        state.clear_editing_prompt(msg.user_id)
        # Don't clear UI messages - keep history visible
        try:
            # Get hotel first to validate it exists
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                return await self.feedback_limit.show_hotels_list(msg, adapter, state, msg.callback_id)

            # Get zone info
            zone = await self.catalog_repo.get_zone_by_code(hotel_code, zone_code)
            if not zone:
                await adapter.send_message(msg.user_id, NO_ZONE_FOUND_MESSAGE)
                return {"ok": "true"}

            # Get scenario prompt for this zone
            result = await self.session.execute(
                _select(Scenario.prompt)
                .join(Hotel, Scenario.hotel_id == Hotel.id)
                .join(Zone, Scenario.zone_id == Zone.id)
                .where(Hotel.short_name == hotel_code, Zone.short_name == zone_code)
            )
            row = result.scalar()

            if row:  # If prompt exists
                prompt_text = f"- {row}"
            else:
                prompt_text = "Инструкция для зоны не настроена"

            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": EDIT_PROMPT_BUTTON,
                            "callback_data": f"{hotel_code}_MGR_EDIT_PROMPT_{zone_code}",
                        }
                    ],
                    [
                        {
                            "text": RESET_PROMPT_BUTTON,
                            "callback_data": f"{hotel_code}_MGR_RESET_PROMPT_{zone_code}",
                        }
                    ],
                    [{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}],
                ]
            }

            await self.send_and_remember_message(
                msg.user_id,
                ZONE_PROMPT_DESCRIPTION_MESSAGE.format(zone_name=zone.name, prompt_text=prompt_text),
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Инструкция загружена")

        except Exception as e:
            logger.error(f"Error handling manager prompt zone callback: {e}")
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")
        return {"ok": "true"}

    async def _handle_manager_edit_prompt_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager edit prompt callback"""
        parts = msg.text.split("_")
        hotel_code = parts[0]
        zone_code = parts[-1]  # Last part is zone_code
        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        try:
            # Get hotel first to validate it exists
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                return await self.feedback_limit.show_hotels_list(msg, adapter, state, msg.callback_id)

            # Get zone info
            zone = await self.catalog_repo.get_zone_by_code(hotel_code, zone_code)
            if not zone:
                await adapter.send_message(msg.user_id, "Зона не найдена")
                return {"ok": "true"}

            # Don't clear UI messages - keep history visible

            # Set editing state for this user
            state.set_editing_prompt(msg.user_id, hotel_code, zone_code)

            # Get current prompt from database
            result = await self.session.execute(
                _select(Scenario.prompt)
                .join(Hotel, Scenario.hotel_id == Hotel.id)
                .join(Zone, Scenario.zone_id == Zone.id)
                .where(Hotel.short_name == hotel_code, Zone.short_name == zone_code)
            )
            row = result.first()

            if row:  # If prompt exists
                current_prompt = row[0]
            else:
                current_prompt = "Инструкция для зоны не настроена"

            # Create keyboard with cancel button
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": CANCEL_BUTTON,
                            "callback_data": f"{hotel_code}_MGR_PROMPT_ZONE_{zone_code}",
                        }
                    ]
                ]
            }

            message_id = await self.send_and_remember_message(
                msg.user_id,
                EDIT_PROMPT_MESSAGE.format(zone_name=zone.name, current_prompt=current_prompt),
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if message_id:
                state.set_editing_prompt_message_id(msg.user_id, message_id)

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Отправьте новую инструкцию")

        except Exception as e:
            logger.error(f"Error handling manager edit prompt callback: {e}")
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_manager_save_prompt_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager save prompt callback"""

        parts = msg.text.split("_")
        hotel_code = parts[0]
        zone_code = parts[-1]  # Last part is zone_code
        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        try:
            # Get editing state
            editing_state = state.get_editing_prompt(msg.user_id)
            if (
                not editing_state
                or editing_state.get("hotel_code") != hotel_code
                or editing_state.get("zone_code") != zone_code
            ):
                await adapter.send_message(msg.user_id, NO_EDITING_SESSION_FOUND_MESSAGE)
                return {"ok": "true"}

            # Get new prompt from message text
            new_prompt = msg.text
            if not new_prompt:
                await adapter.send_message(msg.user_id, NO_EMPTY_PROMPT_MESSAGE)
                return {"ok": "true"}

            zone_info = await self.catalog_repo.get_zone_by_code(hotel_code, zone_code)

            if not zone_info:
                await adapter.send_message(msg.user_id, NO_HOTEL_OR_ZONE_FOUND_MESSAGE)
                return {"ok": "true"}

            hotel_id, zone_id = zone_info.hotel_id, zone_info.id

            # Upsert scenario in one operation
            stmt = insert(Scenario).values(
                hotel_id=hotel_id,
                zone_id=zone_id,
                prompt=new_prompt,
                default_prompt=new_prompt,
                updated_at=datetime.now(timezone.utc),
            )

            # On conflict, update the prompt
            stmt = stmt.on_conflict_do_update(
                index_elements=["hotel_id", "zone_id"],
                set_={
                    "prompt": stmt.excluded.prompt,
                    "updated_at": stmt.excluded.updated_at,
                },
            )

            await self.session.execute(stmt)
            await self.session.commit()

            # Clear editing state
            state.clear_editing_prompt(msg.user_id)

            # Don't clear UI messages - keep history visible

            # Show success message with updated prompt
            keyboard = {"inline_keyboard": [[{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}]]}

            await self.send_and_remember_message(
                msg.user_id,
                SUCCESS_PROMPT_UPDATING_MESSAGE.format(
                    zone_name=zone_info.name if zone_info else zone_code,
                    prompt=new_prompt,
                ),
                adapter,
                state,
                inline_keyboard=keyboard,
            )

        except Exception as e:
            logger.error(f"Error handling manager save prompt callback: {e}")
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_manager_reset_prompt_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager reset prompt callback"""

        parts = msg.text.split("_")
        hotel_code = parts[0]
        zone_code = parts[-1]  # Last part is zone_code
        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        try:
            # Get hotel first to validate it exists
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                return await self.feedback_limit.show_hotels_list(msg, adapter, state, msg.callback_id)

            # Get zone info
            zone = await self.catalog_repo.get_zone_by_code(hotel_code, zone_code)
            if not zone:
                await adapter.send_message(msg.user_id, "Зона не найдена")
                return {"ok": "true"}

            result = await self.session.execute(
                _select(Hotel.id, Zone.id.label("zone_id"), Scenario.default_prompt)
                .join(Zone, Zone.hotel_id == Hotel.id)
                .outerjoin(
                    Scenario,
                    (Scenario.hotel_id == Hotel.id) & (Scenario.zone_id == Zone.id),
                )
                .where(Hotel.short_name == hotel_code, Zone.short_name == zone_code)
            )
            row = result.first()

            if not row:
                await adapter.send_message(msg.user_id, NO_HOTEL_OR_ZONE_FOUND_MESSAGE)
                return {"ok": "true"}

            hotel_id, zone_id, default_prompt = row

            # If no default_prompt exists, we can't reset
            if not default_prompt:
                await adapter.send_message(msg.user_id, NO_DEFAULT_VALUE_FOR_RESET_MESSAGE)
                return {"ok": "true"}

            # Check if scenario exists
            existing_scenario = await self.session.execute(
                _select(Scenario).where(Scenario.hotel_id == hotel_id, Scenario.zone_id == zone_id)
            )
            scenario = existing_scenario.scalars().first()

            if scenario:
                # Update existing scenario
                scenario.prompt = default_prompt
                scenario.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                # Create new scenario
                scenario = Scenario(
                    hotel_id=hotel_id,
                    zone_id=zone_id,
                    prompt=default_prompt,
                    default_prompt=default_prompt,
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                self.session.add(scenario)

            await self.session.commit()

            # Don't clear UI messages - keep history visible

            # Show success message
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": EDIT_PROMPT_BUTTON,
                            "callback_data": f"{hotel_code}_MGR_EDIT_PROMPT_{zone_code}",
                        }
                    ],
                    [
                        {
                            "text": RESET_PROMPT_BUTTON,
                            "callback_data": f"{hotel_code}_MGR_RESET_PROMPT_{zone_code}",
                        }
                    ],
                    [{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}],
                ]
            }

            await self.send_and_remember_message(
                msg.user_id,
                RESET_PROMPT_MESSAGE.format(zone_name=zone.name, prompt=default_prompt),
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Промпт сброшен")

        except Exception as e:
            logger.error(f"Error handling manager reset prompt callback: {e}")
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_manager_status_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle manager status change callback"""
        hotel_code = msg.text.split("_", 1)[0]
        if not await self.check_manager_access(msg, adapter, state, hotel_code):
            return {"ok": "true"}

        try:
            # Format: {hotel_code}_MGR_STATUS_{feedback_id}_{new_status}
            # Example: ALN_MGR_STATUS_123e4567-e89b-12d3-a456-426614174000_in_progress

            parts = msg.text.split("_MGR_STATUS_")
            if len(parts) != 2:
                await adapter.send_message(msg.user_id, INVALID_COMMAND_FORMAT_MESSAGE)
                return {"ok": "true"}

            # Extract feedback_id and new_status from the second part
            status_parts = parts[1].split("_")
            if len(status_parts) < 2:
                await adapter.send_message(msg.user_id, INVALID_COMMAND_FORMAT_MESSAGE)
                return {"ok": "true"}

            feedback_id = status_parts[0]
            new_status = "_".join(status_parts[1:])

            logger.info(f"Status change: feedback_id={feedback_id}, new_status={new_status}")

            # Validate status
            try:
                status_enum = FeedbackStatus(new_status)
                logger.info(f"Status enum created: {status_enum}")
            except ValueError as e:
                logger.error(f"Invalid status: {new_status}, error: {e}")
                await adapter.send_message(msg.user_id, f"Неверный статус: {new_status}")
                return {"ok": "true"}

            # Update feedback status in database

            stmt = update(Feedback).where(Feedback.id == feedback_id).values(status=status_enum)
            result = await self.session.execute(stmt)

            if result.rowcount == 0:
                await adapter.send_message(msg.user_id, NO_FEEDBACK_FOUND_MESSAGE)
                return {"ok": "true"}

            await self.session.commit()

            if status_enum in [FeedbackStatus.SOLVED, FeedbackStatus.REJECTED]:
                await self._delete_feedback_attachments_from_storage(feedback_id)

            # Get status text for callback answer
            status_text = {
                "opened": "Открыт",
                "in_progress": "В работе",
                "solved": "Решено",
                "rejected": "Отклонено",
            }.get(new_status, new_status)

            # Answer callback first
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, f"Статус изменен на {status_text}")

            # Check if we have an existing message for this feedback
            existing_message_id = state.get_feedback_detail_message_id(msg.user_id, feedback_id)

            if existing_message_id:
                # Edit existing message instead of creating new one
                await self._edit_feedback_detail_message(
                    msg.user_id,
                    feedback_id,
                    existing_message_id,
                    adapter,
                    state,
                    hotel_code,
                )
            else:
                # Fallback: create new message if no existing message found
                detail_msg = IncomingMessage(
                    channel=msg.channel,
                    user_id=msg.user_id,
                    text=f"{hotel_code}_MGR_FEEDBACK_{feedback_id}",
                    callback_id=None,
                    payload=None,
                )
                return await self._handle_manager_feedback_detail_callback(detail_msg, adapter, state)

            return {"ok": "true"}

        except Exception as e:
            logger.error(f"Error handling manager status callback: {e}")
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")
            return {"ok": "true"}

    async def _delete_feedback_attachments_from_storage(self, feedback_id: str) -> None:
        """Delete all attachments from S3 storage for a feedback"""
        try:
            attachments = await self.feedback_repo.list_attachments_for_feedback(feedback_id)

            if not attachments:
                logger.info(f"No attachments found for feedback {feedback_id}")
                return

            # Delete each attachment from S3
            deleted_count = 0
            for attachment in attachments:
                try:
                    await self.storage.delete_object(attachment.s3_url)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete attachment {attachment.s3_url} from S3: {e}")

            logger.info(f"Deleted {deleted_count} attachments from S3 for feedback {feedback_id}")

        except Exception as e:
            logger.error(f"Error deleting attachments from storage: {e}")

    async def _edit_feedback_detail_message(
        self,
        user_id: str,
        feedback_id: str,
        message_id: int,
        adapter: ChannelAdapter,
        state: InMemoryState,
        hotel_code: str,
    ) -> None:
        """Edit existing feedback detail message with updated status"""
        try:
            # Get feedback details
            feedback = await self.feedback_repo.get_by_id(feedback_id)
            if not feedback:
                logger.error(f"Feedback not found: {feedback_id}")
                return

            # Get zone, hotel info and guest phone number
            result = await self.session.execute(
                _select(
                    Zone.name.label("zone_name"),
                    Hotel.name.label("hotel_name"),
                    Hotel.short_name.label("hotel_code"),
                    User.phone_number.label("guest_phone"),
                    Hotel.timezone.label("timezone"),
                )
                .select_from(Feedback)
                .join(UserHotel, Feedback.user_stay_id == UserHotel.id)
                .join(Hotel, UserHotel.hotel_id == Hotel.id)
                .join(Zone, Feedback.zone_id == Zone.id)
                .join(User, UserHotel.user_id == User.id)
                .where(Feedback.id == feedback.id)
            )
            row = result.first()

            if not row:
                logger.error(f"Feedback info not found: {feedback_id}")
                return

            info = FeedbackInfoModel.model_validate(row._mapping)

            # Get comments for this feedback
            comments_result = await self.session.execute(
                _select(Comment.comment, Comment.created_at)
                .join(FeedbackComment, Comment.id == FeedbackComment.comment_id)
                .where(FeedbackComment.feedback_id == feedback.id)
                .order_by(Comment.created_at)
            )
            comments = comments_result.all()

            # Get analysis result for this feedback
            analysis_result = await self.session.execute(
                _select(AnalysisResult.recommendation, AnalysisResult.root_causes).where(
                    AnalysisResult.feedback_id == feedback.id
                )
            )
            analysis_row = analysis_result.first()

            # Format feedback details
            status_text = {
                "OPENED": "Открыт",
                "IN_PROGRESS": "В работе",
                "SOLVED": "Решено",
                "REJECTED": "Отклонено",
            }.get(feedback.status.value.upper(), feedback.status.value.upper())

            recommendation = analysis_row[0] if analysis_row else ""
            # Format root_causes: handle both list and string formats
            raw_root_causes = analysis_row[1] if analysis_row and analysis_row[1] else None
            if raw_root_causes:
                if isinstance(raw_root_causes, list):
                    # If it's already a list, just format it
                    root_causes = ", ".join(str(cause).replace("_", " ").strip() for cause in raw_root_causes if cause)
                else:
                    # If it's a string, remove braces and format
                    cleaned = str(raw_root_causes).strip("{}")
                    root_causes = ", ".join(
                        cause.replace("_", " ").strip() for cause in cleaned.split(",") if cause.strip()
                    )
            else:
                root_causes = ""

            feedback_created_at = convert_to_timezone(feedback.created_at, info.timezone)
            feedback_created_at_formatted = feedback_created_at.strftime("%d.%m.%Y %H:%M")
            message = "<b>Детали отзыва</b>\n\n"
            message += f"<b>Отель:</b> {info.hotel_name}\n"
            message += f"<b>Зона:</b> {info.zone_name}\n"
            message += f"<b>Оценка:</b> {'⭐' * feedback.rating}\n"
            message += f"<b>Статус:</b> {status_text}\n"
            message += f"<b>Дата:</b> {feedback_created_at_formatted}\n\n"
            message += f"<b>Номер телефона гостя:</b> {info.guest_phone}\n\n"

            if comments:
                message += "<b>Комментарий:</b>\n"
                for comment_text, _ in comments:
                    message += f"- {comment_text}\n"
                message += "\n"
            else:
                message += "<i>Комментариев нет</i>\n\n"

            message += f"<b>Рекомендация ИИ:</b>\n - {recommendation}\n\n"
            message += f"<b>Причины:</b>\n<i>{root_causes}</i>\n\n"

            await self._clear_media_messages_only(user_id, feedback_id, adapter, state)

            # Create keyboard with status buttons
            keyboard = adapter.create_status_keyboard(feedback.id, hotel_code, feedback.status)

            # Edit the existing message
            success = await self.edit_and_remember_message(
                user_id, message_id, message, adapter, state, inline_keyboard=keyboard
            )

            if not success:
                # Message was deleted or doesn't exist anymore, create new one
                state.clear_feedback_detail_message_id(user_id, feedback_id)
                detail_msg = IncomingMessage(
                    channel="telegram",
                    user_id=user_id,
                    text=f"{hotel_code}_MGR_FEEDBACK_{feedback_id}",
                    callback_id=None,
                    payload=None,
                )
                await self._handle_manager_feedback_detail_callback(detail_msg, adapter, state)

        except Exception as e:
            logger.error(f"Error editing feedback detail message: {e}")
            state.clear_feedback_detail_message_id(user_id, feedback_id)
            try:
                detail_msg = IncomingMessage(
                    channel="telegram",
                    user_id=user_id,
                    text=f"{hotel_code}_MGR_FEEDBACK_{feedback_id}",
                    callback_id=None,
                    payload=None,
                )
                await self._handle_manager_feedback_detail_callback(detail_msg, adapter, state)
            except Exception as fallback_error:
                logger.error(f"Fallback also failed: {fallback_error}")

    async def _clear_media_messages_only(
        self,
        user_id: str,
        feedback_id: str,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> None:
        """Clear only media messages for a specific feedback, keeping all other messages"""
        try:
            # Get media message IDs for this specific feedback
            media_message_ids = state.get_feedback_media_messages(user_id, feedback_id)

            if not media_message_ids:
                logger.info(f"No media messages found for feedback {feedback_id}")
                return

            # Delete only the media messages
            deleted_count = 0
            for message_id in media_message_ids:
                try:
                    await adapter.delete_message(user_id, message_id)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete media message {message_id}: {e}")

            # Remove deleted media messages from state
            if deleted_count > 0:
                # Remove media messages from UI messages list
                ui_messages = state.get_ui_messages(user_id)
                remaining_messages = [msg_id for msg_id in ui_messages if msg_id not in media_message_ids]

                # Update state with remaining messages
                state.take_ui_messages(user_id)  # Clear all
                for msg_id in remaining_messages:
                    state.remember_ui_message(user_id, msg_id)

                # Clear media messages from state
                state.clear_feedback_media_messages(user_id, feedback_id)

            logger.info(f"Cleared {deleted_count} media messages for feedback {feedback_id}")

        except Exception as e:
            logger.error(f"Error clearing media messages: {e}")

    async def _clear_all_user_media_messages(
        self,
        user_id: str,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> None:
        """Clear all media messages for a user"""
        try:
            # Get all media message IDs for this user
            media_message_ids = state.clear_all_user_media_messages(user_id)

            if not media_message_ids:
                logger.info(f"No media messages found for user {user_id}")
                return

            # Delete all media messages
            deleted_count = 0
            for message_id in media_message_ids:
                try:
                    await adapter.delete_message(user_id, message_id)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete media message {message_id}: {e}")

            # Remove deleted media messages from UI messages list
            if deleted_count > 0:
                ui_messages = state.get_ui_messages(user_id)
                remaining_messages = [msg_id for msg_id in ui_messages if msg_id not in media_message_ids]

                # Update state with remaining messages
                state.take_ui_messages(user_id)  # Clear all
                for msg_id in remaining_messages:
                    state.remember_ui_message(user_id, msg_id)

            logger.info(f"Cleared {deleted_count} media messages for user {user_id}")

        except Exception as e:
            logger.error(f"Error clearing all user media messages: {e}")

    async def _show_current_registration_step(
        self,
        user_id: str,
        adapter: ChannelAdapter,
        state: InMemoryState,
        reg_state: dict,
    ) -> None:
        """Show current registration step with appropriate keyboard"""
        step = reg_state.get("step", "ask_phone")
        ctx = reg_state.get("context", {})

        if step == "ask_phone":
            # Show phone request with keyboard
            phone_keyboard = {
                "keyboard": [[{"text": SHARE_PHONE_NUMBER_BUTTON, "request_contact": True}]],
                "resize_keyboard": True,
                "one_time_keyboard": True,
                "selective": True,
            }

            # Get hotel info for personalized message
            hotel_code = ctx.get("target_hotel") or ctx.get("resume", {}).get("hotel")
            if hotel_code:
                try:
                    hotel = await self.catalog_repo.get_hotel_by_code(hotel_code.upper())
                    hotel_title = hotel.name if hotel else hotel_code
                    message = WELCOME_MESSAGE.format(
                        hotel_name=hotel_title,
                        share_phone_number_button=SHARE_PHONE_NUMBER_BUTTON,
                    )
                except Exception:
                    message = WELCOME_MESSAGE_NO_HOTEL_NAME.format(
                        share_phone_number_button=SHARE_PHONE_NUMBER_BUTTON,
                    )
            else:
                message = WELCOME_MESSAGE_NO_HOTEL_NAME.format(
                    share_phone_number_button=SHARE_PHONE_NUMBER_BUTTON,
                )

            await self.send_and_remember_message(
                user_id,
                message,
                adapter,
                state,
                reply_markup=phone_keyboard,
            )

        elif step == "ask_consent":
            # Show consent request
            consent_keyboard = {
                "inline_keyboard": [
                    [{"text": "✅ Согласен", "callback_data": "CONSENT_YES"}],
                    [{"text": "❌ Не согласен", "callback_data": "CONSENT_NO"}],
                ]
            }

            await self.send_and_remember_message(
                user_id,
                CONSENT_MESSAGE,
                adapter,
                state,
                inline_keyboard=consent_keyboard,
            )

    async def _handle_button_state_update(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
    ) -> None:
        """Handle button state update - mark button as pressed and update UI"""

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Обрабатывается...")

        try:
            # Get callback query info
            callback_query = msg.payload.get("callback_query", {})
            if not callback_query:
                return

            message = callback_query.get("message", {})
            if not message:
                return

            message_id = message.get("message_id")
            if not message_id:
                return

            callback_data = callback_query.get("data", "")
            if not callback_data:
                return

            # Skip if this is a disabled button
            if callback_data == "disabled":
                return

            # Get the original keyboard from the message
            reply_markup = message.get("reply_markup", {})
            if not reply_markup.get("inline_keyboard"):
                return

            # Handle button click
            await self.button_state.handle_button_click(msg, adapter, message_id, callback_data, reply_markup)

        except Exception as e:
            logger.error(f"Error handling button state update: {e}")

    async def _handle_about_bot_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle about bot callback"""
        hotel_code = msg.text.split("_", 1)[0]

        # Create keyboard to return to menu
        keyboard = {"inline_keyboard": [[{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}]]}

        await self.send_and_remember_message(msg.user_id, ABOUT_BOT_MESSAGE, adapter, state, inline_keyboard=keyboard)

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Информация о боте")

        return {"ok": "true"}

    async def _handle_help_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle help callback"""
        hotel_code = msg.text.split("_", 1)[0]

        help_text = HELP_MESSAGE.format(
            session_waiting_time_message=int(self.settings.FEEDBACK_SESSION_WAITING_TIME_WITH_COMMENT // 60),
        )

        # Create keyboard to return to menu
        keyboard = {"inline_keyboard": [[{"text": MAIN_MENU_BUTTON, "callback_data": f"{hotel_code}_MENU"}]]}

        await self.send_and_remember_message(msg.user_id, help_text, adapter, state, inline_keyboard=keyboard)

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Помощь")

        return {"ok": "true"}

    # Admin callback handlers
    async def _handle_admin_user_management_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin user management callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        keyboard = adapter.admin_user_management_keyboard()
        await self.send_and_remember_message(
            msg.user_id,
            ADMIN_USER_MANAGEMENT_MENU_MESSAGE,
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Управление пользователями")

        return {"ok": "true"}

    async def _handle_admin_branch_management_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin branch management callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        keyboard = adapter.admin_branch_management_keyboard()
        await self.send_and_remember_message(
            msg.user_id,
            ADMIN_BRANCH_MANAGEMENT_MENU_MESSAGE,
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Управление филиалами")

        return {"ok": "true"}

    async def _handle_admin_select_branch_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin select branch callback - show hotels list"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        # Get hotels for the first page
        hotels, has_next = await self.admin_user_service.get_hotels_paginated(page=1, per_page=10)

        if not hotels:
            await self.send_and_remember_message(
                msg.user_id,
                NO_HOTELS_FOUND_MESSAGE,
                adapter,
                state,
            )
        else:
            keyboard = adapter.admin_select_branch_keyboard(hotels, page=1, has_next=has_next)
            await self.send_and_remember_message(
                msg.user_id,
                ADMIN_SELECT_BRANCH_MESSAGE.format(page=1),
                adapter,
                state,
                inline_keyboard=keyboard,
            )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Выбор филиала")

        return {"ok": "true"}

    async def _handle_admin_select_branch_page_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin select branch pagination callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract page from callback data
            # Format: ADMIN_SELECT_BRANCH_PAGE_2
            page = int(msg.text.split("_")[-1])

            # Get hotels for the requested page
            hotels, has_next = await self.admin_user_service.get_hotels_paginated(page=page, per_page=10)

            if not hotels:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTELS_FOUND_MESSAGE,
                    adapter,
                    state,
                )
            else:
                keyboard = adapter.admin_select_branch_keyboard(hotels, page=page, has_next=has_next)
                await self.send_and_remember_message(
                    msg.user_id,
                    ADMIN_SELECT_BRANCH_MESSAGE.format(page=page),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, f"Страница {page}")

        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing page number from callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_HOTELS_LOAD_ERROR_MESSAGE,
                adapter,
                state,
            )

        return {"ok": "true"}

    async def _handle_admin_selected_branch_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin selected branch callback - show hotel info"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract hotel code from callback data
            # Format: ADMIN_SELECTED_BRANCH_GRN
            hotel_code = msg.text.split("_")[-1]

            # Get hotel information
            hotel_info = await self.admin_user_service.get_hotel_info(hotel_code)

            if not hotel_info:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Format zones list
            zones_text = ""
            if hotel_info["zones"]:
                for zone in hotel_info["zones"]:
                    status = "🔴 Отключена" if zone["disabled_at"] else "🟢 Активна"
                    # Добавим <i> тег, чтобы визуально отличать от основного текста
                    adult_only = " <b>(Взрослая ⭐️)</b>" if zone["is_adult"] else " <b>(Детская 👍👎)</b>"
                    zones_text += f"• {zone['name']}{adult_only} - {status}\n"
            else:
                zones_text = "Нет зон"

            # Create message text
            message_text = (
                f"🏨 <b>{hotel_info['name']}</b> ({hotel_info['short_name']})\n\n"
                f"<u>📝 <b>Описание:</b></u> {hotel_info['description']}\n\n"
                f"<u>👥 <b>Зарегистрированных гостей:</b></u> {hotel_info['guests_count']}\n\n"
                f"<u>📍 <b>Зоны отеля:</b></u>\n{zones_text}"
            )
            # Create keyboard
            keyboard = adapter.admin_hotel_management_keyboard(hotel_code)

            await self.send_and_remember_message(
                msg.user_id,
                message_text,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, f"Отель: {hotel_info['name']}")

        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing hotel code from callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке информации об отеле",
                adapter,
                state,
            )

        return {"ok": "true"}

    async def _handle_admin_edit_hotel_description_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin edit hotel description callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract hotel code from callback data
            # Format: ADMIN_EDIT_HOTEL_DESCRIPTION_GRN
            hotel_code = msg.text.split("_")[-1]

            # Get current hotel information
            hotel_info = await self.admin_user_service.get_hotel_info(hotel_code)

            if not hotel_info:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Отель не найден",
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Set state for editing hotel description
            state.set_user_state(msg.user_id, "editing_hotel_description", hotel_code)

            # Create message with current description
            message_text = (
                f"✏️ <b>Редактирование описания отеля</b>\n\n"
                f"🏨 <b>Отель:</b> {hotel_info['name']} ({hotel_info['short_name']})\n\n"
                f"📝 <b>Текущее описание:</b>\n{hotel_info['description']}\n\n"
                f"<i>Отправьте новое полное описание отеля в следующем сообщении.</i>"
            )

            # Create keyboard with cancel button
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "❌ Отмена",
                            "callback_data": f"ADMIN_SELECTED_BRANCH_{hotel_code}",
                        }
                    ]
                ]
            }

            await self.send_and_remember_message(
                msg.user_id,
                message_text,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Редактирование описания")

        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing hotel code from callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке информации об отеле",
                adapter,
                state,
            )

        return {"ok": "true"}

    async def _handle_admin_edit_hotel_name_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin edit hotel name callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract hotel code from callback data
            # Format: ADMIN_EDIT_HOTEL_NAME_GRN
            hotel_code = msg.text.split("_")[-1]

            # Get current hotel information
            hotel_info = await self.admin_user_service.get_hotel_info(hotel_code)

            if not hotel_info:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Отель не найден",
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Set state for editing hotel name
            state.set_user_state(msg.user_id, "editing_hotel_name", hotel_code)

            # Create message with current name
            message_text = (
                f"✏️ <b>Редактирование названия отеля</b>\n\n"
                f"📝 <b>Текущее название:</b> {hotel_info['name']}\n\n"
                f"<i>Отправьте новое название отеля в следующем сообщении.</i>"
            )

            # Create keyboard with cancel button
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "❌ Отмена",
                            "callback_data": f"ADMIN_SELECTED_BRANCH_{hotel_code}",
                        }
                    ]
                ]
            }

            await self.send_and_remember_message(
                msg.user_id,
                message_text,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Редактирование названия")

        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing hotel code from callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке информации об отеле",
                adapter,
                state,
            )

        return {"ok": "true"}

    async def _handle_admin_add_user_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin add user callback - show hotel selection"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        hotels = await self.catalog_repo.list_hotels()
        if not hotels:
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Нет доступных отелей",
                adapter,
                state,
            )
            return {"ok": "true"}

        keyboard = adapter.admin_hotel_selection_keyboard(hotels)
        await self.send_and_remember_message(
            msg.user_id,
            "🏨 <b>Выберите отель</b>\n\nДля какого отеля добавляем пользователя?",
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Выберите отель")

        return {"ok": "true"}

    async def _handle_admin_list_users_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin list users callback - show hotels list"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Get first page of hotels
            hotels, has_next = await self.admin_user_service.get_hotels_paginated(page=1, per_page=10)

            if not hotels:
                await self.send_and_remember_message(
                    msg.user_id,
                    "👥 <b>Список пользователей</b>\n\n❌ Нет доступных отелей",
                    adapter,
                    state,
                )
            else:
                keyboard = adapter.admin_hotels_list_keyboard(hotels, page=1, has_next=has_next)
                message_text = HANDLE_ADMIN_LIST_MESSAGE
                await self.send_and_remember_message(
                    msg.user_id,
                    message_text,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Список отелей")

        except Exception as e:
            logger.error(f"Error handling admin list users callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке списка отелей",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_select_hotel_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin select hotel callback - show role selection"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        # Extract hotel_code by removing the prefix
        hotel_code = msg.text[len("ADMIN_SELECT_HOTEL_") :]
        logger.info(f"Selected hotel_code: {hotel_code}")
        logger.info(f"Full callback data: {msg.text}")

        # Get hotel UUID from database using hotel_code
        hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
        if not hotel:
            logger.error(f"Hotel not found for code: {hotel_code}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка: отель не найден",
                adapter,
                state,
            )
            return {"ok": "true"}

        hotel_id = str(hotel.id)
        logger.info(f"Found hotel_id: {hotel_id} for hotel_code: {hotel_code}")

        # Store hotel_id in state for later use
        state.set_admin_add_user_data(msg.user_id, {"hotel_id": hotel_id})

        roles = await self.roles_repo.get_manager_and_admin()
        if not roles:
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Нет доступных ролей",
                adapter,
                state,
            )
            return {"ok": "true"}

        keyboard = adapter.admin_role_selection_keyboard(roles, hotel_code)
        await self.send_and_remember_message(
            msg.user_id,
            "👤 <b>Выберите роль</b>\n\nКакую роль назначить пользователю?",
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Выберите роль")

        return {"ok": "true"}

    async def _handle_admin_select_role_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin select role callback - start user input process"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        # Extract hotel_code and role_name by removing the prefix and splitting
        # Format: ADMIN_SELECT_ROLE_{hotel_code}_{role_name}
        # We need to find the last underscore to separate hotel_code and role_name
        prefix = "ADMIN_SELECT_ROLE_"
        remaining = msg.text[len(prefix) :]

        # Find the last underscore to separate hotel_code and role_name
        last_underscore_index = remaining.rfind("_")
        if last_underscore_index == -1:
            logger.error(f"Invalid role selection callback format: {msg.text}")
            return {"ok": "true"}

        hotel_code = remaining[:last_underscore_index]
        role_name = remaining[last_underscore_index + 1 :]

        logger.info(f"Selected hotel_code: {hotel_code}, role_name: {role_name}")
        logger.info(f"Full callback data: {msg.text}")

        # Get hotel UUID from database using hotel_code
        hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
        if not hotel:
            logger.error(f"Hotel not found for code: {hotel_code}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка: отель не найден",
                adapter,
                state,
            )
            return {"ok": "true"}

        hotel_id = str(hotel.id)

        # Get role UUID from database using role_name
        role = await self.roles_repo.get_by_name(role_name)
        if not role:
            logger.error(f"Role not found for name: {role_name}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка: роль не найдена",
                adapter,
                state,
            )
            return {"ok": "true"}

        role_id = str(role.id)

        logger.info(f"Found hotel_id: {hotel_id}, role_id: {role_id}")

        # Store role_id and hotel_id in state
        admin_data = state.get_admin_add_user_data(msg.user_id) or {}
        admin_data.update({"role_id": role_id, "hotel_id": hotel_id})
        state.set_admin_add_user_data(msg.user_id, admin_data)

        # Show channel selection keyboard
        keyboard = adapter.admin_channel_selection_keyboard(hotel_code)
        await self.send_and_remember_message(
            msg.user_id,
            "📝 <b>Добавление пользователя</b>\n\nВыберите канал связи:",
            adapter,
            state,
            inline_keyboard=keyboard,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, "Выберите канал связи")

        return {"ok": "true"}

    async def _handle_admin_select_channel_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin select channel callback - ask for user ID"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        # Extract hotel_code and channel_type from callback data
        # Format: ADMIN_SELECT_CHANNEL_{hotel_code}_{TELEGRAM|MAX}
        prefix = "ADMIN_SELECT_CHANNEL_"
        remaining = msg.text[len(prefix) :]

        # Find the last underscore to separate hotel_code and channel_type
        last_underscore_index = remaining.rfind("_")
        if last_underscore_index == -1:
            logger.error(f"Invalid channel selection callback format: {msg.text}")
            return {"ok": "true"}

        hotel_code = remaining[:last_underscore_index]
        channel_type_str = remaining[last_underscore_index + 1 :]

        # Convert string to ChannelType enum
        try:
            channel_type = ChannelType[channel_type_str]
        except KeyError:
            logger.error(f"Invalid channel type: {channel_type_str}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка: неверный тип канала",
                adapter,
                state,
            )
            return {"ok": "true"}

        logger.info(f"Selected hotel_code: {hotel_code}, channel_type: {channel_type}")

        # Store channel_type in state
        admin_data = state.get_admin_add_user_data(msg.user_id) or {}
        admin_data["channel_type"] = channel_type
        state.set_admin_add_user_data(msg.user_id, admin_data)

        # Ask for user ID
        channel_name = "Telegram ID" if channel_type == ChannelType.TELEGRAM else "MAX ID"
        await self.send_and_remember_message(
            msg.user_id,
            f"📝 <b>Добавление пользователя</b>\n\nВведите {channel_name} пользователя:",
            adapter,
            state,
        )

        if msg.callback_id:
            await adapter.answer_callback(msg.callback_id, f"Введите {channel_name}")

        return {"ok": "true"}

    async def _handle_admin_hotels_list_page_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin hotels list pagination callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract page from callback data
            # Format: ADMIN_HOTELS_LIST_PAGE_2
            page = int(msg.text.split("_")[-1])

            # Get hotels for the requested page
            hotels, has_next = await self.admin_user_service.get_hotels_paginated(page=page, per_page=10)

            if not hotels:
                await self.send_and_remember_message(
                    msg.user_id,
                    "👥 <b>Список пользователей</b>\n\n❌ Нет доступных отелей",
                    adapter,
                    state,
                )
            else:
                keyboard = adapter.admin_hotels_list_keyboard(hotels, page=page, has_next=has_next)
                await self.send_and_remember_message(
                    msg.user_id,
                    f"👥 <b>Список пользователей</b>\n\nВыберите отель для просмотра пользователей (страница {page}):",
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, f"Страница {page}")

        except Exception as e:
            logger.error(f"Error handling admin hotels list page callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке списка отелей",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_hotel_users_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin hotel users callback - show users for selected hotel"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract hotel_code from callback data
            # Format: ADMIN_HOTEL_USERS_GRN
            hotel_code = msg.text.split("_")[-1]

            # Get hotel by code to get hotel_id
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Отель не найден",
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Get first page of users for this hotel
            users, has_next = await self.admin_user_service.get_hotel_users_paginated(
                hotel_id=str(hotel.id),
                page=1,
                per_page=10,
                exclude_telegram_id=msg.user_id,
            )

            if not users:
                # Create keyboard with main menu button
                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Вернуться в главное меню",
                                "callback_data": "ADMIN_MAIN_MENU",
                            }
                        ]
                    ]
                }

                await self.send_and_remember_message(
                    msg.user_id,
                    f"👥 <b>Пользователи отеля {hotel.name}</b>\n\n❌ Нет пользователей с ролью отличной от 'Гость'",
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                keyboard = adapter.admin_hotel_users_keyboard(users, hotel_code, page=1, has_next=has_next)
                await self.send_and_remember_message(
                    msg.user_id,
                    f"👥 <b>Пользователи отеля {hotel.name}</b>\n\nСписок пользователей (исключая гостей):",
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, f"Пользователи отеля {hotel.name}")

        except Exception as e:
            logger.error(f"Error handling admin hotel users callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке пользователей отеля",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_hotel_users_page_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin hotel users pagination callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract hotel_code and page from callback data
            # Format: ADMIN_HOTEL_USERS_PAGE_GRN_2
            parts = msg.text.split("_")
            hotel_code = parts[-2]
            page = int(parts[-1])

            # Get hotel by code to get hotel_id
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Отель не найден",
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Get users for the requested page
            users, has_next = await self.admin_user_service.get_hotel_users_paginated(
                hotel_id=str(hotel.id),
                page=page,
                per_page=10,
                exclude_telegram_id=msg.user_id,
            )

            if not users:
                # Create keyboard with main menu button
                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Вернуться в главное меню",
                                "callback_data": "ADMIN_MAIN_MENU",
                            }
                        ]
                    ]
                }

                await self.send_and_remember_message(
                    msg.user_id,
                    f"👥 <b>Пользователи отеля {hotel.name}</b>\n\n❌ Нет пользователей с ролью отличной от 'Гость'",
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                keyboard = adapter.admin_hotel_users_keyboard(users, hotel_code, page=page, has_next=has_next)
                await self.send_and_remember_message(
                    msg.user_id,
                    f"👥 <b>Пользователи отеля {hotel.name}</b>\n\nСписок пользователей (страница {page}):",
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, f"Страница {page}")

        except Exception as e:
            logger.error(f"Error handling admin hotel users page callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке пользователей отеля",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_user_detail_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin user detail callback - show detailed user information"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract user_id from callback data
            # Format: ADMIN_USER_DETAIL_acf1bfce-5050-4f02-962c-4e436b087f45
            user_id = msg.text.split("_")[-1]

            # Get user details
            user_detail = await self.admin_user_service.get_user_detail(user_id)
            if not user_detail:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Пользователь не найден",
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Format hotels information
            hotels_text = ""
            for hotel in user_detail["hotels"]:
                hotels_text += f"• {hotel['name']} ({hotel['code']}) - {hotel['role']}\n"

            # Create message text
            message_text = ADMIN_USER_INFO_MESSAGE.format(
                user_telegram_id=user_detail["telegram_id"],
                user_phone_number=user_detail["phone_number"],
                hotels_text=hotels_text,
            )

            # Create keyboard with action buttons
            keyboard_rows = [
                [
                    {
                        "text": "🚫 Деактивировать",
                        "callback_data": f"ADMIN_USER_DEACTIVATE_{user_id}",
                    }
                ]
            ]

            # Add back button if user has hotels
            if user_detail["hotels"]:
                keyboard_rows.append(
                    [
                        {
                            "text": "⬅️ Вернуться к списку",
                            "callback_data": f"ADMIN_USER_BACK_TO_LIST_{user_detail['hotels'][0]['code']}",
                        }
                    ]
                )

            keyboard = {"inline_keyboard": keyboard_rows}

            await self.send_and_remember_message(
                msg.user_id,
                message_text,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Информация о пользователе")

        except Exception as e:
            logger.error(f"Error handling admin user detail callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке информации о пользователе",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_user_deactivate_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin user deactivate callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract user_id from callback data
            # Format: ADMIN_USER_DEACTIVATE_acf1bfce-5050-4f02-962c-4e436b087f45
            user_id = msg.text.split("_")[-1]

            # Deactivate user
            success = await self.admin_user_service.deactivate_user(user_id)

            if success:
                await self.send_and_remember_message(
                    msg.user_id,
                    "✅ Пользователь успешно деактивирован",
                    adapter,
                    state,
                )

                # Redirect to admin main menu
                keyboard = adapter.admin_menu_keyboard()
                await self.send_and_remember_message(
                    msg.user_id,
                    "🔧 <b>Панель администратора</b>\n\nВыберите действие:",
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Ошибка при деактивации пользователя",
                    adapter,
                    state,
                )

            if msg.callback_id:
                await adapter.answer_callback(
                    msg.callback_id,
                    "Пользователь деактивирован" if success else "Ошибка",
                )

        except Exception as e:
            logger.error(f"Error handling admin user deactivate callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при деактивации пользователя",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_user_back_to_list_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin user back to list callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Redirect to admin main menu
            keyboard = adapter.admin_menu_keyboard()
            await self.send_and_remember_message(
                msg.user_id,
                "🔧 <b>Панель администратора</b>\n\nВыберите действие:",
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Главное меню")

        except Exception as e:
            logger.error(f"Error handling admin user back to list callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при возврате к списку пользователей",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_edit_user_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin edit user callback - ask for phone number"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Create keyboard with main menu button
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Вернуться в главное меню",
                            "callback_data": "ADMIN_MAIN_MENU",
                        }
                    ]
                ]
            }

            await self.send_and_remember_message(
                msg.user_id,
                "✏️ <b>Изменение пользователя</b>\n\nВведите номер телефона пользователя для поиска:",
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            # Set state to wait for phone number input
            state.set_admin_waiting_for_phone(msg.user_id, True)

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Введите номер телефона")

        except Exception as e:
            logger.error(f"Error handling admin edit user callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при открытии раздела изменения пользователя",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_edit_user_hotel_status_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin edit user hotel status callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract telegram_id and hotel_code from callback data
            # Format: ADMIN_EDIT_HOTEL_telegram_id_hotel_code
            parts = msg.text.split("_")
            telegram_id = parts[-2]
            hotel_code = parts[-1]

            # Get user details to find the specific hotel
            user_detail = await self.admin_user_service.get_user_by_telegram_id(telegram_id)
            if not user_detail:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Пользователь не найден",
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Find the specific hotel by code (prefer active assignments)
            target_hotel = None
            active_hotel = None

            for hotel in user_detail["hotels"]:
                if hotel["code"] == hotel_code:
                    if hotel["is_active"]:
                        active_hotel = hotel
                    if not target_hotel:  # Take the first match as fallback
                        target_hotel = hotel

            # Use active hotel if available, otherwise use the first match
            target_hotel = active_hotel if active_hotel else target_hotel

            if not target_hotel:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Отель не найден",
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Create keyboard with action buttons
            keyboard_rows = [
                [
                    {
                        "text": "🔄 Изменить роль",
                        "callback_data": f"ADMIN_CHANGE_USER_ROLE_{telegram_id}_{hotel_code}",
                    }
                ]
            ]

            # Add activate/deactivate button based on current status
            if target_hotel["is_active"]:
                keyboard_rows.append(
                    [
                        {
                            "text": "❌ Деактивировать в отеле",
                            "callback_data": f"ADMIN_TOGGLE_USER_STATUS_{telegram_id}_{hotel_code}",
                        }
                    ]
                )
            else:
                keyboard_rows.append(
                    [
                        {
                            "text": "✅ Активировать в отеле",
                            "callback_data": f"ADMIN_TOGGLE_USER_STATUS_{telegram_id}_{hotel_code}",
                        }
                    ]
                )

            # Add delete user button
            keyboard_rows.append(
                [
                    {
                        "text": "🗑️ Удалить информацию о пользователе в отеле",
                        "callback_data": f"ADMIN_DELETE_USER_{telegram_id}_{hotel_code}",
                    }
                ]
            )

            # Add back button
            keyboard_rows.append([{"text": "⬅️ Назад к пользователю", "callback_data": "ADMIN_EDIT_USER"}])

            keyboard = {"inline_keyboard": keyboard_rows}

            # Create message text
            status_text = "✅ Активен" if target_hotel["is_active"] else "❌ Деактивирован"
            message_text = ADMIN_CHANGE_USER_STATUS_MESSAGE.format(
                user_telegram_id=user_detail["telegram_id"],
                user_phone_number=user_detail["phone_number"],
                target_hotel_name=target_hotel["name"],
                target_hotel_code=target_hotel["code"],
                target_hotel_role=target_hotel["role"],
                status_text=status_text,
            )

            await self.send_and_remember_message(
                msg.user_id,
                message_text,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Выберите действие")

        except Exception as e:
            logger.error(f"Error handling admin edit user hotel status callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                "❌ Ошибка при загрузке информации о статусе пользователя",
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_change_user_role_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin change user role callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract telegram_id and hotel_code from callback data
            # Format: ADMIN_CHANGE_USER_ROLE_telegram_id_hotel_code or ADMIN_CHANGE_USER_ROLE_telegram_id_hotel_code_role_id
            parts = msg.text.split("_")
            logger.info(f"Parsing callback data: {msg.text}, parts: {parts}, length: {len(parts)}")

            # ADMIN_CHANGE_USER_ROLE_telegram_id_hotel_code_role_id (7 parts)
            # ADMIN_CHANGE_USER_ROLE_telegram_id_hotel_code (6 parts)
            if len(parts) == 7:  # With role_id
                telegram_id = parts[4]
                hotel_code = parts[5]
                role_id = parts[6]
            elif len(parts) == 6:  # Without role_id
                telegram_id = parts[4]
                hotel_code = parts[5]
                role_id = None
            else:
                raise ValueError(f"Invalid callback data format: {msg.text}")

            logger.info(f"Extracted: telegram_id={telegram_id}, hotel_code={hotel_code}, role_id={role_id}")

            # If role_id is provided, change the role
            if role_id:
                # Get user by telegram_id first
                user_data = await self.admin_user_service.get_user_by_telegram_id(telegram_id)
                if not user_data:
                    await self.send_and_remember_message(
                        msg.user_id,
                        "❌ Пользователь не найден",
                        adapter,
                        state,
                    )
                    return {"ok": "true"}

                # Get hotel_id by hotel_code
                hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
                if not hotel:
                    await self.send_and_remember_message(
                        msg.user_id,
                        NO_HOTEL_FOUND_MESSAGE,
                        adapter,
                        state,
                    )
                    return {"ok": "true"}

                # Get role by name (role_id contains the full role name)
                role = await self.roles_repo.get_by_name(role_id)
                if not role:
                    await self.send_and_remember_message(
                        msg.user_id,
                        ROLE_NOT_FOUND_MESSAGE,
                        adapter,
                        state,
                    )
                    return {"ok": "true"}

                success = await self.admin_user_service.change_user_role_in_hotel(
                    user_data["id"], str(hotel.id), str(role.id)
                )

                if success:
                    await self.send_and_remember_message(
                        msg.user_id,
                        SUCCESS_USER_ROLE_CHANGED_MESSAGE,
                        adapter,
                        state,
                    )

                    # Show admin main menu after successful role change
                    keyboard = adapter.admin_menu_keyboard()
                    await self.send_and_remember_message(
                        msg.user_id,
                        ADMIN_MENU_MESSAGE,
                        adapter,
                        state,
                        inline_keyboard=keyboard,
                    )
                else:
                    await self.send_and_remember_message(
                        msg.user_id,
                        ERROR_CHANGING_USER_ROLE_MESSAGE,
                        adapter,
                        state,
                    )

                if msg.callback_id:
                    await adapter.answer_callback(msg.callback_id, "Роль изменена" if success else "Ошибка")

                return {"ok": "true"}

            # Get available roles
            roles = await self.roles_repo.get_all()

            # Create keyboard with role options
            keyboard_rows = []

            for role in roles:
                # Skip "Руководитель сети" role
                if role.name == RoleEnum.NETWORK_MANAGER.value:
                    continue
                keyboard_rows.append(
                    [
                        {
                            "text": f"👔 {role.name}",
                            "callback_data": f"ADMIN_CHANGE_USER_ROLE_{telegram_id}_{hotel_code}_{role.name}",
                        }
                    ]
                )

            # Add back button
            keyboard_rows.append(
                [
                    {
                        "text": BACK_BUTTON,
                        "callback_data": f"ADMIN_EDIT_HOTEL_{telegram_id}_{hotel_code}",
                    }
                ]
            )

            keyboard = {"inline_keyboard": keyboard_rows}

            await self.send_and_remember_message(
                msg.user_id,
                CHANGE_USER_ROLE_MESSAGE,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Выберите роль")

        except Exception as e:
            logger.error(f"Error handling admin change user role callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_LOADING_ROLES_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_toggle_user_status_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin toggle user status callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract telegram_id and hotel_code from callback data
            # Format: ADMIN_TOGGLE_USER_STATUS_telegram_id_hotel_code
            parts = msg.text.split("_")
            telegram_id = parts[-2]
            hotel_code = parts[-1]

            # Get user by telegram_id first
            user_data = await self.admin_user_service.get_user_by_telegram_id(telegram_id)
            if not user_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_USER_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Get hotel_id by hotel_code
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Toggle user status
            success = await self.admin_user_service.toggle_user_status_in_hotel(user_data["id"], str(hotel.id))

            if success:
                await self.send_and_remember_message(
                    msg.user_id,
                    USER_STATUS_CHANGED_MESSAGE,
                    adapter,
                    state,
                )

                # Show admin main menu after successful status change
                keyboard = adapter.admin_menu_keyboard()
                await self.send_and_remember_message(
                    msg.user_id,
                    ADMIN_MENU_MESSAGE,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    ERROR_CHANGING_USER_STATUS_MESSAGE,
                    adapter,
                    state,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Статус изменен" if success else "Ошибка")

        except Exception as e:
            logger.error(f"Error handling admin toggle user status callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_CHANGING_USER_STATUS_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_delete_user_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin delete user callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract telegram_id and hotel_code from callback data
            # Format: ADMIN_DELETE_USER_telegram_id_hotel_code
            parts = msg.text.split("_")
            telegram_id = parts[-2]
            hotel_code = parts[-1]

            # Get user by telegram_id first
            user_data = await self.admin_user_service.get_user_by_telegram_id(telegram_id)
            if not user_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_USER_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Get hotel_id by hotel_code
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Delete user from hotel
            success = await self.admin_user_service.delete_user_from_hotel(user_data["id"], str(hotel.id))

            if success:
                await self.send_and_remember_message(
                    msg.user_id,
                    ZONE_SUCCESSFULLY_DELETED_MESSAGE.format(
                        phone_number=user_data["phone_number"], hotel_name=hotel.name
                    ),
                    adapter,
                    state,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_USER_DELETE_ERROR_MESSAGE,
                    adapter,
                    state,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Пользователь удален" if success else "Ошибка")

        except Exception as e:
            logger.error(f"Error handling admin delete user callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_ZONE_EDIT_ERROR_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_select_zone_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin select zone callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract hotel_code from callback data
            # Format: ADMIN_SELECT_ZONE_hotel_code
            hotel_code = msg.text.split("_")[-1]

            # Get zones for the first page
            zones, has_next = await self.admin_user_service.get_zones_paginated(
                hotel_code=hotel_code, page=1, per_page=5
            )

            if not zones:
                # Create keyboard with add zone button
                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": ADMIN_HOTEL_MANAGEMENT_ADD_ZONE_BUTTON,
                                "callback_data": f"ADMIN_ADD_ZONE_{hotel_code}",
                            }
                        ],
                        [{"text": BACK_BUTTON, "callback_data": "ADMIN_SELECT_BRANCH"}],
                    ]
                }

                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONES_FOUND_ERROR_MESSAGE,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                keyboard = adapter.admin_zones_list_keyboard(zones, hotel_code, page=1, has_next=has_next)
                await self.send_and_remember_message(
                    msg.user_id,
                    ADMIN_ZONES_LIST_PAGE_MESSAGE.format(page=1),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Зоны загружены")

        except Exception as e:
            logger.error(f"Error handling admin select zone callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_LOADING_ZONES_LIST_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_select_zone_page_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin zones list pagination callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract hotel_code and page from callback data
            # Format: ADMIN_SELECT_ZONE_PAGE_hotel_code_page
            parts = msg.text.split("_")
            hotel_code = parts[-2]
            page = int(parts[-1])

            # Get zones for the requested page
            zones, has_next = await self.admin_user_service.get_zones_paginated(
                hotel_code=hotel_code, page=page, per_page=5
            )

            if not zones:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONES_FOUND_MESSAGE,
                    adapter,
                    state,
                )
            else:
                keyboard = adapter.admin_zones_list_keyboard(zones, hotel_code, page=page, has_next=has_next)
                await self.send_and_remember_message(
                    msg.user_id,
                    ADMIN_ZONES_LIST_PAGE_MESSAGE.format(page=page),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, f"Страница {page}")

        except Exception as e:
            logger.error(f"Error handling admin zones list page callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_LOADING_ZONES_LIST_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_edit_zone_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin edit zone callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract zone_id from callback data
            # Format: ADMIN_EDIT_ZONE_zone_id
            zone_id = msg.text.split("_")[-1]

            # Get zone information
            zone_data = await self.catalog_repo.get_zone_by_id(zone_id)
            if not zone_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Get hotel code for back button
            hotel = await self.catalog_repo.get_hotel_by_id(str(zone_data.hotel_id))
            hotel_code = hotel.short_name if hotel else "unknown"

            # Create zone info message
            adult_text = FOR_ALL_AGES_MESSAGE if zone_data.is_adult else FOR_CHILDREN_MESSAGE
            disabled_text = DISABLED_MESSAGE if zone_data.disabled_at else ACTIVE_MESSAGE
            zone_description = RATING_REQUEST_MESSAGE_ZONE.format(zone_name=zone_data.name)
            if zone_data.description is not None:
                zone_description = zone_data.description

            zone_info = EDIT_ZONE_DESCRIPTION_MESSAGE.format(
                zone_name=zone_data.name,
                zone_short_name=zone_data.short_name,
                adult_text=adult_text,
                disabled_text=disabled_text,
                zone_description=zone_description,
            )

            keyboard = adapter.admin_zone_edit_keyboard(zone_id, hotel_code)
            await self.send_and_remember_message(
                msg.user_id,
                zone_info,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Зона выбрана")

        except Exception as e:
            logger.error(f"Error handling admin edit zone callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_LOADING_ZONE_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_add_zone_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin add zone callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract hotel_code from callback data
            # Format: ADMIN_ADD_ZONE_hotel_code
            hotel_code = msg.text.split("_")[-1]

            # Set state for adding zone
            state.set_admin_adding_zone(msg.user_id, hotel_code)

            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_FOUND_ERROR_MESSAGE.format(hotel_code=hotel_code),
                    adapter,
                    state,
                )
                return {"ok": "true"}

            await self.send_and_remember_message(
                msg.user_id,
                ADMIN_ADD_ZONE_MESSAGE.format(hotel_name=hotel.name),
                adapter,
                state,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Введите название зоны")

        except Exception as e:
            logger.error(f"Error handling admin add zone callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_ZONE_ADD_ERROR_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_edit_zone_name_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin edit zone name callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract zone_id from callback data
            # Format: ADMIN_EDIT_ZONE_NAME_zone_id
            zone_id = msg.text.split("_")[-1]

            # Get zone information
            zone_data = await self.catalog_repo.get_zone_by_id(zone_id)
            if not zone_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Set state for editing zone name
            state.set_admin_editing_zone_name(msg.user_id, zone_id)

            # Create zone info message with current data
            adult_text = FOR_ALL_AGES_MESSAGE if zone_data.is_adult else FOR_CHILDREN_MESSAGE
            disabled_text = DISABLED_MESSAGE if zone_data.disabled_at else ACTIVE_MESSAGE

            zone_info = EDIT_ZONE_MESSAGE.format(
                zone_name=zone_data.name,
                zone_short_name=zone_data.short_name,
                adult_text=adult_text,
                disabled_text=disabled_text,
            )

            await self.send_and_remember_message(
                msg.user_id,
                zone_info,
                adapter,
                state,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Введите новое название")

        except Exception as e:
            logger.error(f"Error handling admin edit zone name callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_ZONE_NAME_CHANGE_ERROR_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_edit_zone_description_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin edit zone description callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract zone_id from callback data
            # Format: ADMIN_EDIT_ZONE_DESCRIPTION_zone_id
            zone_id = msg.text.split("_")[-1]

            # Get zone information
            zone_data = await self.catalog_repo.get_zone_by_id(zone_id)
            if not zone_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Set state for editing zone description
            state.set_admin_editing_zone_description(msg.user_id, zone_id)

            zone_description = RATING_REQUEST_MESSAGE_ZONE.format(zone_name=zone_data.name)

            if zone_data.description is not None:
                zone_description = zone_data.description

            zone_info = EDIT_ZONE_DESCRIPTION_INPUT_MESSAGE.format(
                zone_description=zone_description,
            )

            await self.send_and_remember_message(
                msg.user_id,
                zone_info,
                adapter,
                state,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Введите новое описание")

        except Exception as e:
            logger.error(f"Error handling admin edit zone description callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_LOADING_ZONE_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_edit_zone_adult_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin edit zone adult restriction callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract zone_id from callback data
            # Format: ADMIN_EDIT_ZONE_ADULT_zone_id
            zone_id = msg.text.split("_")[-1]

            # Get current zone data
            zone_data = await self.catalog_repo.get_zone_by_id(zone_id)
            if not zone_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Toggle adult restriction
            new_adult_value = not zone_data.is_adult
            success = await self.admin_user_service.update_zone(zone_id=zone_id, is_adult=new_adult_value)

            if success:
                adult_text = FOR_ALL_AGES_MESSAGE if new_adult_value else FOR_CHILDREN_MESSAGE

                # Create back button keyboard
                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": BACK_BUTTON,
                                "callback_data": f"ADMIN_EDIT_ZONE_{zone_id}",
                            }
                        ]
                    ]
                }

                await self.send_and_remember_message(
                    msg.user_id,
                    SUCCESS_ZONE_ADULT_CHANGE_MESSAGE.format(adult_text=adult_text),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_ADULT_CHANGE_ERROR_MESSAGE,
                    adapter,
                    state,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ограничение изменено" if success else "Ошибка")

        except Exception as e:
            logger.error(f"Error handling admin edit zone adult callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_ZONE_ADULT_CHANGE_ERROR_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_delete_zone_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin delete zone callback"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Extract zone_id from callback data
            # Format: ADMIN_DELETE_ZONE_zone_id
            zone_id = msg.text.split("_")[-1]

            # Get zone data for confirmation
            zone_data = await self.catalog_repo.get_zone_by_id(zone_id)
            if not zone_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Delete zone
            success = await self.admin_user_service.delete_zone(zone_id)

            if success:
                # Get hotel code for back button
                hotel = await self.catalog_repo.get_hotel_by_id(str(zone_data.hotel_id))
                hotel_code = hotel.short_name if hotel else "unknown"

                # Create back button keyboard
                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": BACK_BUTTON,
                                "callback_data": f"ADMIN_SELECT_ZONE_{hotel_code}",
                            }
                        ]
                    ]
                }

                await self.send_and_remember_message(
                    msg.user_id,
                    SUCCESS_ZONE_DELETE_MESSAGE.format(zone_name=zone_data.name),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_DELETE_ERROR_MESSAGE,
                    adapter,
                    state,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Зона удалена" if success else "Ошибка")

        except Exception as e:
            logger.error(f"Error handling admin delete zone callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_ZONE_DELETE_ERROR_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_add_branch_callback(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin add branch callback - start hotel creation process"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            # Set state for adding hotel
            state.set_admin_adding_hotel(msg.user_id, True)

            await self.send_and_remember_message(
                msg.user_id,
                ADMIN_ADD_HOTEL_MESSAGE,
                adapter,
                state,
            )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Введите название отеля")

        except Exception as e:
            logger.error(f"Error handling admin add branch callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_HOTEL_ADD_ERROR_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}

    async def _handle_admin_confirm_role_change_callback(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handler for confirm the user`s role change by the administrator"""
        if not await self.check_admin_access(msg, adapter, state):
            return {"ok": "true"}

        try:
            parts = msg.text.split("_")
            logger.info(f"Parsing confirm role change callback data: {msg.text}, parts: {parts}, length: {len(parts)}")

            if len(parts) < 7:
                raise ValueError(f"Invalid callback data format: {msg.text}")

            telegram_id = parts[4]
            hotel_code = parts[5]
            role_name = "_".join(parts[6:])

            logger.info(f"Extracted: telegram_id={telegram_id}, hotel_code={hotel_code}, role_name={role_name}")

            user_data = await self.admin_user_service.get_user_by_telegram_id(telegram_id)
            if not user_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    "❌ Пользователь не найден",
                    adapter,
                    state,
                )
                if msg.callback_id:
                    await adapter.answer_callback(msg.callback_id, "Ошибка")
                return {"ok": "true"}

            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                if msg.callback_id:
                    await adapter.answer_callback(msg.callback_id, "Ошибка")
                return {"ok": "true"}

            role = await self.roles_repo.get_by_name(role_name)
            if not role:
                await self.send_and_remember_message(
                    msg.user_id,
                    ROLE_NOT_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                if msg.callback_id:
                    await adapter.answer_callback(msg.callback_id, "Ошибка")
                return {"ok": "true"}

            success = await self.admin_user_service.change_user_role_in_hotel(
                user_data["id"], str(hotel.id), str(role.id)
            )

            if success:
                await self.send_and_remember_message(
                    msg.user_id,
                    SUCCESS_USER_ROLE_CHANGED_MESSAGE,
                    adapter,
                    state,
                )

                keyboard = adapter.admin_menu_keyboard()
                await self.send_and_remember_message(
                    msg.user_id,
                    ADMIN_MENU_MESSAGE,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    ERROR_CHANGING_USER_ROLE_MESSAGE,
                    adapter,
                    state,
                )

            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Роль изменена" if success else "Ошибка")

        except Exception as e:
            logger.error(f"Error handling admin confirm role change callback: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_CHANGING_USER_ROLE_MESSAGE,
                adapter,
                state,
            )
            if msg.callback_id:
                await adapter.answer_callback(msg.callback_id, "Ошибка")

        return {"ok": "true"}
