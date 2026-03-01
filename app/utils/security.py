from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.repositories.user import UserRepository
from app.repositories.user_hotel import UserHotelRepository


def require_api_key(x_api_key: str) -> bool:
    if settings.TELEGRAM_WEBHOOK_SECRET != x_api_key:
        return False
    return True


async def require_role(session: AsyncSession, user_id: str) -> bool:
    """
    Check if user has an active stay in any hotel.
    User is considered registered only if they have an active UserHotel
    record with close = NULL.
    """
    user = await UserRepository(session).get_by_telegram_id(user_id)
    if not user:
        return False

    # Check if user has any active stay (UserHotel with close = NULL)
    return await UserHotelRepository(session).has_active_stay(user.id)
