"""Symmetric encryption for stored credentials (SMTP app passwords).

New secrets use AES-256-GCM (EMAIL_ENCRYPTION_KEY), bound to the owning user so a ciphertext copied to another
user's row will not decrypt. Rows written before this change used Fernet (TOKEN_ENCRYPTION_KEY) and stay readable.
"""

import base64
import binascii
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet() -> Fernet:
    key = settings.TOKEN_ENCRYPTION_KEY
    if key:
        try:
            return Fernet(key.encode())
        except ValueError as e:
            raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") from e
    if not settings.DEBUG:
        raise ImproperlyConfigured("TOKEN_ENCRYPTION_KEY must be set when DEBUG is off.")
    # Local development only: derive a stable key from SECRET_KEY so the app runs without extra setup.
    derived = base64.urlsafe_b64encode(hashlib.sha256(("kaamsetu-dev:" + settings.SECRET_KEY).encode()).digest())
    return Fernet(derived)


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Stored credential could not be decrypted (was TOKEN_ENCRYPTION_KEY changed?).") from e


# ───────────── AES-256-GCM for SMTP credentials ─────────────

_GCM_PREFIX = "v2:"


def _gcm_key() -> bytes:
    raw = settings.EMAIL_ENCRYPTION_KEY
    if raw:
        try:
            key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        except (binascii.Error, ValueError) as e:
            raise ImproperlyConfigured("EMAIL_ENCRYPTION_KEY is not valid base64.") from e
        if len(key) != 32:
            raise ImproperlyConfigured("EMAIL_ENCRYPTION_KEY must decode to exactly 32 bytes (AES-256).")
        return key
    if not settings.DEBUG:
        raise ImproperlyConfigured("EMAIL_ENCRYPTION_KEY must be set when DEBUG is off.")
    return hashlib.sha256(("kaamsetu-dev-gcm:" + settings.SECRET_KEY).encode()).digest()  # local development only


def encrypt_secret(plain: str, aad: str) -> str:
    """AES-256-GCM with a fresh random nonce. `aad` (e.g. "mailconn:<user id>") is authenticated but not stored."""
    nonce = os.urandom(12)
    ct = AESGCM(_gcm_key()).encrypt(nonce, plain.encode(), aad.encode())
    return _GCM_PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_secret(token: str, aad: str) -> str:
    if not token.startswith(_GCM_PREFIX):
        return decrypt(token)  # legacy Fernet row
    try:
        raw = base64.urlsafe_b64decode(token[len(_GCM_PREFIX):])
        return AESGCM(_gcm_key()).decrypt(raw[:12], raw[12:], aad.encode()).decode()
    except (InvalidTag, binascii.Error, ValueError) as e:
        raise ValueError("Stored credential could not be decrypted (was EMAIL_ENCRYPTION_KEY changed?).") from e
