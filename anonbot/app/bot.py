"""نقطه ورود اصلی اجرای ربات تلگرام (Polling با Backoff و Webhook با aiohttp) و هندلر سراسری خطا."""

import asyncio
import html
import logging
import signal
import traceback
from typing import Optional
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.config import Settings, load_settings
from app.db.session import close_db_engine, create_all_tables, init_db_engine
from app.handlers import inboxes, messaging, super_admin, user_panel
from app.middlewares.pipeline import CorePipelineMiddleware, RateLimiterBackend
from app.services.telegram_helpers import build_bot_session, notify_super_admins
from app.utils.crypto import CryptoManager
from app.utils.logger import setup_logging


async def build_fsm_and_redis(cfg: Settings, logger: logging.Logger) -> tuple[BaseStorage, Optional[ object]]:
    """ساخت لایه FSM و کلاینت Redis اختیاری؛ در صورت عدم تنظیم یا خطا، سوییچ به MemoryStorage."""
    if cfg.redis_url:
        try:
            from redis.asyncio import Redis
            from aiogram.fsm.storage.redis import RedisStorage

            redis_client = Redis.from_url(cfg.redis_url, decode_responses=True)
            await redis_client.ping()
            logger.info("Connected to Redis successfully for FSM and Rate Limiting.")
            return RedisStorage(redis=redis_client), redis_client
        except Exception as exc:
            logger.warning("Redis connection failed (%s); falling back to MemoryStorage.", exc)
    return MemoryStorage(), None


def create_dispatcher(
    cfg: Settings,
    crypto: CryptoManager,
    storage: BaseStorage,
    redis_client: Optional[object],
    logger: logging.Logger,
) -> Dispatcher:
    dp = Dispatcher(storage=storage)
    rate_limiter = RateLimiterBackend(redis_client=redis_client)
    core_mw = CorePipelineMiddleware(cfg=cfg, crypto=crypto, rate_limiter=rate_limiter)

    dp.message.middleware(core_mw)
    dp.callback_query.middleware(core_mw)

    dp.include_router(super_admin.router)
    dp.include_router(user_panel.router)
    dp.include_router(inboxes.router)
    dp.include_router(messaging.router)

    @dp.errors()
    async def global_error_handler(event: ErrorEvent, bot: Bot) -> bool:
        """
        هندلر سراسری خطا: ثبت کامل خطا در لاگ (بدون متن پیام یا آیدی خام فرستنده)،
        ارسال اعلان به ادمین کل در تلگرام و ادامه پایدار کار ربات.
        """
        exc = event.exception
        tb_short = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-3:])
        logger.error("Unhandled exception in update handler: %s\n%s", exc, tb_short)

        alert = (
            "🚨 <b>گزارش خطای سیستمی در ربات</b>\n\n"
            f"• نوع خطا: <code>{html.escape(type(exc).__name__)}</code>\n"
            f"• پیام: <code>{html.escape(str(exc)[:300])}</code>"
        )
        await notify_super_admins(bot, cfg.super_admins, alert)
        return True

    return dp


async def run_polling_with_backoff(bot: Bot, dp: Dispatcher, cfg: Settings, logger: logging.Logger) -> None:
    """
    اجرای ربات در حالت Polling همراه با مدیریت قطعی شبکه یا پروکسی با Exponential Backoff
    و اطلاع‌رسانی به ادمین کل در صورت بروز اختلال پروکسی.
    """
    backoff = 3.0
    max_backoff = 60.0
    proxy_failed_notified = False

    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            me = await bot.get_me()
            cfg.bot_username = me.username or cfg.bot_username
            logger.info("Starting AnonBot polling as @%s (ID: %s)...", me.username, me.id)
            if proxy_failed_notified:
                await notify_super_admins(
                    bot,
                    cfg.super_admins,
                    "✅ اتصال شبکه / پروکسی ربات مجدداً برقرار شد و دریافت پیام‌ها ادامه یافت.",
                )
                proxy_failed_notified = False
            backoff = 3.0
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            break
        except asyncio.CancelledError:
            logger.info("Polling cancelled gracefully.")
            break
        except Exception as exc:
            logger.error(
                "Connection / proxy error in polling loop (%s). Retrying in %.1fs...",
                exc,
                backoff,
            )
            proxy_failed_notified = True
            await asyncio.sleep(backoff)
            backoff = min(backoff * 1.8, max_backoff)


async def run_webhook_server(bot: Bot, dp: Dispatcher, cfg: Settings, logger: logging.Logger) -> None:
    if cfg.proxy_enabled:
        logger.warning(
            "Warning: Proxy is enabled while RUN_MODE=webhook. Telegram servers cannot push webhooks through an outbound client proxy. Consider using RUN_MODE=polling."
        )

    webhook_url = f"{cfg.webhook_host.rstrip('/')}/{cfg.webhook_path.lstrip('/')}"
    await bot.set_webhook(
        url=webhook_url,
        secret_token=cfg.webhook_secret or None,
        allowed_updates=dp.resolve_used_update_types(),
    )
    logger.info("Webhook set to %s; starting aiohttp server on %s:%d", webhook_url, cfg.webapp_host, cfg.webapp_port)

    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=cfg.webhook_secret or None,
    )
    webhook_requests_handler.register(app, path=cfg.webhook_path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=cfg.webapp_host, port=cfg.webapp_port)
    await site.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await runner.cleanup()


async def main() -> None:
    cfg = load_settings()
    logger = setup_logging(cfg.log_dir, cfg.log_level)

    if not cfg.bot_token:
        logger.critical("BOT_TOKEN is missing in .env file!")
        raise SystemExit(1)

    cfg.validate_secrets()
    crypto = CryptoManager(hmac_secret=cfg.hmac_secret, fernet_key=cfg.fernet_key)

    init_db_engine(cfg.database_url)
    await create_all_tables()

    storage, redis_client = await build_fsm_and_redis(cfg, logger)
    session = build_bot_session(cfg)
    bot = Bot(token=cfg.bot_token, session=session)
    dp = create_dispatcher(cfg, crypto, storage, redis_client, logger)

    try:
        if cfg.run_mode == "webhook" and cfg.webhook_host:
            await run_webhook_server(bot, dp, cfg, logger)
        else:
            await run_polling_with_backoff(bot, dp, cfg, logger)
    finally:
        logger.info("Shutting down AnonBot gracefully...")
        await bot.session.close()
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass
        await close_db_engine()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
