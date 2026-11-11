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
