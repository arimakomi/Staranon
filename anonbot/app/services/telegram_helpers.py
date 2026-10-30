"""توابع کمکی ارتباط با Telegram Bot API، نشست پروکسی، بررسی ادمینی کانال/گروه و ریاکشن ایمن."""

import asyncio
import logging
import time
from typing import List, Optional, Tuple
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, ReactionTypeEmoji

from app.config import Settings

logger = logging.getLogger("anonbot.telegram")


def build_bot_session(cfg: Settings) -> AiohttpSession:
    """
    ساخت نشست AiohttpSession با پشتیبانی از پروکسی SOCKS5 / HTTP
    و سرور Bot API سفارشی (در صورت تنظیم در فایل .env).
    """
    proxy_url = cfg.proxy_url if (cfg.proxy_enabled and cfg.proxy_url) else None
    if cfg.api_base_url:
        api_server = TelegramAPIServer.from_base(cfg.api_base_url.rstrip("/"))
        return AiohttpSession(proxy=proxy_url, api=api_server)
    return AiohttpSession(proxy=proxy_url)


async def test_telegram_connection(
    bot_token: str,
    proxy_url: Optional[str] = None,
    api_base_url: Optional[str] = None,
) -> Tuple[bool, str, float]:
    """
    تست اتصال زنده به تلگرام با متد getMe و محاسبه تاخیر (Latency) بر حسب میلی‌ثانیه.
    """
    if api_base_url:
        api_server = TelegramAPIServer.from_base(api_base_url.rstrip("/"))
        session = AiohttpSession(proxy=proxy_url, api=api_server)
    else:
        session = AiohttpSession(proxy=proxy_url)

    bot = Bot(token=bot_token, session=session)
    start_ts = time.perf_counter()
    try:
        me = await bot.get_me()
        latency_ms = (time.perf_counter() - start_ts) * 1000.0
        return True, f"@{me.username} ({me.full_name} - ID: {me.id})", latency_ms
    except Exception as exc:
        latency_ms = (time.perf_counter() - start_ts) * 1000.0
        return False, str(exc), latency_ms
    finally:
        await bot.session.close()


async def verify_chat_ownership_and_bot_member(
    bot: Bot,
    chat_identifier: str | int,
    user_id: int,
    expected_type: str,
) -> Tuple[bool, Optional[int], str]:
    """
    تأیید اینکه:
    ۱. چت هدف از نوع درخواستی (channel یا group/supergroup) است.
    ۲. ربات در آن چت عضو است (و برای کانال حتماً ادمین است).
    ۳. کاربر درخواست‌دهنده در آن کانال یا گروه واقعاً مالک (creator) یا ادمین (administrator) است.
    """
    try:
        chat = await bot.get_chat(chat_identifier)
    except Exception as exc:
        return False, None, f"امکان دسترسی به چت وجود ندارد (آیا ربات را عضو/ادمین کرده‌اید؟): {exc}"

    if expected_type == "channel" and chat.type != "channel":
        return False, None, "شناسه واردشده مربوط به یک کانال تلگرامی نیست."
    if expected_type == "group" and chat.type not in ("group", "supergroup"):
        return False, None, "شناسه واردشده مربوط به یک گروه یا سوپرگروه نیست."

    try:
        me = await bot.get_me()
        bot_member = await bot.get_chat_member(chat.id, me.id)
        if expected_type == "channel" and bot_member.status not in ("administrator", "creator"):
            return False, None, "ربات باید در کانال مورد نظر «ادمین» باشد."
        if expected_type == "group" and bot_member.status in ("left", "kicked"):
            return False, None, "ربات عضو این گروه نیست."
    except Exception as exc:
        return False, None, f"ربات دسترسی بررسی وضعیت عضویت خود در این چت را ندارد: {exc}"

    try:
        user_member = await bot.get_chat_member(chat.id, user_id)
        if user_member.status not in ("creator", "administrator"):
            return False, None, "شما در این کانال یا گروه مالک یا ادمین نیستید."
    except Exception as exc:
        return False, None, f"امکان تایید ادمین بودن شما در این چت وجود ندارد: {exc}"

    return True, chat.id, chat.title or str(chat.id)


async def fetch_real_chat_admin_ids(bot: Bot, target_chat_id: int) -> List[Tuple[int, str, Optional[str]]]:
    """
    دریافت لیست ادمین‌های واقعی کانال یا گروه با getChatAdministrators جهت همگام‌سازی اختیاری.
    خروجی: لیست (user_id, first_name, username)
    """
    result: List[Tuple[int, str, Optional[str]]] = []
    try:
        admins = await bot.get_chat_administrators(target_chat_id)
        for member in admins:
            if not member.user.is_bot:
                result.append((member.user.id, member.user.first_name or "ادمین", member.user.username))
    except Exception as exc:
        logger.warning("Failed to fetch chat administrators for chat %s: %s", target_chat_id, exc)
    return result


async def safe_copy_message(
    bot: Bot,
    target_chat_id: int,
    from_chat_id: int,
    message_id: int,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    reply_to_message_id: Optional[int] = None,
) -> Optional[int]:
    """
    ارسال کپی پیام با copy_message (بدون تگ Forwarded from) و مدیریت هوشمند FloodWait تلگرام.
    """
    for attempt in range(3):
        try:
            msg_id_obj = await bot.copy_message(
                chat_id=target_chat_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
            )
            return msg_id_obj.message_id
        except TelegramRetryAfter as retry_exc:
            await asyncio.sleep(retry_exc.retry_after + 1)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.debug("Could not copy message to %s: %s", target_chat_id, exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error in copy_message (attempt %d): %s", attempt + 1, exc)
            await asyncio.sleep(1.5 * (attempt + 1))
    return None


async def safe_apply_reaction(
    bot: Bot,
    sender_chat_id: int,
    sender_message_id: int,
    emoji: Optional[str],
    inbox_title: str,
    fallback_text_template: str,
) -> bool:
    """
    اعمال ریاکشن روی پیام اصلی فرستنده با setMessageReaction.
    در صورتی که ایموجی در لیست مجاز تلگرام نباشد یا خطایی رخ دهد،
    به صورت خودکار یک پیام متنی کوتاه برای فرستنده ارسال می‌شود.
    """
    try:
        if emoji is None:
            await bot.set_message_reaction(
                chat_id=sender_chat_id,
                message_id=sender_message_id,
                reaction=[],
            )
            return True

        await bot.set_message_reaction(
            chat_id=sender_chat_id,
            message_id=sender_message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
        return True
    except Exception:
        if emoji is not None:
            try:
                await bot.send_message(
                    chat_id=sender_chat_id,
                    text=fallback_text_template.format(inbox_title=inbox_title, emoji=emoji),
                    reply_to_message_id=sender_message_id,
                    parse_mode="HTML",
                )
                return True
            except Exception as inner_exc:
                logger.debug("Fallback reaction message failed: %s", inner_exc)
        return False


async def notify_super_admins(bot: Bot, super_admins: List[int], text: str) -> None:
    """ارسال اعلان خطاهای مهم یا گزارش‌ها به ادمین‌های کل ربات."""
    for admin_id in super_admins:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            pass
