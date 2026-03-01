from urllib import parse as _urlparse

import structlog

from app.adapters.channel import IncomingMessage

logger = structlog.get_logger(__name__)


def process_sp_early(sp_early: str, msg: IncomingMessage) -> dict:
    """Process start parameter early payload"""
    sp_decoded = _urlparse.unquote_plus(sp_early)
    logger.info(f"start.early_payload.decoded {sp_decoded}")

    # Ensure payload exists
    if msg.payload is None:
        msg.payload = {}

    # Preserve existing context if it exists
    existing_context = msg.payload.get("_context", {})

    if "hotel=" in sp_decoded and "zone=" in sp_decoded:
        parts = sp_decoded.split("=")

        if len(parts) == 4:
            context = msg.payload.setdefault("_context", {})
            # Merge with existing context to preserve any pre-set values
            context.update(existing_context)
            context["hotel"] = parts[1]
            context["zone"] = parts[3]
        elif len(parts) == 2 and parts[0] == "hotel":
            context = msg.payload.setdefault("_context", {})
            # Merge with existing context to preserve any pre-set values
            context.update(existing_context)
            context["hotel"] = parts[1]

    # If context was already set (e.g., from MaxAdapter._parse_bot_started),
    # preserve it even if process_sp_early didn't parse it
    if existing_context and not msg.payload.get("_context"):
        msg.payload["_context"] = existing_context

    logger.info(
        f"start.early_payload.final_context {msg.payload.get('_context', {})}"
    )
    return msg.payload
