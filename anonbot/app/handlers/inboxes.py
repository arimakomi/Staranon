"""هندلرهای مدیریت صندوق‌های ناشناس، ساخت، ویرایش، ریست لینک، فیلتر مدیا و مدیریت ادمین‌های صندوق."""

from typing import Dict
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db.models import Inbox, User
from app.db.repositories import BotRepository
from app.handlers.states import CreateInboxSG, EditInboxSG
from app.keyboards.inline import (
    admin_permissions_edit_kb,
    cancel_kb,
    confirm_delete_inbox_kb,
    confirm_reset_link_kb,
    inbox_admins_menu_kb,
    inbox_manage_kb,
    inbox_media_filter_kb,
    inbox_type_selection_kb,
    main_menu_kb,
    my_inboxes_list_kb,
)
from app.locales.fa import INBOX_TYPE_LABELS, MEDIA_LABELS, ROLE_PRESETS, Texts
from app.services.telegram_helpers import (
    fetch_real_chat_admin_ids,
    verify_chat_ownership_and_bot_member,
)
from app.utils.validators import is_valid_slug, sanitize_title

router = Router(name="inboxes")


async def get_bot_username(bot: Bot, cfg: Settings) -> str:
    if cfg.bot_username:
        return cfg.bot_username
    me = await bot.get_me()
    cfg.bot_username = me.username or "bot"
    return cfg.bot_username


async def format_inbox_detail_text(
    inbox: Inbox, repo: BotRepository, bot_username: str
) -> str:
    type_label = INBOX_TYPE_LABELS.get(inbox.type, inbox.type)
    status = "فعال 🟢" if inbox.is_active else "غیرفعال 🔴"
    slug_str = f"<code>{inbox.slug}</code>" if inbox.slug else "تنظیم نشده"
    allowed = [
        lbl for k, lbl in MEDIA_LABELS.items() if (inbox.allowed_media or {}).get(k, True)
    ]
    media_str = "، ".join(allowed) if allowed else "هیچ‌کدام"
    admins = await repo.get_inbox_admins(inbox.id)
    link = f"https://t.me/{bot_username}?start=ib_{inbox.token}"
    slug_link = (
        f"\n🔖 لینک کوتاه (Slug):\n<code>https://t.me/{bot_username}?start={inbox.slug}</code>"
        if inbox.slug
        else ""
    )
    return Texts.INBOX_DETAIL.format(
        title=inbox.title,
        type_label=type_label,
        status=status,
        slug=slug_str,
        welcome=inbox.welcome_text,
        media=media_str,
        admin_count=len(admins) + 1,
        link=link,
        slug_link=slug_link,
    )


async def get_user_effective_perms(
    repo: BotRepository, inbox: Inbox, user_id: int
) -> tuple[bool, Dict[str, bool]]:
    if inbox.owner_id == user_id:
        return True, ROLE_PRESETS["manager"]
    adm = await repo.get_inbox_admin_record(inbox.id, user_id)
    if not adm:
        return False, {}
    return False, dict(adm.permissions or {})


@router.message(F.text == Texts.BTN_MY_INBOXES)
async def show_my_inboxes(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    await state.clear()
    items = await repo.list_user_accessible_inboxes(db_user.id)
    if not items:
        await message.answer(
            Texts.NO_INBOXES,
            reply_markup=inbox_type_selection_kb(),
        )
        return
    await message.answer(
        "📬 <b>صندوق‌های پیام ناشناس شما:</b>\nبرای مدیریت هر صندوق روی نام آن کلیک کنید:",
        parse_mode="HTML",
        reply_markup=my_inboxes_list_kb(items),
    )


@router.callback_query(F.data == "ib_list")
async def cb_ib_list(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    await state.clear()
    items = await repo.list_user_accessible_inboxes(db_user.id)
    if callback.message:
        if not items:
            await callback.message.edit_text(
                Texts.NO_INBOXES, reply_markup=inbox_type_selection_kb()
            )
        else:
            await callback.message.edit_text(
                "📬 <b>صندوق‌های پیام ناشناس شما:</b>",
                parse_mode="HTML",
                reply_markup=my_inboxes_list_kb(items),
            )
    await callback.answer()


@router.message(F.text == Texts.BTN_CREATE_INBOX)
async def msg_create_inbox_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        Texts.SELECT_INBOX_TYPE,
        parse_mode="HTML",
        reply_markup=inbox_type_selection_kb(),
    )


@router.callback_query(F.data == "ib_new_menu")
async def cb_create_inbox_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if callback.message:
        await callback.message.edit_text(
            Texts.SELECT_INBOX_TYPE,
            parse_mode="HTML",
            reply_markup=inbox_type_selection_kb(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("create_ib_type:"))
async def cb_select_inbox_type(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    ib_type = (callback.data or "").split(":")[1]
    if ib_type not in ("personal", "channel", "group"):
        await callback.answer("نوع صندوق نامعتبر است.", show_alert=True)
        return

    await state.update_data(new_inbox_type=ib_type, target_chat_id=None)
    if ib_type == "personal":
        await state.set_state(CreateInboxSG.waiting_title)
        if callback.message:
            await callback.message.answer(
                Texts.ENTER_INBOX_TITLE,
                parse_mode="HTML",
                reply_markup=cancel_kb(),
            )
    else:
        await state.set_state(CreateInboxSG.waiting_target_chat)
        if callback.message:
            await callback.message.answer(
                Texts.ENTER_TARGET_CHAT,
                parse_mode="HTML",
                reply_markup=cancel_kb(),
            )
    await callback.answer()


@router.message(CreateInboxSG.waiting_target_chat)
async def process_target_chat_verification(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db_user: User,
) -> None:
    data = await state.get_data()
    ib_type = data.get("new_inbox_type", "channel")

    chat_identifier: str | int | None = None
    if message.forward_from_chat:
        chat_identifier = message.forward_from_chat.id
    elif message.text:
        raw = message.text.strip()
        if raw.lstrip("-").isdigit():
            chat_identifier = int(raw)
        else:
            if not raw.startswith("@"):
                raw = "@" + raw.split("/")[-1]
            chat_identifier = raw

    if not chat_identifier:
        await message.answer("❌ لطفاً یوزرنیم، آیدی عددی یا یک پیام فورواردشده از کانال/گروه بفرستید:")
        return

    ok, verified_chat_id, info_or_err = await verify_chat_ownership_and_bot_member(
        bot=bot,
        chat_identifier=chat_identifier,
        user_id=db_user.id,
        expected_type=ib_type,
    )
    if not ok or verified_chat_id is None:
        await message.answer(
            Texts.CHAT_VERIFY_FAILED.format(reason=info_or_err),
            parse_mode="HTML",
        )
        return

    await state.update_data(target_chat_id=verified_chat_id)
    await state.set_state(CreateInboxSG.waiting_title)
    await message.answer(
        f"✅ مالکیت شما و حضور ربات در «<b>{info_or_err}</b>» تایید شد!\n\n{Texts.ENTER_INBOX_TITLE}",
        parse_mode="HTML",
    )


@router.message(CreateInboxSG.waiting_title, F.text)
async def process_inbox_title(message: Message, state: FSMContext) -> None:
    title = sanitize_title(message.text or "", max_len=64)
    if len(title) < 2:
        await message.answer("❌ عنوان صندوق باید حداقل ۲ کاراکتر باشد. مجدداً وارد کنید:")
        return
    await state.update_data(new_inbox_title=title)
    await state.set_state(CreateInboxSG.waiting_welcome)
    await message.answer(Texts.ENTER_WELCOME_TEXT, parse_mode="HTML")


@router.message(CreateInboxSG.waiting_welcome, F.text)
async def process_inbox_welcome(
    message: Message,
    state: FSMContext,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    raw_welcome = (message.text or "").strip()
    if raw_welcome in ("پیشفرض", "پیش‌فرض", "default"):
        welcome_text = Texts.DEFAULT_WELCOME
    else:
        welcome_text = sanitize_title(raw_welcome, max_len=500)

    data = await state.get_data()
    ib_type = data.get("new_inbox_type", "personal")
    title = data.get("new_inbox_title", "صندوق من")
    target_chat_id = data.get("target_chat_id")

    inbox = await repo.create_inbox(
        owner_id=db_user.id,
        inbox_type=ib_type,
        title=title,
        welcome_text=welcome_text,
        target_chat_id=target_chat_id,
    )
    await state.clear()

    bot_username = await get_bot_username(bot, settings)
    link = f"https://t.me/{bot_username}?start=ib_{inbox.token}"
    await message.answer(
        Texts.INBOX_CREATED.format(
            title=inbox.title,
            type_label=INBOX_TYPE_LABELS.get(inbox.type, inbox.type),
            link=link,
        ),
        parse_mode="HTML",
        reply_markup=main_menu_kb(is_super_admin),
    )
    detail_txt = await format_inbox_detail_text(inbox, repo, bot_username)
    await message.answer(
        detail_txt,
        parse_mode="HTML",
        reply_markup=inbox_manage_kb(inbox, True, ROLE_PRESETS["manager"], bot_username),
    )


@router.callback_query(F.data.startswith("ib_view:"))
async def cb_view_inbox(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
) -> None:
    await state.clear()
    inbox_id = int((callback.data or "0").split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return
    is_owner, perms = await get_user_effective_perms(repo, inbox, db_user.id)
    if not is_owner and not perms:
        await callback.answer("❌ شما به این صندوق دسترسی ندارید.", show_alert=True)
        return

    bot_username = await get_bot_username(bot, settings)
    detail_txt = await format_inbox_detail_text(inbox, repo, bot_username)
    if callback.message:
        await callback.message.edit_text(
            detail_txt,
            parse_mode="HTML",
            reply_markup=inbox_manage_kb(inbox, is_owner, perms, bot_username),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ib_toggle:"))
async def cb_toggle_inbox_active(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await callback.answer("❌ عدم دسترسی به تنظیمات صندوق.", show_alert=True)
        return
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return
    inbox.is_active = not inbox.is_active
    await repo.session.commit()
    await repo.add_inbox_log(
        inbox.id, db_user.id, "toggle_active", f"تغییر وضعیت فعال بودن به {inbox.is_active}"
    )

    is_owner, perms = await get_user_effective_perms(repo, inbox, db_user.id)
    bot_username = await get_bot_username(bot, settings)
    detail_txt = await format_inbox_detail_text(inbox, repo, bot_username)
    if callback.message:
        await callback.message.edit_text(
            detail_txt,
            parse_mode="HTML",
            reply_markup=inbox_manage_kb(inbox, is_owner, perms, bot_username),
        )
    await callback.answer("✅ وضعیت صندوق تغییر کرد.")


@router.callback_query(F.data.startswith("ib_media:"))
async def cb_inbox_media_menu(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await callback.answer("❌ عدم دسترسی به تنظیمات صندوق.", show_alert=True)
        return
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(
            f"🎞 <b>فیلتر نوع رسانه‌های مجاز در صندوق «{inbox.title}»:</b>\n"
            "با کلیک روی هر گزینه، دریافت آن نوع رسانه را مجاز یا ممنوع کنید:",
            parse_mode="HTML",
            reply_markup=inbox_media_filter_kb(inbox.id, inbox.allowed_media or {}),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ib_media_tog:"))
async def cb_inbox_media_toggle(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    parts = (callback.data or "").split(":")
    inbox_id = int(parts[1])
    media_key = parts[2]
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    updated_map = await repo.toggle_inbox_media(inbox_id, media_key)
    if updated_map is not None and callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=inbox_media_filter_kb(inbox_id, updated_map)
        )
    await callback.answer("✅ فیلتر رسانه به‌روزرسانی شد.")


@router.callback_query(F.data.startswith("ib_edit_title:"))
async def cb_edit_title_ask(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    await state.set_state(EditInboxSG.waiting_new_title)
    await state.update_data(edit_inbox_id=inbox_id)
    if callback.message:
        await callback.message.answer(
            "✏️ عنوان جدید صندوق را وارد کنید:", reply_markup=cancel_kb()
        )
    await callback.answer()


@router.message(EditInboxSG.waiting_new_title, F.text)
async def process_edit_inbox_title(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data.get("edit_inbox_id", 0))
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await state.clear()
        await message.answer("❌ عدم دسترسی.", reply_markup=main_menu_kb(is_super_admin))
        return
    new_title = sanitize_title(message.text or "", max_len=64)
    if len(new_title) < 2:
        await message.answer("❌ عنوان باید حداقل ۲ کاراکتر باشد:")
        return
    inbox.title = new_title
    await repo.session.commit()
    await repo.add_inbox_log(inbox.id, db_user.id, "edit_title", f"تغییر عنوان به {new_title}")
    await state.clear()
    await message.answer(
        f"✅ عنوان صندوق به «<b>{new_title}</b>» تغییر یافت.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("ib_edit_welcome:"))
async def cb_edit_welcome_ask(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    await state.set_state(EditInboxSG.waiting_new_welcome)
    await state.update_data(edit_inbox_id=inbox_id)
    if callback.message:
        await callback.message.answer(
            "💬 متن خوش‌آمدگویی جدید صندوق را وارد کنید:", reply_markup=cancel_kb()
        )
    await callback.answer()


@router.message(EditInboxSG.waiting_new_welcome, F.text)
async def process_edit_inbox_welcome(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data.get("edit_inbox_id", 0))
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await state.clear()
        await message.answer("❌ عدم دسترسی.", reply_markup=main_menu_kb(is_super_admin))
        return
    inbox.welcome_text = sanitize_title(message.text or "", max_len=500)
    await repo.session.commit()
    await repo.add_inbox_log(inbox.id, db_user.id, "edit_welcome", "ویرایش متن خوش‌آمدگویی")
    await state.clear()
    await message.answer(
        "✅ متن خوش‌آمدگویی صندوق با موفقیت به‌روزرسانی شد.",
        reply_markup=main_menu_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("ib_edit_slug:"))
async def cb_edit_slug_ask(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    await state.set_state(EditInboxSG.waiting_new_slug)
    await state.update_data(edit_inbox_id=inbox_id)
    if callback.message:
        await callback.message.answer(
            "🔖 کد کوتاه دلخواه (Slug) را با حروف انگلیسی و اعداد (۳ تا ۳۲ کاراکتر) وارد کنید.\n"
            "برای حذف کد کوتاه، عبارت <code>حذف</code> را بفرستید:",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(EditInboxSG.waiting_new_slug, F.text)
async def process_edit_inbox_slug(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data.get("edit_inbox_id", 0))
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_settings"):
        await state.clear()
        await message.answer("❌ عدم دسترسی.", reply_markup=main_menu_kb(is_super_admin))
        return

    raw = (message.text or "").strip()
    if raw in ("حذف", "clear", "none"):
        await repo.set_inbox_slug(inbox_id, None)
        await state.clear()
        await message.answer(
            "✅ کد کوتاه صندوق حذف شد.",
            reply_markup=main_menu_kb(is_super_admin),
        )
        return

    if not is_valid_slug(raw):
        await message.answer(
            "❌ فرمت کد کوتاه نامعتبر است. فقط حروف انگلیسی، عدد و زیرخط (۳ تا ۳۲ کاراکتر) مجاز است:"
        )
        return

    ok, msg = await repo.set_inbox_slug(inbox_id, raw)
    if not ok:
        await message.answer(f"❌ {msg}\nلطفاً کد دیگری وارد کنید:")
        return

    await state.clear()
    await message.answer(
        f"✅ {msg}",
        reply_markup=main_menu_kb(is_super_admin),
    )


# ------------------------------------------------------------------
# ریست لینک، حذف صندوق و انتقال مالکیت (فقط مالک)
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("ib_reset_ask:"))
async def cb_reset_link_ask(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or inbox.owner_id != db_user.id:
        await callback.answer("❌ فقط مالک اصلی صندوق می‌تواند لینک را ریست کند.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(
            Texts.RESET_LINK_CONFIRM,
            parse_mode="HTML",
            reply_markup=confirm_reset_link_kb(inbox.id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ib_reset_do:"))
async def cb_reset_link_do(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    new_token = await repo.reset_inbox_token(inbox_id, db_user.id)
    if not new_token:
        await callback.answer("❌ فقط مالک صندوق مجاز به ریست لینک است.", show_alert=True)
        return
    bot_username = await get_bot_username(bot, settings)
    link = f"https://t.me/{bot_username}?start=ib_{new_token}"
    inbox = await repo.get_inbox_by_id(inbox_id)
    if callback.message and inbox:
        await callback.message.edit_text(
            Texts.LINK_RESET_DONE.format(link=link),
            parse_mode="HTML",
            reply_markup=inbox_manage_kb(inbox, True, ROLE_PRESETS["manager"], bot_username),
        )
    await callback.answer("✅ لینک قبلی باطل و لینک جدید ساخته شد!")


@router.callback_query(F.data.startswith("ib_del_ask:"))
async def cb_delete_inbox_ask(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or inbox.owner_id != db_user.id:
        await callback.answer("❌ فقط مالک اصلی صندوق می‌تواند آن را حذف کند.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(
            Texts.DELETE_INBOX_CONFIRM.format(title=inbox.title),
            parse_mode="HTML",
            reply_markup=confirm_delete_inbox_kb(inbox.id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ib_del_do:"))
async def cb_delete_inbox_do(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    ok = await repo.delete_inbox(inbox_id, db_user.id)
    if not ok:
        await callback.answer("❌ فقط مالک اصلی صندوق می‌تواند آن را حذف کند.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text(Texts.INBOX_DELETED)
    await callback.answer()


@router.callback_query(F.data.startswith("ib_transfer:"))
async def cb_transfer_ownership_ask(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or inbox.owner_id != db_user.id:
        await callback.answer("❌ فقط مالک اصلی صندوق می‌تواند مالکیت را منتقل کند.", show_alert=True)
        return
    await state.set_state(EditInboxSG.waiting_transfer_user)
    await state.update_data(edit_inbox_id=inbox_id)
    if callback.message:
        await callback.message.answer(
            "👑 <b>انتقال مالکیت صندوق</b>\n\n"
            "لطفاً آیدی عددی یا یوزرنیم مالک جدید را بفرستید (کاربر مقصد باید قبلاً ربات را استارت زده باشد):",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(EditInboxSG.waiting_transfer_user, F.text)
async def process_transfer_ownership(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data.get("edit_inbox_id", 0))
    target_user = await repo.find_user_by_username_or_id(message.text or "")
    if not target_user:
        await message.answer("❌ کاربر مورد نظر در دیتابیس ربات یافت نشد (ابتدا باید ربات را استارت بزند).")
        return
    if target_user.id == db_user.id:
        await message.answer("❌ شما هم‌اکنون مالک این صندوق هستید.")
        return

    ok = await repo.transfer_inbox_ownership(inbox_id, db_user.id, target_user.id)
    await state.clear()
    if ok:
        await message.answer(
            f"✅ مالکیت صندوق با موفقیت به کاربر <b>{target_user.first_name}</b> منتقل شد.",
            parse_mode="HTML",
            reply_markup=main_menu_kb(is_super_admin),
        )
    else:
        await message.answer(
            "❌ خطا در انتقال مالکیت.",
            reply_markup=main_menu_kb(is_super_admin),
        )


# ------------------------------------------------------------------
# مدیریت ادمین‌های صندوق و لاگ اقدامات
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("ib_admins:"))
async def cb_inbox_admins_menu(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("❌ شما دسترسی مدیریت ادمین‌های این صندوق را ندارید.", show_alert=True)
        return
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return

    admins = await repo.get_inbox_admins(inbox_id)
    users_map: Dict[int, User] = {}
    for adm in admins:
        u = await repo.get_user(adm.user_id)
        if u:
            users_map[adm.user_id] = u

    if callback.message:
        await callback.message.edit_text(
            f"👥 <b>مدیریت ادمین‌های صندوق «{inbox.title}»</b>\n\n"
            "برای ویرایش دسترسی‌های هر ادمین روی نام او بزنید یا ادمین جدیدی اضافه کنید:",
            parse_mode="HTML",
            reply_markup=inbox_admins_menu_kb(inbox, admins, users_map),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ib_adm_add:"))
async def cb_add_admin_prompt(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    await state.set_state(EditInboxSG.waiting_new_admin)
    await state.update_data(edit_inbox_id=inbox_id)
    if callback.message:
        await callback.message.answer(
            "➕ <b>افزودن ادمین به صندوق</b>\n\n"
            "یک پیام از شخص مورد نظر به اینجا فوروارد کنید، یا <b>یوزرنیم / آیدی عددی</b> او را بفرستید:",
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(EditInboxSG.waiting_new_admin)
async def process_add_inbox_admin(
    message: Message,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data.get("edit_inbox_id", 0))
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await state.clear()
        await message.answer("❌ عدم دسترسی.", reply_markup=main_menu_kb(is_super_admin))
        return

    target_user: User | None = None
    if message.forward_from:
        target_user = await repo.upsert_user(
            user_id=message.forward_from.id,
            first_name=message.forward_from.first_name or "کاربر",
            username=message.forward_from.username,
        )
    elif message.text:
        target_user = await repo.find_user_by_username_or_id(message.text)

    if not target_user:
        await message.answer(
            "❌ کاربر یافت نشد (اگر فوروارد شیشه‌ای ایشان بسته است، از لینک دعوت یک‌بارمصرف استفاده کنید یا آیدی عددی او را بفرستید):"
        )
        return
    if target_user.id == inbox.owner_id:
        await message.answer("❌ ایشان مالک اصلی صندوق هستند.")
        return

    await repo.add_or_update_inbox_admin(
        inbox_id=inbox.id,
        user_id=target_user.id,
        added_by=db_user.id,
        preset="responder",
    )
    await state.clear()
    await message.answer(
        f"✅ کاربر <b>{target_user.first_name}</b> به عنوان ادمین (قالب پیش‌فرض: پاسخ‌دهنده) به صندوق اضافه شد.",
        parse_mode="HTML",
        reply_markup=main_menu_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("ib_adm_inv:"))
async def cb_create_admin_invite_link(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    invite = await repo.create_admin_invite(inbox_id, db_user.id, preset="responder")
    bot_username = await get_bot_username(bot, settings)
    inv_link = f"https://t.me/{bot_username}?start=inv_{invite.token}"
    if callback.message:
        await callback.message.answer(
            "🔗 <b>لینک دعوت یک‌بارمصرف ادمین ساخته شد:</b>\n\n"
            f"<code>{inv_link}</code>\n\n"
            "⚠️ این لینک فقط برای یک نفر قابل استفاده است و بلافاصله پس از کلیک باطل می‌شود.",
            parse_mode="HTML",
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ib_adm_sync:"))
async def cb_sync_real_chat_admins(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not inbox.target_chat_id:
        await callback.answer("این صندوق به کانال یا گروهی متصل نیست.", show_alert=True)
        return

    real_admins = await fetch_real_chat_admin_ids(bot, inbox.target_chat_id)
    added_count = 0
    for uid, fname, uname in real_admins:
        if uid == inbox.owner_id:
            continue
        await repo.upsert_user(uid, fname, uname)
        existing = await repo.get_inbox_admin_record(inbox.id, uid)
        if existing is None:
            await repo.add_or_update_inbox_admin(inbox.id, uid, db_user.id, preset="responder")
            added_count += 1

    await callback.answer(f"✅ همگام‌سازی انجام شد ({added_count} ادمین جدید اضافه شد).", show_alert=True)
    await cb_inbox_admins_menu(callback, repo, db_user)


@router.callback_query(F.data.startswith("ib_adm_edit:"))
async def cb_edit_single_admin(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    parts = (callback.data or "").split(":")
    inbox_id = int(parts[1])
    admin_uid = int(parts[2])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    rec = await repo.get_inbox_admin_record(inbox_id, admin_uid)
    if not rec:
        await callback.answer("ادمین یافت نشد.", show_alert=True)
        return
    u = await repo.get_user(admin_uid)
    name = u.first_name if u else str(admin_uid)
    if callback.message:
        await callback.message.edit_text(
            f"🛡 <b>ویرایش دسترسی‌های ادمین «{name}»</b>\n\n"
            "می‌توانید یکی از قالب‌های آماده (ناظر، پاسخ‌دهنده، مدیر کامل) را انتخاب کنید یا هر دسترسی را جداگانه روشن/خاموش نمایید:",
            parse_mode="HTML",
            reply_markup=admin_permissions_edit_kb(inbox_id, admin_uid, rec.permissions or {}),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("ib_adm_preset:"))
async def cb_apply_admin_preset(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    parts = (callback.data or "").split(":")
    inbox_id = int(parts[1])
    admin_uid = int(parts[2])
    preset = parts[3]
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    rec = await repo.add_or_update_inbox_admin(inbox_id, admin_uid, db_user.id, preset=preset)
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=admin_permissions_edit_kb(inbox_id, admin_uid, rec.permissions or {})
        )
    await callback.answer("✅ قالب دسترسی اعمال شد.")


@router.callback_query(F.data.startswith("ib_adm_perm:"))
async def cb_toggle_single_admin_perm(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    parts = (callback.data or "").split(":")
    inbox_id = int(parts[1])
    admin_uid = int(parts[2])
    perm_key = parts[3]
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    new_perms = await repo.toggle_admin_permission(inbox_id, admin_uid, perm_key, db_user.id)
    if new_perms is not None and callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=admin_permissions_edit_kb(inbox_id, admin_uid, new_perms)
        )
    await callback.answer("✅ دسترسی فوراً به‌روزرسانی شد.")


@router.callback_query(F.data.startswith("ib_adm_rm:"))
async def cb_remove_admin(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    parts = (callback.data or "").split(":")
    inbox_id = int(parts[1])
    admin_uid = int(parts[2])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    await repo.remove_inbox_admin(inbox_id, admin_uid, db_user.id)
    await callback.answer("🗑 ادمین از صندوق حذف شد.")
    await cb_inbox_admins_menu(callback, repo, db_user)


@router.callback_query(F.data.startswith("ib_logs:"))
async def cb_view_inbox_logs(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    inbox_id = int((callback.data or "0").split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "view_history"):
        await callback.answer("❌ شما دسترسی مشاهده تاریخچه و لاگ این صندوق را ندارید.", show_alert=True)
        return
    logs = await repo.get_inbox_logs(inbox_id, limit=20)
    if not logs:
        await callback.answer("هنوز لاگی برای این صندوق ثبت نشده است.", show_alert=True)
        return

    lines = ["📜 <b>۲۰ اقدام اخیر ثبت‌شده در لاگ صندوق:</b>\n"]
    for lg in logs:
        ts = lg.created_at.strftime("%m-%d %H:%M")
        lines.append(f"• <code>[{ts}]</code> کاربر <code>{lg.actor_user_id}</code>: {lg.details or lg.action}")

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔙 بازگشت به صندوق", callback_data=f"ib_view:{inbox_id}")]
        ]
    )
    if callback.message:
        await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)
    await callback.answer()
