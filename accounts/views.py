from allauth.account.models import EmailAddress
from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from kaamsetu.throttle import hit
from outreach import connection as mail_connection
from outreach.connection import active_connection, disconnect, save_smtp_connection
from outreach.engine import accepted_last_24h
from outreach.mailer import MailError
from outreach.models import Campaign, Contact, EmailTemplate
from outreach.smtp_presets import PRESETS
from resumes.ai.provider import provider_label
from resumes.models import Resume

from .forms import MailConnectForm, ProfileForm


WALKTHROUGH_STEPS = [
    {"img": "img/walkthrough/1-dashboard.png", "title": "Everything in one place",
     "text": "A dashboard that tracks your setup and shows exactly what's left before you can start sending."},
    {"img": "img/walkthrough/2-contacts.png", "title": "Bring your recruiter list",
     "text": "Paste emails, or import a CSV, Excel, Word, PDF or screenshot. You review every address before it's saved."},
    {"img": "img/walkthrough/3-templates.png", "title": "Start from a role-specific template",
     "text": "Pick a starter for your role, or write a blank one. Placeholders fill in for each recipient automatically."},
    {"img": "img/walkthrough/4-template-editor.png", "title": "Write it yourself, or let AI draft it",
     "text": "A document-style editor with live preview — or extract facts from your resume and have AI draft a tailored email."},
    {"img": "img/walkthrough/5-campaigns.png", "title": "Send from your own email",
     "text": "Nothing goes out until you confirm. Track accepted, failed and queued sends with an honest status for each one."},
    {"img": "img/walkthrough/7-resume-workspace.png", "title": "Tailor your resume per job",
     "text": "Edit your resume, then tailor it to a specific job description. Invented numbers or skills are always blocked."},
]


def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    user_count = cache.get("landing_user_count")
    if user_count is None:
        user_count = get_user_model().objects.filter(is_active=True).count()
        cache.set("landing_user_count", user_count, 300)
    return render(request, "landing.html", {"walkthrough": WALKTHROUGH_STEPS, "user_count": user_count})


@login_required
def dashboard(request):
    user = request.user
    conn = active_connection(user)
    counts = {
        "contacts": Contact.objects.filter(owner=user).count(),
        "templates": EmailTemplate.objects.filter(owner=user).count(),
        "resumes": Resume.objects.filter(owner=user).count(),
        "campaigns": Campaign.objects.filter(owner=user).count(),
    }
    steps = [
        ("Connect your email", "Send from your own address (Gmail, Outlook, Yahoo or any SMTP).", bool(conn), reverse("settings")),
        ("Add recruiter contacts", "Paste emails or import a CSV, Excel, Word, PDF or screenshot.", counts["contacts"] > 0, reverse("contact_import")),
        ("Pick an email template", "Start from a role-specific template and make it yours.", counts["templates"] > 0, reverse("template_gallery")),
        ("Add your resume", "Import your current resume or start from a blank one.", counts["resumes"] > 0, reverse("resume_list")),
    ]
    now = timezone.localtime()
    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"
    recent = list(Campaign.objects.filter(owner=user)[:5])
    for c in recent:
        c.c = c.counts()
    return render(
        request,
        "dashboard.html",
        {
            "conn": conn,
            "counts": counts,
            "steps": steps,
            "steps_done": sum(1 for s in steps if s[2]),
            "sent_24h": accepted_last_24h(user),
            "daily_limit": user.profile.daily_limit,
            "recent": recent,
            "greeting": greeting,
            "first_name": user.profile.sender_name.split()[0] if user.profile.sender_name else "",
            "today": now,
        },
    )


@login_required
def settings_view(request):
    profile = request.user.profile
    profile_form = ProfileForm(request.POST or None, instance=profile, prefix="p")
    if request.method == "POST" and profile_form.is_valid():
        profile_form.save()
        messages.success(request, "Settings saved.")
        return redirect("settings")
    return _render_settings(request, profile_form, MailConnectForm(prefix="m"))


def _render_settings(request, profile_form, mail_form):
    """One place builds the Settings page, so it looks the same after a failed email connect."""
    return render(
        request,
        "accounts/settings.html",
        {
            "profile_form": profile_form,
            "mail_form": mail_form,
            "presets": PRESETS,
            "conn": active_connection(request.user),
            "ai_label": provider_label(),
            "mfa_on": is_mfa_enabled(request.user),
            "google_on": "google" in settings.SOCIALACCOUNT_PROVIDERS,
            "google_linked": request.user.socialaccount_set.filter(provider="google").exists(),
            "email_verified": EmailAddress.objects.filter(user=request.user, email__iexact=request.user.email, verified=True).exists(),
        },
    )


def _form_errors(form) -> str:
    errors = form.non_field_errors() or next((e for e in form.errors.values() if e), None)
    return str(errors[0]) if errors else "Check the details and try again."


def _run_mail_form(request, save: bool):
    """Shared by Test Connection and Connect: validates the form, then signs in to the SMTP server.

    Returns (form, message, ok). Plaintext passwords exist only for the length of this call."""
    form = MailConnectForm(request.POST, prefix="m")
    # Shared budget for tests and saves: each one makes the email service sign in to a real mail server.
    if not hit(f"mailconnect:{request.user.pk}", limit=12, window_seconds=600):
        return form, "Too many attempts. Wait ten minutes before trying again.", False
    if not form.is_valid():
        return form, _form_errors(form), False
    cfg, password = form.cleaned_data["smtp"], form.cleaned_data["password"]
    try:
        if save:
            save_smtp_connection(request.user, cfg, password)
        else:
            mail_connection.verify_smtp(cfg, password)
    except MailError as e:
        return form, e.message, False
    return form, "", True


@login_required
@require_POST
def mail_connect(request):
    form, error, ok = _run_mail_form(request, save=True)
    if ok:
        messages.success(request, f"Email connected. Emails will be sent from {form.cleaned_data['smtp'].email}.")
        return redirect("settings")
    messages.error(request, f"Email not connected: {error}")
    form.add_error(None, error) if not form.errors else None
    return _render_settings(request, ProfileForm(instance=request.user.profile, prefix="p"), form)


@login_required
@require_POST
def mail_test(request):
    """Test Connection: signs in without saving anything and without sending an email."""
    form, error, ok = _run_mail_form(request, save=False)
    if ok:
        return JsonResponse({"ok": True, "message": f"Connected to {form.cleaned_data['smtp'].host}. The login works and nothing was sent."})
    return JsonResponse({"ok": False, "message": error}, status=400)


@login_required
@require_POST
def mail_disconnect(request):
    disconnect(request.user)
    messages.success(request, "Email disconnected and the stored password was deleted.")
    return redirect("settings")


@login_required
@require_POST
def ai_toggle(request):
    profile = request.user.profile
    profile.ai_enabled = request.POST.get("enabled") == "1"
    profile.save(update_fields=["ai_enabled"])
    messages.success(request, "AI suggestions are on." if profile.ai_enabled else "AI is off. Nothing will be sent to a model.")
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(nxt)
    return redirect("settings")


@login_required
def ugc(request):
    return render(request, "ugc.html")
