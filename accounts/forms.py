from django import forms
from django.contrib.auth import get_user_model

from outreach.mailer import normalize_app_password

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


class GmailConnectForm(forms.Form):
    gmail_address = forms.EmailField(label="Gmail address")
    app_password = forms.CharField(
        label="App password",
        widget=forms.PasswordInput(attrs={"autocomplete": "off", "placeholder": "abcd efgh ijkl mnop"}),
        help_text="16 letters from myaccount.google.com/apppasswords (needs 2-Step Verification).",
    )

    def clean_gmail_address(self):
        return self.cleaned_data["gmail_address"].strip().lower()

    def clean_app_password(self):
        p = normalize_app_password(self.cleaned_data["app_password"])
        if not p:
            raise forms.ValidationError(
                "That doesn't look like an app password. It is 16 letters, shown as four groups of four. "
                "Your normal Gmail password will not work."
            )
        return p
