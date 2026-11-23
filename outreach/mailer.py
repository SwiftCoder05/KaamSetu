"""SMTP sending with a user's own account (Gmail/Outlook/Yahoo/custom). Direct `smtplib` is used on machines that
may open SMTP connections; on Render the same messages go through outreach/relay.py instead."""

import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 465  # implicit TLS


class MailError(Exception):
    """kind: transient (retry), rate_limit (pause campaign), auth (pause, reconnect),
    service (our email service is misconfigured or down: pause), permanent (fail recipient)."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class OutgoingMessage:
    from_email: str
    to: str
    subject: str
    text: str
    html: str | None = None
    from_name: str = ""
    attachment: Attachment | None = None
    request_id: str = ""  # stable per recipient: lets the email service ignore a repeated request


def normalize_app_password(raw: str) -> str | None:
    """Google shows app passwords as 'abcd efgh ijkl mnop'. Returns the 16 letters, or None."""
    p = re.sub(r"\s+", "", raw or "")
    return p.lower() if re.fullmatch(r"[a-zA-Z]{16}", p) else None


_SIMPLE_EMAIL = re.compile(r"[^\s<>@,;\"]+@[^\s<>@,;\"]+\.[^\s<>@,;\"]+")


def _header_safe(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).strip()


def build_message(msg: OutgoingMessage) -> EmailMessage:
    if not _SIMPLE_EMAIL.fullmatch(msg.to):
        raise MailError("permanent", "Invalid recipient address.")
    m = EmailMessage()
    name = re.sub(r"[\r\n\"<>]+", " ", msg.from_name).strip()
    m["From"] = formataddr((name, msg.from_email)) if name else msg.from_email
    m["To"] = msg.to
    m["Subject"] = _header_safe(msg.subject)
    m["Message-ID"] = make_msgid(domain=msg.from_email.split("@")[-1])
    m.set_content(msg.text)
    if msg.html:
        m.add_alternative(msg.html, subtype="html")
    if msg.attachment:
        maintype, _, subtype = msg.attachment.content_type.partition("/")
        m.add_attachment(msg.attachment.data, maintype=maintype, subtype=subtype or "octet-stream", filename=msg.attachment.filename)
    return m


def classify_smtp_error(e: BaseException, who: str = "Gmail") -> MailError:
    """Maps smtplib/socket failures onto the kinds the send engine understands. `who` names the provider in messages."""
    if isinstance(e, MailError):
        return e
    W = who[:1].upper() + who[1:]  # sentence-start form of the provider name
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return MailError("auth", f"{W} rejected the app password. Create a new one and reconnect in Settings.")
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        codes = [c for c, _ in e.recipients.values()]
        if codes and all(400 <= c < 500 for c in codes):
            return MailError("transient", f"{W} temporarily refused this recipient.")
        return MailError("permanent", f"{W} rejected this recipient.")
    if isinstance(e, smtplib.SMTPResponseException):
        code = e.smtp_code
        text = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        if code == 535 or re.search(r"5\.7\.8|5\.7\.9|application-specific password", text, re.I):
            return MailError("auth", f"{W} rejected the app password. Create a new one and reconnect in Settings.")
        if re.search(r"5\.4\.5|daily user sending (limit|quota)|sending limit exceeded", text, re.I):
            return MailError("rate_limit", f"{W}'s daily sending limit was reached.")
        if re.search(r"4\.7\.0|rate ?limit|too many", text, re.I) or code == 454:
            return MailError("rate_limit", f"{W} asked us to slow down.")
        if 400 <= code < 500:
            return MailError("transient", f"{W} temporarily refused the message ({code}).")
        if code >= 500:
            return MailError("permanent", f"{W} rejected the message ({code}).")
    if isinstance(e, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, ConnectionError, OSError)):
        return MailError("transient", f"Could not reach {who}'s mail server.")
    return MailError("transient", "Unexpected mail error.")


def _connect(user: str, app_password: str, timeout: float = 20) -> smtplib.SMTP_SSL:
    conn = smtplib.SMTP_SSL(GMAIL_HOST, GMAIL_PORT, timeout=timeout, context=ssl.create_default_context())
    conn.login(user, app_password)
    return conn


def connect_smtp(host: str, port: int, security: str, user: str, password: str, timeout: float = 20) -> smtplib.SMTP:
    """Opens an authenticated connection. ssl = implicit TLS (465); starttls = upgrade on 587 and never fall back to plaintext."""
    ctx = ssl.create_default_context()
    if security == "ssl":
        conn = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx)
    else:
        conn = smtplib.SMTP(host, port, timeout=timeout)
        try:
            conn.ehlo()
            conn.starttls(context=ctx)
            conn.ehlo()
        except Exception:
            conn.close()
            raise
    try:
        conn.login(user, password)
    except Exception:
        conn.close()
        raise
    return conn


def verify_gmail_credentials(user: str, app_password: str, connect=_connect) -> None:
    """Logs in once without sending, so a wrong app password is caught when saved, not mid-campaign."""
    try:
        conn = connect(user, app_password)
    except Exception as e:  # noqa: BLE001 - every failure is classified for the user
        raise classify_smtp_error(e) from e
    try:
        conn.quit()
    except Exception:  # noqa: BLE001
        pass


class GmailSmtpMailer:
    """Keeps one SMTP connection open across a batch and reconnects if Gmail drops it."""

    def __init__(self, user: str, app_password: str, connect=_connect):
        self.user = user
        self._password = app_password
        self._connect = connect
        self._conn = None
        self._who = "Gmail"  # provider name used in error messages

    def send(self, msg: OutgoingMessage) -> str:
        email = build_message(msg)
        try:
            if self._conn is None:
                self._conn = self._connect(self.user, self._password)
            refused = self._conn.send_message(email)
        except Exception as e:  # noqa: BLE001
            self._drop()
            raise classify_smtp_error(e, self._who) from e
        if refused:
            raise MailError("permanent", f"{self._who[:1].upper() + self._who[1:]} rejected this recipient.")
        return str(email["Message-ID"])

    def _drop(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

    def close(self):
        if self._conn is not None:
            try:
                self._conn.quit()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None


class SmtpMailer(GmailSmtpMailer):
    """Same batching behaviour as the Gmail mailer, for any host/port/security mode (direct connection)."""

    def __init__(self, cfg, password: str, connect=None):
        self.cfg = cfg
        super().__init__(
            cfg.username, password,
            connect=connect or (lambda user, pw: connect_smtp(cfg.host, cfg.port, cfg.security, user, pw)),
        )
        self._who = {"gmail": "Gmail", "outlook": "Outlook", "yahoo": "Yahoo"}.get(cfg.provider, "your email provider")


def verify_smtp_direct(cfg, password: str, connect=None) -> None:
    """Logs in once without sending (direct connection). Raises MailError on any failure."""
    who = {"gmail": "Gmail", "outlook": "Outlook", "yahoo": "Yahoo"}.get(cfg.provider, "your email provider")
    try:
        conn = (connect or connect_smtp)(cfg.host, cfg.port, cfg.security, cfg.username, password)
    except Exception as e:  # noqa: BLE001 - every failure is classified for the user
        raise classify_smtp_error(e, who) from e
    try:
        conn.quit()
    except Exception:  # noqa: BLE001
        pass
