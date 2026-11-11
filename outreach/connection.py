"""Storing and using a user's Gmail SMTP connection."""

from django.utils import timezone

from . import mailer as mailer_mod
from .models import MailConnection


def save_smtp_connection(user, gmail_address: str, app_password: str, verify=None) -> MailConnection:
    """Verifies the login with Gmail first (raises MailError), then stores the app password encrypted."""
    (verify or mailer_mod.verify_gmail_credentials)(gmail_address, app_password)
    conn, _ = MailConnection.objects.get_or_create(owner=user, defaults={"email": gmail_address, "secret_enc": ""})
    conn.email = gmail_address
    conn.provider = "gmail_smtp"
    conn.set_secret(app_password)
    conn.verified_at = timezone.now()
    conn.save()
    return conn


def active_connection(user) -> MailConnection | None:
    return MailConnection.objects.filter(owner=user).first()


def mailer_factory_for(conn: MailConnection):
    return lambda: mailer_mod.GmailSmtpMailer(conn.email, conn.get_secret())


def disconnect(user) -> None:
    MailConnection.objects.filter(owner=user).delete()
