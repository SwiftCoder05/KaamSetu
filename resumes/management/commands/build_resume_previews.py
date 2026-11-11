"""Creates the "looks exactly like the original" preview for resumes imported before this feature."""

from django.core.management.base import BaseCommand

from resumes.models import Resume


class Command(BaseCommand):
    help = "Build original-file previews (Word -> PDF) for imported resumes that do not have one yet."

    def handle(self, *args, **opts):
        done = 0
        for r in Resume.objects.exclude(original_file=""):
            kind = r.original_kind()
            if kind == "pdf" or r.preview_pdf or r.preview_html or kind not in ("docx", "doc"):
                continue
            with r.original_file.open("rb") as fh:
                r.build_preview(kind, fh.read())
            done += 1
            self.stdout.write(f"  {r.title}: {'PDF preview' if r.preview_pdf else 'formatted text preview' if r.preview_html else 'no converter available'}")
        self.stdout.write(self.style.SUCCESS(f"Previews built: {done}"))
