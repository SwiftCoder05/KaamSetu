"""Symmetric encryption for stored credentials (Gmail app passwords)."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
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
