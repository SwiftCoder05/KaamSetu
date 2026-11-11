"""Email address validation, manual-paste parsing and duplicate detection for contact imports."""

import re
from dataclasses import asdict, dataclass
from typing import Iterable

_LOCAL = re.compile(r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*", re.I)
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", re.I)
_TLD = re.compile(r"[a-z]{2,}", re.I)
_EDGES = " \t\r\n\"'`<>()[]{}.,;:!?"
_FREE_TEXT = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_NAMED = re.compile(r"\s*\"?([^\"<>,;@]+?)\"?\s*<([^<>\s]+@[^<>\s]+)>\s*[,;]?\s*")


@dataclass
class ContactDraft:
    email: str
    name: str = ""
    company: str = ""
    role_title: str = ""
    status: str = "valid"  # valid | invalid | duplicate
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def is_valid_email(raw: str) -> bool:
    """Strict-enough RFC 5322 subset: no quoted locals, no IP literals, TLD of 2+ letters."""
    email = raw.strip()
    if len(email) > 254:
        return False
    at = email.rfind("@")
    if at < 1 or at != email.find("@"):
        return False
    local, domain = email[:at], email[at + 1 :]
    if len(local) > 64 or not _LOCAL.fullmatch(local):
        return False
    labels = domain.split(".")
    if len(labels) < 2 or any(not lb or len(lb) > 63 or not _LABEL.fullmatch(lb) for lb in labels):
        return False
    return bool(_TLD.fullmatch(labels[-1]))


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def _clean_token(token: str) -> str:
    if token.lower().startswith("mailto:"):
        token = token[7:]
    return token.strip(_EDGES)


def finalize_drafts(drafts: list[ContactDraft], existing: Iterable[str] = ()) -> list[ContactDraft]:
    """Marks duplicates (case-insensitive), keeping input order. The first occurrence stays valid."""
    already = {normalize_email(e) for e in existing}
    seen: set[str] = set()
    out = []
    for d in drafts:
        if d.status == "invalid":
            out.append(d)
            continue
        key = normalize_email(d.email)
        if key in already:
            out.append(ContactDraft(key, d.name, d.company, d.role_title, "duplicate", "Already in your contacts"))
        elif key in seen:
            out.append(ContactDraft(key, d.name, d.company, d.role_title, "duplicate", "Duplicate in this list"))
        else:
            seen.add(key)
            out.append(ContactDraft(key, d.name, d.company, d.role_title, "valid", ""))
    return out


def _judge(email: str, name: str = "") -> ContactDraft:
    if is_valid_email(email):
        return ContactDraft(email, name=name)
    reason = "Not a valid email address" if "@" in email else "Missing @, not an email address"
    return ContactDraft(email, status="invalid", reason=reason)


def parse_manual_input(text: str, existing: Iterable[str] = ()) -> list[ContactDraft]:
    """Comma, semicolon, space and newline separated. Every token is judged, so malformed entries are
    reported rather than silently dropped. Understands `Jane Doe <jane@acme.com>` and `mailto:`."""
    drafts: list[ContactDraft] = []
    for line in re.split(r"\r?\n", text):
        named = _NAMED.fullmatch(line)
        if named:
            drafts.append(_judge(_clean_token(named.group(2)), named.group(1).strip()))
            continue
        for raw in re.split(r"[,;\s]+", line):
            email = _clean_token(raw)
            if email:
                drafts.append(_judge(email))
    return finalize_drafts(drafts, existing)


def extract_emails_from_text(text: str, existing: Iterable[str] = ()) -> list[ContactDraft]:
    """Scans arbitrary document text for things that look like emails."""
    drafts = []
    for m in _FREE_TEXT.findall(text):
        email = _clean_token(m)
        drafts.append(ContactDraft(email) if is_valid_email(email) else ContactDraft(email, status="invalid", reason="Not a valid email address"))
    return finalize_drafts(drafts, existing)


def summarize(drafts: list[ContactDraft]) -> dict:
    return {s: sum(1 for d in drafts if d.status == s) for s in ("valid", "invalid", "duplicate")}
