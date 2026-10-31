"""هندلرهای استارت، تایید قوانین، منوی اصلی، پروفایل، لیست سیاه و تنظیمات حریم خصوصی."""

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.db.models import SenderRevealPreference, User
from app.db.repositories import BotRepository
from app.handlers.states import PrivacyAliasState, WritingMessageState
from app.keyboards.inline import (
    build_blocklist_kb,
    build_main_reply_kb,
    build_privacy_settings_kb,
    build_terms_kb,
    build_writing_cancel_kb,
)
from app.locales.fa import (
    BTN_BLOCKLIST,
    BTN_CANCEL_WRITING,
    BTN_HELP,
    BTN_MY_PROFILE,
    BTN_PRIVACY_SETTINGS,
    FORCE_JOIN_STILL_MISSING,
    HELP_TEXT,
    TERMS_TEXT,
    WELCOME_MAIN_TEXT,
)
from app.utils.validators import sanitize_title

router = Router(name="user_panel")


async def _handle_deep_link_payload(
    message: Message,
    payload: str,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    await state.clear()
    if payload.startswith("inv_"):
        inbox = await repo.consume_admin_invite(payload, db_user.id)
        if not inbox:
            await message.answer(
                "❌ لینک دعوت ادمین نامعتبر است، قبلاً استفاده شده یا منقضی شده است.",
                reply_markup=build_main_reply_kb(is_super_admin),
            )
            return
        await message.answer(
            f"🎉 تبریک! شما با موفقیت به عنوان ادمین صندوق «<b>{inbox.title}</b>» اضافه شدید.\n"
            "از این پس پیام‌های دریافتی این صندوق بر اساس سطح دسترسی شما برایتان ارسال خواهد شد.",
            parse_mode="HTML",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    inbox = await repo.get_inbox_by_token_or_slug(payload)
    if not inbox:
        await message.answer(
            "❌ این لینک صندوق ناشناس معتبر نیست یا توسط صاحب آن ریست/حذف شده است.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    if not inbox.is_active:
        await message.answer(
            "⏸ این صندوق پیام ناشناس در حال حاضر توسط صاحب آن غیرفعال شده است.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    sender_hash = repo.crypto.compute_sender_hash(inbox.id, db_user.id)
    if await repo.is_sender_blocked(inbox.id, sender_hash):
        await message.answer(
            "⛔ امکان ارسال پیام به این صندوق برای شما مسدود شده است.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    await state.set_state(WritingMessageState.waiting_anonymous_message)
    await state.update_data(target_inbox_id=inbox.id, reply_to_record_id=None)
    await message.answer(
        f"📬 در حال ارسال پیام ناشناس به صندوق: <b>{inbox.title}</b>\n\n"
        f"💬 {inbox.welcome_text}\n\n"
        "<i>(می‌توانید متن، عکس، ویدیو، ویس، فایل، گیف یا استیکر ارسال کنید. برای انصراف از دکمه پایین استفاده کنید)</i>",
        parse_mode="HTML",
        reply_markup=build_writing_cancel_kb(),
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    payload = ""
    if message.text and " " in message.text:
        payload = message.text.split(" ", 1)[1].strip()

    if not db_user.accepted_terms:
        await message.answer(
            TERMS_TEXT,
            parse_mode="HTML",
            reply_markup=build_terms_kb(payload),
        )
        return

    if payload:
        await _handle_deep_link_payload(message, payload, db_user, repo, state, is_super_admin)
        return

    await state.clear()
    await message.answer(
        WELCOME_MAIN_TEXT.format(name=db_user.first_name),
        parse_mode="HTML",
        reply_markup=build_main_reply_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("terms_ok:"))
async def cb_accept_terms(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    await repo.accept_terms(db_user.id)
    db_user.accepted_terms = True
    payload = callback.data.split(":", 1)[1] if callback.data else ""
    await callback.answer("✅ قوانین تایید شد.")
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass
        if payload:
            await _handle_deep_link_payload(
                callback.message, payload, db_user, repo, state, is_super_admin
            )
        else:
            await callback.message.answer(
                WELCOME_MAIN_TEXT.format(name=db_user.first_name),
                parse_mode="HTML",
                reply_markup=build_main_reply_kb(is_super_admin),
            )


@router.callback_query(F.data.startswith("chk_join:"))
async def cb_check_forced_join(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    forced_channels = await repo.list_forced_channels(only_active=True)
    for ch in forced_channels:
        try:
            member = await bot.get_chat_member(ch.chat_id, db_user.id)
            if member.status in ("left", "kicked"):
                await callback.answer(FORCE_JOIN_STILL_MISSING, show_alert=True)
                return
        except Exception:
            continue

    payload = callback.data.split(":", 1)[1] if callback.data else ""
    await callback.answer("✅ عضویت شما تایید شد!")
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass
        if not db_user.accepted_terms:
            await callback.message.answer(
                TERMS_TEXT,
                parse_mode="HTML",
                reply_markup=build_terms_kb(payload),
            )
            return
        if payload:
            await _handle_deep_link_payload(
                callback.message, payload, db_user, repo, state, is_super_admin
            )
        else:
            await callback.message.answer(
                WELCOME_MAIN_TEXT.format(name=db_user.first_name),
                parse_mode="HTML",
                reply_markup=build_main_reply_kb(is_super_admin),
            )


@router.message(F.text == BTN_CANCEL_WRITING)
@router.message(Command("cancel"))
async def cmd_cancel_writing(
    message: Message,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    await state.clear()
    await message.answer(
        "✅ عملیات لغو شد و به منوی اصلی بازگشتید.",
        reply_markup=build_main_reply_kb(is_super_admin),
    )


@router.message(F.text == BTN_HELP)
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.message(F.text == BTN_MY_PROFILE)
async def show_my_profile(message: Message, db_user: User, repo: BotRepository) -> None:
    owned = await repo.get_user_owned_inboxes(db_user.id)
    admin_of = await repo.get_user_admin_inboxes(db_user.id)
    total_recv = sum(ib.messages_count for ib in owned)
    total_repl = sum(ib.replies_count for ib in owned)

    text = (
        f"👤 <b>پروفایل و آمار شما در ربات</b>\n\n"
        f"🆔 شناسه تلگرام: <code>{db_user.id}</code>\n"
        f"🎭 نام مستعار عمومی: <b>{db_user.alias_name or 'تنظیم نشده'}</b>\n"
        f"📬 تعداد صندوق‌های متعلق به شما: <b>{len(owned)}</b>\n"
        f"👮 صندوق‌هایی که در آن‌ها ادمین هستید: <b>{len(admin_of)}</b>\n"
        f"📩 مجموع پیام‌های دریافتی در صندوق‌های شما: <b>{total_recv}</b>\n"
        f"↩️ مجموع پاسخ‌های ثبت‌شده: <b>{total_repl}</b>\n"
        f"🪪 دریافت درخواست نمایش هویت: <b>{'فعال ✅' if db_user.allow_reveal_global else 'غیرفعال ❌'}</b>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(F.text == BTN_BLOCKLIST)
async def show_blocklist(message: Message, db_user: User, repo: BotRepository) -> None:
    blocks = await repo.list_user_blocks(db_user.id)
    if not blocks:
        await message.answer("✅ لیست سیاه صندوق‌های شما خالی است.")
        return
    await message.answer(
        "🚫 <b>لیست فرستنده‌های مسدودشده در صندوق‌های شما:</b>\n"
        "برای رفع مسدودیت هر کد، روی دکمه مربوط به آن بزنید:",
        parse_mode="HTML",
        reply_markup=build_blocklist_kb(blocks),
    )


@router.callback_query(F.data.startswith("blk_rm:"))
async def cb_unblock_from_list(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    block_id = int(callback.data.split(":")[1])
    rec = await repo.unblock_sender_by_id(block_id, db_user.id)
    if not rec:
        await callback.answer("این مورد قبلاً رفع مسدودیت شده است.", show_alert=True)
        return
    await callback.answer("✅ فرستنده با موفقیت رفع مسدودیت شد.")
    blocks = await repo.list_user_blocks(db_user.id)
    if callback.message:
        if not blocks:
            await callback.message.edit_text("✅ لیست سیاه صندوق‌های شما خالی است.")
        else:
            await callback.message.edit_reply_markup(reply_markup=build_blocklist_kb(blocks))


@router.message(F.text == BTN_PRIVACY_SETTINGS)
async def show_privacy_settings(message: Message, db_user: User, repo: BotRepository) -> None:
    prefs = await repo.list_sender_reveal_prefs(db_user.id)
    text = (
        "🔐 <b>تنظیمات حریم خصوصی و درخواست نمایش هویت</b>\n\n"
        "• شما می‌توانید دریافت «درخواست نمایش هویت» را به طور کلی یا برای یک صندوق خاص خاموش/روشن کنید.\n"
        "• همچنین می‌توانید یک نام مستعار تنظیم کنید."
    )
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_privacy_settings_kb(db_user, prefs),
    )


@router.callback_query(F.data == "prv_glob_tg")
async def cb_toggle_global_reveal(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    new_val = await repo.toggle_global_reveal(db_user.id)
    db_user.allow_reveal_global = new_val
    prefs = await repo.list_sender_reveal_prefs(db_user.id)
    await callback.answer(
        "✅ دریافت درخواست هویت روشن شد." if new_val else "🔕 دریافت درخواست هویت خاموش شد."
    )
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=build_privacy_settings_kb(db_user, prefs)
        )


@router.callback_query(F.data.startswith("prv_ib_tg:"))
async def cb_toggle_inbox_reveal_pref(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    pref_id = int(callback.data.split(":")[1])
    res = await repo.session.execute(
        select(SenderRevealPreference).where(SenderRevealPreference.id == pref_id)
    )
    pref = res.scalar_one_or_none()
    if pref:
        pref.allow_requests = not pref.allow_requests
        await repo.session.commit()
    prefs = await repo.list_sender_reveal_prefs(db_user.id)
    await callback.answer("✅ تنظیمات این صندوق بروزرسانی شد.")
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=build_privacy_settings_kb(db_user, prefs)
        )


@router.callback_query(F.data == "prv_alias")
async def cb_prompt_alias(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PrivacyAliasState.waiting_alias)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🎭 نام مستعار دلخواه خود را ارسال کنید (یا برای حذف نام مستعار، کلمه <code>حذف</code> را بفرستید):",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(PrivacyAliasState.waiting_alias)
async def msg_save_alias(
    message: Message,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    raw = (message.text or "").strip()
    if raw in ("حذف", "پاک", "none", "-"):
        await repo.set_user_alias(db_user.id, None)
        await state.clear()
        await message.answer(
            "✅ نام مستعار شما حذف شد.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return
    cleaned = sanitize_title(raw, max_len=32)
    if len(cleaned) < 2:
        await message.answer("⚠️ نام مستعار باید حداقل ۲ کاراکتر باشد.")
        return
    await repo.set_user_alias(db_user.id, cleaned)
    await state.clear()
    await message.answer(
        f"✅ نام مستعار شما به «<b>{cleaned}</b>» تغییر یافت.",
        parse_mode="HTML",
        reply_markup=build_main_reply_kb(is_super_admin),
    )
