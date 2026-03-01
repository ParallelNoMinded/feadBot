"""
Database configuration for different environments.
Optimized for 200 RPS with proper connection pooling.
"""

from typing import Any, Dict

from app.config.settings import settings


def get_database_config() -> Dict[str, Any]:
    """
    Get database configuration based on environment.
    Optimized for high-load applications (200+ RPS).
    """
    base_config = {
        "echo": settings.DB_ECHO,  # Use setting from environment
        "pool_pre_ping": True,
        "pool_recycle": 3600,  # 1 hour
        "pool_reset_on_return": "commit",
        "connect_args": {
            "command_timeout": 60,
            "server_settings": {
                "application_name": "alean_assistant",
                "jit": "off",
            },
        },
    }

    if settings.APP_ENV == "production":
        # Production configuration for high load
        return {
            **base_config,
            "pool_size": 30,  # Base pool size
            "max_overflow": 50,  # Additional connections (up to 80 total)
            "pool_timeout": 30,  # 30 seconds timeout
            "connect_args": {
                **base_config["connect_args"],
                "command_timeout": 30,  # Shorter timeout for production
                "server_settings": {
                    **base_config["connect_args"]["server_settings"],
                    "statement_timeout": "30s",
                    "idle_in_transaction_session_timeout": "60s",
                },
            },
        }
    elif settings.APP_ENV == "staging":
        # Staging configuration
        return {
            **base_config,
            "pool_size": 15,
            "max_overflow": 25,
            "pool_timeout": 45,
        }
    else:
        # Development configuration
        return {
            **base_config,
            "pool_size": 5,
            "max_overflow": 10,
            "pool_timeout": 60,
        }
