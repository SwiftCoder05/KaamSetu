"""Django-independent entry point: plain data in, one result dict out. Never saves anything, never sends mail.
The views check login, ownership, opt-in, confirmation and quota first; runner.py does the saving."""

import os
from dataclasses import dataclass, field

from resumes.ai.provider import AiDisabledError, AiProvider, AiUnavailableError

from .state import GraphContext
from .workflow import RECURSION_LIMIT, compiled_graph

# LangGraph ships with LangSmith. Tracing would upload prompts (resume text, recipient names) to a third party, so it
# stays off unless an operator explicitly enables it in the environment.
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")


@dataclass
class RecipientRequest:
    sender_name: str
    resume_data: dict  # ResumeVersion.data of the campaign's own version ({} when no studio resume is attached)
    template_subject: str
    template_text: str  # the user's template rendered for this recipient (plain text)
    recipient_name: str = ""
    company: str = ""
    designation: str = ""
    job_title: str = ""
    job_description: str = ""
    company_notes: str = ""
    instructions: str = ""
    resume_version_id: int = 0
    resume_attached: bool = False
    other_companies: list[str] = field(default_factory=list)  # for the guard only; never in a prompt
    campaign_id: int = 0
    recipient_id: int = 0


def run_graph(req: RecipientRequest, provider: AiProvider | None) -> dict:
    """One bounded graph run for one recipient. Raises AiDisabledError (no provider), AiUnavailableError, AiQuotaError or
    AiOutputError from the provider layer. Returns the final result; invalid input comes back as ready=False with
    `input_errors` (nothing was sent to a model in that case)."""
    if provider is None:
        raise AiUnavailableError("AI is not configured on this server. Set AI_PROVIDER in .env (see README).")
    state = compiled_graph().invoke(
        {
            "campaign_id": req.campaign_id, "recipient_id": req.recipient_id, "sender_name": req.sender_name,
            "resume_version_id": req.resume_version_id, "resume_data": req.resume_data, "resume_attached": req.resume_attached,
            "template_subject": req.template_subject, "template_text": req.template_text, "recipient_name": req.recipient_name,
            "company": req.company, "designation": req.designation, "job_title": req.job_title,
            "job_description": req.job_description, "company_notes": req.company_notes, "instructions": req.instructions, "other_companies": req.other_companies,
            "trace": [],
        },
        context=GraphContext(provider=provider),
        config={"recursion_limit": RECURSION_LIMIT},
    )
    if state.get("input_errors"):
        return {"ready": False, "validation_status": "blocked", "input_errors": state["input_errors"], "errors": list(state["input_errors"].values()),
                "warnings": [], "subject": "", "body": "", "body_html": "", "trace": state.get("trace", []), "model": provider.model}
    return {**state["final_result"], "input_errors": {}, "model": provider.model, "trace": state.get("trace", [])}


__all__ = ["RecipientRequest", "run_graph", "AiDisabledError"]
