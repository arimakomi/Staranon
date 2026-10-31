"""Broadcast service with rate limiting (~25 msg/s), FloodWait handling, and blocked user tracking."""

import asyncio
import logging
from typing import Callable, Optional
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select, update

from app.db.models import BroadcastJob, User, utcnow
from app.db.session import get_session_factory

logger = logging.getLogger("anonbot.broadcaster")


async def execute_broadcast_job(
    bot: Bot,
    job_id: int,
    progress_callback: Optional[Callable[[int, int, int, int], None]] = None,
) -> Optional[BroadcastJob]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        res = await session.execute(select(BroadcastJob).where(BroadcastJob.id == job_id))
        job = res.scalar_one_or_none()
        if not job or job.status != "running":
            return job

        users_res = await session.execute(
            select(User.id).where(User.bot_blocked == False).order_by(User.id.asc())  # noqa: E712
        )
        user_ids = list(users_res.scalars().all())
        job.total_users = len(user_ids)
        await session.commit()

    sent = 0
    failed = 0
    blocked = 0

    for idx, uid in enumerate(user_ids, start=1):
        try:
            if job.mode == "copy" and job.source_chat_id and job.source_message_id:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=job.source_chat_id,
                    message_id=job.source_message_id,
                )
            else:
                await bot.send_message(
                    chat_id=uid,
                    text=job.text_content or "",
                    parse_mode="HTML",
                )
            sent += 1
        except TelegramRetryAfter as retry_exc:
            await asyncio.sleep(retry_exc.retry_after + 1)
            try:
                if job.mode == "copy" and job.source_chat_id and job.source_message_id:
                    await bot.copy_message(
                        chat_id=uid,
                        from_chat_id=job.source_chat_id,
                        message_id=job.source_message_id,
                    )
                else:
                    await bot.send_message(
                        chat_id=uid,
                        text=job.text_content or "",
                        parse_mode="HTML",
                    )
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            blocked += 1
            async with session_factory() as s_blk:
                await s_blk.execute(
                    update(User).where(User.id == uid).values(bot_blocked=True)
                )
                await s_blk.commit()
        except Exception:
            failed += 1

        await asyncio.sleep(0.04)

        if idx % 25 == 0 or idx == len(user_ids):
            async with session_factory() as s_upd:
                await s_upd.execute(
                    update(BroadcastJob)
                    .where(BroadcastJob.id == job_id)
                    .values(
                        sent_count=sent,
                        failed_count=failed,
                        blocked_count=blocked,
                    )
                )
                await s_upd.commit()
            if progress_callback:
                try:
                    progress_callback(len(user_ids), sent, failed, blocked)
                except Exception:
                    pass

    async with session_factory() as s_fin:
        await s_fin.execute(
            update(BroadcastJob)
            .where(BroadcastJob.id == job_id)
            .values(
                sent_count=sent,
                failed_count=failed,
                blocked_count=blocked,
                status="completed",
                finished_at=utcnow(),
            )
        )
        await s_fin.commit()
        res_final = await s_fin.execute(select(BroadcastJob).where(BroadcastJob.id == job_id))
        return res_final.scalar_one_or_none()
