// Campaign page 2: per-recipient company and job details. Saves to the server; never calls AI itself.
(function () {
  const form = document.getElementById("ctx-form");
  if (!form) return;
  const cards = () => [...form.querySelectorAll(".rcard")];
  const shared = document.getElementById("shared-toggle");
  const sharedText = document.getElementById("shared-jd");
  const saveState = document.getElementById("save-state");
  const goBtn = document.getElementById("go-btn");
  const get = (card, f) => card.querySelector(`[data-f="${f}"]`);
  let dirty = false;

  function readiness() {
    const counts = { job: 0, notes: 0, general: 0, missing: 0 };
    for (const card of cards()) {
      const hasJd = shared.checked ? sharedText.value.trim() : get(card, "job_description").value.trim();
      const hasCompany = get(card, "company").value.trim();
      const hasNotes = get(card, "company_notes").value.trim();
      const kind = hasJd ? "job" : hasNotes ? "notes" : hasCompany ? "general" : "missing";
      counts[kind]++;
      const chip = card.querySelector("[data-chip]");
      chip.textContent = { job: "Job-specific", notes: "Company-aware", general: "General introduction", missing: "Needs a company" }[kind];
      chip.className = `badge ${{ job: "ok", notes: "ok", general: "info", missing: "warn" }[kind]}`;
      const name = get(card, "name").value.trim();
      card.querySelector("[data-title-name]").textContent = name || "Add a name";
      card.querySelector("[data-greet]").textContent = name || "there";
      card.querySelector("[data-sum-company]").textContent = get(card, "company").value.trim() || "No company yet";
      const title = get(card, "job_title").value.trim();
      card.querySelector("[data-sum-title]").textContent = title ? `· ${title}` : "";
      card.querySelector("[data-avatar]").textContent = (name || card.querySelector("[data-email]").textContent).trim().charAt(0).toUpperCase();
    }
    document.getElementById("sum-job").textContent = `${counts.job} job-specific`;
    document.getElementById("sum-notes").textContent = `${counts.notes} company-aware`;
    document.getElementById("sum-general").textContent = `${counts.general} general`;
    document.getElementById("sum-missing").textContent = `${counts.missing} need a company`;
  }

  function applyShared() {
    document.getElementById("shared-box").hidden = !shared.checked;
    for (const card of cards()) {
      card.querySelector("[data-jd-wrap]").hidden = shared.checked;
      card.querySelector("[data-shared-note]").hidden = !shared.checked;
    }
    readiness();
  }

  function markDirty() {
    dirty = true;
    saveState.textContent = "Unsaved changes";
    readiness();
  }

  function payload() {
    return {
      shared_jd: shared.checked,
      shared_job_description: sharedText.value,
      recipients: cards().map((card) => ({
        id: card.dataset.rid,
        ...Object.fromEntries(["name", "company", "designation", "job_title", "job_url", "job_description", "company_notes", "instructions"].map((f) => [f, get(card, f).value])),
      })),
    };
  }

  function showErrors(errors) {
    form.querySelectorAll(".field-error").forEach((el) => (el.textContent = ""));
    form.querySelectorAll("[data-card-error]").forEach((el) => (el.hidden = true));
    let first = null;
    for (const [rid, errs] of Object.entries(errors || {})) {
      const scope = rid === "shared" ? form : form.querySelector(`.rcard[data-rid="${rid}"]`);
      if (!scope) continue;
      for (const [field, msg] of Object.entries(errs)) {
        const el = scope.querySelector(`[data-error-for="${field}"]`);
        if (el) el.textContent = msg;
      }
      if (rid !== "shared") scope.querySelector("[data-card-error]").hidden = false;
      first = first || scope;
    }
    if (first && first !== form) openCard(first);
    else first?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  async function save() {
    saveState.textContent = "Saving…";
    let res;
    try {
      res = await window.kaam.post(form.dataset.saveUrl, payload());
    } catch {
      res = { ok: false, data: { error: "Network error. Your changes are still on this page." } };
    }
    const { ok, data } = res;
    if (!ok) {
      saveState.textContent = "Not saved";
      window.kaam.toast(data.error || "Could not save.", "bad", 6000);
      return { ok: false };
    }
    showErrors(data.errors);
    const bad = Object.keys(data.errors || {}).length;
    dirty = bad > 0;
    saveState.textContent = bad ? `${bad} card${bad === 1 ? "" : "s"} need fixing` : "All changes saved.";
    return { ok: bad === 0 };
  }

  form.addEventListener("submit", (e) => e.preventDefault()); // Enter inside a field must not reload the page
  form.addEventListener("input", markDirty);
  shared.addEventListener("change", () => {
    applyShared();
    markDirty();
  });
  document.getElementById("save-btn").addEventListener("click", async () => {
    if ((await save()).ok) window.kaam.toast("Saved.", "ok", 2000);
  });
  goBtn.addEventListener("click", async () => {
    goBtn.disabled = true;
    if ((await save()).ok) {
      dirty = false;
      location.href = form.dataset.detailUrl;
      return;
    }
    goBtn.disabled = form.dataset.aiReady !== "1";
  });
  document.getElementById("back-link").addEventListener("click", async (e) => {
    e.preventDefault(); // going back keeps what was typed
    if (!dirty || (await save()).ok) {
      dirty = false;
      location.href = form.dataset.listUrl;
    }
  });
  form.addEventListener("click", (e) => {
    const edit = e.target.closest("[data-edit-name]");
    if (!edit) return;
    const card = edit.closest(".rcard");
    card.querySelector("[data-name-field]").hidden = false;
    card.querySelector("[data-name-line]").hidden = true;
    get(card, "name").focus();
  });
  form.addEventListener("focusout", (e) => {
    // once a name is typed and the box is left, fold it back into the "Hi <name>," line
    const input = e.target.closest('[data-f="name"]');
    if (!input || !input.value.trim()) return;
    const card = input.closest(".rcard");
    card.querySelector("[data-name-field]").hidden = true;
    card.querySelector("[data-name-line]").hidden = false;
  });
  form.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-remove]");
    if (!btn) return;
    if (!confirm("Remove this recipient from the campaign? The contact itself is not deleted.")) return;
    const { ok, data } = await window.kaam.post(btn.dataset.url);
    if (!ok) return window.kaam.toast(data.error || "Could not remove.", "bad", 5000);
    const gone = btn.closest(".rcard");
    if (!reduced) await gone.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(24px) scale(.98)" }], { duration: 240, easing: "ease-in" }).finished;
    gone.remove();
    readiness();
  });
  window.addEventListener("beforeunload", (e) => {
    if (dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  // ── motion: each recipient opens as a centred pop-up over a blurred page ──
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const dlg = (card) => card.querySelector("dialog");
  function openCard(card) {
    const d = dlg(card);
    if (!d.open) d.showModal();
    document.body.classList.add("modal-open");
    d.querySelector("input:not([hidden]), textarea")?.blur();
  }
  function closeCard(card) {
    const d = dlg(card);
    if (!d.open) return;
    if (reduced) return d.close();
    d.classList.add("closing");
    d.addEventListener("animationend", () => { d.classList.remove("closing"); d.close(); }, { once: true });
  }
  form.addEventListener("click", (e) => {
    const open = e.target.closest("[data-open]");
    if (open) return openCard(open.closest(".rcard"));
    const close = e.target.closest("[data-close]");
    if (close) closeCard(close.closest(".rcard"));
    if (e.target.matches("dialog.rmodal")) closeCard(e.target.closest(".rcard")); // a click on the blurred backdrop
  });
  form.addEventListener("cancel", (e) => { // the Escape key: close with the same motion
    if (e.target.matches("dialog.rmodal")) { e.preventDefault(); closeCard(e.target.closest(".rcard")); }
  }, true);
  form.addEventListener("close", (e) => {
    if (!e.target.matches("dialog.rmodal")) return;
    if (!form.querySelector("dialog[open]")) document.body.classList.remove("modal-open");
    readiness();
  }, true);
  const tplBtn = document.getElementById("tpl-toggle");
  tplBtn?.addEventListener("click", () => {
    const box = document.getElementById("tpl-body");
    box.hidden = !box.hidden;
    tplBtn.setAttribute("aria-expanded", String(!box.hidden));
    if (!box.hidden && !reduced) box.animate([{ opacity: 0, transform: "translateY(-6px)" }, { opacity: 1, transform: "none" }], { duration: 220, easing: "ease-out" });
  });
  applyShared();
})();
