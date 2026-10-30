"""تمامی کیبوردهای شیشه‌ای (Inline) و معمولی (Reply) ربات با برچسب‌های فارسی."""

from typing import Dict, List, Sequence, Tuple
from urllib.parse import quote
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.db.models import BlockRecord, ForcedChannel, Inbox, InboxAdmin, User
from app.locales.fa import (
    INBOX_TYPE_LABELS,
    MEDIA_LABELS,
    PERMISSION_LABELS,
    REPORT_REASONS,
    ROLE_PRESET_NAMES,
    Texts,
)


def terms_accept_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ حداقل ۱۸ سال دارم و قوانین را می‌پذیرم", callback_data="accept_terms")]
        ]
    )


def main_menu_kb(is_super_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=Texts.BTN_MY_INBOXES), KeyboardButton(text=Texts.BTN_CREATE_INBOX)],
        [KeyboardButton(text=Texts.BTN_PROFILE), KeyboardButton(text=Texts.BTN_BLACKLIST)],
        [KeyboardButton(text=Texts.BTN_PRIVACY_SETTINGS), KeyboardButton(text=Texts.BTN_HELP)],
    ]
    if is_super_admin:
        rows.append([KeyboardButton(text=Texts.BTN_ADMIN_PANEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=Texts.BTN_CANCEL)]],
        resize_keyboard=True,
    )


def inbox_type_selection_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 صندوق شخصی", callback_data="create_ib_type:personal")],
            [
                InlineKeyboardButton(text="📢 صندوق کانال", callback_data="create_ib_type:channel"),
                InlineKeyboardButton(text="👥 صندوق گروه", callback_data="create_ib_type:group"),
            ],
        ]
    )


def my_inboxes_list_kb(items: List[Tuple[Inbox, bool]]) -> InlineKeyboardMarkup:
    rows = []
    for inbox, is_owner in items:
        status_icon = "🟢" if inbox.is_active else "🔴"
        role_tag = "👑 مالک" if is_owner else "🛡 ادمین"
        type_lbl = INBOX_TYPE_LABELS.get(inbox.type, inbox.type)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status_icon} {inbox.title} ({type_lbl} - {role_tag})",
                    callback_data=f"ib_view:{inbox.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ ساخت صندوق جدید", callback_data="ib_new_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def inbox_manage_kb(inbox: Inbox, is_owner: bool, perms: Dict[str, bool], bot_username: str) -> InlineKeyboardMarkup:
    link = f"https://t.me/{bot_username}?start=ib_{inbox.token}"
    share_url = f"https://t.me/share/url?url={quote(link)}&text={quote(inbox.welcome_text[:100])}"

    rows = [
        [InlineKeyboardButton(text="📤 اشتراک‌گذاری لینک صندوق", url=share_url)],
    ]

    if is_owner or perms.get("manage_settings", False):
        toggle_txt = "🔴 غیرفعال کردن صندوق" if inbox.is_active else "🟢 فعال کردن صندوق"
        rows.append(
            [
                InlineKeyboardButton(text=toggle_txt, callback_data=f"ib_toggle:{inbox.id}"),
                InlineKeyboardButton(text="🎞 فیلتر نوع مدیا", callback_data=f"ib_media:{inbox.id}"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text="✏️ ویرایش عنوان", callback_data=f"ib_edit_title:{inbox.id}"),
                InlineKeyboardButton(text="💬 ویرایش خوش‌آمد", callback_data=f"ib_edit_welcome:{inbox.id}"),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(text="🔖 تنظیم کد کوتاه (Slug)", callback_data=f"ib_edit_slug:{inbox.id}"),
            ]
        )

    if is_owner or perms.get("manage_admins", False):
        rows.append(
            [InlineKeyboardButton(text="👥 مدیریت ادمین‌های صندوق", callback_data=f"ib_admins:{inbox.id}")]
        )

    if is_owner or perms.get("view_history", False):
        rows.append(
            [InlineKeyboardButton(text="📜 لاگ عملکرد ادمین‌ها", callback_data=f"ib_logs:{inbox.id}")]
        )

    if is_owner:
        rows.append(
            [
                InlineKeyboardButton(text="🔄 ریست لینک (ابطال فوری)", callback_data=f"ib_reset_ask:{inbox.id}"),
                InlineKeyboardButton(text="👑 انتقال مالکیت", callback_data=f"ib_transfer:{inbox.id}"),
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="🗑 حذف کامل صندوق", callback_data=f"ib_del_ask:{inbox.id}")]
        )

    rows.append([InlineKeyboardButton(text="🔙 بازگشت به لیست صندوق‌ها", callback_data="ib_list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def inbox_media_filter_kb(inbox_id: int, allowed_media: Dict[str, bool]) -> InlineKeyboardMarkup:
    rows = []
    keys = list(MEDIA_LABELS.keys())
    for i in range(0, len(keys), 2):
        chunk = keys[i : i + 2]
        row = []
        for k in chunk:
            enabled = allowed_media.get(k, True)
            mark = "✅" if enabled else "❌"
            row.append(
                InlineKeyboardButton(
                    text=f"{mark} {MEDIA_LABELS[k]}",
                    callback_data=f"ib_media_tog:{inbox_id}:{k}",
                )
            )
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به صندوق", callback_data=f"ib_view:{inbox_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_reset_link_kb(inbox_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ بله، لینک ریست شود", callback_data=f"ib_reset_do:{inbox_id}"),
                InlineKeyboardButton(text="❌ انصراف", callback_data=f"ib_view:{inbox_id}"),
            ]
        ]
    )


def confirm_delete_inbox_kb(inbox_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🗑 بله، برای همیشه حذف شود", callback_data=f"ib_del_do:{inbox_id}"),
                InlineKeyboardButton(text="❌ انصراف", callback_data=f"ib_view:{inbox_id}"),
            ]
        ]
    )


def inbox_admins_menu_kb(
    inbox: Inbox, admins: Sequence[InboxAdmin], users_map: Dict[int, User]
) -> InlineKeyboardMarkup:
    rows = []
    for adm in admins:
        u = users_map.get(adm.user_id)
        label = u.first_name if u else f"کاربر {adm.user_id}"
        if u and u.username:
            label += f" (@{u.username})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🛡 {label}",
                    callback_data=f"ib_adm_edit:{inbox.id}:{adm.user_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="➕ افزودن با آیدی/فوروارد", callback_data=f"ib_adm_add:{inbox.id}"),
            InlineKeyboardButton(text="🔗 ساخت لینک دعوت یک‌بارمصرف", callback_data=f"ib_adm_inv:{inbox.id}"),
        ]
    )
    if inbox.type in ("channel", "group") and inbox.target_chat_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 همگام‌سازی با ادمین‌های واقعی کانال/گروه",
                    callback_data=f"ib_adm_sync:{inbox.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به صندوق", callback_data=f"ib_view:{inbox.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_permissions_edit_kb(
    inbox_id: int, admin_user_id: int, perms: Dict[str, bool]
) -> InlineKeyboardMarkup:
    rows = []
    # دکمه‌های قالب آماده
    preset_row = [
        InlineKeyboardButton(
            text=ROLE_PRESET_NAMES[p],
            callback_data=f"ib_adm_preset:{inbox_id}:{admin_user_id}:{p}",
        )
        for p in ("observer", "responder", "manager")
    ]
    rows.append(preset_row)

    # تک‌تک دسترسی‌ها
    for perm_key, perm_label in PERMISSION_LABELS.items():
        active = perms.get(perm_key, False)
        icon = "✅" if active else "❌"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {perm_label}",
                    callback_data=f"ib_adm_perm:{inbox_id}:{admin_user_id}:{perm_key}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 حذف این ادمین از صندوق",
                callback_data=f"ib_adm_rm:{inbox_id}:{admin_user_id}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به لیست ادمین‌ها", callback_data=f"ib_admins:{inbox_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def received_message_actions_kb(message_id: int) -> InlineKeyboardMarkup:
    """
    دکمه‌های شیشه‌ای زیر هر پیام دریافتی:
    [💬 پاسخ] [❤️ ریاکشن] [🪪 درخواست هویت] [🚫 بلاک] [⚠️ گزارش]
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💬 پاسخ", callback_data=f"msg_reply:{message_id}"),
                InlineKeyboardButton(text="❤️ ریاکشن", callback_data=f"msg_react_menu:{message_id}"),
                InlineKeyboardButton(text="🪪 درخواست هویت", callback_data=f"msg_reveal_req:{message_id}"),
            ],
            [
                InlineKeyboardButton(text="🚫 بلاک", callback_data=f"msg_block:{message_id}"),
                InlineKeyboardButton(text="⚠️ گزارش", callback_data=f"msg_report_menu:{message_id}"),
            ],
        ]
    )


def sender_post_send_kb(message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=Texts.BTN_DELETE_SENT_MSG,
                    callback_data=f"sender_del_msg:{message_id}",
                )
            ]
        ]
    )


def sender_reply_back_kb(inbox_token: str, parent_msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 پاسخ مجدد به این صندوق",
                    callback_data=f"sender_reply_ib:{inbox_token}:{parent_msg_id}",
                )
            ]
        ]
    )


def reaction_picker_kb(message_id: int, emojis: List[str]) -> InlineKeyboardMarkup:
    emoji_buttons = [
        InlineKeyboardButton(text=em, callback_data=f"msg_react_set:{message_id}:{em}")
        for em in emojis[:8]
    ]
    rows = [
        emoji_buttons,
        [
            InlineKeyboardButton(text="🗑 حذف ریاکشن فعلی", callback_data=f"msg_react_clr:{message_id}"),
            InlineKeyboardButton(text="🔙 بازگشت", callback_data=f"msg_actions_back:{message_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sender_reveal_consent_kb(reveal_request_id: int) -> InlineKeyboardMarkup:
    """
    کیبورد رضایت فرستنده برای نمایش هویت همراه با انتخاب سطح افشا یا رد کامل.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ قبول (فقط نمایش نام من)",
                    callback_data=f"rev_ans:{reveal_request_id}:accept_name",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ قبول (نمایش نام و یوزرنیم)",
                    callback_data=f"rev_ans:{reveal_request_id}:accept_username",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ قبول (لینک مستقیم پروفایل tg://user)",
                    callback_data=f"rev_ans:{reveal_request_id}:accept_link",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ رد درخواست",
                    callback_data=f"rev_ans:{reveal_request_id}:reject",
                ),
                InlineKeyboardButton(
                    text="🚫 رد و بستن درخواست‌ها",
                    callback_data=f"rev_ans:{reveal_request_id}:reject_block",
                ),
            ],
        ]
    )


def report_reasons_kb(message_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"msg_report_do:{message_id}:{key}")]
        for key, label in REPORT_REASONS.items()
    ]
    rows.append([InlineKeyboardButton(text="🔙 انصراف", callback_data=f"msg_actions_back:{message_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def superadmin_report_review_kb(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تایید اعتبار گزارش (اعمال امتیاز منفی/بن خودکار)",
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


def blacklist_manage_kb(blocks: List[Tuple[BlockRecord, Inbox]]) -> InlineKeyboardMarkup:
    rows = []
    for blk, ib in blocks:
        short_h = blk.sender_hash[:8].upper()
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🔓 رفع بلاک #{short_h} از «{ib.title}»",
                    callback_data=f"unblock_id:{blk.id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_privacy_settings_kb(allow_reveal_global: bool, inboxes: List[Tuple[Inbox, bool]]) -> InlineKeyboardMarkup:
    rev_txt = (
        "🟢 دریافت درخواست هویت: روشن (کلیک برای خاموش)"
        if allow_reveal_global
        else "🔴 دریافت درخواست هویت: خاموش (کلیک برای روشن)"
    )
    rows = [[InlineKeyboardButton(text=rev_txt, callback_data="priv_toggle_reveal")]]
    for ib, is_owner in inboxes:
        if is_owner:
            st = "🟢 فعال" if ib.is_active else "🔴 غیرفعال"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"صندوق «{ib.title}»: {st}",
                        callback_data=f"priv_toggle_ib:{ib.id}",
                    )
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def super_admin_panel_kb(
    maintenance: bool,
    filter_links: bool,
) -> InlineKeyboardMarkup:
    maint_txt = "🔴 حالت تعمیرات: روشن" if maintenance else "🟢 حالت تعمیرات: خاموش"
    link_txt = "🟢 فیلتر لینک/آیدی: روشن" if filter_links else "⚪️ فیلتر لینک/آیدی: خاموش"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 آمار زنده ربات", callback_data="sa_stats"),
                InlineKeyboardButton(text="📣 ارسال پیام همگانی", callback_data="sa_broadcast"),
            ],
            [
                InlineKeyboardButton(text="📢 کانال‌های جوین اجباری", callback_data="sa_forced_channels"),
                InlineKeyboardButton(text="👤 جستجو و مدیریت کاربر", callback_data="sa_user_manage"),
            ],
            [
                InlineKeyboardButton(text=maint_txt, callback_data="sa_toggle_maint"),
                InlineKeyboardButton(text=link_txt, callback_data="sa_toggle_links"),
            ],
            [
                InlineKeyboardButton(text="⏱ تنظیم Rate Limit و بن خودکار", callback_data="sa_antispam_cfg"),
            ],
        ]
    )


def forced_channels_kb(channels: Sequence[ForcedChannel]) -> InlineKeyboardMarkup:
    rows = []
    for ch in channels:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 حذف {ch.title} ({ch.chat_id})",
                    callback_data=f"sa_fch_del:{ch.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ افزودن کانال جوین اجباری", callback_data="sa_fch_add")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت به پنل مدیریت", callback_data="sa_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
