from django.contrib import admin

from .models import Resume, ResumeVersion


class VersionInline(admin.TabularInline):
    model = ResumeVersion
    fields = ("label", "source", "locked", "created_at")
    readonly_fields = fields
    extra = 0


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "original_name", "updated_at")
    inlines = [VersionInline]
