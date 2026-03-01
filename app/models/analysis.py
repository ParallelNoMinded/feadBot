from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field

from shared_models import Sentiment


class SentimentAnalysisModel(BaseModel):
    """Model for sentiment analysis"""

    sentiment: Sentiment


class RelevantAnalysisModel(BaseModel):
    """Model for relevant analysis"""

    relevant: bool


class ReviewAnalysisModel(BaseModel):
    """Model for review analysis"""

    tags: list[str] = Field(
        description="Список тегов, характеризующих проблемы постояльцев отеля", min_length=1, max_length=6
    )
    recommendation: str = Field(description="Строка рекомендации для устранения основных проблем")


class BaseFeedbackModel(BaseModel):
    """Base model with common fields for feedback models.
    These fields are non-nullable in the database.
    """

    zone: str = Field(description="Zone of the hotel (e.g., pool, entertainment, children area)")
    rating: int = Field(description="Rating from user feedback")
    name: str = Field(description="Name of the hotel")
    short_name: str = Field(description="Short name of the hotel")
    is_adult: bool = Field(description="Flag to check whether the child is an adult or not")
    created_at: datetime = Field(description="Datetime of creating user feedback")


class LastFeedbackModel(BaseFeedbackModel):
    """Model for receiving last feedback from users.
    Inherits the required fields from the base model.
    """

    comment: Optional[str] = Field(default=None, description="Comment from user")


class AllFeedbacksModel(BaseFeedbackModel):
    """Model for receiving all feedbacks from users.
    Inherits the required fields from the base model.
    """

    comments: List[str] = Field(default_factory=list, description="Comments from user")
    timezone: Optional[str] = Field(default=None, description="Timezone of the hotel")


class FeedbackInfoModel(BaseModel):
    """Model for feedback information including zone, hotel, guest phone and timezone.

    Contains feedback-related details retrieved from multiple joined tables.
    All fields except guest_phone are required as they come from INNER JOINs.
    """

    zone_name: str = Field(description="Zone name from the feedback")
    hotel_name: str = Field(description="Full name of the hotel")
    hotel_code: str = Field(description="Short name/code of the hotel")
    guest_phone: Optional[str] = Field(default=None, description="Guest phone number (may be None if not available)")
    timezone: str = Field(description="Hotel timezone string (e.g., 'Europe/Moscow')")
