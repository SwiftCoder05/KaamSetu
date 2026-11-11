// Shared helpers: toasts, CSRF-aware fetch, confirm-before-submit, select-all checkboxes.
(function () {
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";

  window.kaam = {
    async post(url, body) {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
        body: JSON.stringify(body || {}),
        credentials: "same-origin",
      });
      let data = {};
      try { data = await res.json(); } catch { data = { error: `Request failed (${res.status}).` }; }
      if (!res.ok && !data.error) data.error = `Request failed (${res.status}).`;
      return { ok: res.ok, status: res.status, data };
    },
    toast(message, tone = "", ms = 4500) {
      const box = document.getElementById("toasts");
      const el = document.createElement("div");
      el.className = `toast ${tone}`;
      el.setAttribute("role", tone === "bad" ? "alert" : "status");
      const span = document.createElement("span");
      span.textContent = message;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("aria-label", "Dismiss");
      btn.textContent = "×";
      btn.onclick = () => el.remove();
      el.append(span, btn);
      box.appendChild(el);
      fade(el, ms);
      return el;
    },
  };

  function fade(el, ms) {
    setTimeout(() => {
      el.classList.add("leaving");
      setTimeout(() => el.remove(), 500);
    }, ms);
  }
  document.querySelectorAll(".toast[data-auto]").forEach((el) => fade(el, 5000));

  // <form data-confirm="Are you sure?">
  document.addEventListener("submit", (e) => {
    const msg = e.target.dataset?.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // <input type=checkbox data-select-all="name"> toggles every enabled checkbox named `name`
  document.addEventListener("change", (e) => {
    const name = e.target.dataset?.selectAll;
    if (!name) return;
    document.querySelectorAll(`input[type=checkbox][name="${name}"]:not(:disabled)`).forEach((cb) => {
      if (cb.closest("[hidden]") || cb.closest("tr")?.style.display === "none") return;
      cb.checked = e.target.checked;
      cb.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
  // Bulk bar: shown only while rows are ticked, with a live count.
  document.querySelectorAll("[data-bulk-bar]").forEach((bar) => {
    const name = bar.dataset.bulkBar;
    const countEl = bar.querySelector("[data-bulk-count]");
    const update = () => {
      const n = document.querySelectorAll(`input[type=checkbox][name="${name}"]:checked`).length;
      bar.hidden = n === 0;
      if (countEl) countEl.textContent = n;
    };
    document.addEventListener("change", (e) => { if (e.target.name === name || e.target.dataset?.selectAll === name) update(); });
    update();
  });

  // Numbers on stat cards count up once when the page opens.
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    document.querySelectorAll("[data-count]").forEach((el) => {
      const target = parseInt(el.dataset.count, 10);
      if (!(target > 0)) return;
      const start = performance.now(), dur = Math.min(900, 300 + target * 40);
      const step = (t) => {
        const k = Math.min(1, (t - start) / dur);
        el.textContent = Math.round(target * (1 - Math.pow(1 - k, 3)));
        if (k < 1) requestAnimationFrame(step);
      };
      el.textContent = "0";
      requestAnimationFrame(step);
    });
  }

  // Floating AI helper: the robot offers help; the panel opens only on click.
  document.querySelectorAll("[data-ai-dock]").forEach((dock) => {
    document.body.appendChild(dock); // top level, so it layers correctly with dialogs
    const pop = dock.querySelector(".ai-pop");
    const fab = dock.querySelector("[data-ai-open]");
    const setOpen = (open) => {
      pop.hidden = !open;
      fab.setAttribute("aria-expanded", String(open));
      dock.classList.toggle("open", open);
      if (open) { dock.classList.add("seen"); pop.querySelector("textarea, input, button:not([data-ai-close])")?.focus(); }
    };
    fab.addEventListener("click", () => setOpen(pop.hidden));
    dock.querySelector("[data-ai-close]")?.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) setOpen(false); });
  });
})();
