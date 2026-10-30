"""تعریف وضعیت‌های ماشین حالت (FSM States) در aiogram 3.x."""

from aiogram.fsm.state import State, StatesGroup


class CreateInboxSG(StatesGroup):
    waiting_target_chat = State()
    waiting_title = State()
    waiting_welcome = State()


class EditInboxSG(StatesGroup):
    waiting_new_title = State()
    waiting_new_welcome = State()
    waiting_new_slug = State()
    waiting_transfer_user = State()
    waiting_new_admin = State()


class MessagingSG(StatesGroup):
    writing_to_inbox = State()
    replying_to_sender = State()


class UserProfileSG(StatesGroup):
    waiting_alias = State()


class SuperAdminSG(StatesGroup):
    waiting_broadcast_content = State()
    waiting_forced_channel = State()
    waiting_user_search = State()
    waiting_antispam_input = State()
