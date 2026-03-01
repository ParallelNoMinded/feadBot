from typing import Optional
from uuid import UUID
from datetime import date, datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared_models import UserHotel


class UserHotelRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def has_active_stay(self, user_id: UUID) -> bool:
        result = await self.session.execute(
            select(UserHotel).where(
                UserHotel.user_id == user_id, UserHotel.close.is_(None)
            )
        )
        return result.scalars().first() is not None

    async def get_active_stay(
        self, user_id: UUID, hotel_id: UUID
    ) -> Optional[UserHotel]:
        result = await self.session.execute(
            select(UserHotel).where(
                UserHotel.user_id == user_id,
                UserHotel.hotel_id == hotel_id,
                UserHotel.close.is_(None),
            )
        )
        return result.scalars().first()

    async def get_by_external_pms_id(self, external_pms_id: str) -> Optional[UserHotel]:
        if not external_pms_id:
            return None
        result = await self.session.execute(
            select(UserHotel).where(UserHotel.external_pms_id == external_pms_id)
        )
        return result.scalars().first()

    async def get_by_external_pms_id_for_update(
        self, external_pms_id: str
    ) -> Optional[UserHotel]:
        """
        Get UserHotel by external_pms_id with row-level lock (SELECT FOR UPDATE).

        Args:
            external_pms_id: External PMS reservation ID

        Returns:
            UserHotel record or None if not found
        """
        if not external_pms_id:
            return None
        result = await self.session.execute(
            select(UserHotel)
            .where(UserHotel.external_pms_id == external_pms_id)
            .with_for_update()
        )
        return result.scalars().first()

    async def get_active_stay_for_update(
        self, user_id: UUID, hotel_id: UUID
    ) -> Optional[UserHotel]:
        """
        Get active UserHotel stay with row-level lock (SELECT FOR UPDATE).

        Args:
            user_id: User UUID
            hotel_id: Hotel UUID

        Returns:
            Active UserHotel record or None if not found
        """
        result = await self.session.execute(
            select(UserHotel)
            .where(
                UserHotel.user_id == user_id,
                UserHotel.hotel_id == hotel_id,
                UserHotel.close.is_(None),
            )
            .with_for_update()
        )
        return result.scalars().first()

    async def close_stay_for_conflict(
        self, user_hotel: UserHotel, departure_date: date
    ) -> UserHotel:
        """Close a UserHotel stay by setting its close date."""
        if user_hotel.close is None:
            user_hotel.close = departure_date
            await self.session.commit()
        return user_hotel

    async def update_existing_stay_from_pms(
        self,
        user_hotel: UserHotel,
        room_number: Optional[str],
        external_pms_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> UserHotel:
        """Update existing UserHotel with PMS data."""
        if room_number:
            user_hotel.room_number = room_number
        user_hotel.external_pms_id = external_pms_id
        if first_name is not None:
            user_hotel.first_name = first_name
        if last_name is not None:
            user_hotel.last_name = last_name
        if user_hotel.close:
            user_hotel.close = None
        await self.session.commit()
        return user_hotel

    async def create_user_hotel_from_reservation(
        self,
        user_id: UUID,
        hotel_id: UUID,
        role_id: UUID,
        room_number: Optional[str],
        open_date: date,
        external_pms_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> UserHotel:
        """Create UserHotel from reservation data with commit."""
        user_hotel = UserHotel(
            user_id=user_id,
            hotel_id=hotel_id,
            role_id=role_id,
            room_number=room_number,
            open=open_date,
            external_pms_id=external_pms_id,
            first_name=first_name,
            last_name=last_name,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(user_hotel)

        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return user_hotel

    async def update_user_hotel_from_pms(
        self,
        user_hotel: UserHotel,
        room_number: Optional[str],
        external_pms_id: str,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> UserHotel:
        """Update UserHotel with PMS data with commit."""
        user_hotel.room_number = room_number
        user_hotel.external_pms_id = external_pms_id
        if first_name is not None:
            user_hotel.first_name = first_name
        if last_name is not None:
            user_hotel.last_name = last_name
        await self.session.commit()
        return user_hotel

    async def update_external_pms_id(
        self, user_hotel: UserHotel, external_pms_id: str
    ) -> None:
        """Update external_pms_id only."""
        user_hotel.external_pms_id = external_pms_id
        await self.session.commit()
        return None

    async def update_room_number(self, user_hotel: UserHotel, room_number: str) -> None:
        """Update room_number only."""
        user_hotel.room_number = room_number
        await self.session.commit()
        return None
