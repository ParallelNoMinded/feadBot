from datetime import datetime
from zoneinfo import ZoneInfo
import structlog

logger = structlog.get_logger(__name__)


def convert_to_timezone(dt: datetime, tz_name: str) -> datetime:
    """Convert UTC datetime to hotel timezone

    Args:
        dt: datetime object with timezone info (must not be None)
        tz_name: timezone name like 'Europe/Moscow' (must not be None or empty)

    Returns:
        datetime object in the specified timezone

    Raises:
        ValueError: if dt is None or tz_name is None/empty/invalid
    """
    if dt is None:
        raise ValueError("dt cannot be None")

    try:
        hotel_tz = ZoneInfo(tz_name)
        return dt.astimezone(hotel_tz)
    except Exception as e:
        raise ValueError(f"Invalid timezone '{tz_name}': {e}")
