from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),  # login, signup, email verification, password reset, 2FA, Google
    path("login/", RedirectView.as_view(pattern_name="account_login", query_string=True)),
    path("signup/", RedirectView.as_view(pattern_name="account_signup", query_string=True)),
    path("", include("accounts.urls")),
    path("jobs/", include("outreach.urls")),
    path("resumes/", include("resumes.urls")),
]
