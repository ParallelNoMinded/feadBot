import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Optional


class InMemoryState:
    def __init__(self) -> None:
        self.prompt_last_sent_at: dict[str, datetime] = {}
        self.ui_messages: dict[str, Deque[int]] = {}
        self.selected_hotel: dict[str, str] = {}
        self.compose_prompt_shown: dict[str, bool] = {}
        self.admin_waiting_for_phone: dict[str, bool] = {}

        # In-memory feedback composition sessions (channel:user_id -> dict)
        # {"hotel": str|None, "zone": str|None, "rating": int|None,
        #  "last_activity": datetime, "active": bool, "messages": list[str],
        #  "comment_received": bool, "active_feedback_id": str|None,
        #  "message_count": int, "rating_message_id": int|None,
        #  "feedback_message_ids": list[int], "is_new_feedback": bool,
        #  "first_response_sent": bool, "last_combined_message_time": datetime|None}
        self.feedback_sessions: dict[str, dict] = {}

        # Maximum number of messages per feedback session (will be set from config)
        self.max_feedback_messages: int = 8

        # Registration state stored in memory with TTL
        # key: f"{channel}:{user_id}" -> {"step": str|None, "context": dict|None,
        #      "updated_at": datetime}
        self.registration_states: dict[str, dict] = {}
        self.registration_ttl_seconds: int = 10 * 60

        # Mapping to track which feedback should be auto-finalized per user
        self._locks: dict[str, asyncio.Lock] = {}

        # Admin add user state
        # key: user_id -> {"hotel_id": str, "role_id": str, "telegram_id": str,
        #      "phone_number": str}
        self.admin_add_user_data: dict[str, dict] = {}

        # User states for various operations
        # key: user_id -> {"state_name": "value"}
        self.user_states: dict[str, dict] = {}

        # Media message IDs for each feedback
        # key: f"{user_id}:{feedback_id}" -> list[int]
        self.feedback_media_messages: dict[str, list[int]] = {}

        # TTL and cleanup max ui messages
        self._max_ui_messages_per_user: int = 15

        # Buffer for split messages (MAX splits long messages into parts)
        # key: f"{channel}:{user_id}" -> {"parts": list[str], "last_part_time": datetime}
        self._split_message_buffer: dict[str, dict] = {}
        self._split_message_timeout_seconds: float = 2.0  # 2 seconds to wait for next part
        self._split_message_min_length: int = 4000

    # --- Feedback composition (in-memory) ---
    def _fb_key(self, channel: str, user_id: str) -> str:
        return f"{channel}:{user_id}"

    async def start_feedback_session(
        self,
        channel: str,
        user_id: str,
        *,
        hotel: str | None,
        zone: str | None,
        rating: int | None,
        active_feedback_id: str | None = None,
        is_new_feedback: bool = True,
    ) -> None:
        now = datetime.now(timezone.utc)
        key = self._fb_key(channel, user_id)

        if key not in self._locks:
            self._locks[key] = asyncio.Lock()

        async with self._locks[key]:
            self.feedback_sessions[key] = {
                "hotel": hotel,
                "zone": zone,
                "rating": rating,
                "last_activity": now,
                "active": True,
                "messages": [],
                "comment_received": False,
                "active_feedback_id": active_feedback_id,
                "message_count": 0,
                "rating_message_id": None,
                "instruction_message_id": None,
                "feedback_message_ids": [],
                "is_new_feedback": is_new_feedback,
                "first_response_sent": False,
                "last_combined_message_time": None,
            }

    def get_feedback_session(self, channel: str, user_id: str) -> dict | None:
        return self.feedback_sessions.get(self._fb_key(channel, user_id))

    def touch_feedback_session(self, channel: str, user_id: str) -> None:
        key = self._fb_key(channel, user_id)
        s = self.feedback_sessions.get(key)
        if s:
            s["last_activity"] = datetime.now(timezone.utc)

    def add_feedback_message(self, channel: str, user_id: str, text: str) -> None:
        key = self._fb_key(channel, user_id)
        s = self.feedback_sessions.get(key)
        if s and text:
            items = s.get("items") or []
            items.append({"type": "text", "content": text})
            s["items"] = items
            s["last_activity"] = datetime.now(timezone.utc)

    def add_feedback_media(
        self,
        channel: str,
        user_id: str,
        *,
        media_url: str,
        media_kind: str,
    ) -> None:
        key = self._fb_key(channel, user_id)
        s = self.feedback_sessions.get(key)
        if s and media_url:
            items = s.get("items") or []
            items.append({"type": media_kind, "content": media_url})
            s["items"] = items
            s["last_activity"] = datetime.now(timezone.utc)

    def set_feedback_active_id(self, channel: str, user_id: str, feedback_id: str) -> None:
        key = self._fb_key(channel, user_id)
        s = self.feedback_sessions.get(key)
        if s:
            s["active_feedback_id"] = feedback_id
            s["last_activity"] = datetime.now(timezone.utc)

    def get_feedback_active_id(self, channel: str, user_id: str) -> str | None:
        key = self._fb_key(channel, user_id)
        s = self.feedback_sessions.get(key)
        return s.get("active_feedback_id") if s else None

    def end_feedback_session(self, channel: str, user_id: str) -> None:
        key = self._fb_key(channel, user_id)
        self.feedback_sessions.pop(key, None)
        self._locks.pop(key, None)
        self.clear_split_message_buffer(channel, user_id)

    def can_add_message_to_feedback(self, channel: str, user_id: str) -> bool:
        """Check if user can add more messages to current feedback session."""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        if not session:
            return False
        return session.get("message_count", 0) < self.max_feedback_messages

    def increment_feedback_message_count(self, channel: str, user_id: str) -> int:
        """Increment message count for feedback session and return new count."""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        if not session:
            return 0
        session["message_count"] = session.get("message_count", 0) + 1
        session["last_activity"] = datetime.now(timezone.utc)
        return session["message_count"]

    def get_feedback_message_count(self, channel: str, user_id: str) -> int:
        """Get current message count for feedback session."""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        return session.get("message_count", 0) if session else 0

    def set_max_feedback_messages(self, max_messages: int) -> None:
        """Set maximum number of messages per feedback session."""
        self.max_feedback_messages = max_messages

    def remember_ui_message(self, user_id: str, message_id: int) -> None:
        q = self.ui_messages.setdefault(user_id, deque(maxlen=self._max_ui_messages_per_user))
        q.append(message_id)

    def take_ui_messages(self, user_id: str) -> list[int]:
        q = self.ui_messages.pop(user_id, deque())
        return list(q)

    def get_ui_messages(self, user_id: str) -> list[int]:
        """Get UI messages without removing them from state"""
        q = self.ui_messages.get(user_id, deque())
        return list(q)

    def set_selected_hotel(self, user_id: str, hotel: str) -> None:
        self.selected_hotel[user_id] = hotel

    def get_selected_hotel(self, user_id: str) -> str | None:
        return self.selected_hotel.get(user_id)

    def mark_compose_prompt_shown(self, user_id: str) -> None:
        self.compose_prompt_shown[user_id] = True

    def has_compose_prompt_shown(self, user_id: str) -> bool:
        return bool(self.compose_prompt_shown.get(user_id))

    def clear_compose_prompt(self, user_id: str) -> None:
        self.compose_prompt_shown.pop(user_id, None)

    # --- Registration state (in-memory with TTL) ---
    def _is_expired(self, ts: Optional[datetime], ttl_seconds: int) -> bool:
        if not ts:
            return True
        return (datetime.now(timezone.utc) - ts) > timedelta(seconds=ttl_seconds)

    def get_registration(self, channel: str, user_id: str) -> dict | None:
        key = self._fb_key(channel, user_id)
        st = self.registration_states.get(key)
        if st and self._is_expired(st.get("updated_at"), self.registration_ttl_seconds):
            self.registration_states.pop(key, None)
            return None
        return st

    def upsert_registration(self, channel: str, user_id: str) -> dict:
        key = self._fb_key(channel, user_id)
        st = self.registration_states.get(key) or {"step": None, "context": {}}
        st["updated_at"] = datetime.now(timezone.utc)
        self.registration_states[key] = st
        return st

    def set_registration(
        self,
        channel: str,
        user_id: str,
        *,
        step: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> dict:
        st = self.upsert_registration(channel, user_id)
        if step is not None:
            st["step"] = step
        if context is not None:
            st["context"] = context
        st["updated_at"] = datetime.now(timezone.utc)
        return st

    def clear_registration(self, channel: str, user_id: str) -> None:
        key = self._fb_key(channel, user_id)
        self.registration_states.pop(key, None)

    def set_editing_prompt(self, user_id: str, hotel_code: str, zone_code: str) -> None:
        """Set user in editing prompt mode"""
        key = f"editing_prompt:{user_id}"
        self.registration_states[key] = {
            "step": "editing_prompt",
            "context": {
                "hotel_code": hotel_code,
                "zone_code": zone_code,
                "zone_name": zone_code,  # Will be updated with actual name
            },
            "updated_at": datetime.now(timezone.utc),
        }

    def get_editing_prompt(self, user_id: str) -> Optional[dict]:
        """Get user editing prompt state"""
        key = f"editing_prompt:{user_id}"
        state = self.registration_states.get(key)
        if state and state.get("step") == "editing_prompt":
            return state.get("context")
        return None

    def clear_editing_prompt(self, user_id: str) -> None:
        """Clear user editing prompt state"""
        key = f"editing_prompt:{user_id}"
        self.registration_states.pop(key, None)

    def set_editing_prompt_message_id(self, user_id: str, message_id: str | int) -> None:
        """Set message ID for editing prompt UI"""
        key = f"editing_prompt_message:{user_id}"
        self.ui_messages[key] = message_id

    def get_editing_prompt_message_id(self, user_id: str) -> str | int | None:
        """Get message ID for editing prompt UI"""
        key = f"editing_prompt_message:{user_id}"
        return self.ui_messages.get(key)

    def clear_editing_prompt_message_id(self, user_id: str) -> None:
        """Clear message ID for editing prompt UI"""
        key = f"editing_prompt_message:{user_id}"
        self.ui_messages.pop(key, None)

    def set_rating_message_id(self, channel: str, user_id: str, message_id: int) -> None:
        """Set the message ID for the rating UI message"""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        if session:
            session["rating_message_id"] = message_id

    def get_rating_message_id(self, channel: str, user_id: str) -> int | None:
        """Get the message ID for the rating UI message"""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        return session.get("rating_message_id") if session else None

    def clear_rating_message_id(self, channel: str, user_id: str) -> None:
        """Clear the rating message ID"""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        if session:
            session["rating_message_id"] = None

    def set_instruction_message_id(self, channel: str, user_id: str, message_id: int) -> None:
        """Set instruction message ID for user"""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        if session:
            session["instruction_message_id"] = message_id

    def get_instruction_message_id(self, channel: str, user_id: str) -> int | None:
        """Get instruction message ID for user"""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        return session.get("instruction_message_id") if session else None

    def add_feedback_message_id(self, channel: str, user_id: str, message_id: int) -> None:
        """Add feedback message ID to session"""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        if session:
            feedback_ids = session.get("feedback_message_ids", [])
            feedback_ids.append(message_id)
            session["feedback_message_ids"] = feedback_ids

    def get_feedback_message_ids(self, channel: str, user_id: str) -> list[int]:
        """Get all feedback message IDs for session"""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        return session.get("feedback_message_ids", []) if session else []

    def clear_feedback_message_ids(self, channel: str, user_id: str) -> list[int]:
        """Clear and return all feedback message IDs for session"""
        key = self._fb_key(channel, user_id)
        session = self.feedback_sessions.get(key)
        if session:
            feedback_ids = session.get("feedback_message_ids", [])
            session["feedback_message_ids"] = []
            return feedback_ids
        return []

    def set_feedback_detail_message_id(self, user_id: str, feedback_id: str, message_id: int) -> None:
        """Set feedback detail message ID for specific feedback"""
        key = f"feedback_detail:{user_id}:{feedback_id}"
        self.ui_messages[key] = message_id

    def get_feedback_detail_message_id(self, user_id: str, feedback_id: str) -> int | None:
        """Get feedback detail message ID for specific feedback"""
        key = f"feedback_detail:{user_id}:{feedback_id}"
        return self.ui_messages.get(key)

    def clear_feedback_detail_message_id(self, user_id: str, feedback_id: str) -> None:
        """Clear feedback detail message ID for specific feedback"""
        key = f"feedback_detail:{user_id}:{feedback_id}"
        self.ui_messages.pop(key, None)

    def set_admin_add_user_data(self, user_id: str, data: dict) -> None:
        """Set admin add user data"""
        self.admin_add_user_data[user_id] = data

    def get_admin_add_user_data(self, user_id: str) -> Optional[dict]:
        """Get admin add user data"""
        return self.admin_add_user_data.get(user_id)

    def clear_admin_add_user_data(self, user_id: str) -> None:
        """Clear admin add user data"""
        self.admin_add_user_data.pop(user_id, None)

    def set_admin_waiting_for_phone(self, user_id: str, waiting: bool) -> None:
        """Set admin waiting for phone number input"""
        self.admin_waiting_for_phone[user_id] = waiting

    def is_admin_waiting_for_phone(self, user_id: str) -> bool:
        """Check if admin is waiting for phone number input"""
        return self.admin_waiting_for_phone.get(user_id, False)

    def clear_admin_waiting_for_phone(self, user_id: str) -> None:
        """Clear admin waiting for phone number input"""
        self.admin_waiting_for_phone.pop(user_id, None)

    def set_user_state(self, user_id: str, state_name: str, value: str) -> None:
        """Set user state for various operations"""
        if user_id not in self.user_states:
            self.user_states[user_id] = {}
        self.user_states[user_id][state_name] = value

    def get_user_state(self, user_id: str, state_name: str) -> Optional[str]:
        """Get user state for various operations"""
        return self.user_states.get(user_id, {}).get(state_name)

    def clear_user_state(self, user_id: str, state_name: str) -> None:
        """Clear user state for various operations"""
        if user_id in self.user_states:
            self.user_states[user_id].pop(state_name, None)

    # Zone management states
    def set_admin_adding_zone(self, user_id: str, hotel_code: str) -> None:
        """Set admin adding zone state"""
        self.set_user_state(user_id, "admin_adding_zone", hotel_code)

    def get_admin_adding_zone(self, user_id: str) -> Optional[str]:
        """Get admin adding zone hotel code"""
        return self.get_user_state(user_id, "admin_adding_zone")

    def clear_admin_adding_zone(self, user_id: str) -> None:
        """Clear admin adding zone state"""
        self.clear_user_state(user_id, "admin_adding_zone")

    def set_admin_editing_zone_name(self, user_id: str, zone_id: str) -> None:
        """Set admin editing zone name state"""
        self.set_user_state(user_id, "admin_editing_zone_name", zone_id)

    def get_admin_editing_zone_name(self, user_id: str) -> Optional[str]:
        """Get admin editing zone name zone id"""
        return self.get_user_state(user_id, "admin_editing_zone_name")

    def clear_admin_editing_zone_name(self, user_id: str) -> None:
        """Clear admin editing zone name state"""
        self.clear_user_state(user_id, "admin_editing_zone_name")

    def set_admin_editing_zone_description(self, user_id: str, zone_id: str) -> None:
        """Set admin editing zone description state"""
        self.set_user_state(user_id, "admin_editing_zone_description", zone_id)

    def get_admin_editing_zone_description(self, user_id: str) -> Optional[str]:
        """Get admin editing zone description zone id"""
        return self.get_user_state(user_id, "admin_editing_zone_description")

    def clear_admin_editing_zone_description(self, user_id: str) -> None:
        """Clear admin editing zone description state"""
        self.clear_user_state(user_id, "admin_editing_zone_description")

    # Hotel management states
    def set_admin_adding_hotel(self, user_id: str, adding: bool) -> None:
        """Set admin adding hotel state"""
        self.set_user_state(user_id, "admin_adding_hotel", str(adding))

    def get_admin_adding_hotel(self, user_id: str) -> bool:
        """Get admin adding hotel state"""
        state_value = self.get_user_state(user_id, "admin_adding_hotel")
        return state_value == "True" if state_value else False

    def clear_admin_adding_hotel(self, user_id: str) -> None:
        """Clear admin adding hotel state"""
        self.clear_user_state(user_id, "admin_adding_hotel")

    # Media messages management
    def add_feedback_media_message(self, user_id: str, feedback_id: str, message_id: int) -> None:
        """Add media message ID for specific feedback"""
        key = f"{user_id}:{feedback_id}"
        if key not in self.feedback_media_messages:
            self.feedback_media_messages[key] = []
        self.feedback_media_messages[key].append(message_id)

    def get_feedback_media_messages(self, user_id: str, feedback_id: str) -> list[int]:
        """Get media message IDs for specific feedback"""
        key = f"{user_id}:{feedback_id}"
        return self.feedback_media_messages.get(key, [])

    def clear_feedback_media_messages(self, user_id: str, feedback_id: str) -> list[int]:
        """Clear and return media message IDs for specific feedback"""
        key = f"{user_id}:{feedback_id}"
        return self.feedback_media_messages.pop(key, [])

    def clear_all_user_media_messages(self, user_id: str) -> list[int]:
        """Clear and return all media message IDs for a user"""
        all_media_messages = []
        keys_to_remove = []

        for key, message_ids in self.feedback_media_messages.items():
            if key.startswith(f"{user_id}:"):
                all_media_messages.extend(message_ids)
                keys_to_remove.append(key)

        # Remove all keys for this user
        for key in keys_to_remove:
            self.feedback_media_messages.pop(key, None)

        return all_media_messages

    # --- Split message handling (for MAX long messages) ---
    def add_split_message_part(
        self, channel: str, user_id: str, text: str
    ) -> tuple[bool, str | None]:
        """
        Add a part of a potentially split message and return if message is complete.

        Args:
            channel: Channel name
            user_id: User ID
            text: Text part to add

        Returns:
            Tuple of (is_complete, combined_text)
            - is_complete: True if message is ready to process (timeout or new message)
            - combined_text: Combined text if complete, None otherwise
        """
        key = self._fb_key(channel, user_id)
        now = datetime.now(timezone.utc)

        # If message is short, don't buffer - process immediately
        if len(text) < self._split_message_min_length:
            # Check if there's a buffered message to flush first
            if key in self._split_message_buffer:
                buffer = self._split_message_buffer.pop(key)
                combined = "\n".join(buffer["parts"])
                # Start new buffer with current short message
                self._split_message_buffer[key] = {
                    "parts": [text],
                    "last_part_time": now,
                }
                return True, combined
            # No buffer, short message - process immediately
            return True, text

        # Long message - use buffering logic
        if key not in self._split_message_buffer:
            # First part - start buffering
            self._split_message_buffer[key] = {
                "parts": [text],
                "last_part_time": now,
            }
            return False, None

        buffer = self._split_message_buffer[key]
        time_since_last = (now - buffer["last_part_time"]).total_seconds()

        if time_since_last > self._split_message_timeout_seconds:
            # Timeout - previous message is complete, start new one
            combined = "\n".join(buffer["parts"])
            self._split_message_buffer[key] = {
                "parts": [text],
                "last_part_time": now,
            }
            return True, combined

        # Add part to buffer
        buffer["parts"].append(text)
        buffer["last_part_time"] = now
        return False, None

    def flush_split_message_buffer(
        self, channel: str, user_id: str
    ) -> str | None:
        """
        Flush and return combined message from buffer.

        Args:
            channel: Channel name
            user_id: User ID

        Returns:
            Combined text or None if buffer is empty
        """
        key = self._fb_key(channel, user_id)
        buffer = self._split_message_buffer.pop(key, None)
        if buffer and buffer["parts"]:
            return "\n".join(buffer["parts"])
        return None

    def get_split_message_if_ready(
        self, channel: str, user_id: str
    ) -> str | None:
        """
        Get combined message from buffer if timeout has passed.

        Args:
            channel: Channel name
            user_id: User ID

        Returns:
            Combined text if timeout passed, None otherwise
        """
        key = self._fb_key(channel, user_id)
        buffer = self._split_message_buffer.get(key)
        if not buffer or not buffer["parts"]:
            return None

        now = datetime.now(timezone.utc)
        time_since_last = (now - buffer["last_part_time"]).total_seconds()

        if time_since_last > self._split_message_timeout_seconds:
            # Timeout passed - message is ready
            combined = "\n".join(buffer["parts"])
            self._split_message_buffer.pop(key, None)
            return combined

        return None

    def clear_split_message_buffer(self, channel: str, user_id: str) -> None:
        """Clear split message buffer for user."""
        key = self._fb_key(channel, user_id)
        self._split_message_buffer.pop(key, None)

    def has_split_message_buffer(self, channel: str, user_id: str) -> bool:
        """Check if there's a buffered split message."""
        key = self._fb_key(channel, user_id)
        return key in self._split_message_buffer


STATE = InMemoryState()
