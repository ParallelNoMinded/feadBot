from sqlalchemy import join, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AdminAccount
from shared_models import Role, User, UserHotel
from app.repositories.user import UserRepository
from app.repositories.roles import RolesRepository
from shared_models.constants import ChannelType


class AdminRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self.roles_repo = RolesRepository(session)

    async def get_by_telegram_id(self, telegram_user_id: str) -> AdminAccount | None:
        """Check if user has admin role"""
        j = join(UserHotel, User, UserHotel.user_id == User.id).join(
            Role, UserHotel.role_id == Role.id
        )
        res = await self.session.execute(
            select(Role.name)
            .select_from(j)
            .where(
                User.external_user_id == telegram_user_id,
                UserHotel.close.is_(None),
                Role.name == "Администратор",
            )
        )
        result = res.first()
        if not result:
            return None

        role_name = result[0]
        return AdminAccount(telegram_user_id=telegram_user_id, role=role_name)

    async def upsert(
        self,
        telegram_user_id: str,
        channel_type: ChannelType,
        role: str = "Администратор",
    ) -> User:
        """Create or update admin user"""
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

        return user
