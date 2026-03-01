"""
Handler for PMS reservations webhook endpoint.
Encapsulates request processing, validation, and service orchestration.
"""

from typing import Dict

import structlog
from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.services.base import BaseService
from app.services.pms import PMSService
from app.services.pms_user_sync import PMSUserSyncService

logger = structlog.get_logger(__name__)


def log_request_details(request: Request, payload: dict = None):
    """Log all request details for debugging purposes"""

    logger.info("REQUEST HEADERS:")
    for key, value in request.headers.items():
        logger.info("  %s: %s", key, value)

    logger.info("QUERY PARAMETERS:")
    for key, value in request.query_params.items():
        logger.info("  %s: %s", key, value)

    if payload:
        logger.info("REQUEST PAYLOAD: %s", payload)


class ReservationsHandler(BaseService):
    """Handles PMS reservations webhook processing with clean separation of concerns."""

    def __init__(self, session: AsyncSession, settings: Settings):
        super().__init__(session)
        self.settings = settings
        self.pms_service = PMSService(session=session, settings=settings)

    async def handle_reservations_webhook(
        self,
        request: Request,
        token: str,
    ) -> Dict[str, str]:
        """
        Main entry point for PMS reservations webhook processing.

        Args:
            request: FastAPI Request object
            token: Authentication token from header

        Returns:
            Response dictionary

        Raises:
            HTTPException: if token is empty/invalid or reservations
                list is empty
            ValueError: if payload is invalid
            Exception: if sync fails
        """
        # Validate token
        if not token:
            logger.warning("PMS reservations request rejected: empty token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication token is required",
            )

        if token != self.settings.PMS_RESERVATIONS_TOKEN:
            logger.warning("PMS reservations request rejected: invalid token")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token",
            )

        payload = await request.json()
        log_request_details(request, payload)

        reservations_data = payload.get("reservations", [])

        if not reservations_data or not isinstance(reservations_data, list):
            logger.warning("PMS reservations request rejected: empty or invalid reservations list")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reservations field must be a non-empty list",
            )

        logger.info(f"PMS reservations request received: {len(reservations_data)} reservations")

        try:
            await self.pms_service.sync_reservations(reservations_data)

            sync_stats = await PMSUserSyncService(self.session).sync_all_users_after_pms_update()

            logger.info(
                "PMS user sync completed",
                users_checked=sync_stats["users_checked"],
                users_updated=sync_stats["users_updated"],
                stays_closed=sync_stats["stays_closed"],
            )

            await self.session.commit()

            return {"ok": "true"}
        except Exception as e:
            await self.session.rollback()
            logger.error(
                "PMS webhook processing failed",
                error=str(e),
                exc_info=True,
            )
            raise
