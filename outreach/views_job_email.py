"""Personalised job application email: one company, one role, one selected resume version.

The view is the authority for authentication, ownership, AI opt-in, explicit confirmation and rate limiting.
The graph (personalized_email.service) gets plain data only. Nothing is saved, no campaign or recipient is
created and no email is sent: the user reviews, edits and copies the draft.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from kaamsetu.throttle import hit
from resumes.ai.provider import AiDisabledError, AiOutputError, AiQuotaError, AiUnavailableError, provider_from_settings, provider_is_external, provider_label
from resumes.models import ResumeVersion

from .personalized_email.service import JobEmailInputError, JobEmailRequest, generate_job_email

log = logging.getLogger("kaamsetu.ai")

# The same bucket as resume tailoring and the template writer: this endpoint must not be a way around it.
AI_LIMIT, AI_WINDOW = 12, 3600


def _versions(user):
    return ResumeVersion.objects.filter(resume__owner=user).select_related("resume").order_by("resume__title", "-created_at", "-id")


@login_required
def job_email(request):
    return render(request, "outreach/job_email.html", {
        "versions": _versions(request.user), "ai_enabled": request.user.profile.ai_enabled, "ai_label": provider_label(),
        "ai_configured": provider_from_settings() is not None, "ai_external": provider_is_external(),
    })


def _err(message: str, status: int, errors: dict | None = None) -> JsonResponse:
    return JsonResponse({"error": message, "errors": errors or {}}, status=status)


@login_required
@require_POST
def job_email_generate(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _err("Invalid request.", 400)
    if not isinstance(data, dict):
        return _err("Invalid request.", 400)
    profile = request.user.profile
    if not profile.ai_enabled:
        return _err("AI is switched off. Turn it on in Settings first. Nothing was sent to a model.", 403)
    if data.get("confirm") is not True:  # only the explicit Generate button sends true
        return _err("Press “Generate personalised email” to run the AI.", 403)

    # Ownership: the version id from the browser is only trusted after this owner-scoped lookup.
    vid = str(data.get("resume_version", "")).strip()
    version = _versions(request.user).filter(pk=int(vid)).first() if vid.isdigit() else None
    if version is None:
        return _err("Choose one of your resume versions.", 404, {"resume_version": "Choose one of your resume versions."})

    # Optional contact details: validated for display, never passed to the graph or the model.
    recruiter_email = str(data.get("recruiter_email", "")).strip()[:254]
    job_url = str(data.get("job_url", "")).strip()[:500]
    errors = {}
    if recruiter_email:
        try:
            validate_email(recruiter_email)
        except ValidationError:
            errors["recruiter_email"] = "Enter a valid email address, or leave it empty."
    if job_url:
        try:
            URLValidator(schemes=["http", "https"])(job_url)
        except ValidationError:
            errors["job_url"] = "Enter a full link starting with https://, or leave it empty."
    if errors:
        return _err(next(iter(errors.values())), 400, errors)

    if not hit(f"ai:{request.user.pk}", limit=AI_LIMIT, window_seconds=AI_WINDOW):
        return _err("You've reached the hourly AI limit. Try again later.", 429)

    req = JobEmailRequest(
        user_id=request.user.pk,
        # slightly over each limit, so the graph's input validation can report "too long" instead of silently cutting
        company=str(data.get("company", ""))[:200],
        role=str(data.get("role", ""))[:200],
        job_description=str(data.get("job_description", ""))[:12001],
        recruiter_name=str(data.get("recruiter_name", ""))[:200],
        instructions=str(data.get("instructions", ""))[:1001],
        resume_attached=data.get("resume_attached") is True,
        resume_version_id=version.pk,
        resume_label=f"{version.resume.title} — {version.label}",
        resume_data=version.data,
        sender_name=profile.sender_name,
        ai_enabled=profile.ai_enabled,
        confirmed=True,
    )
    try:
        result = generate_job_email(req, provider_from_settings())
    except JobEmailInputError as e:
        return _err(str(e), 400, e.errors)
    except AiDisabledError as e:
        return _err(str(e), 403)
    except AiQuotaError:
        return _err("The AI provider's quota or rate limit was reached. Try again later.", 429)
    except (AiUnavailableError, AiOutputError) as e:  # messages are user-facing text, never keys or prompts
        return _err(str(e), 502)
    # Metadata only: no resume text, job description, recruiter details or email content in logs.
    log.info("job email user=%s status=%s revisions=%s", request.user.pk, result["validation_status"], result["revision_count"])
    result.pop("trace", None)
    return JsonResponse({**result, "recruiter_email": recruiter_email, "job_url": job_url})
