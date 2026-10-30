"""هندلرهای ارسال پیام ناشناس (copy_message)، حذف ۵ دقیقه‌ای، پاسخ دوطرفه، ریاکشن، درخواست هویت با رضایت، بلاک و گزارش."""

from datetime import timezone
from typing import Dict
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Settings
from app.db.models import User, utcnow
from app.db.repositories import BotRepository
from app.handlers.states import MessagingSG
from app.handlers.user_panel import enter_inbox_writing_mode
from app.keyboards.inline import (
    cancel_kb,
    main_menu_kb,
    reaction_picker_kb,
    received_message_actions_kb,
    report_reasons_kb,
    sender_post_send_kb,
    sender_reply_back_kb,
    sender_reveal_consent_kb,
    superadmin_report_review_kb,
)
from app.locales.fa import MEDIA_LABELS, REPORT_REASONS, Texts
from app.services.telegram_helpers import safe_apply_reaction, safe_copy_message
from app.utils.validators import contains_link_or_id, detect_message_media_type

router = Router(name="messaging")


@router.message(MessagingSG.writing_to_inbox)
async def handle_incoming_anonymous_message(
    message: Message,
    state: FSMContext,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = int(data.get("target_inbox_id", 0))
    parent_msg_id = data.get("parent_message_id")

    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not inbox.is_active:
        await state.clear()
        await message.answer(Texts.INBOX_DISABLED, reply_markup=main_menu_kb(is_super_admin))
        return

    s_hash = repo.crypto.compute_sender_hash(inbox.id, db_user.id)
    if await repo.is_sender_blocked(inbox.id, s_hash):
        await state.clear()
        await message.answer(Texts.SENDER_BLOCKED_IN_INBOX, reply_markup=main_menu_kb(is_super_admin))
        return

    # بررسی فیلتر لینک/آیدی سراسری
    filter_links_val = await repo.get_setting(
        "filter_links", "true" if settings.filter_links_default else "false"
    )
    text_or_caption = message.text or message.caption or ""
    if filter_links_val.lower() == "true" and contains_link_or_id(text_or_caption):
        await message.answer(Texts.LINKS_FILTERED_ERROR)
        return

    # بررسی فیلتر نوع مدیا در صندوق
    media_type = detect_message_media_type(message)
    allowed_map = inbox.allowed_media or {}
    if media_type == "unsupported" or not allowed_map.get(media_type, True):
        allowed_labels = [
            lbl for k, lbl in MEDIA_LABELS.items() if allowed_map.get(k, True)
        ]
        await message.answer(
            Texts.MEDIA_NOT_ALLOWED.format(
                media_type=MEDIA_LABELS.get(media_type, media_type),
                allowed="، ".join(allowed_labels) or "هیچ‌کدام",
            )
        )
        return

    # ذخیره اولیه رکورد پیام برای دریافت message.id
    msg_rec = await repo.save_message(
        inbox_id=inbox.id,
        sender_user_id=db_user.id,
        sender_tg_msg_id=message.message_id,
        delivered_tg_ids={},
        media_type=media_type,
        direction="inbound",
        parent_message_id=parent_msg_id,
    )

    short_h = repo.crypto.short_hash(s_hash)
    header_text = (
        Texts.NEW_REPLY_FROM_SENDER_HEADER.format(inbox_title=inbox.title, short_hash=short_h)
        if parent_msg_id
        else Texts.NEW_ANON_MESSAGE_HEADER.format(inbox_title=inbox.title, short_hash=short_h)
    )

    receivers = await repo.get_authorized_receivers(inbox.id)
    delivered_map: Dict[str, int] = {}
    action_kb = received_message_actions_kb(msg_rec.id)

    for r_uid in receivers:
        try:
            hdr = await bot.send_message(chat_id=r_uid, text=header_text, parse_mode="HTML")
            copied_id = await safe_copy_message(
                bot=bot,
                target_chat_id=r_uid,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=action_kb,
                reply_to_message_id=hdr.message_id,
            )
            if copied_id:
                delivered_map[str(r_uid)] = copied_id
        except Exception:
            pass

    msg_rec.delivered_tg_ids = delivered_map
    await state.clear()

    confirm_msg = await message.answer(
        Texts.MESSAGE_SENT_SUCCESS,
        parse_mode="HTML",
        reply_markup=sender_post_send_kb(msg_rec.id),
    )
    msg_rec.sender_confirm_msg_id = confirm_msg.message_id
    await repo.session.commit()

    await message.answer(
        "به منوی اصلی بازگشتید:",
        reply_markup=main_menu_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("sender_del_msg:"))
async def cb_sender_delete_own_message(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
) -> None:
    msg_id = int((callback.data or "0").split(":")[1])
    msg_rec = await repo.get_message(msg_id)
    if not msg_rec or msg_rec.is_deleted:
        await callback.answer("این پیام قبلاً حذف شده یا وجود ندارد.", show_alert=True)
        return

    # بررسی مالکیت فرستنده با رمزگشایی داخلی در حافظه
    real_sender_id = repo.crypto.decrypt_sender_id(msg_rec.sender_enc)
    if real_sender_id != db_user.id:
        await callback.answer("❌ شما فرستنده این پیام نیستید.", show_alert=True)
        return

    created = msg_rec.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    elapsed = (utcnow() - created).total_seconds()
    if elapsed > settings.delete_window_seconds:
        await callback.answer(Texts.MSG_DELETE_EXPIRED, show_alert=True)
        return

    for chat_id_str, copied_msg_id in (msg_rec.delivered_tg_ids or {}).items():
        try:
            await bot.delete_message(chat_id=int(chat_id_str), message_id=int(copied_msg_id))
        except Exception:
            pass

    await repo.mark_message_deleted(msg_rec.id)
    if callback.message:
        await callback.message.edit_text(Texts.MSG_DELETED_BY_SENDER)
    await callback.answer("✅ پیام از صندوق گیرنده حذف شد.")


@router.callback_query(F.data.startswith("sender_reply_ib:"))
async def cb_sender_reply_again(
    callback: CallbackQuery,
    state: FSMContext,
    repo: BotRepository,
    db_user: User,
) -> None:
    parts = (callback.data or "").split(":")
    inbox_token = parts[1]
    parent_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    if callback.message:
        await enter_inbox_writing_mode(
            callback.message, state, repo, db_user, f"ib_{inbox_token}", parent_message_id=parent_id
        )
    await callback.answer()


# ------------------------------------------------------------------
# پاسخ دوطرفه ناشناس از طرف مالک/ادمین صندوق به فرستنده
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("msg_reply:"))
async def cb_start_reply_to_sender(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
) -> None:
    msg_id = int((callback.data or "0").split(":")[1])
    msg_rec = await repo.get_message(msg_id)
    if not msg_rec or msg_rec.is_deleted:
        await callback.answer("پیام یافت نشد یا توسط فرستنده حذف شده است.", show_alert=True)
        return

    if not await repo.check_inbox_permission(msg_rec.inbox_id, db_user.id, "reply"):
        await callback.answer("❌ شما دسترسی پاسخ دادن به پیام‌های این صندوق را ندارید.", show_alert=True)
        return

    inbox = await repo.get_inbox_by_id(msg_rec.inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return

    await repo.set_active_replier(msg_rec.id, db_user.id)
    short_h = repo.crypto.short_hash(msg_rec.sender_hash)

    # اطلاع‌رسانی به سایر ادمین‌های صندوق که «ادمین X در حال پاسخ است»
    receivers = await repo.get_authorized_receivers(inbox.id)
    admin_display = db_user.alias_name or db_user.first_name
    for r_uid in receivers:
        if r_uid != db_user.id:
            try:
                await bot.send_message(
                    chat_id=r_uid,
                    text=Texts.ADMIN_REPLYING_NOTICE.format(
                        admin_name=admin_display, short_hash=short_h
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await state.set_state(MessagingSG.replying_to_sender)
    await state.update_data(reply_target_msg_id=msg_rec.id)
    if callback.message:
        await callback.message.answer(
            Texts.ENTER_REPLY_TEXT.format(short_hash=short_h, inbox_title=inbox.title),
            parse_mode="HTML",
            reply_markup=cancel_kb(),
        )
    await callback.answer()


@router.message(MessagingSG.replying_to_sender)
async def process_admin_reply_to_sender(
    message: Message,
    state: FSMContext,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    target_msg_id = int(data.get("reply_target_msg_id", 0))
    orig_msg = await repo.get_message(target_msg_id)
    if not orig_msg or orig_msg.is_deleted:
        await state.clear()
        await message.answer(
            "❌ پیام اصلی یافت نشد یا حذف شده است.",
            reply_markup=main_menu_kb(is_super_admin),
        )
        return

    if not await repo.check_inbox_permission(orig_msg.inbox_id, db_user.id, "reply"):
        await state.clear()
        await message.answer("❌ عدم دسترسی به پاسخگویی.", reply_markup=main_menu_kb(is_super_admin))
        return

    inbox = await repo.get_inbox_by_id(orig_msg.inbox_id)
    if not inbox:
        await state.clear()
        return

    # رمزگشایی داخلی آیدی فرستنده صرفاً در حافظه برای ارسال پاسخ با copy_message
    sender_chat_id = repo.crypto.decrypt_sender_id(orig_msg.sender_enc)

    header = await bot.send_message(
        chat_id=sender_chat_id,
        text=Texts.REPLY_DELIVERED_TO_SENDER.format(inbox_title=inbox.title),
        reply_to_message_id=orig_msg.sender_tg_msg_id,
        parse_mode="HTML",
    )
    copied_id = await safe_copy_message(
        bot=bot,
        target_chat_id=sender_chat_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        reply_markup=sender_reply_back_kb(inbox.token, orig_msg.id),
        reply_to_message_id=header.message_id,
    )

    await repo.set_active_replier(orig_msg.id, None)
    short_h = repo.crypto.short_hash(orig_msg.sender_hash)
    await repo.add_inbox_log(
        inbox.id, db_user.id, "reply_message", f"ارسال پاسخ به پیام #{orig_msg.id} (#{short_h})"
    )
    await state.clear()

    if copied_id is not None:
        await message.answer(
            Texts.REPLY_SENT_CONFIRM,
            reply_markup=main_menu_kb(is_super_admin),
        )
    else:
        await message.answer(
            "❌ ارسال پاسخ ناموفق بود (احتمالاً فرستنده ربات را مسدود کرده است).",
            reply_markup=main_menu_kb(is_super_admin),
        )


# ------------------------------------------------------------------
# ریاکشن دوطرفه (setMessageReaction + Fallback)
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("msg_react_menu:"))
async def cb_open_reaction_picker(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
) -> None:
    msg_id = int((callback.data or "0").split(":")[1])
    msg_rec = await repo.get_message(msg_id)
    if not msg_rec or msg_rec.is_deleted:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return
    if not await repo.check_inbox_permission(msg_rec.inbox_id, db_user.id, "react"):
        await callback.answer("❌ شما دسترسی ثبت ریاکشن در این صندوق را ندارید.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=reaction_picker_kb(msg_id, settings.reaction_emojis)
        )
    await callback.answer(Texts.SELECT_REACTION)


@router.callback_query(F.data.startswith("msg_actions_back:"))
async def cb_back_to_msg_actions(callback: CallbackQuery) -> None:
    msg_id = int((callback.data or "0").split(":")[1])
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=received_message_actions_kb(msg_id)
        )
    await callback.answer()


@router.callback_query(F.data.startswith("msg_react_set:"))
async def cb_set_message_reaction(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
) -> None:
    parts = (callback.data or "").split(":")
    msg_id = int(parts[1])
    emoji = parts[2]
    msg_rec = await repo.get_message(msg_id)
    if not msg_rec or msg_rec.is_deleted:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return
    if not await repo.check_inbox_permission(msg_rec.inbox_id, db_user.id, "react"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return

    inbox = await repo.get_inbox_by_id(msg_rec.inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return

    await repo.set_or_update_reaction(msg_rec.id, inbox.id, db_user.id, emoji)
    sender_chat_id = repo.crypto.decrypt_sender_id(msg_rec.sender_enc)
    await safe_apply_reaction(
        bot=bot,
        sender_chat_id=sender_chat_id,
        sender_message_id=msg_rec.sender_tg_msg_id,
        emoji=emoji,
        inbox_title=inbox.title,
        fallback_text_template=Texts.REACTION_FALLBACK_NOTIFY,
    )
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=received_message_actions_kb(msg_rec.id)
        )
    await callback.answer(Texts.REACTION_APPLIED.format(emoji=emoji))


@router.callback_query(F.data.startswith("msg_react_clr:"))
async def cb_clear_message_reaction(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
) -> None:
    msg_id = int((callback.data or "0").split(":")[1])
    msg_rec = await repo.get_message(msg_id)
    if not msg_rec:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return
    if not await repo.check_inbox_permission(msg_rec.inbox_id, db_user.id, "react"):
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    inbox = await repo.get_inbox_by_id(msg_rec.inbox_id)
    await repo.set_or_update_reaction(msg_rec.id, msg_rec.inbox_id, db_user.id, None)
    sender_chat_id = repo.crypto.decrypt_sender_id(msg_rec.sender_enc)
    await safe_apply_reaction(
        bot=bot,
        sender_chat_id=sender_chat_id,
        sender_message_id=msg_rec.sender_tg_msg_id,
        emoji=None,
        inbox_title=inbox.title if inbox else "",
        fallback_text_template=Texts.REACTION_FALLBACK_NOTIFY,
    )
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=received_message_actions_kb(msg_rec.id)
        )
    await callback.answer(Texts.REACTION_REMOVED)


# ------------------------------------------------------------------
# درخواست نمایش هویت با رضایت صریح فرستنده
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("msg_reveal_req:"))
async def cb_request_sender_identity(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
) -> None:
    msg_id = int((callback.data or "0").split(":")[1])
    msg_rec = await repo.get_message(msg_id)
    if not msg_rec or msg_rec.is_deleted:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return
    if not await repo.check_inbox_permission(msg_rec.inbox_id, db_user.id, "request_reveal"):
        await callback.answer("❌ شما دسترسی درخواست هویت در این صندوق را ندارید.", show_alert=True)
        return

    can_req, reason = await repo.can_request_reveal(
        message=msg_rec,
        requester_id=db_user.id,
        daily_limit=settings.reveal_daily_limit,
        cooldown_seconds=settings.reveal_cooldown_seconds,
    )
    if not can_req:
        await callback.answer(f"⚠️ {reason}", show_alert=True)
        return

    inbox = await repo.get_inbox_by_id(msg_rec.inbox_id)
    if not inbox:
        await callback.answer("صندوق یافت نشد.", show_alert=True)
        return

    rev_req = await repo.create_reveal_request(msg_rec, db_user.id)
    sender_chat_id = repo.crypto.decrypt_sender_id(msg_rec.sender_enc)

    try:
        await bot.send_message(
            chat_id=sender_chat_id,
            text=Texts.REVEAL_CONFIRM_SENDER.format(inbox_title=inbox.title),
            parse_mode="HTML",
            reply_markup=sender_reveal_consent_kb(rev_req.id),
            reply_to_message_id=msg_rec.sender_tg_msg_id,
        )
        await callback.answer(Texts.REVEAL_SENT_TO_SENDER, show_alert=True)
    except Exception:
        await callback.answer("❌ ارسال درخواست به فرستنده امکان‌پذیر نبود.", show_alert=True)


@router.callback_query(F.data.startswith("rev_ans:"))
async def cb_sender_respond_reveal(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
) -> None:
    parts = (callback.data or "").split(":")
    req_id = int(parts[1])
    decision = parts[2]

    rev_req = await repo.get_reveal_request(req_id)
    if not rev_req or rev_req.status != "pending":
        await callback.answer("این درخواست قبلاً پاسخ داده شده است.", show_alert=True)
        return

    msg_rec = await repo.get_message(rev_req.message_id)
    if not msg_rec:
        await callback.answer("پیام مرتبط یافت نشد.", show_alert=True)
        return

    real_sender_id = repo.crypto.decrypt_sender_id(msg_rec.sender_enc)
    if real_sender_id != db_user.id:
        await callback.answer("❌ این درخواست متعلق به شما نیست.", show_alert=True)
        return

    inbox = await repo.get_inbox_by_id(rev_req.inbox_id)
    inbox_title = inbox.title if inbox else "صندوق"
    short_h = repo.crypto.short_hash(rev_req.sender_hash)

    if decision.startswith("accept_"):
        await repo.update_reveal_status(rev_req.id, "accepted", block_inbox_requests=False)
        if decision == "accept_name":
            identity_info = f"• نام: <b>{db_user.first_name}</b>"
        elif decision == "accept_username":
            uname = f"@{db_user.username}" if db_user.username else "ندارد"
            identity_info = f"• نام: <b>{db_user.first_name}</b>\n• یوزرنیم: {uname}"
        else:
            identity_info = (
                f"• نام: <b>{db_user.first_name}</b>\n"
                f"• لینک مستقیم پروفایل: <a href='tg://user?id={db_user.id}'>{db_user.first_name}</a>"
            )

        # افشا فقط و فقط برای همان ادمین درخواست‌دهنده (requester_id) ارسال می‌شود و هرگز ذخیره نمی‌شود
        try:
            await bot.send_message(
                chat_id=rev_req.requester_id,
                text=Texts.REVEAL_ACCEPTED_TO_REQUESTER.format(
                    short_hash=short_h,
                    inbox_title=inbox_title,
                    identity_info=identity_info,
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        if callback.message:
            await callback.message.edit_text(Texts.REVEAL_SENDER_DONE_ACCEPT)
        await callback.answer("✅ هویت انتخابی شما ارسال شد.")
    else:
        block_ib = decision == "reject_block"
        await repo.update_reveal_status(rev_req.id, "rejected", block_inbox_requests=block_ib)
        try:
            await bot.send_message(
                chat_id=rev_req.requester_id,
                text=Texts.REVEAL_REJECTED_TO_REQUESTER.format(
                    short_hash=short_h, inbox_title=inbox_title
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        if callback.message:
            await callback.message.edit_text(
                Texts.REVEAL_SENDER_BLOCKED_INBOX if block_ib else Texts.REVEAL_SENDER_DONE_REJECT
            )
        await callback.answer()


# ------------------------------------------------------------------
# بلاک در سطح صندوق و گزارش تخلف به ادمین کل
# ------------------------------------------------------------------
@router.callback_query(F.data.startswith("msg_block:"))
async def cb_block_sender_in_inbox(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    msg_id = int((callback.data or "0").split(":")[1])
    msg_rec = await repo.get_message(msg_id)
    if not msg_rec:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return
    if not await repo.check_inbox_permission(msg_rec.inbox_id, db_user.id, "block"):
        await callback.answer("❌ شما دسترسی بلاک کردن فرستنده در این صندوق را ندارید.", show_alert=True)
        return

    await repo.block_sender(msg_rec.inbox_id, msg_rec.sender_hash, db_user.id)
    short_h = repo.crypto.short_hash(msg_rec.sender_hash)
    await callback.answer(
        f"🚫 فرستنده #{short_h} در این صندوق مسدود شد.", show_alert=True
    )


@router.callback_query(F.data.startswith("msg_report_menu:"))
async def cb_open_report_menu(
    callback: CallbackQuery,
    repo: BotRepository,
    db_user: User,
) -> None:
    msg_id = int((callback.data or "0").split(":")[1])
    msg_rec = await repo.get_message(msg_id)
    if not msg_rec:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return
    is_owner, perms = await get_user_effective_perms_for_report(repo, msg_rec.inbox_id, db_user.id)
    if not is_owner and not perms:
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=report_reasons_kb(msg_id))
    await callback.answer("علت گزارش تخلف را انتخاب کنید:")


async def get_user_effective_perms_for_report(
    repo: BotRepository, inbox_id: int, user_id: int
) -> tuple[bool, bool]:
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox:
        return False, False
    if inbox.owner_id == user_id:
        return True, True
    adm = await repo.get_inbox_admin_record(inbox_id, user_id)
    return False, adm is not None


@router.callback_query(F.data.startswith("msg_report_do:"))
async def cb_submit_report(
    callback: CallbackQuery,
    bot: Bot,
    repo: BotRepository,
    db_user: User,
    settings: Settings,
) -> None:
    parts = (callback.data or "").split(":")
    msg_id = int(parts[1])
    reason_key = parts[2]
    reason_label = REPORT_REASONS.get(reason_key, reason_key)

    msg_rec = await repo.get_message(msg_id)
    if not msg_rec:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return
    is_owner, is_adm = await get_user_effective_perms_for_report(repo, msg_rec.inbox_id, db_user.id)
    if not is_owner and not is_adm:
        await callback.answer("❌ عدم دسترسی.", show_alert=True)
        return

    rep = await repo.create_report(msg_rec, db_user.id, reason_label)
    short_h = repo.crypto.short_hash(msg_rec.sender_hash)

    # ارسال گزارش به ادمین‌های کل ربات به همراه کپی پیام مورد شکایت (بدون افشای هویت فرستنده!)
    alert_text = (
        f"⚠️ <b>گزارش تخلف جدید (#{rep.id})</b>\n\n"
        f"• صندوق شماره: <code>{msg_rec.inbox_id}</code>\n"
        f"• هش فرستنده در صندوق: <code>#{short_h}</code>\n"
        f"• دلیل گزارش: <b>{reason_label}</b>\n\n"
        "پیام مورد شکایت در ادامه کپی شده است. لطفاً بررسی و تعیین وضعیت کنید:"
    )
    for sa_id in settings.super_admins:
        try:
            hdr = await bot.send_message(chat_id=sa_id, text=alert_text, parse_mode="HTML")
            if callback.message:
                await safe_copy_message(
                    bot=bot,
                    target_chat_id=sa_id,
                    from_chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    reply_markup=superadmin_report_review_kb(rep.id),
                    reply_to_message_id=hdr.message_id,
                )
        except Exception:
            pass

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=received_message_actions_kb(msg_rec.id)
        )
    await callback.answer("✅ گزارش شما برای بررسی به مدیریت کل ربات ارسال شد.", show_alert=True)
