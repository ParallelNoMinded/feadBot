import logging
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import settings
from app.core.db_config import get_database_config

logger = logging.getLogger(__name__)

db_config = get_database_config()

engine: AsyncEngine = create_async_engine(settings.DB_URL, **db_config)


# Session factory with proper configuration
AsyncSessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,  # Auto-flush changes
    autocommit=False,  # Explicit transaction control
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency to get database session.
    Properly handles connection lifecycle and error recovery.
    """
    session = None
    try:
        async with AsyncSessionFactory() as session:
            yield session
    except Exception as e:
        logger.error(f"Database session error: {e}")
        if session:
            await session.rollback()
        raise
    finally:
        if session:
            await session.close()


async def close_database_connections():
    """
    Gracefully close all database connections.
    Should be called on application shutdown.
    """
    try:
        await engine.dispose()
        logger.info("Database connections closed gracefully")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")


# Connection pool monitoring
def get_pool_status() -> dict:
    """
    Get current connection pool status for monitoring.
    """
    try:
        pool = engine.pool
        status = {
            "pool_size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }

        # Add invalid count if available (not all pool types support this)
        if hasattr(pool, "invalid"):
            status["invalid"] = pool.invalid()
        else:
            status["invalid"] = "N/A"

        return status
    except Exception as e:
        logger.error(f"Failed to get pool status: {e}")
        return {"error": str(e)}
