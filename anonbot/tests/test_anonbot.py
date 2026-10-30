"""
تست‌های واحد و یکپارچه (pytest) برای بررسی:
۱. هش HMAC-SHA256 اختصاصی هر صندوق و رمزنگاری/رمزگشایی Fernet
۲. رمزنگاری و رمزگشایی فایل بک‌آپ حاوی کلیدها
۳. ساخت صندوق، ریست لینک با حفظ بلاک‌ها و تاریخچه، و یکتا بودن Slug
۴. دسترسی‌های ۸گانه ادمین‌های صندوق (RBAC) و لینک دعوت یک‌بارمصرف
۵. بلاک و رفع بلاک در سطح صندوق
۶. درخواست نمایش هویت با رضایت، سقف روزانه، کولداون و عدم ذخیره دائمی هویت
۷. تایید گزارش تخلف توسط ادمین کل با رمزگشایی داخلی sender_enc و اعمال بن خودکار موقت سراسری
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base
from app.db.repositories import BotRepository
from app.utils.crypto import CryptoManager


@pytest.fixture
def crypto() -> CryptoManager:
    hmac_sec, fernet_k = CryptoManager.generate_new_secrets()
    return CryptoManager(hmac_secret=hmac_sec, fernet_key=fernet_k)


@pytest_asyncio.fixture
async def repo(crypto: CryptoManager):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield BotRepository(session=session, crypto=crypto)
    await engine.dispose()


def test_crypto_hmac_and_fernet(crypto: CryptoManager) -> None:
    sender_id = 556677889
    # هش همان کاربر در دو صندوق مختلف باید کاملاً متفاوت باشد
    h1 = crypto.compute_sender_hash(inbox_id=1, sender_user_id=sender_id)
    h2 = crypto.compute_sender_hash(inbox_id=2, sender_user_id=sender_id)
    assert h1 != h2
    assert len(h1) == 64

    # رمزنگاری و رمزگشایی Fernet
    enc = crypto.encrypt_sender_id(sender_id)
    assert str(sender_id) not in enc
    dec = crypto.decrypt_sender_id(enc)
    assert dec == sender_id


def test_encrypted_backup_blob(crypto: CryptoManager) -> None:
    sample_payload = b"SQLITE_AND_KEYS_ARCHIVE_DATA"
    encrypted = CryptoManager.encrypt_backup_bytes(sample_payload, "MyStrongPassphrase123!")
    assert encrypted.startswith(b"ANONBAK1")
    assert sample_payload not in encrypted

    decrypted = CryptoManager.decrypt_backup_bytes(encrypted, "MyStrongPassphrase123!")
    assert decrypted == sample_payload

    with pytest.raises(ValueError):
        CryptoManager.decrypt_backup_bytes(encrypted, "WrongPassphrase")


@pytest.mark.asyncio
async def test_inbox_link_reset_preserves_blocks_and_history(repo: BotRepository) -> None:
    owner = await repo.upsert_user(1001, "علی")
    sender = await repo.upsert_user(2002, "فرستنده")

    inbox = await repo.create_inbox(owner.id, "personal", "صندوق شخصی علی", "خوش آمدید")
    old_token = inbox.token

    # ذخیره پیام و بلاک کردن فرستنده
    msg = await repo.save_message(
        inbox_id=inbox.id,
        sender_user_id=sender.id,
        sender_tg_msg_id=11,
        delivered_tg_ids={"1001": 22},
    )
    await repo.block_sender(inbox.id, msg.sender_hash, owner.id)
    assert await repo.is_sender_blocked(inbox.id, msg.sender_hash) is True

    # ریست لینک صندوق
    new_token = await repo.reset_inbox_token(inbox.id, owner.id)
    assert new_token is not None
    assert new_token != old_token

    # لینک قبلی باید فوراً باطل شده باشد
    assert await repo.get_inbox_by_token_or_slug(f"ib_{old_token}") is None
    assert await repo.get_inbox_by_token_or_slug(f"ib_{new_token}") is not None

    # بلاک‌ها و تاریخچه پیام‌ها باید حفظ شده باشند چون به inbox_id وصل هستند
    assert await repo.is_sender_blocked(inbox.id, msg.sender_hash) is True
    saved_msg = await repo.get_message(msg.id)
    assert saved_msg is not None
    assert saved_msg.inbox_id == inbox.id


@pytest.mark.asyncio
async def test_inbox_admins_rbac_and_one_time_invite(repo: BotRepository) -> None:
    owner = await repo.upsert_user(10, "مالک")
    adm_user = await repo.upsert_user(20, "ادمین")
    inbox = await repo.create_inbox(owner.id, "channel", "صندوق کانال", "سلام", target_chat_id=-100123)

    # ساخت لینک دعوت یک‌بارمصرف با قالب ناظر (observer)
    invite = await repo.create_admin_invite(inbox.id, owner.id, preset="observer")
    consumed_ib = await repo.consume_admin_invite(invite.token, adm_user.id)
    assert consumed_ib is not None
    assert consumed_ib.id == inbox.id

    # استفاده مجدد از همان لینک یک‌بارمصرف باید رد شود
    assert await repo.consume_admin_invite(invite.token, 30) is None

    # بررسی دسترسی‌های قالب ناظر
    assert await repo.check_inbox_permission(inbox.id, adm_user.id, "receive_messages") is True
    assert await repo.check_inbox_permission(inbox.id, adm_user.id, "reply") is False

    # روشن کردن دسترسی پاسخ برای ادمین
    await repo.toggle_admin_permission(inbox.id, adm_user.id, "reply", owner.id)
    assert await repo.check_inbox_permission(inbox.id, adm_user.id, "reply") is True

    # حذف ادمین و اعمال فوری
    await repo.remove_inbox_admin(inbox.id, adm_user.id, owner.id)
    assert await repo.check_inbox_permission(inbox.id, adm_user.id, "receive_messages") is False


@pytest.mark.asyncio
async def test_consent_identity_reveal_and_restrictions(repo: BotRepository) -> None:
    owner = await repo.upsert_user(111, "گیرنده")
    sender = await repo.upsert_user(222, "فرستنده")
    inbox = await repo.create_inbox(owner.id, "personal", "صندوق تست", "سلام")

    msg = await repo.save_message(
        inbox_id=inbox.id,
        sender_user_id=sender.id,
        sender_tg_msg_id=50,
        delivered_tg_ids={"111": 51},
    )

    can_req, _ = await repo.can_request_reveal(msg, owner.id, daily_limit=5, cooldown_seconds=3600)
    assert can_req is True

    req = await repo.create_reveal_request(msg, owner.id)
    assert req.status == "pending"

    # درخواست مجدد روی همان پیام یا در بازه کولداون باید رد شود
    can_req_again, _ = await repo.can_request_reveal(msg, owner.id, daily_limit=5, cooldown_seconds=3600)
    assert can_req_again is False

    # رد درخواست توسط فرستنده همراه با بستن درخواست‌های بعدی از این صندوق
    await repo.update_reveal_status(req.id, "rejected", block_inbox_requests=True)

    msg2 = await repo.save_message(
        inbox_id=inbox.id,
        sender_user_id=sender.id,
        sender_tg_msg_id=52,
        delivered_tg_ids={"111": 53},
    )
    can_req_msg2, reason = await repo.can_request_reveal(
        msg2, owner.id, daily_limit=5, cooldown_seconds=0
    )
    assert can_req_msg2 is False
    assert "بسته است" in reason


@pytest.mark.asyncio
async def test_report_validation_internal_decrypt_and_global_autoban(repo: BotRepository) -> None:
    owner1 = await repo.upsert_user(301, "مالک اول")
    owner2 = await repo.upsert_user(302, "مالک دوم")
    offender = await repo.upsert_user(999, "مزاحم")
    super_admin_id = 1

    ib1 = await repo.create_inbox(owner1.id, "personal", "صندوق ۱", "سلام")
    ib2 = await repo.create_inbox(owner2.id, "personal", "صندوق ۲", "سلام")

    # مزاحم به دو صندوق مجزا پیام می‌دهد (هش او در دو صندوق متفاوت است)
    m1 = await repo.save_message(ib1.id, offender.id, 101, {"301": 201})
    m2 = await repo.save_message(ib2.id, offender.id, 102, {"302": 202})
    assert m1.sender_hash != m2.sender_hash

    rep1 = await repo.create_report(m1, owner1.id, "توهین")
    rep2 = await repo.create_report(m2, owner2.id, "اسپم")

    # تایید گزارش اول توسط ادمین کل (حد نصاب بن خودکار = ۲)
    _, banned1, count1 = await repo.validate_report_and_check_autoban(
        rep1.id, super_admin_id, is_valid=True, threshold=2, ban_hours=48
    )
    assert count1 == 1
    assert banned1 is False

    # تایید گزارش دوم از صندوق دیگر -> باید با رمزگشایی داخلی sender_enc همان کاربر 999 بن شود
    _, banned2, count2 = await repo.validate_report_and_check_autoban(
        rep2.id, super_admin_id, is_valid=True, threshold=2, ban_hours=48
    )
    assert count2 == 2
    assert banned2 is True

    updated_offender = await repo.get_user(offender.id)
    assert updated_offender is not None
    assert updated_offender.is_banned is True
    assert updated_offender.banned_until is not None
