"""{{placeholder}} rendering for email templates."""

import html
import re
from dataclasses import dataclass, field

from .richtext import sanitize_html, text_to_html

KNOWN_PLACEHOLDERS = ("name", "first_name", "role", "company", "sender_name")

# {{name}} or {{name|Hiring Team}} (the fallback is used when the value is empty)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|([^{}]*))?\}\}")


@dataclass
class RenderResult:
    output: str
    missing: list[str] = field(default_factory=list)  # known placeholders with no value and no fallback (left as {{x}})
    unknown: list[str] = field(default_factory=list)  # placeholders the app cannot fill (left untouched)


def fill_sender_name(text: str, name: str, *, as_html: bool = False) -> str:
    """Writes the user's real name in place of {{sender_name}} (other placeholders are left alone)."""
    if not name:
        return text
    value = html.escape(name, quote=False) if as_html else name

    def sub(m):
        return value if m.group(1) == "sender_name" else m.group(0)

    return PLACEHOLDER_RE.sub(sub, text)


def find_placeholders(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in PLACEHOLDER_RE.finditer(text)))


def render_template(text: str, values: dict, *, as_html: bool = False) -> RenderResult:
    """With as_html, substituted VALUES are HTML-escaped (a name may contain < or &).
    Fallbacks are template-authored and already HTML."""
    missing: dict[str, None] = {}
    unknown: dict[str, None] = {}

    def sub(m: re.Match) -> str:
        name, fallback = m.group(1), m.group(2)
        if name not in KNOWN_PLACEHOLDERS:
            unknown[name] = None
            return m.group(0)
        value = str(values.get(name) or "").strip()
        if value:
            return html.escape(value) if as_html else value
        if fallback and fallback.strip():
            return fallback.strip()
        missing[name] = None
        return m.group(0)

    return RenderResult(PLACEHOLDER_RE.sub(sub, text), list(missing), list(unknown))


def has_unresolved_placeholders(text: str) -> bool:
    return bool(PLACEHOLDER_RE.search(text))


def first_name(full: str | None) -> str:
    parts = (full or "").strip().split()
    return parts[0] if parts else ""


@dataclass
class RenderedEmail:
    subject: str
    body_html: str
    missing: list[str]
    unknown: list[str]


def render_email(subject: str, body_html: str, role_label: str, contact: dict, sender_name: str) -> RenderedEmail:
    values = {
        "name": contact.get("name"),
        "first_name": first_name(contact.get("name")),
        "company": contact.get("company"),
        "role": role_label or contact.get("role_title"),
        "sender_name": sender_name,
    }
    s = render_template(subject, values)
    b = render_template(body_html, values, as_html=True)
    return RenderedEmail(
        subject=re.sub(r"[\r\n]+", " ", s.output).strip(),
        body_html=sanitize_html(b.output),
        missing=list(dict.fromkeys(s.missing + b.missing)),
        unknown=list(dict.fromkeys(s.unknown + b.unknown)),
    )


SAMPLE_CONTACT = {"name": "Priya Sharma", "company": "Acme Labs", "role_title": "Talent Partner"}

_STARTERS_TEXT = [
    {
        "name": "AI Engineer",
        "role_label": "AI Engineer",
        "blurb": "LLM features, retrieval pipelines, evaluation tooling.",
        "subject": "Application for {{role}} — {{sender_name}}",
        "body": """Hi {{first_name|there}},

I'm {{sender_name}}, and I'm reaching out about {{role}} opportunities at {{company|your company}}. I build and ship LLM-powered features, from retrieval pipelines to evaluation tooling.

My resume is attached. If there's a suitable opening, or a better person to talk to, I'd be grateful for a pointer.

Thanks for your time,
{{sender_name}}""",
    },
    {
        "name": "Software Engineer / SDE",
        "role_label": "Software Engineer",
        "blurb": "Reliable backend services and clean, testable systems.",
        "subject": "{{role}} application — {{sender_name}}",
        "body": """Hi {{first_name|there}},

I'm {{sender_name}}, a software engineer interested in {{role}} roles at {{company|your company}}. I enjoy building reliable backend services and clean, testable systems.

I've attached my resume. I'd welcome the chance to talk if there's a fit.

Best regards,
{{sender_name}}""",
    },
    {
        "name": "Full Stack Developer",
        "role_label": "Full Stack Developer",
        "blurb": "Interfaces, APIs and databases, end to end.",
        "subject": "Full Stack Developer — {{sender_name}}",
        "body": """Hi {{first_name|there}},

I'm {{sender_name}}. I work across the stack, from web interfaces to APIs and databases, and I'm looking for a {{role}} role at {{company|a team like yours}}.

My resume is attached. Happy to share more about projects I've shipped.

Regards,
{{sender_name}}""",
    },
    {
        "name": "Data Engineer",
        "role_label": "Data Engineer",
        "blurb": "Pipelines and warehouse models people can trust.",
        "subject": "Data Engineer enquiry — {{sender_name}}",
        "body": """Hi {{first_name|there}},

I'm {{sender_name}}, and I'm exploring {{role}} positions at {{company|your company}}. I design data pipelines and warehouse models that people can trust.

Resume attached. Please let me know if there's an opening I should apply to.

Thank you,
{{sender_name}}""",
    },
    {
        "name": "Machine Learning Engineer",
        "role_label": "Machine Learning Engineer",
        "blurb": "Taking models from experiments to production.",
        "subject": "Machine Learning Engineer — {{sender_name}}",
        "body": """Hi {{first_name|there}},

I'm {{sender_name}}, a machine learning engineer interested in {{role}} roles at {{company|your company}}. I focus on taking models from experiments to production.

My resume is attached; I'd appreciate being considered for any relevant openings.

Kind regards,
{{sender_name}}""",
    },
    {
        "name": "Custom role",
        "role_label": "",
        "blurb": "A short, neutral starting point for any role.",
        "subject": "Interested in {{role}} opportunities — {{sender_name}}",
        "body": """Hi {{first_name|there}},

I'm {{sender_name}}, and I'm reaching out about {{role}} opportunities at {{company|your company}}.

My resume is attached. Thank you for your time.

Best,
{{sender_name}}""",
    },
]

STARTER_TEMPLATES = [{**t, "body": text_to_html(t["body"])} for t in _STARTERS_TEXT]
