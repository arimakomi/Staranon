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
            raise ValueError("HMAC_SECRET must be at least 16 characters.")
        self._hmac_secret = hmac_secret.encode("utf-8")
        try:
            self._fernet = Fernet(fernet_key.encode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid FERNET_KEY: {exc}") from exc

    def compute_sender_hash(self, inbox_id: int, sender_user_id: int) -> str:
        """
        Compute unique per-inbox HMAC-SHA256 hash combining inbox_id and sender_user_id.
        """
        msg = f"inbox:{inbox_id}:sender:{sender_user_id}".encode("utf-8")
        return hmac.new(self._hmac_secret, msg, hashlib.sha256).hexdigest()

    @staticmethod
    def short_hash(sender_hash: str) -> str:
        """Return 8-character uppercase hash code for inbox conversation identification."""
        return sender_hash[:8].upper()

    def encrypt_sender_id(self, sender_user_id: int) -> str:
        """Encrypt sender Telegram user ID with Fernet for storage in sender_enc."""
        payload = str(int(sender_user_id)).encode("utf-8")
        return self._fernet.encrypt(payload).decode("utf-8")

    def decrypt_sender_id(self, sender_enc: str) -> int:
        """
        Decrypt sender Telegram user ID strictly in-memory inside the service layer
        (for reply delivery, 5-minute delete, reactions, or validated report counting on users).
        Never log or display the returned ID.
        """
        try:
            decrypted = self._fernet.decrypt(sender_enc.encode("utf-8"))
            return int(decrypted.decode("utf-8"))
        except (InvalidToken, ValueError) as exc:
            raise ValueError("Failed to decrypt sender ID (invalid Fernet key or corrupted ciphertext).") from exc

    @staticmethod
    def generate_inbox_token() -> str:
        """Generate random URL-safe token for inbox deep links."""
        return secrets.token_urlsafe(16)

    @staticmethod
    def generate_invite_token() -> str:
        """Generate one-time token for inbox admin invites."""
        return secrets.token_urlsafe(18)

    @staticmethod
    def generate_new_secrets() -> tuple[str, str]:
        """Generate fresh (HMAC_SECRET, FERNET_KEY) pair."""
        hmac_sec = secrets.token_hex(32)
        fernet_k = Fernet.generate_key().decode("utf-8")
        return hmac_sec, fernet_k

    @staticmethod
    def derive_backup_fernet(passphrase: str, salt: bytes) -> Fernet:
        """Derive Fernet key from passphrase and 16-byte salt using PBKDF2-HMAC-SHA256."""
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
        """Encrypt backup archive bytes with 16-byte salt prefix."""
        salt = secrets.token_bytes(16)
        f = cls.derive_backup_fernet(passphrase, salt)
        encrypted = f.encrypt(raw_data)
        return b"ANONBAK1" + salt + encrypted

    @classmethod
    def decrypt_backup_bytes(cls, encrypted_blob: bytes, passphrase: str) -> bytes:
        """Decrypt encrypted backup archive bytes."""
        if not encrypted_blob.startswith(b"ANONBAK1") or len(encrypted_blob) < 25:
            raise ValueError("Invalid backup file format.")
        salt = encrypted_blob[8:24]
        ciphertext = encrypted_blob[24:]
        f = cls.derive_backup_fernet(passphrase, salt)
        try:
            return f.decrypt(ciphertext)
        except InvalidToken as exc:
            raise ValueError("Invalid backup passphrase or corrupted backup file.") from exc


def mask_secret(value: Optional[str], visible: int = 4) -> str:
    """Mask tokens, secret keys, and proxy passwords for safe terminal display."""
    if not value:
        return "<empty>"
    if "://" in value and "@" in value:
        scheme, rest = value.split("://", 1)
        creds, hostpart = rest.rsplit("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:****@{hostpart}"
        return f"{scheme}://****@{hostpart}"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"
