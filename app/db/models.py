"""مدل‌های دیتابیس SQLAlchemy 2.x (Async) برای ربات صندوق پیام ناشناس چندگانه."""

from datetime import datetime, timezone
from typing import Dict, Optional
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_ALLOWED_MEDIA: Dict[str, bool] = {
    "text": True,
    "photo": True,
    "video": True,
    "voice": True,
    "document": True,
    "animation": True,
    "sticker": True,
}


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(128), default="کاربر")
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    alias_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    accepted_terms: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_reveal_global: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    banned_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ban_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    valid_reports_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class Inbox(Base):
    __tablename__ = "inboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), default="personal", nullable=False)
    target_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    welcome_text: Mapped[str] = mapped_column(
        Text,
        default="👋 پیامت رو به صورت کاملاً ناشناس بنویس و ارسال کن؛ هویتت در هیچ مرحله‌ای مشخص نمیشه:",
        nullable=False,
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    custom_slug: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    filter_links: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allowed_media: Mapped[Dict[str, bool]] = mapped_column(JSON, default=lambda: dict(DEFAULT_ALLOWED_MEDIA), nullable=False)

    messages_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replies_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InboxAdmin(Base):
    __tablename__ = "inbox_admins"
    __table_args__ = (UniqueConstraint("inbox_id", "user_id", name="uq_inbox_admin_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    added_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role_preset: Mapped[str] = mapped_column(String(32), default="responder", nullable=False)

    receive_messages: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reply: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    react: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    delete_msg: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    block_user: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    request_reveal: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    edit_welcome: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    manage_admins: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AdminInvite(Base):
    __tablename__ = "admin_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    role_preset: Mapped[str] = mapped_column(String(32), default="responder", nullable=False)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_inbox_sender_hash", "inbox_id", "sender_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sender_enc: Mapped[str] = mapped_column(Text, nullable=False)
    sender_tg_msg_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivered_tg_ids: Mapped[Dict[str, int]] = mapped_column(JSON, default=dict, nullable=False)
    reply_to_record_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_replied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    replied_by_admin_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    replying_admin_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class ReactionRecord(Base):
    __tablename__ = "reactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False)
    admin_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BlockRecord(Base):
    __tablename__ = "blocks"
    __table_args__ = (UniqueConstraint("inbox_id", "sender_hash", name="uq_block_inbox_sender"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    blocked_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class RevealRequest(Base):
    __tablename__ = "reveal_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), unique=True, nullable=False)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_by_admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    reveal_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SenderRevealPreference(Base):
    __tablename__ = "sender_reveal_prefs"
    __table_args__ = (UniqueConstraint("inbox_id", "sender_hash", name="uq_reveal_pref_inbox_sender"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False)
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    allow_requests: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ReportRecord(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, index=True)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False)
    sender_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reported_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False, index=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InboxLog(Base):
    __tablename__ = "inbox_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(Integer, ForeignKey("inboxes.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ForcedChannel(Base):
    __tablename__ = "forced_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    invite_link: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BroadcastJob(Base):
    __tablename__ = "broadcast_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    text_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    total_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
