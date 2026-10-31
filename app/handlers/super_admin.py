"""هندلرهای پنل ادمین کل ربات (/admin): آمار، همگانی، جوین اجباری، مدیریت کاربر، جستجوی سراسری پیام‌ها و تایید گزارش تخلف."""

import asyncio
import html
from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
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
from app.locales.fa import (
    BTN_BLOCKLIST,
    BTN_CANCEL_WRITING,
    BTN_CREATE_INBOX,
    BTN_HELP,
    BTN_MY_INBOXES,
    BTN_MY_PERSONAL_LINK,
    BTN_MY_PROFILE,
    BTN_PRIVACY_SETTINGS,
    BTN_SUPER_ADMIN,
    MEDIA_FILTER_LABELS,
)
from app.services.broadcaster import execute_broadcast_job
from app.services.telegram_helpers import safe_copy_message

router = Router(name="super_admin")

MAIN_MENU_BUTTONS = {
    BTN_MY_PERSONAL_LINK,
    BTN_MY_INBOXES,
    BTN_CREATE_INBOX,
    BTN_MY_PROFILE,
    BTN_BLOCKLIST,
    BTN_PRIVACY_SETTINGS,
    BTN_HELP,
    BTN_SUPER_ADMIN,
    BTN_CANCEL_WRITING,
}


async def _send_global_message_search_results(
    message: Message,
    bot: Bot,
    repo: BotRepository,
    query: str,
) -> None:
    results = await repo.search_messages_global(query, limit=10)
    if not results:
        await message.answer(
            f"🔍 هیچ پیامی منطبق با «<code>{html.escape(query[:80])}</code>» در هیچ‌کدام از صندوق‌ها یافت نشد.",
            parse_mode="HTML",
            reply_markup=build_main_reply_kb(True),
        )
        return

    await message.answer(
        f"🔍 <b>نتایج جستجو در تمام صندوق‌ها ({len(results)} مورد یافت شد):</b>",
        parse_mode="HTML",
        reply_markup=build_main_reply_kb(True),
    )

    for msg_rec, inbox, owner_user, sender_user, sender_uid in results:
        if sender_user:
            s_name = html.escape(sender_user.first_name or "بدون نام")
            s_uname = f"@{sender_user.username}" if sender_user.username else "بدون یوزرنیم"
        else:
            s_name = "کاربر ناشناس"
            s_uname = "—"

        s_id_str = str(sender_uid) if sender_uid else "نامشخص"
        s_link = f"<a href='tg://user?id={sender_uid}'>{s_name}</a>" if sender_uid else s_name

        if owner_user:
            r_name = html.escape(owner_user.first_name or "بدون نام")
            r_uname = f"@{owner_user.username}" if owner_user.username else "بدون یوزرنیم"
            r_id = owner_user.id
        else:
            r_name = "نامشخص"
            r_uname = "—"
            r_id = inbox.owner_id if inbox else 0

        r_link = f"<a href='tg://user?id={r_id}'>{r_name}</a>" if r_id else r_name
        inbox_title = html.escape(inbox.title) if inbox else f"صندوق #{msg_rec.inbox_id}"
        media_lbl = MEDIA_FILTER_LABELS.get(msg_rec.media_type, msg_rec.media_type)
        msg_txt = html.escape(msg_rec.message_text) if msg_rec.message_text else "<i>(بدون متن / فایل رسانه‌ای)</i>"
        ts = msg_rec.created_at.strftime("%Y-%m-%d %H:%M:%S")
        del_badge = " (حذف‌شده از صندوق 🗑)" if msg_rec.is_deleted else ""

        info_card = (
            f"📄 <b>مشخصات پیام <code>#{msg_rec.id}</code>{del_badge}</b>\n"
            f"🕒 زمان ارسال: <code>{ts}</code> | نوع: <b>{media_lbl}</b>\n"
            f"────────────────────\n"
            f"👤 <b>فرستنده پیام:</b>\n"
            f"• نام: {s_link}\n"
            f"• یوزرنیم: {s_uname}\n"
            f"• آیدی عددی فرستنده: <code>{s_id_str}</code>\n"
            f"────────────────────\n"
            f"🎯 <b>گیرنده (صاحب صندوق):</b>\n"
            f"• نام: {r_link}\n"
            f"• یوزرنیم: {r_uname}\n"
            f"• آیدی عددی گیرنده: <code>{r_id}</code>\n"
            f"• صندوق: <b>{inbox_title}</b> (<code>#{msg_rec.inbox_id}</code>)\n"
            f"────────────────────\n"
            f"💬 <b>متن پیام:</b>\n{msg_txt}"
        )
        await message.answer(info_card, parse_mode="HTML")

        if sender_uid and msg_rec.sender_tg_msg_id:
            await safe_copy_message(
                bot=bot,
                target_chat_id=message.chat.id,
                from_chat_id=sender_uid,
                message_id=msg_rec.sender_tg_msg_id,
            )


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
        "🛠 <b>پنل مدیریت کل «بات ناشناس» (Super Admin)</b>\n\n"
        "💡 <i>نکته: شما هر زمان پیامی را مستقیماً برای ربات بفرستید یا فوروارد کنید، ربات به صورت خودکار آن را در تمام صندوق‌ها جستجو کرده و مشخصات فرستنده و گیرنده را نمایش می‌دهد.</i>\n\n"
        "یکی از بخش‌های مدیریتی زیر را انتخاب کنید:",
        parse_mode="HTML",
        reply_markup=build_super_admin_kb(maint_active),
    )


@router.callback_query(F.data == "adm_last50")
async def cb_admin_last_50_messages(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await callback.answer("⏳ در حال دریافت ۵۰ پیام آخر...")
    if not callback.message:
        return

    results = await repo.get_recent_messages_global(limit=50)
    if not results:
        await callback.message.answer("📭 هنوز هیچ پیامی در ربات رد و بدل نشده است.")
        return

    header = (
        f"📩 <b>لیست {len(results)} پیام آخر رد و بدل شده در ربات:</b>\n"
        "<i>(برای دریافت کپی اصل مدیا/پیام هر مورد، کافی است شماره آن مثلاً <code>#12</code> را در چت بفرستید)</i>\n"
    )
    chunks: list[str] = []
    current_chunk = header

    for idx, (msg_rec, inbox, owner_user, sender_user, sender_uid) in enumerate(results, start=1):
        s_name = html.escape(sender_user.first_name or "بدون نام") if sender_user else "ناشناس"
        s_uname = f"@{sender_user.username}" if (sender_user and sender_user.username) else "—"
        s_id_str = str(sender_uid) if sender_uid else "نامشخص"

        r_name = html.escape(owner_user.first_name or "بدون نام") if owner_user else "نامشخص"
        r_uname = f"@{owner_user.username}" if (owner_user and owner_user.username) else "—"
        r_id = owner_user.id if owner_user else (inbox.owner_id if inbox else 0)

        inbox_title = html.escape(inbox.title) if inbox else f"صندوق #{msg_rec.inbox_id}"
        media_lbl = MEDIA_FILTER_LABELS.get(msg_rec.media_type, msg_rec.media_type)
        raw_txt = (msg_rec.message_text or "").strip()
        if len(raw_txt) > 160:
            raw_txt = raw_txt[:160] + "..."
        msg_txt = html.escape(raw_txt) if raw_txt else f"<i>[{media_lbl} بدون متن]</i>"
        ts = msg_rec.created_at.strftime("%m-%d %H:%M")

        item_str = (
            f"\n<b>{idx}. پیام</b> <code>#{msg_rec.id}</code> | 🕒 <code>{ts}</code> | {media_lbl}\n"
            f"👤 <b>فرستنده:</b> {s_name} ({s_uname}) | آیدی: <code>{s_id_str}</code>\n"
            f"🎯 <b>گیرنده:</b> {r_name} ({r_uname}) | آیدی: <code>{r_id}</code> | صندوق: <b>{inbox_title}</b>\n"
            f"💬 <b>پیام:</b> {msg_txt}\n"
            f"────────────────────"
        )

        if len(current_chunk) + len(item_str) > 3800:
            chunks.append(current_chunk)
            current_chunk = item_str.lstrip("\n")
        else:
            current_chunk += item_str

    if current_chunk:
        chunks.append(current_chunk)

    for chunk in chunks:
        await callback.message.answer(chunk, parse_mode="HTML")


@router.callback_query(F.data == "adm_msg_search")
async def cb_admin_prompt_message_search(
    callback: CallbackQuery,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await state.set_state(SuperAdminState.waiting_message_search)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🔍 <b>جستجوی سراسری پیام در تمام صندوق‌ها</b>\n\n"
            "• <b>متن یا بخشی از متن پیام</b>، یا <b>شماره پیام</b> (مثلاً <code>#12</code> یا <code>12</code>) را بفرستید تا فرستنده (با آیدی)، گیرنده (با آیدی) و اصل پیام نمایش داده شود:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(Command("search"))
async def cmd_admin_search_message(
    message: Message,
    bot: Bot,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    parts = (message.text or "").split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        await state.set_state(SuperAdminState.waiting_message_search)
        await message.answer(
            "🔍 متن پیام یا شماره پیام (مثلاً <code>#12</code>) را برای جستجو در تمام صندوق‌ها ارسال کنید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )
        return
    await state.clear()
    await _send_global_message_search_results(message, bot, repo, parts[1].strip())


@router.message(SuperAdminState.waiting_message_search)
async def msg_admin_execute_message_search(
    message: Message,
    bot: Bot,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    await state.clear()
    query = (message.text or message.caption or "").strip()
    if not query and message.forward_from_message_id:
        query = str(message.forward_from_message_id)
    if not query:
        await message.answer(
            "⚠️ لطفاً متن پیام یا شماره پیام را ارسال کنید.",
            reply_markup=build_main_reply_kb(True),
        )
        return
    await _send_global_message_search_results(message, bot, repo, query)


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
        "📊 <b>آمار زنده بات ناشناس</b>\n\n"
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


# ----------------------------------------------------------------------
# جستجوی خودکار در تمام صندوق‌ها هنگام ارسال مستقیم پیام توسط مدیریت کل
# ----------------------------------------------------------------------
@router.message(StateFilter(None))
async def msg_super_admin_direct_search_all_inboxes(
    message: Message,
    bot: Bot,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    raw_text = (message.text or message.caption or "").strip()
    if raw_text in MAIN_MENU_BUTTONS or raw_text.startswith("/"):
        return
    if not raw_text and message.forward_from_message_id:
        raw_text = str(message.forward_from_message_id)
    if not raw_text:
        return
    await _send_global_message_search_results(message, bot, repo, raw_text)
