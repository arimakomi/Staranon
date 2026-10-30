"""هندلرهای شروع (/start با لینک صندوق یا دعوت ادمین)، قوانین ۱۸+، پروفایل، لیست سیاه و تنظیمات حریم خصوصی."""

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db.models import User
from app.db.repositories import BotRepository
from app.handlers.states import MessagingSG, UserProfileSG
from app.keyboards.inline import (
    blacklist_manage_kb,
    cancel_kb,
    main_menu_kb,
    terms_accept_kb,
    user_privacy_settings_kb,
)
from app.locales.fa import MEDIA_LABELS, Texts
from app.utils.validators import sanitize_title

router = Router(name="user_panel")


async def enter_inbox_writing_mode(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    identifier: str,
    parent_message_id: int | None = None,
) -> None:
    """ورود فرستنده به حالت نوشتن پیام ناشناس برای یک صندوق مشخص."""
    inbox = await repo.get_inbox_by_token_or_slug(identifier)
    if not inbox:
        await message.answer(Texts.INBOX_NOT_FOUND, parse_mode="HTML")
        return
    if not inbox.is_active:
        await message.answer(Texts.INBOX_DISABLED, parse_mode="HTML")
        return

    s_hash = repo.crypto.compute_sender_hash(inbox.id, db_user.id)
    if await repo.is_sender_blocked(inbox.id, s_hash):
        await message.answer(Texts.SENDER_BLOCKED_IN_INBOX, parse_mode="HTML")
        return

    allowed_list = [
        label for key, label in MEDIA_LABELS.items() if (inbox.allowed_media or {}).get(key, True)
    ]
    media_str = "، ".join(allowed_list) if allowed_list else "هیچ‌کدام"

    await state.set_state(MessagingSG.writing_to_inbox)
    await state.update_data(
        target_inbox_id=inbox.id,
        parent_message_id=parent_message_id,
    )
    await message.answer(
        Texts.WRITING_MODE_HEADER.format(
            title=inbox.title,
            welcome=inbox.welcome_text,
            media=media_str,
        ),
        parse_mode="HTML",
        reply_markup=cancel_kb(),
    )


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    await state.clear()
    payload = (command.args or "").strip()

    # اگر کاربر هنوز قوانین و شرط سنی ۱۸+ را تایید نکرده است، ابتدا قوانین نمایش داده شود
    if not db_user.accepted_terms:
        if payload:
            await state.update_data(pending_start_payload=payload)
        await message.answer(
            Texts.TERMS_PROMPT,
            parse_mode="HTML",
            reply_markup=terms_accept_kb(),
        )
        return

    if payload:
        if payload.startswith("inv_"):
            inv_token = payload[4:]
            joined_inbox = await repo.consume_admin_invite(inv_token, db_user.id)
            if joined_inbox:
                await message.answer(
                    f"🎉 شما با موفقیت به عنوان <b>ادمین صندوق «{joined_inbox.title}»</b> اضافه شدید!",
                    parse_mode="HTML",
                    reply_markup=main_menu_kb(is_super_admin),
                )
            else:
                await message.answer(
                    "❌ لینک دعوت ادمین نامعتبر است یا قبلاً استفاده شده است.",
                    reply_markup=main_menu_kb(is_super_admin),
                )
            return

        # لینک صندوق (ib_<token> یا <slug>)
        await enter_inbox_writing_mode(message, state, repo, db_user, payload)
        return

    await message.answer(
        Texts.MAIN_MENU,
        parse_mode="HTML",
        reply_markup=main_menu_kb(is_super_admin),
    )


@router.callback_query(F.data == "accept_terms")
async def cb_accept_terms(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    await repo.accept_terms(db_user.id)
    data = await state.get_data()
    pending_payload = data.get("pending_start_payload")
    await state.clear()

    if callback.message:
        await callback.message.edit_text(Texts.TERMS_ACCEPTED, parse_mode="HTML")
        if pending_payload:
            if pending_payload.startswith("inv_"):
                joined_inbox = await repo.consume_admin_invite(pending_payload[4:], db_user.id)
                if joined_inbox:
                    await callback.message.answer(
                        f"🎉 شما به عنوان ادمین صندوق «{joined_inbox.title}» اضافه شدید!",
                        reply_markup=main_menu_kb(is_super_admin),
                    )
                    await callback.answer()
                    return
            await enter_inbox_writing_mode(
                callback.message, state, repo, db_user, pending_payload
            )
            await callback.answer()
            return

        await callback.message.answer(
            Texts.MAIN_MENU,
            parse_mode="HTML",
            reply_markup=main_menu_kb(is_super_admin),
        )
    await callback.answer()


@router.callback_query(F.data == "check_forced_join")
async def cb_check_forced_join(
    callback: CallbackQuery,
    is_super_admin: bool,
) -> None:
    # اگر از میدل‌ور عبور کرده یعنی عضو تمام کانال‌ها شده است
    if callback.message:
        await callback.message.delete()
        await callback.message.answer(
            "✅ عضویت شما تایید شد! اکنون می‌توانید از ربات استفاده کنید.",
            reply_markup=main_menu_kb(is_super_admin),
        )
    await callback.answer()


@router.message(F.text == Texts.BTN_CANCEL)
async def handle_cancel(
    message: Message,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    await state.clear()
    await message.answer(
        "عملیات لغو شد. به منوی اصلی بازگشتید:",
        reply_markup=main_menu_kb(is_super_admin),
    )


@router.message(F.text == Texts.BTN_PROFILE)
async def show_user_profile(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    await state.clear()
    inboxes = await repo.list_user_accessible_inboxes(db_user.id)
    owned_count = sum(1 for _, is_own in inboxes if is_own)
    admin_count = sum(1 for _, is_own in inboxes if not is_own)
    alias_display = db_user.alias_name or "تنظیم نشده"
    rev_status = "فعال 🟢" if db_user.allow_reveal_global else "غیرفعال 🔴"

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ تغییر نام مستعار", callback_data="prof_set_alias")]
        ]
    )
    text = (
        "👤 <b>پروفایل کاربری شما</b>\n\n"
        f"• نام تلگرام: <b>{db_user.first_name}</b>\n"
        f"• نام مستعار در ربات: <b>{alias_display}</b>\n"
        f"• تعداد صندوق‌های متعلق به شما: <b>{owned_count}</b>\n"
        f"• تعداد صندوق‌هایی که در آن‌ها ادمین هستید: <b>{admin_count}</b>\n"
        f"• دریافت درخواست نمایش هویت: <b>{rev_status}</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "prof_set_alias")
async def cb_prof_set_alias(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserProfileSG.waiting_alias)
    if callback.message:
        await callback.message.answer(
            "✏️ لطفاً <b>نام مستعار</b> دلخواه خود را وارد کنید (حداکثر ۳۲ کاراکتر):",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(UserProfileSG.waiting_alias, F.text)
async def process_new_alias(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    alias = sanitize_title(message.text or "", max_len=32)
    if not alias:
        await message.answer("❌ نام مستعار معتبر نیست. دوباره تلاش کنید:")
        return
    await repo.set_user_alias(db_user.id, alias)
    await state.clear()
    await message.answer(
        f"✅ نام مستعار شما به «<b>{alias}</b>» تغییر یافت.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(is_super_admin),
    )


@router.message(F.text == Texts.BTN_BLACKLIST)
async def show_blacklist(
    message: Message,
    repo: BotRepository,
    db_user: User,
) -> None:
    blocks = await repo.list_user_blocks(db_user.id)
    if not blocks:
        await message.answer("🚫 لیست سیاه صندوق‌های شما خالی است.")
        return
    await message.answer(
        "🚫 <b>لیست سیاه (فرستندگان مسدودشده در صندوق‌های شما):</b>\n"
        "برای رفع مسدودیت هر مورد، روی دکمه مربوط به آن بزنید:",
        parse_mode="HTML",
        reply_markup=blacklist_manage_kb(blocks),
    )


@router.callback_query(F.data.startswith("unblock_id:"))
async def cb_unblock_sender(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    block_id = int((callback.data or "0").split(":")[1])
    ok = await repo.unblock_sender_by_id(block_id, db_user.id)
    if not ok:
        await callback.answer("❌ خطا در رفع بلاک یا عدم دسترسی.", show_alert=True)
        return
    await callback.answer("✅ فرستنده از لیست سیاه خارج شد.")
    blocks = await repo.list_user_blocks(db_user.id)
    if callback.message:
        if not blocks:
            await callback.message.edit_text("✅ لیست سیاه شما اکنون خالی است.")
        else:
            await callback.message.edit_reply_markup(reply_markup=blacklist_manage_kb(blocks))


@router.message(F.text == Texts.BTN_PRIVACY_SETTINGS)
async def show_privacy_settings(
    message: Message,
    repo: BotRepository,
    db_user: User,
) -> None:
    inboxes = await repo.list_user_accessible_inboxes(db_user.id)
    kb = user_privacy_settings_kb(db_user.allow_reveal_global, inboxes)
    await message.answer(
        "🔐 <b>تنظیمات حریم خصوصی و دریافت پیام</b>\n\n"
        "• می‌توانید دریافت «درخواست نمایش هویت» را به طور کلی برای خودتان ببندید.\n"
        "• همچنین می‌توانید هر یک از صندوق‌های خود را موقتاً روشن یا خاموش کنید:",
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "priv_toggle_reveal")
async def cb_priv_toggle_reveal(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    new_val = await repo.toggle_global_reveal(db_user.id)
    inboxes = await repo.list_user_accessible_inboxes(db_user.id)
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=user_privacy_settings_kb(new_val, inboxes)
        )
    await callback.answer("✅ تنظیم درخواست هویت به‌روزرسانی شد.")


@router.callback_query(F.data.startswith("priv_toggle_ib:"))
async def cb_priv_toggle_inbox(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    has_perm = await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings")
    if not has_perm:
        await callback.answer("❌ شما دسترسی تغییر وضعیت این صندوق را ندارید.", show_alert=True)
        return
    inbox = await repo.get_inbox_by_id(inbox_id)
    if inbox:
        inbox.is_active = not inbox.is_active
        await repo.session.commit()
    inboxes = await repo.list_user_accessible_inboxes(db_user.id)
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=user_privacy_settings_kb(db_user.allow_reveal_global, inboxes)
        )
    await callback.answer("✅ وضعیت صندوق تغییر کرد.")


@router.message(Command("help"))
@router.message(F.text == Texts.BTN_HELP)
async def show_help(message: Message, settings: Settings) -> None:
    text = (
        "📖 <b>راهنمای جامع ربات صندوق پیام ناشناس چندگانه</b>\n\n"
        "۱. <b>صندوق‌های مستقل چندگانه:</b> شما می‌توانید بی‌نهایت صندوق ناشناس شخصی، کانالی یا گروهی بسازید و برای هر کدام لینک، کد کوتاه (Slug)، متن خوش‌آمد و فیلتر رسانه مجزا تعریف کنید.\n\n"
        "۲. <b>تیم ادمین‌های صندوق:</b> برای هر صندوق می‌توانید ادمین اضافه کنید و ۸ سطح دسترسی مختلف (دریافت پیام، پاسخ، ریاکشن، بلاک، درخواست هویت، تاریخچه، تنظیمات، مدیریت ادمین) به آن‌ها بدهید.\n\n"
        "۳. <b>حریم خصوصی و رمزنگاری:</b> هویت فرستنده با الگوریتم‌های HMAC-SHA256 و Fernet محافظت می‌شود. هیچ‌کس (حتی ادمین کل ربات) بدون رضایت مستقیم خود فرستنده قادر به دیدن هویت او نیست.\n\n"
        f"۴. <b>حذف پیام ارسالی:</b> فرستنده تا {settings.delete_window_seconds // 60} دقیقه پس از ارسال پیام می‌تواند آن را از صندوق گیرنده پاک کند."
    )
    await message.answer(text, parse_mode="HTML")
