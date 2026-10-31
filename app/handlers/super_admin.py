"""هندلرهای پنل ادمین کل ربات (/admin): آمار، همگانی، جوین اجباری، مدیریت کاربر و تایید گزارش تخلف."""

import asyncio
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.db.models import BroadcastJob, User
from app.db.repositories import BotRepository
from app.handlers.states import SuperAdminState
from app.keyboards.inline import (
    build_main_reply_kb,
    build_super_admin_kb,
    build_writing_cancel_kb,
)
from app.locales.fa import BTN_SUPER_ADMIN
from app.services.broadcaster import execute_broadcast_job

router = Router(name="super_admin")


@router.message(Command("admin"))
@router.message(F.text == BTN_SUPER_ADMIN)
async def cmd_super_admin_panel(
    message: Message,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    maint_db = await repo.get_setting("maintenance_mode", "0")
    maint_active = settings.maintenance_mode or (maint_db == "1")
    await message.answer(
        "🛠 <b>پنل مدیریت کل ربات (Super Admin)</b>\n"
        "یکی از بخش‌های مدیریتی زیر را انتخاب کنید:",
        parse_mode="HTML",
        reply_markup=build_super_admin_kb(maint_active),
    )


@router.callback_query(F.data == "adm_stats")
async def cb_admin_live_stats(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    stats = await repo.get_live_stats()
    text = (
        "📊 <b>آمار زنده ربات صندوق ناشناس</b>\n\n"
        f"👥 کل کاربران ثبت‌شده: <b>{stats['total_users']}</b>\n"
        f"🟢 کاربران فعال ۲۴ ساعت اخیر: <b>{stats['active_24h']}</b>\n"
        f"📬 کل صندوق‌های ساخته‌شده: <b>{stats['total_inboxes']}</b>\n"
        f"📩 پیام‌های ارسالی امروز (۲۴ ساعت): <b>{stats['messages_today']}</b>\n"
        f"🚨 گزارش‌های تخلف باز: <b>{stats['open_reports']}</b>\n"
        f"⛔ کاربران مسدودشده: <b>{stats['banned_users']}</b>"
    )
    await callback.answer()
    if callback.message:
        await callback.message.answer(text, parse_mode="HTML")


@router.callback_query(F.data == "adm_maint_tg")
async def cb_admin_toggle_maintenance(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    curr = await repo.get_setting("maintenance_mode", "0")
    new_val = "0" if curr == "1" else "1"
    await repo.set_setting("maintenance_mode", new_val)
    settings.maintenance_mode = new_val == "1"
    await callback.answer(
        "🔧 حالت تعمیرات فعال شد." if new_val == "1" else "🟢 ربات از حالت تعمیرات خارج شد.",
        show_alert=True,
    )
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=build_super_admin_kb(settings.maintenance_mode)
        )


@router.callback_query(F.data == "adm_bcast")
async def cb_admin_prompt_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await state.set_state(SuperAdminState.waiting_broadcast_content)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📣 <b>ارسال پیام همگانی به تمام کاربران فعال ربات</b>\n\n"
            "پیام خود (متن، عکس، ویدیو، ویس و...) را ارسال کنید تا با رعایت محدودیت نرخ تلگرام (~۲۵ پیام در ثانیه) برای همه ارسال شود:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(SuperAdminState.waiting_broadcast_content)
async def msg_admin_start_broadcast(
    message: Message,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    await state.clear()

    is_plain_text = bool(message.text and not message.photo and not message.video and not message.document)
    job = BroadcastJob(
        created_by=db_user.id,
        mode="text" if is_plain_text else "copy",
        text_content=message.html_text if is_plain_text else None,
        source_chat_id=None if is_plain_text else message.chat.id,
        source_message_id=None if is_plain_text else message.message_id,
        status="running",
    )
    repo.session.add(job)
    await repo.session.commit()
    await repo.session.refresh(job)

    status_msg = await message.answer(
        f"⏳ صف ارسال همگانی <code>#{job.id}</code> آغاز شد...",
        parse_mode="HTML",
        reply_markup=build_main_reply_kb(True),
    )

    async def _run_and_report() -> None:
        final_job = await execute_broadcast_job(bot, job.id)
        if final_job:
            try:
                await status_msg.edit_text(
                    f"✅ <b>ارسال همگانی #{final_job.id} به پایان رسید!</b>\n\n"
                    f"👥 کل هدف: <b>{final_job.total_users}</b>\n"
                    f"✅ موفق: <b>{final_job.sent_count}</b>\n"
                    f"🚫 بلاک‌کرده: <b>{final_job.blocked_count}</b>\n"
                    f"❌ خطا: <b>{final_job.failed_count}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    asyncio.create_task(_run_and_report())


# ----------------------------------------------------------------------
# مدیریت کانال‌های جوین اجباری (Forced Join Channels)
# ----------------------------------------------------------------------
@router.callback_query(F.data == "adm_fch")
async def cb_admin_forced_channels(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    channels = await repo.list_forced_channels(only_active=False)
    rows = []
    for ch in channels:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 حذف «{ch.title}» ({ch.chat_id})",
                    callback_data=f"adm_fch_rm:{ch.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ افزودن کانال جوین اجباری", callback_data="adm_fch_add")])
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📢 <b>مدیریت کانال‌های جوین اجباری:</b>\n"
            "<i>(ربات باید در کانال‌های جوین اجباری ادمین باشد تا بتواند عضویت کاربران را بررسی کند)</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data.startswith("adm_fch_rm:"))
async def cb_admin_remove_forced_channel(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    ch_id = int(callback.data.split(":")[1])
    await repo.remove_forced_channel(ch_id)
    await callback.answer("🗑 کانال از لیست جوین اجباری حذف شد.", show_alert=True)
    await cb_admin_forced_channels(callback, repo, is_super_admin)


@router.callback_query(F.data == "adm_fch_add")
async def cb_admin_prompt_fch_id(
    callback: CallbackQuery,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    await state.set_state(SuperAdminState.waiting_forced_channel_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "📢 یوزرنیم کانال (مثلاً <code>@MyChannel</code>) یا آیدی عددی آن (مثلاً <code>-100123456789</code>) را ارسال کنید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(SuperAdminState.waiting_forced_channel_id)
async def msg_admin_fch_title(message: Message, state: FSMContext) -> None:
    await state.update_data(fch_id=(message.text or "").strip())
    await state.set_state(SuperAdminState.waiting_forced_channel_title)
    await message.answer("🏷 عنوان نمایشی کانال را ارسال کنید (مثلاً: «کانال رسمی ما»):")


@router.message(SuperAdminState.waiting_forced_channel_title)
async def msg_admin_fch_link(message: Message, state: FSMContext) -> None:
    await state.update_data(fch_title=(message.text or "").strip())
    await state.set_state(SuperAdminState.waiting_forced_channel_link)
    await message.answer("🔗 لینک عضویت کانال (مثلاً <code>https://t.me/MyChannel</code>) را ارسال کنید:", parse_mode="HTML")


@router.message(SuperAdminState.waiting_forced_channel_link)
async def msg_admin_fch_save(
    message: Message,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    link = (message.text or "").strip()
    ch = await repo.add_forced_channel(
        chat_id=data["fch_id"],
        title=data["fch_title"],
        invite_link=link,
    )
    await state.clear()
    await message.answer(
        f"✅ کانال جوین اجباری «<b>{ch.title}</b>» با موفقیت اضافه شد.",
        parse_mode="HTML",
        reply_markup=build_main_reply_kb(True),
    )


# ----------------------------------------------------------------------
# جستجو و مسدودسازی کاربر توسط ادمین کل
# ----------------------------------------------------------------------
@router.callback_query(F.data == "adm_usr")
async def cb_admin_prompt_user_search(
    callback: CallbackQuery,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    await state.set_state(SuperAdminState.waiting_user_search)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "👤 آیدی عددی یا یوزرنیم (@) کاربر مورد نظر را ارسال کنید:",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(SuperAdminState.waiting_user_search)
async def msg_admin_show_user(
    message: Message,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    u = await repo.find_user_by_username_or_id(message.text or "")
    await state.clear()
    if not u:
        await message.answer("❌ کاربر یافت نشد.", reply_markup=build_main_reply_kb(True))
        return

    inboxes = await repo.get_user_owned_inboxes(u.id)
    ban_btn_txt = "🔓 رفع مسدودیت کاربر" if u.is_banned else "⛔ مسدود کردن کاربر"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ban_btn_txt, callback_data=f"adm_ubn:{u.id}")]
        ]
    )
    await message.answer(
        f"👤 <b>مشخصات کاربر:</b>\n\n"
        f"🆔 آیدی: <code>{u.id}</code>\n"
        f"📛 نام: <b>{u.first_name}</b> (@{u.username or '—'})\n"
        f"📬 تعداد صندوق‌ها: <b>{len(inboxes)}</b>\n"
        f"🚨 گزارش‌های معتبر علیه کاربر: <b>{u.valid_reports_count}</b>\n"
        f"⛔ وضعیت مسدودیت: <b>{'مسدود 🔴' if u.is_banned else 'آزاد 🟢'}</b>",
        parse_mode="HTML",
        reply_markup=kb,
    )
    await message.answer("منوی اصلی:", reply_markup=build_main_reply_kb(True))


@router.callback_query(F.data.startswith("adm_ubn:"))
async def cb_admin_toggle_user_ban(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    uid = int(callback.data.split(":")[1])
    u = await repo.get_user(uid)
    if not u:
        await callback.answer("کاربر یافت نشد.", show_alert=True)
        return
    if u.is_banned:
        await repo.unban_user(uid)
        await callback.answer("🟢 کاربر رفع مسدودیت شد.", show_alert=True)
    else:
        await repo.ban_user(uid, reason="مسدودسازی توسط مدیریت کل")
        await callback.answer("🔴 کاربر مسدود شد.", show_alert=True)


# ----------------------------------------------------------------------
# تایید یا رد گزارش تخلف توسط ادمین کل (با رمزگشایی داخلی و بن خودکار)
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("adm_rep_val:"))
async def cb_admin_validate_report(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    _, raw_rep_id, raw_valid = callback.data.split(":")
    rep_id = int(raw_rep_id)
    is_valid = raw_valid == "1"

    rep, auto_banned, current_count = await repo.validate_report_and_check_autoban(
        report_id=rep_id,
        admin_id=db_user.id,
        is_valid=is_valid,
        threshold=settings.auto_ban_reports_threshold,
        ban_hours=settings.auto_ban_duration_hours,
    )
    if not rep:
        await callback.answer("این گزارش قبلاً بررسی شده است.", show_alert=True)
        return

    if is_valid:
        ban_note = (
            f"\n⛔ <b>فرستنده به دلیل رسیدن به {current_count} گزارش معتبر، به مدت {settings.auto_ban_duration_hours} ساعت به صورت خودکار بن شد!</b>"
            if auto_banned
            else f"\n⚠️ شمارنده گزارش‌های معتبر فرستنده به <b>{current_count}/{settings.auto_ban_reports_threshold}</b> رسید."
        )
        await callback.answer("✅ گزارش تایید شد.")
        if callback.message:
            await callback.message.edit_text(
                callback.message.html_text + f"\n\n✅ <b>تایید شده توسط ادمین کل</b>{ban_note}",
                parse_mode="HTML",
            )
    else:
        await callback.answer("❌ گزارش رد شد.")
        if callback.message:
            await callback.message.edit_text(
                callback.message.html_text + "\n\n❌ <b>رد شده (بی‌مورد)</b>",
                parse_mode="HTML",
            )
