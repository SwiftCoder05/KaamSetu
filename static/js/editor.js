// Document-style email editor: contenteditable + formatting toolbar, case changes, placeholders, live preview.
// The server sanitises whatever is posted, so this script only has to be convenient, not trusted.
(function () {
  const form = document.getElementById("tpl-form");
  const editor = document.getElementById("body-editor");
  const hidden = document.getElementById("body");
  const subject = document.getElementById("subject");
  const roleLabel = document.getElementById("role_label");
  const dirtyEl = document.getElementById("dirty");
  const previewUrl = form.dataset.previewUrl;
  let dirty = false;
  let savedRange = null;

  try { document.execCommand("defaultParagraphSeparator", false, "p"); } catch {}
  try { document.execCommand("styleWithCSS", false, false); } catch {}

  const markDirty = () => {
    dirty = true;
    dirtyEl.hidden = false;
    hidden.value = editor.innerHTML;
    schedulePreview();
  };

  // Remember the caret so toolbar clicks and placeholder chips act where the user was typing.
  document.addEventListener("selectionchange", () => {
    const sel = window.getSelection();
    if (sel.rangeCount && editor.contains(sel.anchorNode)) savedRange = sel.getRangeAt(0).cloneRange();
    refreshToolbar();
  });
  function restoreSelection() {
    editor.focus();
    if (!savedRange) return;
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(savedRange);
  }

  const toolbar = document.querySelector(".toolbar");
  toolbar.addEventListener("mousedown", (e) => e.preventDefault()); // keep the selection in the editor
  toolbar.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    restoreSelection();
    if (btn.dataset.cmd) document.execCommand(btn.dataset.cmd, false, null);
    else if (btn.dataset.case) changeCase(btn.dataset.case);
    else if (btn.dataset.action === "link") insertLink();
    markDirty();
    refreshToolbar();
  });

  function refreshToolbar() {
    toolbar.querySelectorAll("button[data-cmd]").forEach((b) => {
      const cmd = b.dataset.cmd;
      if (["bold", "italic", "underline", "strikeThrough", "insertUnorderedList", "insertOrderedList"].includes(cmd)) {
        let on = false;
        try { on = document.queryCommandState(cmd); } catch {}
        b.classList.toggle("on", on && editor.contains(window.getSelection().anchorNode));
        b.setAttribute("aria-pressed", on ? "true" : "false");
      }
    });
  }

  function changeCase(mode) {
    const sel = window.getSelection();
    if (!sel.rangeCount || sel.isCollapsed) {
      window.kaam.toast("Select some text first, then choose a case.", "warn", 2500);
      return;
    }
    const range = sel.getRangeAt(0);
    // Walk the text nodes inside the selection and transform only the selected slice of each,
    // so bold/italic and placeholders around them are preserved.
    const walker = document.createTreeWalker(range.commonAncestorContainer.nodeType === 3 ? range.commonAncestorContainer.parentNode : range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) if (range.intersectsNode(walker.currentNode)) nodes.push(walker.currentNode);
    // Title case: capitalise the first letter of each word; a word that starts before the selection is left alone.
    const conv = (s, midWord) => {
      if (mode === "upper") return s.toUpperCase();
      if (mode === "lower") return s.toLowerCase();
      return s.replace(/(^|\s)(\S+)/g, (m, sp, w, off) =>
        sp + (off === 0 && sp === "" && midWord ? w.toLowerCase() : w[0].toUpperCase() + w.slice(1).toLowerCase()));
    };
    const startNode = range.startContainer, startOff = range.startOffset, endNode = range.endContainer, endOff = range.endOffset;
    let lastChar = startOff > 0 && startNode.nodeType === 3 ? startNode.data[startOff - 1] : " ";
    nodes.forEach((n) => {
      const from = n === startNode ? startOff : 0;
      const to = n === endNode ? endOff : n.data.length;
      if (to <= from) return;
      // Placeholders like {{first_name}} are never changed: they must stay lowercase to work.
      const slice = n.data.slice(from, to).replace(/\{\{[^{}]*\}\}|[^{]+|\{/g, (part, off) => {
        if (part.startsWith("{{")) return part;
        const prev = off > 0 ? n.data[from + off - 1] : lastChar;
        return conv(part, !/\s/.test(prev));
      });
      n.data = n.data.slice(0, from) + slice + n.data.slice(to);
      lastChar = n.data[to - 1] || lastChar;
    });
    const r = document.createRange();
    r.setStart(startNode, startOff);
    r.setEnd(endNode, Math.min(endOff, endNode.length ?? endOff));
    sel.removeAllRanges();
    sel.addRange(r);
  }

  function insertLink() {
    const url = window.prompt("Link address (https://… or mailto:…)", "https://");
    if (!url) return;
    if (!/^(https?:\/\/|mailto:)/i.test(url)) {
      window.kaam.toast("Links must start with https://, http:// or mailto:", "bad");
      return;
    }
    const sel = window.getSelection();
    if (sel.isCollapsed) document.execCommand("insertText", false, url);
    if (!sel.isCollapsed) document.execCommand("createLink", false, url);
  }

  // Placeholder chips
  document.querySelectorAll("[data-ph]").forEach((chip) =>
    chip.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const ph = `{{${chip.dataset.ph}}}`;
      const focused = document.activeElement;
      if (focused === subject) {
        const s = subject.selectionStart ?? subject.value.length;
        subject.setRangeText(ph, s, subject.selectionEnd ?? s, "end");
        subject.dispatchEvent(new Event("input"));
        return;
      }
      restoreSelection();
      document.execCommand("insertText", false, ph);
      markDirty();
    }),
  );

  // Paste as plain text so styles from Word/web pages don't leak into the email.
  editor.addEventListener("paste", (e) => {
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData("text/plain");
    document.execCommand("insertText", false, text);
  });
  editor.addEventListener("input", markDirty);
  [subject, roleLabel, document.getElementById("name")].forEach((el) => el.addEventListener("input", markDirty));

  // Live preview, rendered by the server with a sample recipient.
  let timer = null;
  function schedulePreview() {
    clearTimeout(timer);
    timer = setTimeout(preview, 350);
  }
  async function preview() {
    const { ok, data } = await window.kaam.post(previewUrl, { subject: subject.value, body: editor.innerHTML, role_label: roleLabel.value });
    if (!ok) return;
    document.getElementById("pv-subject").textContent = data.subject;
    document.getElementById("pv-body").innerHTML = data.html; // sanitised server-side
    const warn = document.getElementById("pv-warn");
    const notes = [];
    if (data.unknown.length) notes.push(`Not recognised: ${data.unknown.map((x) => `{{${x}}}`).join(", ")}. These are sent as typed.`);
    if (data.missing.length) notes.push(`No value for ${data.missing.map((x) => `{{${x}}}`).join(", ")} in this sample. Add a fallback like {{${data.missing[0]}|…}} or recipients without it will fail.`);
    warn.hidden = !notes.length;
    warn.textContent = notes.join(" ");
  }

  form.addEventListener("submit", () => {
    hidden.value = editor.innerHTML;
    dirty = false;
  });
  window.addEventListener("beforeunload", (e) => {
    if (dirty) { e.preventDefault(); e.returnValue = ""; }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  // AI email writer. The server returns sanitised HTML and guard results; the editor changes only on "Use this draft".
  const ai = document.getElementById("ai-writer");
  const aiButtons = ai ? ai.querySelectorAll("[data-ai-mode]") : [];
  if (ai && aiButtons.length) {
    const status = document.getElementById("ai-status");
    const result = document.getElementById("ai-result");
    const issuesEl = document.getElementById("ai-issues");
    const useBtn = document.getElementById("ai-use");
    const restoreBtn = document.getElementById("ai-restore");
    let draft = null;
    let previous = null;
    const aiRole = document.getElementById("ai-role");
    const aiResume = document.getElementById("ai-resume");
    if (aiRole) aiRole.value = roleLabel.value;
    // Enter in the role box must not submit (save) the template form.
    if (aiRole) aiRole.addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });

    // Resume source: an existing resume from the studio, or a file uploaded for this draft.
    const aiSource = () => document.querySelector("input[name=ai-src]:checked")?.value || "existing";
    const showSource = () => {
      const picked = aiSource();
      document.querySelectorAll("[data-src]").forEach((p) => (p.hidden = p.dataset.src !== picked));
    };
    document.querySelectorAll("input[name=ai-src]").forEach((r) => r.addEventListener("change", showSource));
    showSource();
    const aiFile = document.getElementById("ai-file");
    const uploadStatus = document.getElementById("ai-upload-status");
    aiFile?.addEventListener("change", async () => {
      const file = aiFile.files[0];
      if (!file) return;
      uploadStatus.textContent = "Reading your resume…";
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(aiFile.dataset.url, { method: "POST", body: form, credentials: "same-origin", headers: { "X-CSRFToken": document.querySelector('meta[name="csrf-token"]')?.content || "" } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        uploadStatus.textContent = data.error || "That file could not be read.";
        uploadStatus.className = "tiny bad-text";
        aiFile.value = "";
        return;
      }
      uploadStatus.textContent = `Using “${data.name}” for this draft.`;
      uploadStatus.className = "tiny muted";
    });

    aiButtons.forEach((btn) => btn.addEventListener("click", async () => {
      aiButtons.forEach((b) => (b.disabled = true));
      result.hidden = true;
      status.hidden = false;
      const started = Date.now();
      const tick = () => (status.textContent = `Writing… ${Math.round((Date.now() - started) / 1000)}s`);
      tick();
      const timer = setInterval(tick, 1000);
      const { ok, data } = await window.kaam.post(ai.dataset.url, {
        confirm: true,
        mode: btn.dataset.aiMode,
        role_label: (aiRole && aiRole.value.trim()) || roleLabel.value,
        resume_source: aiSource(),
        resume_version: aiResume ? aiResume.value : "",
        subject: subject.value,
        body: editor.innerHTML,
      });
      clearInterval(timer);
      aiButtons.forEach((b) => (b.disabled = false));
      if (!ok) {
        status.textContent = data.error;
        window.kaam.toast(data.error, "bad", 7000);
        return;
      }
      status.hidden = true;
      draft = data;
      document.getElementById("ai-subject").textContent = data.subject;
      document.getElementById("ai-body").innerHTML = data.body_html; // sanitised server-side
      issuesEl.hidden = !data.issues.length;
      issuesEl.className = `alert small ${data.state === "blocked" ? "bad" : "warn"}`;
      issuesEl.replaceChildren(...data.issues.map((t) => Object.assign(document.createElement("div"), { textContent: t })));
      useBtn.hidden = data.state === "blocked";
      result.hidden = false;
    }));

    const doRestore = () => {
      if (!previous) return;
      subject.value = previous.subject;
      editor.innerHTML = previous.body;
      previous = null;
      markDirty();
    };
    useBtn.addEventListener("click", () => {
      if (!draft || draft.state === "blocked") return;
      previous = { subject: subject.value, body: editor.innerHTML };
      subject.value = draft.subject;
      editor.innerHTML = draft.body_html;
      markDirty();
      draft = null;
      result.hidden = true;
      // The draft is now in the editor itself, so the panel's job here is done: close it instead of leaving it open.
      document.querySelector("[data-ai-dock]")?.kaamSetOpen?.(false);
      const toastEl = window.kaam.toast("Draft inserted. Review it, then press Save.", "ok", 8000);
      const undo = Object.assign(document.createElement("button"), { type: "button", className: "toast-undo", textContent: "Undo" });
      undo.addEventListener("click", () => { doRestore(); toastEl.remove(); window.kaam.toast("Your previous text is back.", "ok"); });
      toastEl.insertBefore(undo, toastEl.lastElementChild);
    });
    document.getElementById("ai-discard").addEventListener("click", () => {
      draft = null;
      result.hidden = true;
    });
    restoreBtn.addEventListener("click", () => {
      doRestore();
      restoreBtn.hidden = true;
      result.hidden = true;
      window.kaam.toast("Your previous text is back.", "ok");
    });
  }

  // "Draft from resume" (next to the eye): opens the AI panel on the resume + role fields.
  document.querySelector("[data-resume-draft]")?.addEventListener("click", () => {
    const pop = document.getElementById("ai-pop");
    if (pop && pop.hidden) document.querySelector("[data-ai-open]")?.click();
    const role = document.getElementById("ai-role");
    if (role) { if (!role.value) role.value = roleLabel.value; role.focus(); }
  });

  // Preview dialog: opens from the blinking eye, blurs the editor behind it.
  const modal = document.getElementById("pv-modal");
  // Lives at the top level so its backdrop blur sees the page (the page wrapper is animated, which would block it).
  if (modal) document.body.appendChild(modal);
  const openBtn = document.querySelector("[data-preview-open]");
  const setPreview = (open) => {
    modal.hidden = !open;
    document.body.classList.toggle("pv-open", open);
    if (open) { preview(); modal.querySelector("[data-preview-close].icon-btn")?.focus(); } else { openBtn?.focus(); }
  };
  openBtn?.addEventListener("click", () => setPreview(true));
  modal?.querySelectorAll("[data-preview-close]").forEach((el) => el.addEventListener("click", () => setPreview(false)));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && modal && !modal.hidden) setPreview(false); });

  hidden.value = editor.innerHTML;
  preview();
})();
