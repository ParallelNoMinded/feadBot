from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent.parent / ".env", env_file_encoding="utf-8", case_sensitive=False
    )

    # App
    APP_ENV: str = Field(default="local")
    APP_NAME: str = Field(default="Alean Assistant")
    APP_HOST: str = Field(default="0.0.0.0")
    APP_PORT: int = Field(default=8000)

    LOG_LEVEL: str = Field(default="INFO")

    # DB (use Postgres by default)
    DB_URL: str = Field(default="postgresql+asyncpg://alean_user:alean_password@localhost:5433/alean_db")

    # Channels / Integrations
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_WEBHOOK_SECRET: str
    TELEGRAM_WEBHOOK_URL: str
    TELEGRAM_POLLING: bool = False
    TELEGRAM_BOT_USERNAME: str
    TELEGRAM_MESSAGE_MAX_LENGTH: int

    # MAX Messenger
    MAX_BOT_TOKEN: str = ""  # Access token for authorization
    MAX_WEBHOOK_SECRET: str = ""
    MAX_API_URL: str = Field(default="https://platform-api.max.ru/messages")
    MAX_BOT_ID: str = Field(default="")

    # PMS Integration
    PMS_RESERVATIONS_TOKEN: str

    # LLM
    LLM_API_URL: str | None = None
    LLM_API_KEY: str | None = None
    LLM_API_KEY_2: str | None = None  # Second API key for parallel processing

    LLM_MODEL_NAME: str | None = None
    LLM_TEMPERATURE: float | None = 0.01
    LLM_MAX_TOKENS: int | None = 1024

    # S3
    S3_ENDPOINT: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_BUCKET: str | None = None

    # Observability
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None
    LANGFUSE_HOST: str | None = None

    MODEL_PRICE: dict[str, dict[str, float]] = {
        "Qwen/Qwen3-235B-A22B-Instruct-2507": {
            "input": 0.035,
            "output": 0.07,
        },
    }

    # Feedback
    MAX_FEEDBACK_MESSAGES: int = Field(default=8)
    MAX_FEEDBACK_MESSAGES_PER_DAY: int = Field(default=50)

    # Database
    DB_ECHO: bool = Field(default=False)  # Enable SQL logging

    FEEDBACK_SESSION_WAITING_TIME: float = Field(default=1800.0)
    FEEDBACK_SESSION_WAITING_TIME_WITH_COMMENT: float = Field(default=300.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
