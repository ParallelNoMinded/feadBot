from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared_models import Reservation

logger = structlog.get_logger(__name__)


class ReservationsRepository:
    """Repository for PMS reservation data operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, reservation_id: UUID) -> Optional[Reservation]:
        """Get PMS reservation by id (primary key)."""
        result = await self.session.execute(
            select(Reservation).where(Reservation.id == reservation_id)
        )
        return result.scalars().first()

    async def upsert(
        self, reservation: Reservation
    ) -> tuple[type[Reservation] | Reservation, str]:
        """
        Insert or update a PMS reservation using SQLAlchemy merge.

        Returns:
            Tuple of (reservation instance, operation_type)
        """
        existing = await self.get_by_id(reservation.id)
        operation_type = "updated" if existing else "created"

        if existing:
            existing.updated_at = datetime.now(timezone.utc)
            for key in [
                "arrival_date",
                "departure_date",
                "phone_numbers",
                "hotel",
                "status",
                "pms_incoming_status",
                "data",
            ]:
                setattr(existing, key, getattr(reservation, key))
            await self.session.flush()
            result = existing
        else:
            reservation.created_at = reservation.created_at or datetime.now(
                timezone.utc
            )
            self.session.add(reservation)
            await self.session.flush()
            result = reservation

        logger.info(
            f"{operation_type.capitalize()} PMS reservation: {reservation.id}",
            operation=operation_type,
            reservation_id=str(reservation.id),
        )

        return result, operation_type

    async def get_by_phone_number(self, phone_number: str) -> list[Reservation]:
        """
        Get all PMS reservations for a phone number.

        Searches by exact phone number match in the phone_numbers array.
        Note: phone_number should be normalized before calling this method.
        """
        if not phone_number:
            return []

        result = await self.session.execute(
            select(Reservation).where(
                Reservation.phone_numbers.contains([phone_number])
            )
        )
        return list(result.scalars().all())
