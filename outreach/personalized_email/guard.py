"""Fabrication guard for job application emails. Runs in code, independently of the model.

Layers:
1. vet_email_draft (outreach/ai_writer.py): invented numbers, unknown placeholders, blanks, unknown terms.
2. check_recipient_specific (outreach/personalise.py): wrong greeting name, missing sign-off, invented
   relationships/referrals and company news.
3. First-person claim checks (here). vet_email_draft allows any word from the job description, because talking
   about the JOB is fine. But "I have Kubernetes experience" is a claim about the CANDIDATE, so every sentence
   about the candidate is checked against the selected resume ONLY: skills, numbers, years, degrees, names.
The model's own evidence references are never trusted: unknown ids are reported, and claims are verified anyway.

Outcome: ok | warning (needs the user's attention) | blocked (never presented as ready to send).
"""

import re

from resumes.ai.ats import contains, dictionary_terms
from resumes.ai.guard import numbers_in

from ..ai_writer import EmailDraft, _new_terms, vet_email_draft
from ..personalise import check_recipient_specific
from ..render import first_name, render_template
from ..richtext import html_to_text

_FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|I'd|I'll|my|me|myself|mine)\b", re.I)
_CLAIM = re.compile(
    r"\b(?:experience[ds]?|built|build(?:ing)?|developed|develop(?:ing)?|designed|design(?:ing)?|used|using|use|worked|working|"
    r"proficient|skilled|expert(?:ise)?|familiar|shipped|deployed|deploying|led|lead(?:ing)?|implemented|implementing|"
    r"know|knowledge|background|speciali[sz]e[ds]?|hands-on|years?|certified|maintain(?:ed|ing)?|architected|wrote|written|created)\b",
    re.I,
)
_LEARNING = re.compile(r"\b(?:learn(?:ing)?|pick(?:ing)? up|ramp(?:ing)? up|get hands-on|eager to grow|keen to grow|new to)\b", re.I)
_WORD_NUM = r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|several|many|multiple|a few|numerous"
_YEARS = re.compile(rf"\b(?:\d+|{_WORD_NUM})\+?\s*(?:years?|yrs?)\b", re.I)
_DEGREE = re.compile(r"\b(bachelor'?s?|master'?s?|ph\.?d|doctorate|mba|b\.?\s?tech|m\.?\s?tech|b\.?sc|m\.?sc|b\.?e\.|m\.?s\.)", re.I)
_HYPE = re.compile(r"\b(?:world-class|seasoned|rockstar|ninja|guru|unparalleled|extensive experience|deep expertise|mastery|expert in)\b", re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _about_candidate(sentence: str) -> bool:
    return bool(_FIRST_PERSON.search(sentence))


def check_candidate_claims(text: str, *, resume_text: str, jd_text: str, jd_skills: list[str], allowed_names: str) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings) for sentences that make claims about the candidate."""
    resume_l = resume_text.lower()
    resume_numbers = set(numbers_in(resume_text))
    candidate_src = (resume_text + "\n" + allowed_names).lower()
    errors: dict[str, None] = {}
    warnings: dict[str, None] = {}
    skills = list(dict.fromkeys([*jd_skills, *(k["term"] for k in dictionary_terms(jd_text))]))

    for s in _sentences(text):
        if not _about_candidate(s):
            continue
        claim = bool(_CLAIM.search(s))
        learning = bool(_LEARNING.search(s))

        for n in numbers_in(s) if claim else []:  # "a 15-minute call" is not a claim about the candidate
            if n not in resume_numbers:
                errors[f"Uses the figure “{n}” about you, but it is not in your resume."] = None
        for m in _YEARS.finditer(s):
            if m.group(0).lower() not in resume_l:
                errors[f"Claims “{m.group(0)}” of experience, which your resume does not state."] = None
        for m in _DEGREE.finditer(s):
            if m.group(1).lower().rstrip(".") not in resume_l:
                errors[f"Mentions a degree (“{m.group(1)}”) that is not in your resume."] = None

        for skill in [k for k in skills + [t["term"] for t in dictionary_terms(s)] if contains(s, k)]:
            if contains(resume_text, skill):
                continue
            if learning:
                continue  # "keen to learn Kubernetes" is honest, and allowed by the prompt
            if claim:
                errors[f"Claims experience with {skill}, which your resume does not show."] = None
            else:
                warnings[f"Mentions {skill} near a statement about you; your resume does not show it. Check the wording."] = None

        # Proper nouns / technical terms about the candidate that appear in neither the resume nor the job:
        # an invented employer, project, title or tool.
        for t in _new_terms(s, candidate_src):
            t = re.sub(r"['’]s$", "", t)  # "Google's" -> "google"
            if re.fullmatch(r"[\d.,%+]+", t) or t in candidate_src or t in jd_text.lower():  # numbers: checked above
                continue  # a word from the job itself (product, team): fine unless it is a skill (checked above)
            errors[f"Mentions “{t}” about you, which is not in your resume."] = None

        for m in _HYPE.finditer(s):
            if m.group(0).lower() not in resume_l:
                warnings[f"Strong self-description “{m.group(0)}” is not backed by your resume. Consider toning it down."] = None
    return list(errors), list(warnings)


def validate_email(*, subject: str, paragraphs: list[str], resume_text: str, jd_text: str, jd_skills: list[str], company: str,
                   role: str, recruiter_name: str, sender_name: str, evidence_ids: list[str], known_ids: set[str],
                   has_job_description: bool = True, allow_company_news: bool = False) -> dict:
    """Full validation of one draft. Returns subject/body (placeholders filled), status, errors, warnings."""
    allowed_names = "\n".join([company, role, recruiter_name, sender_name])
    sources = "\n".join([resume_text, jd_text, allowed_names])
    errors: list[str] = []
    warnings: list[str] = []

    vetted = vet_email_draft(EmailDraft(subject=subject[:200], paragraphs=paragraphs[:8]), sources)  # raises AiOutputError if empty
    # vet_email_draft: invented numbers block; unknown placeholders and [blanks] block here too (a ready-to-copy
    # email must be complete); unknown terms stay warnings (the first-person checks below decide about claims).
    for issue in vetted["issues"]:
        if issue.startswith("Mentions things not found"):  # "Amazon's" is "Amazon": drop terms whose possessive stem is a known word
            head, _, tail = issue.partition(": ")
            terms, sep, rest = tail.partition(". ")
            kept = [t for t in terms.split(", ") if re.sub(r"['’]s$", "", t.strip()).lower() not in sources.lower()]
            if not kept:
                continue
            issue = f"{head}: {', '.join(kept)}{sep}{rest}"
        fatal = issue.startswith(("Contains figures", "Uses placeholders", "Has blanks"))
        (errors if fatal else warnings).append(issue)

    values = {"name": recruiter_name, "first_name": first_name(recruiter_name), "company": company, "role": role, "sender_name": sender_name}
    subject_out = re.sub(r"[\r\n]+", " ", render_template(vetted["subject"], values).output).strip()
    body_html = render_template(vetted["body_html"], values, as_html=True).output
    body_text = html_to_text(body_html)

    errors += [f"The email {p}." for p in check_recipient_specific(subject_out, body_html, recipient={"name": recruiter_name},
                                                                   sender_name=sender_name, has_job_description=has_job_description, allow_company_news=allow_company_news)]
    e2, w2 = check_candidate_claims(subject_out + "\n" + body_text, resume_text=resume_text, jd_text=jd_text,
                                    jd_skills=jd_skills, allowed_names=allowed_names)
    errors += e2
    warnings += w2
    bad_refs = [i for i in evidence_ids if i not in known_ids]
    if bad_refs:
        warnings.append(f"The AI cited resume items that do not exist ({', '.join(bad_refs[:4])}); they were ignored.")

    errors = list(dict.fromkeys(errors))
    warnings = [w for w in dict.fromkeys(warnings) if w not in errors]
    status = "blocked" if errors else "warning" if warnings else "ok"
    return {"subject": subject_out, "body": body_text, "body_html": body_html, "status": status, "errors": errors, "warnings": warnings}
