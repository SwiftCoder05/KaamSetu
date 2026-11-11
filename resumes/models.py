import os
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import models

from .schema import ResumeData, parse_resume

# No base_url: these files have no public URL and are only served by the owner-checked download view.
private_storage = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)


def original_upload_to(instance, filename):
    ext = os.path.splitext(filename)[1].lower()[:8]
    return f"originals/{instance.owner_id}/{uuid.uuid4().hex}{ext}"


class Resume(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="resumes")
    title = models.CharField(max_length=160)
    # The uploaded file is kept byte-for-byte, so the user can always get their original back.
    original_file = models.FileField(upload_to=original_upload_to, storage=private_storage, blank=True)
    original_name = models.CharField(max_length=255, blank=True)
    original_size = models.PositiveIntegerField(default=0)
    import_notes = models.JSONField(default=list, blank=True)
    # How the original looks: a PDF made from a Word upload (PDF uploads are shown directly), or, when no
    # converter is available, HTML that keeps bold/italics/headings/lists.
    preview_pdf = models.FileField(upload_to=original_upload_to, storage=private_storage, blank=True)
    preview_html = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["owner", "-updated_at"], name="resume_owner_updated_idx")]

    def __str__(self):
        return self.title

    def original_kind(self) -> str:
        return os.path.splitext(self.original_name)[1].lower().lstrip(".")

    def preview_pdf_bytes(self) -> bytes | None:
        """The PDF that shows the original's real look: the upload itself, or its converted copy."""
        for f in (self.preview_pdf, self.original_file if self.original_kind() == "pdf" else None):
            if f:
                with f.open("rb") as fh:
                    data = fh.read()
                if data.startswith(b"%PDF"):
                    return data
        return None

    def build_preview(self, kind: str, raw: bytes, save: bool = True) -> None:
        """Called at import (and by `manage.py build_resume_previews` for older resumes)."""
        from .preview import convert_to_pdf, docx_to_html

        if kind in ("docx", "doc"):
            pdf = convert_to_pdf(kind, raw)
            if pdf:
                self.preview_pdf.save("preview.pdf", ContentFile(pdf), save=False)
            elif kind == "docx":
                self.preview_html = docx_to_html(raw)
        if save:
            self.save(update_fields=["preview_pdf", "preview_html"])

    def latest_version(self):
        return self.versions.order_by("-created_at", "-id").first()


class ResumeVersion(models.Model):
    class Source(models.TextChoices):
        IMPORT = "import", "Imported"
        BLANK = "blank", "Blank"
        MANUAL = "manual", "Edited"
        AI = "ai", "AI-tailored"
        COPY = "copy", "Copy"

    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name="versions")
    label = models.CharField(max_length=160)
    data = models.JSONField(default=dict)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    # The first imported version is locked as the source of truth; edits always create a new version.
    locked = models.BooleanField(default=False)
    job_title = models.CharField(max_length=160, blank=True)
    company = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["resume", "-created_at"], name="rversion_resume_created_idx")]

    def __str__(self):
        return f"{self.resume.title} — {self.label}"

    @property
    def owner_id(self):
        return self.resume.owner_id

    def parsed(self) -> ResumeData:
        return parse_resume(self.data)
