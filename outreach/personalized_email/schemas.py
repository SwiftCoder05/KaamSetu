"""Pydantic models for every model output in the graph. Model output is untrusted: lists and strings are
clipped instead of rejected (small local models over-produce), but wrong types still fail validation."""

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field


def _clip_str(n: int):
    return BeforeValidator(lambda v: (v if isinstance(v, str) else "" if v is None else str(v))[:n] if not isinstance(v, (list, dict)) else v)


def _clip_list(n: int):
    return BeforeValidator(lambda v: v[:n] if isinstance(v, list) else v)


def _clamp(lo: int, hi: int):
    def f(v):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return v
    return BeforeValidator(f)


Short = Annotated[str, _clip_str(120)]
Line = Annotated[str, _clip_str(300)]
Terms = Annotated[list[Short], _clip_list(30)]
Lines = Annotated[list[Line], _clip_list(12)]
Score = Annotated[int, _clamp(1, 5)]


class JDAnalysis(BaseModel):
    job_title: Annotated[str, _clip_str(160)] = ""
    company: Annotated[str, _clip_str(160)] = ""
    required_skills: Terms = Field(default_factory=list)
    preferred_skills: Terms = Field(default_factory=list)
    languages: Terms = Field(default_factory=list)
    frameworks: Terms = Field(default_factory=list)
    cloud: Terms = Field(default_factory=list)
    databases: Terms = Field(default_factory=list)
    tools: Terms = Field(default_factory=list)
    responsibilities: Lines = Field(default_factory=list)
    experience_requirements: Lines = Field(default_factory=list)
    qualifications: Lines = Field(default_factory=list)
    team_context: Annotated[str, _clip_str(600)] = ""

    def all_skills(self) -> list[str]:
        out: dict[str, str] = {}
        for t in [*self.required_skills, *self.preferred_skills, *self.languages, *self.frameworks, *self.cloud,
                  *self.databases, *self.tools]:
            t = t.strip()
            if t and t.lower() not in out:
                out[t.lower()] = t
        return list(out.values())


class RawMatch(BaseModel):
    requirement: Short
    evidence_ids: Annotated[list[Annotated[str, _clip_str(20)]], _clip_list(5)] = Field(default_factory=list)
    explanation: Line = ""


class MatchOutput(BaseModel):
    matches: Annotated[list[RawMatch], _clip_list(15)] = Field(default_factory=list)
    strongest: Annotated[list[Annotated[str, _clip_str(20)]], _clip_list(3)] = Field(default_factory=list)


class GeneratedEmail(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    paragraphs: list[Annotated[str, Field(max_length=1500)]] = Field(min_length=1, max_length=8)
    rationale: Annotated[str, _clip_str(600)] = ""
    evidence_ids: Annotated[list[Annotated[str, _clip_str(20)]], _clip_list(8)] = Field(default_factory=list)


class QualityReview(BaseModel):
    relevance: Score = 3
    specificity: Score = 3
    tone: Score = 3
    clarity: Score = 3
    conciseness: Score = 3
    generic_phrases: Lines = Field(default_factory=list)
    unsupported_claims: Lines = Field(default_factory=list)
    cta_clear: bool = True
    needs_revision: bool = False
    revision_instructions: Annotated[str, _clip_str(600)] = ""
