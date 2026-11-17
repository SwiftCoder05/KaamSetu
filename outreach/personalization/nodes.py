"""Graph nodes for ONE recipient. Each is a plain function: (state[, runtime]) -> partial state update.

Model calls go through the existing provider (runtime.context.provider.generate_json). Provider errors
(AiUnavailableError, AiQuotaError) propagate and end the run: nothing has been saved at that point, because saving
happens in runner.py, outside the graph. Unreadable model output is retried once, then handled per node.

Reused from the job-email graph (same guard, same evidence ids): _call, build_evidence, facts_for_prompt, guard_text,
ensure_signoff, deterministic_quality, analyse_job (JD extraction with its "only what the JD says" filter).
"""

import re

from langgraph.graph import END
from langgraph.runtime import Runtime
from pydantic import ValidationError

from resumes.ai.ats import contains
from resumes.ai.provider import AiOutputError
from resumes.schema import parse_resume, resume_plain_text

from ..personalized_email import nodes as job_nodes
from ..personalized_email.guard import validate_email
from . import prompts
from .schemas import CriticReview, EvidenceSelection, GeneratedEmail, JDAnalysis
from .state import MAX_REVISIONS, GraphContext, PersonalizationState
from .validators import check_company_consistency, clean_context

TARGET_WORDS = 130
_ASK = re.compile(r"\b(?:guidance|advice|appreciate|welcome|happy to|glad to|open to|let me know|look forward|would love|could you|can you|would you)\b", re.I)
_call = job_nodes._call
_rank = job_nodes._rank


# ───────────── 1. input validation ─────────────


def validate_input(state: PersonalizationState) -> dict:
    errors: dict[str, str] = {}
    ctx, ctx_errors = clean_context({"name": state.get("recipient_name"), **{k: state.get(k) for k in
                                    ("company", "designation", "job_title", "job_description", "company_notes", "instructions")}})
    errors.update({k: v for k, v in ctx_errors.items() if k != "job_url"})
    if not (state.get("sender_name") or "").strip():
        errors["sender_name"] = "Your sender name is missing. Add it in Settings."
    if not (state.get("template_subject") or "").strip() and not (state.get("template_text") or "").strip():
        errors["template"] = "The email template is empty or was deleted."
    resume_text = ""
    try:
        resume_text = resume_plain_text(parse_resume(state.get("resume_data") or {}))
    except ValidationError:
        errors["resume"] = "The selected resume could not be read. Open it in the resume studio and save it again."
    if "resume" not in errors and not resume_text.strip():
        errors["resume"] = ("No resume details are available, so the AI has no facts about you to use. "
                            "Choose a resume from the resume studio for AI personalisation.")
    return {"input_errors": errors, "recipient_name": ctx["name"], "company": ctx["company"], "designation": ctx["designation"],
            "job_title": ctx["job_title"], "job_description": ctx["job_description"], "company_notes": ctx["company_notes"], "instructions": ctx["instructions"],
            "has_jd": bool(ctx["job_description"]), "revision_count": 0, "trace": ["validate_input"]}


def route_after_input(state: PersonalizationState):
    """Fan-out: both analysts run in parallel (the same superstep) when the input is valid."""
    return END if state.get("input_errors") else ["job_context", "candidate_evidence"]


# ───────────── 2a. Agent A: job context analyst ─────────────


def job_context(state: PersonalizationState, runtime: Runtime[GraphContext]) -> dict:
    """With a job description: structured analysis (only what the JD says). Without one: no model call and no
    invented requirements; only the facts the user gave (company, title) are carried forward."""
    if not state["has_jd"]:
        empty = JDAnalysis(job_title=state.get("job_title", ""), company=state.get("company", ""))
        return {"jd_analysis": empty.model_dump(), "trace": ["job_context:general"]}
    out = job_nodes.analyse_job({"company": state.get("company") or "(not given)", "role": state.get("job_title") or "(not given)",
                                 "job_description": state["job_description"]}, runtime)
    return {"jd_analysis": out["jd_analysis"], "trace": ["job_context:jd"]}


# ───────────── 2b. Agent B: candidate evidence analyst ─────────────


def _default_picks(evidence: list[dict]) -> list[str]:
    """No job to match against: the strongest generic evidence (real bullets from the most recent items first)."""
    bullets = [e for e in evidence if ".B" in e["id"]]
    return [e["id"] for e in sorted(bullets, key=_rank)[:3]]


def candidate_evidence(state: PersonalizationState, runtime: Runtime[GraphContext]) -> dict:
    evidence = job_nodes.build_evidence(state["resume_data"])
    base = {"evidence": evidence, "candidate_facts": job_nodes.facts_for_prompt(evidence),
            "resume_text": job_nodes.guard_text(state["resume_data"])}
    by_id = {e["id"]: e for e in evidence}
    selected: list[dict] = []
    strongest: list[str] = []
    if state["has_jd"] or state.get("job_title"):
        # Works from the RAW job text, so it never waits for (or duplicates) branch A's requirement extraction.
        user = (f"<job>\nrole: {state.get('job_title') or '(not given)'}\ncompany: {state.get('company') or '(not given)'}\n"
                f"description:\n{state['job_description'][:3500] or '(none)'}\n</job>\n\n"
                f"<candidate_evidence>\n{base['candidate_facts']}\n</candidate_evidence>")
        out = _call(runtime.context.provider, prompts.SELECT_SYSTEM, user, EvidenceSelection)
        if out is not None:
            seen = set()
            for s in out.selected:
                i = s.evidence_id.strip()
                if i in by_id and i not in seen:  # ids are re-read from the real resume; unknown ids are dropped
                    seen.add(i)
                    selected.append({"id": i, "reason": s.reason.strip()})
            strongest = [i.strip() for i in out.strongest if i.strip() in by_id][:3]
    if not selected:
        selected = [{"id": i, "reason": ""} for i in _default_picks(evidence)]
    if not strongest:
        strongest = [s["id"] for s in selected if ".B" in s["id"]][:3]
    return {**base, "selected": selected[:6], "selected_strongest": strongest, "trace": ["candidate_evidence"]}


# ───────────── 3. merge (deterministic) ─────────────


def merge_context(state: PersonalizationState) -> dict:
    jd = JDAnalysis.model_validate(state["jd_analysis"])
    evidence = state["evidence"]
    matches, covered = [], set()
    for skill in jd.all_skills():
        hits = sorted((e for e in evidence if contains(e["text"], skill)), key=_rank)[:3]
        if hits:
            covered.add(skill.lower())
            matches.append({"requirement": skill, "kind": "explicit", "evidence": hits})
    unmatched = [s for s in jd.all_skills() if s.lower() not in covered]
    by_id = {e["id"]: e for e in evidence}
    strongest = state.get("selected_strongest") or [m["evidence"][0]["id"] for m in matches if m["evidence"][0]["id"] in by_id][:3]
    return {"experience_matches": matches, "strongest": list(dict.fromkeys(strongest))[:3], "unmatched_requirements": unmatched[:15],
            "resolved_title": state.get("job_title") or jd.job_title, "trace": ["merge_context"]}


# ───────────── 4. email generation (and refinement) ─────────────


def _email_prompt(state: PersonalizationState, feedback: list[str] | None = None) -> str:
    jd = JDAnalysis.model_validate(state["jd_analysis"])
    by_id = {e["id"]: e for e in state["evidence"]}
    first = (state.get("recipient_name") or "").split()[:1]
    roles = "\n".join(f"- [{e['id']}] {e['text']}" for e in state["evidence"] if re.fullmatch(r"[EP]\d+|HL|ED\d+", e["id"]))
    picked = "\n".join(f"- [{s['id']}] ({by_id[s['id']]['where']}) {by_id[s['id']]['text']}" + (f" — {s['reason']}" if s.get("reason") else "")
                       for s in state.get("selected", []) if s["id"] in by_id)
    strongest = ", ".join(f"[{i}]" for i in state.get("strongest", []))
    matches = "\n".join(f"- {m['requirement']}: " + "; ".join(f"[{e['id']}] {e['text']}" for e in m["evidence"]) for m in state["experience_matches"])
    job = ""
    if state["has_jd"]:
        job = ("responsibilities:\n" + "\n".join(f"- {r}" for r in jd.responsibilities[:8]) + "\n"
               f"required skills: {', '.join(jd.required_skills) or '(none listed)'}\nteam context: {jd.team_context or '(not stated)'}\n")
    base = f"Subject: {state['template_subject']}\n\n{state['template_text']}"
    user = (
        f"<mode>\n{'job' if state['has_jd'] else 'general'}\n</mode>\n\n"
        f"<recipient>\nfirst name: {first[0] if first else '(unknown)'}\ncompany: {state.get('company') or '(unknown)'}\n"
        f"designation: {state.get('designation') or '(unknown)'}\n</recipient>\n\n"
        f"<job>\ntarget role: {state.get('resolved_title') or '(not specified)'}\n{job}</job>\n\n"
        f"<company_notes>\n{state.get('company_notes') or '(none)'}\n</company_notes>\n\n"
        f"<sender_name>\n{state['sender_name']}\n</sender_name>\n\n"
        f"<verified_evidence>\nroles, projects and education:\n{roles or '(none)'}\n\n"
        f"chosen for this recipient:\n{picked or '(none)'}\nstrongest: {strongest or '(none)'}\n\n"
        f"skill matches:\n{matches or '(none)'}\n</verified_evidence>\n\n"
        f"<not_evidenced>\n{', '.join(state.get('unmatched_requirements') or []) or '(none)'}\n</not_evidenced>\n\n"
        f"<base_email>\n{base}\n</base_email>\n\n"
        f"<resume_attached>\n{'yes' if state.get('resume_attached') else 'no'}\n</resume_attached>\n\n"
        f"<extra_instructions>\n{state.get('instructions') or '(none)'}\n</extra_instructions>"
    )
    if feedback:
        prev = state.get("email_subject", "") + "\n\n" + "\n\n".join(state.get("email_paragraphs", []))
        user += ("\n\n<previous_draft>\n" + prev + "\n</previous_draft>\n\n<problems_to_fix>\n" + "\n".join(f"- {f}" for f in feedback) +
                 "\n</problems_to_fix>\nRewrite the email to fix every problem. Remove any claim that is not in <verified_evidence>.")
    return user


def _system() -> str:
    return prompts.EMAIL_SYSTEM.replace("{words}", str(TARGET_WORDS))


def generate_email(state: PersonalizationState, runtime: Runtime[GraphContext]) -> dict:
    out = _call(runtime.context.provider, _system(), _email_prompt(state), GeneratedEmail)
    if out is None:
        raise AiOutputError("The AI reply could not be understood. Nothing was changed; try again.")
    return {**job_nodes._draft_update(out, state["sender_name"]), "trace": ["generate_email"]}


def revise_email(state: PersonalizationState, runtime: Runtime[GraphContext]) -> dict:
    """A refinement (at most MAX_REVISIONS). If the model fails here the previous draft stays and is re-checked."""
    fb = state.get("critic") or {}
    feedback = list(state.get("validation_errors") or []) + list(fb.get("issues") or [])
    feedback = list(dict.fromkeys(feedback))
    out = _call(runtime.context.provider, _system(), _email_prompt(state, feedback[:10] or ["Make it more specific to this recipient."]), GeneratedEmail)
    update = {"revision_count": state.get("revision_count", 0) + 1, "trace": ["revise_email"]}
    return {**update, **job_nodes._draft_update(out, state["sender_name"])} if out is not None else update


# ───────────── 5. independent critic ─────────────


def critic_issues(r: CriticReview, has_jd: bool) -> list[str]:
    issues = []
    if not r.personalised or r.generic:
        issues.append("Reads as generic: it does not feel written for this recipient.")
    if has_jd and not r.reflects_job:
        issues.append("Does not reflect the supplied job description.")
    if not r.natural:
        issues.append("Does not sound natural.")
    if not r.follows_template:
        issues.append("Does not follow your template or instructions.")
    if r.unsupported_hiring_or_relationship:
        issues.append("Claims hiring, a relationship or company news that was not given.")
    issues += [f"Unsupported claim: {c}" for c in r.unsupported_claims[:3]]
    if r.needs_revision and r.revision_instructions.strip():
        issues.append(r.revision_instructions.strip())
    return issues


def review_email(state: PersonalizationState, runtime: Runtime[GraphContext]) -> dict:
    """Feedback for refinement. A good score is never treated as proof that the email is factually correct:
    that is the guard's job (validate_fabrication), which runs in code."""
    paragraphs = state["email_paragraphs"]
    issues = [i for i in job_nodes.deterministic_quality(state["email_subject"], paragraphs) if not i.startswith("No clear call to action")]
    body = " ".join(paragraphs)
    if not job_nodes._CTA.search(body) and not _ASK.search(body):
        issues.append("No clear call to action (for example, asking for a short call).")
    if state["email_subject"].strip().lower() == (state.get("template_subject") or "").strip().lower():
        issues.append("The subject is the template's subject. Write one specific to this role or company.")
    jd = JDAnalysis.model_validate(state["jd_analysis"])
    user = (f"<mode>\n{'job' if state['has_jd'] else 'general'}\n</mode>\n\n"
            f"<recipient>\nfirst name: {(state.get('recipient_name') or '').split()[:1] or '(unknown)'}\ncompany: {state.get('company') or '(unknown)'}\n</recipient>\n\n"
            f"<job>\ntarget role: {state.get('resolved_title') or '(not specified)'}\n" + "\n".join(f"- {r}" for r in jd.responsibilities[:8]) + "\n</job>\n\n"
            f"<company_notes>\n{state.get('company_notes') or '(none)'}\n</company_notes>\n\n"
            f"<verified_evidence>\n{state['candidate_facts']}\n</verified_evidence>\n\n"
            f"<base_email>\nSubject: {state['template_subject']}\n\n{state['template_text']}\n</base_email>\n\n"
            f"<extra_instructions>\n{state.get('instructions') or '(none)'}\n</extra_instructions>\n\n"
            f"<email>\nSubject: {state['email_subject']}\n\n" + "\n\n".join(paragraphs) + "\n</email>")
    out = _call(runtime.context.provider, prompts.CRITIC_SYSTEM, user, CriticReview)
    fb: dict = {"available": out is not None, "issues": issues}
    if out is not None:
        fb.update(out.model_dump())
        fb["issues"] = list(dict.fromkeys(issues + critic_issues(out, state["has_jd"])))
        weak = out.relevance <= 2 or out.specificity <= 2
        fb["needs_revision"] = bool(out.needs_revision or weak or fb["issues"])
    else:
        fb["needs_revision"] = bool(issues)
    return {"critic": fb, "trace": ["review_email"]}


# ───────────── 6. final validation and guard (in code, independent of the model) ─────────────


def validate_fabrication(state: PersonalizationState) -> dict:
    jd = JDAnalysis.model_validate(state["jd_analysis"])
    user_text = "\n".join([state["job_description"], state.get("company_notes", ""), state["template_subject"], state["template_text"], state.get("instructions", ""),
                           state.get("designation", ""), state.get("job_title", "")])
    try:
        v = validate_email(
            subject=state["email_subject"], paragraphs=state["email_paragraphs"], resume_text=state["resume_text"], jd_text=user_text,
            jd_skills=jd.all_skills(), company=state.get("company", ""), role=state.get("resolved_title", ""),
            recruiter_name=state.get("recipient_name", ""), sender_name=state["sender_name"],
            evidence_ids=state.get("email_evidence_ids", []), known_ids={e["id"] for e in state["evidence"]},
            has_job_description=state["has_jd"], allow_company_news=bool(state.get("company_notes")),
        )
    except AiOutputError:
        v = {"subject": "", "body": "", "body_html": "", "status": "blocked", "errors": ["The AI returned an empty email."], "warnings": []}
    errors = list(v["errors"])
    allowed = "\n".join([state["resume_text"], user_text, state.get("company", ""), state.get("recipient_name", ""), state["sender_name"]])
    errors += check_company_consistency(v["subject"] + "\n" + v["body"], company=state.get("company", ""),
                                        other_companies=state.get("other_companies", []), allowed_text=allowed)
    errors = list(dict.fromkeys(errors))
    warnings = [w for w in v["warnings"] if w not in errors]
    status = "blocked" if errors else "warning" if warnings else "ok"
    return {"validation_status": status, "validation_errors": errors, "validation_warnings": warnings, "final_subject": v["subject"],
            "final_body": v["body"], "final_body_html": v["body_html"], "trace": ["validate_fabrication"]}


# ───────────── routing (bounded) ─────────────


def route_after_validation(state: PersonalizationState) -> str:
    """At most MAX_REVISIONS refinements, whatever the critic or the guard says."""
    if state.get("revision_count", 0) >= MAX_REVISIONS:
        return "finalize"
    needs = state.get("validation_status") == "blocked" or (state.get("critic") or {}).get("needs_revision")
    return "revise" if needs else "finalize"


# ───────────── 7. finalize ─────────────


def finalize(state: PersonalizationState) -> dict:
    status = state["validation_status"]
    ready = status != "blocked"  # a blocked draft is never presented as an email
    by_id = {e["id"]: e for e in state["evidence"]}
    critic = dict(state.get("critic") or {})
    warnings = list(state.get("validation_warnings", []))
    if ready:  # reviewer concerns that survived the refinements are shown to the user, not hidden
        warnings += [f"Reviewer: {i}" for i in critic.get("issues", [])[:3] if f"Reviewer: {i}" not in warnings]
        if critic.get("unsupported_hiring_or_relationship"):
            status = "warning"
    used = [by_id[i] for i in state.get("email_evidence_ids", []) if i in by_id]
    result = {
        "ready": ready,
        "validation_status": status if ready else "blocked",
        "subject": state["final_subject"] if ready else "",
        "body": state["final_body"] if ready else "",
        "body_html": state["final_body_html"] if ready else "",
        "mode": "job" if state["has_jd"] else "general",
        "company": state.get("company", ""),
        "job_title": state.get("resolved_title", ""),
        "matched_skills": [m["requirement"] for m in state["experience_matches"]],
        "missing_skills": state.get("unmatched_requirements", []),
        "relevant_experience": [by_id[i] for i in state.get("strongest", []) if i in by_id],
        "warnings": warnings if ready or status != "blocked" else [],
        "errors": state.get("validation_errors", []),
        "rationale": state.get("email_rationale", ""),
        "revision_count": state.get("revision_count", 0),
        "critic_available": critic.get("available", False),
        "evidence": used,
    }
    return {"final_result": result, "trace": ["finalize"]}
