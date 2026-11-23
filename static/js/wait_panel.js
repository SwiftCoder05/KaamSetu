// "While you wait" panel for slow AI calls: estimated step tracker, elapsed time, and rotating
// job-hunt tips / myth-or-fact cards. Usage: const w = window.kaamWait.start(el, "email"); ... w.stop();
(function () {
  // Steps mirror the real pipelines; timings (seconds) are typical, not live progress.
  const STEPS = {
    email: [
      [0, "Reading the job post"],
      [8, "Finding your best-matching experience"],
      [18, "Writing the draft"],
      [38, "Reviewing tone and clarity"],
      [52, "Fact-checking every claim against your resume"],
    ],
    resume: [
      [0, "Reading the job description"],
      [6, "Picking your most relevant experience"],
      [15, "Drafting suggestions with the model"],
      [110, "Fact-checking each suggestion"],
      [140, "Scoring your ATS match"],
    ],
  };

  const T = (tag, text, kinds) => ({ type: "tip", tag, text, kinds });
  const Q = (claim, fact, why, kinds) => ({ type: "quiz", tag: "Myth or fact?", claim, fact, why, kinds });
  const BOTH = ["email", "resume"];
  const CARDS = [
    T("Resume tip", "Start each bullet with a strong verb and end with the result: “Cut page load time by 40% by caching API responses.”", ["resume"]),
    T("Resume tip", "Use the posting's exact wording for skills you really have. If it says “PostgreSQL”, don't only write “Postgres”.", ["resume"]),
    T("Resume tip", "Put your most relevant experience in the top third of the page. That is where a skim starts, and often ends.", ["resume"]),
    T("Resume tip", "Plain section headings (Experience, Education, Skills) are the ones resume parsers recognise most reliably.", ["resume"]),
    T("Resume tip", "Spell an acronym out once, e.g. “Search Engine Optimization (SEO)”, so a search for either form finds you.", ["resume"]),
    T("Resume tip", "Keep one master resume and save a tailored copy per job. That is exactly what versions in the resume studio are for.", ["resume"]),
    T("Resume tip", "Avoid putting key details in tables, text boxes or page headers. Some parsers skip them entirely.", ["resume"]),
    T("Email tip", "Name the role in the subject line and add one hook: “Backend Engineer application: payments API experience”.", ["email"]),
    T("Email tip", "The first two lines decide whether the rest gets read. Say which role you want and your strongest match, straight away.", ["email"]),
    T("Email tip", "End with one clear, small ask, like “Would you be open to a 15-minute call next week?”", ["email"]),
    T("Email tip", "Mention one specific thing about the company, such as a product, launch or blog post. It shows this isn't a mass email.", ["email"]),
    T("Email tip", "Short wins. Aim for under about 150 words and let your attached resume carry the detail.", ["email"]),
    T("Email tip", "Send during the recruiter's working hours, so your email lands near the top of their inbox instead of under an overnight pile.", ["email"]),
    T("Follow-up", "No reply? One polite follow-up after about a week is normal. Add something new, like a relevant project, rather than just “bumping”.", BOTH),
    T("Interview prep", "Prepare three STAR stories (Situation, Task, Action, Result). Between them they cover most behavioural questions.", BOTH),
    T("Interview prep", "Check the typical salary range for the role before the first call, so you are never caught off guard by the question.", BOTH),
    T("Networking", "When reaching out to someone at the company, ask for a short conversation first, not a referral. Referrals tend to follow good conversations.", BOTH),
    T("Did you know?", "“KaamSetu” means “work bridge”: kaam (work) + setu (bridge).", BOTH),
    T("Did you know?", "“Résumé” is French for “summary”, and “curriculum vitae” is Latin for “course of life”.", BOTH),
    T("Did you know?", "Around 1482, Leonardo da Vinci wrote a letter to the Duke of Milan listing what he could build. It is often called one of the first résumés.", BOTH),
    Q("Applicant tracking systems can't read PDF resumes.", false, "Most modern ATS read text-based PDFs fine. The real problem is scanned or image-only PDFs, which contain no text to read.", ["resume"]),
    Q("Pasting the job description into your resume in white text beats the ATS.", false, "Parsers extract all text, so recruiters see the stuffed keywords in plain view. It is a fast way to get rejected.", ["resume"]),
    Q("Numbers make resume bullets more convincing.", true, "“Reduced build time from 12 to 4 minutes” is more concrete than “improved build speed”. Only use numbers you can back up.", ["resume"]),
    Q("It's fine to list a skill you've only read about if the job asks for it.", false, "Interviewers probe listed skills. That is why KaamSetu blocks suggestions your resume can't support.", BOTH),
    Q("Recruiters read every word of an application email.", false, "Most skim. Put the role and your strongest match in the first two lines.", ["email"]),
    Q("Using the exact job title from the posting can help recruiters find you.", true, "Recruiters often search by title and keywords, so matching their wording, honestly, helps you show up.", BOTH),
    Q("Following up once after about a week looks pushy.", false, "A single, polite follow-up is normal and often welcome. Several in a row is what feels pushy.", ["email"]),
  ];

  const KEY = "kaam.waitPanel.seen";
  const loadSeen = () => { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch { return []; } };
  const saveSeen = (ids) => { try { localStorage.setItem(KEY, JSON.stringify(ids.slice(-40))); } catch { /* storage unavailable */ } };

  function deck(kind) {
    const pool = CARDS.map((c, id) => ({ ...c, id })).filter((c) => c.kinds.includes(kind));
    for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [pool[i], pool[j]] = [pool[j], pool[i]]; }
    const seen = new Set(loadSeen());
    return [...pool.filter((c) => !seen.has(c.id)), ...pool.filter((c) => seen.has(c.id))];
  }

  const h = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  };
  const fmt = (s) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`);

  function start(el, kind) {
    if (!el) return { stop() {} };
    const steps = STEPS[kind] || STEPS.email;
    const cards = deck(kind);
    let elapsed = 0, idx = 0, sinceTurn = 0, paused = false;

    el.replaceChildren();
    el.classList.add("wait-panel");
    el.setAttribute("aria-live", "off");

    const head = h("div", "wait-head");
    const time = h("span", "wait-time", "0s");
    head.append(h("strong", null, "Working on it"), time);

    const list = h("ol", "wait-steps");
    const items = steps.map(([, label]) => { const li = h("li", null, label); list.append(li); return li; });
    const note = h("p", "tiny muted wait-note", "Steps are estimated from typical timings. Your result appears here as soon as it's ready.");

    const card = h("div", "wait-card");
    const cardTop = h("div", "wait-card-top");
    const tag = h("span", "wait-tag");
    const nav = h("div", "wait-nav");
    const prev = h("button", "wait-arrow", "‹");
    const count = h("span", "tiny muted");
    const next = h("button", "wait-arrow", "›");
    prev.type = next.type = "button";
    prev.setAttribute("aria-label", "Previous tip");
    next.setAttribute("aria-label", "Next tip");
    nav.append(prev, count, next);
    cardTop.append(tag, nav);
    const body = h("div", "wait-body");
    card.append(cardTop, body);
    el.append(head, list, note, h("div", "tiny muted wait-label", "While you wait"), card);

    function paintSteps() {
      let cur = 0;
      steps.forEach(([at], i) => { if (elapsed >= at) cur = i; });
      items.forEach((li, i) => { li.className = i < cur ? "done" : i === cur ? "now" : ""; });
    }

    function show(i) {
      idx = (i + cards.length) % cards.length;
      sinceTurn = 0;
      const c = cards[idx];
      saveSeen([...loadSeen().filter((x) => x !== c.id), c.id]);
      tag.textContent = c.tag;
      tag.dataset.type = c.type;
      count.textContent = `${idx + 1} / ${cards.length}`;
      body.replaceChildren();
      body.classList.remove("fade-in");
      void body.offsetWidth;
      body.classList.add("fade-in");
      if (c.type === "tip") { body.append(h("p", null, c.text)); return; }

      const claim = h("p", "wait-claim", `“${c.claim}”`);
      const choices = h("div", "wait-choices");
      const answer = h("p", "small wait-answer");
      answer.hidden = true;
      for (const [label, val] of [["Myth", false], ["Fact", true]]) {
        const b = h("button", "btn sm", label);
        b.type = "button";
        b.addEventListener("click", () => {
          choices.querySelectorAll("button").forEach((x) => (x.disabled = true));
          const right = val === c.fact;
          b.classList.add(right ? "right" : "wrong");
          answer.replaceChildren(h("strong", null, `${right ? "Correct!" : "Not quite."} It's a ${c.fact ? "fact" : "myth"}. `), document.createTextNode(c.why));
          answer.hidden = false;
          sinceTurn = -8; // give time to read the answer
        });
        choices.append(b);
      }
      body.append(claim, choices, answer);
    }

    prev.addEventListener("click", () => show(idx - 1));
    next.addEventListener("click", () => show(idx + 1));
    card.addEventListener("mouseenter", () => (paused = true));
    card.addEventListener("mouseleave", () => (paused = false));
    card.addEventListener("focusin", () => (paused = true));
    card.addEventListener("focusout", () => (paused = false));

    paintSteps();
    show(0);
    const timer = setInterval(() => {
      elapsed++;
      time.textContent = fmt(elapsed);
      paintSteps();
      if (!paused && ++sinceTurn >= (cards[idx].type === "quiz" ? 16 : 10)) show(idx + 1);
    }, 1000);

    return { stop() { clearInterval(timer); el.replaceChildren(); } };
  }

  window.kaamWait = { start };
})();
