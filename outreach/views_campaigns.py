import json
import os
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from kaamsetu.throttle import hit
from resumes.ai.provider import AiQuotaError, AiUnavailableError, provider_from_settings, provider_is_external, provider_label
from resumes.export import safe_filename, to_pdf
from resumes.models import ResumeVersion

from . import engine
from .connection import active_connection, connection_for_campaign, mailer_factory_for
from .files import FileExtractError, detect_file_kind
from .models import Campaign, CampaignAttachment, Contact, EmailTemplate, Recipient
from .personalization import runner, validators
from .render import has_unresolved_placeholders, render_email
from .richtext import MAX_BODY_CHARS, html_to_text, is_blank, sanitize_html
from types import SimpleNamespace

MAX_RECIPIENTS = 500


def _owned(request, pk) -> Campaign:
    return get_object_or_404(Campaign, pk=pk, owner=request.user)


def _is_unfilled(subject: str, body: str) -> bool:
    return has_unresolved_placeholders(subject) or has_unresolved_placeholders(html_to_text(body))


def _ai_context(user) -> dict:
    """What the campaign pages show about AI, so users see where recipient data would go before choosing it."""
    return {
        "ai_enabled": user.profile.ai_enabled,
        "ai_configured": provider_from_settings() is not None,
        "ai_label": provider_label(),
        "ai_external": provider_is_external(),
        "ai_max_recipients": settings.AI_CAMPAIGN_MAX_RECIPIENTS,
        "ai_per_hour": settings.AI_CAMPAIGN_PER_HOUR,
        "ai_per_day": settings.AI_CAMPAIGN_PER_DAY,
    }


def _resume_versions(user):
    return ResumeVersion.objects.filter(resume__owner=user).select_related("resume").order_by("resume__title", "-created_at")


@login_required
def campaign_list(request):
    campaigns = list(Campaign.objects.filter(owner=request.user))
    for c in campaigns:
        c.c = c.counts()
    return render(request, "outreach/campaigns.html", {"campaigns": campaigns})


def _collect_people(request, contacts) -> tuple[list[dict], list[str]]:
    """Everyone this campaign is for: saved contacts picked in the dialog, plus people typed in directly. The typed ones
    live only on the campaign (they are NOT added to the Contacts list). Returns (people, problems)."""
    people, seen, problems = [], set(), []
    for ct in contacts.filter(pk__in=request.POST.getlist("contacts")):
        if ct.email.lower() not in seen:
            seen.add(ct.email.lower())
            people.append({"contact": ct, "email": ct.email, "name": ct.name, "company": ct.company, "role_title": ct.role_title})
    try:
        extra = json.loads(request.POST.get("new_recipients") or "[]")
    except json.JSONDecodeError:
        extra = []
        problems.append("The added email addresses could not be read. Add them again.")
    blocked = {e.lower() for e in Contact.objects.filter(owner=request.user, do_not_contact=True).values_list("email", flat=True)}
    for item in (extra if isinstance(extra, list) else [])[: MAX_RECIPIENTS + 1]:
        if not isinstance(item, dict):
            continue
        email = str(item.get("email", "")).strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            problems.append(f"“{email[:60]}” is not a valid email address.")
            continue
        if email in blocked:
            problems.append(f"{email} is on your do-not-contact list, so it was not added.")
            continue
        if email in seen:
            continue
        cleaned, errs = validators.clean_context({"name": item.get("name"), "company": item.get("company"), "designation": item.get("role_title")})
        if errs:
            problems.append(f"{email}: " + " ".join(errs.values()))
            continue
        seen.add(email)
        people.append({"contact": None, "email": email, "name": cleaned["name"], "company": cleaned["company"], "role_title": cleaned["designation"]})
    return people, problems


@login_required
def campaign_new(request):
    user = request.user
    templates = EmailTemplate.objects.filter(owner=user)
    contacts = Contact.objects.filter(owner=user, do_not_contact=False)
    versions = _resume_versions(user)
    default_source = "studio" if versions.exists() else "upload"
    ctx = {"templates": templates, "contacts": contacts, "versions": versions, "errors": {}, "form": request.POST,
           "contacts_data": [{"id": c.pk, "email": c.email, "name": c.name, "company": c.company, "role_title": c.role_title} for c in contacts],
           "initial_recipients": [], "max_mb": settings.MAX_UPLOAD_BYTES // (1024 * 1024),
           "personalisation": request.POST.get("personalisation") or Campaign.Personalisation.BASIC, **_ai_context(user),
           "resume_source": request.POST.get("resume_source")
           or (("studio" if request.POST.get("resume_version") else "none") if request.method == "POST" else default_source)}
    if request.method == "GET":
        ctx["form"] = {"template": request.GET.get("template", ""), "name": ""}
        return render(request, "outreach/campaign_new.html", ctx)

    errors = ctx["errors"]
    name = request.POST.get("name", "").strip()[:160]
    template = templates.filter(pk=request.POST.get("template") or 0).first()
    picked, problems = _collect_people(request, contacts)
    ctx["initial_recipients"] = [{"id": p["contact"].pk if p["contact"] else None, "email": p["email"], "name": p["name"],
                                  "company": p["company"], "role_title": p["role_title"]} for p in picked]
    if not template:
        errors["template"] = "Choose a template."
    if problems:
        errors["contacts"] = " ".join(problems)
    elif not picked:
        errors["contacts"] = "Add at least one recipient."
    elif len(picked) > MAX_RECIPIENTS:
        errors["contacts"] = f"A campaign can have at most {MAX_RECIPIENTS} recipients."

    mode = ctx["personalisation"]
    if mode not in Campaign.Personalisation.values:
        errors["personalisation"] = "Choose how to personalise the emails."
    elif mode == Campaign.Personalisation.AI:
        if not ctx["ai_enabled"]:
            errors["personalisation"] = "AI is switched off. Turn it on in Settings to use AI personalisation, or pick another option."
        elif not ctx["ai_configured"]:
            errors["personalisation"] = "AI is not configured on this server. Pick another option."
        elif len(picked) > settings.AI_CAMPAIGN_MAX_RECIPIENTS:
            errors["personalisation"] = f"AI personalisation works for up to {settings.AI_CAMPAIGN_MAX_RECIPIENTS} recipients per campaign."
        elif ctx["ai_external"] and request.POST.get("ai_consent") != "yes":
            errors["personalisation"] = "Tick the box to confirm recipient details may be sent to the external AI provider."

    source = ctx["resume_source"]
    version, upload = None, None
    if source == "studio":
        version = versions.filter(pk=request.POST.get("resume_version") or 0).first()
        if not version:
            errors["resume"] = "Choose a resume from the resume studio, or pick another option."
    elif source == "upload":
        try:
            upload = _read_resume_upload(request.FILES.get("resume_file"))
        except ValueError as e:
            errors["resume"] = str(e)
    elif source != "none":
        errors["resume"] = "Choose how to attach your resume."
    if errors:
        if upload is None and source == "upload" and "resume" not in errors:
            errors["resume"] = "Please choose your resume file again (browsers forget it when a form has errors)."
        return render(request, "outreach/campaign_new.html", ctx)

    sender = user.profile.sender_name
    with transaction.atomic():
        c = Campaign.objects.create(
            owner=user, name=name or f"{template.name} · {timezone.localdate():%d %b}", template=template,
            resume_version=version, sender_name=sender, personalisation=mode,
        )
        # AI recipients start as "pending" holding the template rendering; the job details are filled in on the next page
        # (campaign_context) and the emails are generated on the review page, one recipient per graph run.
        is_ai = mode == Campaign.Personalisation.AI
        source = Recipient.Source.PENDING if is_ai else Recipient.Source.TEMPLATE
        rows = []
        for p in picked:  # already de-duplicated by e-mail address
            # Manual: every recipient gets the identical email (recipient placeholders use their fallbacks).
            values = {} if mode == Campaign.Personalisation.MANUAL else {"name": p["name"], "company": p["company"], "role_title": p["role_title"]}
            r = render_email(template.subject, template.body, template.role_label, values, sender)
            # An AI campaign may use a name read from the address (kept on the campaign only; shown as "guessed" on page 2).
            name = p["name"] or (validators.guess_name_from_email(p["email"]) if is_ai else "")
            if is_ai and name != p["name"]:
                r = render_email(template.subject, template.body, template.role_label, {"name": name, "company": p["company"], "role_title": p["role_title"]}, sender)
            rows.append(Recipient(campaign=c, contact=p["contact"], email=p["email"], name=name, subject=r.subject[:400], body=r.body_html,
                                  content_source=source,
                                  job_context=validators.empty_context(SimpleNamespace(company=p["company"], role_title=p["role_title"])) if is_ai else {}))
        Recipient.objects.bulk_create(rows)
        if upload:
            filename, content_type, data = upload
            CampaignAttachment.objects.create(campaign=c, filename=filename, content_type=content_type, data=data, size=len(data))
    return redirect("campaign_context" if is_ai else "campaign_detail", pk=c.pk)


RESUME_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
}


def _read_resume_upload(f):
    """Validates an uploaded resume (type checked by content, not just the name) and returns (filename, type, bytes)."""
    if not f:
        raise ValueError("Choose your resume file, or pick another option.")
    if f.size > settings.MAX_UPLOAD_BYTES:
        raise ValueError(f"The file is larger than {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    data = f.read()
    try:
        kind = detect_file_kind(f.name, data)
    except FileExtractError as e:
        if e.code == "unsupported_type":
            raise ValueError("Attach your resume as a PDF or Word file (.pdf, .docx or .doc).") from e
        raise ValueError(e.message) from e
    if kind not in RESUME_TYPES:
        raise ValueError("Attach your resume as a PDF or Word file (.pdf, .docx or .doc).")
    stem = os.path.splitext(os.path.basename(f.name))[0]
    clean = re.sub(r"[^A-Za-z0-9 ._()-]+", "", stem).strip(" .")[:120] or "Resume"
    return f"{clean}.{kind}", RESUME_TYPES[kind], data


@login_required
def campaign_detail(request, pk):
    c = _owned(request, pk)
    recipients = list(c.recipients.all())
    for r in recipients:
        r.unfilled = _is_unfilled(r.subject, r.body)
    return render(
        request,
        "outreach/campaign_detail.html",
        {
            "c": c,
            # uploaded file (or the rendered studio PDF after confirming); bytes are not loaded here
            "attachment": CampaignAttachment.objects.filter(campaign=c).defer("data").first(),
            "counts": c.counts(),
            "recipients": recipients,
            "unfilled": sum(1 for r in recipients if r.unfilled),
            "pending": sum(1 for r in recipients if r.content_source == Recipient.Source.PENDING and not r.gen_error),
            "failed": sum(1 for r in recipients if r.content_source == Recipient.Source.PENDING and r.gen_error),
            "fallbacks": sum(1 for r in recipients if r.content_source == Recipient.Source.FALLBACK),
            "concurrency": max(1, settings.AI_CAMPAIGN_CONCURRENCY),
            "is_ai": c.personalisation == Campaign.Personalisation.AI,
            **_ai_context(request.user),
            "sample": recipients[0] if recipients else None,
            "conn": active_connection(request.user),
            "sent_24h": engine.accepted_last_24h(request.user),
            "daily_limit": request.user.profile.daily_limit,
            "autostart": request.GET.get("autostart") == "1",
        },
    )


@login_required
@require_POST
def campaign_confirm(request, pk):
    """The ONLY way a campaign becomes sendable: its owner ticks the box and presses Send."""
    c = _owned(request, pk)
    if c.status != Campaign.Status.DRAFT:
        return redirect("campaign_detail", pk=c.pk)
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the confirmation box to send.")
        return redirect("campaign_detail", pk=c.pk)
    if not active_connection(request.user):
        messages.error(request, "Connect your email account in Settings before sending.")
        return redirect("campaign_detail", pk=c.pk)
    recipients = list(c.recipients.only("subject", "body", "content_source", "gen_started_at"))
    if any(r.content_source == Recipient.Source.PENDING or r.is_generating for r in recipients):
        messages.error(request, "Some emails are still being written or failed. Let AI finish, retry the failed ones, or use your template for the rest.")
        return redirect("campaign_detail", pk=c.pk)
    if any(_is_unfilled(r.subject, r.body) for r in recipients):
        messages.error(request, "Some emails still contain a {{placeholder}}. Edit them, or fix the template or contacts, before sending.")
        return redirect("campaign_detail", pk=c.pk)
    pdf = None
    if c.resume_version_id:
        v = c.resume_version
        try:
            pdf = to_pdf(v.parsed())
        except Exception:  # noqa: BLE001
            messages.error(request, "The resume could not be converted to PDF. Open it in the resume studio and check it, or send without it.")
            return redirect("campaign_detail", pk=c.pk)
    with transaction.atomic():
        # Conditional update: the draft -> confirmed switch happens once. From here on every edit or AI write
        # (they only touch recipients of DRAFT campaigns) is refused, so the previewed content is frozen.
        if not Campaign.objects.filter(pk=c.pk, status=Campaign.Status.DRAFT).update(
            status=Campaign.Status.CONFIRMED, confirmed_at=timezone.now()
        ):
            return redirect("campaign_detail", pk=c.pk)
        if pdf is not None:
            CampaignAttachment.objects.update_or_create(
                campaign=c,
                defaults={"filename": safe_filename(v.parsed().basics.name or v.resume.title, "pdf"), "data": pdf, "size": len(pdf)},
            )
    return _autostart(c)


def _autostart(c: Campaign):
    return redirect(reverse("campaign_detail", args=[c.pk]) + "?autostart=1")


def _state(c: Campaign, extra: dict | None = None) -> dict:
    c.refresh_from_db()
    return {
        "status": c.status,
        "pause_reason": c.pause_reason,
        "counts": c.counts(),
        "recipients": list(c.recipients.values("id", "status", "error")),
        **(extra or {}),
    }


@login_required
@require_POST
def campaign_send(request, pk):
    """Sends one small batch. The campaign page calls this in a loop while it is open;
    `python manage.py run_sender` does the same in the background."""
    c = _owned(request, pk)
    if c.status == Campaign.Status.PAUSED and c.pause_reason == "user":
        return JsonResponse(_state(c))
    if c.status in (Campaign.Status.COMPLETED, Campaign.Status.CANCELLED):
        return JsonResponse(_state(c))
    conn = connection_for_campaign(c)  # always the campaign owner's own account
    if not conn:
        return JsonResponse(_state(c, {"error": "Connect your email account in Settings before sending."}), status=400)
    try:
        result = engine.process_batch(c, mailer_factory_for(conn), from_email=conn.email)
    except engine.NotConfirmedError as e:
        return JsonResponse(_state(c, {"error": str(e)}), status=409)
    except ValueError as e:  # e.g. the stored app password can no longer be decrypted
        return JsonResponse(_state(c, {"error": str(e)}), status=400)
    return JsonResponse(_state(c, {"batch": {"accepted": result.accepted, "failed": result.failed, "skipped": result.skipped}}))


@login_required
@require_POST
def campaign_action(request, pk, action):
    c = _owned(request, pk)
    if action == "pause" and c.status in (Campaign.Status.CONFIRMED, Campaign.Status.SENDING):
        c.status, c.pause_reason = Campaign.Status.PAUSED, "user"
        c.save(update_fields=["status", "pause_reason"])
        messages.info(request, "Paused. Nothing more will be sent until you resume.")
    elif action == "resume" and c.status == Campaign.Status.PAUSED:
        c.status, c.pause_reason = Campaign.Status.SENDING, ""
        c.save(update_fields=["status", "pause_reason"])
        return _autostart(c)
    elif action == "cancel" and c.status not in (Campaign.Status.COMPLETED, Campaign.Status.CANCELLED, Campaign.Status.DRAFT):
        n = engine.cancel(c)
        messages.info(request, f"Campaign cancelled. {n} unsent email(s) will not be sent.")
    elif action == "retry" and c.confirmed_at:
        n = engine.retry_failed(c)
        if n:
            return _autostart(c)
        messages.info(request, "There are no failed emails to retry.")
    elif action == "use_template" and c.status == Campaign.Status.DRAFT:
        # Clearing gen_started_at also cancels any run in flight: its result will be discarded, never saved over this choice.
        n = c.recipients.filter(content_source=Recipient.Source.PENDING).update(
            content_source=Recipient.Source.FALLBACK, content_note="AI personalisation skipped. Using your template instead.",
            gen_started_at=None, gen_error="",
        )
        messages.info(request, f"{n} email(s) will use your template instead of AI.")
    elif action == "delete" and c.status in (Campaign.Status.DRAFT, Campaign.Status.COMPLETED, Campaign.Status.CANCELLED):
        c.delete()
        messages.success(request, "Campaign deleted.")
        return redirect("campaign_list")
    return redirect("campaign_detail", pk=c.pk)


# ───────────── recipient-level personalisation (draft campaigns only; nothing here sends mail) ─────────────

FROZEN = {"error": "This campaign has been confirmed, so its emails can no longer be changed."}
NOT_AI = {"error": "This campaign does not use AI personalisation."}


def _recipient_json(r: Recipient) -> dict:
    info = r.gen_info or {}
    ctx = r.job_context or {}
    return {
        "id": r.pk, "email": r.email, "name": r.name, "subject": r.subject, "body_html": sanitize_html(r.body), "source": r.content_source,
        "label": r.get_content_source_display(), "gen_label": r.gen_label, "note": r.content_note, "unfilled": _is_unfilled(r.subject, r.body),
        "gen_status": r.gen_status, "approved": r.approved, "error": r.gen_error, "stale": r.context_changed,
        "company": ctx.get("company", ""), "job_title": ctx.get("job_title", "") or info.get("job_title", ""),
        "warnings": info.get("warnings", []), "rationale": info.get("rationale", ""),
    }


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _ai_ready(request):
    """Opt-in, provider and the campaign quota are checked BEFORE any provider call. Returns (provider, error_response)."""
    if not request.user.profile.ai_enabled:
        return None, JsonResponse({"error": "AI is switched off in Settings, so nothing was sent to a model. "
                                            "Turn it on, or use your template for the remaining emails."}, status=403)
    provider = provider_from_settings()
    if provider is None:
        return None, JsonResponse({"error": "AI is not configured on this server. Use your template for the remaining emails."}, status=503)
    # A separate bucket from the template writer's "ai:<user>" limit, so campaigns never use up the writer's quota.
    uid = request.user.pk
    if not hit(f"ai-campaign:{uid}:hour", settings.AI_CAMPAIGN_PER_HOUR, 3600):
        return None, JsonResponse({"error": f"You've reached the limit of {settings.AI_CAMPAIGN_PER_HOUR} AI emails per hour. "
                                            "Try again later, or use your template for the rest."}, status=429)
    if not hit(f"ai-campaign:{uid}:day", settings.AI_CAMPAIGN_PER_DAY, 86400):
        return None, JsonResponse({"error": f"You've reached the limit of {settings.AI_CAMPAIGN_PER_DAY} AI emails per day. "
                                            "Try again tomorrow, or use your template for the rest."}, status=429)
    return provider, None


def _progress(c: Campaign) -> dict:
    waiting = c.recipients.filter(content_source=Recipient.Source.PENDING)
    return {"pending": waiting.filter(gen_error="").count(), "failed": waiting.exclude(gen_error="").count(),
            "generating": runner.generating_count(c), "total": c.recipients.count()}


def _draft_recipient(request, pk, rid):
    c = _owned(request, pk)
    return c, get_object_or_404(Recipient, pk=rid, campaign=c)


@login_required
def campaign_context(request, pk):
    """Page 2 of an AI campaign: one card per recipient for their own company and job details."""
    c = _owned(request, pk)
    if c.status != Campaign.Status.DRAFT or c.personalisation != Campaign.Personalisation.AI:
        return redirect("campaign_detail", pk=c.pk)
    cards = []
    for r in c.recipients.all():
        guess = validators.guess_name_from_email(r.email)
        cards.append({"r": r, "c": {**validators.empty_context(), **(r.job_context or {})}, "written": r.content_source != Recipient.Source.PENDING,
                      "initial": (r.name or r.email)[:1].upper(), "guessed": bool(r.name) and r.name == guess and not (r.contact and r.contact.name)})
    return render(request, "outreach/campaign_context.html", {
        "c": c, "cards": cards, "limits": validators.LIMITS, "ai_ready": request.user.profile.ai_enabled and provider_from_settings() is not None,
        "concurrency": max(1, settings.AI_CAMPAIGN_CONCURRENCY), **_ai_context(request.user),
    })


@login_required
@require_POST
def campaign_context_save(request, pk):
    """Saves every card (partial saves are fine: cards with problems are reported and left as they were). Does not call AI.
    Only rows of this user's own campaign can be changed, and the Contact records are never touched."""
    c = _owned(request, pk)
    if c.status != Campaign.Status.DRAFT:
        return JsonResponse(FROZEN, status=409)
    if c.personalisation != Campaign.Personalisation.AI:
        return JsonResponse(NOT_AI, status=400)
    data = _json_body(request)
    items = data.get("recipients")
    if not isinstance(items, list):
        return JsonResponse({"error": "Nothing to save."}, status=400)
    shared = data.get("shared_jd") is True
    shared_text = re.sub(r"\r\n?", "\n", str(data.get("shared_job_description") or "")).strip()
    errors: dict = {}
    if len(shared_text) > validators.LIMITS["job_description"]:
        errors["shared"] = {"job_description": f"Too long (max {validators.LIMITS['job_description']:,} characters)."}
        shared_text = c.job_description
    rows = {r.pk: r for r in c.recipients.all()}  # ids that are not recipients of THIS campaign are never touched
    template = c.template
    saved = []
    for item in items:
        try:
            rid = int(item.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        r = rows.get(rid)
        if r is None:
            errors[str(rid)] = {"id": "This recipient is not part of the campaign (it may have been removed)."}
            continue
        cleaned, errs = validators.clean_context(item)
        if shared:
            cleaned["job_description"] = shared_text
        if errs:
            errors[str(rid)] = errs
            continue
        name = cleaned.pop("name")
        ctx = {k: cleaned[k] for k in validators.CONTEXT_KEYS}
        fields = {"name": name, "job_context": ctx}
        if r.content_source == Recipient.Source.PENDING and not r.is_generating and template is not None:
            # still waiting: refresh the template version that is shown (and used if AI is skipped) with the edited details
            rendered = render_email(template.subject, template.body, ctx["job_title"] or template.role_label,
                                    {"name": name, "company": ctx["company"], "role_title": ctx["designation"]}, c.sender_name)
            fields.update(subject=rendered.subject[:400], body=rendered.body_html)
        Recipient.objects.filter(pk=r.pk, campaign__status=Campaign.Status.DRAFT).update(**fields)
        saved.append(rid)
    Campaign.objects.filter(pk=c.pk, status=Campaign.Status.DRAFT).update(shared_jd=shared, job_description=shared_text if shared else c.job_description)
    return JsonResponse({"saved": saved, "errors": errors})


@login_required
@require_POST
def recipient_remove(request, pk, rid):
    """Drops one recipient from a draft campaign. The contact itself is not deleted."""
    c, r = _draft_recipient(request, pk, rid)
    if c.status != Campaign.Status.DRAFT:
        return JsonResponse(FROZEN, status=409)
    if c.recipients.count() <= 1:
        return JsonResponse({"error": "A campaign needs at least one recipient. Discard the draft instead."}, status=400)
    r.delete()
    return JsonResponse({"removed": rid, "remaining": c.recipients.count()})


@login_required
@require_POST
def campaign_personalise(request, pk):
    """Writes the NEXT waiting recipient's email: one graph run per request, so slow local models never hit a request
    timeout. The review page runs `concurrency` of these loops at once; the server enforces the same cap, and a
    recipient is claimed atomically so nobody is generated twice. Never sends anything."""
    c = _owned(request, pk)
    if c.status != Campaign.Status.DRAFT:
        return JsonResponse({**FROZEN, "pending": 0}, status=409)
    if c.personalisation != Campaign.Personalisation.AI:
        return JsonResponse({**NOT_AI, "pending": 0}, status=400)
    if runner.generating_count(c) >= max(1, settings.AI_CAMPAIGN_CONCURRENCY):
        return JsonResponse({"busy": True, **_progress(c)})
    r, started = None, None
    for cand in runner.claimable(c)[:5]:
        started = runner.claim(cand.pk)
        if started:
            r = cand
            break
    if r is None:
        return JsonResponse({"done": True, **_progress(c), "recipients": [_recipient_json(x) for x in c.recipients.all()]})
    provider, err = _ai_ready(request)
    if err:
        runner.release(r.pk, started)
        return err
    try:
        out = runner.generate(c, r, provider, started)
    except (AiQuotaError, AiUnavailableError) as e:
        status = 429 if isinstance(e, AiQuotaError) else 502
        msg = "The AI provider's rate limit was reached. Try again later, or use your template for the rest." if status == 429 else str(e)
        return JsonResponse({"error": msg, **_progress(c)}, status=status)
    return JsonResponse({"recipient": _recipient_json(out.recipient), "outcome": out.kind, **_progress(c)})


@login_required
@require_POST
def recipient_regenerate(request, pk, rid):
    """Writes a new email for one recipient, on an explicit button press (also how a failed recipient is retried).
    If the new draft fails the guard, the current email is kept unchanged. An edited or approved email is only
    replaced when the request says `overwrite: true`."""
    c, r = _draft_recipient(request, pk, rid)
    if c.status != Campaign.Status.DRAFT:
        return JsonResponse(FROZEN, status=409)
    if c.personalisation != Campaign.Personalisation.AI:
        return JsonResponse(NOT_AI, status=400)
    body = _json_body(request)
    if body.get("confirm") is not True:
        return JsonResponse({"error": "Press “Regenerate” to ask the AI."}, status=403)
    if (r.content_source == Recipient.Source.EDITED or r.approved) and body.get("overwrite") is not True:
        return JsonResponse({"error": "This email was edited or approved by you. Regenerating replaces it.", "needs_overwrite": True}, status=409)
    started = runner.claim(r.pk, overwrite=True)
    if started is None:
        return JsonResponse({"error": "This email is already being written."}, status=409)
    provider, err = _ai_ready(request)
    if err:
        runner.release(r.pk, started)
        return err
    try:
        out = runner.generate(c, r, provider, started, overwrite=True)
    except AiQuotaError:
        return JsonResponse({"error": "The AI provider's rate limit was reached. Your email is unchanged."}, status=429)
    except AiUnavailableError as e:
        return JsonResponse({"error": f"{e} Your email is unchanged."}, status=502)
    rec = _recipient_json(out.recipient)
    if out.kind in ("saved", "fallback"):
        return JsonResponse({"recipient": rec})
    if out.kind == "discarded":
        return JsonResponse({"error": out.message, "recipient": rec}, status=409)
    return JsonResponse({"error": f"{out.message} Your email is unchanged.".strip(), "recipient": rec}, status=422)


@login_required
@require_POST
def recipient_edit(request, pk, rid):
    """Saves the user's own edit of one email. Sanitised like a template; frozen once the campaign is confirmed.
    Editing also cancels any AI run in flight for this recipient, so its result can never replace the edit."""
    c, r = _draft_recipient(request, pk, rid)
    if c.status != Campaign.Status.DRAFT:
        return JsonResponse(FROZEN, status=409)
    data = _json_body(request)
    subject = re.sub(r"[\r\n]+", " ", str(data.get("subject", ""))).strip()[:400]
    body = str(data.get("body", ""))
    if len(body) > MAX_BODY_CHARS:
        return JsonResponse({"error": "The email is too long."}, status=400)
    body = sanitize_html(body)
    if not subject:
        return JsonResponse({"error": "Add a subject line."}, status=400)
    if is_blank(body):
        return JsonResponse({"error": "Write the email body."}, status=400)
    if not Recipient.objects.filter(pk=r.pk, campaign__status=Campaign.Status.DRAFT).update(
        subject=subject, body=body, content_source=Recipient.Source.EDITED, content_note="Edited by you.", gen_started_at=None,
        gen_error="", approved=False,
    ):
        return JsonResponse(FROZEN, status=409)
    r.refresh_from_db()
    return JsonResponse({"recipient": _recipient_json(r)})


@login_required
@require_POST
def recipient_approve(request, pk, rid):
    """The user marks one reviewed email as approved. Only an email that is written (not waiting, failed or being written)."""
    c, r = _draft_recipient(request, pk, rid)
    if c.status != Campaign.Status.DRAFT:
        return JsonResponse(FROZEN, status=409)
    if r.content_source == Recipient.Source.PENDING or r.is_generating:
        return JsonResponse({"error": "This email is not written yet."}, status=409)
    if _is_unfilled(r.subject, r.body):
        return JsonResponse({"error": "This email still contains a {{placeholder}}. Edit it first."}, status=400)
    Recipient.objects.filter(pk=r.pk, campaign__status=Campaign.Status.DRAFT).update(approved=True)
    r.refresh_from_db()
    return JsonResponse({"recipient": _recipient_json(r)})
