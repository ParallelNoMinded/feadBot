from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class IncomingMessage:
    channel: str  # telegram|max
    user_id: str
    text: str | None = None
    rating: int | None = None
    media_url: str | None = None
    media_token: str | None = None  # channel-specific token (e.g., telegram file_id)
    media_kind: str | None = None  # image|audio|video|document
    payload: dict | None = None
    callback_id: str | None = None
    contact_phone: str | None = None  # phone number from contact sharing


@runtime_checkable
class ChannelAdapter(Protocol):
    channel_name: str

    async def parse_webhook(self, payload: bytes, headers: dict[str, str]) -> IncomingMessage | None:
        pass

    async def send_message(
        self,
        user_id: str,
        text: str,
        buttons: list[list[str]] | None = None,
        inline_keyboard: dict | None = None,
        reply_markup: dict | None = None,
    ) -> str | int | None:
        pass

    async def answer_callback(self, callback_query_id: str, text: str | None = None) -> None:
        pass

    async def edit_message(
        self,
        chat_id: str,
        message_id: int,
        text: str | None = None,
        inline_keyboard: dict | None = None,
    ) -> bool:
        pass

    async def edit_message_reply_markup(
        self,
        chat_id: str,
        message_id: int,
        inline_keyboard: dict | None = None,
    ) -> bool:
        pass

    async def delete_message(self, chat_id: str, message_id: str | int) -> bool | None:
        pass

    async def send_document_bytes(
        self,
        user_id: str,
        filename: str,
        data: bytes,
        caption: str | None = None,
        reply_markup: dict | None = None,
    ) -> str | int | None:
        pass

    async def send_media_group_bytes(
        self,
        user_id: str,
        items: list[dict],
        caption: str | None = None,
    ) -> list[str | int] | None:
        pass

    async def download_file_bytes(self, file_token: str) -> bytes | None:
        pass
