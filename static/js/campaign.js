// Campaign page: recipient preview switcher, soft "no resume" warning, and the batch-send loop.
(function () {
  // ── preview switcher ──
  const pick = document.getElementById("pick-preview");
  pick?.addEventListener("change", () => {
    document.querySelectorAll(".preview-item").forEach((el) => (el.hidden = el.dataset.i !== pick.value));
  });

  // ── soft warning when sending without a resume: shown once, then the next click goes through ──
  const confirmForm = document.getElementById("confirm-form");
  let warnedNoResume = false;
  confirmForm?.addEventListener("submit", (e) => {
    if (confirmForm.dataset.hasResume === "1" || warnedNoResume) return;
    e.preventDefault();
    warnedNoResume = true;
    window.kaam.toast("No resume is attached to these emails. Click Send again if that's intended.", "warn", 4000);
  });

  // ── draft preview: per-recipient AI writing, edit, regenerate and approve (nothing here sends mail) ──
  const genTones = { ready: "ok", approved: "ok", generating: "info", needs_attention: "warn", failed: "bad", edited: "info", pending: "muted", none: "muted" };
  const sendBtn = document.getElementById("send-btn");
  const items = () => [...document.querySelectorAll(".preview-item")];

  function refreshSend() {
    if (!sendBtn) return;
    const blocked = items().some((el) => el.dataset.unfilled === "1" || el.dataset.source === "pending" || el.dataset.gen === "generating");
    sendBtn.disabled = blocked || sendBtn.dataset.conn !== "1";
    const fb = document.getElementById("ai-fallback-count");
    if (fb) {
      const n = items().filter((el) => el.dataset.gen === "needs_attention").length;
      fb.textContent = n;
      document.getElementById("ai-fallbacks").hidden = n === 0;
    }
    const failedEl = document.getElementById("ai-failed-count");
    if (failedEl) {
      const n = items().filter((el) => el.dataset.gen === "failed").length;
      failedEl.textContent = n;
      document.getElementById("ai-failed").hidden = n === 0;
    }
  }

  function paintItem(rec) {
    const el = document.querySelector(`.preview-item[data-rid="${rec.id}"]`);
    if (!el || el.querySelector("[data-save]")?.hidden === false) return; // never repaint an email the user is editing
    el.querySelector("[data-subject]").textContent = rec.subject;
    el.querySelector("[data-body]").innerHTML = rec.body_html; // sanitised by the server
    const badge = el.querySelector("[data-source]");
    badge.textContent = rec.gen_label || rec.label;
    badge.className = `badge ${genTones[rec.gen_status] || "muted"}`;
    const note = el.querySelector("[data-note]");
    note.textContent = rec.note || "";
    note.hidden = !rec.note;
    const err = el.querySelector("[data-error]");
    if (err) {
      err.textContent = rec.error || "";
      err.hidden = !(rec.error && rec.source === "pending");
    }
    const stale = el.querySelector("[data-stale]");
    if (stale) stale.hidden = !rec.stale;
    const warn = el.querySelector("[data-warnings]");
    if (warn) {
      warn.replaceChildren(...(rec.warnings || []).map((w) => Object.assign(document.createElement("li"), { textContent: w })));
      warn.hidden = !(rec.warnings || []).length;
    }
    el.dataset.source = rec.source;
    el.dataset.gen = rec.gen_status;
    el.dataset.unfilled = rec.unfilled ? "1" : "";
    const regen = el.querySelector("[data-regen]");
    if (regen) {
      regen.hidden = rec.gen_status === "pending" || rec.gen_status === "generating";
      regen.querySelector("[data-regen-label]").textContent = rec.gen_status === "failed" ? "Retry" : "Regenerate";
    }
    const approve = el.querySelector("[data-approve]");
    if (approve) approve.hidden = ["pending", "generating", "failed", "approved"].includes(rec.gen_status);
    const opt = pick?.querySelector(`option[data-rid="${rec.id}"]`);
    if (opt) opt.textContent = rec.email + (rec.unfilled ? " ⚠" : "");
    refreshSend();
  }

  async function post(url, body) {
    try {
      return await window.kaam.post(url, body);
    } catch {
      return { ok: false, data: { error: "Network error. Check your connection and try again." } };
    }
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".preview-item [data-edit], .preview-item [data-save], .preview-item [data-cancel], .preview-item [data-regen], .preview-item [data-approve]");
    if (!btn) return;
    const el = btn.closest(".preview-item");
    const subject = el.querySelector("[data-subject]");
    const input = el.querySelector("[data-subject-input]");
    const body = el.querySelector("[data-body]");
    const editing = (on) => {
      input.hidden = !on;
      subject.hidden = on;
      body.contentEditable = on ? "true" : "false";
      el.querySelector("[data-edit]").hidden = on;
      el.querySelector("[data-save]").hidden = !on;
      el.querySelector("[data-cancel]").hidden = !on;
      for (const b of el.querySelectorAll("[data-regen], [data-approve]")) b.disabled = on;
    };
    if (btn.matches("[data-edit]")) {
      el._original = body.innerHTML;
      input.value = subject.textContent.trim();
      editing(true);
      body.focus();
    } else if (btn.matches("[data-cancel]")) {
      body.innerHTML = el._original;
      editing(false);
    } else if (btn.matches("[data-save]")) {
      btn.disabled = true;
      const { ok, data } = await post(el.dataset.editUrl, { subject: input.value, body: body.innerHTML });
      btn.disabled = false;
      if (!ok) return window.kaam.toast(data.error, "bad", 6000);
      editing(false);
      paintItem(data.recipient);
      window.kaam.toast(data.recipient.unfilled ? "Saved, but it still contains a {{placeholder}}." : "Saved.", data.recipient.unfilled ? "warn" : "ok", 3000);
    } else if (btn.matches("[data-approve]")) {
      btn.disabled = true;
      const { ok, data } = await post(el.dataset.approveUrl, {});
      btn.disabled = false;
      if (!ok) return window.kaam.toast(data.error, "bad", 6000);
      paintItem(data.recipient);
      window.kaam.toast("Approved.", "ok", 2000);
    } else if (btn.matches("[data-regen]")) {
      const needsOk = el.dataset.gen === "edited" || el.dataset.gen === "approved";
      if (needsOk && !confirm("You edited or approved this email. Regenerating replaces it with a new AI draft. Continue?")) return;
      btn.disabled = true;
      const { ok, data } = await post(el.dataset.regenUrl, { confirm: true, overwrite: needsOk });
      btn.disabled = false;
      if (data.recipient) paintItem(data.recipient);
      if (!ok) return window.kaam.toast(data.error, "bad", 7000);
      window.kaam.toast("New AI email written. Review it before sending.", "ok", 3000);
    }
  });

  const aiBox = document.getElementById("ai-box");
  if (aiBox && Number(aiBox.dataset.pending) > 0) {
    const title = document.getElementById("ai-title");
    const count = document.getElementById("ai-count");
    const errBox = document.getElementById("ai-error");
    const resumeBtn = document.getElementById("ai-resume");
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const workers = Math.max(1, Number(aiBox.dataset.concurrency) || 1); // bounded concurrency: the server enforces it too
    let stopped = false;
    const stop = (msg) => {
      stopped = true;
      title.textContent = "AI personalisation paused";
      errBox.textContent = msg;
      errBox.hidden = false;
      resumeBtn.hidden = false;
    };
    async function worker() {
      while (!stopped) {
        const { ok, data } = await post(aiBox.dataset.url, {});
        if (data.recipient) paintItem(data.recipient);
        if (typeof data.pending === "number") count.textContent = data.pending;
        if (data.busy) {
          await wait(3000); // the other workers are using every slot
          continue;
        }
        if (!ok) return stop(data.error);
        if (data.done) {
          for (const rec of data.recipients || []) paintItem(rec);
          if (data.generating > 0) {
            await wait(4000); // another page or tab is still writing some emails
            continue;
          }
          return;
        }
      }
    }
    async function run() {
      stopped = false;
      resumeBtn.hidden = true;
      errBox.hidden = true;
      title.textContent = "Writing personalised emails…";
      await Promise.all(Array.from({ length: workers }, worker));
      if (stopped) return;
      title.textContent = "AI personalisation finished";
      document.getElementById("ai-progress").hidden = true;
      refreshSend();
    }
    resumeBtn.addEventListener("click", run);
    if (aiBox.dataset.aiEnabled === "1") run();
    else stop("AI is switched off in Settings, so nothing is sent to a model. Turn it on, or use your template for the rest.");
  }

  // ── sender loop ──
  const runner = document.getElementById("runner");
  if (!runner) return;
  const url = runner.dataset.sendUrl;
  const startBtn = document.getElementById("start-btn");
  const liveBox = document.getElementById("live-box");
  const badge = document.getElementById("status-badge");
  const tones = { accepted: "ok", completed: "ok", sending: "info", confirmed: "info", queued: "muted", paused: "warn", failed: "bad", skipped: "muted", cancelled: "muted" };
  const pauseText = {
    daily_limit: "Daily sending limit reached. Resume later, once the rolling 24-hour window has room again.",
    provider_rate_limit: "Gmail asked us to slow down. Try resuming in a while.",
    auth_error: "Gmail rejected the app password. Reconnect in Settings, then resume.",
    user: "Paused by you.",
  };
  let running = false;

  function paint(state) {
    const c = state.counts;
    for (const k of ["accepted", "queued", "failed", "skipped"]) document.getElementById(`n-${k}`).textContent = c[k] + (k === "queued" ? c.sending : 0);
    const pct = (n) => (c.total ? (100 * n) / c.total : 0) + "%";
    document.getElementById("bar-ok").style.width = pct(c.accepted);
    document.getElementById("bar-bad").style.width = pct(c.failed);
    document.getElementById("bar-skip").style.width = pct(c.skipped);
    badge.textContent = state.status;
    badge.className = `badge dot ${tones[state.status] || "muted"}`;
    for (const r of state.recipients || []) {
      const row = document.getElementById(`r-${r.id}`);
      if (!row) continue;
      const b = row.querySelector("[data-status]");
      b.textContent = r.status;
      b.className = `badge dot ${tones[r.status] || "muted"}`;
      row.querySelector("[data-error]").textContent = r.error || "";
    }
    const pauseBox = document.getElementById("pause-box");
    pauseBox.hidden = state.status !== "paused";
    if (state.status === "paused") document.getElementById("pause-text").textContent = pauseText[state.pause_reason] || "Paused.";
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  async function loop() {
    if (running) return;
    running = true;
    liveBox.hidden = false;
    if (startBtn) startBtn.disabled = true;
    try {
      for (;;) {
        const { ok, data } = await window.kaam.post(url);
        if (data.counts) paint(data);
        if (!ok) {
          window.kaam.toast(data.error || "Sending stopped.", "bad", 7000);
          break;
        }
        if (data.status !== "sending" && data.status !== "confirmed") {
          if (data.status === "completed") window.kaam.toast("All done. Every email has been processed.", "ok");
          // reload so the action buttons match the new state
          await wait(1200);
          location.replace(location.pathname);
          return;
        }
        await wait(2500);
      }
    } finally {
      running = false;
      liveBox.hidden = true;
      if (startBtn) startBtn.disabled = false;
    }
  }

  startBtn?.addEventListener("click", loop);
  window.addEventListener("beforeunload", (e) => {
    if (running) { e.preventDefault(); e.returnValue = ""; }
  });

  const s = runner.dataset.status;
  if (runner.dataset.autostart === "1" && (s === "confirmed" || s === "sending")) loop();
  else {
    // paint bars from the server-rendered numbers
    const n = (id) => Number(document.getElementById(id).textContent) || 0;
    const total = n("n-accepted") + n("n-queued") + n("n-failed") + n("n-skipped");
    paint({ status: s, pause_reason: runner.dataset.pause, counts: { accepted: n("n-accepted"), queued: n("n-queued"), sending: 0, failed: n("n-failed"), skipped: n("n-skipped"), total } });
  }
})();
