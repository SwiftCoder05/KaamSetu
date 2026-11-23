"""Contacts, email templates, Gmail connection and campaigns.

Every row belongs to one user (`owner`). Views always query through `owner=request.user`,
so one user can never read or change another user's data (tests/test_isolation.py checks this).
"""

import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from kaamsetu import crypto


def context_hash(ctx: dict) -> str:
    """Fingerprint of the job details an email was written from (to notice later edits)."""
    return hashlib.sha256(json.dumps(ctx or {}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


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
    """One user's own outgoing mail account. The password is stored encrypted (AES-256-GCM) and never shown again."""

    class Security(models.TextChoices):
        SSL = "ssl", "SSL/TLS (port 465)"
        STARTTLS = "starttls", "STARTTLS (port 587)"

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mail_connection")
    provider = models.CharField(max_length=20, default="gmail")  # gmail | outlook | yahoo | custom
    email = models.EmailField()
    smtp_host = models.CharField(max_length=253, default="smtp.gmail.com")
    smtp_port = models.PositiveIntegerField(default=465)
    security = models.CharField(max_length=8, choices=Security.choices, default=Security.SSL)
    smtp_username = models.CharField(max_length=254, blank=True, help_text="Blank means the email address.")
    secret_enc = models.TextField(help_text="Encrypted SMTP password. Never shown again after saving.")
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def username(self) -> str:
        return self.smtp_username or self.email

    @property
    def provider_label(self) -> str:
        return {"gmail": "Gmail", "outlook": "Outlook", "yahoo": "Yahoo", "custom": "Custom SMTP"}.get(self.provider, self.provider)

    def _aad(self) -> str:
        return f"mailconn:{self.owner_id}"  # ties the ciphertext to its owner

    def set_secret(self, plain: str):
        self.secret_enc = crypto.encrypt_secret(plain, self._aad())

    def get_secret(self) -> str:
        return crypto.decrypt_secret(self.secret_enc, self._aad())

    def __str__(self):
        return f"{self.email} ({self.provider})"


class Campaign(models.Model):
    class Personalisation(models.TextChoices):
        BASIC = "basic", "Basic: fill placeholders per recipient"
        AI = "ai", "AI: write a unique email per recipient"
        MANUAL = "manual", "Manual: the same email for everyone"

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
    personalisation = models.CharField(max_length=8, choices=Personalisation.choices, default=Personalisation.BASIC)
    # Inputs for AI personalisation (only used while the campaign is a draft; the generated text lives on each Recipient).
    job_description = models.TextField(blank=True)
    ai_instructions = models.CharField(max_length=1000, blank=True)
    # True: every recipient uses the campaign-level job_description (shared JD mode); False: each recipient has their own.
    shared_jd = models.BooleanField(default=False)
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
    class Source(models.TextChoices):
        TEMPLATE = "template", "Template"  # deterministic placeholder rendering (basic / manual campaigns)
        PENDING = "pending", "Waiting for AI"  # AI campaign, not generated yet; subject/body hold the template rendering meanwhile
        AI = "ai", "AI-generated"  # written by the model and passed every guard
        FALLBACK = "fallback", "Fallback: template"  # AI failed or was rejected; template rendering used instead (see content_note)
        EDITED = "edited", "Edited"  # changed by the user in the preview

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
    content_source = models.CharField(max_length=10, choices=Source.choices, default=Source.TEMPLATE)
    content_note = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    message_id = models.CharField(max_length=300, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    # AI personalisation (draft campaigns only; the send engine never reads these).
    # job_context: {"company", "designation", "job_title", "job_description", "job_url", "instructions"}. A JSON field:
    # these values are always read and written together and never filtered on, so columns would only add migrations.
    job_context = models.JSONField(default=dict, blank=True)
    gen_info = models.JSONField(default=dict, blank=True)  # last result: warnings, rationale, skills, context_hash, ...
    gen_error = models.CharField(max_length=300, blank=True)  # last generation failure (retryable)
    gen_started_at = models.DateTimeField(null=True, blank=True)  # set while a graph run owns this recipient
    generated_at = models.DateTimeField(null=True, blank=True)
    approved = models.BooleanField(default=False)  # the user pressed "Approve" on this email

    GEN_STALE_AFTER = timedelta(minutes=15)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["campaign", "email"], name="unique_recipient_per_campaign")]
        indexes = [
            models.Index(fields=["campaign", "status"]),
            models.Index(fields=["status", "sent_at"]),  # rolling 24h daily-limit count
            models.Index(fields=["status", "claimed_at"], name="recipient_stale_idx"),  # find stuck 'sending' rows
        ]

    @property
    def is_generating(self) -> bool:
        return bool(self.gen_started_at and self.gen_started_at > timezone.now() - self.GEN_STALE_AFTER)

    @property
    def gen_status(self) -> str:
        """What the review page shows: generating | pending | failed | needs_attention | ready | edited | approved | none."""
        if self.is_generating:
            return "generating"
        src = self.content_source
        if src == self.Source.PENDING:
            return "failed" if self.gen_error else "pending"
        if src == self.Source.FALLBACK:
            return "needs_attention"
        if self.approved:
            return "approved"
        return {self.Source.EDITED: "edited", self.Source.AI: "ready"}.get(src, "none")

    GEN_LABELS = {"generating": "Writing…", "pending": "Waiting for AI", "failed": "Failed: retry", "needs_attention": "Needs attention: template used",
                  "ready": "AI draft: review it", "edited": "Edited by you", "approved": "Approved"}

    @property
    def gen_label(self) -> str:
        return self.GEN_LABELS.get(self.gen_status) or self.get_content_source_display()

    @property
    def context_changed(self) -> bool:
        """The job details were changed after this email was written, so it may no longer match them."""
        h = (self.gen_info or {}).get("context_hash")
        return bool(h) and h != context_hash(self.job_context)

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
