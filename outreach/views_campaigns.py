import os
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from resumes.export import safe_filename, to_pdf
from resumes.models import ResumeVersion

from . import engine
from .connection import active_connection, mailer_factory_for
from .files import FileExtractError, detect_file_kind
from .models import Campaign, CampaignAttachment, Contact, EmailTemplate, Recipient
from .render import has_unresolved_placeholders, render_email
from .richtext import html_to_text

MAX_RECIPIENTS = 500


def _owned(request, pk) -> Campaign:
    return get_object_or_404(Campaign, pk=pk, owner=request.user)


def _resume_versions(user):
    return ResumeVersion.objects.filter(resume__owner=user).select_related("resume").order_by("resume__title", "-created_at")


@login_required
def campaign_list(request):
    campaigns = list(Campaign.objects.filter(owner=request.user))
    for c in campaigns:
        c.c = c.counts()
    return render(request, "outreach/campaigns.html", {"campaigns": campaigns})


@login_required
def campaign_new(request):
    user = request.user
    templates = EmailTemplate.objects.filter(owner=user)
    contacts = Contact.objects.filter(owner=user, do_not_contact=False)
    versions = _resume_versions(user)
    default_source = "studio" if versions.exists() else "upload"
    ctx = {"templates": templates, "contacts": contacts, "versions": versions, "errors": {}, "form": request.POST,
           "picked_ids": request.POST.getlist("contacts"), "max_mb": settings.MAX_UPLOAD_BYTES // (1024 * 1024),
           "resume_source": request.POST.get("resume_source")
           or (("studio" if request.POST.get("resume_version") else "none") if request.method == "POST" else default_source)}
    if request.method == "GET":
        ctx["form"] = {"template": request.GET.get("template", ""), "name": ""}
        return render(request, "outreach/campaign_new.html", ctx)

    errors = ctx["errors"]
    name = request.POST.get("name", "").strip()[:160]
    template = templates.filter(pk=request.POST.get("template") or 0).first()
    picked = list(contacts.filter(pk__in=request.POST.getlist("contacts")))
    if not template:
        errors["template"] = "Choose a template."
    if not picked:
        errors["contacts"] = "Choose at least one contact."
    elif len(picked) > MAX_RECIPIENTS:
        errors["contacts"] = f"A campaign can have at most {MAX_RECIPIENTS} recipients."

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
            resume_version=version, sender_name=sender,
        )
        rows, seen = [], set()
        for ct in picked:
            if ct.email in seen:
                continue
            seen.add(ct.email)
            r = render_email(template.subject, template.body, template.role_label,
                             {"name": ct.name, "company": ct.company, "role_title": ct.role_title}, sender)
            rows.append(Recipient(campaign=c, contact=ct, email=ct.email, name=ct.name, subject=r.subject[:400], body=r.body_html))
        Recipient.objects.bulk_create(rows)
        if upload:
            filename, content_type, data = upload
            CampaignAttachment.objects.create(campaign=c, filename=filename, content_type=content_type, data=data, size=len(data))
    return redirect("campaign_detail", pk=c.pk)


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
        r.unfilled = has_unresolved_placeholders(r.subject) or has_unresolved_placeholders(html_to_text(r.body))
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
        messages.error(request, "Connect Gmail in Settings before sending.")
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
        if pdf is not None:
            CampaignAttachment.objects.update_or_create(
                campaign=c,
                defaults={"filename": safe_filename(v.parsed().basics.name or v.resume.title, "pdf"), "data": pdf, "size": len(pdf)},
            )
        c.confirmed_at = timezone.now()
        c.status = Campaign.Status.CONFIRMED
        c.save()
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
    conn = active_connection(request.user)
    if not conn:
        return JsonResponse(_state(c, {"error": "Connect Gmail in Settings before sending."}), status=400)
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
    elif action == "delete" and c.status in (Campaign.Status.DRAFT, Campaign.Status.COMPLETED, Campaign.Status.CANCELLED):
        c.delete()
        messages.success(request, "Campaign deleted.")
        return redirect("campaign_list")
    return redirect("campaign_detail", pk=c.pk)
