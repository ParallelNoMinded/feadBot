from typing import List, Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from shared_models import Role
from shared_models import RoleEnum


class RolesRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_name(self, name: str) -> Optional[Role]:
        res = await self.session.execute(select(Role).where(Role.name == name))
        return res.scalars().first()

    async def get_manager_and_admin(self) -> List[Role]:
        res = await self.session.execute(
            select(Role).where(
                or_(
                    Role.name == RoleEnum.MANAGER.value,
                    Role.name == RoleEnum.ADMIN.value,
                )
            )
        )
        return res.scalars().all()

    async def get_all(self) -> List[Role]:
        res = await self.session.execute(select(Role))
        return res.scalars().all()
