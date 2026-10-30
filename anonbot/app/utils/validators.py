"""اعتبارسنجی ورودی‌ها، Slugها، لینک‌ها و تشخیص نوع پیام تلگرام."""

import re
from typing import Optional
from aiogram.types import Message

SLUG_REGEX = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
LINK_OR_MENTION_REGEX = re.compile(
    r"(https?://\S+|www\.\S+|t\.me/\S+|telegram\.me/\S+|@[a-zA-Z0-9_]{4,32})",
    re.IGNORECASE,
)


def is_valid_slug(slug: str) -> bool:
    """بررسی معتبر بودن کد کوتاه (Slug) انتخابی برای لینک صندوق."""
    if not slug:
        return False
    reserved = {"start", "admin", "help", "inv", "ib"}
    if slug.lower() in reserved or slug.lower().startswith(("ib_", "inv_")):
        return False
    return bool(SLUG_REGEX.match(slug))


def sanitize_title(title: str, max_len: int = 64) -> str:
    """پاکسازی عنوان صندوق یا نام مستعار از تگ‌های HTML و کاراکترهای کنترلی."""
    cleaned = re.sub(r"[<>]", "", title or "").strip()
    return cleaned[:max_len]


def contains_link_or_id(text: Optional[str]) -> bool:
    """بررسی وجود لینک یا یوزرنیم تلگرام در متن یا کپشن پیام (برای فیلتر ضد تبلیغ)."""
    if not text:
        return False
    return bool(LINK_OR_MENTION_REGEX.search(text))


def detect_message_media_type(message: Message) -> str:
    """تشخیص نوع رسانه پیام تلگرام جهت تطبیق با فیلترهای مجاز هر صندوق."""
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
