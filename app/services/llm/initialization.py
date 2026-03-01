"""
Service for initializing LLM services at application startup.
"""

import structlog

from app.services.llm.llm_pool import llm_pool

logger = structlog.get_logger(__name__)


async def initialize_llm_services():
    """Initialize all LLM services at application startup."""
    try:
        logger.info("Starting LLM services initialization...")

        # Initialize the global LLM pool (this will create the singleton)
        pool_status = llm_pool.get_pool_status()
        logger.info(f"LLM pool status: {pool_status}")

        logger.info("LLM services initialization completed successfully")

    except Exception as e:
        logger.error(f"Failed to initialize LLM services: {e}")
        return False

    return True
