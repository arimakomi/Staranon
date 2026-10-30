"""مدل‌های کامل دیتابیس SQLAlchemy 2.x (Async) برای تمامی جداول ربات صندوق پیام ناشناس."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # Telegram User ID
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    first_name: Mapped[str] = mapped_column(String(128), default="کاربر")
    alias_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    accepted_terms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_reveal_global: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    banned_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ban_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    valid_reports_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, index=True
    )

    inboxes: Mapped[List["Inbox"]] = relationship("Inbox", back_populates="owner", cascade="all, delete-orphan")


class Inbox(Base):
    __tablename__ = "inboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(16), default="personal", nullable=False)  # personal | channel | group
    target_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    slug: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True, index=True)
    welcome_text: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_media: Mapped[Dict[str, bool]] = mapped_column(
        JSON,
        default=lambda: {
            "text": True,
            "photo": True,
            "video": True,
            "voice": True,
            "document": True,
            "animation": True,
            "sticker": True,
        },
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    owner: Mapped["User"] = relationship("User", back_populates="inboxes")
    admins: Mapped[List["InboxAdmin"]] = relationship(
        "InboxAdmin", back_populates="inbox", cascade="all, delete-orphan"
    )
    messages: Mapped[List["MessageRecord"]] = relationship(
        "MessageRecord", back_populates="inbox", cascade="all, delete-orphan"
    )


class InboxAdmin(Base):
    __tablename__ = "inbox_admins"
    __table_args__ = (UniqueConstraint("inbox_id", "user_id", name="uq_inbox_admin_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permissions: Mapped[Dict[str, bool]] = mapped_column(
        JSON,
        default=lambda: {
            "receive_messages": True,
            "reply": True,
            "react": True,
            "block": True,
            "request_reveal": True,
            "view_history": False,
            "manage_settings": False,
            "manage_admins": False,
        },
        nullable=False,
    )
    added_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    inbox: Mapped["Inbox"] = relationship("Inbox", back_populates="admins")


class AdminInvite(Base):
    """لینک‌های دعوت یک‌بارمصرف برای افزودن ادمین به صندوق."""

    __tablename__ = "admin_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    preset: Mapped[str] = mapped_column(String(32), default="responder", nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_inbox_sender_hash", "inbox_id", "sender_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sender_enc: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet encrypted sender user_id

    sender_tg_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_confirm_msg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # نگاشت آیدی چت گیرنده (مالک/ادمین‌ها) به آیدی پیام کپی‌شده: {"123456": 890}
    delivered_tg_ids: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    parent_message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(
        String(16), default="inbound", nullable=False
    )  # inbound (از فرستنده به صندوق) | outbound (پاسخ صندوق به فرستنده)
    media_type: Mapped[str] = mapped_column(String(32), default="text", nullable=False)
    active_replier_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    inbox: Mapped["Inbox"] = relationship("Inbox", back_populates="messages")


class ReactionRecord(Base):
    __tablename__ = "reactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    admin_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    emoji: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class BlockRecord(Base):
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("inbox_id", "sender_hash", name="uq_inbox_sender_block"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    blocked_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class SenderRevealPreference(Base):
    """تنظیم فرستنده برای بستن درخواست هویت از یک صندوق خاص."""

    __tablename__ = "sender_reveal_prefs"
    __table_args__ = (UniqueConstraint("inbox_id", "sender_hash", name="uq_inbox_sender_reveal_pref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    allow_reveal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RevealRequest(Base):
    """
    جدول درخواست‌های نمایش هویت.
    طبق قوانین امنیتی، هویت فرستنده هرگز دائمی در این جدول ذخیره نمی‌شود؛
    فقط وضعیت درخواست (pending / accepted / rejected) ثبت می‌گردد.
    """

    __tablename__ = "reveal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requester_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )  # pending | accepted | rejected
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ReportRecord(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reporter_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="open", nullable=False, index=True
    )  # open | valid | dismissed
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class InboxLog(Base):
    """ثبت تمامی اقدامات ادمین‌های صندوق برای مشاهده توسط مالک صندوق."""

    __tablename__ = "inbox_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class SystemSetting(Base):
    """تنظیمات پویا و قابل ویرایش از پنل ادمین کل در تلگرام."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ForcedChannel(Base):
    """کانال‌های جوین اجباری."""

    __tablename__ = "forced_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # @channel or -100...
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    invite_link: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BroadcastJob(Base):
    """صف و وضعیت پیام‌های همگانی ادمین کل."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="text", nullable=False)  # text | copy
    text_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    total_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="running", nullable=False
    )  # running | completed | cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
