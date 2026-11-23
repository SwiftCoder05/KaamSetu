from django.urls import path

from . import views_campaigns as camp
from . import views_contacts as con
from . import views_job_email as job
from . import views_templates as tpl

urlpatterns = [
    path("contacts/", con.contact_list, name="contact_list"),
    path("contacts/import/", con.contact_import, name="contact_import"),
    path("contacts/import/confirm/", con.contact_import_confirm, name="contact_import_confirm"),
    path("contacts/bulk/", con.contact_bulk, name="contact_bulk"),
    path("contacts/<int:pk>/edit/", con.contact_edit, name="contact_edit"),
    path("contacts/<int:pk>/delete/", con.contact_delete, name="contact_delete"),
    path("templates/", tpl.template_gallery, name="template_gallery"),
    path("templates/new/", tpl.template_create, name="template_create"),
    path("templates/resume-upload/", tpl.template_resume_upload, name="template_resume_upload"),
    path("templates/<int:pk>/", tpl.template_edit, name="template_edit"),
    path("templates/<int:pk>/preview/", tpl.template_preview, name="template_preview"),
    path("templates/<int:pk>/ai/", tpl.template_ai, name="template_ai"),
    path("templates/<int:pk>/duplicate/", tpl.template_duplicate, name="template_duplicate"),
    path("templates/<int:pk>/delete/", tpl.template_delete, name="template_delete"),
    path("apply/", job.job_email, name="job_email"),
    path("apply/generate/", job.job_email_generate, name="job_email_generate"),
    path("campaigns/", camp.campaign_list, name="campaign_list"),
    path("campaigns/new/", camp.campaign_new, name="campaign_new"),
    path("campaigns/<int:pk>/", camp.campaign_detail, name="campaign_detail"),
    path("campaigns/<int:pk>/confirm/", camp.campaign_confirm, name="campaign_confirm"),
    path("campaigns/<int:pk>/send/", camp.campaign_send, name="campaign_send"),
    path("campaigns/<int:pk>/context/", camp.campaign_context, name="campaign_context"),
    path("campaigns/<int:pk>/context/save/", camp.campaign_context_save, name="campaign_context_save"),
    path("campaigns/<int:pk>/personalise/", camp.campaign_personalise, name="campaign_personalise"),
    path("campaigns/<int:pk>/recipients/<int:rid>/remove/", camp.recipient_remove, name="recipient_remove"),
    path("campaigns/<int:pk>/recipients/<int:rid>/approve/", camp.recipient_approve, name="recipient_approve"),
    path("campaigns/<int:pk>/recipients/<int:rid>/edit/", camp.recipient_edit, name="recipient_edit"),
    path("campaigns/<int:pk>/recipients/<int:rid>/regenerate/", camp.recipient_regenerate, name="recipient_regenerate"),
    path("campaigns/<int:pk>/<str:action>/", camp.campaign_action, name="campaign_action"),
]
