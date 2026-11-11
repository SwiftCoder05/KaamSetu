from allauth.account.models import EmailAddress
from allauth.mfa.utils import is_mfa_enabled
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from kaamsetu.throttle import hit
from outreach.connection import active_connection, disconnect, save_smtp_connection
from outreach.engine import accepted_last_24h
from outreach.mailer import MailError
from outreach.models import Campaign, Contact, EmailTemplate
from resumes.ai.provider import provider_label
from resumes.models import Resume

from .forms import GmailConnectForm, ProfileForm


def landing(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "landing.html")


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
        ("Connect Gmail", "Send from your own address with an app password.", bool(conn), reverse("settings")),
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
    return _render_settings(request, profile_form, GmailConnectForm(prefix="g"))


def _render_settings(request, profile_form, gmail_form):
    """One place builds the Settings page, so it looks the same after a failed Gmail connect."""
    return render(
        request,
        "accounts/settings.html",
        {
            "profile_form": profile_form,
            "gmail_form": gmail_form,
            "conn": active_connection(request.user),
            "ai_label": provider_label(),
            "mfa_on": is_mfa_enabled(request.user),
            "google_on": "google" in settings.SOCIALACCOUNT_PROVIDERS,
            "google_linked": request.user.socialaccount_set.filter(provider="google").exists(),
            "email_verified": EmailAddress.objects.filter(user=request.user, email__iexact=request.user.email, verified=True).exists(),
        },
    )


@login_required
@require_POST
def mail_connect(request):
    form = GmailConnectForm(request.POST, prefix="g")
    if not hit(f"mailconnect:{request.user.pk}", limit=8, window_seconds=600):
        form.is_valid()
        form.add_error(None, "Too many attempts. Wait ten minutes before trying again.")
    elif form.is_valid():
        try:
            save_smtp_connection(request.user, form.cleaned_data["gmail_address"], form.cleaned_data["app_password"])
        except MailError as e:
            form.add_error(None, e.message)
        else:
            messages.success(request, f"Gmail connected. Emails will be sent from {form.cleaned_data['gmail_address']}.")
            return redirect("settings")
    errors = form.non_field_errors() or form.errors.get("app_password") or form.errors.get("gmail_address")
    if errors:
        messages.error(request, f"Gmail not connected: {errors[0]}")
    return _render_settings(request, ProfileForm(instance=request.user.profile, prefix="p"), form)


@login_required
@require_POST
def mail_disconnect(request):
    disconnect(request.user)
    messages.success(request, "Gmail disconnected and the stored app password was deleted.")
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
