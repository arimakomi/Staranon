"""Initial schema for AnonBot multi-inbox Telegram bot.

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-10-30 10:00:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=False),
        sa.Column("alias_name", sa.String(length=64), nullable=True),
        sa.Column("accepted_terms", sa.Boolean(), nullable=False),
        sa.Column("allow_reveal_global", sa.Boolean(), nullable=False),
        sa.Column("is_banned", sa.Boolean(), nullable=False),
        sa.Column("banned_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ban_reason", sa.String(length=255), nullable=True),
        sa.Column("valid_reports_count", sa.Integer(), nullable=False),
        sa.Column("bot_blocked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=False)
    op.create_index("ix_users_is_banned", "users", ["is_banned"], unique=False)
    op.create_index("ix_users_last_active_at", "users", ["last_active_at"], unique=False)

    op.create_table(
        "inboxes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("target_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=True),
        sa.Column("welcome_text", sa.Text(), nullable=False),
        sa.Column("allowed_media", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inboxes_owner_id", "inboxes", ["owner_id"], unique=False)
    op.create_index("ix_inboxes_target_chat_id", "inboxes", ["target_chat_id"], unique=False)
    op.create_index("ix_inboxes_token", "inboxes", ["token"], unique=True)
    op.create_index("ix_inboxes_slug", "inboxes", ["slug"], unique=True)
    op.create_index("ix_inboxes_is_active", "inboxes", ["is_active"], unique=False)

    op.create_table(
        "inbox_admins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inbox_id", "user_id", name="uq_inbox_admin_user"),
    )
    op.create_index("ix_inbox_admins_inbox_id", "inbox_admins", ["inbox_id"], unique=False)
    op.create_index("ix_inbox_admins_user_id", "inbox_admins", ["user_id"], unique=False)

    op.create_table(
        "admin_invites",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("preset", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_invites_inbox_id", "admin_invites", ["inbox_id"], unique=False)
    op.create_index("ix_admin_invites_token", "admin_invites", ["token"], unique=True)

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("sender_hash", sa.String(length=64), nullable=False),
        sa.Column("sender_enc", sa.Text(), nullable=False),
        sa.Column("sender_tg_msg_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_confirm_msg_id", sa.BigInteger(), nullable=True),
        sa.Column("delivered_tg_ids", sa.JSON(), nullable=False),
        sa.Column("parent_message_id", sa.Integer(), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("active_replier_id", sa.BigInteger(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_inbox_id", "messages", ["inbox_id"], unique=False)
    op.create_index("ix_messages_sender_hash", "messages", ["sender_hash"], unique=False)
    op.create_index("ix_messages_created_at", "messages", ["created_at"], unique=False)
    op.create_index("ix_messages_inbox_sender_hash", "messages", ["inbox_id", "sender_hash"], unique=False)

    op.create_table(
        "reactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("admin_user_id", sa.BigInteger(), nullable=False),
        sa.Column("emoji", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reactions_message_id", "reactions", ["message_id"], unique=True)
    op.create_index("ix_reactions_inbox_id", "reactions", ["inbox_id"], unique=False)

    op.create_table(
        "blocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("sender_hash", sa.String(length=64), nullable=False),
        sa.Column("blocked_by", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inbox_id", "sender_hash", name="uq_inbox_sender_block"),
    )
    op.create_index("ix_blocks_inbox_id", "blocks", ["inbox_id"], unique=False)
    op.create_index("ix_blocks_sender_hash", "blocks", ["sender_hash"], unique=False)

    op.create_table(
        "sender_reveal_prefs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("sender_hash", sa.String(length=64), nullable=False),
        sa.Column("allow_reveal", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("inbox_id", "sender_hash", name="uq_inbox_sender_reveal_pref"),
    )
    op.create_index("ix_sender_reveal_prefs_inbox_id", "sender_reveal_prefs", ["inbox_id"], unique=False)
    op.create_index("ix_sender_reveal_prefs_sender_hash", "sender_reveal_prefs", ["sender_hash"], unique=False)

    op.create_table(
        "reveal_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("requester_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reveal_requests_message_id", "reveal_requests", ["message_id"], unique=True)
    op.create_index("ix_reveal_requests_inbox_id", "reveal_requests", ["inbox_id"], unique=False)
    op.create_index("ix_reveal_requests_requester_id", "reveal_requests", ["requester_id"], unique=False)
    op.create_index("ix_reveal_requests_sender_hash", "reveal_requests", ["sender_hash"], unique=False)
    op.create_index("ix_reveal_requests_created_at", "reveal_requests", ["created_at"], unique=False)

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("reporter_id", sa.BigInteger(), nullable=False),
        sa.Column("sender_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reports_message_id", "reports", ["message_id"], unique=False)
    op.create_index("ix_reports_inbox_id", "reports", ["inbox_id"], unique=False)
    op.create_index("ix_reports_sender_hash", "reports", ["sender_hash"], unique=False)
    op.create_index("ix_reports_status", "reports", ["status"], unique=False)

    op.create_table(
        "inbox_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("inbox_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("details", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inbox_id"], ["inboxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inbox_logs_inbox_id", "inbox_logs", ["inbox_id"], unique=False)
    op.create_index("ix_inbox_logs_created_at", "inbox_logs", ["created_at"], unique=False)

    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "forced_channels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("invite_link", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
    )

    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("admin_id", sa.BigInteger(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("total_users", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("blocked_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("broadcasts")
    op.drop_table("forced_channels")
    op.drop_table("settings")
    op.drop_table("inbox_logs")
    op.drop_table("reports")
    op.drop_table("reveal_requests")
    op.drop_table("sender_reveal_prefs")
    op.drop_table("blocks")
    op.drop_table("reactions")
    op.drop_table("messages")
    op.drop_table("admin_invites")
    op.drop_table("inbox_admins")
    op.drop_table("inboxes")
    op.drop_table("users")
