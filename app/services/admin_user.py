from datetime import date, datetime
from typing import Any, Dict, List
from uuid import UUID

import structlog
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.base import BaseService
from shared_models import (
    AnalysisResult,
    Attachment,
    Comment,
    Feedback,
    FeedbackAttachment,
    FeedbackComment,
    FeedbackStatusHistory,
    Hotel,
    Role,
    Scenario,
    User,
    UserHotel,
    Zone,
)
from shared_models.constants import ChannelType

logger = structlog.get_logger(__name__)


class AdminUserService(BaseService):
    """Service for admin user management operations"""

    def __init__(self, session: AsyncSession):
        super().__init__(session)

    async def create_user_and_assignment(
        self,
        telegram_id: str,
        phone_number: str,
        hotel_id: str,
        role_id: str,
        channel_type: ChannelType,
    ) -> bool:
        """Create user and user_hotel assignment"""
        try:
            logger.info(
                f"Creating user with hotel_id={hotel_id} "
                f"(type: {type(hotel_id)}), role_id={role_id} "
                f"(type: {type(role_id)})"
            )
            logger.info(
                f"hotel_id value: '{hotel_id}', length: {len(hotel_id) if isinstance(hotel_id, str) else 'N/A'}"
            )
            logger.info(f"role_id value: '{role_id}', length: {len(role_id) if isinstance(role_id, str) else 'N/A'}")

            # Check if user already exists by telegram_id
            user = await self.user_repo.get_by_telegram_id(telegram_id)

            if not user:
                # Create new user
                user = User(
                    external_user_id=telegram_id,
                    phone_number=phone_number,
                    channel_type=channel_type,
                )
                self.session.add(user)
                await self.session.flush()

            # Create user_hotel assignment
            hotel_uuid = UUID(hotel_id)
            role_uuid = UUID(role_id)

            user_hotel = await self.user_hotel_repo.get_active_stay(user.id, hotel_uuid)

            if user_hotel:
                user_hotel.role_id = role_uuid

                logger.info(f"Updated role for existing user_hotel: {user_hotel.id}")
            else:
                user_hotel = UserHotel(
                    user_id=user.id, hotel_id=hotel_uuid, role_id=role_uuid, open=date.today(), close=None
                )
                self.session.add(user_hotel)
                logger.info("Created new assignment for user in hotel")

            await self.session.commit()
            logger.info(f"User created: {telegram_id} assigned to hotel {hotel_id}")
            return True

        except Exception as e:
            logger.error(f"Error creating user: {e}")
            await self.session.rollback()
            return False

    async def get_user_by_telegram_id(self, telegram_id: str) -> Dict[str, Any]:
        """Get user information by telegram ID including all hotels"""
        try:
            logger.info(f"Searching for user with telegram_id: {telegram_id}")

            # Search user by telegram_id
            user = await self.user_repo.get_by_telegram_id(telegram_id)

            if not user:
                logger.warning(f"User not found with telegram_id: {telegram_id}")
                return None

            logger.info(f"Found user: {user.id}, external_user_id: {user.external_user_id}")

            # Get all hotel assignments for this user
            assignments_result = await self.session.execute(
                select(
                    UserHotel,
                    Role.name.label("role_name"),
                    Hotel.name.label("hotel_name"),
                    Hotel.short_name.label("hotel_code"),
                )
                .join(Role, UserHotel.role_id == Role.id)
                .join(Hotel, UserHotel.hotel_id == Hotel.id)
                .where(UserHotel.user_id == user.id)
                .order_by(UserHotel.open.desc())
            )
            assignments = assignments_result.all()

            # Build user data
            user_data = {
                "id": str(user.id),
                "telegram_id": user.external_user_id,
                "phone_number": user.phone_number,
                "hotels": [],
            }

            for user_hotel, role_name, hotel_name, hotel_code in assignments:
                is_active = user_hotel.close is None
                user_data["hotels"].append(
                    {"name": hotel_name, "code": hotel_code, "role": role_name, "is_active": is_active}
                )

            return user_data
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return None

    async def get_hotels_paginated(self, page: int = 1, per_page: int = 10) -> tuple[List[Dict[str, Any]], bool]:
        """Get hotels with pagination"""
        try:
            # Calculate offset
            offset = (page - 1) * per_page

            # Get hotels for current page
            result = await self.session.execute(
                select(Hotel)
                .order_by(Hotel.name)
                .offset(offset)
                .limit(per_page + 1)  # Get one extra to check if there's next page
            )
            hotels = result.scalars().all()

            # Check if there's next page
            has_next = len(hotels) > per_page
            if has_next:
                hotels = hotels[:per_page]  # Remove the extra item

            hotels_data = [
                {"id": str(hotel.id), "name": hotel.name, "code": hotel.short_name, "description": hotel.description}
                for hotel in hotels
            ]

            return hotels_data, has_next

        except Exception as e:
            logger.error(f"Error getting hotels paginated: {e}")
            return [], False

    async def get_hotel_info(self, hotel_code: str) -> Dict[str, Any]:
        """Get detailed hotel information including guests count and zones"""
        try:
            # Get hotel by code
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)

            if not hotel:
                return None

            # Count guests (users with role "Гость")
            guests_count_result = await self.session.execute(
                select(func.count(UserHotel.id))
                .join(Role, UserHotel.role_id == Role.id)
                .where(
                    UserHotel.hotel_id == hotel.id,
                    Role.name == "Гость",
                    UserHotel.close.is_(None),  # Only active stays
                )
            )
            guests_count = guests_count_result.scalar() or 0

            # Get zones for this hotel
            zones_result = await self.session.execute(select(Zone).where(Zone.hotel_id == hotel.id).order_by(Zone.name))
            zones = zones_result.scalars().all()

            zones_data = [
                {
                    "id": str(zone.id),
                    "name": zone.name,
                    "short_name": zone.short_name,
                    "is_adult": zone.is_adult,
                    "disabled_at": zone.disabled_at,
                }
                for zone in zones
            ]

            return {
                "id": str(hotel.id),
                "name": hotel.name,
                "short_name": hotel.short_name,
                "description": hotel.description,
                "timezone": hotel.timezone,
                "guests_count": guests_count,
                "zones": zones_data,
            }

        except Exception as e:
            logger.error(f"Error getting hotel info: {e}")
            return None

    async def update_hotel_description(self, hotel_code: str, new_description: str) -> bool:
        """Update hotel description"""
        try:
            # Update hotel description
            result = await self.session.execute(
                update(Hotel).where(Hotel.short_name == hotel_code).values(description=new_description)
            )

            if result.rowcount > 0:
                await self.session.commit()
                logger.info(f"Updated description for hotel {hotel_code}")
                return True
            else:
                logger.warning(f"Hotel {hotel_code} not found for description update")
                return False

        except Exception as e:
            logger.error(f"Error updating hotel description: {e}")
            await self.session.rollback()
            return False

    async def update_hotel_name(self, hotel_code: str, new_name: str) -> bool:
        """Update hotel name"""
        try:
            # Update hotel name
            result = await self.session.execute(
                update(Hotel).where(Hotel.short_name == hotel_code).values(name=new_name)
            )

            if result.rowcount > 0:
                await self.session.commit()
                logger.info(f"Updated name for hotel {hotel_code}")
                return True
            else:
                logger.warning(f"Hotel {hotel_code} not found for name update")
                return False

        except Exception as e:
            logger.error(f"Error updating hotel name: {e}")
            await self.session.rollback()
            return False

    async def get_hotel_users_paginated(
        self,
        hotel_id: str,
        page: int = 1,
        per_page: int = 10,
        exclude_telegram_id: str = None,
    ) -> tuple[List[Dict[str, Any]], bool]:
        """Get users for a specific hotel with pagination (excluding guests)"""
        try:
            # Calculate offset
            offset = (page - 1) * per_page

            # Build where conditions
            where_conditions = [UserHotel.hotel_id == hotel_id, UserHotel.close.is_(None), Role.name != "Гость"]

            # Exclude specific telegram_id if provided
            if exclude_telegram_id:
                where_conditions.append(User.external_user_id != exclude_telegram_id)

            # Get users for current page
            result = await self.session.execute(
                select(User, Role.name.label("role_name"), Hotel.name.label("hotel_name"))
                .join(UserHotel, User.id == UserHotel.user_id)
                .join(Role, UserHotel.role_id == Role.id)
                .join(Hotel, UserHotel.hotel_id == Hotel.id)
                .where(and_(*where_conditions))
                .order_by(User.created_at.desc())
                .offset(offset)
                .limit(per_page + 1)  # Get one extra to check if there's next page
            )
            users = result.all()

            # Check if there's next page
            has_next = len(users) > per_page
            if has_next:
                users = users[:per_page]  # Remove the extra item

            users_data = [
                {
                    "id": str(user.id),
                    "telegram_id": user.external_user_id,
                    "phone_number": user.phone_number,
                    "role_name": role_name,
                    "hotel_name": hotel_name,
                    "created_at": user.created_at.isoformat(),
                }
                for user, role_name, hotel_name in users
            ]

            return users_data, has_next

        except Exception as e:
            logger.error(f"Error getting hotel users paginated: {e}")
            return [], False

    async def get_user_detail(self, user_id: str) -> Dict[str, Any] | None:
        """Get detailed information about a user including all hotels"""
        try:
            # Convert string user_id to UUID for database query
            user_uuid = UUID(user_id)

            # Get user with all hotel assignments
            result = await self.session.execute(
                select(
                    User,
                    UserHotel,
                    Role.name.label("role_name"),
                    Hotel.name.label("hotel_name"),
                    Hotel.short_name.label("hotel_code"),
                )
                .join(UserHotel, User.id == UserHotel.user_id)
                .join(Role, UserHotel.role_id == Role.id)
                .join(Hotel, UserHotel.hotel_id == Hotel.id)
                .where(
                    User.id == user_uuid,
                )
            )
            assignments = result.all()

            if not assignments:
                return None

            # Group assignments by user
            first_user = assignments[0][0]  # User object
            user_data = {
                "id": str(first_user.id),
                "telegram_id": first_user.external_user_id,
                "phone_number": first_user.phone_number,
                "hotels": [],
            }

            for _, user_hotel, role_name, hotel_name, hotel_code in assignments:
                is_active = user_hotel.close is None
                user_data["hotels"].append(
                    {"name": hotel_name, "code": hotel_code, "role": role_name, "is_active": is_active}
                )

            return user_data

        except Exception as e:
            logger.error(f"Error getting user detail: {e}")
            return None

    async def deactivate_user(self, user_id: str) -> bool:
        """Deactivate user by closing all active hotel assignments"""
        try:
            # Close all active assignments for the user
            await self.session.execute(
                update(UserHotel)
                .where(UserHotel.user_id == user_id, UserHotel.close.is_(None))
                .values(close=datetime.utcnow())
            )

            await self.session.commit()
            return True

        except Exception as e:
            logger.error(f"Error deactivating user: {e}")
            await self.session.rollback()
            return False

    async def search_user_by_phone(self, phone_number: str) -> Dict[str, Any] | None:
        """Search user by phone number and return detailed information"""
        try:
            # Search user by phone number
            result = await self.session.execute(select(User).where(User.phone_number == phone_number))
            user = result.scalars().first()

            if not user:
                return None

            # Get all hotel assignments for this user
            assignments_result = await self.session.execute(
                select(
                    UserHotel,
                    Role.name.label("role_name"),
                    Hotel.name.label("hotel_name"),
                    Hotel.short_name.label("hotel_code"),
                )
                .join(Role, UserHotel.role_id == Role.id)
                .join(Hotel, UserHotel.hotel_id == Hotel.id)
                .where(UserHotel.user_id == user.id)
                .order_by(UserHotel.open.desc())
            )
            assignments = assignments_result.all()

            # Build user data
            user_data = {
                "id": str(user.id),
                "telegram_id": user.external_user_id,
                "phone_number": user.phone_number,
                "hotels": [],
            }

            for user_hotel, role_name, hotel_name, hotel_code in assignments:
                is_active = user_hotel.close is None
                user_data["hotels"].append(
                    {
                        "hotel_id": str(user_hotel.hotel_id),
                        "name": hotel_name,
                        "code": hotel_code,
                        "role": role_name,
                        "is_active": is_active,
                        "created_at": user_hotel.open.isoformat(),
                        "closed_at": user_hotel.close.isoformat() if user_hotel.close else None,
                    }
                )

            return user_data

        except Exception as e:
            logger.error(f"Error searching user by phone: {e}")
            return None

    async def change_user_role_in_hotel(self, user_id: str, hotel_id: str, new_role_id: str) -> bool:
        """Change user role in specific hotel"""
        try:
            # Update the role for the user in the specific hotel
            await self.session.execute(
                update(UserHotel)
                .where(
                    UserHotel.user_id == user_id,
                    UserHotel.hotel_id == hotel_id,
                    UserHotel.close.is_(None),  # Only update active assignments
                )
                .values(role_id=new_role_id)
            )

            await self.session.commit()
            return True

        except Exception as e:
            logger.error(f"Error changing user role in hotel: {e}")
            await self.session.rollback()
            return False

    async def toggle_user_status_in_hotel(self, user_id: str, hotel_id: str) -> bool:
        """Toggle user status (activate/deactivate) in specific hotel"""
        try:
            # First, check current status
            result = await self.session.execute(
                select(UserHotel)
                .where(UserHotel.user_id == user_id, UserHotel.hotel_id == hotel_id)
                .order_by(UserHotel.open.desc())
                .limit(1)
            )
            user_hotel = result.scalars().first()

            if not user_hotel:
                return False

            # Toggle status
            if user_hotel.close is None:
                await self.session.execute(
                    update(UserHotel).where(UserHotel.id == user_hotel.id).values(close=date.today())
                )
            else:
                await self.session.execute(update(UserHotel).where(UserHotel.id == user_hotel.id).values(close=None))

            await self.session.commit()
            return True

        except Exception as e:
            logger.error(f"Error toggling user status in hotel: {e}")
            await self.session.rollback()
            return False

    async def delete_user_from_hotel(self, user_id: str, hotel_id: str) -> bool:
        """Delete user from hotel and all related data"""
        try:
            # First, find the user_hotel record
            result = await self.session.execute(
                select(UserHotel)
                .where(UserHotel.user_id == user_id, UserHotel.hotel_id == hotel_id)
                .order_by(UserHotel.open.desc())
                .limit(1)
            )
            user_hotel = result.scalars().first()

            if not user_hotel:
                logger.warning(f"User hotel relationship not found for user_id={user_id}, hotel_id={hotel_id}")
                return False

            user_stay_id = str(user_hotel.id)

            # Delete related data in the correct order
            # (respecting foreign key constraints)

            # 1. Delete analysis results
            await self.session.execute(
                delete(AnalysisResult).where(
                    AnalysisResult.feedback_id.in_(select(Feedback.id).where(Feedback.user_stay_id == user_stay_id))
                )
            )

            # 2. Delete feedback attachments
            await self.session.execute(
                delete(FeedbackAttachment).where(
                    FeedbackAttachment.feedback_id.in_(select(Feedback.id).where(Feedback.user_stay_id == user_stay_id))
                )
            )

            # 3. Delete feedback comments
            await self.session.execute(
                delete(FeedbackComment).where(
                    FeedbackComment.feedback_id.in_(select(Feedback.id).where(Feedback.user_stay_id == user_stay_id))
                )
            )

            # 4. Delete feedbacks
            await self.session.execute(delete(Feedback).where(Feedback.user_stay_id == user_stay_id))

            # 5. Finally, delete the user_hotel relationship
            await self.session.execute(delete(UserHotel).where(UserHotel.id == user_stay_id))

            await self.session.commit()
            logger.info(f"Successfully deleted user {user_id} from hotel {hotel_id}")
            return True

        except Exception as e:
            logger.error(f"Error deleting user from hotel: {e}")
            await self.session.rollback()
            return False

    async def get_zones_paginated(
        self, hotel_code: str, page: int = 1, per_page: int = 5
    ) -> tuple[List[Dict[str, Any]], bool]:
        """Get zones for a hotel with pagination"""
        try:
            # Get hotel by code
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)

            if not hotel:
                return [], False

            # Calculate offset
            offset = (page - 1) * per_page

            # Get zones for current page
            result = await self.session.execute(
                select(Zone)
                .where(Zone.hotel_id == hotel.id)
                .order_by(Zone.name)
                .offset(offset)
                .limit(per_page + 1)  # Get one extra to check if there's next page
            )
            zones = result.scalars().all()

            # Check if there's next page
            has_next = len(zones) > per_page
            if has_next:
                zones = zones[:per_page]  # Remove the extra item

            zones_data = [
                {
                    "id": str(zone.id),
                    "name": zone.name,
                    "short_name": zone.short_name,
                    "is_adult": zone.is_adult,
                    "disabled_at": zone.disabled_at,
                }
                for zone in zones
            ]

            return zones_data, has_next

        except Exception as e:
            logger.error(f"Error getting zones paginated: {e}")
            return [], False

    async def create_zone(self, hotel_code: str, name: str, short_name: str, is_adult: bool = False) -> bool:
        """Create a new zone for a hotel"""
        try:
            # Get hotel by code
            hotel = await self.catalog_repo.get_hotel_by_code(hotel_code)

            if not hotel:
                logger.error(f"Hotel {hotel_code} not found")
                return False

            # Create new zone
            new_zone = Zone(hotel_id=hotel.id, name=name, short_name=short_name, is_adult=is_adult)

            self.session.add(new_zone)
            await self.session.commit()

            logger.info(f"Created zone {name} for hotel {hotel_code}")
            return True

        except Exception as e:
            logger.error(f"Error creating zone: {e}")
            await self.session.rollback()
            return False

    async def update_zone(
        self, zone_id: str, name: str = None, short_name: str = None, is_adult: bool = None, description: str = None
    ) -> bool:
        """Update zone information"""
        try:
            # Build update values
            update_values = {}
            if name is not None:
                update_values["name"] = name
            if short_name is not None:
                update_values["short_name"] = short_name
            if is_adult is not None:
                update_values["is_adult"] = is_adult
            if description is not None:
                update_values["description"] = description

            if not update_values:
                return True  # Nothing to update

            # Update zone
            result = await self.session.execute(update(Zone).where(Zone.id == zone_id).values(**update_values))

            if result.rowcount > 0:
                await self.session.commit()
                logger.info(f"Updated zone {zone_id}")
                return True
            else:
                logger.warning(f"Zone {zone_id} not found for update")
                return False

        except Exception as e:
            logger.error(f"Error updating zone: {e}")
            await self.session.rollback()
            return False

    async def check_zone_short_name_unique(self, hotel_id: str, short_name: str) -> bool:
        """Check if short name is unique within hotel"""
        try:
            query = select(Zone).where(Zone.hotel_id == hotel_id, Zone.short_name == short_name)

            result = await self.session.execute(query)
            existing_zone = result.scalars().first()

            return existing_zone is None

        except Exception as e:
            logger.error(f"Error checking zone short name uniqueness: {e}")
            return False

    async def delete_zone(self, zone_id: str) -> bool:
        """Delete a zone along with its dependent data to satisfy FK constraints."""
        try:
            feedback_ids_result = await self.session.execute(select(Feedback.id).where(Feedback.zone_id == zone_id))
            feedback_ids = feedback_ids_result.scalars().all()

            if feedback_ids:
                await self.session.execute(delete(AnalysisResult).where(AnalysisResult.feedback_id.in_(feedback_ids)))
                await self.session.execute(
                    delete(FeedbackStatusHistory).where(FeedbackStatusHistory.feedback_id.in_(feedback_ids))
                )

                attachment_ids_result = await self.session.execute(
                    select(FeedbackAttachment.attachment_id).where(FeedbackAttachment.feedback_id.in_(feedback_ids))
                )
                attachment_ids = attachment_ids_result.scalars().all()
                await self.session.execute(
                    delete(FeedbackAttachment).where(FeedbackAttachment.feedback_id.in_(feedback_ids))
                )
                if attachment_ids:
                    await self.session.execute(delete(Attachment).where(Attachment.id.in_(attachment_ids)))

                comment_ids_result = await self.session.execute(
                    select(FeedbackComment.comment_id).where(FeedbackComment.feedback_id.in_(feedback_ids))
                )
                comment_ids = comment_ids_result.scalars().all()
                await self.session.execute(delete(FeedbackComment).where(FeedbackComment.feedback_id.in_(feedback_ids)))
                if comment_ids:
                    await self.session.execute(delete(Comment).where(Comment.id.in_(comment_ids)))

                await self.session.execute(delete(Feedback).where(Feedback.id.in_(feedback_ids)))

            await self.session.execute(delete(Scenario).where(Scenario.zone_id == zone_id))

            result = await self.session.execute(delete(Zone).where(Zone.id == zone_id))
            await self.session.commit()

            if result.rowcount > 0:
                logger.info(f"Deleted zone {zone_id} and {len(feedback_ids)} related feedbacks")
                return True
            logger.warning(f"Zone {zone_id} not found for deletion")

        except Exception as e:
            logger.error(f"Error deleting zone: {e}")
            await self.session.rollback()
        return False

    async def check_hotel_short_name_unique(self, short_name: str) -> bool:
        """Check if hotel short name is unique"""
        try:
            hotel = await self.catalog_repo.get_hotel_by_code(short_name)
            return hotel is None
        except Exception as e:
            logger.error(f"Error checking hotel short name uniqueness: {e}")
            return False

    async def create_hotel(
        self,
        name: str,
        short_name: str,
        description: str,
        timezone: str,
    ) -> bool:
        """Create a new hotel"""
        try:
            # Create new hotel
            hotel = Hotel(name=name, short_name=short_name, description=description, timezone=timezone)
            self.session.add(hotel)
            await self.session.commit()

            logger.info(f"Hotel created: {name} ({short_name})")
            return True

        except Exception as e:
            logger.error(f"Error creating hotel: {e}")
            await self.session.rollback()
            return False
