"""Graph nodes. Each is a plain function: (state[, runtime]) -> partial state update.

Model calls go through the existing provider (runtime.context.provider.generate_json). Provider errors
(AiUnavailableError, AiQuotaError) propagate and end the run; nothing is saved anywhere, so a failed run
leaves no partial records. Unreadable model output is retried once, then handled per node.
"""

import json
import re

from langgraph.runtime import Runtime
from pydantic import ValidationError

from resumes.ai.ats import contains
from resumes.ai.provider import AiOutputError
from resumes.ai.tailor import extract_json
from resumes.schema import parse_resume, resume_plain_text

from . import prompts
from .guard import validate_email
from .schemas import GeneratedEmail, JDAnalysis, MatchOutput, QualityReview
from .state import MAX_REVISIONS, GraphContext, JobEmailState

LIMITS = {"company": 120, "role": 160, "recruiter_name": 120, "instructions": 1000, "jd_min": 80, "jd_max": 12000}
MAX_FACT_CHARS = 8000
TARGET_WORDS = 130
_NAME_RE = re.compile(r"^[^\W\d_][\w .'’-]*$", re.UNICODE)
_GENERIC = (
    "i am writing to express", "i am writing to apply", "to whom it may concern", "great fit", "perfect fit",
    "i believe i would be", "passionate about leveraging", "synergy", "dynamic team", "hit the ground running",
    "i am confident that i", "esteemed organization", "esteemed company",
)
_CTA = re.compile(r"\?|\b(?:call|chat|conversation|discuss|talk|connect|meet|speak|interview)\b", re.I)


def _call(provider, system: str, user: str, model_cls):
    """One structured call with one retry on malformed output. Returns a validated model or None."""
    schema = model_cls.model_json_schema()
    for attempt in range(2):
        raw = provider.generate_json(system, user if attempt == 0 else user + prompts.RETRY, schema)
        try:
            return model_cls.model_validate(extract_json(raw))
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
            continue
    return None


# ───────────── 1. input validation ─────────────


def validate_input(state: JobEmailState) -> dict:
    errors: dict[str, str] = {}
    if not state.get("ai_enabled") or not state.get("confirmed"):
        errors["ai"] = "AI is switched off or the request was not confirmed. Nothing was sent to a model."
    company = (state.get("company") or "").strip()
    role = (state.get("role") or "").strip()
    jd = (state.get("job_description") or "").strip()
    recruiter = (state.get("recruiter_name") or "").strip()
    instructions = (state.get("instructions") or "").strip()
    if not company:
        errors["company"] = "Enter the company name."
    elif len(company) > LIMITS["company"]:
        errors["company"] = f"Company name is too long (max {LIMITS['company']} characters)."
    if not role:
        errors["role"] = "Enter the job title."
    elif len(role) > LIMITS["role"]:
        errors["role"] = f"Job title is too long (max {LIMITS['role']} characters)."
    if len(jd) < LIMITS["jd_min"]:
        errors["job_description"] = "Paste the full job description (responsibilities and requirements)."
    elif len(jd) > LIMITS["jd_max"]:
        errors["job_description"] = f"The job description is too long (max {LIMITS['jd_max']:,} characters). Remove the boilerplate."
    if recruiter and (len(recruiter) > LIMITS["recruiter_name"] or not _NAME_RE.match(recruiter)):
        errors["recruiter_name"] = "Enter only the person's name (letters, spaces, . ' -)."
    if len(instructions) > LIMITS["instructions"]:
        errors["instructions"] = f"Instructions are too long (max {LIMITS['instructions']} characters)."
    resume_text = ""
    try:
        resume_text = resume_plain_text(parse_resume(state.get("resume_data") or {}))
    except ValidationError:
        errors["resume_version"] = "The selected resume could not be read. Open it in the resume studio and save it again."
    if not state.get("resume_version_id"):
        errors["resume_version"] = "Choose a resume version."
    elif not resume_text.strip() and "resume_version" not in errors:
        errors["resume_version"] = "The selected resume is empty. Add your experience in the resume studio first."
    return {"input_errors": errors, "company": company, "role": role, "job_description": jd, "recruiter_name": recruiter,
            "instructions": instructions, "revision_count": 0, "trace": ["validate_input"]}


# ───────────── 2. job description analysis ─────────────


def _supported_by(text: str, jd: str) -> bool:
    """A paraphrased line counts only if most of its meaningful words are in the JD (no invented responsibilities)."""
    words = [w for w in re.findall(r"[a-z0-9+#]{4,}", text.lower())]
    if not words:
        return False
    jd_l = jd.lower()
    return sum(w in jd_l for w in words) / len(words) >= 0.6


def analyse_job(state: JobEmailState, runtime: Runtime[GraphContext]) -> dict:
    jd = state["job_description"]
    user = (f"<company_given_by_user>\n{state['company']}\n</company_given_by_user>\n\n<role_given_by_user>\n{state['role']}\n</role_given_by_user>\n\n"
            f"<job_description>\n{jd}\n</job_description>")
    out = _call(runtime.context.provider, prompts.JD_SYSTEM, user, JDAnalysis)
    if out is None:
        raise AiOutputError("The AI could not analyse that job description. Try again, or shorten it to the responsibilities and requirements.")
    # Keep only what the JD really says: skills must appear in it, lines must mostly be its own words.
    for f in ("required_skills", "preferred_skills", "languages", "frameworks", "cloud", "databases", "tools"):
        setattr(out, f, [t.strip() for t in getattr(out, f) if t.strip() and contains(jd, t.strip())])
    for f in ("responsibilities", "experience_requirements", "qualifications"):
        setattr(out, f, [t.strip() for t in getattr(out, f) if t.strip() and _supported_by(t, jd)])
    if out.company and not contains(jd, out.company):
        out.company = ""
    if out.team_context and not _supported_by(out.team_context, jd):
        out.team_context = ""
    return {"jd_analysis": out.model_dump(), "trace": ["analyse_job"]}


# ───────────── 3. candidate profile (deterministic) ─────────────


def build_evidence(data: dict) -> list[dict]:
    """Every resume item gets a stable id so matches and claims can be traced back to the real text."""
    r = parse_resume(data)
    ev: list[dict] = []

    def add(eid: str, where: str, text: str):
        text = re.sub(r"\s+", " ", text or "").strip()
        if text:
            ev.append({"id": eid, "where": where, "text": text[:600]})

    add("HL", "Headline", r.basics.headline)
    add("SUM", "Summary", r.summary)
    for i, g in enumerate(r.skills):
        add(f"SK{i}", f"Skills · {g.category}".strip(" ·"), ", ".join(g.items))
    for i, e in enumerate(r.experience):
        where = " · ".join(x for x in (e.title, e.company) if x)
        dates = " - ".join(x for x in (e.start, "Present" if e.current else e.end) if x)
        add(f"E{i}", where, f"{e.title} at {e.company}" + (f" ({dates})" if dates else ""))
        for j, b in enumerate(e.bullets):
            add(f"E{i}.B{j}", where, b)
    for i, p in enumerate(r.projects):
        add(f"P{i}", f"Project · {p.name}", f"{p.name}" + (f" ({', '.join(p.tech)})" if p.tech else ""))
        for j, b in enumerate(p.bullets):
            add(f"P{i}.B{j}", f"Project · {p.name}", b)
    for i, e in enumerate(r.education):
        add(f"ED{i}", "Education", ", ".join(x for x in (e.degree, e.school, e.details) if x))
    for i, c in enumerate(r.certifications):
        add(f"C{i}", "Certification", " · ".join(x for x in (c.name, c.issuer, c.year) if x))
    return ev


def facts_for_prompt(evidence: list[dict]) -> str:
    out, total = [], 0
    for e in evidence:
        line = f"[{e['id']}] ({e['where']}) {e['text']}"
        if total + len(line) > MAX_FACT_CHARS:
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)


_MONTHS = {"jan": "january", "feb": "february", "mar": "march", "apr": "april", "jun": "june", "jul": "july", "aug": "august",
           "sep": "september", "sept": "september", "oct": "october", "nov": "november", "dec": "december"}


def guard_text(data: dict) -> str:
    """What the fact check treats as true about the candidate: the resume text PLUS its dates. resume_plain_text()
    leaves dates out, so a true "since January 2022" would otherwise look invented. Short month names are also
    written out ("Jan 2022" -> "january 2022")."""
    r = parse_resume(data)
    dates = [d for e in r.experience for d in (e.start, e.end)] + [d for e in r.education for d in (e.start, e.end)] + [c.year for c in r.certifications]
    expanded = [re.sub(r"\b([A-Za-z]{3,4})\b", lambda m: _MONTHS.get(m.group(1).lower(), m.group(0)), d) for d in dates if d]
    return "\n".join([resume_plain_text(r), *[d for d in dates if d], *expanded])


def analyse_candidate(state: JobEmailState) -> dict:
    evidence = build_evidence(state["resume_data"])
    return {"evidence": evidence, "candidate_facts": facts_for_prompt(evidence),
            "resume_text": guard_text(state["resume_data"]), "trace": ["analyse_candidate"]}


# ───────────── 4. experience matching ─────────────


def _rank(e: dict) -> int:
    return 0 if ".B" in e["id"] else 1 if e["id"][0] in "EP" else 2  # bullets are the best evidence, skill lists the weakest


def match_experience(state: JobEmailState, runtime: Runtime[GraphContext]) -> dict:
    jd = JDAnalysis.model_validate(state["jd_analysis"])
    evidence = state["evidence"]
    by_id = {e["id"]: e for e in evidence}
    matches: dict[str, dict] = {}

    # Deterministic: every JD skill literally present in the resume is an explicit match.
    skills = jd.all_skills()
    for skill in skills:
        hits = sorted((e for e in evidence if contains(e["text"], skill)), key=_rank)[:3]
        if hits:
            matches[skill.lower()] = {"requirement": skill, "kind": "explicit", "evidence": hits, "explanation": ""}

    user = (f"<job>\nrole: {state['role']}\nrequired skills: {', '.join(jd.required_skills) or '(none listed)'}\n"
            f"preferred skills: {', '.join(jd.preferred_skills) or '(none listed)'}\n"
            f"responsibilities:\n" + "\n".join(f"- {r}" for r in jd.responsibilities) + "\n</job>\n\n"
            f"<candidate_evidence>\n{state['candidate_facts']}\n</candidate_evidence>")
    out = _call(runtime.context.provider, prompts.MATCH_SYSTEM, user, MatchOutput)
    weak: list[str] = []
    strongest: list[str] = []
    if out is not None:
        for m in out.matches:
            req = m.requirement.strip()
            if not req:
                continue
            cited = [by_id[i.strip()] for i in m.evidence_ids if i.strip() in by_id]  # ids re-read from the real resume
            if not cited:
                weak.append(req)
                continue
            kind = "explicit" if any(contains(e["text"], req) for e in cited) else "indirect"
            key = req.lower()
            if key in matches and matches[key]["kind"] == "explicit":
                matches[key]["explanation"] = m.explanation.strip()
                continue
            matches[key] = {"requirement": req, "kind": kind, "evidence": sorted(cited, key=_rank)[:3], "explanation": m.explanation.strip()}
        strongest = [i.strip() for i in out.strongest if i.strip() in by_id][:3]
    if not strongest:  # fall back to the bullets cited most often
        counts: dict[str, int] = {}
        for m in matches.values():
            for e in m["evidence"]:
                if ".B" in e["id"]:
                    counts[e["id"]] = counts.get(e["id"], 0) + (2 if m["kind"] == "explicit" else 1)
        strongest = [i for i, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:3]

    covered = {k for k in matches}
    unmatched = [s for s in skills if s.lower() not in covered] + [w for w in weak if w.lower() not in covered]
    ordered = sorted(matches.values(), key=lambda m: (m["kind"] != "explicit", m["requirement"].lower()))[:15]
    return {"experience_matches": ordered, "strongest": strongest, "unmatched_requirements": list(dict.fromkeys(unmatched))[:15],
            "trace": ["match_experience"]}


# ───────────── 5. email generation (and the single revision) ─────────────


def _email_prompt(state: JobEmailState, feedback: list[str] | None = None) -> str:
    jd = JDAnalysis.model_validate(state["jd_analysis"])
    by_id = {e["id"]: e for e in state["evidence"]}
    lines = []
    for m in state["experience_matches"]:
        ev = "; ".join(f"[{e['id']}] {e['text']}" for e in m["evidence"])
        lines.append(f"- {m['requirement']} ({m['kind']}): {ev}")
    strongest = "\n".join(f"- [{i}] ({by_id[i]['where']}) {by_id[i]['text']}" for i in state.get("strongest", []) if i in by_id)
    roles = "\n".join(f"- [{e['id']}] {e['text']}" for e in state["evidence"] if re.fullmatch(r"[EP]\d+|HL|ED\d+", e["id"]))
    user = (
        f"<job>\ncompany: {state['company']}\nrole: {state['role']}\n"
        f"responsibilities:\n" + "\n".join(f"- {r}" for r in jd.responsibilities[:8]) + "\n"
        f"required skills: {', '.join(jd.required_skills) or '(none listed)'}\n"
        f"team context: {jd.team_context or '(not stated)'}\n</job>\n\n"
        f"<recruiter_name>\n{state.get('recruiter_name') or '(unknown)'}\n</recruiter_name>\n\n"
        f"<sender_name>\n{state['sender_name']}\n</sender_name>\n\n"
        f"<verified_evidence>\nroles, projects and education:\n{roles or '(none)'}\n\n"
        f"strongest evidence for this job:\n{strongest or '(none)'}\n\nrequirement matches:\n" + ("\n".join(lines) or "(none)") +
        "\n</verified_evidence>\n\n"
        f"<not_evidenced>\n{', '.join(state.get('unmatched_requirements') or []) or '(none)'}\n</not_evidenced>\n\n"
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


def ensure_signoff(paragraphs: list[str], sender_name: str) -> list[str]:
    """Small local models often forget the sign-off. Adding the user's own name is not a claim, so it is done in code
    (instead of blocking the draft, or asking the model again)."""
    first = (sender_name or "").split()[:1]
    if not first or any(first[0].lower() in p.lower() for p in paragraphs[-1:]):
        return paragraphs
    return [*paragraphs, f"Best regards,\n{sender_name}"]


def _draft_update(out: GeneratedEmail, sender_name: str = "") -> dict:
    paragraphs = ensure_signoff([p for p in (x.strip() for x in out.paragraphs) if p], sender_name)
    return {"email_subject": out.subject.strip(), "email_paragraphs": paragraphs,
            "email_rationale": out.rationale.strip(), "email_evidence_ids": [i.strip() for i in out.evidence_ids if i.strip()]}


def generate_email(state: JobEmailState, runtime: Runtime[GraphContext]) -> dict:
    out = _call(runtime.context.provider, _system(), _email_prompt(state), GeneratedEmail)
    if out is None:
        raise AiOutputError("The AI reply could not be understood. Nothing was saved; try again.")
    return {**_draft_update(out, state["sender_name"]), "trace": ["generate_email"]}


def revise_email(state: JobEmailState, runtime: Runtime[GraphContext]) -> dict:
    """The single permitted revision. If the model fails here, the previous draft stays (and is re-validated)."""
    fb = state.get("email_quality_feedback") or {}
    feedback = list(state.get("validation_errors") or []) + list(fb.get("issues") or [])
    if fb.get("revision_instructions"):
        feedback.append(fb["revision_instructions"])
    out = _call(runtime.context.provider, _system(), _email_prompt(state, feedback[:10] or ["Make it more specific to the job."]), GeneratedEmail)
    update = {"revision_count": state.get("revision_count", 0) + 1, "trace": ["revise_email"]}
    return {**update, **_draft_update(out, state["sender_name"])} if out is not None else update


# ───────────── 6. quality review ─────────────


def deterministic_quality(subject: str, paragraphs: list[str]) -> list[str]:
    body = "\n".join(paragraphs)
    words = len(body.split())
    issues = []
    if words > 200:
        issues.append(f"Too long ({words} words); aim for 100-160.")
    elif words < 60:
        issues.append(f"Too short ({words} words) to show why you fit; aim for 100-160.")
    low = body.lower()
    generic = [g for g in _GENERIC if g in low]
    if generic:
        issues.append("Generic phrasing: " + ", ".join(f"“{g}”" for g in generic[:3]) + ".")
    if not _CTA.search(body):
        issues.append("No clear call to action (for example, asking for a short call).")
    if len(subject) > 90:
        issues.append("Subject line is long; keep it under about 70 characters.")
    return issues


def review_email(state: JobEmailState, runtime: Runtime[GraphContext]) -> dict:
    """Quality feedback only. A score here is never treated as proof that the email is factually correct."""
    paragraphs = state["email_paragraphs"]
    issues = deterministic_quality(state["email_subject"], paragraphs)
    user = (f"<job>\nrole: {state['role']} at {state['company']}\n"
            + "\n".join(f"- {r}" for r in JDAnalysis.model_validate(state["jd_analysis"]).responsibilities[:8]) + "\n</job>\n\n"
            f"<verified_evidence>\n{state['candidate_facts']}\n</verified_evidence>\n\n"
            f"<email>\nSubject: {state['email_subject']}\n\n" + "\n\n".join(paragraphs) + "\n</email>")
    out = _call(runtime.context.provider, prompts.REVIEW_SYSTEM, user, QualityReview)
    fb: dict = {"available": out is not None, "issues": issues}
    if out is not None:
        fb.update(out.model_dump())
        fb["issues"] = issues + [f"Generic phrasing: “{g}”." for g in out.generic_phrases[:3]] + \
            ([] if out.cta_clear else ["The reviewer found no clear call to action."])
        weak = out.relevance <= 2 or out.specificity <= 2
        fb["needs_revision"] = bool(out.needs_revision or weak or issues)
    else:
        fb["needs_revision"] = bool(issues)
    fb["issues"] = list(dict.fromkeys(fb["issues"]))
    return {"email_quality_feedback": fb, "trace": ["review_email"]}


# ───────────── 7. fabrication validation ─────────────


def validate_fabrication(state: JobEmailState) -> dict:
    jd = JDAnalysis.model_validate(state["jd_analysis"])
    try:
        v = validate_email(
            subject=state["email_subject"], paragraphs=state["email_paragraphs"], resume_text=state["resume_text"],
            jd_text=state["job_description"], jd_skills=jd.all_skills(), company=state["company"], role=state["role"],
            recruiter_name=state.get("recruiter_name", ""), sender_name=state["sender_name"],
            evidence_ids=state.get("email_evidence_ids", []), known_ids={e["id"] for e in state["evidence"]},
        )
    except AiOutputError:
        v = {"subject": "", "body": "", "status": "blocked", "errors": ["The AI returned an empty email."], "warnings": []}
    return {"validation_status": v["status"], "validation_errors": v["errors"], "validation_warnings": v["warnings"],
            "final_subject": v["subject"], "final_body": v["body"], "trace": ["validate_fabrication"]}


# ───────────── routing ─────────────


def route_after_input(state: JobEmailState) -> str:
    return "invalid" if state.get("input_errors") else "ok"


def route_after_validation(state: JobEmailState) -> str:
    """Bounded: at most MAX_REVISIONS revisions, whatever the review or the guard says."""
    if state.get("revision_count", 0) >= MAX_REVISIONS:
        return "finalize"
    needs = state.get("validation_status") == "blocked" or (state.get("email_quality_feedback") or {}).get("needs_revision")
    return "revise" if needs else "finalize"


# ───────────── 8. finalize ─────────────


def finalize(state: JobEmailState) -> dict:
    status = state["validation_status"]
    ready = status != "blocked"  # a blocked draft is never presented as an email to send
    by_id = {e["id"]: e for e in state["evidence"]}
    fb = dict(state.get("email_quality_feedback") or {})
    if state.get("revision_count"):
        # the review ran on the first draft; re-check the cheap deterministic points on the final one
        fb["issues"] = deterministic_quality(state["email_subject"], state["email_paragraphs"])
        fb["note"] = "Revised once based on this review."
    used = [by_id[i] for i in state.get("email_evidence_ids", []) if i in by_id]
    result = {
        "ready": ready,
        "validation_status": status,
        "subject": state["final_subject"] if ready else "",
        "body": state["final_body"] if ready else "",
        "company": state["company"],
        "role": state["role"],
        "resume_version": {"id": state["resume_version_id"], "label": state.get("resume_label", "")},
        "matched_skills": [m["requirement"] for m in state["experience_matches"] if m["kind"] == "explicit"],
        "relevant_experience": [by_id[i] for i in state.get("strongest", []) if i in by_id],
        "matches": state["experience_matches"],
        "missing_skills": state.get("unmatched_requirements", []),
        "warnings": state.get("validation_warnings", []),
        "errors": state.get("validation_errors", []),
        "quality": fb,
        "revision_count": state.get("revision_count", 0),
        "evidence": used,
        "rationale": state.get("email_rationale", ""),
    }
    return {"final_result": result, "trace": ["finalize"]}
