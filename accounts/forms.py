import re

from django import forms
from django.contrib.auth import get_user_model

from outreach.mailer import normalize_app_password
from outreach.smtp_presets import PRESETS, build_settings

from .models import Profile

User = get_user_model()


class SignupExtrasForm(forms.Form):
    """Extra signup field on top of allauth's email + password form (see ACCOUNT_SIGNUP_FORM_CLASS)."""

    full_name = forms.CharField(label="Your name", max_length=120, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    field_order = ["full_name", "email", "password1", "password2"]

    def signup(self, request, user):
        profile = user.profile
        profile.full_name = self.cleaned_data["full_name"].strip()
        profile.save(update_fields=["full_name"])


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["full_name", "daily_limit"]
        labels = {"full_name": "Name used to sign your emails", "daily_limit": "Daily sending limit"}


class MailConnectForm(forms.Form):
    """Connect Email: provider preset + SMTP details. Never asks for a normal account password."""

    provider = forms.ChoiceField(label="Email provider", choices=[(k, v["label"]) for k, v in PRESETS.items()], initial="gmail")
    email = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autocomplete": "off"}))
    smtp_host = forms.CharField(label="SMTP host", required=False, max_length=253, widget=forms.TextInput(attrs={"placeholder": "smtp.example.com"}))
    security = forms.ChoiceField(label="Security", required=False, choices=[("ssl", "SSL/TLS (port 465)"), ("starttls", "STARTTLS (port 587)")])
    smtp_port = forms.IntegerField(label="Port", required=False, min_value=1, max_value=65535)
    smtp_username = forms.CharField(label="SMTP username", required=False, max_length=254, help_text="Leave blank to use your email address.")
    password = forms.CharField(
        label="App password",
        max_length=256,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="An app password or provider-issued SMTP password. It is stored encrypted and never shown again.",
    )

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        provider = data["provider"]
        try:
            data["smtp"] = build_settings(
                provider, data["email"], data.get("smtp_host", ""), data.get("smtp_port"), data.get("security", ""), data.get("smtp_username", "")
            )
        except ValueError as e:
            raise forms.ValidationError(str(e)) from None
        password = data["password"]
        if provider == "gmail":
            password = normalize_app_password(password)
            if not password:
                self.add_error(
                    "password",
                    "That doesn't look like a Google App Password. It is 16 letters, shown as four groups of four. "
                    "Your normal Google password will not work.",
                )
        elif re.search(r"[\x00-\x1f\x7f]", password):
            self.add_error("password", "The password contains characters that cannot be used.")
        data["password"] = password
        return data
