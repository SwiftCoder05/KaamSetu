from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("live-users/", views.live_users, name="live_users"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/mail/connect/", views.mail_connect, name="mail_connect"),
    path("settings/mail/test/", views.mail_test, name="mail_test"),
    path("settings/mail/disconnect/", views.mail_disconnect, name="mail_disconnect"),
    path("settings/ai/", views.ai_toggle, name="ai_toggle"),
    path("ugc/", views.ugc, name="ugc"),
]
