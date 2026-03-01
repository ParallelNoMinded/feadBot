"""
Service for handling Telegram webhook requests.
Encapsulates all webhook processing logic in a clean, testable way.
"""

from typing import Dict, Optional, Tuple

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.channel import ChannelAdapter, IncomingMessage
from app.adapters.telegram.adapter import TelegramAdapter
from app.config.settings import settings
from app.core.state import InMemoryState
from app.services.base import BaseService
from app.services.message_router import MessageRouter
from app.services.registration import RegistrationService
from app.utils.payload import process_sp_early
from app.utils.security import require_api_key, require_role

logger = structlog.get_logger(__name__)


class TelegramWebhookHandler(BaseService):
    """Handles Telegram webhook processing with clean separation of concerns."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.message_router = MessageRouter(session)

    async def handle_webhook(
        self,
        payload: bytes,
        headers: Dict[str, str],
        secret_token: str,
        adapter: TelegramAdapter,
        state: InMemoryState,
    ) -> Dict[str, str]:
        """
        Main entry point for webhook processing.

        Args:
            payload: Raw webhook payload
            headers: Request headers
            secret_token: Telegram secret token for validation
            adapter: Telegram adapter instance
            state: In-memory state manager

        Returns:
            Response dictionary
        """
        try:
            # Step 1: Validate and parse
            msg = await self._validate_and_parse(payload, headers, secret_token, adapter)
            if not msg:
                return {"ok": "true"}

            # Step 2: Handle user registration if needed
            registration_result = await self._handle_user_registration(msg, state, adapter)
            if registration_result:
                return registration_result

            # Step 3: Process message context and payload
            ctx, parsed_early = await self._process_message_context(msg, state)

            # Step 4: Route message to appropriate handler
            return await self.message_router.route_message(msg, adapter, state, ctx, parsed_early)

        except Exception as e:
            logger.error("webhook.processing.error", error=str(e), exc_info=True)
            return {"ok": "false"}

    async def _validate_and_parse(
        self,
        payload: bytes,
        headers: Dict[str, str],
        secret_token: str,
        adapter: TelegramAdapter,
    ) -> Optional[IncomingMessage]:
        """Validate webhook and parse message."""
        logger.info("telegram.webhook.received", payload=payload)

        # Verify secret
        if not require_api_key(secret_token) or secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
            raise ValueError("Invalid secret token")

        # Parse webhook
        msg = await adapter.parse_webhook(payload, headers)
        if not msg:
            return None

        logger.info(
            "telegram.webhook.parsed",
            user_id=msg.user_id,
            text=msg.text if msg.text else None,
            callback_id=msg.callback_id if msg.callback_id else None,
            has_payload=bool((msg.payload or {})),
        )

        return msg

    async def _handle_user_registration(
        self, msg: IncomingMessage, state: InMemoryState, adapter: ChannelAdapter
    ) -> Optional[Dict[str, str]]:
        """Handle user registration if user is not registered."""
        # Check if user exists in database
        if await require_role(self.session, msg.user_id):
            return None

        logger.info(
            "User not found in database, checking registration state",
            user_id=msg.user_id,
        )

        # Check if user has active registration session
        active_registration = state.get_registration(msg.channel, msg.user_id)

        if not active_registration:
            logger.info(
                "No active registration found, starting registration process",
                user_id=msg.user_id,
            )
            registration_service = RegistrationService(self.session, adapter)
            await registration_service.start(msg.user_id)
            return {"ok": "true"}

        return None

    async def _process_message_context(self, msg: IncomingMessage, state: InMemoryState) -> Tuple[Dict, Dict]:
        """Process message context and early payload parsing."""
        # Process early payload parsing
        parsed_early = (msg.payload or {}).get("_parsed", {})
        sp_early = parsed_early.get("start_payload")
        if sp_early:
            existing_context = (msg.payload or {}).get("_context", {})
            msg.payload = process_sp_early(sp_early, msg)
            if existing_context:
                current_context = msg.payload.get("_context", {})
                current_context.update(existing_context)
                msg.payload["_context"] = current_context

        # Get context from payload
        ctx = (msg.payload or {}).get("_context", {})
        if ctx.get("hotel"):
            state.set_selected_hotel(msg.user_id, ctx.get("hotel"))

        logger.info(
            "telegram.process_message_context",
            ctx=ctx,
            has_hotel=bool(ctx.get("hotel")),
            has_zone=bool(ctx.get("zone")),
        )

        return ctx, parsed_early
