import asyncio
from datetime import datetime, timezone

import structlog

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.adapters.max.adapter import MaxAdapter
from app.adapters.telegram.adapter import TelegramAdapter
from app.config.settings import settings
from app.core.db import AsyncSessionFactory
from app.core.state import STATE, InMemoryState
from app.repositories.user import UserRepository
from app.services.base import BaseService
from app.services.feedback_processor import FeedbackProcessorService
from app.services.ui_message import UIMessageService
from shared_models import Feedback, FeedbackStatus, Sentiment

logger = structlog.get_logger(__name__)


async def run_feedback_session_gc(interval_seconds: float = 5.0) -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            sessions = list(STATE.feedback_sessions.items())
            for key, s in sessions:
                # Extract channel and user_id from key
                # Format: "channel:user_id"
                key_parts = key.split(":", 1)
                if len(key_parts) != 2:
                    continue
                channel = key_parts[0]
                user_id = key_parts[1]
                last = s.get("last_activity")
                rating = s.get("rating")
                if not last:
                    continue

                # Skip if session is already ended (no active_feedback_id)
                if key not in STATE.feedback_sessions or not s.get("active_feedback_id"):
                    continue
                # Normalize naive datetimes to UTC if present (backward-compat)
                if last.tzinfo is None:
                    try:
                        last = last.replace(tzinfo=timezone.utc)
                    except Exception:
                        continue
                timeout = (
                    settings.FEEDBACK_SESSION_WAITING_TIME_WITH_COMMENT
                    if s.get("comment_received")
                    else settings.FEEDBACK_SESSION_WAITING_TIME
                )

                if (now - last).total_seconds() < timeout:
                    continue

                # Session timeout - process feedback and send acknowledgment
                hotel_code = s.get("hotel")
                active_feedback_id = s.get("active_feedback_id")
                logger.info(
                    f"feedback.session.timeout: channel={channel}, "
                    f"user_id={user_id}, hotel={hotel_code}, rating={rating}, "
                    f"feedback_id={active_feedback_id}"
                )

                # End session immediately to prevent duplicate processing
                STATE.end_feedback_session(channel, user_id)

                # Process feedback if we have an active feedback ID
                if active_feedback_id:
                    asyncio.create_task(
                        _process_feedback_and_ack(
                            active_feedback_id,
                            user_id,
                            hotel_code,
                            channel,
                            STATE,
                        )
                    )

                    async with AsyncSessionFactory() as session:
                        adapter = _get_adapter_for_channel(channel)
                        await BaseService(session).clear_ui_messages(user_id, adapter, STATE)
            await asyncio.sleep(interval_seconds)
        except Exception as exc:
            logger.warning("feedback.gc.error", error=str(exc))
            await asyncio.sleep(1.0)


async def _process_feedback_and_ack(
    feedback_id: str,
    user_id: str,
    hotel_code: str | None,
    channel: str,
    state: InMemoryState,
):
    """Process feedback and send acknowledgment."""
    try:
        # Disable rating UI before processing feedback
        await _disable_rating_ui_on_timeout(user_id, channel, state)
        async with AsyncSessionFactory() as session:
            result = await FeedbackProcessorService(session).process_feedback_session_relevant(feedback_id, state)
            if result is not None:
                feedback, combined_input, session_id, sentiment = result
                asyncio.create_task(
                    _run_bg_and_open(
                        feedback.id,
                        combined_input,
                        session_id,
                        sentiment,
                        state,
                    )
                )
            await session.commit()
        # Send acknowledgment after processing
    except Exception as e:
        logger.error("feedback.processing.error", feedback_id=feedback_id, error=str(e))

    await _ack_after_delay(user_id, hotel_code, channel)


async def _disable_rating_ui_on_timeout(user_id: str, channel: str, state: InMemoryState) -> None:
    """Disable rating UI when feedback session times out"""
    try:
        # Get current feedback session
        current_session = state.get_feedback_session(channel, user_id)
        if not current_session:
            return

        # Get rating message ID from state
        rating_message_id = state.get_rating_message_id(channel, user_id)
        if not rating_message_id:
            return

        # Get hotel and zone from current session
        hotel_code = current_session.get("hotel")
        zone_code = current_session.get("zone")
        current_rating = current_session.get("rating")

        if not hotel_code or not zone_code:
            return

        adapter = _get_adapter_for_channel(channel)

        # Create a minimal message object
        msg = IncomingMessage(
            channel=channel,
            user_id=user_id,
            text="",
            payload={},
            callback_id=None,
        )

        # Create UI message service and disable rating UI
        async with AsyncSessionFactory() as session:
            ui_service = UIMessageService(session)
            await ui_service.disable_rating_ui(
                msg,
                adapter,
                state,
                hotel_code,
                zone_code,
                current_rating,
                rating_message_id,
            )

    except Exception as e:
        logger.error(f"Error disabling rating UI on timeout: {e}")


async def _run_bg_and_open(
    feedback_id: str,
    combined_input: str,
    session_id: str,
    sentiment: Sentiment,
    state: InMemoryState,
):
    """Run background processing then set status to OPENED.

    Uses a fresh DB session to avoid cross-session entity issues.
    """
    try:
        async with AsyncSessionFactory() as session:
            processor = FeedbackProcessorService(session)

            # Reload feedback in this session
            fb: Feedback | None = await session.get(Feedback, feedback_id)
            if not fb:
                logger.error(f"Feedback not found: {feedback_id}")
                return

            # Run background processing
            await processor.process_feedback_session_background(fb, combined_input, session_id, sentiment, state, True)

            # Set status to OPENED after full processing
            fb.status = FeedbackStatus.OPENED
            await session.commit()
    except Exception as exc:
        logger.warning("feedback.bg_or_open.error", error=str(exc), feedback_id=feedback_id)


async def _ack_after_delay(uid: str, hotel_code: str | None, channel: str):
    """Send acknowledgment after delay."""
    try:
        await asyncio.sleep(2)

        # Check if user still has an active feedback session to avoid spam
        # Check for both channels to avoid sending duplicate messages
        if STATE.get_feedback_session("telegram", uid) or STATE.get_feedback_session("max", uid):
            logger.info(f"feedback.ack.skipped.session_still_active: " f"user_id={uid}, channel={channel}")
            return

        adapter = _get_adapter_for_channel(channel)

        if hotel_code:
            async with AsyncSessionFactory() as session2:
                # Get last feedback ID for the user
                user_repo = UserRepository(session2)
                last_feedback_id = await user_repo.get_last_feedback_id(uid)

                keyboard = adapter.main_menu_keyboard(hotel_code, last_feedback_id)

                hotel_description = await BaseService(session2).get_hotel_description(hotel_code, uid)

                message_id = await adapter.send_message(uid, hotel_description, inline_keyboard=keyboard)
                if message_id:
                    logger.info(f"feedback.ack.sent.main_menu: user_id={uid}, channel={channel}, hotel={hotel_code}")
                else:
                    logger.warning(
                        f"feedback.ack.failed: user_id={uid}, channel={channel}, hotel={hotel_code}, "
                        f"reason=send_message_returned_none"
                    )
    except Exception as e:
        logger.warning(f"feedback.ack.error: user_id={uid}, channel={channel}, error={str(e)}")


def _get_adapter_for_channel(channel: str) -> ChannelAdapter:
    """Get appropriate adapter for the given channel."""
    if channel == "max":
        return MaxAdapter()
    else:
        # Default to Telegram for backward compatibility
        return TelegramAdapter()
