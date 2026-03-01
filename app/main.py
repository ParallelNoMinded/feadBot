import asyncio
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

from app.api.routes import router as api_router
from app.config.settings import get_settings
from app.core.db import close_database_connections
from app.core.db_middleware import DatabaseMiddleware
from app.observability.logging import configure_logging
from app.services.analysis_recovery import AnalysisRecoveryService
from app.services.llm.initialization import initialize_llm_services
from app.services.report_scheduler import run_weekly_reports_scheduler
from app.workers.feedback_session_gc import run_feedback_session_gc

logger = structlog.get_logger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)

    logger.info("app.starting", env=settings.APP_ENV)

    # Initialize LLM services
    llm_init_success = await initialize_llm_services()
    if not llm_init_success:
        logger.error("Failed to initialize LLM services")

    # Recover incomplete analyses
    recovery_service = AnalysisRecoveryService()
    asyncio.create_task(recovery_service.recover_incomplete_analyses())

    # Start polling in local/dev if enabled
    if settings.TELEGRAM_POLLING:
        # Start simple weekly scheduler in background
        asyncio.create_task(run_weekly_reports_scheduler())
    asyncio.create_task(run_feedback_session_gc())

    yield

    # Close database connections
    await close_database_connections()
    logger.info("app.stopping")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.APP_NAME,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    # Add database middleware for error handling and retries
    app.add_middleware(DatabaseMiddleware, max_retries=3, retry_delay=0.1)

    return app


app = create_app()

app.include_router(api_router)
