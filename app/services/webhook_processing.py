import asyncio
import io
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import structlog
from PIL import Image
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.adapters.max.adapter import MaxAdapter
from app.adapters.telegram.adapter import TelegramAdapter
from app.config.messages import (
    ACTIVE_MESSAGE,
    ACTIVE_USER_MESSAGE,
    ADMIN_BRANCH_MANAGEMENT_SELECT_BRANCH_BUTTON,
    ADMIN_MENU_MESSAGE,
    BACK_BUTTON,
    CHANGE_USER_STATUS_MESSAGE,
    DEACTIVATED_USER_MESSAGE,
    DISABLED_MESSAGE,
    EDIT_PROMPT_BUTTON,
    ENTER_PHONE_NUMBER_MESSAGE,
    ERROR_GETTING_HOTEL_INFO_MESSAGE,
    ERROR_HAPPENED_MESSAGE,
    ERROR_HOTEL_DESCRIPTION_UPDATING_MESSAGE,
    ERROR_HOTEL_NAME_UPDATING_MESSAGE,
    ERROR_PROMPT_UPDATING_MESSAGE,
    ERROR_REPORTING_MESSAGE,
    ERROR_SEARCHING_USER_MESSAGE,
    ERROR_USER_ADDITION_MESSAGE,
    ERROR_ZONE_DESCRIPTION_UPDATING_MESSAGE,
    ERROR_ZONE_NAME_UPDATING_MESSAGE,
    FOR_CHILDREN_MESSAGE,
    INVALID_DATE_FORMAT_MESSAGE,
    INVALID_DATE_RANGE_MESSAGE,
    LEAVE_FEEDBACK_MESSAGE,
    MAIN_MENU_BUTTON,
    NO_EMPTY_PROMPT_MESSAGE,
    NO_FOUND_USER_MESSAGE,
    NO_HOTEL_ADD_ERROR_MESSAGE,
    NO_HOTEL_CODE_FOUND_MESSAGE,
    NO_HOTEL_FOUND_MESSAGE,
    NO_HOTEL_FOUND_WITH_CODE_MESSAGE,
    NO_HOTEL_NAME_EMPTY_MESSAGE,
    NO_HOTEL_OR_ZONE_FOUND_MESSAGE,
    NO_ZONE_ADD_ERROR_MESSAGE,
    NO_ZONE_ID_FOUND_MESSAGE,
    NO_ZONE_NAME_EMPTY_MESSAGE,
    RATE_REQUEST_MESSAGE,
    RESET_PROMPT_BUTTON,
    SUCCESS_HOTEL_ADDITION_MESSAGE,
    SUCCESS_HOTEL_DESCRIPTION_UPDATING_MESSAGE,
    SUCCESS_HOTEL_NAME_UPDATING_MESSAGE,
    SUCCESS_PROMPT_UPDATING_MESSAGE,
    SUCCESS_USER_ADDITION_MESSAGE,
    SUCCESS_ZONE_ADDITION_MESSAGE,
    SUCCESS_ZONE_DESCRIPTION_UPDATING_MESSAGE,
    SUCCESS_ZONE_NAME_UPDATING_MESSAGE,
    UNSUCCESSFUL_HOTEL_SHORT_NAME_GENERATION_MESSAGE,
    UNSUCCESSFUL_ZONE_SHORT_NAME_GENERATION_MESSAGE,
    USER_INFORMATION_MESSAGE,
)
from app.core.state import InMemoryState
from app.repositories.feedback_pg import FeedbackPGRepository
from app.repositories.managers import ManagerRepository
from app.services.admin_user import AdminUserService
from app.services.base import BaseService
from app.services.feedback_limit import FeedbackLimitService
from app.services.feedback_processor import FeedbackProcessorService
from app.services.menu import MenuService
from app.services.registration import RegistrationService
from app.services.reporting import ReportingService
from app.services.storage import S3Storage
from app.services.ui_message import UIMessageService
from shared_models import Hotel, MediaType, Role, Scenario, Zone
from shared_models.constants import ChannelType

logger = structlog.get_logger(__name__)


class WebhookProcessingService(BaseService):
    """Service for processing webhook logic"""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.feedback_limit = FeedbackLimitService(session)
        self.ui_message = UIMessageService(session)
        self.feedback_processor = FeedbackProcessorService(session)
        self.manager_repo = ManagerRepository(session)
        self.feedback_repo = FeedbackPGRepository(session)
        self.storage = S3Storage()
        self.admin_user_service = AdminUserService(session)

    async def process_feedback_session(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        active_fs: Optional[Dict],
        ctx: Dict,
        parsed_early: dict,
    ) -> Dict[str, str]:
        """Process active feedback session or redirect to main menu"""
        try:
            logger.info(f"Processing feedback session for user {msg.user_id}, text: {msg.text}, ctx: {ctx}")
            # Check if user is editing a prompt (this has priority for both managers and regular users)
            editing_state = state.get_editing_prompt(msg.user_id)
            if editing_state and msg.text:
                logger.info(f"User is editing a prompt: {msg.user_id}")
                return await self._handle_prompt_editing(msg, adapter, state, editing_state)

            # Check if manager is waiting for custom period input (priority check)
            if state.get_user_state(msg.user_id, "awaiting_custom_period") == "true" and msg.text:
                logger.info(f"Manager is waiting for custom period input: {msg.user_id}")
                return await self._handle_manager_custom_period_input(msg, adapter, state)

            # If user is a manager, redirect to manager menu and end any active feedback session
            is_manager = await self.manager_repo.get_by_telegram_id(
                msg.user_id,
                ctx.get("hotel"),
            ) if ctx.get("hotel") else None
            if is_manager:
                logger.info(f"Manager detected, redirecting to menu: {msg.user_id}")
                # End any active feedback session
                if active_fs:
                    state.end_feedback_session(msg.channel, msg.user_id)

                # Redirect to manager menu
                menu_service = MenuService(self.session)
                return await menu_service.handle_menu_request(msg, adapter, state)

            # If user is an admin, check if they're in add user process
            is_admin = await self.admin_repo.get_by_telegram_id(msg.user_id)
            logger.info(f"Admin check for user {msg.user_id}: is_admin={bool(is_admin)}")
            if is_admin:
                logger.info(f"Admin detected: {msg.user_id}")
                # End any active feedback session
                if active_fs:
                    state.end_feedback_session(msg.channel, msg.user_id)

                # Check if admin is waiting for phone number input
                if state.is_admin_waiting_for_phone(msg.user_id) and msg.text:
                    logger.info(f"Admin is waiting for phone number input: {msg.user_id}")
                    return await self._handle_admin_phone_input(msg, adapter, state)

                # Check if admin is in add user process
                admin_data = state.get_admin_add_user_data(msg.user_id)
                if admin_data and msg.text:
                    logger.info(f"Admin is in add user process: {msg.user_id}")
                    return await self._handle_admin_add_user_input(msg, adapter, state, admin_data)

                # Check if admin is editing hotel description
                if state.get_user_state(msg.user_id, "editing_hotel_description") and msg.text:
                    logger.info(f"Admin is editing hotel description: {msg.user_id}")
                    return await self._handle_admin_hotel_description_input(msg, adapter, state)

                # Check if admin is editing hotel name
                if state.get_user_state(msg.user_id, "editing_hotel_name") and msg.text:
                    logger.info(f"Admin is editing hotel name: {msg.user_id}")
                    return await self._handle_admin_hotel_name_input(msg, adapter, state)

                # Check if admin is adding zone
                if state.get_admin_adding_zone(msg.user_id) and msg.text:
                    logger.info(f"Admin is adding zone: {msg.user_id}")
                    return await self._handle_admin_add_zone_input(msg, adapter, state)

                # Check if admin is editing zone name
                if state.get_admin_editing_zone_name(msg.user_id) and msg.text:
                    logger.info(f"Admin is editing zone name: {msg.user_id}")
                    return await self._handle_admin_edit_zone_name_input(msg, adapter, state)

                # Check if admin is editing zone description
                if state.get_admin_editing_zone_description(msg.user_id) and msg.text:
                    logger.info(f"Admin is editing zone description: {msg.user_id}")
                    return await self._handle_admin_edit_zone_description_input(msg, adapter, state)

                # Check if admin is adding hotel
                if state.get_admin_adding_hotel(msg.user_id) and msg.text:
                    logger.info(f"Admin is adding hotel: {msg.user_id}")
                    return await self._handle_admin_add_hotel_input(msg, adapter, state)

                # Redirect to admin menu
                keyboard = adapter.admin_menu_keyboard()
                await self.send_and_remember_message(
                    msg.user_id,
                    ADMIN_MENU_MESSAGE,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
                return {"ok": "true"}

            # Feedback composition has priority over registration prompts
            if not active_fs:
                logger.info(f"No active feedback session, checking registration: {msg.user_id}")
                # If registration is in progress, route into registration flow
                reg_state = state.get_registration(msg.channel, msg.user_id)
                if reg_state and (reg_state.get("step") or "") != "completed":
                    if msg.text:
                        registration_service = RegistrationService(self.session, adapter)
                        await registration_service.handle_message(msg.user_id, msg.text)
                        await self.session.commit()
                    return {"ok": "true"}

            # If there's an active feedback session, process the message as feedback
            if active_fs:
                logger.info(f"Processing active feedback session: {msg.user_id}")
                return await self._process_active_feedback_session(msg, adapter, state, active_fs, parsed_early)

            # No active feedback session - show message about using buttons
            logger.warning(f"No active feedback session for user {msg.user_id}")
            return await self._redirect_to_main_menu(msg, adapter, state)

        except Exception as e:
            logger.error(f"Error processing feedback session: {e}")
            return {"ok": "false"}

    async def _show_rating_required_message(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> None:
        """Show message asking user to provide rating first"""
        try:
            await self.send_and_remember_message(
                msg.user_id,
                RATE_REQUEST_MESSAGE,
                adapter,
                state,
            )
        except Exception as e:
            logger.error(f"Error showing rating required message: {e}")

    async def _process_active_feedback_session(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        active_fs: Dict,
        parsed_early: dict,
    ) -> Dict[str, str]:
        """Process message in active feedback session"""
        try:
            # Check if user has provided rating first
            if not active_fs.get("rating"):
                logger.info(f"feedback.no_rating_provided: user_id={msg.user_id}")
                await self._show_rating_required_message(msg, adapter, state)
                return {"ok": "true"}

            # Mark comment received and touch session
            active_fs["comment_received"] = True
            state.touch_feedback_session(msg.channel, msg.user_id)

            # Get active feedback ID
            active_feedback_id = state.get_feedback_active_id(msg.channel, msg.user_id)
            logger.info(f"feedback.active_id: {active_feedback_id}")

            # Verify feedback exists in database
            if active_feedback_id:
                feedback_exists = await self._verify_feedback_exists(active_feedback_id)
                if not feedback_exists:
                    logger.error(f"feedback.not_found: {active_feedback_id}")
                    state.end_feedback_session(msg.channel, msg.user_id)
                    return {"ok": "true"}

            # Process text comments
            # Handle split messages for MAX (long messages are split into parts)
            text_to_process = None
            should_increment_count = False
            # Track if we're processing a combined message (to avoid blocking response)
            is_combined_message = False

            if msg.text and active_feedback_id:
                # Check if there's a buffer BEFORE adding new part
                # (to know if we're processing a combined message)
                had_buffer_before = state.has_split_message_buffer(
                    msg.channel, msg.user_id
                )
                
                # Check if this is a split message part
                is_complete, combined_text = state.add_split_message_part(
                    msg.channel, msg.user_id, msg.text
                )

                if is_complete and combined_text:
                    # Previous message is complete, process it
                    logger.info(
                        f"feedback.split_message.complete: "
                        f"user_id={msg.user_id}, "
                        f"combined_length={len(combined_text)}"
                    )
                    text_to_process = combined_text
                    should_increment_count = True
                    is_combined_message = (
                        had_buffer_before or len(combined_text) > len(msg.text)
                    )
                elif not is_complete:
                    # Message might be split - buffering, don't process yet
                    logger.info(
                        f"feedback.split_message.buffering: "
                        f"user_id={msg.user_id}"
                    )
                else:
                    # Current message is complete (not split)
                    text_to_process = msg.text
                    should_increment_count = True

            if not should_increment_count and (
                msg.media_token or (msg.payload and parsed_early.get("media_tokens"))
            ):
                should_increment_count = True

            # Check message limit BEFORE incrementing count
            if should_increment_count:
                message_limit_reached = not state.can_add_message_to_feedback(
                    msg.channel, msg.user_id
                )
                if message_limit_reached:
                    logger.info(
                        f"feedback.message_limit_reached: user_id={msg.user_id}"
                    )
                    # Flush any remaining split message buffer
                    flushed_text = state.flush_split_message_buffer(
                        msg.channel, msg.user_id
                    )
                    if flushed_text and active_feedback_id:
                        try:
                            logger.info(
                                f"feedback.split_message.flushed_on_limit: "
                                f"user_id={msg.user_id}"
                            )
                            await self._process_text_comment(
                                flushed_text,
                                active_feedback_id,
                                msg.user_id,
                                state,
                                msg,
                                adapter,
                            )
                        except Exception as e:
                            logger.error(
                                f"Error processing flushed split message: {e}",
                                exc_info=True,
                            )
                    await self._finalize_feedback_session(msg, adapter, state)
                    return {"ok": "true"}

                # Increment message count only for complete messages
                message_count = state.increment_feedback_message_count(
                    msg.channel, msg.user_id
                )
                logger.info(
                    f"feedback.message_count: user_id={msg.user_id}, "
                    f"count={message_count}"
                )

            # Process the text comment if we have one to process
            if text_to_process and active_feedback_id:
                try:
                    await self._process_text_comment(
                        text_to_process,
                        active_feedback_id,
                        msg.user_id,
                        state,
                        msg,
                        adapter,
                        is_combined_message=is_combined_message,
                    )
                except Exception as e:
                    logger.error(
                        f"Error processing text comment: {e}", exc_info=True
                    )
                    # Continue processing even if text comment failed

            # Process media tokens
            if msg.media_token or (msg.payload and parsed_early.get("media_tokens")):
                try:
                    await self._process_media_tokens(msg, active_feedback_id, state, parsed_early, adapter)
                except Exception as e:
                    logger.error(
                        f"Error processing media tokens: {e}", exc_info=True
                    )
                    # Continue processing even if media processing failed

            # Check if there's a buffered message ready to process
            # (timeout has passed since last part)
            # Only check if we haven't already processed a message in this cycle
            # to avoid duplicate processing
            if active_feedback_id and not text_to_process:
                ready_text = state.get_split_message_if_ready(
                    msg.channel, msg.user_id
                )
                if ready_text:
                    logger.info(
                        f"feedback.split_message.ready_after_timeout: "
                        f"user_id={msg.user_id}"
                    )
                    # Check limit before processing
                    if state.can_add_message_to_feedback(msg.channel, msg.user_id):
                        message_count = state.increment_feedback_message_count(
                            msg.channel, msg.user_id
                        )
                        logger.info(
                            f"feedback.message_count: user_id={msg.user_id}, "
                            f"count={message_count}"
                        )
                        try:
                            await self._process_text_comment(
                                ready_text,
                                active_feedback_id,
                                msg.user_id,
                                state,
                                msg,
                                adapter,
                            )
                        except Exception as e:
                            logger.error(
                                f"Error processing ready split message: {e}",
                                exc_info=True,
                            )

            # Check message limit AFTER processing
            # to ensure analysis runs if limit reached
            if not state.can_add_message_to_feedback(msg.channel, msg.user_id):
                # Flush any remaining buffered message
                flushed_text = state.flush_split_message_buffer(
                    msg.channel, msg.user_id
                )
                if flushed_text and active_feedback_id:
                    try:
                        logger.info(
                            f"feedback.split_message.flushed_on_limit: "
                            f"user_id={msg.user_id}"
                        )
                        await self._process_text_comment(
                            flushed_text,
                            active_feedback_id,
                            msg.user_id,
                            state,
                            msg,
                            adapter,
                        )
                    except Exception as e:
                        logger.error(
                            f"Error processing flushed split message: {e}",
                            exc_info=True,
                        )

                logger.info(
                    f"feedback.message_limit_reached_after_processing: "
                    f"user_id={msg.user_id}"
                )
                await self._finalize_feedback_session(msg, adapter, state)
                return {"ok": "true"}

            return {"ok": "true"}

        except Exception as e:
            logger.error(
                f"Error processing active feedback session: {e}", exc_info=True
            )
            # Even if there's an error, try to finalize if limit is reached
            try:
                if not state.can_add_message_to_feedback(msg.channel, msg.user_id):
                    logger.info(
                        f"feedback.message_limit_reached_on_error: "
                        f"user_id={msg.user_id}"
                    )
                    await self._finalize_feedback_session(msg, adapter, state)
            except Exception as finalize_error:
                logger.error(
                    f"Error finalizing feedback session on error: {finalize_error}",
                    exc_info=True,
                )
            return {"ok": "false"}

    async def _finalize_feedback_session(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> None:
        """Finalize feedback session when limit is reached"""
        try:
            # Flush any remaining split message buffer before finalizing
            flushed_text = state.flush_split_message_buffer(
                msg.channel, msg.user_id
            )
            if flushed_text:
                active_feedback_id = state.get_feedback_active_id(
                    msg.channel, msg.user_id
                )
                if active_feedback_id:
                    try:
                        logger.info(
                            f"feedback.split_message.flushed_on_finalize: "
                            f"user_id={msg.user_id}"
                        )
                        await self._process_text_comment(
                            flushed_text,
                            active_feedback_id,
                            msg.user_id,
                            state,
                            msg,
                            adapter,
                        )
                    except Exception as e:
                        logger.error(
                            f"Error processing flushed split message on finalize: {e}",
                            exc_info=True,
                        )

            active_feedback_id = state.get_feedback_active_id(msg.channel, msg.user_id)
            result = await self.feedback_processor.process_feedback_session_relevant(
                active_feedback_id,
                state,
                adapter=adapter,
            )
            if result is not None:
                feedback, combined_input, session_id, sentiment = result
                current_session = state.get_feedback_session(msg.channel, msg.user_id)
                is_new_feedback = current_session.get("is_new_feedback", True) if current_session else True
                asyncio.create_task(
                    self.feedback_processor.process_feedback_session_background(
                        feedback,
                        combined_input,
                        session_id,
                        sentiment,
                        state,
                        is_new_feedback,
                        adapter=adapter,
                    )
                )

            await self._send_feedback_completion_message(msg, adapter, state)

            state.end_feedback_session(msg.channel, msg.user_id)
        except Exception as e:
            logger.error(f"Error finalizing feedback session: {e}")

    async def _verify_feedback_exists(self, active_feedback_id: str) -> bool:
        """Verify feedback exists in database"""
        try:
            fb_exists = await self.feedback_repo.get_by_id(active_feedback_id)
            logger.info(f"feedback.exists_in_db: {fb_exists is not None}, id: {active_feedback_id}")
            return fb_exists is not None
        except Exception as e:
            logger.error(f"feedback.verification.error: {e}")
            return False

    async def _send_feedback_completion_message(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> None:
        """Send completion message when feedback session reaches message limit"""
        try:
            active_fs = state.get_feedback_session(msg.channel, msg.user_id)
            hotel_code = active_fs.get("hotel") if active_fs else None

            if hotel_code:
                # Disable rating UI first
                await self._disable_rating_ui_if_exists(msg, adapter, state, active_fs)
                await self.ui_message.clear_feedback_messages(msg, adapter, state)

                # Get hotel description
                hotel_description = await self.get_hotel_description(hotel_code, msg.user_id)

                await self.send_and_remember_message(
                    msg.user_id,
                    hotel_description,
                    adapter,
                    state,
                    reply_markup=adapter.main_menu_keyboard(hotel_code),
                )

                logger.info(f"feedback.completion_message_sent: user_id={msg.user_id}")
            else:
                logger.warning(f"feedback.completion_message_skipped: no_hotel_code user_id={msg.user_id}")

        except Exception as e:
            logger.error(f"Error sending feedback completion message: {e}")

    async def _disable_rating_ui_if_exists(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        current_session: dict,
    ) -> None:
        """Disable rating UI if it exists for the current feedback session"""
        try:
            rating_message_id = state.get_rating_message_id(msg.channel, msg.user_id)
            if not rating_message_id:
                return

            hotel_code = current_session.get("hotel")
            zone_code = current_session.get("zone")
            current_rating = current_session.get("rating")

            if not hotel_code or not zone_code:
                return

            await self.ui_message.disable_rating_ui(
                msg,
                adapter,
                state,
                hotel_code,
                zone_code,
                current_rating,
                rating_message_id,
            )

        except Exception as e:
            logger.error(f"Error disabling rating UI: {e}")

    async def _process_text_comment(
        self,
        text: str,
        active_feedback_id: str,
        user_id: str,
        state: InMemoryState,
        msg: IncomingMessage = None,
        adapter: ChannelAdapter = None,
        is_combined_message: bool = False,
    ) -> None:
        """Process and save text comment"""
        try:
            channel = msg.channel if msg else "telegram"
            
            # Truncate very long text before processing to prevent errors
            # Use a reasonable limit (e.g., 100KB) to prevent database issues
            MAX_COMMENT_LENGTH = 100000  # 100KB should be more than enough
            if text and len(text) > MAX_COMMENT_LENGTH:
                logger.warning(
                    f"feedback.comment.too_long: user_id={user_id}, "
                    f"length={len(text)}, truncating to {MAX_COMMENT_LENGTH}"
                )
                # Truncate using the same method as in feedback_processor
                truncated_text = self.feedback_processor.truncate_text(
                    text, max_length=MAX_COMMENT_LENGTH
                )
                text = truncated_text
            
            state.add_feedback_message(channel, user_id, text)
            # Check if this is feedback addition BEFORE saving
            instruction_message_id = state.get_instruction_message_id(channel, user_id)
            message_count = state.get_feedback_message_count(channel, user_id)

            if instruction_message_id and message_count == 1:
                # This is the first message in feedback addition - remove button only
                # Keep the text unchanged, only remove the button
                logger.info(f"feedback.addition.removing_button: user_id={user_id}")
                
                try:
                    # Remove button from instruction message without changing text
                    # Pass None to remove keyboard
                    success = await adapter.edit_message_reply_markup(
                        msg.user_id,
                        instruction_message_id,
                        inline_keyboard=None,  # None removes buttons
                    )
                    if success:
                        logger.info(
                            f"feedback.addition.button_removed: "
                            f"user_id={user_id}, message_id={instruction_message_id}"
                        )
                    else:
                        logger.warning(
                            f"feedback.addition.button_remove_failed: "
                            f"user_id={user_id}, message_id={instruction_message_id}"
                        )
                except Exception as e:
                    logger.error(
                        f"feedback.addition.button_remove_error: "
                        f"user_id={user_id}, error={e}"
                    )

            # Save comment to database only if text is valid
            if text and text.strip() and text.strip().lower() != "none":
                await self.feedback_repo.add_comment(active_feedback_id, text)
                logger.info(f"feedback.comment.saved: {active_feedback_id}")

                # Commit the comment
                await self.session.commit()
                logger.info(f"feedback.comment.committed: {active_feedback_id}")
            else:
                logger.info(f"feedback.comment.skipped: empty or invalid text: '{text}'")

            # Re-set active feedback to ensure it stays in STATE
            state.set_feedback_active_id(channel, user_id, active_feedback_id)
            logger.info(f"feedback.re_set_active: {active_feedback_id}")

            # Send response message after processing text comment
            # Only send response if this is not a buffered split message part
            # (buffered parts will be processed together as one message)
            if msg and adapter:
                active_fs = state.get_feedback_session(channel, user_id)
                hotel_code = active_fs.get("hotel") if active_fs else None

                if hotel_code:
                    # Check if there's a buffered message - if yes, don't send response yet
                    # (it will be sent when the complete message is processed)
                    # Exception: if this is a combined message, we should send response
                    # even if there's a new buffer (for the next part)
                    has_buffered = state.has_split_message_buffer(channel, user_id)
                    
                    # If this is a combined message, check if we recently processed
                    # another combined message (parts of the same split message)
                    # to avoid sending multiple responses for one user message
                    should_send_response = True
                    if is_combined_message:
                        # Use lock to ensure atomic check and update
                        # (use the same lock mechanism as feedback sessions)
                        lock_key = f"{channel}:{user_id}"
                        if lock_key not in state._locks:
                            state._locks[lock_key] = asyncio.Lock()
                        
                        async with state._locks[lock_key]:
                            last_combined_time = active_fs.get(
                                "last_combined_message_time"
                            )
                            now = datetime.now(timezone.utc)
                            
                            if last_combined_time:
                                time_since_last = (
                                    now - last_combined_time
                                ).total_seconds()
                                # If we processed a combined message recently (< 3 seconds),
                                # don't send response (it's likely another part)
                                if time_since_last < 3.0:
                                    should_send_response = False
                                    logger.info(
                                        f"feedback.split_message.skip_response: "
                                        f"user_id={user_id}, "
                                        f"time_since_last={time_since_last:.2f}s"
                                    )
                            
                            # Update last combined message time atomically
                            active_fs["last_combined_message_time"] = now
                    
                    # If this is a combined message, ignore the buffer check
                    # (the buffer contains the next part, not the current one)
                    if (not has_buffered or is_combined_message) and should_send_response:
                        # No buffered message - safe to send response
                        # Check if first response was already sent to avoid duplicates
                        # when processing split messages
                        first_response_sent = active_fs.get("first_response_sent", False)
                        
                        if message_count == 1 and not first_response_sent:
                            # First message - send "Спасибо за ваш отзыв..."
                            await self.ui_message.send_feedback_response_after_first_message(
                                msg, adapter, state, hotel_code
                            )
                            # Mark first response as sent
                            active_fs["first_response_sent"] = True
                        elif message_count > 1:
                            # Additional message - send "Спасибо, что написали комментарий..."
                            await self.ui_message.send_feedback_response_after_additional_message(
                                msg, adapter, state, hotel_code
                            )

        except Exception as e:
            logger.error(
                f"feedback.comment.save.error: {e}", exc_info=True
            )
            await self.session.rollback()
            # Re-raise to allow caller to handle
            raise

    async def _process_media_tokens(
        self,
        msg: IncomingMessage,
        active_feedback_id: str,
        state: InMemoryState,
        parsed_early: dict,
        adapter: ChannelAdapter,
    ) -> None:
        """
        Process media tokens and save attachments.
        Run uploads in parallel for throughput.
        """
        try:
            media_tokens: list[str] = []
            if msg.media_token:
                media_tokens = [msg.media_token]
            if msg.payload and parsed_early.get("media_tokens"):
                media_tokens = parsed_early["media_tokens"]

            if (msg.media_kind or "").lower() == "image" and media_tokens:
                media_tokens = media_tokens[:1]

            if not media_tokens:
                return

            tasks = [
                asyncio.create_task(
                    self._process_single_media_token(
                        media_token,
                        active_feedback_id,
                        msg,
                        state,
                    )
                )
                for media_token in media_tokens
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # Re-set active feedback to ensure it stays in STATE
            state.set_feedback_active_id(msg.channel, msg.user_id, active_feedback_id)
            logger.info(f"feedback.re_set_active: {active_feedback_id}")

            # Send response message after processing media
            active_fs = state.get_feedback_session(msg.channel, msg.user_id)
            hotel_code = active_fs.get("hotel") if active_fs else None

            if hotel_code:
                message_count = state.get_feedback_message_count(msg.channel, msg.user_id)
                active_fs = state.get_feedback_session(msg.channel, msg.user_id)
                first_response_sent = active_fs.get("first_response_sent", False) if active_fs else False
                
                if message_count == 1 and not first_response_sent:
                    # First message - send "Спасибо за ваш отзыв..."
                    await self.ui_message.send_feedback_response_after_first_message(msg, adapter, state, hotel_code)
                    # Mark first response as sent
                    if active_fs:
                        active_fs["first_response_sent"] = True
                elif message_count > 1:
                    # Additional message - send "Спасибо, что написали комментарий..."
                    await self.ui_message.send_feedback_response_after_additional_message(
                        msg, adapter, state, hotel_code
                    )

        except Exception as e:
            logger.error(f"feedback.media.processing.error: {e}")

    async def _process_single_media_token(
        self,
        media_token: str,
        active_feedback_id: str,
        msg: IncomingMessage,
        state: InMemoryState,
    ) -> None:
        """Download media from Telegram, convert if needed, upload to S3, save attachment."""
        try:
            if not media_token or not active_feedback_id:
                return

            adapter = TelegramAdapter() if msg.channel == ChannelType.TELEGRAM else MaxAdapter()
            file_bytes = await adapter.download_file_bytes(media_token)
            if not file_bytes:
                logger.error(f"feedback.media.token.processing.error: {media_token}")
                return

            # Determine S3 path components
            hotel_name, zone_name = await self._get_hotel_zone_names(active_feedback_id)
            hotel_dir = self._sanitize_s3_component(hotel_name or "unknown_hotel")
            zone_dir = self._sanitize_s3_component(zone_name or "unknown_zone")
            base_path = f"{hotel_dir}/{zone_dir}"

            # Build filename and content bytes
            now_ts = int(datetime.now(timezone.utc).timestamp())
            token_short = media_token[:12].replace("/", "_")
            unique_suffix = uuid.uuid4().hex[:8]

            media_kind = (msg.media_kind or "image").lower()
            if media_kind == "audio":
                filename = f"{active_feedback_id}_{now_ts}_{token_short}_{unique_suffix}.ogg"
                content_type = "audio/ogg"
                upload_bytes = file_bytes
                media_type = MediaType.AUDIO
            elif media_kind == "document":
                filename = f"{active_feedback_id}_{now_ts}_{token_short}_{unique_suffix}"
                content_type = "application/octet-stream"
                upload_bytes = file_bytes
                media_type = MediaType.DOCUMENT
            else:
                # Convert image to PNG
                filename = f"{active_feedback_id}_{now_ts}_{token_short}_{unique_suffix}.png"
                content_type = "image/png"
                media_type = MediaType.IMAGE
                try:
                    img_stream = io.BytesIO(file_bytes)
                    with Image.open(img_stream) as img:
                        png_stream = io.BytesIO()
                        img.save(png_stream, format="PNG")
                        upload_bytes = png_stream.getvalue()
                except Exception:
                    # If Pillow fails, fallback to original bytes
                    logger.error(f"feedback.media.token.processing.error: {media_token}")
                    upload_bytes = file_bytes

            # Upload to S3
            key = f"{base_path}/{filename}"
            await self.storage.put_bytes(key, upload_bytes, content_type)

            # Save attachment and update state
            await self._save_uploaded_media(
                active_feedback_id,
                state,
                msg,
                uploaded_url=key,
                media_type=media_type,
            )

        except Exception as e:
            logger.error(f"feedback.media.token.processing.error: {e}")

    async def _save_uploaded_media(
        self,
        active_feedback_id: str,
        state: InMemoryState,
        msg: IncomingMessage,
        *,
        uploaded_url: str,
        media_type: MediaType | None = None,
    ) -> None:
        """Persist uploaded media as attachment and remember in session state."""
        try:
            # Save to STATE
            state.add_feedback_media(
                msg.channel,
                msg.user_id,
                media_url=uploaded_url,
                media_kind=(msg.media_kind or "image"),
            )

            # Determine media type if not provided
            kind = (msg.media_kind or "image").lower()
            if media_type is None:
                if kind == "audio":
                    media_type = MediaType.AUDIO
                elif kind == "document":
                    media_type = MediaType.DOCUMENT
                else:
                    media_type = MediaType.IMAGE

            await self.feedback_repo.add_attachment(
                active_feedback_id,
                media_type,
                uploaded_url,
            )
            await self.session.commit()
            logger.info(f"feedback.attachment.committed: {active_feedback_id}")
        except Exception as e:
            logger.error(f"feedback.attachment.save.error: {e}")
            await self.session.rollback()

    async def _get_hotel_zone_names(self, feedback_id: str) -> tuple[Optional[str], Optional[str]]:
        """Get hotel_name and zone_name by feedback_id for S3 pathing."""
        try:
            last_feedback = await self.feedback_repo.get_feedback_with_last_comment(feedback_id)
            if not last_feedback:
                return None, None
            hotel_name = last_feedback.name
            zone_name = last_feedback.zone
            return hotel_name, zone_name
        except Exception:
            return None, None

    @staticmethod
    def _sanitize_s3_component(value: str) -> str:
        """Sanitize path component for S3 key: remove slashes and trim whitespace."""
        s = (value or "").strip()
        for ch in ["/", "\\", "\n", "\r", "\t"]:
            s = s.replace(ch, "_")
        return s or "unknown"

    async def _save_media_attachment(self, active_feedback_id: str, media_url: str, media_kind: Optional[str]) -> None:
        """Save media attachment to database"""
        try:
            # Determine media type based on media_kind
            if media_kind == "audio":
                media_type = MediaType.AUDIO
            elif media_kind == "video":
                media_type = MediaType.VIDEO
            elif media_kind == "document":
                media_type = MediaType.DOCUMENT
            else:  # image or default
                media_type = MediaType.IMAGE

            await self.feedback_repo.add_attachment(active_feedback_id, media_type, media_url)
            logger.info(f"feedback.attachment.saved: {active_feedback_id}, url: {media_url}")

            # Commit the attachment
            await self.session.commit()
            logger.info(f"feedback.attachment.committed: {active_feedback_id}")

        except Exception as e:
            logger.error(f"feedback.attachment.save.error: {e}")
            await self.session.rollback()

    async def _redirect_to_main_menu(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Redirect to main menu when no active feedback session"""
        try:
            logger.info("feedback.session.no_active: redirecting to main menu")

            # Check if we already sent a redirect message recently to avoid spam
            last_redirect = state.prompt_last_sent_at.get(msg.user_id)
            if last_redirect and (datetime.now(timezone.utc) - last_redirect).total_seconds() < 5:
                logger.info(f"feedback.redirect.skipped.recent: user_id={msg.user_id}")
                return {"ok": "true"}

            hotel_code = await self.user_repo.get_active_hotel_code(msg.user_id)

            is_manager = await self.manager_repo.get_by_telegram_id(msg.user_id, hotel_code)

            await self.clear_ui_message_buttons(msg.user_id, adapter, state)

            logger.info(f"Cleared buttons for guest user {msg.user_id} outside feedback session")

            if not is_manager:
                await self.send_and_remember_message(
                    msg.user_id,
                    LEAVE_FEEDBACK_MESSAGE,
                    adapter,
                    state,
                )

            # Update last redirect time
            state.prompt_last_sent_at[msg.user_id] = datetime.now(timezone.utc)

            # Show hotel menu
            return await self.ui_message.show_hotel_menu(msg, adapter, state, hotel_code)

        except Exception as e:
            logger.error(f"Error redirecting to main menu: {e}")
            return {"ok": "false"}

    async def _handle_prompt_editing(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        editing_state: Dict,
    ) -> Dict[str, str]:
        """Handle prompt editing when user sends text"""

        hotel_code = editing_state.get("hotel_code")
        zone_code = editing_state.get("zone_code")
        new_prompt = msg.text.strip()

        # Delete the editing prompt message immediately after user input
        await self.clear_editing_prompt_message(msg.user_id, adapter, state)

        try:
            if not new_prompt:
                await adapter.send_message(msg.user_id, NO_EMPTY_PROMPT_MESSAGE)
                return {"ok": "true"}

            # Get hotel and zone IDs
            result = await self.session.execute(
                _select(Hotel.id, Zone.id.label("zone_id"))
                .join(Zone, Zone.hotel_id == Hotel.id)
                .where(Hotel.short_name == hotel_code, Zone.short_name == zone_code)
            )
            row = result.first()

            if not row:
                await adapter.send_message(msg.user_id, NO_HOTEL_OR_ZONE_FOUND_MESSAGE)
                return {"ok": "true"}

            hotel_id, zone_id = row

            # Check if scenario exists
            existing_scenario = await self.session.execute(
                _select(Scenario).where(Scenario.hotel_id == hotel_id, Scenario.zone_id == zone_id)
            )
            scenario = existing_scenario.scalar_one_or_none()

            if scenario:
                # Update existing scenario
                scenario.prompt = new_prompt
                scenario.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                # Create new scenario
                scenario = Scenario(
                    hotel_id=hotel_id,
                    zone_id=zone_id,
                    prompt=new_prompt,
                    default_prompt=new_prompt,
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                self.session.add(scenario)

            await self.session.commit()

            # Get zone name for display
            zone_result = await self.session.execute(_select(Zone.name).where(Zone.id == zone_id))
            zone_name = zone_result.scalar_one_or_none() or zone_code

            # Clear editing state
            state.clear_editing_prompt(msg.user_id)

            # Show success message with updated prompt
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
                SUCCESS_PROMPT_UPDATING_MESSAGE.format(zone_name=zone_name, prompt=new_prompt),
                adapter,
                state,
                inline_keyboard=keyboard,
            )

        except Exception as e:
            logger.error(f"Error handling prompt editing: {e}")
            await adapter.send_message(msg.user_id, ERROR_PROMPT_UPDATING_MESSAGE)
            # Clear editing state even on error
            state.clear_editing_prompt(msg.user_id)

        return {"ok": "true"}

    async def _handle_manager_custom_period_input(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle manager custom period input for reports"""

        try:
            # Clear buttons from previous instruction message
            await self.clear_ui_message_buttons(msg.user_id, adapter, state)

            # Get stored hotel info from state
            hotel_code = state.get_user_state(msg.user_id, "custom_report_hotel_code")
            hotel_short_name = state.get_user_state(msg.user_id, "custom_report_hotel_short_name")

            if not hotel_code:
                logger.error(f"No hotel code found in state for user {msg.user_id}")
                await adapter.send_message(msg.user_id, ERROR_REPORTING_MESSAGE)
                return {"ok": "true"}

            # Parse and validate dates
            start_date, end_date, error_message = self._parse_custom_period(msg.text)

            if error_message:
                # Send error message with back button
                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": BACK_BUTTON,
                                "callback_data": f"{hotel_code}_MGR_REPORTS",
                            }
                        ]
                    ]
                }
                await self.send_and_remember_message(
                    msg.user_id,
                    error_message,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
                return {"ok": "true"}

            # Clear state
            state.clear_user_state(msg.user_id, "awaiting_custom_period")
            state.clear_user_state(msg.user_id, "custom_report_hotel_code")
            state.clear_user_state(msg.user_id, "custom_report_hotel_short_name")

            # Show processing message
            processing_msg = await adapter.send_message(msg.user_id, "⏳ Генерирую отчет, пожалуйста подождите...")

            # Generate report
            reporting_service = ReportingService(self.session)

            # Determine scope (single hotel or all hotels)
            if hotel_short_name == "ALL":
                # Get all hotels for the manager

                manager_repo = ManagerRepository(self.session)
                hotel_codes = await manager_repo.list_hotels(msg.user_id)
            else:
                # Single hotel
                hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
                if not hotel:
                    await adapter.send_message(msg.user_id, NO_HOTEL_FOUND_MESSAGE)
                    return {"ok": "true"}
                hotel_codes = [hotel.short_name]

            # Generate XLSX report (CPU-bound operations run in thread pool)
            xlsx_bytes = await reporting_service.export_xlsx(
                hotels_scope=hotel_codes, date_from=start_date, date_to=end_date
            )

            # Delete processing message
            if processing_msg:
                await adapter.delete_message(msg.user_id, processing_msg)

            # Send report
            hotel_info = await self.catalog_repo.get_hotel_by_code(hotel_code)
            scope_name = (
                "По всем доступным отелям"
                if hotel_short_name == "ALL"
                else f"Отель {hotel_info.name if hotel_info else hotel_code}"
            )

            period_text = f"с {start_date.strftime('%d.%m.%Y')} по {end_date.strftime('%d.%m.%Y')}"

            await adapter.send_document_bytes(
                msg.user_id,
                filename=f"feedback_report_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx",
                data=xlsx_bytes,
                caption=f"✅ Отчет готов\n\n📊 Период: {period_text}\n🏨 {scope_name}",
            )

            # Small delay to ensure report message is sent
            await asyncio.sleep(1)

            # Redirect to menu
            menu_msg = IncomingMessage(
                channel="telegram",
                user_id=msg.user_id,
                text=f"{hotel_code}_MENU",
                callback_id=None,
                payload={},
            )
            menu_service = MenuService(self.session)
            await menu_service.handle_menu_request(menu_msg, adapter, state)

        except Exception as e:
            logger.error(f"Error generating custom period report: {e}")
            await adapter.send_message(msg.user_id, ERROR_REPORTING_MESSAGE)
            # Clear state on error
            state.clear_user_state(msg.user_id, "awaiting_custom_period")
            state.clear_user_state(msg.user_id, "custom_report_hotel_code")
            state.clear_user_state(msg.user_id, "custom_report_hotel_short_name")

        return {"ok": "true"}

    def _parse_custom_period(self, text: str) -> tuple[datetime | None, datetime | None, str | None]:
        """
        Parse custom period from user input.
        Expected format: "ОТ ДД.ММ.ГГГГ ДО ДД.ММ.ГГГГ"
        Returns: (start_date, end_date, error_message)
        """

        # Normalize input: convert to uppercase and clean extra spaces
        text = text.strip().upper()
        text = re.sub(r"\s+", " ", text)

        # Pattern to match the date format
        pattern = r"ОТ\s+(\d{2})\.(\d{2})\.(\d{4})\s+ДО\s+(\d{2})\.(\d{2})\.(\d{4})"
        match = re.match(pattern, text)

        if not match:
            return None, None, INVALID_DATE_FORMAT_MESSAGE

        try:
            # Extract dates
            start_day, start_month, start_year = match.groups()[:3]
            end_day, end_month, end_year = match.groups()[3:]

            # Create datetime objects
            start_date = datetime(int(start_year), int(start_month), int(start_day))
            end_date = datetime(int(end_year), int(end_month), int(end_day))

            # Validate date range
            if start_date >= end_date:
                return None, None, INVALID_DATE_RANGE_MESSAGE

            # Don't allow dates too far in the future (e.g., more than 1 year ahead)
            max_future_date = datetime.now() + timedelta(days=365)
            if start_date > max_future_date or end_date > max_future_date:
                return None, None, "❌ Дата не может быть больше года в будущем."

            return start_date, end_date, None

        except ValueError as e:
            logger.error(f"Error parsing dates: {e}")
            return None, None, INVALID_DATE_FORMAT_MESSAGE

    async def _handle_admin_add_user_input(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
        admin_data: dict,
    ) -> Dict[str, str]:
        """Handle admin add user input process"""
        try:
            # Check what data we're waiting for
            if "telegram_id" not in admin_data:
                # Store telegram_id and ask for phone number
                admin_data["telegram_id"] = msg.text.strip()
                state.set_admin_add_user_data(msg.user_id, admin_data)

                await self.send_and_remember_message(
                    msg.user_id,
                    ENTER_PHONE_NUMBER_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            elif "phone_number" not in admin_data:
                # Store phone_number and create user, preprocess to keep only digits
                raw_phone = msg.text.strip()
                admin_data["phone_number"] = "".join(c for c in raw_phone if c.isdigit())

                # Create user and assignment
                logger.info(f"Admin data: {admin_data}")
                # Use channel_type from admin_data if available, otherwise fallback to msg.channel
                if "channel_type" in admin_data:
                    channel_type = admin_data["channel_type"]
                else:
                    channel_type = ChannelType.TELEGRAM if msg.channel == "telegram" else ChannelType.MAX
                success = await self.admin_user_service.create_user_and_assignment(
                    telegram_id=admin_data["telegram_id"],
                    phone_number=admin_data["phone_number"],
                    hotel_id=admin_data["hotel_id"],
                    role_id=admin_data["role_id"],
                    channel_type=channel_type,
                )

                if success:
                    # Get hotel and role names for display
                    hotel = await self.catalog_repo.get_hotel_by_id(admin_data["hotel_id"])

                    result = await self.session.execute(_select(Role).where(Role.id == admin_data["role_id"]))
                    role = result.scalars().first()

                    hotel_name = hotel.name if hotel else admin_data["hotel_id"]
                    role_name = role.name if role else admin_data["role_id"]

                    await self.send_and_remember_message(
                        msg.user_id,
                        SUCCESS_USER_ADDITION_MESSAGE.format(
                            telegram_id=admin_data["telegram_id"],
                            phone_number=admin_data["phone_number"],
                            hotel_name=hotel_name,
                            role=role_name,
                        ),
                        adapter,
                        state,
                    )
                else:
                    await self.send_and_remember_message(
                        msg.user_id,
                        ERROR_USER_ADDITION_MESSAGE,
                        adapter,
                        state,
                    )

                # Clear admin data
                state.clear_admin_add_user_data(msg.user_id)

                # Show admin menu
                keyboard = adapter.admin_menu_keyboard()
                await self.send_and_remember_message(
                    msg.user_id,
                    ADMIN_MENU_MESSAGE,
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )

                return {"ok": "true"}

        except Exception as e:
            logger.error(f"Error handling admin add user input: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_HAPPENED_MESSAGE,
                adapter,
                state,
            )
            return {"ok": "true"}

    async def _handle_admin_phone_input(
        self,
        msg: IncomingMessage,
        adapter: ChannelAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """Handle admin phone number input for user search"""
        try:
            phone_number = msg.text.strip()

            # Clear waiting state
            state.clear_admin_waiting_for_phone(msg.user_id)

            # Search for user by phone number
            user_data = await self.admin_user_service.search_user_by_phone(phone_number)

            if not user_data:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_FOUND_USER_MESSAGE.format(phone_number=phone_number),
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Format hotels information
            hotels_text = ""
            keyboard_rows = []

            for hotel in user_data["hotels"]:
                status = ACTIVE_USER_MESSAGE if hotel["is_active"] else DEACTIVATED_USER_MESSAGE
                hotels_text += f"• {hotel['name']} ({hotel['code']}) - {hotel['role']} - {status}\n"

                # Add button for each hotel
                keyboard_rows.append(
                    [
                        {
                            "text": CHANGE_USER_STATUS_MESSAGE.format(hotel_name=hotel["name"]),
                            "callback_data": f"ADMIN_EDIT_HOTEL_{user_data['telegram_id']}_{hotel['code']}",
                        }
                    ]
                )

            # Add back button
            keyboard_rows.append([{"text": BACK_BUTTON, "callback_data": "ADMIN_USER_MANAGEMENT"}])

            keyboard = {"inline_keyboard": keyboard_rows}

            # Create message text
            message_text = USER_INFORMATION_MESSAGE.format(
                telegram_id=user_data["telegram_id"],
                phone_number=user_data["phone_number"],
                hotels_text=hotels_text,
            )

            await self.send_and_remember_message(
                msg.user_id,
                message_text,
                adapter,
                state,
                inline_keyboard=keyboard,
            )

        except Exception as e:
            logger.error(f"Error handling admin phone input: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_SEARCHING_USER_MESSAGE,
                adapter,
                state,
            )
            state.clear_admin_waiting_for_phone(msg.user_id)

        return {"ok": "true"}

    async def _handle_admin_hotel_description_input(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle admin hotel description input"""
        try:
            hotel_code = state.get_user_state(msg.user_id, "editing_hotel_description")
            if not hotel_code:
                return {"ok": "true"}

            # Clear the editing state
            state.clear_user_state(msg.user_id, "editing_hotel_description")

            # Update hotel description
            success = await self.admin_user_service.update_hotel_description(hotel_code, msg.text)

            if success:
                # Get updated hotel info
                hotel_info = await self.admin_user_service.get_hotel_info(hotel_code)

                if hotel_info:
                    # Format zones list
                    zones_text = ""
                    if hotel_info["zones"]:
                        for zone in hotel_info["zones"]:
                            status = DISABLED_MESSAGE if zone["disabled_at"] else ACTIVE_MESSAGE
                            adult_only = " <b>(Взрослая ⭐️)</b>" if zone["is_adult"] else " <b>(Детская 👍👎)</b>"
                            zones_text += f"• {zone['name']}{adult_only} - {status}\n"
                    else:
                        zones_text = "Нет зон"

                    # Create message text
                    message_text = SUCCESS_HOTEL_DESCRIPTION_UPDATING_MESSAGE.format(
                        hotel_name=hotel_info["name"],
                        hotel_short_name=hotel_info["short_name"],
                        description=hotel_info["description"],
                        guests_count=hotel_info["guests_count"],
                        zones_text=zones_text,
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
                else:
                    await self.send_and_remember_message(
                        msg.user_id,
                        ERROR_GETTING_HOTEL_INFO_MESSAGE,
                        adapter,
                        state,
                    )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    ERROR_HOTEL_DESCRIPTION_UPDATING_MESSAGE,
                    adapter,
                    state,
                )

        except Exception as e:
            logger.error(f"Error handling admin hotel description input: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_HOTEL_DESCRIPTION_UPDATING_MESSAGE,
                adapter,
                state,
            )
            state.clear_user_state(msg.user_id, "editing_hotel_description")

        return {"ok": "true"}

    async def _handle_admin_hotel_name_input(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle admin hotel name input"""
        try:
            hotel_code = state.get_user_state(msg.user_id, "editing_hotel_name")
            if not hotel_code:
                return {"ok": "true"}

            # Clear the editing state
            state.clear_user_state(msg.user_id, "editing_hotel_name")

            # Update hotel name
            success = await self.admin_user_service.update_hotel_name(hotel_code, msg.text)

            if success:
                # Get updated hotel info
                hotel_info = await self.admin_user_service.get_hotel_info(hotel_code)

                if hotel_info:
                    # Format zones list
                    zones_text = ""
                    if hotel_info["zones"]:
                        for zone in hotel_info["zones"]:
                            status = DISABLED_MESSAGE if zone["disabled_at"] else ACTIVE_MESSAGE
                            adult_only = " <b>(Взрослая ⭐️)</b>" if zone["is_adult"] else " <b>(Детская 👍👎)</b>"
                            zones_text += f"• {zone['name']}{adult_only} - {status}\n"
                    else:
                        zones_text = "Нет зон"

                    # Create message text
                    message_text = SUCCESS_HOTEL_NAME_UPDATING_MESSAGE.format(
                        hotel_name=hotel_info["name"],
                        hotel_short_name=hotel_info["short_name"],
                        description=hotel_info["description"],
                        guests_count=hotel_info["guests_count"],
                        zones_text=zones_text,
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
                else:
                    await self.send_and_remember_message(
                        msg.user_id,
                        ERROR_GETTING_HOTEL_INFO_MESSAGE,
                        adapter,
                        state,
                    )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    ERROR_HOTEL_NAME_UPDATING_MESSAGE,
                    adapter,
                    state,
                )

        except Exception as e:
            logger.error(f"Error handling admin hotel name input: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_HOTEL_NAME_UPDATING_MESSAGE,
                adapter,
                state,
            )
            state.clear_user_state(msg.user_id, "editing_hotel_name")

        return {"ok": "true"}

    async def _handle_admin_add_zone_input(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle admin add zone name input"""
        try:
            hotel_code = state.get_admin_adding_zone(msg.user_id)
            if not hotel_code:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_CODE_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            zone_name = msg.text.strip()
            if not zone_name:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_NAME_EMPTY_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Get hotel to check uniqueness
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)
            if not hotel:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_FOUND_WITH_CODE_MESSAGE.format(hotel_code=hotel_code),
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Generate unique short name
            base_short_name = zone_name[:3].upper()
            short_name = base_short_name

            # Check if short name is unique, if not, try variations
            counter = 1
            while not await self.admin_user_service.check_zone_short_name_unique(str(hotel.id), short_name):
                if counter > 99:  # Prevent infinite loop
                    await self.send_and_remember_message(
                        msg.user_id,
                        UNSUCCESSFUL_ZONE_SHORT_NAME_GENERATION_MESSAGE.format(zone_name=zone_name),
                        adapter,
                        state,
                    )
                    return {"ok": "true"}

                # Try with counter suffix
                short_name = (
                    f"{base_short_name[:2]}{counter:01d}" if counter < 10 else f"{base_short_name[:1]}{counter:02d}"
                )
                counter += 1

            success = await self.admin_user_service.create_zone(
                hotel_code=hotel_code,
                name=zone_name,
                short_name=short_name,
                is_adult=False,
            )

            if success:
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
                    SUCCESS_ZONE_ADDITION_MESSAGE.format(
                        zone_name=zone_name,
                        short_name=short_name,
                        adult_text=FOR_CHILDREN_MESSAGE,
                    ),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_ADD_ERROR_MESSAGE,
                    adapter,
                    state,
                )

        except Exception as e:
            logger.error(f"Error handling admin add zone input: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_ZONE_ADD_ERROR_MESSAGE,
                adapter,
                state,
            )
        finally:
            state.clear_admin_adding_zone(msg.user_id)

        return {"ok": "true"}

    async def _handle_admin_edit_zone_name_input(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle admin edit zone name input"""
        try:
            zone_id = state.get_admin_editing_zone_name(msg.user_id)
            if not zone_id:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_ID_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            new_name = msg.text.strip()
            if not new_name:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_NAME_EMPTY_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            success = await self.admin_user_service.update_zone(zone_id=zone_id, name=new_name)

            if success:
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
                    SUCCESS_ZONE_NAME_UPDATING_MESSAGE.format(new_name=new_name),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    ERROR_ZONE_NAME_UPDATING_MESSAGE,
                    adapter,
                    state,
                )

        except Exception as e:
            logger.error(f"Error handling admin edit zone name input: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_ZONE_NAME_UPDATING_MESSAGE,
                adapter,
                state,
            )
        finally:
            state.clear_admin_editing_zone_name(msg.user_id)

        return {"ok": "true"}

    async def _handle_admin_edit_zone_description_input(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle admin edit zone description input"""
        try:
            zone_id = state.get_admin_editing_zone_description(msg.user_id)
            if not zone_id:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_ZONE_ID_FOUND_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            new_description = msg.text.strip()
            success = await self.admin_user_service.update_zone(zone_id=zone_id, description=new_description)

            if success:
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
                    SUCCESS_ZONE_DESCRIPTION_UPDATING_MESSAGE.format(new_description=new_description),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    ERROR_ZONE_DESCRIPTION_UPDATING_MESSAGE,
                    adapter,
                    state,
                )

        except Exception as e:
            logger.error(f"Error handling admin edit zone description input: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                ERROR_ZONE_DESCRIPTION_UPDATING_MESSAGE,
                adapter,
                state,
            )
        finally:
            state.clear_admin_editing_zone_description(msg.user_id)

        return {"ok": "true"}

    async def _handle_admin_add_hotel_input(
        self, msg: IncomingMessage, adapter: ChannelAdapter, state: InMemoryState
    ) -> Dict[str, str]:
        """Handle admin add hotel name input"""
        try:
            hotel_name = msg.text.strip()
            if not hotel_name:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_NAME_EMPTY_MESSAGE,
                    adapter,
                    state,
                )
                return {"ok": "true"}

            # Generate unique short name
            base_short_name = hotel_name[:3].upper()
            short_name = base_short_name

            # Check if short name is unique, if not, try variations
            counter = 1
            while not await self.admin_user_service.check_hotel_short_name_unique(short_name):
                if counter > 99:  # Prevent infinite loop
                    await self.send_and_remember_message(
                        msg.user_id,
                        UNSUCCESSFUL_HOTEL_SHORT_NAME_GENERATION_MESSAGE.format(hotel_name=hotel_name),
                        adapter,
                        state,
                    )
                    return {"ok": "true"}

                # Try with counter suffix
                short_name = (
                    f"{base_short_name[:2]}{counter:01d}" if counter < 10 else f"{base_short_name[:1]}{counter:02d}"
                )
                counter += 1

            success = await self.admin_user_service.create_hotel(
                name=hotel_name,
                short_name=short_name,
                description="Описание отеля",
                timezone="Europe/Moscow",
            )

            if success:
                # Create keyboard with options
                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": ADMIN_BRANCH_MANAGEMENT_SELECT_BRANCH_BUTTON,
                                "callback_data": "ADMIN_SELECT_BRANCH",
                            }
                        ],
                        [
                            {
                                "text": MAIN_MENU_BUTTON,
                                "callback_data": "ADMIN_MAIN_MENU",
                            }
                        ],
                    ]
                }

                await self.send_and_remember_message(
                    msg.user_id,
                    SUCCESS_HOTEL_ADDITION_MESSAGE.format(hotel_name=hotel_name, short_name=short_name),
                    adapter,
                    state,
                    inline_keyboard=keyboard,
                )
            else:
                await self.send_and_remember_message(
                    msg.user_id,
                    NO_HOTEL_ADD_ERROR_MESSAGE,
                    adapter,
                    state,
                )

        except Exception as e:
            logger.error(f"Error handling admin add hotel input: {e}")
            await self.send_and_remember_message(
                msg.user_id,
                NO_HOTEL_ADD_ERROR_MESSAGE,
                adapter,
                state,
            )
        finally:
            state.clear_admin_adding_hotel(msg.user_id)

        return {"ok": "true"}
