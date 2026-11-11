"""Encrypts two-factor secrets (authenticator key, backup codes) before they reach the database."""

from allauth.mfa.adapter import DefaultMFAAdapter

from kaamsetu import crypto


class EncryptedMfaAdapter(DefaultMFAAdapter):
    def encrypt(self, text: str) -> str:
        return crypto.encrypt(text)

    def decrypt(self, encrypted_text: str) -> str:
        return crypto.decrypt(encrypted_text)
