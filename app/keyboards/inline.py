"""ساخت کیبوردهای شیشه‌ای (Inline) و معمولی (Reply) با Callback Data کوتاه (<= ۶۴ بایت)."""

from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.db.models import BlockRecord, ForcedChannel, Inbox, InboxAdmin, SenderRevealPreference, User
from app.locales.fa import (
    BTN_ACCEPT_TERMS,
    BTN_BLOCKLIST,
    BTN_CANCEL_WRITING,
    BTN_CHECK_JOIN,
    BTN_CREATE_INBOX,
    BTN_HELP,
    BTN_MY_INBOXES,
    BTN_MY_PROFILE,
    BTN_PRIVACY_SETTINGS,
    BTN_SUPER_ADMIN,
    INBOX_TYPE_LABELS,
    MEDIA_FILTER_LABELS,
    PERMISSION_LABELS,
    ROLE_PRESET_LABELS,
)
from app.utils.crypto import CryptoManager


def build_main_reply_kb(is_super_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_MY_INBOXES), KeyboardButton(text=BTN_CREATE_INBOX)],
        [KeyboardButton(text=BTN_MY_PROFILE), KeyboardButton(text=BTN_BLOCKLIST)],
        [KeyboardButton(text=BTN_PRIVACY_SETTINGS), KeyboardButton(text=BTN_HELP)],
    ]
    if is_super_admin:
        rows.append([KeyboardButton(text=BTN_SUPER_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def build_writing_cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_CANCEL_WRITING)]],
        resize_keyboard=True,
    )


def build_terms_kb(payload: str = "") -> InlineKeyboardMarkup:
    cb = f"terms_ok:{payload[:45]}" if payload else "terms_ok:"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_ACCEPT_TERMS, callback_data=cb)]]
    )


def build_force_join_kb(channels: Sequence[ForcedChannel], payload: str = "") -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for ch in channels:
        rows.append([InlineKeyboardButton(text=f"📢 عضویت در «{ch.title}»", url=ch.invite_link)])
    cb = f"chk_join:{payload[:45]}" if payload else "chk_join:"
    rows.append([InlineKeyboardButton(text=BTN_CHECK_JOIN, callback_data=cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_inboxes_list_kb(inboxes: Sequence[Inbox], user_id: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for ib in inboxes:
        status_icon = "🟢" if ib.is_active else "🔴"
        role_tag = "👑" if ib.owner_id == user_id else "👮"
        type_lbl = INBOX_TYPE_LABELS.get(ib.type, "👤")
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status_icon} {role_tag} {ib.title} ({type_lbl})",
                    callback_data=f"ib_v:{ib.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ ساخت صندوق جدید", callback_data="ib_new_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_create_inbox_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 صندوق شخصی", callback_data="ib_mk:personal")],
            [InlineKeyboardButton(text="📢 صندوق کانال (نیازمند ادمین بودن)", callback_data="ib_mk:channel")],
            [InlineKeyboardButton(text="👥 صندوق گروه (نیازمند ادمین بودن)", callback_data="ib_mk:group")],
            [InlineKeyboardButton(text="🔙 بازگشت به صندوق‌های من", callback_data="ib_list")],
        ]
    )


def build_inbox_detail_kb(
    inbox: Inbox,
    bot_username: str,
    can_edit_welcome: bool,
    can_manage_admins: bool,
    is_owner: bool,
) -> InlineKeyboardMarkup:
    code = inbox.custom_slug or f"ib_{inbox.token}"
    deep_link = f"https://t.me/{bot_username}?start={code}"
    share_text = quote(f"💌 از طریق لینک زیر به صورت کاملاً ناشناس برام پیام بفرست:\n{deep_link}")
    share_url = f"https://t.me/share/url?url={quote(deep_link)}&text={share_text}"

    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📤 اشتراک‌گذاری لینک صندوق", url=share_url)],
    ]

    if can_edit_welcome:
        toggle_lbl = "🔴 غیرفعال کردن صندوق" if inbox.is_active else "🟢 فعال کردن صندوق"
        link_flt_lbl = "🔗 فیلتر لینک و آیدی: روشن ✅" if inbox.filter_links else "🔗 فیلتر لینک و آیدی: خاموش ❌"
        rows.append(
            [
                InlineKeyboardButton(text=toggle_lbl, callback_data=f"ib_tg:{inbox.id}"),
                InlineKeyboardButton(text="🎞 فیلتر نوع مدیا", callback_data=f"ib_mf:{inbox.id}"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text="✏️ ویرایش خوش‌آمد", callback_data=f"ib_ew:{inbox.id}"),
                InlineKeyboardButton(text="🏷 ویرایش عنوان", callback_data=f"ib_et:{inbox.id}"),
            ]
        )
        rows.append([InlineKeyboardButton(text=link_flt_lbl, callback_data=f"ib_lf:{inbox.id}")])

    if is_owner:
        rows.append(
            [
                InlineKeyboardButton(text="🔄 ریست لینک (ابطال فوری)", callback_data=f"ib_rst:{inbox.id}"),
                InlineKeyboardButton(text="🔖 کد کوتاه (Slug)", callback_data=f"ib_slg:{inbox.id}"),
            ]
        )

    if can_manage_admins:
        rows.append([InlineKeyboardButton(text="👥 مدیریت ادمین‌های صندوق", callback_data=f"ib_adm:{inbox.id}")])

    rows.append([InlineKeyboardButton(text="📜 لاگ اقدامات ادمین‌ها", callback_data=f"ib_log:{inbox.id}")])

    if is_owner:
        rows.append([InlineKeyboardButton(text="🗑 حذف کامل این صندوق", callback_data=f"ib_del_c:{inbox.id}")])

    rows.append([InlineKeyboardButton(text="🔙 بازگشت به لیست صندوق‌ها", callback_data="ib_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_media_filter_kb(inbox_id: int, allowed_media: Dict[str, bool]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for key, label in MEDIA_FILTER_LABELS.items():
        enabled = allowed_media.get(key, True)
        state = "✅ مجاز" if enabled else "❌ مسدود"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{label}: {state}",
                    callback_data=f"ib_mft:{inbox_id}:{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به تنظیمات صندوق", callback_data=f"ib_v:{inbox_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_inbox_admins_kb(
    inbox: Inbox,
    admins: List[Tuple[InboxAdmin, Optional[User]]],
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for adm, u in admins:
        name = u.first_name if u else f"کاربر {adm.user_id}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👮 {name} ({adm.role_preset})",
                    callback_data=f"ib_av:{inbox.id}:{adm.user_id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="➕ افزودن با آیدی/فوروارد", callback_data=f"ib_aa:{inbox.id}"),
            InlineKeyboardButton(text="🔗 ساخت لینک دعوت یک‌بارمصرف", callback_data=f"ib_ai:{inbox.id}"),
        ]
    )
    if inbox.type in ("channel", "group") and inbox.target_chat_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 همگام‌سازی با ادمین‌های واقعی کانال/گروه",
                    callback_data=f"ib_asy:{inbox.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به صندوق", callback_data=f"ib_v:{inbox.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_preset_select_kb(inbox_id: int, mode: str, target_uid: int = 0) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for preset_key, label in ROLE_PRESET_LABELS.items():
        if mode == "invite":
            cb = f"ib_ais:{inbox_id}:{preset_key}"
        else:
            cb = f"ib_aps:{inbox_id}:{target_uid}:{preset_key}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"ib_adm:{inbox_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_permissions_kb(inbox_id: int, adm: InboxAdmin) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for perm_key, label in PERMISSION_LABELS.items():
        val = bool(getattr(adm, perm_key, False))
        icon = "✅" if val else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {label}",
                    callback_data=f"ib_apt:{inbox_id}:{adm.user_id}:{perm_key}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 حذف این ادمین از صندوق",
                callback_data=f"ib_arm:{inbox_id}:{adm.user_id}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به لیست ادمین‌ها", callback_data=f"ib_adm:{inbox_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_received_message_kb(
    msg_id: int,
    emojis: List[str],
    active_emoji: Optional[str] = None,
    is_blocked: bool = False,
) -> InlineKeyboardMarkup:
    emoji_row: List[InlineKeyboardButton] = []
    for idx, emj in enumerate(emojis[:5]):
        txt = f"[{emj}]" if active_emoji == emj else emj
        emoji_row.append(InlineKeyboardButton(text=txt, callback_data=f"m_rc:{msg_id}:{idx}"))

    blk_text = "🔓 رفع بلاک فرستنده" if is_blocked else "🚫 بلاک فرستنده"
    rows: List[List[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="↩️ پاسخ ناشناس", callback_data=f"m_rp:{msg_id}"),
            InlineKeyboardButton(text="🪪 درخواست هویت", callback_data=f"m_rv:{msg_id}"),
        ],
        emoji_row,
        [
            InlineKeyboardButton(text=blk_text, callback_data=f"m_bl:{msg_id}"),
            InlineKeyboardButton(text="🗑 حذف از صندوق", callback_data=f"m_dl:{msg_id}"),
            InlineKeyboardButton(text="⚠️ گزارش تخلف", callback_data=f"m_rg:{msg_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_sender_sent_kb(msg_id: int, inbox_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 حذف پیام ارسالی (مهلت ۵ دقیقه)",
                    callback_data=f"s_del:{msg_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ ارسال پیام دیگر به همین صندوق",
                    callback_data=f"s_again:{inbox_id}",
                )
            ],
        ]
    )


def build_sender_received_reply_kb(reply_msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ پاسخ به این پیام",
                    callback_data=f"s_rpl:{reply_msg_id}",
                )
            ]
        ]
    )


def build_reveal_consent_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ موافقم: فقط نام من نمایش داده شود",
                    callback_data=f"rv_ok:{req_id}:name",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ موافقم: نام و یوزرنیم (@)",
                    callback_data=f"rv_ok:{req_id}:username",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ موافقم: لینک مستقیم پروفایل (tg://user)",
                    callback_data=f"rv_ok:{req_id}:link",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ رد درخواست (ناشناس بمانم)",
                    callback_data=f"rv_no:{req_id}:0",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔕 رد و بستن درخواست‌های بعدی این صندوق",
                    callback_data=f"rv_no:{req_id}:1",
                )
            ],
        ]
    )


def build_blocklist_kb(blocks: List[Tuple[BlockRecord, Inbox]]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for blk, ib in blocks[:25]:
        short_code = CryptoManager.short_hash(blk.sender_hash)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔓 رفع بلاک #{short_code} در «{ib.title}»",
                    callback_data=f"blk_rm:{blk.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_privacy_settings_kb(
    user: User,
    inbox_prefs: List[Tuple[SenderRevealPreference, Inbox]],
) -> InlineKeyboardMarkup:
    glob_lbl = (
        "🪪 دریافت درخواست نمایش هویت (کلی): روشن ✅"
        if user.allow_reveal_global
        else "🪪 دریافت درخواست نمایش هویت (کلی): خاموش ❌"
    )
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=glob_lbl, callback_data="prv_glob_tg")],
        [
            InlineKeyboardButton(
                text=f"🎭 تنظیم نام مستعار (فعلی: {user.alias_name or 'تنظیم نشده'})",
                callback_data="prv_alias",
            )
        ],
    ]
    for pref, ib in inbox_prefs[:15]:
        st = "روشن ✅" if pref.allow_requests else "مسدود 🔕"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"صندوق «{ib.title}»: {st}",
                    callback_data=f"prv_ib_tg:{pref.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_super_admin_kb(maintenance_active: bool) -> InlineKeyboardMarkup:
    maint_lbl = "🟢 خروج از حالت تعمیرات" if maintenance_active else "🔧 فعال‌سازی حالت تعمیرات"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 آمار زنده ربات", callback_data="adm_stats"),
                InlineKeyboardButton(text="📣 ارسال پیام همگانی", callback_data="adm_bcast"),
            ],
            [
                InlineKeyboardButton(text="📢 کانال‌های جوین اجباری", callback_data="adm_fch"),
                InlineKeyboardButton(text="👤 جستجو و مدیریت کاربر", callback_data="adm_usr"),
            ],
            [
                InlineKeyboardButton(text=maint_lbl, callback_data="adm_maint_tg"),
            ],
        ]
    )


def build_report_review_kb(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تایید گزارش (ثبت اخطار/بن خودکار فرستنده)",
                    callback_data=f"adm_rep_val:{report_id}:1",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ رد گزارش (بی‌مورد)",
                    callback_data=f"adm_rep_val:{report_id}:0",
                )
            ],
        ]
    )
