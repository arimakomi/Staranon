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
            # بررسی انقضای بن موقت
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
    # مدیریت صندوق‌ها (Inboxes)
    # ------------------------------------------------------------------
    async def create_inbox(
        self,
        owner_id: int,
        inbox_type: str,
        title: str,
        welcome_text: str,
        target_chat_id: Optional[int] = None,
    ) -> Inbox:
        token = self.crypto.generate_inbox_token()
        inbox = Inbox(
            owner_id=owner_id,
            type=inbox_type,
            target_chat_id=target_chat_id,
            title=title[:64],
            token=token,
            welcome_text=welcome_text,
            allowed_media={
                "text": True,
                "photo": True,
                "video": True,
                "voice": True,
                "document": True,
                "animation": True,
                "sticker": True,
            },
            is_active=True,
        )
        self.session.add(inbox)
        await self.session.commit()
        await self.session.refresh(inbox)
        await self.add_inbox_log(inbox.id, owner_id, "create_inbox", f"ساخت صندوق «{inbox.title}»")
        return inbox

    async def get_inbox_by_id(self, inbox_id: int) -> Optional[Inbox]:
        res = await self.session.execute(select(Inbox).where(Inbox.id == inbox_id))
        return res.scalar_one_or_none()

    async def get_inbox_by_token_or_slug(self, identifier: str) -> Optional[Inbox]:
        if identifier.startswith("ib_"):
            tok = identifier[3:]
            res = await self.session.execute(select(Inbox).where(Inbox.token == tok))
            return res.scalar_one_or_none()
        res = await self.session.execute(
            select(Inbox).where(
                or_(Inbox.slug == identifier, Inbox.token == identifier)
            )
        )
        return res.scalar_one_or_none()

    async def list_user_accessible_inboxes(self, user_id: int) -> List[Tuple[Inbox, bool]]:
        """برگرداندن لیست (صندوق، آیا_مالک_است) برای کاربر."""
        owned_res = await self.session.execute(
            select(Inbox).where(Inbox.owner_id == user_id).order_by(Inbox.id.desc())
        )
        owned = [(ib, True) for ib in owned_res.scalars().all()]

        admin_res = await self.session.execute(
            select(Inbox)
            .join(InboxAdmin, InboxAdmin.inbox_id == Inbox.id)
            .where(InboxAdmin.user_id == user_id, Inbox.owner_id != user_id)
            .order_by(Inbox.id.desc())
        )
        admin_inboxes = [(ib, False) for ib in admin_res.scalars().all()]
        return owned + admin_inboxes

    async def reset_inbox_token(self, inbox_id: int, actor_id: int) -> Optional[str]:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox or inbox.owner_id != actor_id:
            return None
        new_token = self.crypto.generate_inbox_token()
        inbox.token = new_token
        await self.session.commit()
        await self.add_inbox_log(inbox_id, actor_id, "reset_link", "ریست لینک اختصاصی صندوق")
        return new_token

    async def set_inbox_slug(self, inbox_id: int, slug: Optional[str]) -> Tuple[bool, str]:
        if slug:
            existing = await self.session.execute(
                select(Inbox).where(func.lower(Inbox.slug) == slug.lower(), Inbox.id != inbox_id)
            )
            if existing.scalar_one_or_none() is not None:
                return False, "این کد کوتاه (Slug) قبلاً توسط صندوق دیگری انتخاب شده است."
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return False, "صندوق یافت نشد."
        inbox.slug = slug
        await self.session.commit()
        return True, "کد کوتاه صندوق با موفقیت ثبت شد."

    async def toggle_inbox_media(self, inbox_id: int, media_key: str) -> Optional[Dict[str, bool]]:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return None
        media_map = dict(inbox.allowed_media or {})
        media_map[media_key] = not media_map.get(media_key, True)
        inbox.allowed_media = media_map
        await self.session.commit()
        return media_map

    async def delete_inbox(self, inbox_id: int, owner_id: int) -> bool:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox or inbox.owner_id != owner_id:
            return False
        await self.session.delete(inbox)
        await self.session.commit()
        return True

    async def transfer_inbox_ownership(self, inbox_id: int, current_owner_id: int, new_owner_id: int) -> bool:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox or inbox.owner_id != current_owner_id:
            return False
        inbox.owner_id = new_owner_id
        # اگر مالک جدید در لیست ادمین‌ها بود، رکورد ادمین جداگانه‌اش را پاک کن
        await self.session.execute(
            delete(InboxAdmin).where(
                InboxAdmin.inbox_id == inbox_id, InboxAdmin.user_id == new_owner_id
            )
        )
        await self.session.commit()
        await self.add_inbox_log(
            inbox_id, current_owner_id, "transfer_ownership", f"انتقال مالکیت به کاربر {new_owner_id}"
        )
        return True

    # ------------------------------------------------------------------
    # مدیریت ادمین‌ها و دسترسی‌های صندوق (RBAC)
    # ------------------------------------------------------------------
    async def check_inbox_permission(self, inbox_id: int, user_id: int, perm: str) -> bool:
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return False
        if inbox.owner_id == user_id:
            return True
        res = await self.session.execute(
            select(InboxAdmin).where(
                InboxAdmin.inbox_id == inbox_id, InboxAdmin.user_id == user_id
            )
        )
        admin_rec = res.scalar_one_or_none()
        if not admin_rec:
            return False
        return bool((admin_rec.permissions or {}).get(perm, False))

    async def get_inbox_admins(self, inbox_id: int) -> Sequence[InboxAdmin]:
        res = await self.session.execute(
            select(InboxAdmin).where(InboxAdmin.inbox_id == inbox_id).order_by(InboxAdmin.id.asc())
        )
        return res.scalars().all()

    async def get_inbox_admin_record(self, inbox_id: int, user_id: int) -> Optional[InboxAdmin]:
        res = await self.session.execute(
            select(InboxAdmin).where(
                InboxAdmin.inbox_id == inbox_id, InboxAdmin.user_id == user_id
            )
        )
        return res.scalar_one_or_none()

    async def add_or_update_inbox_admin(
        self,
        inbox_id: int,
        user_id: int,
        added_by: int,
        preset: str = "responder",
    ) -> InboxAdmin:
        perms = dict(ROLE_PRESETS.get(preset, ROLE_PRESETS["responder"]))
        rec = await self.get_inbox_admin_record(inbox_id, user_id)
        if rec is None:
            rec = InboxAdmin(
                inbox_id=inbox_id,
                user_id=user_id,
                permissions=perms,
                added_by=added_by,
            )
            self.session.add(rec)
        else:
            rec.permissions = perms
        await self.session.commit()
        await self.session.refresh(rec)
        await self.add_inbox_log(
            inbox_id, added_by, "add_or_update_admin", f"تنظیم ادمین {user_id} با قالب {preset}"
        )
        return rec

    async def toggle_admin_permission(
        self, inbox_id: int, admin_user_id: int, perm_key: str, actor_id: int
    ) -> Optional[Dict[str, bool]]:
        rec = await self.get_inbox_admin_record(inbox_id, admin_user_id)
        if not rec:
            return None
        perms = dict(rec.permissions or {})
        perms[perm_key] = not perms.get(perm_key, False)
        rec.permissions = perms
        await self.session.commit()
        await self.add_inbox_log(
            inbox_id,
            actor_id,
            "toggle_admin_perm",
            f"تغییر دسترسی {perm_key} برای ادمین {admin_user_id} به {perms[perm_key]}",
        )
        return perms

    async def remove_inbox_admin(self, inbox_id: int, admin_user_id: int, actor_id: int) -> bool:
        res = await self.session.execute(
            delete(InboxAdmin).where(
                InboxAdmin.inbox_id == inbox_id, InboxAdmin.user_id == admin_user_id
            )
        )
        await self.session.commit()
        if res.rowcount and res.rowcount > 0:
            await self.add_inbox_log(
                inbox_id, actor_id, "remove_admin", f"حذف ادمین {admin_user_id}"
            )
            return True
        return False

    async def create_admin_invite(
        self, inbox_id: int, created_by: int, preset: str = "responder"
    ) -> AdminInvite:
        token = self.crypto.generate_invite_token()
        invite = AdminInvite(
            inbox_id=inbox_id,
            token=token,
            preset=preset,
            created_by=created_by,
            is_used=False,
        )
        self.session.add(invite)
        await self.session.commit()
        await self.session.refresh(invite)
        return invite

    async def consume_admin_invite(self, token: str, joining_user_id: int) -> Optional[Inbox]:
        res = await self.session.execute(
            select(AdminInvite).where(
                AdminInvite.token == token, AdminInvite.is_used == False  # noqa: E712
            )
        )
        invite = res.scalar_one_or_none()
        if not invite:
            return None
        inbox = await self.get_inbox_by_id(invite.inbox_id)
        if not inbox:
            return None
        invite.is_used = True
        if inbox.owner_id != joining_user_id:
            await self.add_or_update_inbox_admin(
                inbox.id, joining_user_id, invite.created_by, invite.preset
            )
        await self.session.commit()
        return inbox

    async def get_authorized_receivers(self, inbox_id: int) -> List[int]:
        """لیست آیدی تلگرام مالک + تمام ادمین‌های دارای دسترسی receive_messages."""
        inbox = await self.get_inbox_by_id(inbox_id)
        if not inbox:
            return []
        receivers = [inbox.owner_id]
        admins = await self.get_inbox_admins(inbox_id)
        for adm in admins:
            if (adm.permissions or {}).get("receive_messages", False) and adm.user_id not in receivers:
                receivers.append(adm.user_id)
        return receivers

    # ------------------------------------------------------------------
    # پیام‌ها، حذف ۵ دقیقه‌ای، ریاکشن و لاگ صندوق
    # ------------------------------------------------------------------
    async def save_message(
        self,
        inbox_id: int,
        sender_user_id: int,
        sender_tg_msg_id: int,
        delivered_tg_ids: Dict[str, Any],
        media_type: str = "text",
        direction: str = "inbound",
        parent_message_id: Optional[int] = None,
        sender_confirm_msg_id: Optional[int] = None,
    ) -> MessageRecord:
        s_hash = self.crypto.compute_sender_hash(inbox_id, sender_user_id)
        s_enc = self.crypto.encrypt_sender_id(sender_user_id)
        msg = MessageRecord(
            inbox_id=inbox_id,
            sender_hash=s_hash,
            sender_enc=s_enc,
            sender_tg_msg_id=sender_tg_msg_id,
            sender_confirm_msg_id=sender_confirm_msg_id,
            delivered_tg_ids=delivered_tg_ids,
            parent_message_id=parent_message_id,
            direction=direction,
            media_type=media_type,
            is_deleted=False,
        )
        self.session.add(msg)
        await self.session.commit()
        await self.session.refresh(msg)
        return msg

    async def get_message(self, message_id: int) -> Optional[MessageRecord]:
        res = await self.session.execute(
            select(MessageRecord).where(MessageRecord.id == message_id)
        )
        return res.scalar_one_or_none()

    async def set_active_replier(self, message_id: int, admin_id: Optional[int]) -> None:
        await self.session.execute(
            update(MessageRecord)
            .where(MessageRecord.id == message_id)
            .values(active_replier_id=admin_id)
        )
        await self.session.commit()

    async def mark_message_deleted(self, message_id: int) -> None:
        await self.session.execute(
            update(MessageRecord)
            .where(MessageRecord.id == message_id)
            .values(is_deleted=True)
        )
        await self.session.commit()

    async def set_or_update_reaction(
        self, message_id: int, inbox_id: int, admin_user_id: int, emoji: Optional[str]
    ) -> Optional[ReactionRecord]:
        res = await self.session.execute(
            select(ReactionRecord).where(ReactionRecord.message_id == message_id)
        )
        rec = res.scalar_one_or_none()
        if emoji is None:
            if rec:
                await self.session.delete(rec)
                await self.session.commit()
                await self.add_inbox_log(
                    inbox_id, admin_user_id, "remove_reaction", f"حذف ریاکشن روی پیام #{message_id}"
                )
            return None

        if rec is None:
            rec = ReactionRecord(
                message_id=message_id,
                inbox_id=inbox_id,
                admin_user_id=admin_user_id,
                emoji=emoji,
            )
            self.session.add(rec)
        else:
            rec.admin_user_id = admin_user_id
            rec.emoji = emoji
            rec.updated_at = utcnow()
        await self.session.commit()
        await self.session.refresh(rec)
        await self.add_inbox_log(
            inbox_id, admin_user_id, "set_reaction", f"ثبت ریاکشن {emoji} روی پیام #{message_id}"
        )
        return rec

    async def add_inbox_log(
        self, inbox_id: int, actor_user_id: int, action: str, details: Optional[str] = None
    ) -> None:
        log = InboxLog(
            inbox_id=inbox_id,
            actor_user_id=actor_user_id,
            action=action[:64],
            details=details[:255] if details else None,
        )
        self.session.add(log)
        await self.session.commit()

    async def get_inbox_logs(self, inbox_id: int, limit: int = 20) -> Sequence[InboxLog]:
        res = await self.session.execute(
            select(InboxLog)
            .where(InboxLog.inbox_id == inbox_id)
            .order_by(InboxLog.id.desc())
            .limit(limit)
        )
        return res.scalars().all()

    # ------------------------------------------------------------------
    # بلاک در سطح صندوق (Blocks)
    # ------------------------------------------------------------------
    async def is_sender_blocked(self, inbox_id: int, sender_hash: str) -> bool:
        res = await self.session.execute(
            select(BlockRecord.id).where(
                BlockRecord.inbox_id == inbox_id, BlockRecord.sender_hash == sender_hash
            )
        )
        return res.scalar_one_or_none() is not None

    async def block_sender(
        self, inbox_id: int, sender_hash: str, blocked_by: int, reason: Optional[str] = None
    ) -> BlockRecord:
        res = await self.session.execute(
            select(BlockRecord).where(
                BlockRecord.inbox_id == inbox_id, BlockRecord.sender_hash == sender_hash
            )
        )
        rec = res.scalar_one_or_none()
        if rec is None:
            rec = BlockRecord(
                inbox_id=inbox_id,
                sender_hash=sender_hash,
                blocked_by=blocked_by,
                reason=reason,
            )
            self.session.add(rec)
            await self.session.commit()
            await self.session.refresh(rec)
            await self.add_inbox_log(
                inbox_id,
                blocked_by,
                "block_sender",
                f"مسدودسازی فرستنده #{self.crypto.short_hash(sender_hash)}",
            )
        return rec

    async def unblock_sender_by_id(self, block_id: int, actor_id: int) -> bool:
        res = await self.session.execute(select(BlockRecord).where(BlockRecord.id == block_id))
        rec = res.scalar_one_or_none()
        if not rec:
            return False
        has_perm = await self.check_inbox_permission(rec.inbox_id, actor_id, "block")
        if not has_perm:
            return False
        inbox_id = rec.inbox_id
        s_hash = rec.sender_hash
        await self.session.delete(rec)
        await self.session.commit()
        await self.add_inbox_log(
            inbox_id, actor_id, "unblock_sender", f"رفع مسدودیت #{self.crypto.short_hash(s_hash)}"
        )
        return True

    async def list_user_blocks(self, owner_id: int) -> List[Tuple[BlockRecord, Inbox]]:
        res = await self.session.execute(
            select(BlockRecord, Inbox)
            .join(Inbox, Inbox.id == BlockRecord.inbox_id)
            .where(Inbox.owner_id == owner_id)
            .order_by(BlockRecord.id.desc())
            .limit(40)
        )
        return list(res.all())

    # ------------------------------------------------------------------
    # درخواست نمایش هویت با رضایت فرستنده (Reveal Requests)
    # ------------------------------------------------------------------
    async def can_request_reveal(
        self,
        message: MessageRecord,
        requester_id: int,
        daily_limit: int,
        cooldown_seconds: int,
    ) -> Tuple[bool, str]:
        # ۱. بررسی یک درخواست برای هر پیام
        existing = await self.session.execute(
            select(RevealRequest).where(RevealRequest.message_id == message.id)
        )
        req_rec = existing.scalar_one_or_none()
        if req_rec is not None:
            return False, f"برای این پیام قبلاً درخواست ثبت شده است (وضعیت: {req_rec.status})."

        # ۲. بررسی تنظیمات فرستنده (سراسری و صندوقی) با رمزگشایی داخلی در حافظه
        sender_id = self.crypto.decrypt_sender_id(message.sender_enc)
        sender_user = await self.get_user(sender_id)
        if sender_user and not sender_user.allow_reveal_global:
            return False, "فرستنده دریافت درخواست هویت را در تنظیمات خود غیرفعال کرده است."

        pref_res = await self.session.execute(
            select(SenderRevealPreference).where(
                SenderRevealPreference.inbox_id == message.inbox_id,
                SenderRevealPreference.sender_hash == message.sender_hash,
            )
        )
        pref = pref_res.scalar_one_or_none()
        if pref is not None and not pref.allow_reveal:
            return False, "فرستنده درخواست‌های هویت از طرف این صندوق را بسته است."

        # ۳. بررسی سقف روزانه و کولداون برای درخواست‌دهنده
        now = utcnow()
        day_ago = now - timedelta(days=1)
        count_res = await self.session.execute(
            select(func.count(RevealRequest.id)).where(
                RevealRequest.requester_id == requester_id,
                RevealRequest.created_at >= day_ago,
            )
        )
        daily_count = count_res.scalar_one() or 0
        if daily_count >= daily_limit:
            return False, f"شما به سقف روزانه ({daily_limit} درخواست در ۲۴ ساعت) رسیده‌اید."

        last_res = await self.session.execute(
            select(RevealRequest)
            .where(
                RevealRequest.inbox_id == message.inbox_id,
                RevealRequest.sender_hash == message.sender_hash,
            )
            .order_by(RevealRequest.id.desc())
            .limit(1)
        )
        last_req = last_res.scalar_one_or_none()
        if last_req is not None:
            created = last_req.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now - created).total_seconds() < cooldown_seconds:
                return False, "لطفاً تا پایان زمان انتظار (Cooldown) برای این فرستنده صبر کنید."

        return True, "OK"

    async def create_reveal_request(
        self, message: MessageRecord, requester_id: int
    ) -> RevealRequest:
        req = RevealRequest(
            message_id=message.id,
            inbox_id=message.inbox_id,
            requester_id=requester_id,
            sender_hash=message.sender_hash,
            status="pending",
        )
        self.session.add(req)
        await self.session.commit()
        await self.session.refresh(req)
        await self.add_inbox_log(
            message.inbox_id,
            requester_id,
            "request_reveal",
            f"ارسال درخواست هویت برای پیام #{message.id}",
        )
        return req

    async def get_reveal_request(self, request_id: int) -> Optional[RevealRequest]:
        res = await self.session.execute(
            select(RevealRequest).where(RevealRequest.id == request_id)
        )
        return res.scalar_one_or_none()

    async def update_reveal_status(
        self, request_id: int, status: str, block_inbox_requests: bool = False
    ) -> Optional[RevealRequest]:
        req = await self.get_reveal_request(request_id)
        if not req or req.status != "pending":
            return None
        req.status = status
        req.responded_at = utcnow()
        if block_inbox_requests:
            pref_res = await self.session.execute(
                select(SenderRevealPreference).where(
                    SenderRevealPreference.inbox_id == req.inbox_id,
                    SenderRevealPreference.sender_hash == req.sender_hash,
                )
            )
            pref = pref_res.scalar_one_or_none()
            if pref is None:
                pref = SenderRevealPreference(
                    inbox_id=req.inbox_id,
                    sender_hash=req.sender_hash,
                    allow_reveal=False,
                )
                self.session.add(pref)
            else:
                pref.allow_reveal = False
        await self.session.commit()
        await self.session.refresh(req)
        return req

    # ------------------------------------------------------------------
    # گزارش تخلف و بن خودکار با رمزگشایی صرفاً داخلی (Reports & Auto-Ban)
    # ------------------------------------------------------------------
    async def create_report(
        self, message: MessageRecord, reporter_id: int, reason: str
    ) -> ReportRecord:
        rep = ReportRecord(
            message_id=message.id,
            inbox_id=message.inbox_id,
            reporter_id=reporter_id,
            sender_hash=message.sender_hash,
            reason=reason,
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
        """
        بررسی و تایید گزارش توسط ادمین کل ربات.
        در صورت تایید اعتبار (is_valid=True)، سرویس به صورت کاملاً داخلی در حافظه
        مقدار sender_enc پیام را رمزگشایی می‌کند تا valid_reports_count کاربر متخلف را در جدول users
        افزایش دهد و در صورت رسیدن به حد نصاب، او را در کل ربات موقتاً بن کند.
        هیچ آیدی خامی به بیرون درز نمی‌کند.
        """
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
                # رمزگشایی صرفاً داخلی در حافظه برای اعمال شمارنده روی جدول users
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
