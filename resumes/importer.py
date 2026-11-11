"""Best-effort mapping of plain resume text into the structured model.

Heuristic by nature: anything unmatched is kept in `additional` so no content is lost, and the user is
told that formatting was not preserved. The original file is always stored unchanged.
"""

import re

from .schema import Certification, Education, Experience, Link, Project, ResumeData, SkillGroup

HEADINGS = [
    ("summary", r"(professional\s+)?(summary|profile|objective|about(\s+me)?)"),
    ("skills", r"(technical\s+|core\s+|key\s+)?(skills|technologies|competencies|tech\s+stack)"),
    ("experience", r"((professional|work|relevant)\s+)?(experience|employment(\s+history)?|work\s+history)"),
    ("projects", r"(personal\s+|academic\s+|selected\s+|key\s+)?projects"),
    ("education", r"education(\s+(and|&)\s+training)?"),
    ("certifications", r"(certifications?|certificates?|licenses?(\s+(and|&)\s+certifications?)?|courses)"),
    ("other", r"(achievements|awards|honou?rs|publications|interests|languages|volunteer(ing)?|extracurricular.*|references)"),
]
HEADINGS = [(k, re.compile(p, re.I)) for k, p in HEADINGS]

BULLET = re.compile(r"^\s*([•●▪◦‣∙·*\-–—]|\d+[.)])\s+")
DATE_TOKEN = r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+)?(?:19|20)\d{2}"
DATE_RANGE = re.compile(rf"({DATE_TOKEN})\s*(?:-|–|—|to)\s*({DATE_TOKEN}|present|current|now|ongoing)", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PHONE = re.compile(r"(\+?\d[\d\s().-]{8,}\d)")
URL_RE = re.compile(r"\b((?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com|[a-z0-9-]+\.(?:dev|io|me|com|in|tech|ai))/?[^\s,|)]*)", re.I)
ONGOING = re.compile(r"present|current|now|ongoing", re.I)


def _clip(s: str, n: int) -> str:
    return (s or "")[:n]


def heading_of(line: str):
    t = re.sub(r"[:\s]+$", "", line).strip()
    if not t or len(t) > 40:
        return None
    for key, rx in HEADINGS:
        if rx.fullmatch(t):
            return key
    return None


def _header_parts(lines: list[str]):
    joined = " | ".join(lines)
    rng = DATE_RANGE.search(joined)
    rest = []
    for ln in lines:
        x = DATE_RANGE.sub("", ln)
        x = re.sub(r"^[|•·\s]+|[|•·\s]+$", "", x).strip()
        if x:
            rest.append(x)
    parts = [p.strip() for ln in rest for p in re.split(r"\s+\|\s+|\s+[–—]\s+|\s+at\s+|\s{3,}", ln) if p.strip()]
    return rng, parts


def parse_resume_text(text: str) -> tuple[ResumeData, list[str]]:
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    non_empty = [ln for ln in lines if ln.strip()]
    data = ResumeData()
    notes = ["Imported as editable text. Layout, fonts, columns and graphics from the original file were not preserved; the original file is stored unchanged."]

    first_heading = next((i for i, ln in enumerate(lines) if heading_of(ln.strip())), -1)
    header_lines = [ln for ln in (non_empty[:6] if first_heading == -1 else lines[:first_heading]) if ln.strip()]
    name = next((ln for ln in header_lines if not EMAIL.search(ln) and not PHONE.search(ln) and len(ln.strip()) < 60), None)
    if name:
        data.basics.name = _clip(name.strip(), 120)
    header_text = " | ".join(header_lines)
    if m := EMAIL.search(header_text):
        data.basics.email = _clip(m.group(0), 254)
    if m := PHONE.search(header_text):
        data.basics.phone = _clip(m.group(1).strip(), 40)
    links: dict[str, Link] = {}
    for m in URL_RE.finditer(EMAIL.sub(" ", header_text)):
        url = re.sub(r"[.,;]+$", "", m.group(1))
        label = "LinkedIn" if "linkedin" in url.lower() else "GitHub" if "github" in url.lower() else "Website"
        links.setdefault(url.lower(), Link(label=label, url=_clip(url, 300)))
    data.basics.links = list(links.values())[:6]
    headline = next(
        (ln for ln in header_lines if ln.strip() != (name or "").strip() and not EMAIL.search(ln) and not PHONE.search(ln) and len(ln.strip()) < 100),
        None,
    )
    if headline:
        data.basics.headline = _clip(headline.strip(), 160)

    sections: list[tuple[str, list[str]]] = []
    start_at = len(lines) if first_heading == -1 else first_heading
    for raw in lines[start_at:]:
        h = heading_of(raw.strip())
        if h:
            sections.append((h, []))
        elif sections:
            sections[-1][1].append(raw)
    if not sections:
        notes.append("No section headings were recognised, so the full text was placed under 'Additional content'. Please sort it into sections.")
        data.additional = _clip("\n".join(ln for ln in non_empty if ln.strip() != (name or "").strip() and not EMAIL.search(ln) and not PHONE.search(ln)), 8000)
        return data, notes

    other: list[str] = []
    for key, sec_lines in sections:
        body = [ln for ln in sec_lines if ln.strip()]
        if key == "summary":
            data.summary = _clip(" ".join(body).strip(), 2000)
        elif key == "skills":
            for ln in body[:20]:
                clean = BULLET.sub("", ln)
                m = re.match(r"^([^:]{2,40}):\s*(.+)$", clean)
                items = [_clip(x.strip(), 80) for x in re.split(r"[,;|•·]", m.group(2) if m else clean) if x.strip()]
                data.skills.append(SkillGroup(category=_clip(m.group(1).strip(), 80) if m else "", items=items[:60]))
        elif key in ("experience", "projects"):
            header: list[str] = []
            bullets: list[str] = []

            def flush():
                nonlocal header, bullets
                if not header and not bullets:
                    return
                rng, parts = _header_parts(header)
                bl = [_clip(b, 600) for b in bullets]
                if key == "experience" and len(data.experience) < 30:
                    end = rng.group(2) if rng else ""
                    data.experience.append(
                        Experience(
                            title=_clip(parts[0] if parts else "", 120),
                            company=_clip(parts[1] if len(parts) > 1 else "", 120),
                            location=_clip(parts[2] if len(parts) > 2 else "", 120),
                            start=_clip(rng.group(1) if rng else "", 30),
                            end="" if ONGOING.search(end) else _clip(end, 30),
                            current=bool(ONGOING.search(end)),
                            bullets=bl[:20],
                        )
                    )
                elif key == "projects" and len(data.projects) < 30:
                    tech = [t.strip() for p in parts[1:] for t in re.split(r"[,;]", p) if t.strip()]
                    data.projects.append(
                        Project(name=_clip(parts[0] if parts else "", 120), tech=[_clip(t, 60) for t in tech[:30]], bullets=bl[:15])
                    )
                header, bullets = [], []

            for ln in body:
                if BULLET.match(ln):
                    bullets.append(BULLET.sub("", ln).strip())
                elif bullets and re.match(r"^\s{2,}\S", ln) and not DATE_RANGE.search(ln):
                    bullets[-1] += " " + ln.strip()
                else:
                    if bullets:
                        flush()
                    header.append(ln.strip())
            flush()
        elif key == "education":
            cur: list[str] = []

            def flush_edu():
                nonlocal cur
                if not cur:
                    return
                rng, parts = _header_parts(cur)
                if len(data.education) < 10:
                    data.education.append(
                        Education(
                            school=_clip(parts[0] if parts else "", 160),
                            degree=_clip(parts[1] if len(parts) > 1 else "", 160),
                            start=_clip(rng.group(1) if rng else "", 30),
                            end=_clip(rng.group(2) if rng else "", 30),
                            details=_clip("; ".join(parts[2:]), 500),
                        )
                    )
                cur = []

            for ln in body:
                if BULLET.match(ln) and cur:
                    cur.append(BULLET.sub("", ln))
                elif cur and DATE_RANGE.search(" ".join(cur)):
                    flush_edu()
                    cur.append(ln.strip())
                else:
                    cur.append(ln.strip())
            flush_edu()
        elif key == "certifications":
            for ln in body[:30]:
                clean = BULLET.sub("", ln).strip()
                year = m.group(0) if (m := re.search(r"\b(19|20)\d{2}\b", clean)) else ""
                parts = [x.strip() for x in re.split(r"\s+[–—|-]\s+|,\s+", re.sub(r"\b(19|20)\d{2}\b", "", clean, count=1)) if x.strip()]
                data.certifications.append(
                    Certification(name=_clip(parts[0] if parts else clean, 160), issuer=_clip(parts[1] if len(parts) > 1 else "", 120), year=year)
                )
        else:
            other += body
    if other:
        data.additional = _clip("\n".join(other), 8000)
        notes.append("Some sections (e.g. awards, languages) were placed under 'Additional content'.")
    if not data.experience:
        notes.append("No work experience entries were detected. Please check the Experience section.")
    notes.append("Imported entries (titles, companies, dates) are guesses from plain text. Please review each one.")
    return data, notes
