"""Provider presets and validation for a user's SMTP settings.

For Gmail, Outlook and Yahoo the host is fixed by the preset (whatever the browser sends is ignored), so a preset
can never be bent into "connect to an arbitrary server". Only the Custom provider accepts a host.
"""

import re
from dataclasses import dataclass

SECURITY_PORT = {"ssl": 465, "starttls": 587}
_HOST = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", re.I)
_EMAIL = re.compile(r"[^\s<>@,;\"'\\()\[\]]+@[^\s<>@,;\"'\\()\[\]]+\.[^\s<>@,;\"'\\()\[\]]+")

PRESETS = {
    "gmail": {
        "label": "Gmail", "host": "smtp.gmail.com", "securities": ["ssl", "starttls"], "security": "ssl",
        "password_label": "Google App Password",
        "help": "Needs 2-Step Verification. Create an App Password at myaccount.google.com/apppasswords (16 letters). "
                "Your normal Google password will not work.",
    },
    "outlook": {
        "label": "Outlook / Microsoft 365", "host": "smtp.office365.com", "securities": ["starttls"], "security": "starttls",
        "password_label": "Password or app password",
        "help": "Works only if SMTP AUTH is enabled for your mailbox. Many personal and work accounts have it switched off "
                "(your admin can enable it). With two-step verification, create an app password in your Microsoft account.",
    },
    "yahoo": {
        "label": "Yahoo Mail", "host": "smtp.mail.yahoo.com", "securities": ["ssl", "starttls"], "security": "ssl",
        "password_label": "Yahoo App Password",
        "help": "Create an app password under Account security > Generate app password. Your normal password will not work.",
    },
    "custom": {
        "label": "Custom SMTP", "host": "", "securities": ["ssl", "starttls"], "security": "starttls",
        "password_label": "SMTP password",
        "help": "Enter your provider's SMTP host. Use port 465 with SSL/TLS or port 587 with STARTTLS (port 25 is not supported).",
    },
}


@dataclass(frozen=True)
class SmtpSettings:
    provider: str
    email: str
    host: str
    port: int
    security: str
    username: str  # never blank: defaults to the email address


def valid_email(value: str) -> bool:
    return bool(_EMAIL.fullmatch(value or "")) and len(value) <= 254


def build_settings(provider: str, email: str, host: str = "", port=None, security: str = "", username: str = "") -> SmtpSettings:
    """Returns checked settings or raises ValueError with a message that is safe to show the user."""
    preset = PRESETS.get(provider)
    if not preset:
        raise ValueError("Choose an email provider.")
    email = (email or "").strip().lower()
    if not valid_email(email):
        raise ValueError("Enter a valid email address.")
    host = preset["host"] if provider != "custom" else (host or "").strip().lower()
    if not _HOST.fullmatch(host):
        raise ValueError("Enter the SMTP host as a name such as smtp.example.com (not an IP address).")
    security = (security or preset["security"]).strip().lower()
    if security not in SECURITY_PORT:
        raise ValueError("Security mode must be SSL/TLS or STARTTLS.")
    if security not in preset["securities"]:
        raise ValueError(f"{preset['label']} supports only {' or '.join(SECURITY_PORT_LABEL[s] for s in preset['securities'])}.")
    expected = SECURITY_PORT[security]
    port = expected if port in (None, "") else port
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError("The SMTP port must be a number.") from None
    if port == 25:
        raise ValueError("Port 25 is not supported. Use 465 (SSL/TLS) or 587 (STARTTLS).")
    if port != expected:
        raise ValueError(f"{SECURITY_PORT_LABEL[security]} uses port {expected}, not {port}. Change the port or the security mode.")
    username = (username or "").strip() or email
    if len(username) > 254 or re.search(r"[\x00-\x1f\x7f]", username):
        raise ValueError("The SMTP username is not valid.")
    return SmtpSettings(provider, email, host, port, security, username)


SECURITY_PORT_LABEL = {"ssl": "SSL/TLS (port 465)", "starttls": "STARTTLS (port 587)"}


def settings_for(conn) -> SmtpSettings:
    """SMTP settings of a stored connection. Rows saved before this feature carry the Gmail defaults."""
    return SmtpSettings(conn.provider, conn.email, conn.smtp_host, conn.smtp_port, conn.security, conn.username)
