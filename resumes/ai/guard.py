"""Checks every AI suggestion before the user sees it, and again when it is applied.

What the guard does:
- Each suggestion must add at least one JD keyword the resume is missing. Edits that only reshuffle
  wording without improving the ATS match are dropped, so the resume is not padded with filler.
- The "original" text of a rewrite is always read from the real resume, never from the model.
- Numbers or metrics that are not already in the resume BLOCK a suggestion (no invented achievements).
- New skills and new lines are allowed, but they are shown as additions the user must accept: the user
  decides what is true about their own experience.
- Suggestions are independent: accepting or declining one never changes another.
"""

import copy
import re

from pydantic import BaseModel, Field

from ..schema import ResumeData, SkillGroup, resume_plain_text
from .ats import contains, is_skill, norm, skill_group_for

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
STOP = {"I", "A", "An", "The", "And", "Or", "In", "On", "At", "With", "For", "Of", "To", "By", "As"}
LIMITS = {"headline": 120, "summary": 600, "bullet_rewrite": 260, "bullet_add": 260, "skill": 60}
MAX_BULLET_ADDS, MAX_ADDS_PER_ENTRY, MAX_SKILLS = 5, 2, 15
TYPE_ORDER = {"headline": 0, "summary": 1, "skill": 2, "bullet_rewrite": 3, "bullet_add": 4}


class Target(BaseModel):
    section: str = "summary"  # summary | headline | experience | projects
    entry_index: int | None = Field(default=None, ge=0)
    bullet_index: int | None = Field(default=None, ge=0)


class RawKeyword(BaseModel):
    term: str = Field(min_length=1, max_length=80)
    category: str = Field(default="", max_length=40)
    importance: str = Field(default="required", max_length=20)


class RawSuggestion(BaseModel):
    type: str = Field(max_length=30)  # headline | summary | bullet_rewrite | bullet_add
    target: Target = Field(default_factory=Target)
    proposed: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(default="", max_length=600)


class ModelOutput(BaseModel):
    job_title: str = Field(default="", max_length=160)
    keywords: list[RawKeyword] = Field(default_factory=list, max_length=40)
    suggestions: list[RawSuggestion] = Field(default_factory=list, max_length=30)


def numbers_in(text: str) -> list[str]:
    return [n.replace(",", "") for n in NUMBER_RE.findall(text)]


def terms_in(text: str) -> list[str]:
    """Capitalised / tech-looking tokens (not sentence-initial). Used by the email writer's guard."""
    out: dict[str, None] = {}
    for i, w in enumerate(text.split()):
        t = re.sub(r"^[^A-Za-z0-9#+.]+|[^A-Za-z0-9#+]+$", "", w)
        if len(t) < 2 or t in STOP:
            continue
        looks_tech = re.search(r"[A-Z].*[A-Z]|[a-z][A-Z]|\d|[#+]", t) or (i > 0 and re.match(r"^[A-Z][a-z]+", t))
        if looks_tech:
            out[t.lower()] = None
    return list(out)


def invented_numbers(proposed: str, resume: ResumeData) -> list[str]:
    known = set(numbers_in(resume_plain_text(resume)))
    return list(dict.fromkeys(n for n in numbers_in(proposed) if n not in known))


# Small models sometimes describe the edit ("Rewrote bullet[0] of Experience[0] to…") instead of writing it.
_META = re.compile(r"\b(?:bullet|experience|project|entry)s?\s*\[\s*\d+\s*\]|^(?:rewrote|rewrite|rephrased?|added|add(?:ed)? (?:a )?(?:new )?bullet|"
                   r"changed?|replace[ds]?|updated?|included?)\b", re.I)


def is_meta_text(text: str) -> bool:
    return bool(_META.search(text.strip()))


def _clean(text: str, kind: str) -> str:
    t = re.sub(r"^\s*(?:[-*•▪]\s*)", "", str(text or "")).replace("**", "").strip()
    t = re.sub(r"\s+", " ", t) if kind != "summary" else t
    return t[: LIMITS.get(kind, 600)]


def _entries(resume: ResumeData, section: str):
    return resume.experience if section == "experience" else resume.projects if section == "projects" else None


def original_text(resume: ResumeData, kind: str, t: Target) -> str | None:
    """What the suggestion replaces, read from the real resume. None means the target does not exist."""
    if kind == "headline":
        return resume.basics.headline
    if kind == "summary":
        return resume.summary
    entries = _entries(resume, t.section)
    if entries is None or t.entry_index is None or t.entry_index >= len(entries):
        return None
    if kind == "bullet_add":
        return ""
    if kind == "bullet_rewrite" and t.bullet_index is not None and t.bullet_index < len(entries[t.entry_index].bullets):
        return entries[t.entry_index].bullets[t.bullet_index]
    return None


def entry_name(resume: ResumeData, t: dict) -> str:
    entries = _entries(resume, t.get("section", ""))
    i = t.get("entry_index")
    if entries is None or i is None or i >= len(entries):
        return ""
    e = entries[i]
    return " · ".join(x for x in (getattr(e, "title", ""), getattr(e, "company", ""), getattr(e, "name", "")) if x)


def _skill_group(resume: ResumeData, term: str) -> str:
    wanted = skill_group_for(term)
    for g in resume.skills:
        if g.category.strip().lower() == wanted.lower():
            return g.category
    return resume.skills[0].category if resume.skills and wanted == "Skills" else wanted


def vet_tailoring(resume: ResumeData, out: ModelOutput, keywords: list[dict]) -> list[dict]:
    text = resume_plain_text(resume)
    missing = [k for k in keywords if not contains(text, k["term"])]
    suggestions, seen, adds_per_entry = [], set(), {}

    for s in out.suggestions:
        kind = s.type.strip().lower()
        if kind == "rewrite":
            kind = "bullet_rewrite"
        if kind not in ("headline", "summary", "bullet_rewrite", "bullet_add"):
            continue  # skills are proposed by the app itself from the missing keywords
        original = original_text(resume, kind, s.target)
        proposed = _clean(s.proposed, kind)
        if original is None or not proposed or proposed.strip() == (original or "").strip() or is_meta_text(proposed):
            continue
        adds = [k["term"] for k in missing if contains(proposed, k["term"]) and not contains(original, k["term"])]
        if not adds:
            continue  # no ATS gain: not worth changing the user's words
        key = (kind, s.target.section, s.target.entry_index, s.target.bullet_index) if kind != "bullet_add" else None
        if kind in ("headline", "summary"):
            key = (kind,)
        if key and key in seen:
            continue
        if kind == "bullet_add":
            entry = (s.target.section, s.target.entry_index)
            if sum(adds_per_entry.values()) >= MAX_BULLET_ADDS or adds_per_entry.get(entry, 0) >= MAX_ADDS_PER_ENTRY:
                continue
            adds_per_entry[entry] = adds_per_entry.get(entry, 0) + 1
        elif key:
            seen.add(key)
        bad = invented_numbers(proposed, resume)
        notes = []
        if bad:
            notes.append(f"Contains figures that are not in your resume ({', '.join(bad)}). Invented numbers are never applied; edit them out or decline.")
        if kind == "bullet_add":
            notes.append("New line. Accept only if it describes work you really did.")
        target = s.target.model_dump() if kind in ("bullet_rewrite", "bullet_add") else {"section": kind}
        suggestions.append({"type": kind, "target": target, "where": entry_name(resume, target), "original": original or "",
                            "proposed": proposed, "adds": adds, "rationale": s.rationale.strip()[:300],
                            "state": "blocked" if bad else "ok", "notes": notes})

    for k in [k for k in missing if is_skill(k)][:MAX_SKILLS]:
        suggestions.append({"type": "skill", "target": {"section": "skills"}, "where": "", "original": "", "proposed": k["term"],
                            "group": _skill_group(resume, k["term"]), "adds": [k["term"]], "rationale": f"The job asks for {k['term']}.",
                            "state": "ok", "notes": ["New skill. Accept only if you have it."]})

    suggestions.sort(key=lambda s: TYPE_ORDER[s["type"]])
    for i, s in enumerate(suggestions, 1):
        s["id"] = f"s{i}"
    return suggestions


def apply_tailoring(resume: ResumeData, accepted: list[dict]) -> ResumeData:
    """Applies accepted suggestions to a copy. Each one is re-checked here, and each is independent of the
    others: rewrites address the original bullets and new lines are appended, so declining one never shifts another."""
    nxt = copy.deepcopy(resume)
    adds: list[tuple[str, int, str]] = []
    for s in sorted(accepted, key=lambda s: TYPE_ORDER.get(str(s.get("type")), 9)):
        kind = str(s.get("type", ""))
        if kind not in LIMITS:
            continue
        proposed = _clean(s.get("proposed", ""), kind)
        if not proposed:
            continue
        if invented_numbers(proposed, resume):
            raise ValueError("A suggestion contains figures that are not in your resume, so it cannot be applied. Edit the number out or decline it.")
        t = Target.model_validate({**(s.get("target") or {}), "section": (s.get("target") or {}).get("section") or kind})
        if kind == "headline":
            nxt.basics.headline = proposed[:160]
        elif kind == "summary":
            nxt.summary = proposed[:2000]
        elif kind == "bullet_rewrite":
            if original_text(resume, kind, t) is not None:
                _entries(nxt, t.section)[t.entry_index].bullets[t.bullet_index] = proposed
        elif kind == "bullet_add":
            if original_text(resume, kind, t) is not None:
                adds.append((t.section, t.entry_index, proposed))
        elif kind == "skill":
            term = proposed[:80]
            if any(norm(i) == norm(term) for g in nxt.skills for i in g.items):
                continue
            group_name = str(s.get("group") or skill_group_for(term))[:80]
            group = next((g for g in nxt.skills if g.category.strip().lower() == group_name.lower()), None)
            if group is None:
                group = SkillGroup(category=group_name, items=[])
                nxt.skills.append(group)
            group.items.append(term)
    for section, i, text in adds:
        bullets = _entries(nxt, section)[i].bullets
        if len(bullets) < 20:
            bullets.append(text)
    return nxt
