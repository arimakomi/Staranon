"""نقطه ورود اصلی ربات (Polling با Backoff نمایی، Webhook با aiohttp، پشتیبانی اختیاری Redis و هندلر سراسری خطا)."""

import asyncio
import logging
import signal
from typing import Any, Optional
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.config import load_settings
from app.db.session import close_db_engine, create_all_tables, init_db_engine
from app.handlers import inboxes, messaging, super_admin, user_panel
from app.middlewares.pipeline import BotPipelineMiddleware, SlidingWindowRateLimiter
from app.services.telegram_helpers import build_bot_session, notify_super_admins
from app.utils.crypto import CryptoManager
from app.utils.logger import setup_logging


async def build_fsm_storage_and_redis(redis_url: Optional[str]) -> tuple[Any, Optional[Any]]:
    if redis_url:
        try:
            from redis.asyncio import Redis
            from aiogram.fsm.storage.redis import RedisStorage

            redis_client = Redis.from_url(redis_url, decode_responses=True)
            await redis_client.ping()
            logging.getLogger("anonbot").info("Connected to Redis successfully for FSM and Rate Limiting.")
            return RedisStorage(redis=redis_client), redis_client
        except Exception as exc:
            logging.getLogger("anonbot").warning(
                "Redis unavailable (%s); falling back to in-memory FSM storage and rate limiter.",
                exc,
            )
    return MemoryStorage(), None


async def run_bot() -> None:
    cfg = load_settings()
    logger = setup_logging(cfg.log_dir, cfg.log_level)

    if not cfg.bot_token:
        logger.critical("BOT_TOKEN is empty in .env. Exiting.")
        raise SystemExit(1)

    cfg.validate_secrets()
    crypto = CryptoManager(hmac_secret=cfg.hmac_secret, fernet_key=cfg.fernet_key)

    init_db_engine(cfg.database_url)
    await create_all_tables()

    storage, redis_client = await build_fsm_storage_and_redis(cfg.redis_url)
    rate_limiter = SlidingWindowRateLimiter(redis_client=redis_client)

    session = build_bot_session(cfg)
    bot = Bot(
        token=cfg.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher(storage=storage)
    pipeline_mw = BotPipelineMiddleware(crypto=crypto, rate_limiter=rate_limiter)
    dp.message.outer_middleware(pipeline_mw)
    dp.callback_query.outer_middleware(pipeline_mw)

    dp.include_router(super_admin.router)
    dp.include_router(user_panel.router)
    dp.include_router(inboxes.router)
    dp.include_router(messaging.router)

    @dp.errors()
    async def global_error_handler(event: ErrorEvent) -> bool:
        logger.exception("Unhandled exception while processing update: %s", event.exception)
        if cfg.super_admins:
            err_name = type(event.exception).__name__
            await notify_super_admins(
                bot,
                cfg.super_admins,
                f"⚠️ <b>خطای داخلی در ربات ثبت شد:</b>\n<code>{err_name}: {str(event.exception)[:300]}</code>",
            )
        return True

    stop_event = asyncio.Event()

    def _handle_sig(*_: Any) -> None:
        logger.info("Shutdown signal received. Stopping AnonBot gracefully...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_sig)
        except NotImplementedError:
            pass

    try:
        if cfg.run_mode == "webhook" and cfg.webhook_host:
            webhook_url = f"{cfg.webhook_host}{cfg.webhook_path}"
            logger.info("Starting AnonBot in WEBHOOK mode at %s", webhook_url)
            await bot.set_webhook(
                url=webhook_url,
                secret_token=cfg.webhook_secret or None,
                drop_pending_updates=True,
            )
            app = web.Application()
            SimpleRequestHandler(
                dispatcher=dp,
                bot=bot,
                secret_token=cfg.webhook_secret or None,
            ).register(app, path=cfg.webhook_path)
            setup_application(app, dp, bot=bot)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, host=cfg.webapp_host, port=cfg.webapp_port)
            await site.start()
            await stop_event.wait()
            await runner.cleanup()
        else:
            logger.info("Starting AnonBot in POLLING mode...")
            await bot.delete_webhook(drop_pending_updates=True)
            backoff = 2.0
            while not stop_event.is_set():
                try:
                    await dp.start_polling(bot, handle_signals=False)
                    break
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("Polling error (%s). Retrying in %.1fs...", exc, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 1.8, 60.0)
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        await bot.session.close()
        await close_db_engine()
        logger.info("AnonBot stopped cleanly.")


if __name__ == "__main__":
    asyncio.run(run_bot())
