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

  // <div data-category-editor="/save/url"><input><button></div>: saves a category tag via fetch,
  // so it can sit inside a page-wide bulk-select <form> without nesting <form> inside <form>.
  document.querySelectorAll("[data-category-editor]").forEach((box) => {
    const url = box.dataset.categoryEditor;
    const input = box.querySelector("input");
    const btn = box.querySelector("button");
    const save = async () => {
      const { ok, data } = await window.kaam.post(url, { category: input.value.trim() });
      window.kaam.toast(ok ? "Category updated." : data.error || "Could not update category.", ok ? "" : "bad");
    };
    btn.addEventListener("click", save);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); save(); } });
  });

  // <button data-toggle-select="name"> shows/hides every [data-select-all="name"]'s checkboxes
  // (each wrapped in .select-check) so they're out of the way until the user asks to pick resumes.
  document.querySelectorAll("[data-toggle-select]").forEach((toggle) => {
    const name = toggle.dataset.toggleSelect;
    const scope = document.getElementById(name);
    if (!scope) return;
    const setOn = (on) => {
      scope.classList.toggle("selecting", on);
      toggle.textContent = on ? "Cancel" : "Select";
      if (!on) {
        scope.querySelectorAll(`input[type=checkbox][name="ids"]`).forEach((cb) => { cb.checked = false; cb.dispatchEvent(new Event("change", { bubbles: true })); });
      }
    };
    toggle.addEventListener("click", () => setOn(!scope.classList.contains("selecting")));
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

  // Password visibility toggle: wrap any bare password input (e.g. plain Django widgets) with
  // the same eye button markup the allauth field template renders, then wire up every toggle.
  document.querySelectorAll('input[type="password"]').forEach((input) => {
    if (input.closest(".password-field")) return; // already has a toggle
    const wrap = document.createElement("div");
    wrap.className = "password-field";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "eye-btn password-toggle";
    btn.setAttribute("data-password-toggle", "");
    btn.setAttribute("aria-label", "Show password");
    btn.setAttribute("aria-pressed", "false");
    btn.innerHTML =
      '<svg class="pw-eye-open" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/></svg>' +
      '<svg class="pw-eye-closed" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" hidden><path d="M17.94 17.94A10.94 10.94 0 0 1 12 19c-7 0-10-7-10-7a18.6 18.6 0 0 1 4.22-5.94M9.9 4.24A10.94 10.94 0 0 1 12 4c7 0 10 7 10 7a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><path d="M1 1l22 22"/></svg>';
    wrap.appendChild(btn);
  });
  document.querySelectorAll("[data-password-toggle]").forEach((btn) => {
    const input = btn.closest(".password-field")?.querySelector("input");
    if (!input) return;
    btn.addEventListener("click", () => {
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.querySelector(".pw-eye-open").hidden = show;
      btn.querySelector(".pw-eye-closed").hidden = !show;
      btn.setAttribute("aria-pressed", String(show));
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });
  });

  // "Rewrite password" fields: flag a mismatch against the original password as soon as it
  // differs by even one character, instead of waiting for submit.
  document.querySelectorAll("[data-password-confirm]").forEach((wrap) => {
    const confirmInput = wrap.querySelector("input");
    const form = wrap.closest("form");
    const targetInput = form?.querySelector(`[name="${wrap.dataset.passwordConfirm}"]`);
    const msg = wrap.parentElement.querySelector("[data-password-mismatch]");
    if (!confirmInput || !targetInput || !msg) return;
    const check = () => { msg.hidden = !confirmInput.value || confirmInput.value === targetInput.value; };
    confirmInput.addEventListener("input", check);
    targetInput.addEventListener("input", check);
  });

  // Floating AI helper: the robot offers help; the panel opens only on click, over a blurred backdrop.
  document.querySelectorAll("[data-ai-dock]").forEach((dock) => {
    document.body.appendChild(dock); // top level, so it layers correctly with dialogs
    const pop = dock.querySelector(".ai-pop");
    const fab = dock.querySelector("[data-ai-open]");
    const backdrop = Object.assign(document.createElement("div"), { className: "ai-pop-backdrop" });
    backdrop.hidden = true;
    document.body.appendChild(backdrop);
    const setOpen = (open) => {
      pop.hidden = !open;
      backdrop.hidden = !open;
      fab.setAttribute("aria-expanded", String(open));
      dock.classList.toggle("open", open);
      if (open) { dock.classList.add("seen"); pop.querySelector("textarea, input, button:not([data-ai-close])")?.focus(); }
    };
    dock.kaamSetOpen = setOpen; // so other scripts (e.g. the template editor) can close this panel after it is done
    fab.addEventListener("click", () => setOpen(pop.hidden));
    dock.querySelector("[data-ai-close]")?.addEventListener("click", () => setOpen(false));
    backdrop.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pop.hidden) setOpen(false); });
  });
})();
