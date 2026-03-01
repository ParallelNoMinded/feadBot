from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.repositories.pms_reservations import ReservationsRepository
from app.repositories.user_pms_reservation import ReservationRepository
from app.services.base import BaseService
from app.services.pms_user_sync import PMSUserSyncService
from shared_models import Reservation, User, ReservationStatus

logger = structlog.get_logger(__name__)


# Most statuses commented for MVP to avoid storing non-critical states.
# Uncomment when full status handling is needed.
PMS_STATUS_MAPPING = {
    "аннуляция": ReservationStatus.CANCELLED,
    "отказ": ReservationStatus.CANCELLED,
    # "гарантированная бронь": ReservationStatus.IN_HOUSE,
    "заезд": ReservationStatus.IN_HOUSE,
    "выезд": ReservationStatus.CHECKED_OUT,
    "выехал": ReservationStatus.CHECKED_OUT,
    "выселение": ReservationStatus.CHECKED_OUT,
    "checked_out": ReservationStatus.CHECKED_OUT,
    "незаезд": ReservationStatus.CANCELLED,
    "in_house": ReservationStatus.IN_HOUSE,
    # "бронь": ReservationStatus.IN_HOUSE,
    # "ожидает номер": ReservationStatus.IN_HOUSE,
    # "travelline": ReservationStatus.IN_HOUSE,
    # "переселение": ReservationStatus.INHOUSE,  # need to be tested, maybe it not exists in PMS
}


class PMSService(BaseService):
    """Service for PMS (Property Management System) data synchronization."""

    def __init__(self, session: AsyncSession, settings: Settings):
        super().__init__(session)
        self.settings = settings
        self.repository = ReservationsRepository(session)
        self.user_reservation_repo = ReservationRepository(session)
        self.pms_user_sync = PMSUserSyncService(session)

    async def sync_reservations(self, reservations_data: list[dict]) -> None:
        """
        Process and sync reservations data to database.

        At the first error, stops processing and raises exception.
        No data is written to database if any error occurs.
        Does NOT commit or rollback transaction - transaction management
        is handled by caller.

        Args:
            reservations_data: List of reservation dictionaries from PMS

        Raises:
            ValueError: if any reservation fails to process
            Exception: any other error during processing
        """
        reservation_ref = None
        processed_count = 0
        skipped_count = 0
        try:
            for reservation_dict in reservations_data:
                reservation_ref = reservation_dict.get("Ref")
                reservation = self._parse_reservation(reservation_dict)

                if reservation is None:
                    skipped_count += 1
                    continue

                await self.repository.upsert(reservation)
                await self._create_user_reservation_links(reservation)
                processed_count += 1

            logger.info(
                "PMS sync completed",
                total_count=len(reservations_data),
                processed_count=processed_count,
                skipped_count=skipped_count,
            )

        except Exception as e:
            logger.error(
                "PMS sync error",
                error=str(e),
                reservation_ref=reservation_ref,
                exc_info=True,
            )
            raise

    async def _create_user_reservation_links(self, reservation: Reservation) -> None:
        """Create links between users and reservations_users table."""
        reservation_id = reservation.id
        phone_numbers = reservation.phone_numbers or []

        if not phone_numbers:
            return

        for phone in phone_numbers:
            if not phone:
                continue

            users_result = await self.session.execute(select(User).where(User.phone_number == phone))
            users = users_result.scalars().all()

            if not users:
                logger.warning(
                    "No users found for phone number",
                    phone_number=phone,
                    reservation_id=str(reservation_id),
                )
                continue

            for user in users:
                try:
                    await self.user_reservation_repo.create(user.id, reservation_id)
                except Exception as e:
                    logger.error(
                        "Failed to create user-reservation link",
                        user_id=str(user.id),
                        reservation_id=str(reservation_id),
                        phone=phone,
                        error=str(e),
                    )

    @staticmethod
    def _parse_reservation(data: dict) -> Reservation | None:
        """Parse reservation data from PMS format.

        Args:
            data: Reservation dictionary from PMS

        Returns:
            Reservation object or None if status is unknown

        Raises:
            ValueError: if required fields are missing or invalid
        """
        ref = data.get("Ref")
        if not ref:
            raise ValueError("Ref field is required")

        arrival_date_str = data.get("ArrivalDate")
        if not arrival_date_str:
            raise ValueError("ArrivalDate field is required")

        departure_date_str = data.get("DepartureDate")
        if not departure_date_str:
            raise ValueError("DepartureDate field is required")

        hotel = data.get("Hotel")
        if not hotel or not isinstance(hotel, str) or not hotel.strip():
            raise ValueError(f"Hotel field is required and cannot be empty (got: {hotel})")

        arrival_date = datetime.strptime(arrival_date_str, "%d.%m.%Y %H:%M:%S")
        arrival_date = arrival_date.replace(tzinfo=timezone.utc)

        departure_date = datetime.strptime(departure_date_str, "%d.%m.%Y %H:%M:%S")
        departure_date = departure_date.replace(tzinfo=timezone.utc)

        guest_phones: list[str] = []
        seen_phones: set[str] = set()

        def add_phone(raw_phone: str | None) -> None:
            if not raw_phone:
                return
            normalized = PMSUserSyncService.normalize_phone(raw_phone)
            if not normalized or normalized in seen_phones:
                return
            seen_phones.add(normalized)
            guest_phones.append(normalized)

        add_phone(data.get("PhoneNumber"))
        if "Guest" in data and isinstance(data["Guest"], list):
            for guest in data["Guest"]:
                if guest.get("PhoneNumber"):
                    add_phone(guest["PhoneNumber"])

        raw_status = data.get("ReservationStatus") or ""
        status_key = raw_status.strip().lower()
        status = PMS_STATUS_MAPPING.get(status_key)

        if status is None:
            logger.warning(
                "Unknown reservation status, defaulting to CHECKED_OUT",
                status=raw_status,
                reservation_ref=ref,
            )
            status = ReservationStatus.CHECKED_OUT

        phone_numbers = guest_phones
        if not phone_numbers:
            logger.warning(
                "Reservation with Заезд status has no guest phone numbers",
                reservation_ref=ref,
            )
            return None

        reservation = Reservation(
            arrival_date=arrival_date,
            departure_date=departure_date,
            phone_numbers=phone_numbers,
            hotel=hotel,
            status=status,
            pms_incoming_status=raw_status,
            data=data,
        )

        return reservation
