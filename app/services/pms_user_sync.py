"""
Service for synchronizing PMS reservation data with registered users.
Handles automatic data enrichment during registration and updates.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.pms_reservations import ReservationsRepository
from app.repositories.user_pms_reservation import ReservationRepository
from app.services.base import BaseService
from shared_models import Hotel, Reservation, User, UserHotel, ReservationStatus, RoleEnum

logger = structlog.get_logger(__name__)


class PMSUserSyncService(BaseService):
    """Service for syncing PMS reservation data with user accounts."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.reservations_repo = ReservationsRepository(session)
        self.user_reservation_repo = ReservationRepository(session)

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Normalize phone number for comparison. Removes non-digit characters except leading +."""
        if not phone:
            return ""
        normalized = phone.lstrip("+")
        return "".join(c for c in normalized if c.isdigit())

    @staticmethod
    def _get_alternative_phone(phone: str) -> Optional[str]:
        """Get alternative phone format (7 <-> 8) if phone is 11 digits."""
        if len(phone) != 11:
            return None
        if phone.startswith("7"):
            return "8" + phone[1:]
        if phone.startswith("8"):
            return "7" + phone[1:]
        return None

    async def _search_reservations_by_phone(self, phone_number: str) -> list[Reservation]:
        """Search reservations by phone number, trying normalized and alternative formats."""
        normalized_phone = self.normalize_phone(phone_number)
        if not normalized_phone:
            return []

        reservations = await self.reservations_repo.get_by_phone_number(normalized_phone)
        if reservations:
            return reservations

        alt_phone = self._get_alternative_phone(normalized_phone)
        if alt_phone:
            reservations = await self.reservations_repo.get_by_phone_number(alt_phone)
            if reservations:
                return reservations

        return []

    @staticmethod
    def _filter_reservations_by_hotel(
        reservations: list[Reservation], hotel: Hotel, use_fuzzy: bool = False
    ) -> list[Reservation]:
        """Filter reservations by hotel, with optional fuzzy matching."""
        if not reservations:
            return []

        matching = [r for r in reservations if r.hotel == hotel.name or r.hotel == hotel.short_name]
        if matching:
            return matching

        if not use_fuzzy:
            return []

        hotel_name_lower = hotel.name.lower()
        hotel_short_lower = hotel.short_name.lower() if hotel.short_name else ""
        matching = [
            r
            for r in reservations
            if r.hotel.lower() == hotel_name_lower
            or r.hotel.lower() == hotel_short_lower
            or hotel_name_lower in r.hotel.lower()
            or hotel_short_lower in r.hotel.lower()
        ]

        if matching:
            logger.info(
                "Found reservation with fuzzy hotel match",
                hotel_hint_name=hotel.name,
                hotel_hint_short=hotel.short_name,
                pms_hotel_name=matching[0].hotel,
            )

        return matching

    @staticmethod
    def _filter_active_reservations(reservations: list[Reservation]) -> list[Reservation]:
        """Filter only active reservations (arrival <= now <= departure)."""
        now = datetime.now(timezone.utc)
        return [
            r
            for r in reservations
            if r.status == ReservationStatus.IN_HOUSE and r.arrival_date <= now <= r.departure_date
        ]

    async def find_reservation_by_phone(self, phone_number: str, hotel_hint: Hotel) -> Optional[Reservation]:
        """Find active reservation by phone number."""
        reservations = await self._search_reservations_by_phone(phone_number)
        if not reservations:
            return None

        active_reservations = self._filter_active_reservations(reservations)
        if not active_reservations:
            return None

        matching = self._filter_reservations_by_hotel(active_reservations, hotel_hint)
        if matching:
            return max(matching, key=lambda r: r.arrival_date)

        return max(active_reservations, key=lambda r: r.arrival_date)

    @staticmethod
    def extract_room_number(reservation: Reservation) -> Optional[str]:
        """Extract room number from reservation data."""
        room_no = reservation.data.get("RoomNo")
        if room_no is None:
            return None
        return str(room_no) if room_no else None

    @staticmethod
    def _normalize_room_number(room: Optional[str]) -> Optional[str]:
        """Normalize room number: strip whitespace, convert empty to None."""
        if not room:
            return None
        normalized = room.strip()
        return normalized if normalized else None

    async def enrich_user_from_reservation(
        self, user: User, hotel: Hotel, reservation: Reservation
    ) -> Optional[UserHotel]:
        """Enrich or create UserHotel record with data from PMS reservation."""
        room_number = self.extract_room_number(reservation)
        reservation_ref_str = str(reservation.id)

        reservation_status = reservation.status

        existing = await self._get_or_resolve_existing_stay(user, hotel, reservation_ref_str)

        existing_with_pms_id = await self.user_hotel_repo.get_by_external_pms_id(reservation_ref_str)

        if existing_with_pms_id:
            if existing_with_pms_id.user_id == user.id and existing_with_pms_id.hotel_id == hotel.id:
                existing = existing_with_pms_id
            else:
                logger.warning(
                    "Found existing UserHotel with same external_pms_id for different user/hotel, clearing it",
                    existing_user_hotel_id=str(existing_with_pms_id.id),
                    existing_user_id=str(existing_with_pms_id.user_id),
                    existing_hotel_id=str(existing_with_pms_id.hotel_id),
                    new_user_id=str(user.id),
                    new_hotel_id=str(hotel.id),
                    external_pms_id=reservation_ref_str,
                )
                existing = await self.user_hotel_repo.get_active_stay_for_update(user.id, hotel.id)
        else:
            existing = await self.user_hotel_repo.get_active_stay(user.id, hotel.id)

        if reservation_status in [ReservationStatus.CHECKED_OUT, ReservationStatus.CANCELLED]:
            user_hotel = await self._handle_checked_out_status(existing, user, hotel, reservation, reservation_ref_str)

            await self._ensure_user_reservation_link(user, reservation)
            return user_hotel

        user_hotel = await self._handle_in_house_status(
            existing, user, hotel, reservation, reservation_ref_str, room_number
        )
        await self._ensure_user_reservation_link(user, reservation)
        return user_hotel

    async def _handle_checked_out_status(
        self,
        existing: Optional[UserHotel],
        user: User,
        hotel: Hotel,
        reservation: Reservation,
        reservation_ref_str: str,
    ) -> Optional[UserHotel]:
        """Handle CHECKED_OUT reservation status - close existing stays."""
        if existing:
            await self.user_hotel_repo.close_stay_for_conflict(existing, reservation.departure_date.date())
            logger.info(
                "Closed UserHotel stay due to CHECKED_OUT status",
                user_id=str(user.id),
                hotel_id=str(hotel.id),
                external_pms_id=reservation_ref_str,
            )
        return existing

    async def _ensure_user_reservation_link(self, user: User, reservation: Reservation) -> None:
        """Ensure ReservationUsers link exists for user and PMS reservation."""

        try:
            await self.user_reservation_repo.create(user.id, reservation.id)
        except Exception as exc:
            logger.error(
                "Failed to ensure user-reservation link",
                user_id=str(user.id),
                reservation_id=str(reservation.id),
                error=str(exc),
            )

    async def _get_or_resolve_existing_stay(
        self, user: User, hotel: Hotel, reservation_ref_str: str
    ) -> Optional[UserHotel]:
        """Get existing stay or resolve conflicts with same external_pms_id."""

        existing_with_pms_id = await self.user_hotel_repo.get_by_external_pms_id_for_update(reservation_ref_str)

        if existing_with_pms_id:
            if existing_with_pms_id.user_id == user.id and existing_with_pms_id.hotel_id == hotel.id:
                return existing_with_pms_id

            logger.warning(
                "Found existing UserHotel with same external_pms_id for different user/hotel, closing it",
                existing_user_hotel_id=str(existing_with_pms_id.id),
                existing_user_id=str(existing_with_pms_id.user_id),
                existing_hotel_id=str(existing_with_pms_id.hotel_id),
                new_user_id=str(user.id),
                new_hotel_id=str(hotel.id),
                external_pms_id=reservation_ref_str,
            )

        return await self.user_hotel_repo.get_active_stay_for_update(user.id, hotel.id)

    async def _handle_in_house_status(
        self,
        existing: Optional[UserHotel],
        user: User,
        hotel: Hotel,
        reservation: Reservation,
        reservation_ref_str: str,
        room_number: Optional[str],
    ) -> Optional[UserHotel]:
        """Handle IN_HOUSE or INHOUSE reservation status - create or update stay."""
        if existing:
            first_name = reservation.data.get("FirstName", "").strip() or None
            last_name = reservation.data.get("LastName", "").strip() or None

            await self.user_hotel_repo.update_existing_stay_from_pms(
                existing,
                room_number,
                reservation_ref_str,
                first_name,
                last_name,
            )
            logger.info(
                "Updated UserHotel with PMS data",
                user_id=str(user.id),
                hotel_id=str(hotel.id),
                room_number=room_number,
                external_pms_id=reservation_ref_str,
                status=reservation.status.value,
                first_name=first_name,
                last_name=last_name,
            )
            return existing

        return await self._create_new_user_hotel(user, hotel, reservation, reservation_ref_str, room_number)

    async def sync_user_on_registration(self, user_id: str, phone_number: str, selected_hotel: Hotel) -> Optional[dict]:
        """Sync user data during registration if reservation found."""
        user = await self.user_repo.get_by_telegram_id(user_id)
        if not user:
            logger.warning("User not found in database", user_id=user_id)
            return None

        reservation = await self.find_reservation_by_phone_any(phone_number, selected_hotel)
        if not reservation:
            logger.warning("Reservation not found", user_id=user_id, phone_number=phone_number)
            return None

        hotel = await self.catalog_repo.find_hotel_by_name(reservation.hotel)
        if not hotel:
            logger.warning(
                "Hotel from PMS not found in database",
                user_id=user_id,
                pms_hotel_name=reservation.hotel,
                reservation_id=str(reservation.id),
            )
            return {
                "reservation_found": True,
                "hotel_not_found": True,
                "pms_hotel_name": reservation.hotel,
            }

        if reservation.status != ReservationStatus.IN_HOUSE:
            logger.warning(
                "Reservation found but status is not Заезд",
                user_id=user_id,
                reservation_id=str(reservation.id),
                reservation_status=reservation.status.value,
            )
            pms_status = reservation.pms_incoming_status or reservation.status.value
            return {
                "reservation_found": True,
                "invalid_status": True,
                "reservation_status": pms_status,
            }

        user_hotel = await self.enrich_user_from_reservation(user, hotel, reservation)

        return {
            "reservation_found": True,
            "hotel": hotel,
            "room_number": self.extract_room_number(reservation),
            "arrival_date": reservation.arrival_date,
            "departure_date": reservation.departure_date,
            "user_hotel": user_hotel,
        }

    async def _create_new_user_hotel(
        self, user: User, hotel: Hotel, reservation: Reservation, reservation_ref_str: str, room_number: Optional[str]
    ) -> UserHotel:
        """Create new UserHotel from reservation data with concurrent creation handling."""
        role = await self.roles_repo.get_by_name(RoleEnum.GUEST.value)

        first_name = reservation.data.get("FirstName", "").strip() or None
        last_name = reservation.data.get("LastName", "").strip() or None

        try:
            user_hotel = await self.user_hotel_repo.create_user_hotel_from_reservation(
                user.id,
                hotel.id,
                role.id,
                room_number,
                reservation.arrival_date.date(),
                reservation_ref_str,
                first_name,
                last_name,
            )
            logger.info(
                "Created UserHotel from PMS reservation",
                user_id=str(user.id),
                hotel_id=str(hotel.id),
                room_number=room_number,
                external_pms_id=reservation_ref_str,
                status=reservation.status.value,
                first_name=first_name,
                last_name=last_name,
            )
            return user_hotel
        except Exception as e:
            logger.warning(
                "Failed to create UserHotel, possibly created by concurrent request",
                user_id=str(user.id),
                hotel_id=str(hotel.id),
                external_pms_id=reservation_ref_str,
                error=str(e),
            )
            existing = await self.user_hotel_repo.get_active_stay(user.id, hotel.id)
            if not existing:
                raise
            await self.user_hotel_repo.update_user_hotel_from_pms(
                existing,
                room_number,
                reservation_ref_str,
                first_name,
                last_name,
            )
            return existing

    async def find_reservation_by_phone_any(
        self, phone_number: str, hotel_hint: Optional[Hotel] = None
    ) -> Optional[Reservation]:
        """Find any reservation (active or future) by phone number. Used for syncing existing users."""
        reservations = await self._search_reservations_by_phone(phone_number)
        if not reservations:
            return None

        if hotel_hint:
            matching = self._filter_reservations_by_hotel(reservations, hotel_hint, use_fuzzy=True)
            if matching:
                return max(matching, key=lambda r: r.arrival_date)

            logger.info(
                "Reservation hotel doesn't match hint",
                hotel_hint_name=hotel_hint.name,
                hotel_hint_short=hotel_hint.short_name,
                found_reservations=[r.hotel for r in reservations],
            )
            return None

        return max(reservations, key=lambda r: r.arrival_date)

    async def sync_all_users_after_pms_update(self) -> dict:
        """Sync all registered users after PMS data update. Updates room numbers and closes expired stays."""
        stats = {"users_checked": 0, "users_updated": 0, "stays_closed": 0}

        result = await self.session.execute(
            select(UserHotel, User).join(User, UserHotel.user_id == User.id).where(UserHotel.close.is_(None))
        )

        active_user_hotels = result.all()
        active_user_ids = {user_hotel.user_id for user_hotel, _ in active_user_hotels}

        for user_hotel, user in active_user_hotels:

            if not user.phone_number:
                continue

            hotel = await self.catalog_repo.get_hotel_by_id(str(user_hotel.hotel_id))
            if not hotel:
                continue

            reservation_close_date = datetime.now(timezone.utc).date()

            reservation = None

            if user_hotel.external_pms_id:
                try:
                    reservation_by_id = await self.reservations_repo.get_by_id(UUID(user_hotel.external_pms_id))
                except Exception:
                    reservation_by_id = None

                if reservation_by_id:
                    reservation = reservation_by_id
                    if reservation.status != ReservationStatus.IN_HOUSE:
                        await self.user_hotel_repo.close_stay_for_conflict(
                            user_hotel, reservation_close_date
                        )
                        stats["stays_closed"] += 1
                        logger.info(
                            "Closed user stay due to non-IN_HOUSE status by external_pms_id",
                            user_id=str(user.id),
                            hotel_id=str(user_hotel.hotel_id),
                            phone_number=user.phone_number,
                            reservation_status=reservation.status.value,
                            reservation_id=str(reservation.id),
                        )
                        continue

            if reservation is None:
                reservation = await self.find_reservation_by_phone_any(
                    user.phone_number, hotel_hint=hotel
                )

            if not reservation:
                await self.user_hotel_repo.close_stay_for_conflict(user_hotel, reservation_close_date)
                stats["stays_closed"] += 1
                logger.info(
                    "Closed user stay because reservation not found",
                    user_id=str(user.id),
                    hotel_id=str(user_hotel.hotel_id),
                    phone_number=user.phone_number,
                )
                continue
            if reservation.status != ReservationStatus.IN_HOUSE:
                await self.user_hotel_repo.close_stay_for_conflict(user_hotel, reservation_close_date)
                stats["stays_closed"] += 1
                logger.info(
                    "Closed user stay due to non-IN_HOUSE status",
                    user_id=str(user.id),
                    hotel_id=str(user_hotel.hotel_id),
                    phone_number=user.phone_number,
                    reservation_status=reservation.status.value,
                    reservation_id=str(reservation.id),
                )
                continue

            reservation_hotel = await self.catalog_repo.find_hotel_by_name(reservation.hotel)
            if not reservation_hotel:
                await self.user_hotel_repo.close_stay_for_conflict(user_hotel, reservation_close_date)
                stats["stays_closed"] += 1
                logger.warning(
                    "Closed user stay because PMS hotel not found in catalog",
                    user_id=str(user.id),
                    hotel_id=str(user_hotel.hotel_id),
                    phone_number=user.phone_number,
                    reservation_id=str(reservation.id),
                    pms_hotel_name=reservation.hotel,
                )
                continue

            room_number = self.extract_room_number(reservation)
            old_room = self._normalize_room_number(user_hotel.room_number)
            new_room = self._normalize_room_number(room_number)

            reservation_ref_str = str(reservation.id)
            if user_hotel.external_pms_id != reservation_ref_str:
                existing_with_pms_id = await self.user_hotel_repo.get_by_external_pms_id(reservation_ref_str)
                if existing_with_pms_id and existing_with_pms_id.id != user_hotel.id:
                    logger.warning(
                        "Found existing UserHotel with same external_pms_id during sync, closing it",
                        existing_user_hotel_id=str(existing_with_pms_id.id),
                        current_user_hotel_id=str(user_hotel.id),
                        external_pms_id=reservation_ref_str,
                    )

                old_external_pms_id = user_hotel.external_pms_id
                await self.user_hotel_repo.update_external_pms_id(user_hotel, reservation_ref_str)
                user_hotel.external_pms_id = reservation_ref_str
                stats["users_updated"] += 1
                logger.info(
                    "Updated external_pms_id from PMS",
                    user_id=str(user.id),
                    phone_number=user.phone_number,
                    old_external_pms_id=old_external_pms_id,
                    new_external_pms_id=reservation_ref_str,
                    reservation_id=str(reservation.id),
                )

            if old_room != new_room:
                await self.user_hotel_repo.update_room_number(user_hotel, new_room)
                user_hotel.room_number = new_room
                stats["users_updated"] += 1
                logger.info(
                    "Updated room number from PMS",
                    user_id=str(user.id),
                    phone_number=user.phone_number,
                    old_room=old_room,
                    new_room=new_room,
                    reservation_id=str(reservation.id),
                )

            now = datetime.now(timezone.utc)
            if reservation.departure_date < now and user_hotel.close is None:
                await self.user_hotel_repo.close_stay_for_conflict(user_hotel, reservation.departure_date.date())
                stats["stays_closed"] += 1
                logger.info(
                    "Closed user stay after PMS sync",
                    user_id=str(user.id),
                    hotel_id=str(user_hotel.hotel_id),
                    departure_date=reservation.departure_date.date(),
                )

        all_users_result = await self.session.execute(
            select(User).where(User.phone_number.isnot(None), User.phone_number != "")
        )

        for user in all_users_result.scalars().all():
            if user.id in active_user_ids or not user.phone_number:
                continue

            stats["users_checked"] += 1

            reservation = await self.find_reservation_by_phone_any(phone_number=user.phone_number)
            if not reservation:
                continue

            hotel = await self.catalog_repo.find_hotel_by_name(reservation.hotel)
            if not hotel:
                logger.warning(
                    "Hotel from PMS not found in catalog during sync",
                    user_id=str(user.id),
                    pms_hotel_name=reservation.hotel,
                    reservation_id=str(reservation.id),
                )
                continue

            try:
                await self.enrich_user_from_reservation(user, hotel, reservation)
                stats["users_updated"] += 1
                logger.info(
                    "Created/updated UserHotel for user without active stay",
                    user_id=str(user.id),
                    phone_number=user.phone_number,
                    hotel_id=str(hotel.id),
                    reservation_id=str(reservation.id),
                )
            except Exception as e:
                logger.error(
                    "Failed to enrich user from reservation",
                    user_id=str(user.id),
                    phone_number=user.phone_number,
                    reservation_id=str(reservation.id),
                    error=str(e),
                    exc_info=True,
                )
                continue

        return stats
