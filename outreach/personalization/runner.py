"""Draft generation for campaign recipients: claim → run the graph → save. Draft campaigns only.

Guarantees (each covered by tests):
- One graph run per recipient. A recipient is CLAIMED with a conditional UPDATE (gen_started_at), so two browser
  workers can never generate the same recipient, and a stale claim (crashed request) is reclaimable after 15 minutes.
- The result is saved with a conditional UPDATE that still requires our claim. If the user edited the email meanwhile
  (editing clears the claim), or the campaign was confirmed, the result is discarded: nothing is ever overwritten.
- A failure affects only that recipient; other recipients' drafts are untouched. Failed recipients keep their error and
  can be retried on their own.
- Nothing here sends mail, and the send engine never imports this module.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q
from django.utils import timezone
from langgraph.errors import GraphRecursionError

from resumes.ai.provider import AiOutputError, AiProvider, AiQuotaError, AiUnavailableError

from ..models import Campaign, CampaignAttachment, Recipient, context_hash
from ..render import render_email
from ..richtext import html_to_text
from .service import RecipientRequest, run_graph

MAX_NOTE = 300


@dataclass
class Outcome:
    kind: str  # saved | fallback | failed | discarded | kept
    recipient: Recipient
    message: str = ""


def _stale_cutoff():
    return timezone.now() - Recipient.GEN_STALE_AFTER


def claimable(c: Campaign):
    """Pending recipients nobody is generating right now. Failed ones (gen_error set) need an explicit retry."""
    return c.recipients.filter(content_source=Recipient.Source.PENDING, gen_error="").filter(
        Q(gen_started_at__isnull=True) | Q(gen_started_at__lt=_stale_cutoff())
    )


def generating_count(c: Campaign) -> int:
    return c.recipients.filter(gen_started_at__gt=_stale_cutoff()).count()


def claim(pk: int, *, overwrite: bool = False) -> datetime | None:
    """Atomically marks one recipient as being generated. Returns the claim marker, or None if it is not claimable."""
    now = timezone.now()
    qs = Recipient.objects.filter(pk=pk, campaign__status=Campaign.Status.DRAFT).filter(
        Q(gen_started_at__isnull=True) | Q(gen_started_at__lt=_stale_cutoff())
    )
    if not overwrite:
        qs = qs.filter(content_source=Recipient.Source.PENDING)
    return now if qs.update(gen_started_at=now, gen_error="") else None


def release(pk: int, started: datetime) -> None:
    Recipient.objects.filter(pk=pk, gen_started_at=started).update(gen_started_at=None)


def build_request(c: Campaign, r: Recipient) -> RecipientRequest | None:
    """Plain data for the graph. None when the template was deleted (nothing to personalise from)."""
    t = c.template
    if t is None:
        return None
    ctx = r.job_context or {}
    company, designation = ctx.get("company", ""), ctx.get("designation", "")
    job_title = ctx.get("job_title", "")
    rendered = render_email(t.subject, t.body, job_title or t.role_label, {"name": r.name, "company": company, "role_title": designation}, c.sender_name)
    version = c.resume_version
    others = [(o or {}).get("company", "") for o in c.recipients.exclude(pk=r.pk).values_list("job_context", flat=True)]
    return RecipientRequest(
        campaign_id=c.pk, recipient_id=r.pk, sender_name=c.sender_name, resume_data=version.data if version else {},
        resume_version_id=version.pk if version else 0, template_subject=rendered.subject, template_text=html_to_text(rendered.body_html),
        recipient_name=r.name, company=company, designation=designation, job_title=job_title,
        job_description=ctx.get("job_description", "") or (c.job_description if c.shared_jd else ""),
        company_notes=ctx.get("company_notes", ""), instructions=ctx.get("instructions", "") or c.ai_instructions,
        resume_attached=bool(version) or CampaignAttachment.objects.filter(campaign=c).exists(), other_companies=[o for o in others if o],
    )


def _info(result: dict, r: Recipient) -> dict:
    return {
        "warnings": result.get("warnings", [])[:8], "errors": result.get("errors", [])[:8], "rationale": result.get("rationale", "")[:600],
        "mode": result.get("mode", ""), "job_title": result.get("job_title", ""), "matched_skills": result.get("matched_skills", [])[:12],
        "missing_skills": result.get("missing_skills", [])[:12], "revision_count": result.get("revision_count", 0),
        "model": result.get("model", ""), "context_hash": context_hash(r.job_context),
        "evidence": [{"id": e["id"], "where": e["where"], "text": e["text"][:200]} for e in result.get("evidence", [])[:6]],
    }


def generate(c: Campaign, r: Recipient, provider: AiProvider, started: datetime, *, overwrite: bool = False) -> Outcome:
    """Runs the graph for a recipient this caller has CLAIMED (`started` is the marker from claim()). Provider outages and
    quota errors propagate after the claim is released; every other outcome is returned."""
    req = build_request(c, r)
    if req is None:
        Recipient.objects.filter(pk=r.pk, gen_started_at=started).update(gen_started_at=None, gen_error="The template was deleted, so this email cannot be written.")
        r.refresh_from_db()
        return Outcome("failed", r, "The template was deleted, so this email cannot be written.")
    try:
        result = run_graph(req, provider)
    except (AiQuotaError, AiUnavailableError):
        release(r.pk, started)
        raise
    except (AiOutputError, GraphRecursionError) as e:
        msg = (str(e) if isinstance(e, AiOutputError) else "The AI workflow did not finish. Try again.")[:MAX_NOTE]
        Recipient.objects.filter(pk=r.pk, gen_started_at=started).update(gen_started_at=None, gen_error=msg)
        r.refresh_from_db()
        return Outcome("failed", r, msg)
    except Exception:
        release(r.pk, started)
        raise

    now = timezone.now()
    info = _info(result, r)
    if result["ready"]:
        saved = Recipient.objects.filter(pk=r.pk, campaign__status=Campaign.Status.DRAFT, gen_started_at=started).update(
            subject=result["subject"][:400], body=result["body_html"], content_source=Recipient.Source.AI,
            content_note=f"Written by {result.get('model', 'AI')}"[:MAX_NOTE], gen_info=info, gen_error="", gen_started_at=None,
            generated_at=now, approved=False,
        )
        r.refresh_from_db()
        return Outcome("saved" if saved else "discarded", r, "" if saved else "The email changed while it was being written, so the AI result was not used.")

    reason = (result.get("errors") or ["the draft did not pass the checks"])[0]
    reason = reason.rstrip(".")
    if overwrite or r.content_source != Recipient.Source.PENDING:
        # Regenerating over an existing email: keep the current one exactly as it is.
        Recipient.objects.filter(pk=r.pk, gen_started_at=started).update(gen_started_at=None, gen_info={**(r.gen_info or {}), "last_rejected": info["errors"]})
        r.refresh_from_db()
        return Outcome("kept", r, f"The new draft was rejected: {reason}.")
    # First generation: the user's own template (already stored, rendered for this recipient) stays, clearly marked.
    saved = Recipient.objects.filter(pk=r.pk, campaign__status=Campaign.Status.DRAFT, gen_started_at=started).update(
        content_source=Recipient.Source.FALLBACK, content_note=f"AI draft not used: {reason}. Using your template instead."[:MAX_NOTE],
        gen_info=info, gen_error="", gen_started_at=None, generated_at=now, approved=False,
    )
    r.refresh_from_db()
    return Outcome("fallback" if saved else "discarded", r, reason)
