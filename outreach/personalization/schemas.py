"""Pydantic models for the model outputs that are specific to this graph (JD analysis and the generated email
reuse the job-email schemas). Model output is untrusted: lists and strings are clipped, wrong types still fail."""

from typing import Annotated

from pydantic import BaseModel, Field

from ..personalized_email.schemas import GeneratedEmail, JDAnalysis, Line, Lines, Score, _clip_list, _clip_str  # noqa: F401


class SelectedItem(BaseModel):
    evidence_id: Annotated[str, _clip_str(20)]
    reason: Line = ""


class EvidenceSelection(BaseModel):
    selected: Annotated[list[SelectedItem], _clip_list(8)] = Field(default_factory=list)
    strongest: Annotated[list[Annotated[str, _clip_str(20)]], _clip_list(3)] = Field(default_factory=list)


class CriticReview(BaseModel):
    personalised: bool = True  # specific to this recipient / company / job, not a template with names swapped in
    reflects_job: bool = True
    generic: bool = False
    natural: bool = True
    follows_template: bool = True
    unsupported_hiring_or_relationship: bool = False
    unsupported_claims: Lines = Field(default_factory=list)
    relevance: Score = 3
    specificity: Score = 3
    tone: Score = 3
    needs_revision: bool = False
    revision_instructions: Annotated[str, _clip_str(600)] = ""
