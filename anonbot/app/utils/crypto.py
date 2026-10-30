"""سرویس رمزنگاری و هش امنیتی هویت فرستنده و بک‌آپ."""

import base64
import hashlib
import hmac
import secrets
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


class CryptoManager:
    """
    مدیریت هش یک‌طرفه HMAC-SHA256 (مخصوص هر صندوق) و رمزنگاری متقارن Fernet
    برای آیدی عددی فرستنده در جدول پیام‌ها.
    """

    def __init__(self, hmac_secret: str, fernet_key: str) -> None:
        if not hmac_secret or len(hmac_secret) < 16:
            raise ValueError("HMAC_SECRET باید حداقل ۱۶ کاراکتر باشد.")
        self._hmac_secret = hmac_secret.encode("utf-8")
        try:
            self._fernet = Fernet(fernet_key.encode("utf-8"))
        except Exception as exc:
            raise ValueError(f"FERNET_KEY نامعتبر است: {exc}") from exc

    def compute_sender_hash(self, inbox_id: int, sender_user_id: int) -> str:
        """
        تولید هش یکتا و غیرقابل برگشت با ترکیب inbox_id و آیدی تلگرام فرستنده.
        فرستنده در دو صندوق متفاوت هش متفاوتی دارد تا امکان ردیابی متقاطع وجود نداشته باشد.
        """
        msg = f"inbox:{inbox_id}:sender:{sender_user_id}".encode("utf-8")
        return hmac.new(self._hmac_secret, msg, hashlib.sha256).hexdigest()

    @staticmethod
    def short_hash(sender_hash: str) -> str:
        """نمایش کد کوتاه ۸ کاراکتری از هش برای شناسایی مکالمه توسط ادمین صندوق."""
        return sender_hash[:8].upper()

    def encrypt_sender_id(self, sender_user_id: int) -> str:
        """رمزنگاری آیدی تلگرام فرستنده با Fernet برای ذخیره در ستون sender_enc."""
        payload = str(int(sender_user_id)).encode("utf-8")
        return self._fernet.encrypt(payload).decode("utf-8")

    def decrypt_sender_id(self, sender_enc: str) -> int:
        """
        رمزگشایی آیدی تلگرام فرستنده صرفاً در حافظه داخلی سرویس (In-Memory)
        جهت ارسال پاسخ، اعمال ریاکشن، حذف ۵ دقیقه‌ای، یا ثبت گزارش معتبر در جدول users.
        هرگز خروجی این متد نباید در لاگ‌ها یا پنل ادمین نمایش داده شود.
        """
        try:
            decrypted = self._fernet.decrypt(sender_enc.encode("utf-8"))
            return int(decrypted.decode("utf-8"))
        except (InvalidToken, ValueError) as exc:
            raise ValueError("خطا در رمزگشایی شناسه فرستنده (کلید Fernet نامعتبر یا داده دستکاری شده است).") from exc

    @staticmethod
    def generate_inbox_token() -> str:
        """تولید توکن تصادفی امن برای لینک صندوق (بدون ارتباط با آیدی کاربر)."""
        return secrets.token_urlsafe(16)

    @staticmethod
    def generate_invite_token() -> str:
        """تولید توکن یک‌بارمصرف برای دعوت ادمین صندوق."""
        return secrets.token_urlsafe(18)

    @staticmethod
    def generate_new_secrets() -> tuple[str, str]:
        """تولید جفت کلید جدید (HMAC_SECRET, FERNET_KEY) برای نصب اولیه."""
        hmac_sec = secrets.token_hex(32)
        fernet_k = Fernet.generate_key().decode("utf-8")
        return hmac_sec, fernet_k

    @staticmethod
    def derive_backup_fernet(passphrase: str, salt: bytes) -> Fernet:
        """مشتق‌سازی کلید Fernet از رمز عبور یا کلید سیستم برای رمزنگاری فایل بک‌آپ."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=390000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        return Fernet(key)

    @classmethod
    def encrypt_backup_bytes(cls, raw_data: bytes, passphrase: str) -> bytes:
        """رمزنگاری بایت‌های آرشیو بک‌آپ به همراه Salt شانزده بایتی در ابتدای فایل."""
        salt = secrets.token_bytes(16)
        f = cls.derive_backup_fernet(passphrase, salt)
        encrypted = f.encrypt(raw_data)
        return b"ANONBAK1" + salt + encrypted

    @classmethod
    def decrypt_backup_bytes(cls, encrypted_blob: bytes, passphrase: str) -> bytes:
        """رمزگشایی آرشیو بک‌آپ رمزنگاری‌شده."""
        if not encrypted_blob.startswith(b"ANONBAK1") or len(encrypted_blob) < 25:
            raise ValueError("فرمت فایل بک‌آپ نامعتبر است.")
        salt = encrypted_blob[8:24]
        ciphertext = encrypted_blob[24:]
        f = cls.derive_backup_fernet(passphrase, salt)
        try:
            return f.decrypt(ciphertext)
        except InvalidToken as exc:
            raise ValueError("رمز عبور بک‌آپ اشتباه است یا فایل بک‌آپ آسیب دیده است.") from exc


def mask_secret(value: Optional[str], visible: int = 4) -> str:
    """ماسک کردن توکن، کلیدهای امنیتی و رمز عبور پروکسی برای نمایش امن در CLI."""
    if not value:
        return "<خالی>"
    if "://" in value and "@" in value:
        # ماسک کردن رمز عبور در URL پروکسی یا دیتابیس
        scheme, rest = value.split("://", 1)
        creds, hostpart = rest.rsplit("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:****@{hostpart}"
        return f"{scheme}://****@{hostpart}"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"
