from django.contrib import admin

from .models import Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "full_name", "daily_limit", "ai_enabled")
    search_fields = ("user__username", "full_name")
