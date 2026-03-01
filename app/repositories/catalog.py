from typing import Optional
from uuid import UUID

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from shared_models import Hotel, Zone


class CatalogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_hotels(self) -> list[Hotel]:
        res = await self.session.execute(select(Hotel))
        return list(res.scalars().all())

    async def get_hotel_by_code(self, code: str) -> Optional[Hotel]:
        res = await self.session.execute(select(Hotel).where(Hotel.short_name == code))
        return res.scalars().first()

    async def get_hotel_by_id(self, hotel_id: str) -> Optional[Hotel]:
        """Get hotel by UUID"""
        res = await self.session.execute(
            select(Hotel).where(Hotel.id == UUID(hotel_id))
        )
        return res.scalars().first()

    async def find_hotel_by_name(self, name: str) -> Optional[Hotel]:
        """Find hotel by PMS name using exact match (case-insensitive)."""
        if not name:
            return None

        result = await self.session.execute(select(Hotel).where(Hotel.name == name))
        hotel = result.scalars().first()
        if hotel:
            return hotel

        return await self.get_hotel_by_code(name)

    async def list_zones_for_hotel_code(self, hotel_code: str) -> list[tuple[str, str]]:
        """List zones for a hotel by code"""
        hotel = await self.get_hotel_by_code(hotel_code)
        if not hotel:
            return []
        res = await self.session.execute(select(Zone).where(Zone.hotel_id == hotel.id))
        zones = list(res.scalars().all())
        return [(z.short_name, z.name) for z in zones]

    async def get_zone_by_code(self, hotel_code: str, zone_code: str) -> Optional[Zone]:
        """Get zone by code"""
        hotel = await self.get_hotel_by_code(hotel_code)
        if not hotel:
            return None
        res = await self.session.execute(
            select(Zone).where(Zone.hotel_id == hotel.id, Zone.short_name == zone_code)
        )
        return res.scalars().first()

    async def get_zone_by_id(self, zone_id: str) -> Optional[Zone]:
        """Get zone by ID"""
        res = await self.session.execute(select(Zone).where(Zone.id == zone_id))
        return res.scalars().first()

    async def delete_zone(self, zone_id: str) -> bool:
        """Delete zone by ID"""
        result = await self.session.execute(delete(Zone).where(Zone.id == zone_id))
        if result.rowcount > 0:
            await self.session.commit()
            return True
        return False
