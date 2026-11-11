"""Structured resume model. Stored as JSON on each ResumeVersion and validated on every save."""

import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

SECTION_KEYS = ("summary", "skills", "experience", "projects", "education", "certifications")
SECTION_LABELS = {
    "summary": "Professional summary",
    "skills": "Skills",
    "experience": "Experience",
    "projects": "Projects",
    "education": "Education",
    "certifications": "Certifications",
}


def S(n: int):
    return Annotated[str, StringConstraints(max_length=n)]


def new_id() -> str:
    return uuid.uuid4().hex[:10]


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)


class Link(_M):
    label: S(60) = ""
    url: S(300) = ""


class Basics(_M):
    name: S(120) = ""
    headline: S(160) = ""
    email: S(254) = ""
    phone: S(40) = ""
    location: S(120) = ""
    links: list[Link] = Field(default_factory=list, max_length=10)


class SkillGroup(_M):
    id: S(40) = Field(default_factory=new_id)
    category: S(80) = ""
    items: list[S(80)] = Field(default_factory=list, max_length=60)


class Experience(_M):
    id: S(40) = Field(default_factory=new_id)
    company: S(120) = ""
    title: S(120) = ""
    location: S(120) = ""
    start: S(30) = ""
    end: S(30) = ""
    current: bool = False
    bullets: list[S(600)] = Field(default_factory=list, max_length=20)


class Project(_M):
    id: S(40) = Field(default_factory=new_id)
    name: S(120) = ""
    url: S(300) = ""
    tech: list[S(60)] = Field(default_factory=list, max_length=30)
    bullets: list[S(600)] = Field(default_factory=list, max_length=15)


class Education(_M):
    id: S(40) = Field(default_factory=new_id)
    school: S(160) = ""
    degree: S(160) = ""
    start: S(30) = ""
    end: S(30) = ""
    details: S(500) = ""


class Certification(_M):
    id: S(40) = Field(default_factory=new_id)
    name: S(160) = ""
    issuer: S(120) = ""
    year: S(10) = ""
    url: S(300) = ""


class ResumeData(_M):
    basics: Basics = Field(default_factory=Basics)
    summary: S(2000) = ""
    skills: list[SkillGroup] = Field(default_factory=list, max_length=20)
    experience: list[Experience] = Field(default_factory=list, max_length=30)
    projects: list[Project] = Field(default_factory=list, max_length=30)
    education: list[Education] = Field(default_factory=list, max_length=10)
    certifications: list[Certification] = Field(default_factory=list, max_length=30)
    # Text from an imported file that could not be mapped to a section. Nothing from an import is silently dropped.
    additional: S(8000) = ""
    section_order: list[str] = Field(default_factory=lambda: list(SECTION_KEYS))

    @field_validator("section_order")
    @classmethod
    def _order(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for k in v:
            if k in SECTION_KEYS and k not in seen:
                seen.append(k)
        return seen + [k for k in SECTION_KEYS if k not in seen]


def parse_resume(data) -> ResumeData:
    return ResumeData.model_validate(data or {})


def resume_plain_text(r: ResumeData) -> str:
    parts = [r.basics.name, r.basics.headline, r.summary]
    for s in r.skills:
        parts += [s.category, *s.items]
    for e in r.experience:
        parts += [e.company, e.title, e.location, *e.bullets]
    for p in r.projects:
        parts += [p.name, *p.tech, *p.bullets]
    for e in r.education:
        parts += [e.school, e.degree, e.details]
    for c in r.certifications:
        parts += [c.name, c.issuer]
    parts.append(r.additional)
    return "\n".join(x for x in parts if x)
