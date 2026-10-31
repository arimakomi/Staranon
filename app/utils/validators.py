"""Input validation, slug verification, anti-link filter, and media type detection."""

import re
from typing import Optional
from aiogram.types import Message

SLUG_REGEX = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
LINK_OR_MENTION_REGEX = re.compile(
    r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|@[a-zA-Z0-9_]{4,32})",
    re.IGNORECASE,
)


def is_valid_slug(slug: str) -> bool:
    """Verify if custom inbox slug is valid and not reserved."""
    if not slug:
        return False
    reserved = {"start", "admin", "help", "inv", "ib"}
    if slug.lower() in reserved or slug.lower().startswith(("ib_", "inv_")):
        return False
    return bool(SLUG_REGEX.match(slug))


def sanitize_title(title: str, max_len: int = 64) -> str:
    """Strip HTML brackets and trim title/alias."""
    cleaned = re.sub(r"[<>]", "", title or "").strip()
    return cleaned[:max_len]


def contains_link_or_id(text: Optional[str]) -> bool:
    """Check if message text/caption contains URLs or Telegram @mentions."""
    if not text:
        return False
    return bool(LINK_OR_MENTION_REGEX.search(text))


def detect_message_media_type(message: Message) -> str:
    """Detect Telegram message media category for per-inbox media filtering."""
    if message.photo:
        return "photo"
    if message.video or message.video_note:
        return "video"
    if message.voice or message.audio:
        return "voice"
    if message.animation:
        return "animation"
    if message.sticker:
        return "sticker"
    if message.document:
        return "document"
    if message.text:
        return "text"
    return "unsupported"
