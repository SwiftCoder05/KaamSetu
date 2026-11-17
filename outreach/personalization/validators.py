"""Validation of what the user types for a recipient (page 2), and the company-consistency check of a draft.

The job URL is stored for the user's own reference only. It is never fetched (no server-side requests to
user-supplied addresses) and never sent to a model.
"""

import re
from urllib.parse import urlparse

from resumes.ai.ats import contains

from ..models import context_hash  # noqa: F401  (re-exported: the fingerprint of the job details)
from ..personalized_email.nodes import _NAME_RE

LIMITS = {"name": 120, "company": 120, "designation": 120, "job_title": 160, "job_description": 12000, "company_notes": 4000, "job_url": 500, "instructions": 1000}
CONTEXT_KEYS = ("company", "designation", "job_title", "job_description", "company_notes", "job_url", "instructions")


def _line(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def empty_context(contact=None) -> dict:
    """Starting values for a recipient card, taken from the contact (the Contact itself is never changed)."""
    return {"company": (getattr(contact, "company", "") or "")[:120], "designation": (getattr(contact, "role_title", "") or "")[:120],
            "job_title": "", "job_description": "", "company_notes": "", "job_url": "", "instructions": ""}


def clean_context(raw: dict) -> tuple[dict, dict[str, str]]:
    """Returns ({"name", **context}, errors). Single-line fields are collapsed; the job description keeps its line breaks."""
    raw = raw if isinstance(raw, dict) else {}
    out = {"name": _line(raw.get("name")), **{k: _line(raw.get(k)) for k in CONTEXT_KEYS if k not in ("job_description", "company_notes")}}
    for key in ("job_description", "company_notes"):
        out[key] = re.sub(r"\r\n?", "\n", str(raw.get(key) or "")).strip()
    errors: dict[str, str] = {}
    for key, limit in LIMITS.items():
        if len(out.get(key, "")) > limit:
            errors[key] = f"Too long (max {limit:,} characters)."
    if out["name"] and "name" not in errors and not _NAME_RE.match(out["name"]):
        errors["name"] = "Use only the person's name (letters, spaces, . ' -)."
    if out["job_url"] and "job_url" not in errors:
        u = urlparse(out["job_url"])
        if u.scheme not in ("http", "https") or not u.netloc or " " in out["job_url"]:
            errors["job_url"] = "Enter a full link starting with http:// or https://."
    return out, errors


def check_company_consistency(text: str, *, company: str, other_companies: list[str], allowed_text: str) -> list[str]:
    """Errors when the email names another recipient's company (unless this recipient's own details mention it)."""
    mine = (company or "").strip().lower()
    out = []
    for other in dict.fromkeys(c.strip() for c in other_companies if c and c.strip()):
        o = other.lower()
        if len(o) < 3 or o == mine or (mine and (o in mine or mine in o)):
            continue
        if contains(text, other) and not contains(allowed_text, other):
            out.append(f"Mentions “{other}”, which is another recipient's company" + (f", not {company}." if company else "."))
    return out


_GENERIC_BOXES = {"hr", "info", "jobs", "job", "careers", "career", "recruit", "recruiter", "recruiting", "recruitment", "talent", "hiring",
                  "admin", "support", "hello", "hi", "contact", "team", "office", "mail", "email", "enquiries", "inquiries", "apply", "resume",
                  "cv", "work", "people", "staffing", "noreply", "no-reply", "sales", "help", "service", "careersupport"}


def guess_name_from_email(email: str) -> str:
    """A name taken from the shape of an address, only when it is clearly a name: `priya.sharma@` -> "Priya Sharma",
    `rahul@` -> "Rahul". Anything unclear (`varunpes34@`, `hr@`, `jobs@`) returns "" rather than a wrong greeting."""
    local = (email or "").split("@")[0].lower()
    if not local or any(ch.isdigit() for ch in local) and not re.search(r"[._-]", local):
        return ""  # letters mixed with digits and no separator: a handle, not a name
    parts = [p for p in re.split(r"[._+\-]+", re.sub(r"\d+", "", local)) if p]
    if not parts or any(p in _GENERIC_BOXES for p in parts) or any(not p.isalpha() for p in parts):
        return ""
    if len(parts) == 1:
        return parts[0].title() if 3 <= len(parts[0]) <= 15 else ""
    if len(parts) <= 3 and all(2 <= len(p) <= 20 for p in parts):
        return " ".join(p.title() for p in parts)
    return ""
