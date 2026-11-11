from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/mail/connect/", views.mail_connect, name="mail_connect"),
    path("settings/mail/disconnect/", views.mail_disconnect, name="mail_disconnect"),
    path("settings/ai/", views.ai_toggle, name="ai_toggle"),
    path("ugc/", views.ugc, name="ugc"),
]
