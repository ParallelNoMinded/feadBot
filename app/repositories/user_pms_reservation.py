from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared_models import ReservationUsers

logger = structlog.get_logger(__name__)


class ReservationRepository:
    """Repository for User-PMS Reservation relationship operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        reservation_id: UUID,
    ) -> ReservationUsers:
        """Create a new User-PMS Reservation relationship."""
        existing = await self.get_by_user_and_reservation(user_id, reservation_id)
        if existing:
            logger.info(
                "User-PMS Reservation relationship already exists",
                user_id=str(user_id),
                reservation_id=str(reservation_id),
            )
            return existing

        relationship = ReservationUsers(
            user_id=user_id,
            reservation_id=reservation_id,
        )
        self.session.add(relationship)
        await self.session.flush()
        logger.info(
            "Created User-PMS Reservation relationship",
            user_id=str(user_id),
            reservation_id=str(reservation_id),
        )
        return relationship

    async def get_by_user_and_reservation(
        self,
        user_id: UUID,
        reservation_id: UUID,
    ) -> Optional[ReservationUsers]:
        """Get relationship by user and reservation."""
        result = await self.session.execute(
            select(ReservationUsers).where(
                ReservationUsers.user_id == user_id,
                ReservationUsers.reservation_id == reservation_id,
            )
        )
        return result.scalars().first()
