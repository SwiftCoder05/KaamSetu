import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from kaamsetu.throttle import hit
from resumes.ai.provider import AiDisabledError, AiOutputError, AiQuotaError, AiUnavailableError, provider_from_settings, provider_label
from resumes.models import ResumeVersion
from resumes.schema import resume_plain_text

from .ai_writer import WriterInputError, run_email_writer
from .models import EmailTemplate
from .render import KNOWN_PLACEHOLDERS, SAMPLE_CONTACT, STARTER_TEMPLATES, fill_sender_name, find_placeholders, render_email
from .richtext import MAX_BODY_CHARS, is_blank, sanitize_html


@login_required
def template_gallery(request):
    return render(
        request,
        "outreach/template_gallery.html",
        {
            "mine": EmailTemplate.objects.filter(owner=request.user),
            # previews show the user's own name, exactly as the copy they create will
            "starters": [
                (i, {**s, "subject": fill_sender_name(s["subject"], me), "body": fill_sender_name(s["body"], me, as_html=True)})
                for i, s in enumerate(STARTER_TEMPLATES)
                for me in [request.user.profile.sender_name]
            ],
        },
    )


@login_required
@require_POST
def template_create(request):
    """Creates a template from a starter (by index) or blank, then opens the editor."""
    starter = request.POST.get("starter", "")
    me = request.user.profile.sender_name  # the user's own name is written into the template
    if starter.isdigit() and int(starter) < len(STARTER_TEMPLATES):
        s = STARTER_TEMPLATES[int(starter)]
        t = EmailTemplate.objects.create(
            owner=request.user, name=s["name"], role_label=s["role_label"],
            subject=fill_sender_name(s["subject"], me), body=fill_sender_name(s["body"], me, as_html=True),
        )
    else:
        t = EmailTemplate.objects.create(
            owner=request.user, name="Untitled template", subject=fill_sender_name("Hello from {{sender_name}}", me),
            body=fill_sender_name("<p>Hi {{first_name|there}},</p><p></p><p>Best,<br>{{sender_name}}</p>", me, as_html=True),
        )
    return redirect("template_edit", pk=t.pk)


@login_required
def template_edit(request, pk):
    t = get_object_or_404(EmailTemplate, pk=pk, owner=request.user)
    errors: dict[str, str] = {}
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:120]
        role_label = request.POST.get("role_label", "").strip()[:120]
        subject = request.POST.get("subject", "").replace("\r", " ").replace("\n", " ").strip()[:300]
        body = request.POST.get("body", "")
        if len(body) > MAX_BODY_CHARS:
            errors["body"] = "The email is too long."
        body = sanitize_html(body)
        if not name:
            errors["name"] = "Give the template a name."
        if not subject:
            errors["subject"] = "Add a subject line."
        if is_blank(body):
            errors["body"] = "Write the email body."
        if not errors:
            t.name, t.role_label, t.subject, t.body = name, role_label, subject, body
            t.save()
            unknown = [p for p in find_placeholders(subject + body) if p not in KNOWN_PLACEHOLDERS]
            if unknown:
                messages.warning(request, f"Saved, but these placeholders are not recognised and will be left as-is: {', '.join(unknown)}.")
            else:
                messages.success(request, "Template saved.")
            return redirect("template_edit", pk=t.pk)
        t.name, t.role_label, t.subject, t.body = name, role_label, subject, body
    versions = ResumeVersion.objects.filter(resume__owner=request.user).select_related("resume")[:30]
    return render(
        request,
        "outreach/template_editor.html",
        {
            "t": t, "errors": errors, "placeholders": KNOWN_PLACEHOLDERS, "sender_name": request.user.profile.sender_name,
            "ai_enabled": request.user.profile.ai_enabled, "ai_label": provider_label(), "resume_versions": versions,
        },
    )


@login_required
@require_POST
def template_ai(request, pk):
    """Asks the AI for an email draft. Returns a suggestion only; nothing is saved until the user saves the template."""
    get_object_or_404(EmailTemplate, pk=pk, owner=request.user)
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Invalid request."}, status=400)
    sender_facts, resume_attached = "", False
    vid = str(data.get("resume_version", "")).strip()
    if vid:
        if not vid.isdigit():
            return JsonResponse({"error": "Unknown resume version."}, status=400)
        v = get_object_or_404(ResumeVersion, pk=int(vid), resume__owner=request.user)
    else:  # no picker in the editor: the user's most recent resume supplies the facts, if they have one
        v = ResumeVersion.objects.filter(resume__owner=request.user).order_by("-created_at", "-id").first()
    if v:
        sender_facts, resume_attached = resume_plain_text(v.parsed())[:8000], True
    profile = request.user.profile
    if profile.ai_enabled and data.get("confirm") is True and not hit(f"ai:{request.user.pk}", limit=12, window_seconds=3600):
        return JsonResponse({"error": "You've reached the hourly AI limit. Try again later."}, status=429)
    try:
        result = run_email_writer(
            ai_enabled=profile.ai_enabled,
            confirmed=data.get("confirm") is True,  # only the explicit button sends true
            mode=str(data.get("mode", "")),
            provider=provider_from_settings(),
            job_description=str(data.get("job_description", ""))[:12000],
            instructions=str(data.get("instructions", ""))[:1000],
            role_label=str(data.get("role_label", ""))[:120],
            current_subject=str(data.get("subject", ""))[:300],
            current_body_html=sanitize_html(str(data.get("body", ""))[:MAX_BODY_CHARS]),
            sender_name=profile.sender_name,
            sender_facts=sender_facts,
            resume_attached=resume_attached,
        )
    except AiDisabledError as e:
        return JsonResponse({"error": str(e)}, status=403)
    except WriterInputError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except AiQuotaError as e:
        return JsonResponse({"error": str(e)}, status=429)
    except (AiUnavailableError, AiOutputError) as e:
        return JsonResponse({"error": str(e)}, status=502)
    # AI drafts also get the user's real name instead of the {{sender_name}} placeholder.
    result["subject"] = fill_sender_name(result["subject"], profile.sender_name)
    result["body_html"] = fill_sender_name(result["body_html"], profile.sender_name, as_html=True)
    return JsonResponse(result)


@login_required
@require_POST
def template_preview(request, pk):
    get_object_or_404(EmailTemplate, pk=pk, owner=request.user)
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON")
    body = str(data.get("body", ""))[:MAX_BODY_CHARS]
    r = render_email(
        str(data.get("subject", ""))[:300], sanitize_html(body), str(data.get("role_label", ""))[:120],
        SAMPLE_CONTACT, request.user.profile.sender_name,
    )
    return JsonResponse({"subject": r.subject, "html": r.body_html, "missing": r.missing, "unknown": r.unknown})


@login_required
@require_POST
def template_duplicate(request, pk):
    t = get_object_or_404(EmailTemplate, pk=pk, owner=request.user)
    copy = EmailTemplate.objects.create(owner=request.user, name=f"{t.name} (copy)"[:120], role_label=t.role_label, subject=t.subject, body=t.body)
    messages.success(request, "Template duplicated.")
    return redirect("template_edit", pk=copy.pk)


@login_required
@require_POST
def template_delete(request, pk):
    get_object_or_404(EmailTemplate, pk=pk, owner=request.user).delete()
    messages.success(request, "Template deleted. Campaigns already created keep their own copy of the text.")
    return redirect("template_gallery")
