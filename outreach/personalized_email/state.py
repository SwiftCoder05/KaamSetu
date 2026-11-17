"""Typed LangGraph state. Plain, JSON-serialisable values only: no Django objects, no provider, no secrets.

Deliberately NOT in state: the recruiter's email address and the job URL. They are never needed by a node,
so keeping them out guarantees they can never reach a prompt. The view echoes them back to the browser.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

MAX_REVISIONS = 1


@dataclass(frozen=True)
class GraphContext:
    """Per-invocation runtime context (not state): the AI provider from resumes.ai.provider."""

    provider: Any


class JobEmailState(TypedDict, total=False):
    # input
    user_id: int
    ai_enabled: bool
    confirmed: bool
    company: str
    role: str
    job_description: str
    recruiter_name: str
    instructions: str
    resume_attached: bool
    resume_version_id: int
    resume_label: str
    resume_data: dict
    sender_name: str

    # candidate profile (deterministic, from ResumeVersion.data)
    evidence: list[dict]  # [{"id": "E0.B1", "where": "...", "text": "..."}]
    candidate_facts: str  # the evidence list rendered for prompts
    resume_text: str  # resume_plain_text(), used by the guard

    # analysis
    jd_analysis: dict
    experience_matches: list[dict]  # verified: {"requirement", "kind", "evidence": [...], "explanation"}
    strongest: list[str]  # evidence ids of the 2-3 strongest items
    unmatched_requirements: list[str]

    # draft
    email_subject: str
    email_paragraphs: list[str]
    email_rationale: str
    email_evidence_ids: list[str]
    email_quality_feedback: dict

    # validation
    validation_status: str  # ok | warning | blocked
    validation_warnings: list[str]
    validation_errors: list[str]  # fabrication problems in the draft
    input_errors: dict  # field -> message, from input validation
    revision_count: int
    final_subject: str  # validated subject/body (placeholders filled); only shown when not blocked
    final_body: str

    final_result: dict
    trace: Annotated[list[str], operator.add]  # node names in execution order (for tests and debugging)
