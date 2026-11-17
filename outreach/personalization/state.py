"""Typed LangGraph state for ONE recipient's email. Plain JSON-serialisable values only: no Django objects, no
provider, no credentials, no account data.

Deliberately NOT in state: the recruiter's email address and the job URL (no node needs them, so they can never
reach a prompt). `other_companies` is used only by the final guard, to catch another recipient's company showing
up in this email; no prompt ever contains it.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

MAX_REVISIONS = 2


@dataclass(frozen=True)
class GraphContext:
    """Per-invocation runtime context (not state): the AI provider from resumes.ai.provider."""

    provider: Any


class PersonalizationState(TypedDict, total=False):
    # ids (only for tracing and tests; no node reads them)
    campaign_id: int
    recipient_id: int
    # sender and resume
    sender_name: str
    resume_version_id: int
    resume_data: dict
    resume_attached: bool
    # the user's template, already rendered for this recipient
    template_subject: str
    template_text: str
    # recipient and job context (all optional except the name rules in validate_input)
    recipient_name: str
    company: str
    designation: str
    job_title: str
    job_description: str
    company_notes: str  # the user's own research about the company: facts the email may use
    instructions: str
    other_companies: list[str]
    has_jd: bool

    input_errors: dict

    # branch A: job context analyst
    jd_analysis: dict
    # branch B: candidate evidence analyst
    evidence: list[dict]  # [{"id": "E0.B1", "where": "...", "text": "..."}]
    candidate_facts: str
    resume_text: str  # what the fact check treats as true about the candidate
    selected: list[dict]  # evidence picked for this job: {"id", "reason"}
    selected_strongest: list[str]
    # merge
    experience_matches: list[dict]
    strongest: list[str]
    unmatched_requirements: list[str]
    resolved_title: str

    # draft
    email_subject: str
    email_paragraphs: list[str]
    email_rationale: str
    email_evidence_ids: list[str]
    critic: dict

    # validation
    validation_status: str  # ok | warning | blocked
    validation_errors: list[str]
    validation_warnings: list[str]
    final_subject: str
    final_body: str
    final_body_html: str
    revision_count: int

    final_result: dict
    trace: Annotated[list[str], operator.add]
