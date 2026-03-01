from typing import List

from sqlalchemy import join
from sqlalchemy import select
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manager import ManagerAccount
from shared_models import Hotel, Role, User, UserHotel
from app.repositories.user import UserRepository
from app.repositories.roles import RolesRepository
from shared_models import RoleEnum
from shared_models.constants import ChannelType


class ManagerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self.roles_repo = RolesRepository(session)

    async def get_by_telegram_id(self, telegram_user_id: str, hotel_code: str) -> ManagerAccount | None:
        j = (
            join(UserHotel, User, UserHotel.user_id == User.id)
            .join(Role, UserHotel.role_id == Role.id)
            .join(Hotel, UserHotel.hotel_id == Hotel.id)
        )
        res = await self.session.execute(
            select(Role.name, Hotel.short_name)
            .select_from(j)
            .where(
                User.external_user_id == telegram_user_id,
                UserHotel.close.is_(None),
                Role.name != RoleEnum.GUEST.value,
                Hotel.short_name == hotel_code.upper(),
            )
        )
        result = res.first()
        if not result:
            return None

        role_name, hotel_short_name = result
        return ManagerAccount(
            telegram_user_id=telegram_user_id,
            role=role_name,
            hotel_code=hotel_short_name,
        )

    async def upsert(
        self,
        telegram_user_id: str,
        channel_type: ChannelType,
        role: str = RoleEnum.MANAGER.value,
    ) -> User:
        user = await self.user_repo.get_by_telegram_id(telegram_user_id)
        if not user:
            user = User(external_user_id=telegram_user_id, channel_type=channel_type)
            self.session.add(user)
            await self.session.flush()
        # ensure role exists
        ro = await self.roles_repo.get_by_name(role)
        if not ro:
            ro = Role(name=role)
            self.session.add(ro)
            await self.session.flush()
        # assignment will be added separately with specific hotel
        return user

    async def list_hotels(self, telegram_user_id: str) -> List[str]:
        # Return hotel codes for which user has manager role assignment

        res = await self.session.execute(
            _select(UserHotel, Hotel, Role)
            .join(Hotel, Hotel.id == UserHotel.hotel_id)
            .join(Role, Role.id == UserHotel.role_id)
            .join(User, User.id == UserHotel.user_id)
            .where(
                User.external_user_id == telegram_user_id,
                Role.name == RoleEnum.MANAGER.value,
            )
        )
        return [h.short_name for _, h, _ in res.all()]
