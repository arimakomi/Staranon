"""لایه دسترسی به داده‌ها (Repository) و عملیات دیتابیس."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AdminInvite,
    BlockRecord,
    BroadcastJob,
    ForcedChannel,
    Inbox,
    InboxAdmin,
    InboxLog,
    MessageRecord,
    ReactionRecord,
    ReportRecord,
    RevealRequest,
    SenderRevealPreference,
    SystemSetting,
    User,
    utcnow,
)
from app.locales.fa import ROLE_PRESETS
from app.utils.crypto import CryptoManager


class BotRepository:
    def __init__(self, session: AsyncSession, crypto: CryptoManager) -> None:
        self.session = session
        self.crypto = crypto

    # ------------------------------------------------------------------
    # مدیریت کاربران (Users)
    # ------------------------------------------------------------------
    async def upsert_user(
        self,
        user_id: int,
        first_name: str,
        username: Optional[str] = None,
    ) -> User:
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        now = utcnow()
        if user is None:
            user = User(
                id=user_id,
                first_name=(first_name or "کاربر")[:128],
                username=username[:64] if username else None,
                accepted_terms=False,
                allow_reveal_global=True,
                is_banned=False,
                valid_reports_count=0,
                bot_blocked=False,
                created_at=now,
                last_active_at=now,
            )
            self.session.add(user)
        else:
            user.first_name = (first_name or user.first_name)[:128]
            user.username = username[:64] if username else None
            user.last_active_at = now
            user.bot_blocked = False
            if user.is_banned and user.banned_until:
                until = user.banned_until
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                if now >= until:
                    user.is_banned = False
                    user.banned_until = None
                    user.ban_reason = None
        await self.session.commit()
        await self.session.refresh(user)
        await self.get_or_create_personal_inbox(user)
        return user

    async def get_user(self, user_id: int) -> Optional[User]:
        res = await self.session.execute(select(User).where(User.id == user_id))
        return res.scalar_one_or_none()

    async def find_user_by_username_or_id(self, query: str) -> Optional[User]:
        q = query.strip().lstrip("@")
        if q.lstrip("-").isdigit():
            return await self.get_user(int(q))
        res = await self.session.execute(
            select(User).where(func.lower(User.username) == q.lower())
        )
        return res.scalar_one_or_none()

    async def accept_terms(self, user_id: int) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(accepted_terms=True)
        )
        await self.session.commit()

    async def set_user_alias(self, user_id: int, alias: Optional[str]) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(alias_name=alias)
        )
        await self.session.commit()

    async def toggle_global_reveal(self, user_id: int) -> bool:
        user = await self.get_user(user_id)
        if not user:
            return True
        user.allow_reveal_global = not user.allow_reveal_global
        await self.session.commit()
        return user.allow_reveal_global

    async def ban_user(
        self,
        user_id: int,
        reason: str = "نقض قوانین ربات",
        duration_hours: Optional[int] = None,
    ) -> Optional[User]:
        user = await self.get_user(user_id)
        if not user:
            return None
        user.is_banned = True
        user.ban_reason = reason[:255]
        if duration_hours and duration_hours > 0:
            user.banned_until = utcnow() + timedelta(hours=duration_hours)
        else:
            user.banned_until = None
        await self.session.commit()
        return user

    async def unban_user(self, user_id: int) -> Optional[User]:
        user = await self.get_user(user_id)
        if not user:
            return None
        user.is_banned = False
        user.banned_until = None
        user.ban_reason = None
        await self.session.commit()
        return user

    # ------------------------------------------------------------------
    # مدیریت صندوق‌های ناشناس (Inboxes)
    # ------------------------------------------------------------------
    async def create_inbox(
        self,
        owner_id: int,
        inbox_type: str,
        title: str,
        welcome_text: Optional[str] = None,
        target_chat_id: Optional[int] = None,
        filter_links: bool = False,
    ) -> Inbox:
        token = CryptoManager.generate_inbox_token()
        inbox = Inbox(
            owner_id=owner_id,
            type=inbox_type,
            target_chat_id=target_chat_id,
            title=title[:128],
            welcome_text=welcome_text
            or "👋 پیامت رو به صورت کاملاً ناشناس بنویس و ارسال کن؛ هویتت در هیچ مرحله‌ای مشخص نمیشه:",
            token=token,
            is_active=True,
            filter_links=filter_links,
        )
        self.session.add(inbox)
        await self.session.commit()
        await self.session.refresh(inbox)
        await self.log_inbox_action(inbox.id, owner_id, "create_inbox", f"ساخت صندوق «{inbox.title}»")
        return inbox

    async def get_or_create_personal_inbox(self, user: User) -> Inbox:
        stmt = (
            select(Inbox)
            .where(Inbox.owner_id == user.id, Inbox.type == "personal")
            .order_by(Inbox.id.asc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing is not None:
            return existing
        default_title = f"صندوق شخصی {user.first_name or 'من'}"[:128]
        return await self.create_inbox(
            owner_id=user.id,
            inbox_type="personal",
            title=default_title,
        )

    async def get_inbox_by_id(self, inbox_id: int) -> Optional[Inbox]:
        res = await self.session.execute(select(Inbox).where(Inbox.id == inbox_id))
        return res.scalar_one_or_none()

    async def get_inbox_by_token_or_slug(self, code: str) -> Optional[Inbox]:
        cleaned = code.strip()
        if cleaned.startswith("ib_"):
            raw_token = cleaned[3:]
            stmt = select(Inbox).where(or_(Inbox.token == raw_token, Inbox.custom_slug == raw_token))
        else:
            stmt = select(Inbox).where(or_(Inbox.token == cleaned, Inbox.custom_slug == cleaned))
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_user_owned_inboxes(self, user_id: int) -> Sequence[Inbox]:
        stmt = select(Inbox).where(Inbox.owner_id == user_id).order_by(Inbox.id.desc())
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def get_user_admin_inboxes(self, user_id: int) -> Sequence[Inbox]:
        stmt = (
            select(Inbox)
            .join(InboxAdmin, InboxAdmin.inbox_id == Inbox.id)
            .where(InboxAdmin.user_id == user_id)
            .order_by(Inbox.id.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def get_user_manageable_inboxes(self, user_id: int) -> List[Inbox]:
        owned = list(await self.get_user_owned_inboxes(user_id))
        admin_of = list(await self.get_user_admin_inboxes(user_id))
        seen = {ib.id for ib in owned}
        for ib in admin_of:
            if ib.id not in seen:
                owned.append(ib)
                seen.add(ib.id)
        return owned

    async def reset_inbox_token(self, inbox_id: int, actor_id: int) -> Optional[str]:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return None
        new_token = CryptoManager.generate_inbox_token()
        inbox.token = new_token
        await self.session.commit()
        await self.log_inbox_action(inbox_id, actor_id, "reset_link", "ریست لینک صندوق و ابطال توکن قبلی")
        return new_token

    async def set_inbox_slug(self, inbox_id: int, slug: Optional[str], actor_id: int) -> Tuple[bool, str]:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return False, "صندوق یافت نشد."
        if slug:
            existing = await self.session.execute(
                select(Inbox).where(
                    and_slug := (func.lower(Inbox.custom_slug) == slug.lower()),
                    Inbox.id != inbox_id,
                )
            )
            if existing.scalar_one_or_none() is not None:
                return False, "این کد کوتاه (Slug) قبلاً توسط صندوق دیگری انتخاب شده است."
        inbox.custom_slug = slug
        await self.session.commit()
        await self.log_inbox_action(inbox_id, actor_id, "set_slug", f"تنظیم کد کوتاه: {slug}")
        return True, "کد کوتاه اختصاصی با موفقیت ذخیره شد."

    async def toggle_inbox_active(self, inbox_id: int, actor_id: int) -> Optional[bool]:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return None
        inbox.is_active = not inbox.is_active
        await self.session.commit()
        state_str = "فعال" if inbox.is_active else "غیرفعال"
        await self.log_inbox_action(inbox_id, actor_id, "toggle_active", f"تغییر وضعیت صندوق به {state_str}")
        return inbox.is_active

    async def toggle_inbox_link_filter(self, inbox_id: int, actor_id: int) -> Optional[bool]:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return None
        inbox.filter_links = not inbox.filter_links
        await self.session.commit()
        return inbox.filter_links

    async def update_inbox_welcome(self, inbox_id: int, welcome_text: str, actor_id: int) -> bool:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return False
        inbox.welcome_text = welcome_text[:1500]
        await self.session.commit()
        await self.log_inbox_action(inbox_id, actor_id, "edit_welcome", "ویرایش پیام خوش‌آمد صندوق")
        return True

    async def update_inbox_title(self, inbox_id: int, title: str, actor_id: int) -> bool:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return False
        inbox.title = title[:128]
        await self.session.commit()
        await self.log_inbox_action(inbox_id, actor_id, "edit_title", f"تغییر عنوان به {inbox.title}")
        return True

    async def toggle_inbox_media_filter(self, inbox_id: int, media_key: str, actor_id: int) -> Optional[Dict[str, bool]]:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return None
        current = dict(inbox.allowed_media or {})
        current[media_key] = not current.get(media_key, True)
        inbox.allowed_media = current
        await self.session.commit()
        await self.log_inbox_action(inbox_id, actor_id, "toggle_media", f"تغییر فیلتر رسانه {media_key}")
        return current

    async def delete_inbox(self, inbox_id: int, owner_id: int) -> bool:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox or inbox.owner_id != owner_id:
            return False
        await self.session.delete(inbox)
        await self.session.commit()
        return True

    # ------------------------------------------------------------------
    # مدیریت ادمین‌های صندوق و دسترسی‌ها (RBAC)
    # ------------------------------------------------------------------
    async def get_inbox_admin_record(self, inbox_id: int, user_id: int) -> Optional[InboxAdmin]:
        stmt = select(InboxAdmin).where(
            InboxAdmin.inbox_id == inbox_id,
            InboxAdmin.user_id == user_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def check_inbox_permission(self, inbox_id: int, user_id: int, perm_name: str) -> bool:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return False
        if inbox.owner_id == user_id:
            return True
        adm = await self.get_inbox_admin_record(inbox_id, user_id)
        if not adm:
            return False
        return bool(getattr(adm, perm_name, False))

    async def list_inbox_admins(self, inbox_id: int) -> List[Tuple[InboxAdmin, Optional[User]]]:
        stmt = (
            select(InboxAdmin, User)
            .outerjoin(User, User.id == InboxAdmin.user_id)
            .where(InboxAdmin.inbox_id == inbox_id)
            .order_by(InboxAdmin.id.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.all())

    async def add_or_update_inbox_admin(
        self,
        inbox_id: int,
        target_user_id: int,
        added_by: int,
        preset: str = "responder",
    ) -> InboxAdmin:
        perms = ROLE_PRESETS.get(preset, ROLE_PRESETS["responder"])
        adm = await self.get_inbox_admin_record(inbox_id, target_user_id)
        if adm is None:
            adm = InboxAdmin(
                inbox_id=inbox_id,
                user_id=target_user_id,
                added_by=added_by,
                role_preset=preset,
                **perms,
            )
            self.session.add(adm)
        else:
            adm.role_preset = preset
            for k, v in perms.items():
                setattr(adm, k, v)
        await self.session.commit()
        await self.session.refresh(adm)
        await self.log_inbox_action(
            inbox_id,
            added_by,
            "set_admin",
            f"تنظیم ادمین {target_user_id} با قالب {preset}",
        )
        return adm

    async def toggle_admin_permission(
        self,
        inbox_id: int,
        admin_user_id: int,
        perm_key: str,
        actor_id: int,
    ) -> Optional[InboxAdmin]:
        adm = await self.get_inbox_admin_record(inbox_id, admin_user_id)
        if not adm or not hasattr(adm, perm_key):
            return None
        current_val = bool(getattr(adm, perm_key))
        setattr(adm, perm_key, not current_val)
        adm.role_preset = "custom"
        await self.session.commit()
        await self.session.refresh(adm)
        await self.log_inbox_action(
            inbox_id,
            actor_id,
            "toggle_perm",
            f"تغییر دسترسی {perm_key} برای ادمین {admin_user_id}",
        )
        return adm

    async def remove_inbox_admin(self, inbox_id: int, admin_user_id: int, actor_id: int) -> bool:
        adm = await self.get_inbox_admin_record(inbox_id, admin_user_id)
        if not adm:
            return False
        await self.session.delete(adm)
        await self.session.commit()
        await self.log_inbox_action(inbox_id, actor_id, "remove_admin", f"حذف ادمین {admin_user_id}")
        return True

    async def get_authorized_recipient_ids(self, inbox_id: int) -> List[int]:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return []
        recipients = [inbox.owner_id]
        stmt = select(InboxAdmin.user_id).where(
            InboxAdmin.inbox_id == inbox_id,
            InboxAdmin.receive_messages == True,  # noqa: E712
        )
        res = await self.session.execute(stmt)
        for uid in res.scalars().all():
            if uid not in recipients:
                recipients.append(uid)
        return recipients

    async def create_admin_invite(
        self,
        inbox_id: int,
        created_by: int,
        preset: str = "responder",
        valid_hours: int = 24,
    ) -> AdminInvite:
        token = CryptoManager.generate_invite_token()
        invite = AdminInvite(
            inbox_id=inbox_id,
            token=token,
            role_preset=preset,
            created_by=created_by,
            is_used=False,
            expires_at=utcnow() + timedelta(hours=valid_hours),
        )
        self.session.add(invite)
        await self.session.commit()
        await self.session.refresh(invite)
        return invite

    async def consume_admin_invite(self, token: str, user_id: int) -> Optional[Inbox]:
        raw = token[4:] if token.startswith("inv_") else token
        stmt = select(AdminInvite).where(AdminInvite.token == raw)
        res = await self.session.execute(stmt)
        inv = res.scalar_one_or_none()
        if not inv or inv.is_used:
            return None
        exp = inv.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if utcnow() > exp:
            return None

        inbox = await self.get_inbox_by_id(inv.inbox_id)
        if not inbox:
            return None
        inv.is_used = True
        inv.used_by = user_id
        if inbox.owner_id != user_id:
            await self.add_or_update_inbox_admin(
                inbox_id=inbox.id,
                target_user_id=user_id,
                added_by=inv.created_by,
                preset=inv.role_preset,
            )
        await self.session.commit()
        return inbox

    async def log_inbox_action(
        self,
        inbox_id: int,
        actor_id: int,
        action: str,
        details: Optional[str] = None,
    ) -> None:
        log_entry = InboxLog(
            inbox_id=inbox_id,
            actor_id=actor_id,
            action=action[:64],
            details=details,
        )
        self.session.add(log_entry)
        await self.session.commit()

    async def get_inbox_logs(self, inbox_id: int, limit: int = 15) -> Sequence[InboxLog]:
        stmt = (
            select(InboxLog)
            .where(InboxLog.inbox_id == inbox_id)
            .order_by(InboxLog.id.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    # ------------------------------------------------------------------
    # مدیریت پیام‌ها، حذف ۵ دقیقه‌ای، پاسخ دوطرفه و ریاکشن‌ها
    # ------------------------------------------------------------------
    async def save_message(
        self,
        inbox_id: int,
        sender_user_id: int,
        sender_tg_msg_id: int,
        delivered_tg_ids: Dict[str, int],
        reply_to_record_id: Optional[int] = None,
        message_text: Optional[str] = None,
        media_type: str = "text",
    ) -> MessageRecord:
        sender_hash = self.crypto.compute_sender_hash(inbox_id, sender_user_id)
        sender_enc = self.crypto.encrypt_sender_id(sender_user_id)
        rec = MessageRecord(
            inbox_id=inbox_id,
            sender_hash=sender_hash,
            sender_enc=sender_enc,
            sender_tg_msg_id=sender_tg_msg_id,
            delivered_tg_ids=delivered_tg_ids,
            media_type=media_type,
            message_text=message_text,
            reply_to_record_id=reply_to_record_id,
            is_deleted=False,
            is_replied=False,
        )
        self.session.add(rec)
        await self.session.execute(
            update(Inbox)
            .where(Inbox.id == inbox_id)
            .values(messages_count=Inbox.messages_count + 1)
        )
        await self.session.commit()
        await self.session.refresh(rec)
        return rec

    async def search_messages_global(
        self,
        query: str,
        limit: int = 10,
    ) -> List[Tuple[MessageRecord, Inbox, Optional[User], Optional[User], int]]:
        """
        جستجوی سراسری پیام در تمامی صندوق‌ها برای مدیریت کل:
        بر اساس متن پیام، شماره رکورد پیام (#ID) یا شناسه پیام تلگرام،
        همراه با رمزگشایی آیدی فرستنده و واکشی مشخصات فرستنده و گیرنده (صاحب صندوق).
        """
        q = (query or "").strip()
        if not q:
            return []

        conditions = []
        numeric_q = q.lstrip("#")
        if numeric_q.isdigit():
            num_val = int(numeric_q)
            conditions.append(MessageRecord.id == num_val)
            conditions.append(MessageRecord.sender_tg_msg_id == num_val)

        conditions.append(MessageRecord.message_text.ilike(f"%{q}%"))

        stmt = (
            select(MessageRecord, Inbox)
            .join(Inbox, Inbox.id == MessageRecord.inbox_id)
            .where(or_(*conditions))
            .order_by(MessageRecord.id.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        rows = list(res.all())

        # اگر کاربر عدد فرستاده و در ستون JSON delivered_tg_ids بود نیز بررسی شود
        if not rows and numeric_q.isdigit():
            recent_stmt = (
                select(MessageRecord, Inbox)
                .join(Inbox, Inbox.id == MessageRecord.inbox_id)
                .order_by(MessageRecord.id.desc())
                .limit(200)
            )
            recent_res = await self.session.execute(recent_stmt)
            target_num = int(numeric_q)
            for m_rec, ib_rec in recent_res.all():
                if target_num in (m_rec.delivered_tg_ids or {}).values():
                    rows.append((m_rec, ib_rec))
                    break

        results: List[Tuple[MessageRecord, Inbox, Optional[User], Optional[User], int]] = []
        for msg_rec, inbox_rec in rows:
            try:
                sender_uid = self.crypto.decrypt_sender_id(msg_rec.sender_enc)
            except Exception:
                sender_uid = 0
            sender_user = await self.get_user(sender_uid) if sender_uid else None
            receiver_user = await self.get_user(inbox_rec.owner_id)
            results.append((msg_rec, inbox_rec, receiver_user, sender_user, sender_uid))

        return results

    async def get_recent_messages_global(
        self,
        limit: int = 50,
    ) -> List[Tuple[MessageRecord, Inbox, Optional[User], Optional[User], int]]:
        """واکشی آخرین پیام‌های رد و بدل شده در کل ربات برای مدیریت کل."""
        stmt = (
            select(MessageRecord, Inbox)
            .join(Inbox, Inbox.id == MessageRecord.inbox_id)
            .order_by(MessageRecord.id.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        rows = list(res.all())

        results: List[Tuple[MessageRecord, Inbox, Optional[User], Optional[User], int]] = []
        for msg_rec, inbox_rec in rows:
            try:
                sender_uid = self.crypto.decrypt_sender_id(msg_rec.sender_enc)
            except Exception:
                sender_uid = 0
            sender_user = await self.get_user(sender_uid) if sender_uid else None
            receiver_user = await self.get_user(inbox_rec.owner_id)
            results.append((msg_rec, inbox_rec, receiver_user, sender_user, sender_uid))

        return results

    async def get_message(self, message_id: int) -> Optional[MessageRecord]:
        res = await self.session.execute(
            select(MessageRecord).where(MessageRecord.id == message_id)
        )
        return res.scalar_one_or_none()

    async def mark_message_deleted(self, message_id: int) -> Optional[MessageRecord]:
        msg = await self.get_message(message_id)
        if not msg:
            return None
        msg.is_deleted = True
        await self.session.commit()
        return msg

    async def set_replying_admin(self, message_id: int, admin_id: Optional[int]) -> Optional[MessageRecord]:
        msg = await self.get_message(message_id)
        if not msg:
            return None
        msg.replying_admin_id = admin_id
        await self.session.commit()
        return msg

    async def mark_message_replied(self, message_id: int, admin_id: int) -> Optional[MessageRecord]:
        msg = await self.get_message(message_id)
        if not msg:
            return None
        msg.is_replied = True
        msg.replied_by_admin_id = admin_id
        msg.replying_admin_id = None
        await self.session.execute(
            update(Inbox)
            .where(Inbox.id == msg.inbox_id)
            .values(replies_count=Inbox.replies_count + 1)
        )
        await self.session.commit()
        await self.log_inbox_action(
            msg.inbox_id,
            admin_id,
            "reply_message",
            f"پاسخ به پیام #{msg.id} (کد {CryptoManager.short_hash(msg.sender_hash)})",
        )
        return msg

    async def set_or_toggle_reaction(
        self,
        message_id: int,
        inbox_id: int,
        admin_user_id: int,
        emoji: str,
    ) -> Optional[str]:
        stmt = select(ReactionRecord).where(
            ReactionRecord.message_id == message_id,
            ReactionRecord.admin_user_id == admin_user_id,
        )
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing and existing.emoji == emoji:
            await self.session.delete(existing)
            await self.session.commit()
            await self.log_inbox_action(inbox_id, admin_user_id, "remove_reaction", f"حذف واکنش از پیام #{message_id}")
            return None
        if existing:
            existing.emoji = emoji
        else:
            self.session.add(
                ReactionRecord(
                    message_id=message_id,
                    inbox_id=inbox_id,
                    admin_user_id=admin_user_id,
                    emoji=emoji,
                )
            )
        await self.session.commit()
        await self.log_inbox_action(inbox_id, admin_user_id, "add_reaction", f"ثبت واکنش {emoji} روی پیام #{message_id}")
        return emoji

    # ------------------------------------------------------------------
    # مدیریت بلاک و لیست سیاه (Blocks)
    # ------------------------------------------------------------------
    async def is_sender_blocked(self, inbox_id: int, sender_hash: str) -> bool:
        stmt = select(BlockRecord.id).where(
            BlockRecord.inbox_id == inbox_id,
            BlockRecord.sender_hash == sender_hash,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def block_sender(
        self,
        inbox_id: int,
        sender_hash: str,
        actor_id: int,
        note: Optional[str] = None,
    ) -> BlockRecord:
        stmt = select(BlockRecord).where(
            BlockRecord.inbox_id == inbox_id,
            BlockRecord.sender_hash == sender_hash,
        )
        res = await self.session.execute(stmt)
        rec = res.scalar_one_or_none()
        if rec is None:
            rec = BlockRecord(
                inbox_id=inbox_id,
                sender_hash=sender_hash,
                blocked_by=actor_id,
                note=note or f"کاربر #{CryptoManager.short_hash(sender_hash)}",
            )
            self.session.add(rec)
            await self.session.commit()
            await self.session.refresh(rec)
            await self.log_inbox_action(
                inbox_id,
                actor_id,
                "block_sender",
                f"مسدودسازی فرستنده #{CryptoManager.short_hash(sender_hash)}",
            )
        return rec

    async def unblock_sender_by_id(self, block_id: int, actor_id: int) -> Optional[BlockRecord]:
        res = await self.session.execute(select(BlockRecord).where(BlockRecord.id == block_id))
        rec = res.scalar_one_or_none()
        if not rec:
            return None
        inbox_id = rec.inbox_id
        short_code = CryptoManager.short_hash(rec.sender_hash)
        await self.session.delete(rec)
        await self.session.commit()
        await self.log_inbox_action(inbox_id, actor_id, "unblock_sender", f"رفع مسدودیت #{short_code}")
        return rec

    async def unblock_sender_by_hash(self, inbox_id: int, sender_hash: str, actor_id: int) -> bool:
        res = await self.session.execute(
            select(BlockRecord).where(
                BlockRecord.inbox_id == inbox_id,
                BlockRecord.sender_hash == sender_hash,
            )
        )
        rec = res.scalar_one_or_none()
        if not rec:
            return False
        await self.session.delete(rec)
        await self.session.commit()
        await self.log_inbox_action(
            inbox_id,
            actor_id,
            "unblock_sender",
            f"رفع مسدودیت #{CryptoManager.short_hash(sender_hash)}",
        )
        return True

    async def list_user_blocks(self, owner_id: int) -> List[Tuple[BlockRecord, Inbox]]:
        stmt = (
            select(BlockRecord, Inbox)
            .join(Inbox, Inbox.id == BlockRecord.inbox_id)
            .where(Inbox.owner_id == owner_id)
            .order_by(BlockRecord.id.desc())
            .limit(40)
        )
        res = await self.session.execute(stmt)
        return list(res.all())

    # ------------------------------------------------------------------
    # درخواست نمایش هویت با رضایت صریح فرستنده (Reveal Requests)
    # ------------------------------------------------------------------
    async def can_request_reveal(
        self,
        msg: MessageRecord,
        admin_user_id: int,
        daily_limit: int,
        cooldown_seconds: int,
    ) -> Tuple[bool, str]:
        existing_msg_req = await self.session.execute(
            select(RevealRequest).where(RevealRequest.message_id == msg.id)
        )
        if existing_msg_req.scalar_one_or_none() is not None:
            return False, "⚠️ برای این پیام قبلاً یک بار درخواست نمایش هویت ارسال شده است."

        sender_uid = self.crypto.decrypt_sender_id(msg.sender_enc)
        sender_user = await self.get_user(sender_uid)
        if sender_user and not sender_user.allow_reveal_global:
            return False, "🔒 این فرستنده دریافت درخواست نمایش هویت را در تنظیمات حریم خصوصی خود بسته است."

        pref_res = await self.session.execute(
            select(SenderRevealPreference).where(
                SenderRevealPreference.inbox_id == msg.inbox_id,
                SenderRevealPreference.sender_hash == msg.sender_hash,
            )
        )
        pref = pref_res.scalar_one_or_none()
        if pref and not pref.allow_requests:
            return False, "🔒 این فرستنده دریافت درخواست نمایش هویت از طرف این صندوق را بسته است."

        now = utcnow()
        if cooldown_seconds > 0:
            cutoff_cd = now - timedelta(seconds=cooldown_seconds)
            cd_res = await self.session.execute(
                select(RevealRequest.id).where(
                    RevealRequest.inbox_id == msg.inbox_id,
                    RevealRequest.sender_hash == msg.sender_hash,
                    RevealRequest.created_at >= cutoff_cd,
                )
            )
            if cd_res.scalar_one_or_none() is not None:
                return False, "⏳ فاصله زمانی مجاز (کولداون) برای ارسال درخواست مجدد به این فرستنده هنوز به پایان نرسیده است."

        day_start = now - timedelta(hours=24)
        cnt_res = await self.session.execute(
            select(func.count(RevealRequest.id)).where(
                RevealRequest.inbox_id == msg.inbox_id,
                RevealRequest.created_at >= day_start,
            )
        )
        today_count = cnt_res.scalar_one() or 0
        if today_count >= daily_limit:
            return False, f"⚠️ سقف مجاز درخواست نمایش هویت روزانه این صندوق ({daily_limit} مورد در روز) پر شده است."

        return True, ""

    async def create_reveal_request(
        self,
        msg: MessageRecord,
        admin_user_id: int,
    ) -> RevealRequest:
        req = RevealRequest(
            message_id=msg.id,
            inbox_id=msg.inbox_id,
            sender_hash=msg.sender_hash,
            requested_by_admin_id=admin_user_id,
            status="pending",
        )
        self.session.add(req)
        await self.session.commit()
        await self.session.refresh(req)
        await self.log_inbox_action(
            msg.inbox_id,
            admin_user_id,
            "request_reveal",
            f"درخواست نمایش هویت برای پیام #{msg.id}",
        )
        return req

    async def get_reveal_request(self, req_id: int) -> Optional[RevealRequest]:
        res = await self.session.execute(select(RevealRequest).where(RevealRequest.id == req_id))
        return res.scalar_one_or_none()

    async def update_reveal_status(
        self,
        req_id: int,
        status: str,
        reveal_level: Optional[str] = None,
        block_inbox_requests: bool = False,
    ) -> Optional[RevealRequest]:
        req = await self.get_reveal_request(req_id)
        if not req:
            return None
        req.status = status
        req.reveal_level = reveal_level
        req.responded_at = utcnow()
        if block_inbox_requests:
            await self.set_sender_inbox_reveal_pref(req.inbox_id, req.sender_hash, False)
        await self.session.commit()
        await self.session.refresh(req)
        return req

    async def set_sender_inbox_reveal_pref(
        self,
        inbox_id: int,
        sender_hash: str,
        allow: bool,
    ) -> SenderRevealPreference:
        res = await self.session.execute(
            select(SenderRevealPreference).where(
                SenderRevealPreference.inbox_id == inbox_id,
                SenderRevealPreference.sender_hash == sender_hash,
            )
        )
        pref = res.scalar_one_or_none()
        if pref is None:
            pref = SenderRevealPreference(
                inbox_id=inbox_id,
                sender_hash=sender_hash,
                allow_requests=allow,
            )
            self.session.add(pref)
        else:
            pref.allow_requests = allow
            pref.updated_at = utcnow()
        await self.session.commit()
        await self.session.refresh(pref)
        return pref

    async def list_sender_reveal_prefs(self, sender_user_id: int) -> List[Tuple[SenderRevealPreference, Inbox]]:
        all_prefs = await self.session.execute(
            select(SenderRevealPreference, Inbox).join(
                Inbox, Inbox.id == SenderRevealPreference.inbox_id
            )
        )
        matched: List[Tuple[SenderRevealPreference, Inbox]] = []
        for pref, ib in all_prefs.all():
            expected_hash = self.crypto.compute_sender_hash(ib.id, sender_user_id)
            if pref.sender_hash == expected_hash:
                matched.append((pref, ib))
        return matched

    # ------------------------------------------------------------------
    # گزارش تخلف (Report Abuse) و بن خودکار موقت با رمزگشایی داخلی
    # ------------------------------------------------------------------
    async def create_report(
        self,
        msg: MessageRecord,
        reported_by: int,
        reason: str,
    ) -> ReportRecord:
        rep = ReportRecord(
            message_id=msg.id,
            inbox_id=msg.inbox_id,
            sender_hash=msg.sender_hash,
            reported_by=reported_by,
            reason=reason[:128],
            status="open",
        )
        self.session.add(rep)
        await self.session.commit()
        await self.session.refresh(rep)
        return rep

    async def validate_report_and_check_autoban(
        self,
        report_id: int,
        admin_id: int,
        is_valid: bool,
        threshold: int,
        ban_hours: int,
    ) -> Tuple[Optional[ReportRecord], bool, int]:
        res = await self.session.execute(
            select(ReportRecord).where(ReportRecord.id == report_id)
        )
        rep = res.scalar_one_or_none()
        if not rep or rep.status != "open":
            return None, False, 0

        rep.status = "valid" if is_valid else "dismissed"
        rep.reviewed_by = admin_id
        rep.reviewed_at = utcnow()

        auto_banned = False
        current_count = 0

        if is_valid:
            msg = await self.get_message(rep.message_id)
            if msg:
                offender_user_id = self.crypto.decrypt_sender_id(msg.sender_enc)
                offender = await self.get_user(offender_user_id)
                if offender:
                    offender.valid_reports_count = (offender.valid_reports_count or 0) + 1
                    current_count = offender.valid_reports_count
                    if current_count >= threshold:
                        offender.is_banned = True
                        offender.banned_until = utcnow() + timedelta(hours=ban_hours)
                        offender.ban_reason = f"بن خودکار موقت به دلیل دریافت {current_count} گزارش تخلف معتبر"
                        auto_banned = True

        await self.session.commit()
        await self.session.refresh(rep)
        return rep, auto_banned, current_count

    # ------------------------------------------------------------------
    # تنظیمات سیستم، کانال‌های جوین اجباری و آمار زنده
    # ------------------------------------------------------------------
    async def get_setting(self, key: str, default: str = "") -> str:
        res = await self.session.execute(
            select(SystemSetting.value).where(SystemSetting.key == key)
        )
        val = res.scalar_one_or_none()
        return val if val is not None else default

    async def set_setting(self, key: str, value: str) -> None:
        res = await self.session.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        rec = res.scalar_one_or_none()
        if rec is None:
            rec = SystemSetting(key=key, value=value)
            self.session.add(rec)
        else:
            rec.value = value
            rec.updated_at = utcnow()
        await self.session.commit()

    async def list_forced_channels(self, only_active: bool = True) -> Sequence[ForcedChannel]:
        stmt = select(ForcedChannel)
        if only_active:
            stmt = stmt.where(ForcedChannel.is_active == True)  # noqa: E712
        res = await self.session.execute(stmt.order_by(ForcedChannel.id.asc()))
        return res.scalars().all()

    async def add_forced_channel(self, chat_id: str, title: str, invite_link: str) -> ForcedChannel:
        res = await self.session.execute(
            select(ForcedChannel).where(ForcedChannel.chat_id == chat_id)
        )
        ch = res.scalar_one_or_none()
        if ch is None:
            ch = ForcedChannel(
                chat_id=chat_id,
                title=title[:128],
                invite_link=invite_link[:255],
                is_active=True,
            )
            self.session.add(ch)
        else:
            ch.title = title[:128]
            ch.invite_link = invite_link[:255]
            ch.is_active = True
        await self.session.commit()
        await self.session.refresh(ch)
        return ch

    async def remove_forced_channel(self, channel_id: int) -> bool:
        res = await self.session.execute(
            delete(ForcedChannel).where(ForcedChannel.id == channel_id)
        )
        await self.session.commit()
        return bool(res.rowcount and res.rowcount > 0)

    async def get_live_stats(self) -> Dict[str, int]:
        now = utcnow()
        day_ago = now - timedelta(hours=24)

        total_users = (await self.session.execute(select(func.count(User.id)))).scalar_one() or 0
        active_24h = (
            await self.session.execute(
                select(func.count(User.id)).where(User.last_active_at >= day_ago)
            )
        ).scalar_one() or 0
        total_inboxes = (await self.session.execute(select(func.count(Inbox.id)))).scalar_one() or 0
        messages_today = (
            await self.session.execute(
                select(func.count(MessageRecord.id)).where(MessageRecord.created_at >= day_ago)
            )
        ).scalar_one() or 0
        open_reports = (
            await self.session.execute(
                select(func.count(ReportRecord.id)).where(ReportRecord.status == "open")
            )
        ).scalar_one() or 0
        banned_users = (
            await self.session.execute(
                select(func.count(User.id)).where(User.is_banned == True)  # noqa: E712
            )
        ).scalar_one() or 0

        return {
            "total_users": total_users,
            "active_24h": active_24h,
            "total_inboxes": total_inboxes,
            "messages_today": messages_today,
            "open_reports": open_reports,
            "banned_users": banned_users,
        }
