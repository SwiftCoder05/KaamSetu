"""Contacts, email templates, Gmail connection and campaigns.

Every row belongs to one user (`owner`). Views always query through `owner=request.user`,
so one user can never read or change another user's data (tests/test_isolation.py checks this).
"""

from django.conf import settings
from django.db import models

from kaamsetu import crypto


class Contact(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contacts")
    email = models.EmailField(max_length=254)
    name = models.CharField(max_length=120, blank=True)
    company = models.CharField(max_length=120, blank=True)
    role_title = models.CharField(max_length=120, blank=True)
    do_not_contact = models.BooleanField(default=False, help_text="Never include this address in a campaign.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["owner", "email"], name="unique_contact_per_owner")]
        indexes = [models.Index(fields=["owner", "-created_at"], name="contact_owner_created_idx")]

    def __str__(self):
        return self.email


class EmailTemplate(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_templates")
    name = models.CharField(max_length=120)
    role_label = models.CharField(max_length=120, blank=True, help_text="Fills {{role}}. Leave empty to use each contact's role.")
    subject = models.CharField(max_length=300)
    body = models.TextField(help_text="Sanitised HTML.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["owner", "-updated_at"], name="template_owner_updated_idx")]

    def __str__(self):
        return self.name


class MailConnection(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_connection")
    provider = models.CharField(max_length=20, default="gmail_smtp")
    email = models.EmailField()
    secret_enc = models.TextField(help_text="Encrypted app password. Never shown again after saving.")
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def set_secret(self, plain: str):
        self.secret_enc = crypto.encrypt(plain)

    def get_secret(self) -> str:
        return crypto.decrypt(self.secret_enc)

    def __str__(self):
        return f"{self.email} ({self.provider})"


class Campaign(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        CONFIRMED = "confirmed"
        SENDING = "sending"
        PAUSED = "paused"
        COMPLETED = "completed"
        CANCELLED = "cancelled"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=160)
    template = models.ForeignKey(EmailTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns")
    resume_version = models.ForeignKey("resumes.ResumeVersion", on_delete=models.SET_NULL, null=True, blank=True, related_name="campaigns")
    sender_name = models.CharField(max_length=120)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    pause_reason = models.CharField(max_length=40, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["owner", "-created_at"], name="campaign_owner_created_idx"),
            models.Index(fields=["status"], name="campaign_status_idx"),  # background sender looks up runnable campaigns
        ]

    def __str__(self):
        return self.name

    def counts(self) -> dict:
        rows = self.recipients.values("status").annotate(n=models.Count("id"))
        out = {s: 0 for s in Recipient.Status.values}
        for r in rows:
            out[r["status"]] = r["n"]
        out["total"] = sum(out[s] for s in Recipient.Status.values)
        return out


class CampaignAttachment(models.Model):
    """The resume PDF, rendered once at confirmation so every recipient gets exactly what was previewed.

    Kept out of `Campaign` so listing campaigns never loads file bytes. To move files to object storage
    later (S3, Supabase Storage), replace `data` with a key; nothing else in the app needs to change.
    """

    campaign = models.OneToOneField(Campaign, on_delete=models.CASCADE, related_name="attachment")
    filename = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100, default="application/pdf")
    size = models.PositiveIntegerField(default=0)
    data = models.BinaryField(editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.filename} ({self.size} bytes)"


class Recipient(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        SENDING = "sending"
        ACCEPTED = "accepted"  # Gmail accepted it for delivery; not proof that it reached the inbox
        FAILED = "failed"
        SKIPPED = "skipped"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="recipients")
    contact = models.ForeignKey(Contact, on_delete=models.SET_NULL, null=True, blank=True)
    email = models.EmailField()
    name = models.CharField(max_length=120, blank=True)
    subject = models.CharField(max_length=400)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    message_id = models.CharField(max_length=300, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["campaign", "email"], name="unique_recipient_per_campaign")]
        indexes = [
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["status", "sent_at"]),  # rolling 24h daily-limit count
            models.Index(fields=["status", "claimed_at"], name="recipient_stale_idx"),  # find stuck 'sending' rows
        ]

    def __str__(self):
        return f"{self.email} [{self.status}]"


class SendLog(models.Model):
    recipient = models.ForeignKey(Recipient, on_delete=models.CASCADE, related_name="logs")
    event = models.CharField(max_length=12)  # attempt | retry | accepted | failed | skipped
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["created_at"], name="sendlog_created_idx")]  # lets old logs be purged cheaply
