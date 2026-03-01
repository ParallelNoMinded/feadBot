"""
Database middleware for handling connection errors and retries.
"""

import asyncio
import logging
from typing import Callable

from fastapi import HTTPException, Request
from sqlalchemy.exc import DisconnectionError, OperationalError, TimeoutError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.db import get_pool_status as _get_pool_status

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseHTTPMiddleware):
    """
    Middleware to handle database connection errors gracefully.
    Provides automatic retry for transient database errors.
    """

    def __init__(self, app, max_retries: int = 3, retry_delay: float = 0.1):
        super().__init__(app)
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Handle database errors with retry logic.
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await call_next(request)
                return response

            except (OperationalError, DisconnectionError, TimeoutError) as e:
                last_exception = e

                if attempt < self.max_retries:
                    logger.warning(f"Database error on attempt {attempt + 1}/{self.max_retries + 1}: {e}. Retrying...")
                    await asyncio.sleep(self.retry_delay * (2**attempt))  # Exponential backoff
                else:
                    logger.error(f"Database error after {self.max_retries + 1} attempts: {e}")
                    raise HTTPException(
                        status_code=503, detail="Database temporarily unavailable. Please try again later."
                    )
            except Exception as e:
                # Re-raise non-database errors immediately
                raise e

        # This should never be reached, but just in case
        raise last_exception


def get_pool_status() -> dict:
    """
    Get current connection pool status.
    """
    try:
        return _get_pool_status()
    except Exception as e:
        logger.error(f"Failed to get pool status: {e}")
        return {"error": str(e)}
