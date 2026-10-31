"""هندلرهای ارسال پیام ناشناس (copy_message)، حذف ۵ دقیقه‌ای، پاسخ دوطرفه، ریاکشن، درخواست هویت و گزارش تخلف."""

from datetime import timezone
from typing import Dict
from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import settings
from app.db.models import User, utcnow
from app.db.repositories import BotRepository
from app.handlers.states import AdminReplyState, ReportMessageState, WritingMessageState
from app.keyboards.inline import (
    build_main_reply_kb,
    build_received_message_kb,
    build_report_review_kb,
    build_reveal_consent_kb,
    build_sender_received_reply_kb,
    build_sender_sent_kb,
    build_writing_cancel_kb,
)
from app.locales.fa import MEDIA_FILTER_LABELS, REACTION_FALLBACK_TEMPLATE
from app.services.telegram_helpers import (
     safe_apply_reaction,
    safe_copy_message,
)
from app.utils.crypto import CryptoManager
from app.utils.validators import contains_link_or_id, detect_message_media_type

router = Router(name="messaging")


@router.message(WritingMessageState.waiting_anonymous_message)
@router.message(WritingMessageState.waiting_sender_followup)
async def handle_incoming_anonymous_message(
    message: Message,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    inbox_id = data.get("target_inbox_id")
    reply_to_record_id = data.get("reply_to_record_id")

    if not inbox_id:
        await state.clear()
        await message.answer(
            "⚠️ نشست ارسال پیام منقضی شده است.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    inbox = await repo.get_inbox_by_id(int(inbox_id))
    if not inbox or not inbox.is_active:
        await state.clear()
        await message.answer(
            "❌ این صندوق دیگر فعال نیست.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    sender_hash = repo.crypto.compute_sender_hash(inbox.id, db_user.id)
    if await repo.is_sender_blocked(inbox.id, sender_hash):
        await state.clear()
        await message.answer(
            "⛔ شما در این صندوق مسدود شده‌اید.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    media_type = detect_message_media_type(message)
    if media_type == "unsupported":
        await message.answer("⚠️ این نوع پیام پشتیبانی نمی‌شود.")
        return

    allowed_map = inbox.allowed_media or {}
    if not allowed_map.get(media_type, True):
        media_lbl = MEDIA_FILTER_LABELS.get(media_type, media_type)
        await message.answer(
            f"🚫 صاحب این صندوق دریافت «<b>{media_lbl}</b>» را غیرفعال کرده است.",
            parse_mode="HTML",
        )
        return

    text_or_caption = message.text or message.caption or ""
    if (inbox.filter_links or settings.filter_links_default) and contains_link_or_id(text_or_caption):
        await message.answer(
            "🚫 ارسال لینک یا یوزرنیم (@) در این صندوق مجاز نیست."
        )
        return

    saved_msg = await repo.save_message(
        inbox_id=inbox.id,
        sender_user_id=db_user.id,
        sender_tg_msg_id=message.message_id,
        delivered_tg_ids={},
        reply_to_record_id=reply_to_record_id,
    )

    short_code = CryptoManager.short_hash(sender_hash)
    recipients = await repo.get_authorized_recipient_ids(inbox.id)
    delivered_map: Dict[str, int] = {}

    header_text = (
        f"📨 <b>پیام ناشناس جدید در صندوق «{inbox.title}»</b>\n"
        f"🔖 کد فرستنده: <code>#{short_code}</code> | 🔢 پیام: <code>#{saved_msg.id}</code>"
    )
    kb = build_received_message_kb(
        msg_id=saved_msg.id,
        emojis=settings.reaction_emojis,
        active_emoji=None,
        is_blocked=False,
    )

    for admin_uid in recipients:
        try:
            await bot.send_message(chat_id=admin_uid, text=header_text, parse_mode="HTML")
            copied_id = await safe_copy_message(
                bot=bot,
                target_chat_id=admin_uid,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                reply_markup=kb,
            )
            if copied_id is not None:
                delivered_map[str(admin_uid)] = copied_id
        except Exception:
            continue

    saved_msg.delivered_tg_ids = delivered_map
    await repo.session.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>پیام ناشناس شما با موفقیت به «{inbox.title}» ارسال شد!</b>\n"
        "⏳ تا ۵ دقیقه آینده می‌توانید پیام ارسالی خود را از صندوق گیرنده نیز حذف کنید:",
        parse_mode="HTML",
        reply_markup=build_sender_sent_kb(saved_msg.id, inbox.id),
    )
    await message.answer(
        "منوی اصلی:",
        reply_markup=build_main_reply_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("s_again:"))
async def cb_send_another_message(
    callback: CallbackQuery,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    inbox_id = int(callback.data.split(":")[1])
    inbox = await repo.get_inbox_by_id(inbox_id)
    if not inbox or not inbox.is_active:
        await callback.answer("این صندوق غیرفعال شده است.", show_alert=True)
        return
    await state.set_state(WritingMessageState.waiting_anonymous_message)
    await state.update_data(target_inbox_id=inbox.id, reply_to_record_id=None)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"✍️ پیام جدید خود را برای صندوق «<b>{inbox.title}</b>» ارسال کنید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.callback_query(F.data.startswith("s_del:"))
async def cb_sender_delete_within_window(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    msg_id = int(callback.data.split(":")[1])
    msg = await repo.get_message(msg_id)
    if not msg or msg.is_deleted:
        await callback.answer("این پیام قبلاً حذف شده است.", show_alert=True)
        return

    sender_uid = repo.crypto.decrypt_sender_id(msg.sender_enc)
    if sender_uid != db_user.id:
        await callback.answer("شما فرستنده این پیام نیستید.", show_alert=True)
        return

    created = msg.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    elapsed = (utcnow() - created).total_seconds()
    if elapsed > settings.delete_window_seconds:
        await callback.answer(
            "⏰ مهلت ۵ دقیقه‌ای حذف پیام به پایان رسیده است!",
            show_alert=True,
        )
        return

    for admin_id_str, tg_msg_id in (msg.delivered_tg_ids or {}).items():
        try:
            await bot.delete_message(chat_id=int(admin_id_str), message_id=int(tg_msg_id))
        except Exception:
            pass

    await repo.mark_message_deleted(msg.id)
    await callback.answer("🗑 پیام شما با موفقیت از صندوق گیرنده حذف شد!", show_alert=True)
    if callback.message:
        await callback.message.edit_text("🗑 <i>این پیام توسط شما از صندوق گیرنده حذف شد.</i>", parse_mode="HTML")


@router.callback_query(F.data.startswith("m_rp:"))
async def cb_admin_start_reply(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    msg_id = int(callback.data.split(":")[1])
    msg = await repo.get_message(msg_id)
    if not msg or msg.is_deleted:
        await callback.answer("این پیام حذف شده است.", show_alert=True)
        return

    if not await repo.check_inbox_permission(msg.inbox_id, db_user.id, "reply"):
        await callback.answer("شما دسترسی پاسخگویی در این صندوق را ندارید.", show_alert=True)
        return

    await repo.set_replying_admin(msg.id, db_user.id)
    inbox = await repo.get_inbox_by_id(msg.inbox_id)
    short_code = CryptoManager.short_hash(msg.sender_hash)

    recipients = await repo.get_authorized_recipient_ids(msg.inbox_id)
    for other_admin_id in recipients:
        if other_admin_id != db_user.id:
            try:
                await bot.send_message(
                    chat_id=other_admin_id,
                    text=f"✍️ ادمین <b>{db_user.first_name}</b> در حال نوشتن پاسخ به پیام <code>#{msg.id}</code> (کد <code>#{short_code}</code>) در صندوق «{inbox.title if inbox else ''}» است...",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    await state.set_state(AdminReplyState.waiting_reply_content)
    await state.update_data(reply_target_msg_id=msg.id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"↩️ <b>پاسخ ناشناس به فرستنده <code>#{short_code}</code> (پیام #{msg.id}):</b>\n"
            "پاسخ خود (متن، عکس، ویس، ویدیو و...) را ارسال کنید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(AdminReplyState.waiting_reply_content)
async def msg_admin_send_reply(
    message: Message,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    target_msg_id = int(data["reply_target_msg_id"])
    orig_msg = await repo.get_message(target_msg_id)
    if not orig_msg or orig_msg.is_deleted:
        await state.clear()
        await message.answer(
            "❌ پیام اصلی یافت نشد یا حذف شده است.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    inbox = await repo.get_inbox_by_id(orig_msg.inbox_id)
    if not inbox:
        await state.clear()
        return

    sender_uid = repo.crypto.decrypt_sender_id(orig_msg.sender_enc)

    await bot.send_message(
        chat_id=sender_uid,
        text=f"📬 <b>پاسخ جدید از طرف صندوق «{inbox.title}»:</b>",
        parse_mode="HTML",
        reply_to_message_id=orig_msg.sender_tg_msg_id,
    )
    copied_id = await safe_copy_message(
        bot=bot,
        target_chat_id=sender_uid,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        reply_markup=build_sender_received_reply_kb(orig_msg.id),
    )

    await state.clear()
    if copied_id is None:
        await message.answer(
            "❌ ارسال پاسخ ناموفق بود (فرستنده ربات را مسدود کرده است).",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    await repo.mark_message_replied(orig_msg.id, db_user.id)
    await message.answer(
        "✅ پاسخ شما به صورت کاملاً ناشناس برای فرستنده ارسال شد.",
        reply_markup=build_main_reply_kb(is_super_admin),
    )


@router.callback_query(F.data.startswith("s_rpl:"))
async def cb_sender_reply_back(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
) -> None:
    orig_msg_id = int(callback.data.split(":")[1])
    orig_msg = await repo.get_message(orig_msg_id)
    if not orig_msg:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return
    inbox = await repo.get_inbox_by_id(orig_msg.inbox_id)
    if not inbox or not inbox.is_active:
        await callback.answer("این صندوق در حال حاضر غیرفعال است.", show_alert=True)
        return
    if await repo.is_sender_blocked(inbox.id, orig_msg.sender_hash):
        await callback.answer("شما در این صندوق مسدود شده‌اید.", show_alert=True)
        return

    await state.set_state(WritingMessageState.waiting_sender_followup)
    await state.update_data(target_inbox_id=inbox.id, reply_to_record_id=orig_msg.id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            f"↩️ پاسخ خود را برای صندوق «<b>{inbox.title}</b>» بنویسید و ارسال کنید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.callback_query(F.data.startswith("m_rc:"))
async def cb_admin_react_message(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_msg_id, raw_idx = callback.data.split(":")
    msg_id = int(raw_msg_id)
    idx = int(raw_idx)

    msg = await repo.get_message(msg_id)
    if not msg or msg.is_deleted:
        await callback.answer("این پیام حذف شده است.", show_alert=True)
        return

    if not await repo.check_inbox_permission(msg.inbox_id, db_user.id, "react"):
        await callback.answer("شما دسترسی ثبت ریاکشن در این صندوق را ندارید.", show_alert=True)
        return

    emojis = settings.reaction_emojis
    if idx < 0 or idx >= len(emojis):
        await callback.answer()
        return

    chosen_emoji = emojis[idx]
    active_emoji = await repo.set_or_toggle_reaction(
        message_id=msg.id,
        inbox_id=msg.inbox_id,
        admin_user_id=db_user.id,
        emoji=chosen_emoji,
    )

    inbox = await repo.get_inbox_by_id(msg.inbox_id)
    sender_uid = repo.crypto.decrypt_sender_id(msg.sender_enc)
    await safe_apply_reaction(
        bot=bot,
        sender_chat_id=sender_uid,
        sender_message_id=msg.sender_tg_msg_id,
        emoji=active_emoji,
        inbox_title=inbox.title if inbox else "صندوق ناشناس",
        fallback_text_template=REACTION_FALLBACK_TEMPLATE,
    )

    is_blk = await repo.is_sender_blocked(msg.inbox_id, msg.sender_hash)
    await callback.answer(
        f"واکنش {active_emoji} ثبت شد!" if active_emoji else "واکنش شما برداشته شد."
    )
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=build_received_message_kb(
                msg_id=msg.id,
                emojis=emojis,
                active_emoji=active_emoji,
                is_blocked=is_blk,
            )
        )


@router.callback_query(F.data.startswith("m_bl:"))
async def cb_admin_toggle_block_sender(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    msg_id = int(callback.data.split(":")[1])
    msg = await repo.get_message(msg_id)
    if not msg:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return

    if not await repo.check_inbox_permission(msg.inbox_id, db_user.id, "block_user"):
        await callback.answer("شما دسترسی مسدودسازی در این صندوق را ندارید.", show_alert=True)
        return

    short_code = CryptoManager.short_hash(msg.sender_hash)
    currently_blocked = await repo.is_sender_blocked(msg.inbox_id, msg.sender_hash)
    if currently_blocked:
        await repo.unblock_sender_by_hash(msg.inbox_id, msg.sender_hash, db_user.id)
        new_blk = False
        await callback.answer(f"🔓 فرستنده #{short_code} رفع مسدودیت شد.")
    else:
        await repo.block_sender(msg.inbox_id, msg.sender_hash, db_user.id)
        new_blk = True
        await callback.answer(f"🚫 فرستنده #{short_code} در این صندوق مسدود شد.", show_alert=True)

    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=build_received_message_kb(
                msg_id=msg.id,
                emojis=settings.reaction_emojis,
                active_emoji=None,
                is_blocked=new_blk,
            )
        )


@router.callback_query(F.data.startswith("m_dl:"))
async def cb_admin_delete_from_inbox(
    callback: CallbackQuery,
    db_user: User,
    repo: BotRepository,
) -> None:
    msg_id = int(callback.data.split(":")[1])
    msg = await repo.get_message(msg_id)
    if not msg:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return

    if not await repo.check_inbox_permission(msg.inbox_id, db_user.id, "delete_msg"):
        await callback.answer("شما دسترسی حذف پیام از این صندوق را ندارید.", show_alert=True)
        return

    await repo.mark_message_deleted(msg.id)
    await repo.log_inbox_action(msg.inbox_id, db_user.id, "delete_msg", f"حذف پیام #{msg.id}")
    await callback.answer("🗑 پیام از صندوق حذف شد.")
    if callback.message:
        try:
            await callback.message.delete()
        except Exception:
            pass


# ----------------------------------------------------------------------
# درخواست نمایش هویت با رضایت صریح فرستنده (Consent-Only Reveal)
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("m_rv:"))
async def cb_admin_request_reveal(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    msg_id = int(callback.data.split(":")[1])
    msg = await repo.get_message(msg_id)
    if not msg or msg.is_deleted:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return

    if not await repo.check_inbox_permission(msg.inbox_id, db_user.id, "request_reveal"):
        await callback.answer("شما دسترسی ارسال درخواست هویت در این صندوق را ندارید.", show_alert=True)
        return

    can_req, reason = await repo.can_request_reveal(
        msg=msg,
        admin_user_id=db_user.id,
        daily_limit=settings.reveal_daily_limit,
        cooldown_seconds=settings.reveal_cooldown_seconds,
    )
    if not can_req:
        await callback.answer(reason, show_alert=True)
        return

    req = await repo.create_reveal_request(msg, db_user.id)
    inbox = await repo.get_inbox_by_id(msg.inbox_id)
    sender_uid = repo.crypto.decrypt_sender_id(msg.sender_enc)

    try:
        await bot.send_message(
            chat_id=sender_uid,
            text=(
                f"🪪 <b>درخواست نمایش هویت از طرف صندوق «{inbox.title if inbox else ''}»</b>\n\n"
                "گیرنده پیام از شما درخواست کرده است که در صورت تمایل، هویت خود را در این مکالمه مشخص کنید.\n"
                "⚠️ <b>توجه مهم:</b> تایید این درخواست <b>قابل بازگشت نیست</b>، اما اطلاعات انتخابی شما صرفاً برای همان درخواست‌دهنده و فقط در همین مکالمه ارسال می‌شود.\n\n"
                "👇 لطفاً یکی از گزینه‌های زیر را انتخاب کنید:"
            ),
            parse_mode="HTML",
            reply_to_message_id=msg.sender_tg_msg_id,
            reply_markup=build_reveal_consent_kb(req.id),
        )
    except Exception:
        await callback.answer("❌ ارسال درخواست به فرستنده امکان‌پذیر نبود.", show_alert=True)
        return

    await callback.answer(
        "✅ درخواست نمایش هویت برای فرستنده ارسال شد. در صورت موافقت او به شما اطلاع داده می‌شود.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("rv_ok:"))
async def cb_sender_approve_reveal(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_req_id, level = callback.data.split(":")
    req_id = int(raw_req_id)
    req = await repo.get_reveal_request(req_id)
    if not req or req.status != "pending":
        await callback.answer("این درخواست قبلاً پاسخ داده شده است.", show_alert=True)
        return

    msg = await repo.get_message(req.message_id)
    if not msg:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return

    sender_uid = repo.crypto.decrypt_sender_id(msg.sender_enc)
    if sender_uid != db_user.id:
        await callback.answer("عدم دسترسی.", show_alert=True)
        return

    await repo.update_reveal_status(req.id, status="approved", reveal_level=level)
    short_code = CryptoManager.short_hash(msg.sender_hash)

    if level == "name":
        identity_str = f"👤 نام فرستنده: <b>{db_user.first_name}</b>"
    elif level == "username":
        uname = f"@{db_user.username}" if db_user.username else "بدون یوزرنیم"
        identity_str = f"👤 نام: <b>{db_user.first_name}</b> | یوزرنیم: {uname}"
    else:
        identity_str = (
            f"👤 نام: <a href='tg://user?id={db_user.id}'>{db_user.first_name}</a> "
            f"(لینک مستقیم پروفایل)"
        )

    try:
        await bot.send_message(
            chat_id=req.requested_by_admin_id,
            text=(
                f"🪪 <b>موافقت با نمایش هویت (پیام #{msg.id} - کد #{short_code}):</b>\n\n"
                f"{identity_str}\n\n"
                "<i>(این اطلاعات با رضایت صریح فرستنده و صرفاً برای شما در این مکالمه نمایش داده شد)</i>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await callback.answer("✅ هویت انتخابی شما برای گیرنده ارسال شد.")
    if callback.message:
        await callback.message.edit_text(
            "✅ شما با درخواست نمایش هویت در سطح انتخابی موافقت کردید.",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("rv_no:"))
async def cb_sender_reject_reveal(
    callback: CallbackQuery,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
) -> None:
    _, raw_req_id, raw_blk = callback.data.split(":")
    req_id = int(raw_req_id)
    block_future = raw_blk == "1"

    req = await repo.get_reveal_request(req_id)
    if not req or req.status != "pending":
        await callback.answer("این درخواست قبلاً پاسخ داده شده است.", show_alert=True)
        return

    msg = await repo.get_message(req.message_id)
    if not msg:
        await callback.answer("پیام یافت نشد.", show_alert=True)
        return

    sender_uid = repo.crypto.decrypt_sender_id(msg.sender_enc)
    if sender_uid != db_user.id:
        await callback.answer("عدم دسترسی.", show_alert=True)
        return

    await repo.update_reveal_status(
        req.id,
        status="rejected",
        block_inbox_requests=block_future,
    )
    short_code = CryptoManager.short_hash(msg.sender_hash)

    try:
        await bot.send_message(
            chat_id=req.requested_by_admin_id,
            text=f"❌ فرستنده پیام <code>#{msg.id}</code> (کد <code>#{short_code}</code>) درخواست نمایش هویت را رد کرد و مایل است ناشناس بماند.",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await callback.answer("✅ درخواست نمایش هویت رد شد و شما کاملاً ناشناس ماندید.")
    if callback.message:
        extra = " و درخواست‌های بعدی این صندوق مسدود شد." if block_future else "."
        await callback.message.edit_text(f"🔒 درخواست نمایش هویت رد شد{extra}")


# ----------------------------------------------------------------------
# گزارش تخلف پیام (Report Abuse) به ادمین کل
# ----------------------------------------------------------------------
@router.callback_query(F.data.startswith("m_rg:"))
async def cb_prompt_report_message(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    msg_id = int(callback.data.split(":")[1])
    await state.set_state(ReportMessageState.waiting_report_reason)
    await state.update_data(report_msg_id=msg_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "⚠️ <b>گزارش تخلف پیام به مدیریت کل ربات</b>\n"
            "لطفاً علت گزارش خود (مثلاً: مزاحمت، فحاشی، فیشینگ یا اسپم) را در یک پیام کوتاه بنویسید:",
            parse_mode="HTML",
            reply_markup=build_writing_cancel_kb(),
        )


@router.message(ReportMessageState.waiting_report_reason)
async def msg_submit_report(
    message: Message,
    bot: Bot,
    db_user: User,
    repo: BotRepository,
    state: FSMContext,
    is_super_admin: bool,
) -> None:
    data = await state.get_data()
    msg_id = int(data["report_msg_id"])
    reason = (message.text or "تخلف").strip()[:120]

    msg = await repo.get_message(msg_id)
    await state.clear()
    if not msg:
        await message.answer(
            "❌ پیام مورد نظر یافت نشد.",
            reply_markup=build_main_reply_kb(is_super_admin),
        )
        return

    rep = await repo.create_report(msg, db_user.id, reason)
    short_code = CryptoManager.short_hash(msg.sender_hash)

    for admin_id in settings.super_admins:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🚨 <b>گزارش تخلف جدید #{rep.id}</b>\n\n"
                    f"🔢 شناسه پیام: <code>#{msg.id}</code> (صندوق <code>#{msg.inbox_id}</code>)\n"
                    f"🔖 کد هش فرستنده در صندوق: <code>#{short_code}</code>\n"
                    f"👤 گزارش‌دهنده: <code>{db_user.id}</code>\n"
                    f"📝 علت گزارش: <b>{rep.reason}</b>\n\n"
                    "<i>(در صورت تایید اعتبار گزارش، سیستم به صورت داخلی شمارنده تخلفات فرستنده را افزایش داده و در صورت رسیدن به سقف مجاز، او را خودکار بن موقت می‌کند)</i>"
                ),
                parse_mode="HTML",
                reply_markup=build_report_review_kb(rep.id),
            )
        except Exception:
            pass

    await message.answer(
        "✅ گزارش تخلف شما ثبت و برای بررسی به مدیریت کل ربات ارسال شد.",
        reply_markup=build_main_reply_kb(is_super_admin),
    )
