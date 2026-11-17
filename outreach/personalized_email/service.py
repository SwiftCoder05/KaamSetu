"""Django-independent entry point. The view does authentication, ownership, opt-in, confirmation and rate
limiting, then passes plain data here. This module never saves anything and never sends mail."""

import os
from dataclasses import dataclass

from resumes.ai.provider import AiDisabledError, AiProvider, AiUnavailableError

from .graph import RECURSION_LIMIT, compiled_graph
from .state import GraphContext

# LangGraph ships with LangSmith. Tracing would upload prompts (resume text) to a third party, so it stays
# off unless an operator explicitly enables it in the environment.
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")


class JobEmailInputError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__(next(iter(errors.values()), "Invalid request."))


@dataclass
class JobEmailRequest:
    user_id: int
    company: str
    role: str
    job_description: str
    resume_version_id: int
    resume_label: str
    resume_data: dict  # ResumeVersion.data of a version the view verified belongs to user_id
    sender_name: str
    recruiter_name: str = ""
    instructions: str = ""
    resume_attached: bool = False
    ai_enabled: bool = False
    confirmed: bool = False


def generate_job_email(req: JobEmailRequest, provider: AiProvider | None) -> dict:
    """Runs the graph once (bounded). Raises AiDisabledError, AiUnavailableError, AiQuotaError, AiOutputError
    or JobEmailInputError; returns the final_result dict otherwise."""
    if not req.ai_enabled or not req.confirmed:
        raise AiDisabledError("AI is switched off. Turn it on in Settings, then press “Generate”.")
    if provider is None:
        raise AiUnavailableError("AI is not configured on this server. Set AI_PROVIDER in .env (see README).")
    state = compiled_graph().invoke(
        {
            "user_id": req.user_id, "ai_enabled": req.ai_enabled, "confirmed": req.confirmed, "company": req.company,
            "role": req.role, "job_description": req.job_description, "recruiter_name": req.recruiter_name,
            "instructions": req.instructions, "resume_attached": req.resume_attached, "resume_version_id": req.resume_version_id,
            "resume_label": req.resume_label, "resume_data": req.resume_data, "sender_name": req.sender_name, "trace": [],
        },
        context=GraphContext(provider=provider),
        config={"recursion_limit": RECURSION_LIMIT},
    )
    if state.get("input_errors"):
        raise JobEmailInputError(state["input_errors"])
    return {**state["final_result"], "model": provider.model, "trace": state.get("trace", [])}
