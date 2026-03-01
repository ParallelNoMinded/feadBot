import asyncio
from datetime import datetime, timezone
from typing import Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.telegram.adapter import TelegramAdapter
from app.core.db import AsyncSessionFactory
from app.models.manager import ManagerAccount
from app.repositories.managers import ManagerRepository
from app.services.reporting import ReportingService

logger = structlog.get_logger(__name__)


async def _send_reports_for_manager(
    session: AsyncSession, telegram_user_id: str, hotels: Sequence[str]
) -> None:
    rs = ReportingService(session)
    xlsx_bytes = await rs.export_xlsx(hotels_scope=hotels)
    await TelegramAdapter().send_document_bytes(
        telegram_user_id,
        filename="weekly_report.xlsx",
        data=xlsx_bytes,
        caption="Недельный отчет",
    )


async def run_weekly_reports_scheduler(poll_interval_seconds: int = 300) -> None:
    # naive ticker: checks every poll_interval_seconds and sends weekly on Mondays at 09:00 UTC
    logger.info("scheduler.reports.start")
    while True:
        try:
            now = datetime.now(timezone.utc)
            if (
                now.weekday() == 0
                and now.hour == 9
                and now.minute < (poll_interval_seconds // 60 + 1)
            ):
                async with AsyncSessionFactory() as session:
                    # Fetch all manager accounts
                    result = await session.execute(select(ManagerAccount))
                    managers = list(result.scalars().all())
                    mhr = ManagerRepository(session)
                    for m in managers:
                        hotels = await mhr.list_hotels(str(getattr(m, "id", "")))
                        if hotels:
                            await _send_reports_for_manager(
                                session, m.telegram_user_id, hotels
                            )
        except Exception as e:
            logger.error("scheduler.reports.error", error=str(e))
        await asyncio.sleep(poll_interval_seconds)
