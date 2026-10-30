"""هندلرهای پنل ادمین کل ربات (/admin): آمار زنده، پیام همگانی، جوین اجباری، مدیریت کاربران، بررسی گزارش‌ها و تنظیمات ضد اسپم."""

import asyncio
from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import Settings
from app.db.models import BroadcastJob
from app.db.repositories import BotRepository
from app.handlers.states import SuperAdminSG
from app.keyboards.inline import (
    cancel_kb,
    forced_channels_kb,
    main_menu_kb,
    super_admin_panel_kb,
)
from app.locales.fa import Texts
from app.services.broadcaster import execute_broadcast_job

router = Router(name="super_admin")


async def render_super_admin_home(
    repo: BotRepository, settings: Settings
) -> tuple[str, InlineKeyboardMarkup]:
    maint = (
        await repo.get_setting("maintenance_mode", "true" if settings.maintenance_mode else "false")
    ).lower() == "true"
    flinks = (
        await repo.get_setting("filter_links", "true" if settings.filter_links_default else "false")
    ).lower() == "true"
    rl = await repo.get_setting("rate_limit_per_minute", str(settings.rate_limit_per_minute))
    stats = await repo.get_live_stats()

    text = (
        "🛠 <b>پنل مدیریت کل ربات (Super Admin)</b>\n\n"
        f"👥 کل کاربران: <b>{stats['total_users']}</b> نفر\n"
        f"🟢 فعال ۲۴ ساعت گذشته: <b>{stats['active_24h']}</b> نفر\n"
        f"📬 کل صندوق‌های ساخته‌شده: <b>{stats['total_inboxes']}</b>\n"
        f"✉️ پیام‌های ۲۴ ساعت گذشته: <b>{stats['messages_today']}</b>\n"
        f"⚠️ گزارش‌های باز بررسی‌نشده: <b>{stats['open_reports']}</b>\n"
        f"⛔️ کاربران مسدود: <b>{stats['banned_users']}</b>\n"
        f"⏱ محدودیت نرخ فعلی: <b>{rl} درخواست/دقیقه</b>"
    )
    return text, super_admin_panel_kb(maint, flinks)


@router.message(Command("admin"))
@router.message(F.text == Texts.BTN_ADMIN_PANEL)
async def cmd_super_admin(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    await state.clear()
    text, kb = await render_super_admin_home(repo, settings)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "sa_home")
async def cb_sa_home(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    await state.clear()
    text, kb = await render_super_admin_home(repo, settings)
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "sa_stats")
async def cb_sa_stats(
    callback: CallbackQuery,
    repo: BotRepository,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    text, kb = await render_super_admin_home(repo, settings)
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("📊 آمار زنده به‌روزرسانی شد.")


@router.callback_query(F.data == "sa_toggle_maint")
async def cb_sa_toggle_maintenance(
    callback: CallbackQuery,
    repo: BotRepository,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    cur = (
        await repo.get_setting("maintenance_mode", "true" if settings.maintenance_mode else "false")
    ).lower() == "true"
    new_val = "false" if cur else "true"
    await repo.set_setting("maintenance_mode", new_val)
    text, kb = await render_super_admin_home(repo, settings)
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("✅ وضعیت تعمیرات تغییر کرد.")


@router.callback_query(F.data == "sa_toggle_links")
async def cb_sa_toggle_links(
    callback: CallbackQuery,
    repo: BotRepository,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    cur = (
        await repo.get_setting("filter_links", "true" if settings.filter_links_default else "false")
    ).lower() == "true"
    new_val = "false" if cur else "true"
    await repo.set_setting("filter_links", new_val)
    text, kb = await render_super_admin_home(repo, settings)
    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer("✅ فیلتر لینک/آیدی تغییر کرد.")


# ------------------------------------------------------------------
# بررسی گزارش‌های تخلف و اعمال بن خودکار با رمزگشایی داخلی
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("adm_rep_val:"))
async def cb_validate_report(
    callback: CallbackQuery,
    repo: BotRepository,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    if not is_super_admin or not callback.from_user:
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    parts = (callback.data or "").split(":")
    report_id = int(parts[1])
    is_valid = parts[2] == "1"

    thresh_str = await repo.get_setting(
        "auto_ban_threshold", str(settings.auto_ban_reports_threshold)
    )
    threshold = int(thresh_str) if thresh_str.isdigit() else settings.auto_ban_reports_threshold

    rep, auto_banned, cur_count = await repo.validate_report_and_check_autoban(
        report_id=report_id,
        admin_id=callback.from_user.id,
        is_valid=is_valid,
        threshold=threshold,
        ban_hours=settings.auto_ban_duration_hours,
    )
    if not rep:
        await callback.answer("این گزارش قبلاً بررسی شده است.", show_alert=True)
        return

    if is_valid:
        ban_msg = (
            f"⛔️ فرستنده متخلف به علت رسیدن به {cur_count} گزارش معتبر به صورت خودکار به مدت {settings.auto_ban_duration_hours} ساعت مسدود شد!"
            if auto_banned
            else f"✅ گزارش تایید شد (تعداد گزارش‌های معتبر این فرستنده: {cur_count}/{threshold})."
        )
        await callback.answer(ban_msg, show_alert=True)
    else:
        await callback.answer("❌ گزارش رد شد.", show_alert=True)

    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


# ------------------------------------------------------------------
# پیام همگانی (Broadcast) با صف و نمایش پیشرفت
# ------------------------------------------------------------------
@router.callback_query(F.data == "sa_broadcast")
async def cb_sa_broadcast_ask(
    callback: CallbackQuery,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    await state.set_state(SuperAdminSG.waiting_broadcast_content)
    if callback.message:
        await callback.message.answer(
            "📣 <b>ارسال پیام همگانی به تمام کاربران</b>\n\n"
            "لطفاً پیام مورد نظر خود (متن، عکس، ویدیو یا پست فورواردی) را ارسال کنید:",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(SuperAdminSG.waiting_broadcast_content)
async def process_broadcast_message(
    message: Message,
    state: FSMContext,
    bot: Bot,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin or not message.from_user:
        return
    await state.clear()

    job = BroadcastJob(
        admin_id=message.from_user.id,
        mode="copy",
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
        status="running",
    )
    repo.session.add(job)
    await repo.session.commit()
    await repo.session.refresh(job)

    status_msg = await message.answer(
        f"⏳ <b>صف ارسال همگانی (#{job.id}) آغاز شد...</b>",
        parse_mode="HTML",
        reply_markup=main_menu_kb(True),
    )

    async def _run_and_notify() -> None:
        final_job = await execute_broadcast_job(bot, job.id)
        if final_job:
            try:
                await status_msg.edit_text(
                    f"✅ <b>ارسال همگانی (#{final_job.id}) به پایان رسید!</b>\n\n"
                    f"• کل کاربران هدف: <b>{final_job.total_users}</b>\n"
                    f"• ارسال موفق: <b>{final_job.sent_count}</b>\n"
                    f"• بلاک‌کنندگان ربات: <b>{final_job.blocked_count}</b>\n"
                    f"• خطاهای دیگر: <b>{final_job.failed_count}</b>",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    asyncio.create_task(_run_and_notify())


# ------------------------------------------------------------------
# مدیریت کانال‌های جوین اجباری
# ------------------------------------------------------------------
@router.callback_query(F.data == "sa_forced_channels")
async def cb_sa_forced_channels(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    channels = await repo.list_forced_channels(only_active=False)
    if callback.message:
        await callback.message.edit_text(
            "📢 <b>مدیریت کانال‌های جوین اجباری:</b>\n"
            "ربات باید در تمام کانال‌های زیر ادمین باشد تا بتواند عضویت کاربران را بررسی کند:",
            parse_mode="HTML",
            reply_markup=forced_channels_kb(channels),
        )
    await callback.answer()


@router.callback_query(F.data == "sa_fch_add")
async def cb_sa_add_forced_channel_ask(
    callback: CallbackQuery,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    await state.set_state(SuperAdminSG.waiting_forced_channel)
    if callback.message:
        await callback.message.answer(
            "➕ لطفاً اطلاعات کانال جوین اجباری را در ۳ خط به فرمت زیر بفرستید:\n\n"
            "<code>@channel_username_or_id\nعنوان کانال\nhttps://t.me/channel_invite_link</code>",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(SuperAdminSG.waiting_forced_channel, F.text)
async def process_add_forced_channel(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    lines = [ln.strip() for ln in (message.text or "").strip().splitlines() if ln.strip()]
    if len(lines) < 3:
        await message.answer("❌ لطفاً دقیقاً ۳ خط (آیدی کانال، عنوان، لینک عضویت) وارد کنید:")
        return
    chat_id, title, invite_link = lines[0], lines[1], lines[2]
    await repo.add_forced_channel(chat_id, title, invite_link)
    await state.clear()
    await message.answer(
        f"✅ کانال «<b>{title}</b>» به لیست جوین اجباری اضافه شد.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(True),
    )


@router.callback_query(F.data.startswith("sa_fch_del:"))
async def cb_sa_del_forced_channel(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    ch_id = int((callback.data or "0").split(":")[1])
    await repo.remove_forced_channel(ch_id)
    await callback.answer("🗑 کانال از جوین اجباری حذف شد.")
    await cb_sa_forced_channels(callback, repo, is_super_admin)


# ------------------------------------------------------------------
# جستجو و مدیریت کاربر توسط ادمین کل (بن/رفع بن و مشاهده صندوق‌ها)
# ------------------------------------------------------------------
@router.callback_query(F.data == "sa_user_manage")
async def cb_sa_user_search_ask(
    callback: CallbackQuery,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    await state.set_state(SuperAdminSG.waiting_user_search)
    if callback.message:
        await callback.message.answer(
            "🔍 لطفاً <b>آیدی عددی</b> یا <b>یوزرنیم</b> کاربر مورد نظر را بفرستید:",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(SuperAdminSG.waiting_user_search, F.text)
async def process_sa_user_search(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    u = await repo.find_user_by_username_or_id(message.text or "")
    if not u:
        await message.answer("❌ کاربری با این مشخصات یافت نشد.")
        return
    await state.clear()
    inboxes = await repo.list_user_accessible_inboxes(u.id)
    owned = [ib for ib, is_own in inboxes if is_own]
    ib_lines = "\n".join([f"  • #{ib.id}: {ib.title} ({ib.type})" for ib in owned]) or "  ندارد"
    ban_st = "مسدود ⛔️" if u.is_banned else "آزاد 🟢"
    ban_btn_txt = "🔓 رفع مسدودیت کاربر" if u.is_banned else "⛔️ مسدود کردن کاربر"
    ban_cb = f"sa_uban:{u.id}" if u.is_banned else f"sa_ban:{u.id}"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=ban_btn_txt, callback_data=ban_cb)],
            [InlineKeyboardButton(text="🔙 بازگشت به پنل مدیریت", callback_data="sa_home")],
        ]
    )
    await message.answer(
        f"👤 <b>مشخصات کاربر:</b>\n\n"
        f"• آیدی عددی: <code>{u.id}</code>\n"
        f"• نام: <b>{u.first_name}</b>\n"
        f"• یوزرنیم: @{u.username or 'ندارد'}\n"
        f"• وضعیت حساب: <b>{ban_st}</b>\n"
        f"• گزارش‌های معتبر ثبت‌شده علیه او: <b>{u.valid_reports_count}</b>\n\n"
        f"📬 <b>صندوق‌های متعلق به این کاربر:</b>\n{ib_lines}",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("sa_ban:"))
async def cb_sa_ban_user(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    uid = int((callback.data or "0").split(":")[1])
    await repo.ban_user(uid, reason="مسدودسازی توسط مدیریت کل")
    await callback.answer("⛔️ کاربر مسدود شد.", show_alert=True)
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔓 رفع مسدودیت کاربر", callback_data=f"sa_uban:{uid}")],
                    [InlineKeyboardButton(text="🔙 بازگشت به پنل مدیریت", callback_data="sa_home")],
                ]
            )
        )


@router.callback_query(F.data.startswith("sa_uban:"))
async def cb_sa_unban_user(
    callback: CallbackQuery,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    uid = int((callback.data or "0").split(":")[1])
    await repo.unban_user(uid)
    await callback.answer("🟢 مسدودیت کاربر رفع شد.", show_alert=True)
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⛔️ مسدود کردن کاربر", callback_data=f"sa_ban:{uid}")],
                    [InlineKeyboardButton(text="🔙 بازگشت به پنل مدیریت", callback_data="sa_home")],
                ]
            )
        )


@router.callback_query(F.data == "sa_antispam_cfg")
async def cb_sa_antispam_cfg(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    rl = await repo.get_setting("rate_limit_per_minute", str(settings.rate_limit_per_minute))
    th = await repo.get_setting("auto_ban_threshold", str(settings.auto_ban_reports_threshold))
    await state.set_state(SuperAdminSG.waiting_antispam_input)
    if callback.message:
        await callback.message.answer(
            "⏱ <b>تنظیم محدودیت نرخ (Rate Limit) و سقف بن خودکار</b>\n\n"
            f"• محدودیت فعلی در دقیقه: <code>{rl}</code>\n"
            f"• سقف گزارش معتبر برای بن خودکار: <code>{th}</code>\n\n"
            "لطفاً دو عدد جدید را با فاصله وارد کنید (مثال: <code>20 3</code>):",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(SuperAdminSG.waiting_antispam_input, F.text)
async def process_sa_antispam_input(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    is_super_admin: bool,
) -> None:
    if not is_super_admin:
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        await message.answer("❌ لطفاً دقیقاً دو عدد صحیح با فاصله بفرستید (مثلاً: 15 3):")
        return
    await repo.set_setting("rate_limit_per_minute", parts[0])
    await repo.set_setting("auto_ban_threshold", parts[1])
    await state.clear()
    await message.answer(
        f"✅ محدودیت نرخ به {parts[0]} پیام/دقیقه و سقف بن خودکار به {parts[1]} گزارش معتبر تغییر یافت.",
        reply_markup=main_menu_kb(True),
    )
