from enum import Enum


class ReservationStatus(str, Enum):
    """Reservation status from PMS system"""

    CHECKED_OUT = "CHECKED_OUT"  # Выезд
    IN_HOUSE = "IN_HOUSE"  # Заезд
    INHOUSE = "INHOUSE"  # Переселение
    CANCELLED = "CANCELLED"  # Отказ


class ChannelType(str, Enum):
    """Channel types"""

    TELEGRAM = "TELEGRAM"
    MAX = "MAX"


class FeedbackStatus(str, Enum):
    """Feedback statuses"""

    CREATED = "created"
    OPENED = "opened"
    IN_PROGRESS = "in_progress"
    SOLVED = "solved"
    REJECTED = "rejected"


class MediaType(str, Enum):
    """Types of media files"""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class Sentiment(str, Enum):
    """Types of sentiments in analysis"""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class RoleEnum(str, Enum):
    """User roles in the system"""

    GUEST = "Гость"
    MANAGER = "Менеджер"
    NETWORK_MANAGER = "Руководитель сети"
    ADMIN = "Администратор"


class AnalysisStatus(str, Enum):
    """Analysis statuses"""

    RELEVANT = "relevant"
    SENTIMENT = "sentiment"
    ANALYSIS = "analysis"
    COMPLETED = "completed"

    def __str__(self) -> str:
        return self.value
