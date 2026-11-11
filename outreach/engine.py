"""Campaign send engine.

Safety rules:
- Nothing is sent unless the campaign was explicitly confirmed by its owner.
- A per-user rolling 24h limit caps how many messages Gmail is asked to accept.
- Each recipient row is claimed atomically, so two workers can never send the same email twice.
- Rate-limit and auth errors pause the campaign and put the recipient back; nothing is wrongly failed.
"""

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .mailer import Attachment, MailError, OutgoingMessage
from .models import Campaign, CampaignAttachment, Contact, Recipient, SendLog
from .render import has_unresolved_placeholders
from .richtext import email_document, html_to_text, sanitize_html

log = logging.getLogger("kaamsetu.send")

STALE_AFTER = timedelta(minutes=15)


class NotConfirmedError(Exception):
    def __init__(self):
        super().__init__("This campaign has not been explicitly confirmed, so nothing was sent.")


@dataclass
class BatchResult:
    processed: int = 0
    accepted: int = 0
    failed: int = 0
    skipped: int = 0
    remaining: int = 0
    status: str = "sending"
    pause_reason: str = ""


def accepted_last_24h(user, now=None) -> int:
    since = (now or timezone.now()) - timedelta(hours=24)
    return Recipient.objects.filter(campaign__owner=user, status=Recipient.Status.ACCEPTED, sent_at__gte=since).count()


def _log(r: Recipient, event: str, detail: str = ""):
    SendLog.objects.create(recipient=r, event=event, detail=detail[:300])


def _set_status(c: Campaign, status: str, reason: str = ""):
    c.status = status
    c.pause_reason = reason
    fields = ["status", "pause_reason"]
    if status == Campaign.Status.COMPLETED:
        c.completed_at = timezone.now()
        fields.append("completed_at")
    c.save(update_fields=fields)


def _claim(c: Campaign, n: int, now) -> list[Recipient]:
    """Conditional UPDATE per row: only the worker whose update matched owns the row (race-safe on any DB)."""
    claimed = []
    for rid in c.recipients.filter(status=Recipient.Status.QUEUED).values_list("id", flat=True)[: n * 2]:
        if len(claimed) >= n:
            break
        if Recipient.objects.filter(pk=rid, status=Recipient.Status.QUEUED).update(status=Recipient.Status.SENDING, claimed_at=now):
            claimed.append(Recipient.objects.get(pk=rid))
    return claimed


def _expire_stale(c: Campaign, now):
    """A row stuck in 'sending' means a worker died mid-send; it may or may not have gone out, so it is never resent blindly."""
    for r in c.recipients.filter(status=Recipient.Status.SENDING, claimed_at__lt=now - STALE_AFTER):
        r.status = Recipient.Status.FAILED
        r.error = "Interrupted while sending. It may have been delivered: check your Gmail Sent folder before retrying."
        r.save(update_fields=["status", "error"])
        _log(r, "failed", "stale claim")


def _release(r: Recipient, error: str = ""):
    r.status = Recipient.Status.QUEUED
    r.error = error[:300]
    r.claimed_at = None
    r.save(update_fields=["status", "error", "claimed_at"])


def _fail(r: Recipient, error: str):
    r.status = Recipient.Status.FAILED
    r.error = error[:300]
    r.save(update_fields=["status", "error"])
    _log(r, "failed", error)


def process_batch(
    campaign: Campaign,
    mailer_factory: Callable[[], object],
    *,
    from_email: str,
    batch_size: int | None = None,
    max_retries: int = 2,
    max_total_attempts: int = 5,
    gap_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], object] = timezone.now,
) -> BatchResult:
    batch_size = batch_size or settings.SEND_BATCH_SIZE
    gap = settings.SEND_GAP_SECONDS if gap_seconds is None else gap_seconds
    c = Campaign.objects.get(pk=campaign.pk)

    # A finished campaign has nothing left to send; a repeated call is a harmless no-op.
    if c.confirmed_at and c.status == Campaign.Status.COMPLETED:
        return BatchResult(status="completed")
    # Explicit-confirmation gate: importing contacts or creating a draft can never cause a send.
    if not c.confirmed_at or c.status not in (Campaign.Status.CONFIRMED, Campaign.Status.SENDING, Campaign.Status.PAUSED):
        raise NotConfirmedError()

    t = now()
    result = BatchResult()
    _expire_stale(c, t)

    budget = c.owner.profile.daily_limit - accepted_last_24h(c.owner, t)
    if budget <= 0:
        _set_status(c, Campaign.Status.PAUSED, "daily_limit")
        return BatchResult(remaining=c.recipients.filter(status=Recipient.Status.QUEUED).count(), status="paused", pause_reason="daily_limit")

    _set_status(c, Campaign.Status.SENDING)
    claimed = _claim(c, min(batch_size, budget), t)
    blocked = set(
        Contact.objects.filter(owner=c.owner, do_not_contact=True, email__in=[r.email for r in claimed]).values_list("email", flat=True)
    )
    att = CampaignAttachment.objects.filter(campaign=c).first()
    attachment = Attachment(att.filename, att.content_type, bytes(att.data)) if att else None

    mailer = None
    pause = ""
    try:
        for i, r in enumerate(claimed):
            if pause:
                _release(r)
                continue
            result.processed += 1
            if r.email in blocked:
                r.status = Recipient.Status.SKIPPED
                r.error = "Recipient is marked as do-not-contact"
                r.save(update_fields=["status", "error"])
                _log(r, "skipped", "do not contact")
                result.skipped += 1
                continue
            body_html = sanitize_html(r.body)  # re-sanitised at send time (defence in depth)
            text = html_to_text(body_html)
            if has_unresolved_placeholders(r.subject) or has_unresolved_placeholders(text):
                _fail(r, "Unfilled {{placeholder}} in the message")
                result.failed += 1
                continue
            if r.attempts >= max_total_attempts:
                _fail(r, "Too many attempts")
                result.failed += 1
                continue

            if mailer is None:
                mailer = mailer_factory()
            for attempt in range(max_retries + 1):
                r.attempts += 1
                r.save(update_fields=["attempts"])
                _log(r, "attempt" if attempt == 0 else "retry", f"attempt {r.attempts}")
                try:
                    message_id = mailer.send(
                        OutgoingMessage(
                            from_email=from_email,
                            from_name=c.sender_name,
                            to=r.email,
                            subject=r.subject,
                            text=text,
                            html=email_document(body_html),
                            attachment=attachment,
                        )
                    )
                except MailError as err:
                    if err.kind in ("transient", "rate_limit") and attempt < max_retries:
                        sleep(min(err.retry_after or 2**attempt, 15))
                        continue
                    if err.kind in ("rate_limit", "auth"):
                        # Not this recipient's fault: put it back and stop, so nothing is lost or wrongly failed.
                        _release(r, err.message)
                        _log(r, "failed", err.message)
                        pause = "provider_rate_limit" if err.kind == "rate_limit" else "auth_error"
                        result.processed -= 1
                    else:
                        _fail(r, err.message)
                        result.failed += 1
                    break
                else:
                    r.status = Recipient.Status.ACCEPTED
                    r.message_id = message_id[:300]
                    r.sent_at = timezone.now()
                    r.error = ""
                    r.save(update_fields=["status", "message_id", "sent_at", "error"])
                    _log(r, "accepted", "Gmail accepted the message")
                    result.accepted += 1
                    break
            if gap and i < len(claimed) - 1 and not pause:
                sleep(gap)
    finally:
        if mailer is not None and hasattr(mailer, "close"):
            mailer.close()

    result.remaining = c.recipients.filter(status=Recipient.Status.QUEUED).count()
    if pause:
        _set_status(c, Campaign.Status.PAUSED, pause)
        result.status, result.pause_reason = "paused", pause
    elif result.remaining == 0 and not c.recipients.filter(status=Recipient.Status.SENDING).exists():
        _set_status(c, Campaign.Status.COMPLETED)
        result.status = "completed"
    log.info("campaign %s batch: %s", c.pk, result)
    return result


def retry_failed(c: Campaign) -> int:
    """Puts failed recipients back in the queue (the user asked for it explicitly)."""
    c.refresh_from_db()
    n = c.recipients.filter(status=Recipient.Status.FAILED).update(status=Recipient.Status.QUEUED, error="", claimed_at=None, attempts=0)
    if n and c.status in (Campaign.Status.COMPLETED, Campaign.Status.PAUSED):
        _set_status(c, Campaign.Status.CONFIRMED)
    return n


def cancel(c: Campaign) -> int:
    c.refresh_from_db()
    n = c.recipients.filter(status=Recipient.Status.QUEUED).update(status=Recipient.Status.SKIPPED, error="Campaign cancelled")
    _set_status(c, Campaign.Status.CANCELLED)
    return n


def runnable_campaigns():
    """Campaigns a background worker may continue: confirmed/sending, or paused only by the daily limit."""
    return Campaign.objects.filter(confirmed_at__isnull=False).filter(
        Q(status__in=[Campaign.Status.CONFIRMED, Campaign.Status.SENDING])
        | Q(status=Campaign.Status.PAUSED, pause_reason="daily_limit")
    )
