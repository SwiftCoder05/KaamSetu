"""Client for the private email service on Vercel (email-service/).

Render blocks outbound SMTP, so the Vercel service opens the SMTP connection. Every call is:
  * over HTTPS,
  * signed (HMAC-SHA256 of "<unix time>.<body>" with the shared secret; the service rejects other callers and replays),
  * carrying the SMTP password only inside an AES-256-GCM envelope keyed from the shared secret and bound to the
    request id, so it is never plaintext JSON, even to a proxy that terminates TLS.
Nothing here logs credentials, and error messages come from the service already scrubbed.
"""

import base64
import hashlib
import hmac
import json
import os
import time
import uuid

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

from .mailer import MailError, OutgoingMessage

SEND_TIMEOUT = httpx.Timeout(35.0, connect=10.0)  # the service itself gives up after 25 s
VERIFY_TIMEOUT = httpx.Timeout(35.0, connect=10.0)
MAX_ATTACHMENT_BYTES = 3 * 1024 * 1024  # Vercel limits request bodies to 4.5 MB (base64 adds a third)
KINDS = {"transient", "rate_limit", "auth", "permanent"}
_INFO = b"kaamsetu-smtp-password-v1"


def configured() -> bool:
    return bool(settings.VERCEL_EMAIL_API_URL)


def _secret() -> bytes:
    secret = settings.VERCEL_EMAIL_SERVICE_SECRET
    if len(secret) < 32:
        raise MailError("service", "The email service is not configured. Contact the site owner.")
    return secret.encode()


def seal_password(secret: bytes, request_id: str, password: str) -> str:
    """base64( iv[12] | ciphertext | tag[16] ), AES-256-GCM, key = HKDF-SHA256(secret), AAD = request id."""
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(secret)
    iv = os.urandom(12)
    return base64.b64encode(iv + AESGCM(key).encrypt(iv, password.encode(), request_id.encode())).decode()


def sign(secret: bytes, timestamp: str, body: bytes) -> str:
    return hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def _smtp_block(cfg, password: str, request_id: str, secret: bytes) -> dict:
    return {
        "host": cfg.host,
        "port": cfg.port,
        "secure": cfg.security == "ssl",
        "username": cfg.username,
        "accountEmail": cfg.email,
        "passwordEnc": seal_password(secret, request_id, password),
    }


def _post(path: str, payload: dict, timeout: httpx.Timeout, client: httpx.Client | None = None) -> dict:
    secret = _secret()
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    ts = str(int(time.time()))
    headers = {"content-type": "application/json", "x-kaamsetu-timestamp": ts, "x-kaamsetu-signature": sign(secret, ts, body)}
    url = settings.VERCEL_EMAIL_API_URL + path
    try:
        if client is not None:
            resp = client.post(url, content=body, headers=headers, timeout=timeout)
        else:
            resp = httpx.post(url, content=body, headers=headers, timeout=timeout)
    except httpx.TimeoutException:
        # The service may still have sent it. A retry reuses the same request id, so it cannot send twice.
        raise MailError("transient", "The email service took too long to answer.") from None
    except httpx.HTTPError:
        raise MailError("transient", "Could not reach the email service.") from None
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code == 200 and isinstance(data, dict) and data.get("ok"):
        return data
    raise _error_from(resp.status_code, data)


def _error_from(status: int, data) -> MailError:
    err = (data or {}).get("error") if isinstance(data, dict) else None
    code = (err or {}).get("code") if isinstance(err, dict) else None
    if status in (401, 403) or code in ("unauthorized", "service_not_configured", "bad_credentials_envelope"):
        return MailError("service", "The email service rejected our request. The site owner must check the shared secret.")
    if isinstance(err, dict) and err.get("kind") in KINDS:
        retry = err.get("retryAfter")
        return MailError(err["kind"], str(err.get("message") or "The mail server refused the message.")[:300], retry if isinstance(retry, (int, float)) else None)
    if code == "invalid_request":
        return MailError("permanent", f"The email service refused the request: {str((err or {}).get('message', ''))[:200]}")
    if status == 413:
        return MailError("permanent", "The message and attachments are too large to send (limit about 3 MB).")
    return MailError("transient", "The email service is temporarily unavailable.")


def verify_via_relay(cfg, password: str) -> None:
    """Signs in to the user's SMTP server through the service without sending anything. Raises MailError."""
    request_id = f"verify-{uuid.uuid4().hex}"
    _post("/api/verify-smtp", {"requestId": request_id, "smtp": _smtp_block(cfg, password, request_id, _secret())}, VERIFY_TIMEOUT)


class RelayMailer:
    """Same interface as the direct SMTP mailers, so the campaign engine does not know which one it is using."""

    def __init__(self, cfg, password: str):
        self.cfg = cfg
        self._password = password
        self._client = httpx.Client()

    def send(self, msg: OutgoingMessage) -> str:
        if msg.from_email.lower() != self.cfg.email.lower():
            # Defence in depth: mail can only leave from the connected account of the user this mailer belongs to.
            raise MailError("permanent", "The sender address does not match the connected account.")
        attachments = []
        if msg.attachment:
            if len(msg.attachment.data) > MAX_ATTACHMENT_BYTES:
                raise MailError("permanent", "The attachment is too large to send (limit about 3 MB).")
            attachments.append({
                "filename": msg.attachment.filename,
                "contentType": msg.attachment.content_type,
                "contentBase64": base64.b64encode(msg.attachment.data).decode(),
            })
        request_id = msg.request_id or f"send-{uuid.uuid4().hex}"
        email = {"from": self.cfg.email, "to": [msg.to], "subject": " ".join(msg.subject.split()), "text": msg.text}
        if msg.from_name:
            email["fromName"] = " ".join(msg.from_name.replace('"', " ").replace("<", " ").replace(">", " ").split())[:120]
        if msg.html:
            email["html"] = msg.html
        if attachments:
            email["attachments"] = attachments
        data = _post(
            "/api/send-email",
            {"requestId": request_id, "smtp": _smtp_block(self.cfg, self._password, request_id, _secret()), "email": email},
            SEND_TIMEOUT,
            self._client,
        )
        return str(data.get("messageId") or f"<{request_id}>")

    def close(self):
        self._client.close()
