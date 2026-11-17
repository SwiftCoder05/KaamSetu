// Job application email: collect inputs, call the generator once per click, show the reviewed draft.
// All server text is inserted with textContent (never innerHTML). Nothing here sends mail.
(function () {
  const root = document.getElementById("job-email");
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const form = $("job-form");
  const btn = $("generate-btn");
  const fields = ["company", "role", "job_description", "recruiter_name", "recruiter_email", "job_url", "resume_version", "instructions"];
  const tones = { ok: "ok", warning: "warn", blocked: "bad" };
  const labels = { ok: "Fact check passed", warning: "Check the warnings", blocked: "Blocked" };
  let busy = false;

  const jd = $("job_description");
  const count = () => ($("jd-count").textContent = `${jd.value.length.toLocaleString()} / 12,000`);
  jd.addEventListener("input", count);
  count();

  function show(which) {
    for (const id of ["result-empty", "result-loading", "result-body"]) $(id).hidden = id !== which;
  }
  function clearErrors() {
    document.querySelectorAll("[data-err]").forEach((el) => (el.textContent = ""));
  }
  function li(text) {
    const el = document.createElement("li");
    el.textContent = text;
    return el;
  }
  function chips(container, items, cls) {
    container.replaceChildren(
      ...(items.length ? items : ["none"]).map((t) => {
        const s = document.createElement("span");
        s.className = items.length ? `kw ${cls}` : "tiny muted";
        s.textContent = t;
        return s;
      }),
    );
  }

  function render(d) {
    const badge = $("status-badge");
    badge.className = `badge dot ${tones[d.validation_status] || ""}`;
    badge.textContent = labels[d.validation_status] || d.validation_status;

    const box = $("status-box");
    box.replaceChildren();
    const list = (title, items, tone) => {
      if (!items.length) return;
      const a = document.createElement("div");
      a.className = `alert ${tone} small`;
      const inner = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = title;
      const ul = document.createElement("ul");
      ul.style.cssText = "margin:.3rem 0 0;padding-left:1.1rem";
      ul.append(...items.map(li));
      inner.append(strong, ul);
      a.append(inner);
      box.append(a);
    };
    if (!d.ready) list("This draft was blocked because it made claims your resume does not support. Regenerate, or adjust your instructions:", d.errors, "bad");
    list("Please check before sending:", d.warnings, "warn");

    $("draft").hidden = !d.ready;
    $("out-subject").value = d.subject || "";
    $("out-body").value = d.body || "";
    const mail = $("mailto-btn");
    mail.hidden = !(d.ready && d.recruiter_email);
    $("out-rationale").textContent = d.rationale || "";
    $("out-evidence").replaceChildren(...(d.evidence.length ? d.evidence : d.relevant_experience).map((e) => li(`${e.where}: ${e.text}`)));
    chips($("out-matched"), d.matched_skills, "matched");
    chips($("out-missing"), d.missing_skills, "missing");

    const q = d.quality || {};
    const qb = $("out-quality");
    qb.replaceChildren();
    if (q.available) {
      const p = document.createElement("p");
      p.textContent = `Relevance ${q.relevance}/5 · Specificity ${q.specificity}/5 · Tone ${q.tone}/5 · Clarity ${q.clarity}/5 · Conciseness ${q.conciseness}/5. These scores are the AI's opinion, not a fact check.`;
      qb.append(p);
    } else {
      qb.append(Object.assign(document.createElement("p"), { textContent: "The AI review was unavailable; only automatic checks ran." }));
    }
    if (q.issues?.length) {
      const ul = document.createElement("ul");
      ul.style.cssText = "margin:.3rem 0;padding-left:1.1rem";
      ul.append(...q.issues.map(li));
      qb.append(ul);
    }
    qb.append(Object.assign(document.createElement("p"), { className: "tiny muted", textContent: `Revisions: ${d.revision_count} of 1 · model ${d.model || ""}` }));
    root.dataset.recruiterEmail = d.recruiter_email || "";
    updateMailto();
    show("result-body");
  }

  function updateMailto() {
    const to = root.dataset.recruiterEmail;
    if (!to) return;
    $("mailto-btn").href = `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent($("out-subject").value)}&body=${encodeURIComponent($("out-body").value)}`;
  }
  $("out-subject").addEventListener("input", updateMailto);
  $("out-body").addEventListener("input", updateMailto);

  async function generate() {
    if (busy) return;
    clearErrors();
    const payload = { confirm: true, resume_attached: $("resume_attached").checked };
    for (const f of fields) payload[f] = $(f).value.trim();
    busy = true;
    btn.disabled = true;
    $("regen-btn").disabled = true;
    show("result-loading");
    let res;
    try {
      res = await window.kaam.post(root.dataset.url, payload);
    } catch {
      res = { ok: false, data: { error: "Network error. Check your connection and try again." } };
    }
    busy = false;
    btn.disabled = false;
    $("regen-btn").disabled = false;
    if (!res.ok) {
      for (const [k, v] of Object.entries(res.data.errors || {})) {
        const el = document.querySelector(`[data-err="${k}"]`);
        if (el) el.textContent = v;
      }
      show($("out-body").value ? "result-body" : "result-empty");
      window.kaam.toast(res.data.error || "Something went wrong.", "bad", 7000);
      return;
    }
    render(res.data);
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    generate();
  });
  $("regen-btn").addEventListener("click", generate);
  $("edit-input-btn").addEventListener("click", () => {
    form.scrollIntoView({ behavior: "smooth", block: "start" });
    $("company").focus({ preventScroll: true });
  });
  $("copy-btn").addEventListener("click", async () => {
    const text = `Subject: ${$("out-subject").value}\n\n${$("out-body").value}`;
    try {
      await navigator.clipboard.writeText(text);
      window.kaam.toast("Copied. Paste it into your email.", "ok", 2500);
    } catch {
      $("out-body").select();
      window.kaam.toast("Copy blocked by the browser: the text is selected, press Ctrl+C.", "warn", 4000);
    }
  });
})();
