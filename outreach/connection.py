"""Storing and using a user's own SMTP connection (Gmail, Outlook, Yahoo or custom).

Every lookup is by owner, so a user can only ever load, use or delete their own connection.
"""

from django.utils import timezone

from . import mailer as mailer_mod
from . import relay
from .models import MailConnection
from .smtp_presets import SmtpSettings, settings_for


def verify_smtp(cfg: SmtpSettings, password: str) -> None:
    """Signs in to the user's SMTP server without sending. Raises MailError with a user-safe message.

    On Render this goes through the Vercel email service (Render cannot open SMTP connections itself)."""
    if relay.configured():
        relay.verify_via_relay(cfg, password)
    else:
        mailer_mod.verify_smtp_direct(cfg, password)


def save_smtp_connection(user, cfg: SmtpSettings, password: str, verify=None) -> MailConnection:
    """Verifies the login first (raises MailError), then stores the password encrypted. Nothing is saved on failure."""
    (verify or verify_smtp)(cfg, password)
    conn, _ = MailConnection.objects.get_or_create(owner=user, defaults={"email": cfg.email, "secret_enc": ""})
    conn.provider, conn.email = cfg.provider, cfg.email
    conn.smtp_host, conn.smtp_port, conn.security = cfg.host, cfg.port, cfg.security
    conn.smtp_username = "" if cfg.username == cfg.email else cfg.username
    conn.set_secret(password)
    conn.verified_at = timezone.now()
    conn.save()
    return conn


def active_connection(user) -> MailConnection | None:
    return MailConnection.objects.filter(owner=user).first()


def connection_for_campaign(campaign) -> MailConnection | None:
    """The sending account for a campaign is always its owner's. Callers never pass a connection in by id."""
    return active_connection(campaign.owner)


def mailer_factory_for(conn: MailConnection):
    """Returns a function that builds the mailer for this connection (decrypting the password only at that moment)."""
    cfg = settings_for(conn)
    if relay.configured():
        return lambda: relay.RelayMailer(cfg, conn.get_secret())
    return lambda: mailer_mod.SmtpMailer(cfg, conn.get_secret())


def disconnect(user) -> None:
    MailConnection.objects.filter(owner=user).delete()
