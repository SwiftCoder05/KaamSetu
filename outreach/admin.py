from django.contrib import admin

from .models import Campaign, Contact, EmailTemplate, MailConnection, Recipient


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "company", "owner", "do_not_contact")
    search_fields = ("email", "name", "company")
    list_filter = ("do_not_contact",)


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "updated_at")


@admin.register(MailConnection)
class MailConnectionAdmin(admin.ModelAdmin):
    list_display = ("email", "owner", "provider", "verified_at")
    exclude = ("secret_enc",)  # the encrypted app password is never shown


class RecipientInline(admin.TabularInline):
    model = Recipient
    fields = ("email", "status", "attempts", "error", "sent_at")
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "status", "confirmed_at", "created_at")
    list_filter = ("status",)
    inlines = [RecipientInline]
