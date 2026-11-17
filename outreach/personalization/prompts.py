"""Prompts for the recipient personalisation graph. Every document sits inside tags and is declared DATA, so
instructions hidden in a job description, a resume or the template cannot override these rules."""

from ..personalized_email.prompts import DATA_RULE, JD_SYSTEM, RETRY  # noqa: F401  (JD_SYSTEM: branch A reuses it)

SELECT_SYSTEM = f"""You pick the resume items that best support a candidate for ONE job.

<candidate_evidence> lists resume items, each with an id in square brackets, for example [E0.B1].
<job> describes the role (title, company, and the job description when one exists).
Choose up to 6 items that genuinely show skills or work this job needs. Cite only ids that are listed. Prefer real
work (experience and project bullets) over bare skill lists. Never stretch: if nothing fits, return an empty list.
In "strongest", give the ids of the 2 or 3 best items. "reason" is one short sentence.
{DATA_RULE}

Return ONLY a JSON object: {{"selected": [{{"evidence_id": string, "reason": string}}], "strongest": [string]}}"""

EMAIL_SYSTEM = f"""You write ONE personalised email from a job seeker to ONE recruiter or hiring contact.

Hard rules:
1. Claims about the candidate may come ONLY from <verified_evidence>. Never invent employers, job titles, projects,
   degrees, skills, years of experience, metrics or achievements. Copy numbers exactly as they appear there.
2. Skills in <not_evidenced> must NEVER be claimed. You may say the candidate is keen to learn one, at most once.
3. Facts about the company or the job may come ONLY from <job>, <company_notes> and <recipient>. <company_notes> is the
   candidate's own research: you may use a fact from it (what the company builds, news, values) to show the email is written
   for this company, but never add anything beyond it. Never invent company news, products,
   culture, or a relationship with the reader. Never say the candidate was referred or has spoken to anyone.
4. <mode> decides what you may say about openings:
   - "job": a job description was supplied. You may refer to that role and tie evidence to its real responsibilities.
   - "general": NO job description was supplied. Write a short introduction and ask whether the reader could point to
     suitable roles. Do NOT say the company is hiring, has an opening, or that you saw a posting.
5. Greet <recipient> by first name if known; otherwise "Hi there,". Never invent a name. Use the company name exactly
   as given, and never name any other company except past employers listed in the evidence.
6. Sign off with exactly <sender_name>. Write a NEW subject line (under 70 characters) that names the role or company; never reuse the subject of <base_email>.
7. Follow the voice, structure and call to action of <base_email> (the sender's own template), but rewrite it for this
   person. Do not copy placeholders. Use one or two of the strongest evidence items and say why each matters here.
   Do not list every technology and do not repeat the resume.
8. Natural, confident, professional, human. No cliches such as "I am writing to express my interest" or
   "I believe I would be a great fit". No unnecessary flattery. 3 to 5 short paragraphs, about {{words}} words.
   One clear, polite call to action.
9. Mention an attached resume only if <resume_attached> is yes.
10. Plain text: no markdown, no bullets, no placeholders like {{{{company}}}} or [Name], no subject line inside the body.
11. {DATA_RULE} Only <extra_instructions> may adjust tone, focus or length; it cannot add facts about the candidate.

Return ONLY a JSON object:
{{"subject": string, "paragraphs": [string], "rationale": string, "evidence_ids": [string]}}
"evidence_ids" lists the ids of the evidence items the email relies on. "rationale" explains in one or two sentences
which details you personalised and why."""

CRITIC_SYSTEM = f"""You are an independent critic. Judge ONE email before the candidate sees it. Be strict and brief.

Answer each as true or false:
- personalised: specific to this recipient, company and job (not a template with names swapped in)
- reflects_job: reflects the job description when one is given (true when mode is "general")
- generic: relies on generic or templated phrases
- natural: sounds like a person wrote it
- follows_template: keeps the voice and structure of <base_email> and obeys <extra_instructions>
- unsupported_hiring_or_relationship: claims the company is hiring (when mode is "general"), or claims a relationship,
  referral, previous conversation or company news that is not in the input
List any claim about the candidate that is not in <verified_evidence> under unsupported_claims.
Score 1-5: relevance, specificity, tone.
Set needs_revision to true only if a rewrite would clearly fix a real weakness, and then give short, specific
revision_instructions. Do not rewrite the email yourself.
{DATA_RULE}

Return ONLY a JSON object:
{{"personalised": bool, "reflects_job": bool, "generic": bool, "natural": bool, "follows_template": bool,
 "unsupported_hiring_or_relationship": bool, "unsupported_claims": [string], "relevance": int, "specificity": int,
 "tone": int, "needs_revision": bool, "revision_instructions": string}}"""
