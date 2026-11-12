"""هندلرهای پیشرفته پنل مدیریت کل ربات (/admin): آمار، همگانی، جوین اجباری، کنترل ۳۶۰ درجه کاربر و صندوق‌ها، ۵۰ پیام آخر و جستجوی سراسری."""

import asyncio
import html
from typing import List, Optional, Tuple
from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import settings
from app.db.models import BroadcastJob, Inbox, MessageRecord, User
from app.db.repositories import BotRepository
from app.handlers.states import SuperAdminState
from app.keyboards.inline import (
    build_admin_all_inboxes_kb,
    build_admin_inbox_control_kb,
    build_admin_recent_users_kb,
    build_admin_user_control_kb,
    build_admin_user_inboxes_kb,
    build_main_reply_kb,
    build_report_review_kb,
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
    INBOX_TYPE_LABELS,
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


async def _send_chunked_messages_list(
    target_message: Message,
    header: str,
    results: List[Tuple[MessageRecord, Inbox, Optional[User], Optional[User], int]],
) -> None:
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
        del_tag = " 🗑[حذف‌شده]" if msg_rec.is_deleted else ""

        item_str = (
            f"\n<b>{idx}. پیام</b> <code>#{msg_rec.id}</code>{del_tag} | 🕒 <code>{ts}</code> | {media_lbl}\n"
            f"👤 <b>فرستنده:</b> {s_name} ({s_uname}) | آیدی: <code>{s_id_str}</code>\n"
            f"🎯 <b>گیرنده:</b> {r_name} ({r_uname}) | آیدی: <code>{r_id}</code> | صندوق: <b>{inbox_title}</b> (<code>#{msg_rec.inbox_id}</code>)\n"
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
        await target_message.answer(chunk, parse_mode="HTML")


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

        ctrl_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"👤 پنل فرستنده ({s_id_str})",
                        callback_data=f"adm_uview:{sender_uid}",
                    )
                    if sender_uid
                    else InlineKeyboardButton(text="—", callback_data="noop"),
                    InlineKeyboardButton(
                        text=f"🎯 پنل گیرنده ({r_id})",
                        callback_data=f"adm_uview:{r_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=f"📬 کنترل صندوق #{msg_rec.inbox_id}",
                        callback_data=f"adm_ib_ctrl:{msg_rec.inbox_id}",
                    )
                ],
            ]
        )

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
        await message.answer(info_card, parse_mode="HTML", reply_markup=ctrl_kb)

        if sender_uid and msg_rec.sender_tg_msg_id:
            await safe_copy_message(
                bot=bot,
                target_chat_id=message.chat.id,
                from_chat_id=sender_uid,
                message_id=msg_rec.sender_tg_msg_id,
            )


async def _format_admin_user_card(user: User, repo: BotRepository) -> tuple[str, InlineKeyboardMarkup]:
    inboxes = await repo.get_user_owned_inboxes(user.id)
    total_recv = sum(ib.messages_count for ib in inboxes)
    total_sent = await repo.get_user_sent_messages_count(user.id)
    uname_str = f"@{user.username}" if user.username else "ندارد"
    ban_str = (
        f"مسدود 🔴 (علت: {user.ban_reason or 'نامشخص'})"
        if user.is_banned
        else "آزاد و فعال 🟢"
    )
    created_str = user.created_at.strftime("%Y-%m-%d %H:%M")
    active_str = user.last_active_at.strftime("%Y-%m-%d %H:%M")

    text = (
        f"👤 <b>پنل کنترل ۳۶۰ درجه کاربر</b>\n"
        f"────────────────────\n"
        f"🆔 آیدی عددی: <code>{user.id}</code>\n"
        f"📛 نام: <a href='tg://user?id={user.id}'>{html.escape(user.first_name or 'کاربر')}</a>\n"
        f"🔗 یوزرنیم: {uname_str}\n"
        f"🎭 نام مستعار: <b>{html.escape(user.alias_name or 'تنظیم نشده')}</b>\n"
        f"📅 تاریخ عضویت: <code>{created_str}</code>\n"
        f"🕒 آخرین فعالیت: <code>{active_str}</code>\n"
        f"────────────────────\n"
        f"📬 تعداد صندوق‌های کاربر: <b>{len(inboxes)}</b>\n"
        f"📥 کل پیام‌های دریافتی در صندوق‌هاش: <b>{total_recv}</b>\n"
        f"📤 کل پیام‌های ارسالی به دیگران: <b>{total_sent}</b>\n"
        f"🚨 گزارش‌های تخلف ثبت‌شده علیه او: <b>{user.valid_reports_count}</b>\n"
        f"🛡 وضعیت حساب: <b>{ban_str}</b>\n\n"
        f"👇 از دکمه‌های زیر برای کنترل کامل این کاربر و صندوق‌هایش استفاده کنید:"
    )
    kb = build_admin_user_control_kb(user, len(inboxes))
    return text, kb


async def _format_admin_inbox_card(
    inbox: Inbox,
    bot: Bot,
    repo: BotRepository,
) -> tuple[str, InlineKeyboardMarkup]:
    me = await bot.get_me()
    bot_username = me.username or "Bot"
    owner = await repo.get_user(inbox.owner_id)
    owner_name = html.escape(owner.first_name) if owner else "نامشخص"
    owner_uname = f"@{owner.username}" if (owner and owner.username) else "—"
    type_str = INBOX_TYPE_LABELS.get(inbox.type, "👤 شخصی")
    st_str = "🟢 فعال (روشن)" if inbox.is_active else "🔴 قطع‌شده (غیرفعال)"
    link = f"https://t.me/{bot_username}?start=ib_{inbox.token}"
    slug_line = (
        f"🔖 لینک کوتاه: <code>https://t.me/{bot_username}?start={inbox.custom_slug}</code>\n"
        if inbox.custom_slug
        else ""
    )
    text = (
        f"📬 <b>کنترل مدیریتی صندوق <code>#{inbox.id}</code></b>\n"
        f"────────────────────\n"
        f"🏷 عنوان صندوق: <b>{html.escape(inbox.title)}</b>\n"
        f"📌 نوع صندوق: <b>{type_str}</b> | وضعیت: <b>{st_str}</b>\n"
        f"👑 صاحب صندوق: <a href='tg://user?id={inbox.owner_id}'>{owner_name}</a> ({owner_uname}) | <code>{inbox.owner_id}</code>\n"
        f"📩 تعداد پیام‌های دریافتی: <b>{inbox.messages_count}</b> | ↩️ پاسخ‌ها: <b>{inbox.replies_count}</b>\n"
        f"🔗 لینک صندوق:\n<code>{link}</code>\n"
        f"{slug_line}"
        f"💬 متن خوش‌آمد:\n<i>{html.escape(inbox.welcome_text[:300])}</i>"
    )
    return text, build_admin_inbox_control_kb(inbox)


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
        "🛠 <b>پنل مدیریت کل پیشرفته «بات ناشناس» (Super Admin Pro)</b>\n\n"
        "💡 <i>نکته: هر زمان پیامی را مستقیماً در چت ربات بفرستید یا فوروارد کنید، ربات خودکار آن را در تمام صندوق‌ها جستجو کرده و مشخصات فرستنده و گیرنده را نمایش می‌دهد.</i>\n\n"
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
        f"📩 <b>لیست {len(results)} پیام آخر رد و بدل شده در کل ربات:</b>\n"
        "<i>(برای دریافت کپی اصل مدیا/پیام یا باز کردن پنل فرستنده و گیرنده، شماره پیام مثلاً <code>#12</code> را در چت بفرستید)</i>\n"
    )
    await _send_chunked_messages_list(callback.message, header, results)


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


# ----------------------------------------------------------------------
# کنترل ۳۶۰ درجه کاربر، صندوق‌های کاربر، ۵۰ پیام صندوق و پیام‌های ارسالی کاربر
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
            "👤 <b>جستجو و کنترل کامل کاربر</b>\n\n"
            "لطفاً <b>آیدی عددی</b>، <b>یوزرنیم (@)</b> کاربر را بفرستید و یا یک پیام از او به اینجا فوروارد کنید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(SuperAdminState.waiting_user_search)
async def msg_admin_show_user(
    message: Message,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    u: Optional[User] = None
    if message.forward_from and not message.forward_from.is_bot:
        u = await repo.upsert_user(
            message.forward_from.id,
            message.forward_from.first_name or "کاربر",
            message.forward_from.username,
        )
    elif message.text:
        u = await repo.find_user_by_username_or_id(message.text)

    await state.clear()
    if not u:
        await message.answer(
            "❌ کاربری با این مشخصات در دیتابیس ربات یافت نشد.",
            reply_markup=build_main_reply_kb(True),
        )
        return

    card_text, kb = await _format_admin_user_card(u, repo)
    await message.answer(card_text, parse_mode="HTML", reply_markup=kb)
    await message.answer("منوی اصلی:", reply_markup=build_main_reply_kb(True))


@router.callback_query(F.data.startswith("adm_uview:"))
async def cb_admin_view_user_panel(
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
    card_text, kb = await _format_admin_user_card(u, repo)
    await callback.answer()
    if callback.message:
        try:
            await callback.message.edit_text(card_text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(card_text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm_uib_list:"))
async def cb_admin_list_user_inboxes(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    owner_id = int(callback.data.split(":")[1])
    u = await repo.get_user(owner_id)
    inboxes = await repo.get_user_owned_inboxes(owner_id)
    await callback.answer()
    if not callback.message:
        return
    uname = html.escape(u.first_name) if u else str(owner_id)
    text = (
        f"📬 <b>لیست تمام صندوق‌های کاربر «{uname}» (<code>{owner_id}</code>):</b>\n\n"
        "روی هر صندوق بزنید تا بتوانید **۵۰ پیام آخر آن را ببینید**، **صندوق را قطع/وصل کنید**، **لینکش را ریست کنید** یا **حذفش نمایید**:"
    )
    kb = build_admin_user_inboxes_kb(inboxes, owner_id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm_ib_ctrl:"))
async def cb_admin_control_inbox(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        await callback.answer("این صندوق یافت نشد یا حذف شده است.", show_alert=True)
        return
    text, kb = await _format_admin_inbox_card(inbox, bot, repo)
    await callback.answer()
    if callback.message:
        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await callback.message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm_ib_tg:"))
async def cb_admin_toggle_inbox(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    inbox_id = int(callback.data.split(":")[1])
    new_st = await repo.toggle_inbox_active(inbox_id, db_user.id)
    if new_st is None:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return
    await callback.answer(
        "🟢 صندوق با موفقیت فعال (وصل) شد." if new_st else "🔴 صندوق با موفقیت قطع (غیرفعال) شد.",
        show_alert=True,
    )
    inbox = await repo.get_inbox_by_id(inbox_id)
    if inbox and callback.message:
        text, kb = await _format_admin_inbox_card(inbox, bot, repo)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm_ib_rst:"))
async def cb_admin_reset_inbox_token(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    inbox_id = int(callback.data.split(":")[1])
    new_tok = await repo.reset_inbox_token(inbox_id, db_user.id)
    if not new_tok:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return
    await callback.answer("🔄 لینک صندوق ریست شد و لینک قبلی باطل گردید.", show_alert=True)
    inbox = await repo.get_inbox_by_id(inbox_id)
    if inbox and callback.message:
        text, kb = await _format_admin_inbox_card(inbox, bot, repo)
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm_ib_del:"))
async def cb_admin_delete_inbox(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    inbox_id = int(callback.data.split(":")[1])
    owner_id = await repo.admin_delete_inbox(inbox_id)
    if owner_id is None:
        await callback.answer("این صندوق قبلاً حذف شده است.", show_alert=True)
        return
    await callback.answer("🗑 صندوق به طور کامل حذف شد!", show_alert=True)
    callback.data = f"adm_uib_list:{owner_id}"
    await cb_admin_list_user_inboxes(callback, repo, is_super_admin)


@router.callback_query(F.data.startswith("adm_ib_msgs:"))
async def cb_admin_inbox_last_50_messages(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return
    await callback.answer("⏳ در حال دریافت ۵۰ پیام آخر این صندوق...")
    if not callback.message:
        return

    results = await repo.get_inbox_messages_for_admin(inbox_id, limit=50)
    if not results:
        await callback.message.answer(f"📭 هنوز هیچ پیامی در صندوق «<b>{html.escape(inbox.title)}</b>» ثبت نشده است.", parse_mode="HTML")
        return

    header = (
        f"📥 <b>{len(results)} پیام آخر صندوق «{html.escape(inbox.title)}» (<code>#{inbox.id}</code>):</b>\n"
        "<i>(برای دریافت اصل مدیا، شماره پیام مثلاً <code>#12</code> را در چت بفرستید)</i>\n"
    )
    await _send_chunked_messages_list(callback.message, header, results)


@router.callback_query(F.data.startswith("adm_usent:"))
async def cb_admin_user_sent_messages(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    uid = int(callback.data.split(":")[1])
    u = await repo.get_user(uid)
    await callback.answer("⏳ در حال واکشی پیام‌های ارسالی این کاربر...")
    if not callback.message:
        return

    results = await repo.get_user_sent_messages_for_admin(uid, limit=50)
    uname = html.escape(u.first_name) if u else str(uid)
    if not results:
        await callback.message.answer(
            f"📭 کاربر «<b>{uname}</b>» (<code>{uid}</code>) تاکنون هیچ پیامی به هیچ صندوقی ارسال نکرده است.",
            parse_mode="HTML",
        )
        return

    header = (
        f"📤 <b>{len(results)} پیام آخر ارسال‌شده توسط «{uname}» (<code>{uid}</code>) به سایر صندوق‌ها:</b>\n"
        "<i>(برای دریافت کپی اصل مدیا/پیام، شماره پیام مثلاً <code>#12</code> را در چت بفرستید)</i>\n"
    )
    await _send_chunked_messages_list(callback.message, header, results)


@router.callback_query(F.data.startswith("adm_urecv:"))
async def cb_admin_user_received_messages(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    uid = int(callback.data.split(":")[1])
    u = await repo.get_user(uid)
    await callback.answer("⏳ در حال واکشی پیام‌های دریافتی این کاربر...")
    if not callback.message:
        return

    results = await repo.get_user_received_messages_for_admin(uid, limit=50)
    uname = html.escape(u.first_name) if u else str(uid)
    if not results:
        await callback.message.answer(
            f"📭 هنوز هیچ پیامی در صندوق‌های کاربر «<b>{uname}</b>» (<code>{uid}</code>) دریافت نشده است.",
            parse_mode="HTML",
        )
        return

    header = (
        f"📥 <b>{len(results)} پیام آخر دریافت‌شده در تمام صندوق‌های «{uname}» (<code>{uid}</code>):</b>\n"
        "<i>(برای دریافت کپی اصل مدیا/پیام، شماره پیام مثلاً <code>#12</code> را در چت بفرستید)</i>\n"
    )
    await _send_chunked_messages_list(callback.message, header, results)


@router.callback_query(F.data.startswith("adm_udm:"))
async def cb_admin_prompt_direct_message(
    callback: CallbackQuery,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    uid = int(callback.data.split(":")[1])
    await state.set_state(SuperAdminState.waiting_direct_message_to_user)
    await state.update_data(dm_target_uid=uid)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"✉️ <b>ارسال پیام مستقیم از طرف ربات به کاربر <code>{uid}</code></b>\n"
            "پیام خود (متن، عکس، ویس، ویدیو یا فایل) را ارسال کنید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(SuperAdminState.waiting_direct_message_to_user)
async def msg_admin_send_direct_message(
    message: Message,
    bot: Bot,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    data = await state.get_data()
    target_uid = int(data["dm_target_uid"])
    await state.clear()

    try:
        await bot.send_message(
            chat_id=target_uid,
            text="📢 <b>پیام جدید از طرف مدیریت «بات ناشناس»:</b>",
            parse_mode="HTML",
        )
        copied = await safe_copy_message(
            bot=bot,
            target_chat_id=target_uid,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        if copied is None:
            raise RuntimeError("copy_failed")
        await message.answer(
            f"✅ پیام شما با موفقیت برای کاربر <code>{target_uid}</code> ارسال شد.",
            parse_mode="HTML",
            reply_markup=build_main_reply_kb(True),
        )
    except Exception:
        await message.answer(
            f"❌ ارسال پیام به کاربر <code>{target_uid}</code> ناموفق بود (احتمالاً ربات را بلاک کرده است).",
            parse_mode="HTML",
            reply_markup=build_main_reply_kb(True),
        )


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
        u = await repo.unban_user(uid)
        await callback.answer("🟢 کاربر رفع مسدودیت شد.", show_alert=True)
    else:
        u = await repo.ban_user(uid, reason="مسدودسازی توسط مدیریت کل")
        await callback.answer("🔴 کاربر به صورت دائمی مسدود شد.", show_alert=True)
    if u and callback.message:
        card_text, kb = await _format_admin_user_card(u, repo)
        await callback.message.edit_text(card_text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm_ubn24:"))
async def cb_admin_ban_user_24h(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    uid = int(callback.data.split(":")[1])
    u = await repo.ban_user(uid, reason="مسدودسازی موقت ۲۴ ساعته توسط مدیریت کل", duration_hours=24)
    if not u:
        await callback.answer("کاربر یافت نشد.", show_alert=True)
        return
    await callback.answer("⏳ کاربر به مدت ۲۴ ساعت مسدود شد.", show_alert=True)
    if callback.message:
        card_text, kb = await _format_admin_user_card(u, repo)
        await callback.message.edit_text(card_text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm_urst_rep:"))
async def cb_admin_reset_user_reports(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    uid = int(callback.data.split(":")[1])
    u = await repo.admin_reset_user_reports(uid)
    if not u:
        await callback.answer("کاربر یافت نشد.", show_alert=True)
        return
    await callback.answer("🧹 شمارنده گزارش‌های تخلف کاربر صفر شد.", show_alert=True)
    if callback.message:
        card_text, kb = await _format_admin_user_card(u, repo)
        await callback.message.edit_text(card_text, parse_mode="HTML", reply_markup=kb)


# ----------------------------------------------------------------------
# قابلیت‌های مکمل: آخرین کاربران، لیست کل صندوق‌ها و گزارش‌های باز
# ----------------------------------------------------------------------
@router.callback_query(F.data == "adm_users_recent")
async def cb_admin_recent_users(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    users = await repo.admin_list_recent_users(limit=25)
    await callback.answer()
    if not callback.message:
        return
    if not users:
        await callback.message.answer("📭 هنوز کاربری ثبت نشده است.")
        return
    await callback.message.answer(
        "👥 <b>لیست ۲۵ کاربر اخیر فعال در ربات:</b>\n"
        "برای باز کردن پنل کنترل ۳۶۰ درجه هر کاربر، روی نام او بزنید:",
        parse_mode="HTML",
        reply_markup=build_admin_recent_users_kb(users),
    )


@router.callback_query(F.data == "adm_inboxes_all")
async def cb_admin_all_inboxes(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    items = await repo.admin_list_all_inboxes(limit=30)
    await callback.answer()
    if not callback.message:
        return
    if not items:
        await callback.message.answer("📭 هنوز هیچ صندوقی ساخته نشده است.")
        return
    await callback.message.answer(
        "📬 <b>لیست صندوق‌های فعال ربات (به ترتیب بیشترین پیام):</b>\n"
        "برای مشاهده ۵۰ پیام آخر، قطع/وصل کردن یا حذف هر صندوق روی آن بزنید:",
        parse_mode="HTML",
        reply_markup=build_admin_all_inboxes_kb(items),
    )


@router.callback_query(F.data == "adm_reports_open")
async def cb_admin_open_reports(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    reports = await repo.list_open_reports_for_admin(limit=15)
    await callback.answer()
    if not callback.message:
        return
    if not reports:
        await callback.message.answer("✅ هیچ گزارش تخلف بررسی‌نشده‌ای وجود ندارد.")
        return

    for rep, msg, inbox, offender_user, offender_uid in reports:
        off_name = html.escape(offender_user.first_name) if offender_user else "ناشناس"
        off_uname = f"@{offender_user.username}" if (offender_user and offender_user.username) else "—"
        ib_title = html.escape(inbox.title) if inbox else f"#{rep.inbox_id}"
        msg_txt = html.escape(msg.message_text[:250]) if (msg and msg.message_text) else "<i>[رسانه/بدون متن]</i>"
        await callback.message.answer(
            f"🚨 <b>گزارش تخلف باز #{rep.id}</b>\n"
            f"🔢 پیام: <code>#{rep.message_id}</code> | صندوق: <b>{ib_title}</b> (<code>#{rep.inbox_id}</code>)\n"
            f"👤 متخلف (فرستنده): <b>{off_name}</b> ({off_uname}) | آیدی: <code>{offender_uid}</code>\n"
            f"🎯 گزارش‌دهنده: <code>{rep.reported_by}</code>\n"
            f"📝 علت گزارش: <b>{html.escape(rep.reason)}</b>\n"
            f"💬 متن پیام: {msg_txt}",
            parse_mode="HTML",
            reply_markup=build_report_review_kb(rep.id),
        )


# ----------------------------------------------------------------------
# آمار، حالت تعمیرات، همگانی و کانال‌های جوین اجباری
# ----------------------------------------------------------------------
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
