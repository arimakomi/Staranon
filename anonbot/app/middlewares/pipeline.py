"""میدل‌ورهای تزریق دیتابیس، بررسی حالت تعمیرات، مسدودیت کاربر، محدودیت نرخ (Redis/حافظه) و جوین اجباری."""

import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List, Optional
from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject

from app.config import Settings
from app.db.repositories import BotRepository
from app.db.session import get_session_factory
from app.locales.fa import Texts
from app.utils.crypto import CryptoManager

logger = logging.getLogger("anonbot.middleware")


class RateLimiterBackend:
    """پشتیبان دوگانه Rate Limit با Redis اختیاری و Fallback خودکار به حافظه رم."""

    def __init__(self, redis_client: Any = None) -> None:
        self.redis = redis_client
        self._memory_buckets: Dict[int, List[float]] = defaultdict(list)

    async def is_allowed(self, user_id: int, limit_per_minute: int) -> bool:
        if limit_per_minute <= 0:
            return True
        now = time.time()

        if self.redis is not None:
            key = f"anonbot:rl:{user_id}:{int(now // 60)}"
            try:
                val = await self.redis.incr(key)
                if val == 1:
                    await self.redis.expire(key, 65)
                return int(val) <= limit_per_minute
            except Exception as exc:
                logger.warning("Redis rate limiter error, falling back to memory: %s", exc)

        bucket = self._memory_buckets[user_id]
        cutoff = now - 60.0
        bucket[:] = [ts for ts in bucket if ts > cutoff]
        if len(bucket) >= limit_per_minute:
            return False
        bucket.append(now)
        return True


class CorePipelineMiddleware(BaseMiddleware):
    def __init__(
        self,
        cfg: Settings,
        crypto: CryptoManager,
        rate_limiter: RateLimiterBackend,
    ) -> None:
        super().__init__()
        self.cfg = cfg
        self.crypto = crypto
        self.rate_limiter = rate_limiter

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        bot: Optional[Bot] = data.get("bot")

        session_factory = get_session_factory()
        async with session_factory() as session:
            repo = BotRepository(session=session, crypto=self.crypto)
            data["session"] = session
            data["repo"] = repo
            data["crypto"] = self.crypto
            data["settings"] = self.cfg

            if tg_user is None or tg_user.is_bot:
                return await handler(event, data)

            is_super_admin = tg_user.id in self.cfg.super_admins
            data["is_super_admin"] = is_super_admin

            # ۱. بررسی حالت تعمیرات (برای کاربران عادی)
            maint_setting = await repo.get_setting(
                "maintenance_mode", "true" if self.cfg.maintenance_mode else "false"
            )
            if maint_setting.lower() == "true" and not is_super_admin:
                await self._reply_text(event, Texts.MAINTENANCE_ACTIVE)
                return None

            # ۲. ثبت یا به‌روزرسانی کاربر در دیتابیس
            db_user = await repo.upsert_user(
                user_id=tg_user.id,
                first_name=tg_user.first_name or "کاربر",
                username=tg_user.username,
            )
            data["db_user"] = db_user

            # ۳. بررسی مسدودیت سراسری کاربر
            if db_user.is_banned and not is_super_admin:
                until_str = (
                    db_user.banned_until.strftime("%Y-%m-%d %H:%M UTC")
                    if db_user.banned_until
                    else "دائمی"
                )
                msg = Texts.USER_BANNED.format(
                    reason=db_user.ban_reason or "نقض قوانین",
                    until=until_str,
                )
                await self._reply_text(event, msg)
                return None

            # ۴. بررسی محدودیت نرخ (Rate Limit)
            rl_str = await repo.get_setting(
                "rate_limit_per_minute", str(self.cfg.rate_limit_per_minute)
            )
            rl_limit = int(rl_str) if rl_str.isdigit() else self.cfg.rate_limit_per_minute
            if not is_super_admin:
                allowed = await self.rate_limiter.is_allowed(tg_user.id, rl_limit)
                if not allowed:
                    await self._reply_text(event, Texts.RATE_LIMITED)
                    return None

            # ۵. بررسی جوین اجباری در تمام تعاملات
            if not is_super_admin and bot is not None:
                channels = await repo.list_forced_channels(only_active=True)
                if channels:
                    missing = []
                    for ch in channels:
                        try:
                            member = await bot.get_chat_member(ch.chat_id, tg_user.id)
                            if member.status in ("left", "kicked"):
                                missing.append(ch)
                        except Exception:
                            # اگر ربات دسترسی به کانال نداشت، کاربر را مسدود نمی‌کنیم
                            pass
                    if missing:
                        rows = [
                            [InlineKeyboardButton(text=f"📢 {c.title}", url=c.invite_link)]
                            for c in missing
                        ]
                        rows.append(
                            [InlineKeyboardButton(text=Texts.BTN_CHECK_JOIN, callback_data="check_forced_join")]
                        )
                        kb = InlineKeyboardMarkup(inline_keyboard=rows)
                        if isinstance(event, CallbackQuery) and event.data == "check_forced_join":
                            await event.answer(Texts.NOT_JOINED_YET, show_alert=True)
                            return None
                        await self._reply_text(event, Texts.FORCED_JOIN_MSG, reply_markup=kb)
                        return None

            return await handler(event, data)

    @staticmethod
    async def _reply_text(
        event: TelegramObject,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        if isinstance(event, Message):
            await event.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        elif isinstance(event, CallbackQuery):
            if event.message:
                await event.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
            await event.answer()
