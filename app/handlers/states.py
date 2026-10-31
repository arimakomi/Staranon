"""تعریف تمامی وضعیت‌های FSM (ماشین حالت) ربات."""

from aiogram.fsm.state import State, StatesGroup


class CreateInboxState(StatesGroup):
    waiting_chat_identifier = State()
    waiting_title = State()
    waiting_welcome = State()


class EditInboxState(StatesGroup):
    waiting_new_welcome = State()
    waiting_new_title = State()
    waiting_custom_slug = State()
    waiting_admin_identifier = State()


class WritingMessageState(StatesGroup):
    waiting_anonymous_message = State()
    waiting_sender_followup = State()


class AdminReplyState(StatesGroup):
    waiting_reply_content = State()


class ReportMessageState(StatesGroup):
    waiting_report_reason = State()


class PrivacyAliasState(StatesGroup):
    waiting_alias = State()


class SuperAdminState(StatesGroup):
    waiting_broadcast_content = State()
    waiting_forced_channel_id = State()
    waiting_forced_channel_title = State()
    waiting_forced_channel_link = State()
    waiting_user_search = State()
    waiting_ban_reason = State()
