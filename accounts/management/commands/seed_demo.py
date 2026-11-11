"""Creates (or refreshes) a demo account with a few contacts, templates and a resume. Safe to run twice.

    python manage.py seed_demo --email test@kaamsetu.dev --password "..."
"""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from outreach.models import Contact, EmailTemplate
from outreach.render import STARTER_TEMPLATES
from resumes.importer import parse_resume_text
from resumes.models import Resume, ResumeVersion

RESUME_TEXT = """Asha Rao
Backend Engineer
asha.rao@example.com | +91 98765 43210 | github.com/asharao

Summary
Backend engineer who builds reliable Python and Go services and cares about clean, testable code.

Skills
Languages: Python, Go, SQL, TypeScript
Frameworks: Django, FastAPI, React
Tools: PostgreSQL, Redis, Docker, AWS

Experience
Software Engineer | Acme Labs | Bengaluru
Jan 2022 - Present
- Built payment APIs serving 2 million requests a day
- Cut p95 latency by 40% by adding Redis caching
- Mentored 3 interns on testing and code review

Software Engineering Intern | Beta Systems | Remote
May 2021 - Dec 2021
- Wrote data pipelines in Python that replaced manual spreadsheet reports

Projects
KaamSetu | Django, Alpine.js
- Job outreach tool that sends personalised emails from Gmail with safe rate limits

Education
IIT Madras | B.Tech Computer Science | 2017 - 2021
"""


class Command(BaseCommand):
    help = "Create a demo user with sample data."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="test@kaamsetu.dev")
        parser.add_argument("--password", required=True)

    def handle(self, *args, **o):
        User = get_user_model()
        email = o["email"].lower()
        user, created = User.objects.get_or_create(username=email, defaults={"email": email})
        user.set_password(o["password"])
        user.save()
        user.profile.full_name = user.profile.full_name or "Asha Rao"
        user.profile.save()
        # The demo account skips the emailed verification link so it can sign in immediately.
        EmailAddress.objects.update_or_create(user=user, email=email, defaults={"verified": True, "primary": True})

        for email_, name, company, role in [
            ("priya.sharma@acmelabs.example", "Priya Sharma", "Acme Labs", "Talent Partner"),
            ("rahul@betasystems.example", "Rahul Verma", "Beta Systems", "Engineering Manager"),
            ("careers@gammatech.example", "", "Gamma Tech", ""),
        ]:
            Contact.objects.get_or_create(owner=user, email=email_, defaults={"name": name, "company": company, "role_title": role})

        if not EmailTemplate.objects.filter(owner=user).exists():
            for s in STARTER_TEMPLATES[:2]:
                EmailTemplate.objects.create(owner=user, name=s["name"], role_label=s["role_label"], subject=s["subject"], body=s["body"])

        if not Resume.objects.filter(owner=user).exists():
            data, notes = parse_resume_text(RESUME_TEXT)
            r = Resume.objects.create(owner=user, title="Backend resume", import_notes=notes)
            ResumeVersion.objects.create(resume=r, label="Version 1", data=data.model_dump(), source=ResumeVersion.Source.MANUAL)

        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} demo user {email}"))
