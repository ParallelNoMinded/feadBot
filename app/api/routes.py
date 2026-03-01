import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.max.adapter import MaxAdapter
from app.adapters.telegram.adapter import TelegramAdapter
from app.config.settings import settings
from app.core.db import get_session
from app.core.state import STATE
from app.services.max_webhook_handler import MaxWebhookHandler
from app.services.pms_handler import ReservationsHandler
from app.services.telegram_webhook_handler import TelegramWebhookHandler

logger = structlog.get_logger(__name__)


router = APIRouter()

# Initialize feedback message limit from config
STATE.set_max_feedback_messages(settings.MAX_FEEDBACK_MESSAGES)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/webhook/max")
async def max_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    adapter: MaxAdapter = Depends(MaxAdapter),
    x_max_bot_api_secret: str = Header(default=""),
) -> dict[str, str]:
    """
    MAX webhook endpoint.

    Handles incoming MAX webhook requests by delegating to
    MaxWebhookHandler. This provides clean separation of concerns
    and makes the code more testable.

    Authentication is done via x-max-bot-api-secret header
    as per MAX API specification.
    """
    try:
        payload = await request.body()
        headers = dict(request.headers)

        # Log raw webhook for debugging callbacks
        logger.info(
            "max.webhook.raw",
            payload_preview=payload.decode("utf-8")[:500],
            headers_keys=list(headers.keys()),
        )

        webhook_handler = MaxWebhookHandler(session)
        result = await webhook_handler.handle_webhook(
            payload=payload,
            headers=headers,
            secret_token=x_max_bot_api_secret,
            adapter=adapter,
            state=STATE,
        )

    except ValueError as e:
        logger.warning("max.webhook.validation.error", error=str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    except Exception as e:
        logger.error("max.webhook.unexpected.error", error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error",
        )

    return result


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    adapter: TelegramAdapter = Depends(TelegramAdapter),
    x_telegram_bot_api_secret_token: str = Header(default=""),
) -> dict[str, str]:
    """
    Telegram webhook endpoint.

    Handles incoming Telegram webhook requests by delegating to
    TelegramWebhookHandler. This provides clean separation of concerns
    and makes the code more testable.
    """
    try:
        payload = await request.body()
        headers = dict(request.headers)

        webhook_handler = TelegramWebhookHandler(session)
        result = await webhook_handler.handle_webhook(
            payload=payload,
            headers=headers,
            secret_token=x_telegram_bot_api_secret_token,
            adapter=adapter,
            state=STATE,
        )

    except ValueError as e:
        logger.warning("webhook.validation.error", error=str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    except Exception as e:
        logger.error("webhook.unexpected.error", error=str(e), exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

    return result


@router.post("/api/reservations")
async def pms_reservations(
    request: Request,
    token: str = Header(alias="authorization", default=""),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    PMS reservations endpoint.

    Accepts reservation data from PMS and syncs it to database.
    If any error occurs, no data is written (all or nothing).

    Requires Authorization header for authentication.
    """
    try:
        handler = ReservationsHandler(session=session, settings=settings)
        return await handler.handle_reservations_webhook(request=request, token=token)

    except Exception as e:
        logger.error(f"PMS reservations error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
