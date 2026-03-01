from typing import Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.catalog import CatalogRepository
from app.repositories.managers import ManagerRepository
from app.services.base import BaseService
from shared_models import UserHotel

logger = structlog.get_logger(__name__)


class UserValidationService(BaseService):
    """Service for user validation operations"""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.catalog_repo = CatalogRepository(session)

    async def is_registered(self, user_id: str, hotel_code: str) -> bool:
        """Check if user is registered for specific hotel"""
        try:
            user = await self.user_repo.get_by_telegram_id(user_id)
            if not user:
                return False

            # Check if user is a manager - managers are considered registered
            manager_repo = ManagerRepository(self.session)
            manager = await manager_repo.get_by_telegram_id(user_id, hotel_code)
            if manager:
                return True

            # Check open stay assignment for this hotel
            h = await self.catalog_repo.get_hotel_by_code(hotel_code.upper())
            if not h:
                return False

            stay = await self.user_hotel_repo.get_active_stay(user.id, h.id)

            return stay is not None
        except Exception as e:
            logger.error(f"Error checking registration: {e}")
            return False

    async def has_active_stay(self, user_id: str, hotel_code: str) -> bool:
        """Check if user has active stay in hotel"""
        try:
            # Get user by telegram_id
            user = await self.user_repo.get_by_telegram_id(user_id)

            if not user:
                return False

            # Get hotel by code
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code.upper())
            if not hotel:
                return False

            stay = await self.user_hotel_repo.get_active_stay(user.id, hotel.id)
            return stay is not None

        except Exception as e:
            logger.error(f"Error validating active stay: {e}")
            return False

    async def get_active_stay(self, user_id: str, hotel_code: str) -> Optional[UserHotel]:
        """Get active stay for user in hotel"""
        try:
            user = await self.user_repo.get_by_telegram_id(user_id)
            if not user:
                return None

            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code.upper())
            if not hotel:
                return None

            return await self.user_hotel_repo.get_active_stay(user.id, hotel.id)

        except Exception as e:
            logger.error(f"Error getting active stay: {e}")
            return None
