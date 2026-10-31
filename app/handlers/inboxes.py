"""هندلرهای مدیریت صندوق‌های ناشناس (ساخت، ویرایش، ریست لینک، فیلتر مدیا، Slug و مدیریت ادمین‌ها)."""

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.db.models import Inbox, User
from app.db.repositories import BotRepository
from app.handlers.states import CreateInboxState, EditInboxState
from app.keyboards.inline import (
    build_admin_permissions_kb,
    build_create_inbox_type_kb,
    build_inbox_admins_kb,
    build_inbox_detail_kb,
    build_inboxes_list_kb,
    build_main_reply_kb,
    build_media_filter_kb,
    build_preset_select_kb,
    build_writing_cancel_kb,
)
from app.locales.fa import (
    BTN_CREATE_INBOX,
    BTN_MY_INBOXES,
    INBOX_TYPE_LABELS,
)
from app.services.telegram_helpers import (
    fetch_real_chat_admin_ids,
    verify_chat_ownership_and_bot_member,
)
from app.utils.validators import is_valid_slug, sanitize_title

router = Router(name="inboxes")


async def _get_bot_username(bot: Bot) -> str:
    me = await bot.get_me()
    return me.username or "Bot"


def _format_inbox_summary(inbox: Inbox, bot_username: str, is_owner: bool) -> str:
    type_str = INBOX_TYPE_LABELS.get(inbox.type, "👤 شخصی")
    status_str = "🟢 فعال" if inbox.is_active else "🔴 غیرفعال"
    main_link = f"https://t.me/{bot_username}?start=ib_{inbox.token}"
    slug_line = (
        f"🔖 لینک کوتاه (Slug): <code>https://t.me/{bot_username}?start={inbox.custom_slug}</code>\n"
        if inbox.custom_slug
        else ""
    )
    role_str = "👑 مالک صندوق" if is_owner else "👮 ادمین صندوق"
    return (
        f"📬 <b>صندوق: {inbox.title}</b>\n\n"
        f"📌 نوع صندوق: <b>{type_str}</b> | نقش شما: <b>{role_str}</b>\n"
        f"📡 وضعیت: <b>{status_str}</b>\n"
        f"📩 پیام‌های دریافتی: <b>{inbox.messages_count}</b> | ↩️ پاسخ‌ها: <b>{inbox.replies_count}</b>\n\n"
        f"🔗 <b>لینک اختصاصی صندوق:</b>\n<code>{main_link}</code>\n"
        f"{slug_line}\n"
        f"💬 <b>پیام خوش‌آمد فعلی:</b>\n<i>{inbox.welcome_text}</i>"
    )


@router.message(F.text == BTN_MY_INBOXES)
async def msg_my_inboxes(message: Message, db_user: User, repo: BotRepository) -> None:
    inboxes = await repo.get_user_manageable_inboxes(db_user.id)
    if not inboxes:
        await message.answer(
            "📭 شما هنوز هیچ صندوق پیام ناشناسی نساخته‌اید.\n"
            "با زدن دکمه زیر اولین صندوق خود را بسازید:",
            reply_markup=build_create_inbox_type_kb(),
        )
        return
    await message.answer(
        "📬 <b>لیست صندوق‌های ناشناس شما (مالک یا ادمین):</b>\n"
        "برای مشاهده لینک، تنظیمات یا مدیریت ادمین‌ها، روی صندوق مورد نظر بزنید:",
        parse_mode="HTML",
        reply_markup=build_inboxes_list_kb(inboxes, db_user.id),
    )


@router.callback_query(F.data == "ib_list")
async def cb_inboxes_list(callback: CallbackQuery, db_user: User, repo: BotRepository) -> None:
    inboxes = await repo.get_user_manageable_inboxes(db_user.id)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "📬 <b>لیست صندوق‌های ناشناس شما:</b>",
            parse_mode="HTML",
            reply_markup=build_inboxes_list_kb(inboxes, db_user.id),
        )


@router.message(F.text == BTN_CREATE_INBOX)
async def msg_create_inbox_menu(message: Message) -> None:
    await message.answer(
        "➕ <b>ساخت صندوق پیام ناشناس جدید</b>\n\n"
        "لطفاً نوع صندوقی که می‌خواهید بسازید را انتخاب کنید:",
        parse_mode="HTML",
        reply_markup=build_create_inbox_type_kb(),
    )


@router.callback_query(F.data == "ib_new_menu")
async def cb_create_inbox_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "➕ <b>ساخت صندوق پیام ناشناس جدید</b>\n\n"
            "لطفاً نوع صندوقی که می‌خواهید بسازید را انتخاب کنید:",
            parse_mode="HTML",
            reply_markup=build_create_inbox_type_kb(),
        )


@router.callback_query(F.data.startswith("ib_mk:"))
async def cb_select_inbox_type(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    ib_type = callback.data.split(":")[1]
    await state.clear()
    await state.update_data(inbox_type=ib_type, target_chat_id=None)
    await callback.answer()

    if not callback.message:
        return

    if ib_type == "personal":
        await state.set_state(CreateInboxState.waiting_title)
        await callback.message.answer(
            "👤 <b>ساخت صندوق شخصی</b>\n\n"
            "یک عنوان برای صندوق خود وارد کنید (مثلاً: «صندوق ناشناس من» یا «پیشنهادات و انتقادات»):",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )
    else:
        await state.set_state(CreateInboxState.waiting_chat_identifier)
        lbl = "کانال" if ib_type == "channel" else "گروه"
        await callback.message.answer(
            f"📢 <b>ساخت صندوق اختصاصی {lbl}</b>\n\n"
            f"۱. ابتدا مطمئن شوید که ربات در {lbl} شما عضو است (برای کانال حتماً ربات باید <b>ادمین</b> باشد) و شما نیز مالک یا ادمین آن هستید.\n"
            f"۲. حالا <b>یوزرنیم {lbl}</b> (مثلاً <code>@MyChannel</code>) یا <b>آیدی عددی آن</b> (مثلاً <code>-100123456789</code>) را بفرستید، یا یک پیام از آن کانال را به اینجا فوروارد کنید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(CreateInboxState.waiting_chat_identifier)
async def msg_verify_channel_or_group(
    message: Message,
    bot: Bot,
    db_user: User,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    ib_type = data.get("inbox_type", "channel")

    chat_identifier: str | int | None = None
    if message.forward_from_chat:
        chat_identifier = message.forward_from_chat.id
    elif message.text:
        raw = message.text.strip()
        if raw.lstrip("-").isdigit():
            chat_identifier = int(raw)
        else:
            chat_identifier = raw if raw.startswith("@") else f"@{raw}"

    if not chat_identifier:
        await message.answer("⚠️ لطفاً یوزرنیم (@)، آیدی عددی چت یا یک پیام فورواردشده از کانال ارسال کنید.")
        return

    ok, verified_chat_id, info_or_err = await verify_chat_ownership_and_bot_member(
        bot=bot,
        chat_identifier=chat_identifier,
        user_id=db_user.id,
        expected_type=ib_type,
    )
    if not ok or verified_chat_id is None:
        await message.answer(f"❌ <b>تایید نشد:</b>\n{info_or_err}", parse_mode="HTML")
        return

    await state.update_data(target_chat_id=verified_chat_id, default_title=info_or_err)
    await state.set_state(CreateInboxState.waiting_title)
    await message.answer(
        f"✅ مالکیت/ادمینی شما در «<b>{info_or_err}</b>» تایید شد!\n\n"
        "حالا عنوان دلخواه برای این صندوق را ارسال کنید (یا کلمه <code>پیشفرض</code> را بفرستید تا همان نام چت ثبت شود):",
        parse_mode="HTML",
    )


@router.message(CreateInboxState.waiting_title)
async def msg_create_inbox_title(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    data = await state.get_data()
    if raw in ("پیشفرض", "پیش‌فرض", "default") and data.get("default_title"):
        title = sanitize_title(data["default_title"])
    else:
        title = sanitize_title(raw)

    if len(title) < 2:
        await message.answer("⚠️ عنوان صندوق باید حداقل ۲ کاراکتر باشد.")
        return

    await state.update_data(title=title)
    await state.set_state(CreateInboxState.waiting_welcome)
    await message.answer(
        "💬 <b>پیام خوش‌آمد صندوق</b> را بنویسید (متنی که فرستنده هنگام باز کردن لینک شما می‌بیند).\n"
        "اگر می‌خواهید متن استاندارد قرار گیرد، کلمه <code>پیشفرض</code> را بفرستید:",
        parse_mode="HTML",
    )


@router.message(CreateInboxState.waiting_welcome)
async def msg_create_inbox_finish(
    message: Message,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    raw_welcome = (message.text or "").strip()
    welcome = None if raw_welcome in ("پیشفرض", "پیش‌فرض", "default") else raw_welcome[:1000]
    data = await state.get_data()
    inbox = await repo.create_inbox(
        owner_id=db_user.id,
        inbox_type=data.get("inbox_type", "personal"),
        title=data.get("title", "صندوق ناشناس"),
        welcome_text=welcome,
        target_chat_id=data.get("target_chat_id"),
    )
    await state.clear()
    bot_username = await _get_bot_username(bot)

    await message.answer(
        "🎉 <b>صندوق پیام ناشناس شما با موفقیت ساخته شد!</b>",
        parse_mode="HTML",
        reply_markup=build_main_reply_kb(is_super_admin),
    )
    await message.answer(
        _format_inbox_summary(inbox, bot_username, is_owner=True),
        parse_mode="HTML",
        reply_markup=build_inbox_detail_kb(
            inbox=inbox,
            bot_username=bot_username,
            can_edit_welcome=True,
            can_manage_admins=True,
            is_owner=True,
        ),
    )


@router.callback_query(F.data.startswith("ib_v:"))
async def cb_view_inbox(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return

    is_owner = inbox.owner_id == db_user.id
    can_edit = await repo.check_inbox_permission(inbox_id, db_user.id, "edit_welcome")
    can_adm = await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins")
    if not is_owner and not await repo.get_inbox_admin_record(inbox_id, db_user.id):
        await callback.answer("شما به این صندوق دسترسی ندارید.", show_alert=True)
        return

    bot_username = await _get_bot_username(bot)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            _format_inbox_summary(inbox, bot_username, is_owner),
            parse_mode="HTML",
            reply_markup=build_inbox_detail_kb(inbox, bot_username, can_edit, can_adm, is_owner),
        )


@router.callback_query(F.data.startswith("ib_tg:"))
async def cb_toggle_inbox_active(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "edit_welcome"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    new_state = await repo.toggle_inbox_active(inbox_id, db_user.id)
    await callback.answer("🟢 صندوق فعال شد." if new_state else "🔴 صندوق غیرفعال شد.")
    await cb_view_inbox(callback, bot, db_user, repo)


@router.callback_query(F.data.startswith("ib_lf:"))
async def cb_toggle_link_filter(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "edit_welcome"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    new_state = await repo.toggle_inbox_link_filter(inbox_id, db_user.id)
    await callback.answer(
        "🔗 فیلتر ضد لینک روشن شد." if new_state else "🔗 فیلتر ضد لینک خاموش شد."
    )
    await cb_view_inbox(callback, bot, db_user, repo)


@router.callback_query(F.data.startswith("ib_rst:"))
async def cb_reset_inbox_link(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or inbox.owner_id != db_user.id:
        await callback.answer("فقط مالک اصلی صندوق می‌تواند لینک را ریست کند.", show_alert=True)
        return
    await repo.reset_inbox_token(inbox_id, db_user.id)
    await callback.answer(
        "🔄 لینک جدید ساخته شد و لینک قبلی فوراً باطل گردید! (بلاک‌ها و تاریخچه حفظ شدند)",
        show_alert=True,
    )
    await cb_view_inbox(callback, bot, db_user, repo)


@router.callback_query(F.data.startswith("ib_mf:"))
async def cb_media_filter_menu(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not await repo.check_inbox_permission(inbox_id, db_user.id, "edit_welcome"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"🎞 <b>فیلتر نوع رسانه مجاز برای صندوق «{inbox.title}»</b>\n"
            "روی هر مورد بزنید تا مجاز یا مسدود شود:",
            parse_mode="HTML",
            reply_markup=build_media_filter_kb(inbox.id, inbox.allowed_media),
        )


@router.callback_query(F.data.startswith("ib_mft:"))
async def cb_toggle_media_item(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_id, media_key = callback.data.split(":")
    inbox_id = int(raw_id)
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "edit_welcome"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    updated = await repo.toggle_inbox_media_filter(inbox_id, media_key, db_user.id)
    await callback.answer("✅ فیلتر بروزرسانی شد.")
    if updated is not None and callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=build_media_filter_kb(inbox_id, updated)
        )


@router.callback_query(F.data.startswith("ib_ew:"))
async def cb_prompt_edit_welcome(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "edit_welcome"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await state.set_state(EditInboxState.waiting_new_welcome)
    await state.update_data(edit_inbox_id=inbox_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "✏️ متن خوش‌آمد جدید را برای این صندوق ارسال کنید:",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(EditInboxState.waiting_new_welcome)
async def msg_save_new_welcome(
    message: Message,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data["edit_inbox_id"])
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("⚠️ متن خوش‌آمد خیلی کوتاه است.")
        return
    await repo.update_inbox_welcome(inbox_id, text, db_user.id)
    await state.clear()
    await message.answer(
        "✅ پیام خوش‌آمد صندوق بروزرسانی شد.",
        reply_markup=build_main_reply_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("ib_et:"))
async def cb_prompt_edit_title(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "edit_welcome"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await state.set_state(EditInboxState.waiting_new_title)
    await state.update_data(edit_inbox_id=inbox_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🏷 عنوان جدید صندوق را ارسال کنید:",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(EditInboxState.waiting_new_title)
async def msg_save_new_title(
    message: Message,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data["edit_inbox_id"])
    title = sanitize_title(message.text or "")
    if len(title) < 2:
        await message.answer("⚠️ عنوان باید حداقل ۲ کاراکتر باشد.")
        return
    await repo.update_inbox_title(inbox_id, title, db_user.id)
    await state.clear()
    await message.answer(
        f"✅ عنوان صندوق به «<b>{title}</b>» تغییر یافت.",
        parse_mode="HTML",
        reply_markup=build_main_reply_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("ib_slg:"))
async def cb_prompt_slug(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or inbox.owner_id != db_user.id:
        await callback.answer("فقط مالک صندوق می‌تواند کد کوتاه تنظیم کند.", show_alert=True)
        return
    await state.set_state(EditInboxState.waiting_custom_slug)
    await state.update_data(edit_inbox_id=inbox_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "🔖 کد کوتاه دلخواه (Slug) را با حروف انگلیسی و اعداد (۳ تا ۳۲ کاراکتر، مثلاً <code>ali_inbox</code>) ارسال کنید.\n"
            "برای حذف کد کوتاه، کلمه <code>حذف</code> را بفرستید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(EditInboxState.waiting_custom_slug)
async def msg_save_slug(
    message: Message,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data["edit_inbox_id"])
    raw = (message.text or "").strip()
    if raw in ("حذف", "none", "-"):
        await repo.set_inbox_slug(inbox_id, None, db_user.id)
        await state.clear()
        await message.answer(
            "✅ کد کوتاه حذف شد.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    if not is_valid_slug(raw):
        await message.answer(
            "⚠️ فرمت کد کوتاه نامعتبر است! فقط حروف انگلیسی، عدد و خط زیر (_) بین ۳ تا ۳۲ کاراکتر مجاز است."
        )
        return

    ok, msg_txt = await repo.set_inbox_slug(inbox_id, raw, db_user.id)
    if not ok:
        await message.answer(f"❌ {msg_txt}")
        return
    await state.clear()
    await message.answer(
        f"✅ {msg_txt}",
        reply_markup=build_main_reply_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("ib_del_c:"))
async def cb_delete_inbox(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    ok = await repo.delete_inbox(inbox_id, db_user.id)
    if not ok:
        await callback.answer("فقط مالک اصلی می‌تواند صندوق را حذف کند.", show_alert=True)
        return
    await callback.answer("🗑 صندوق به طور کامل حذف شد.")
    await cb_inboxes_list(callback, db_user, repo)


@router.callback_query(F.data.startswith("ib_log:"))
async def cb_view_inbox_logs(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return
    logs = await repo.get_inbox_logs(inbox_id, limit=12)
    if not logs:
        await callback.answer("هنوز لاگی ثبت نشده است.", show_alert=True)
        return
    lines = [f"📜 <b>آخرین لاگ‌های مدیریتی صندوق «{inbox.title}»:</b>\n"]
    for lg in logs:
        ts = lg.created_at.strftime("%m-%d %H:%M")
        lines.append(f"• <code>{ts}</code> | کاربر <code>{lg.actor_id}</code>: {lg.details or lg.action}")
    await callback.answer()
    if callback.message:
        await callback.message.answer("\n".join(lines), parse_mode="HTML")


# ----------------------------------------------------------------------
# مدیریت ادمین‌های صندوق (Inbox Admins RBAC)
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("ib_adm:"))
async def cb_inbox_admins_menu(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("شما دسترسی مدیریت ادمین‌های این صندوق را ندارید.", show_alert=True)
        return

    admins = await repo.list_inbox_admins(inbox_id)
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"👥 <b>مدیریت ادمین‌های صندوق «{inbox.title}»</b>\n\n"
            f"👑 مالک اصلی: <code>{inbox.owner_id}</code>\n"
            f"👮 تعداد ادمین‌های فعلی: <b>{len(admins)}</b>\n\n"
            "برای ویرایش دسترسی‌های هر ادمین روی نام او بزنید یا ادمین جدید اضافه کنید:",
            parse_mode="HTML",
            reply_markup=build_inbox_admins_kb(inbox, admins),
        )


@router.callback_query(F.data.startswith("ib_ai:"))
async def cb_admin_invite_choose_preset(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🔗 <b>ساخت لینک دعوت ادمین یک‌بارمصرف</b>\n"
            "لطفاً قالب دسترسی اولیه ادمین دعوت‌شده را انتخاب کنید:",
            parse_mode="HTML",
            reply_markup=build_preset_select_kb(inbox_id, mode="invite"),
        )


@router.callback_query(F.data.startswith("ib_ais:"))
async def cb_create_admin_invite_link(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_id, preset = callback.data.split(":")
    inbox_id = int(raw_id)
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    inv = await repo.create_admin_invite(inbox_id, db_user.id, preset=preset)
    bot_username = await _get_bot_username(bot)
    link = f"https://t.me/{bot_username}?start=inv_{inv.token}"
    await callback.answer("🔗 لینک دعوت ساخته شد.")
    if callback.message:
        await callback.message.answer(
            f"🔗 <b>لینک دعوت یک‌بارمصرف ادمین (اعتبار ۲۴ ساعت):</b>\n\n"
            f"<code>{link}</code>\n\n"
            "<i>این لینک را برای شخص مورد نظر بفرستید. به محض استارت زدن، لینک مصرف و باطل می‌شود.</i>",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("ib_aa:"))
async def cb_prompt_add_admin(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await state.set_state(EditInboxState.waiting_admin_identifier)
    await state.update_data(edit_inbox_id=inbox_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "👮 <b>افزودن ادمین به صندوق</b>\n\n"
            "یک پیام از کاربر مورد نظر فوروارد کنید، یا <b>آیدی عددی / یوزرنیم (@)</b> او را ارسال نمایید\n"
            "<i>(توجه: کاربر باید حداقل یک بار ربات را استارت زده باشد؛ در غیر این صورت از «لینک دعوت یک‌بارمصرف» استفاده کنید)</i>:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(EditInboxState.waiting_admin_identifier)
async def msg_resolve_admin_user(
    message: Message,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data["edit_inbox_id"])

    target_user: User | None = None
    if message.forward_from and not message.forward_from.is_bot:
        target_user = await repo.upsert_user(
            message.forward_from.id,
            message.forward_from.first_name or "ادمین",
            message.forward_from.username,
        )
    elif message.text:
        target_user = await repo.find_user_by_username_or_id(message.text)

    if not target_user:
        await message.answer(
            "❌ کاربر در دیتابیس ربات یافت نشد (احتمالاً هنوز ربات را استارت نزده است).\n"
            "پیشنهاد می‌شود از گزینه «🔗 ساخت لینک دعوت یک‌بارمصرف» استفاده کنید."
        )
        return

    await state.clear()
    await message.answer(
        f"✅ کاربر <b>{target_user.first_name}</b> (<code>{target_user.id}</code>) انتخاب شد.\n"
        "حالا قالب دسترسی او را انتخاب کنید:",
        parse_mode="HTML",
        reply_markup=build_preset_select_kb(inbox_id, mode="direct", target_uid=target_user.id),
    )
    await message.answer(
        "بازگشت به منوی اصلی:",
        reply_markup=build_main_reply_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("ib_aps:"))
async def cb_apply_direct_admin_preset(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_ib, raw_uid, preset = callback.data.split(":")
    inbox_id = int(raw_ib)
    target_uid = int(raw_uid)
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    adm = await repo.add_or_update_inbox_admin(inbox_id, target_uid, db_user.id, preset)
    await callback.answer("✅ ادمین با موفقیت اضافه شد.")
    if callback.message:
        await callback.message.edit_text(
            f"👮 <b>تنظیمات دسترسی ادمین <code>{adm.user_id}</code></b>\n"
            "می‌توانید هر کدام از دسترسی‌های زیر را به صورت تکی روشن یا خاموش کنید:",
            parse_mode="HTML",
            reply_markup=build_admin_permissions_kb(inbox_id, adm),
        )


@router.callback_query(F.data.startswith("ib_av:"))
async def cb_view_single_admin(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_ib, raw_uid = callback.data.split(":")
    inbox_id = int(raw_ib)
    target_uid = int(raw_uid)
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    adm = await repo.get_inbox_admin_record(inbox_id, target_uid)
    if not adm:
        await callback.answer("ادمین یافت نشد.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"👮 <b>سطوح دسترسی ادمین <code>{adm.user_id}</code></b>\n"
            "با کلیک روی هر گزینه، وضعیت آن را تغییر دهید:",
            parse_mode="HTML",
            reply_markup=build_admin_permissions_kb(inbox_id, adm),
        )


@router.callback_query(F.data.startswith("ib_apt:"))
async def cb_toggle_single_admin_perm(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_ib, raw_uid, perm_key = callback.data.split(":")
    inbox_id = int(raw_ib)
    target_uid = int(raw_uid)
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    adm = await repo.toggle_admin_permission(inbox_id, target_uid, perm_key, db_user.id)
    await callback.answer("✅ سطح دسترسی تغییر کرد.")
    if adm and callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=build_admin_permissions_kb(inbox_id, adm)
        )


@router.callback_query(F.data.startswith("ib_arm:"))
async def cb_remove_admin(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_ib, raw_uid = callback.data.split(":")
    inbox_id = int(raw_ib)
    target_uid = int(raw_uid)
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return
    await repo.remove_inbox_admin(inbox_id, target_uid, db_user.id)
    await callback.answer("🗑 ادمین از این صندوق حذف شد و دسترسی او فوراً قطع گردید.", show_alert=True)
    callback.data = f"ib_adm:{inbox_id}"
    await cb_inbox_admins_menu(callback, db_user, repo)


@router.callback_query(F.data.startswith("ib_asy:"))
async def cb_sync_real_chat_admins(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not inbox.target_chat_id:
        await callback.answer("این صندوق به کانال یا گروه متصل نیست.", show_alert=True)
        return
    if not await repo.check_inbox_permission(inbox_id, db_user.id, "manage_admins"):
        await callback.answer("عدم دسترسی.", show_alert=True)
        return

    real_admins = await fetch_real_chat_admin_ids(bot, inbox.target_chat_id)
    added = 0
    for uid, fname, uname in real_admins:
        if uid == inbox.owner_id:
            continue
        await repo.upsert_user(uid, fname, uname)
        existing = await repo.get_inbox_admin_record(inbox_id, uid)
        if not existing:
            await repo.add_or_update_inbox_admin(inbox_id, uid, db_user.id, preset="responder")
            added += 1

    await callback.answer(
        f"✅ همگام‌سازی انجام شد! {added} ادمین جدید از چت اضافه شدند.",
        show_alert=True,
    )
    callback.data = f"ib_adm:{inbox_id}"
    await cb_inbox_admins_menu(callback, db_user, repo)
