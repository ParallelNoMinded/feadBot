# flake8: noqa
import uuid
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlmodel import Column, Field, Index, SQLModel

from shared_models.constants import (
    AnalysisStatus,
    FeedbackStatus,
    MediaType,
    Sentiment,
    ReservationStatus,
    ChannelType,
)


def generate_uuid(*field_values: str) -> uuid.UUID:
    """
    Generate deterministic UUID5.

    :param field_values: Variable number of field values to include in UUID generation
    :return: Deterministic UUID5
    """
    if not field_values:
        raise ValueError("At least one field value must be provided")

    combined_string = ""
    for i, field_value in enumerate(field_values, 1):
        combined_string += f"pos{i}:{str(field_value)}|"

    combined_string = combined_string.rstrip("|")
    return uuid.uuid5(uuid.NAMESPACE_DNS, combined_string)


class UUIDBase(SQLModel):
    """
    Base class for SQLModel models that use deterministic UUID generation.

    This class is designed for models that need deterministic UUID generation
    based on business logic fields using the generate_uuid function.
    """

    __abstract__ = True

    id: UUID = Field(primary_key=True)

    def __init__(self, **data):
        super().__init__(**data)
        if not self.id:
            self.id = self._generate_deterministic_uuid()

    def _generate_deterministic_uuid(self) -> UUID:
        """
        Generate deterministic UUID based on entity-specific fields.
        Subclasses should override this method to define their UUID generation logic.
        """
        raise NotImplementedError("Subclasses must implement _generate_deterministic_uuid method")


class Hotel(UUIDBase, table=True):
    """Hotel entity representing accommodation facilities.

    Stores information about hotels including their names, short identifiers,
    and timezone settings. Each hotel can have multiple zones and scenarios
    associated with it.

    Attributes:
        id: Unique identifier for the hotel (generated from name and description)
        name: Full name of the hotel
        short_name: Abbreviated name used for display and references
        timezone: Timezone string for the hotel location
    """

    __tablename__ = "hotels"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, nullable=False))
    short_name: str = Field(sa_column=Column(Text, nullable=False, index=True))
    description: str = Field(sa_column=Column(Text, nullable=False))
    timezone: str = Field(sa_column=Column(Text, nullable=False))

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("hotel", self.name, self.description)


class Zone(UUIDBase, table=True):
    """Zone entity representing a specific area within a hotel.

    Stores information about zones within a hotel, including their names,
    short identifiers, and whether they are adult-only. Each zone can be
    associated with a specific hotel and has a status indicating whether
    it is currently active or not.
    """

    __tablename__ = "zones"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    hotel_id: UUID = Field(foreign_key="hotels.id", nullable=False, index=True)
    name: str = Field(sa_column=Column(Text, nullable=False))
    short_name: str = Field(sa_column=Column(Text, nullable=False, index=True))
    is_adult: bool = Field(sa_column=Column(Boolean, nullable=False))
    disabled_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=True,
            index=True,
        ),
    )
    description: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("zone", str(self.hotel_id), self.name, self.short_name)


class Role(UUIDBase, table=True):
    """Role entity defining user permissions and access levels.

    Represents different roles within the system (e.g., admin, manager, staff)
    with associated permissions stored as JSON. Each role has a unique name
    and defines what actions users with this role can perform.
    """

    __tablename__ = "roles"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, unique=True, nullable=False, index=True))

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("role", self.name)


class User(UUIDBase, table=True):
    """User entity representing system users and hotel staff.

    Stores information about users who can access the feedback system,
    including hotel staff, managers, and administrators. Each user has
    an external ID for integration with external systems.

    Attributes:
        id: Unique identifier for the user (generated from external_user_id)
        external_user_id: Unique identifier from external system (PMS, etc.)
        phone_number: Contact phone number (optional)
        created_at: Timestamp when the user was created
    """

    __tablename__ = "users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    external_user_id: str = Field(sa_column=Column(Text, unique=True, nullable=False, index=True))
    channel_type: ChannelType = Field(
        sa_column=Column(SAEnum(ChannelType, name="channeltype"), nullable=True, index=True)
    )
    phone_number: str = Field(sa_column=Column(Text, nullable=False, index=True))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        )
    )

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("user", self.external_user_id)


class UserHotel(UUIDBase, table=True):
    """User-Hotel relationship entity representing user stays and assignments.

    Links users to hotels for specific periods, representing either guest stays
    or staff assignments. Tracks the duration of the relationship and includes
    room information for guests or role assignments for staff.

    Attributes:
        id: Unique identifier for the user-hotel relationship (generated from user_id, hotel_id, open date)
        user_id: Foreign key reference to the user
        hotel_id: Foreign key reference to the hotel
        role_id: Foreign key reference to the user's role at this hotel
        room_number: Room number for guest stays (NULL for staff)
        open: Start date of the relationship/stay
        close: End date of the relationship/stay (NULL for ongoing)
        external_pms_id: Unique identifier from external PMS system
    """

    __tablename__ = "user_hotel"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", nullable=False, index=True)
    hotel_id: UUID = Field(foreign_key="hotels.id", nullable=False, index=True)
    role_id: UUID = Field(foreign_key="roles.id", nullable=False, index=True)
    room_number: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    open: date = Field(sa_column=Column(Date, nullable=False))
    close: Optional[date] = Field(default=None, sa_column=Column(Date, nullable=True))
    external_pms_id: Optional[UUID] = Field(foreign_key="reservations.id", nullable=True, index=True)
    last_name: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    first_name: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("user_hotel", str(self.user_id), str(self.hotel_id), str(self.open))


class Comment(UUIDBase, table=True):
    """
    Comment entity storing textual feedback and responses.

    Represents individual comments that can be associated with feedback entries.
    Comments can be from guests providing feedback or from staff responding
    to feedback. Each comment is timestamped for chronological tracking.

    Attributes:
        id: Unique identifier for the comment (generated from comment text and created_at)
        comment: The actual comment text content
        created_at: Timestamp when the comment was created
    """

    __tablename__ = "comments"
    __table_args__ = (Index("idx_comments_created_at", "id", "created_at"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    comment: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            index=True,
        )
    )

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("comment", self.comment, str(self.created_at))


class Attachment(UUIDBase, table=True):
    """
    Attachment entity storing media files associated with feedback.

    Represents media files (images, videos, documents) that can be attached
    to feedback entries. Files are stored in S3 and referenced by URL.
    Each attachment has a specific media type for proper handling and display.

    Attributes:
        id: Unique identifier for the attachment
        media_type: Type of media (image, video, audio, document)
        s3_url: URL pointing to the file in S3 storage
        created_at: Timestamp when the attachment was uploaded
    """

    __tablename__ = "attachments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    media_type: MediaType = Field(sa_column=Column(SAEnum(MediaType), nullable=False))
    s3_url: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime, nullable=False, default=datetime.now(timezone.utc)),
    )

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("attachment", self.media_type, self.s3_url, str(self.created_at))


class Feedback(UUIDBase, table=True):
    """
    Feedback entity representing guest feedback and ratings.

    Stores feedback ratings and comments from guests, with associations
    to the user stay, zone, and comments/attachments. Maintains a status
    field to track the lifecycle of the feedback.
    """

    __tablename__ = "feedbacks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_stay_id: UUID = Field(foreign_key="user_hotel.id", nullable=False, index=True)
    zone_id: UUID = Field(foreign_key="zones.id", nullable=False, index=True)
    rating: int = Field(sa_column=Column(SmallInteger, nullable=False))
    status: FeedbackStatus = Field(sa_column=Column(SAEnum(FeedbackStatus), nullable=False, index=True))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            index=True,
        )
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )
    )

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("feedback", str(self.user_stay_id), str(self.zone_id), str(self.created_at))


class FeedbackComment(SQLModel, table=True):
    """
    FeedbackComment entity storing comments associated with feedback.

    Represents comments that can be associated with feedback entries.
    Each comment is associated with a specific feedback entry.
    """

    __tablename__ = "feedback_comments"

    feedback_id: UUID = Field(foreign_key="feedbacks.id", primary_key=True)
    comment_id: UUID = Field(foreign_key="comments.id", primary_key=True)


class FeedbackAttachment(SQLModel, table=True):
    """
    FeedbackAttachment entity storing attachments associated with feedback.

    Represents media files (images, videos, documents) that can be attached
    to feedback entries. Files are stored in S3 and referenced by URL.
    Each attachment has a specific media type for proper handling and display.
    """

    __tablename__ = "feedback_attachments"

    feedback_id: UUID = Field(foreign_key="feedbacks.id", primary_key=True)
    attachment_id: UUID = Field(foreign_key="attachments.id", primary_key=True)


class FeedbackStatusHistory(UUIDBase, table=True):
    """
    FeedbackStatusHistory entity tracking feedback status changes.

    Maintains an audit trail of all status changes for feedback entries,
    including who made the change and when. Essential for tracking
    the lifecycle of feedback processing and accountability.
    """

    __tablename__ = "feedback_status_history"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    feedback_id: UUID = Field(foreign_key="feedbacks.id", nullable=False, index=True)
    status: FeedbackStatus = Field(sa_column=Column(SAEnum(FeedbackStatus), nullable=False))
    changed_by: UUID = Field(foreign_key="users.id", nullable=False)
    changed_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            index=True,
        )
    )

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("feedback_status_history", str(self.feedback_id), str(self.status), str(self.changed_at))


class AnalysisResult(UUIDBase, table=True):
    """
    AnalysisResult entity storing analysis results for feedback.

    Represents analysis results for feedback entries, including sentiment,
    root causes, and recommendations. Each result is associated with a specific
    feedback entry.
    """

    __tablename__ = "analysis_results"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    feedback_id: UUID = Field(foreign_key="feedbacks.id", nullable=False, index=True)
    sentiment: Sentiment = Field(sa_column=Column(SAEnum(Sentiment), nullable=True, index=True))
    root_causes: Optional[list[str]] = Field(default=None, sa_column=Column(ARRAY(Text), nullable=True))
    recommendation: str = Field(sa_column=Column(Text, nullable=True))
    status: AnalysisStatus = Field(sa_column=Column(SAEnum(AnalysisStatus), nullable=False, index=True))
    relevance: bool = Field(sa_column=Column(Boolean, nullable=True))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            index=True,
        )
    )

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("analysis_result", str(self.feedback_id), str(self.created_at))


class Scenario(UUIDBase, table=True):
    """Scenario entity defining AI prompts for different hotel zones.

    Stores customized AI prompts and scenarios for different hotel zones,
    allowing for zone-specific feedback collection and response generation.
    Each scenario is specific to a hotel-zone combination and can be updated.

    Attributes:
        id: Unique identifier for the scenario
        hotel_id: Foreign key reference to the hotel
        zone_id: Foreign key reference to the zone
        prompt: Current AI prompt for this hotel-zone combination
        default_prompt: Default prompt that can be used as fallback
        updated_at: Timestamp of the last update to the scenario
    """

    __tablename__ = "scenarios"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    hotel_id: UUID = Field(foreign_key="hotels.id", nullable=False, index=True)
    zone_id: UUID = Field(foreign_key="zones.id", nullable=False, index=True)
    prompt: str = Field(sa_column=Column(Text, nullable=False))
    default_prompt: str = Field(sa_column=Column(Text, nullable=False))
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime,
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("scenario", str(self.hotel_id), str(self.zone_id), str(self.updated_at))


class Report(UUIDBase, table=True):
    """
    Report entity storing generated feedback reports.

    Represents generated reports containing feedback analysis and statistics
    for specific hotels. Reports include filter criteria and are stored
    in external storage with references via storage keys.

    Attributes:
        id: Unique identifier for the report
        name: Human-readable name of the report
        hotel_id: Foreign key reference to the hotel the report covers
        filters: JSON object containing the filter criteria used for the report
        storage_key: Key/URL for accessing the report file in external storage
        created_at: Timestamp when the report was generated
    """

    __tablename__ = "reports"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(sa_column=Column(Text, nullable=False))
    hotel_id: UUID = Field(foreign_key="hotels.id", nullable=False, index=True)
    filters: dict = Field(sa_column=Column(JSONB, nullable=False))
    storage_key: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            index=True,
        )
    )

    def _generate_deterministic_uuid(self) -> UUID:
        return generate_uuid("report", self.name, str(self.hotel_id), str(self.created_at))


class Reservation(UUIDBase, table=True):
    """
    Reservation entity storing PMS reservation data.

    Represents reservation data synced from PMS system, including guest information,
    stay dates, hotel, and additional reservation details. The data field contains
    the full JSON payload from PMS for flexibility.

    Attributes:
        id: Unique reservation identifier generated from Ref + Hotel (primary key)
        arrival_date: Guest arrival date
        departure_date: Guest departure date
        phone_numbers: Guest phone numbers (array)
        hotel: Hotel name
        status: Current reservation status from PMS (mapped to internal enum)
        pms_incoming_status: Original status string from PMS system (e.g., "Выезд")
        data: Full JSON data from PMS containing all reservation details
        created_at: Timestamp when the reservation was synced
        updated_at: Timestamp when the reservation was last updated
    """

    __tablename__ = "reservations"

    id: UUID = Field(primary_key=True)
    arrival_date: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    departure_date: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    phone_numbers: Optional[list[str]] = Field(sa_column=Column(ARRAY(Text), nullable=False, index=True))
    hotel: str = Field(sa_column=Column(Text, nullable=False, index=True))
    status: ReservationStatus = Field(sa_column=Column(SAEnum(ReservationStatus), nullable=False, index=True))
    pms_incoming_status: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    data: dict = Field(sa_column=Column(JSONB, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            index=True,
        )
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )
    )

    def _generate_deterministic_uuid(self) -> UUID:
        # Extract Ref from data field
        ref = self.data.get("Ref") if isinstance(self.data, dict) else ""
        if not ref:
            raise ValueError("Reservation data must contain Ref field")
        return generate_uuid("reservation", ref, self.hotel)


class ReservationUsers(SQLModel, table=True):
    """
    ReservationUsers entity linking users to their PMS reservations.

    Represents the relationship between system users and PMS reservations,
    tracking which users are associated with which reservations and hotels.
    One reservation can be associated with multiple users (one-to-many).

    Attributes:
        user_id: Foreign key reference to the users (part of composite PK)
        reservation_id: Foreign key reference to the PMS reservation (part of composite PK)
    """

    __tablename__ = "reservation_users"

    user_id: UUID = Field(foreign_key="users.id", primary_key=True, index=True)
    reservation_id: UUID = Field(foreign_key="reservations.id", primary_key=True, index=True)
