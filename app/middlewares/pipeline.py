"""میدل‌ورهای زنجیره‌ای برای تزریق دیتابیس، حالت تعمیرات، بررسی مسدودیت، Rate Limit و جوین اجباری."""

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict, Optional
from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import settings
from app.db.repositories import BotRepository
from app.db.session import get_session_factory
from app.keyboards.inline import build_force_join_kb
from app.locales.fa import (
    BANNED_TEXT,
    FORCE_JOIN_TEXT,
    MAINTENANCE_TEXT,
    RATE_LIMIT_TEXT,
)
from app.utils.crypto import CryptoManager


class SlidingWindowRateLimiter:
    def __init__(self, redis_client: Optional[Any] = None) -> None:
        self.redis = redis_client
        self._local: Dict[int, Deque[float]] = defaultdict(deque)

    async def is_allowed(self, user_id: int, max_per_minute: int) -> bool:
        if max_per_minute <= 0:
            return True
        now = time.time()
        window = 60.0

        if self.redis is not None:
            try:
                key = f"anonbot:rl:{user_id}"
                pipe = self.redis.pipeline()
                pipe.zremrangebyscore(key, 0, now - window)
                pipe.zadd(key, {str(now): now})
                pipe.zcard(key)
                pipe.expire(key, 65)
                results = await pipe.execute()
                count = int(results[2])
                return count <= max_per_minute
            except Exception:
                pass

        q = self._local[user_id]
        while q and q[0] < now - window:
            q.popleft()
        if len(q) >= max_per_minute:
            return False
        q.append(now)
        return True


class BotPipelineMiddleware(BaseMiddleware):
    def __init__(self, crypto: CryptoManager, rate_limiter: SlidingWindowRateLimiter) -> None:
        super().__init__()
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

        if tg_user is None or tg_user.is_bot:
            return await handler(event, data)

        session_factory = get_session_factory()
        async with session_factory() as session:
            repo = BotRepository(session=session, crypto=self.crypto)
            data["repo"] = repo
            data["crypto"] = self.crypto

            is_super_admin = tg_user.id in settings.super_admins
            data["is_super_admin"] = is_super_admin

            db_user = await repo.upsert_user(
                user_id=tg_user.id,
                first_name=tg_user.first_name or "کاربر",
                username=tg_user.username,
            )
            data["db_user"] = db_user

            maint_db = await repo.get_setting("maintenance_mode", "0")
            in_maintenance = settings.maintenance_mode or (maint_db == "1")
            if in_maintenance and not is_super_admin:
                await self._reply_event(event, MAINTENANCE_TEXT)
                return None

            if db_user.is_banned and not is_super_admin:
                until_str = (
                    db_user.banned_until.strftime("%Y-%m-%d %H:%M UTC")
                    if db_user.banned_until
                    else "دائمی"
                )
                await self._reply_event(
                    event,
                    BANNED_TEXT.format(
                        reason=db_user.ban_reason or "نقض قوانین",
                        until=until_str,
                    ),
                )
                return None

            if not is_super_admin:
                allowed = await self.rate_limiter.is_allowed(
                    tg_user.id, settings.rate_limit_per_minute
                )
                if not allowed:
                    await self._reply_event(event, RATE_LIMIT_TEXT)
                    return None

            if bot and not is_super_admin:
                if isinstance(event, CallbackQuery) and event.data and event.data.startswith("chk_join:"):
                    return await handler(event, data)

                forced_channels = await repo.list_forced_channels(only_active=True)
                if forced_channels:
                    missing = []
                    for ch in forced_channels:
                        try:
                            member = await bot.get_chat_member(ch.chat_id, tg_user.id)
                            if member.status in ("left", "kicked"):
                                missing.append(ch)
                        except Exception:
                            continue

                    if missing:
                        start_payload = ""
                        if isinstance(event, Message) and event.text and event.text.startswith("/start "):
                            start_payload = event.text.split(" ", 1)[1].strip()
                        kb = build_force_join_kb(missing, start_payload)
                        await self._reply_event(event, FORCE_JOIN_TEXT, reply_markup=kb)
                        return None

            return await handler(event, data)

    @staticmethod
    async def _reply_event(event: TelegramObject, text: str, reply_markup: Any = None) -> None:
        try:
            if isinstance(event, Message):
                await event.answer(text, parse_mode="HTML", reply_markup=reply_markup)
            elif isinstance(event, CallbackQuery):
                if reply_markup and event.message:
                    await event.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
                    await event.answer()
                else:
                    await event.answer(text[:190], show_alert=True)
        except Exception:
            pass
