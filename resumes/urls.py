from django.urls import path

from . import views

urlpatterns = [
    path("", views.resume_list, name="resume_list"),
    path("all/", views.resume_all, name="resume_all"),
    path("new/", views.resume_new, name="resume_new"),
    path("<int:pk>/", views.resume_workspace, name="resume_workspace"),
    path("<int:pk>/original/", views.resume_original, name="resume_original"),
    path("<int:pk>/original/page/<int:n>.png", views.resume_original_page, name="resume_original_page"),
    path("<int:pk>/delete/", views.resume_delete, name="resume_delete"),
    path("versions/<int:vid>/save/", views.version_save, name="version_save"),
    path("versions/<int:vid>/export/<str:fmt>/", views.version_export, name="version_export"),
    path("versions/<int:vid>/delete/", views.version_delete, name="version_delete"),
    path("versions/<int:vid>/tailor/", views.version_tailor, name="version_tailor"),
    path("versions/<int:vid>/tailor/check/", views.version_tailor_check, name="version_tailor_check"),
    path("versions/<int:vid>/apply/", views.version_apply, name="version_apply"),
]
