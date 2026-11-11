// Resume workspace (Alpine.js component): structured editing, live preview, save/versioning and AI suggestions.
document.addEventListener("alpine:init", () => {
  window.Alpine.data("resumeEditor", (cfg) => ({
    d: JSON.parse(document.getElementById("resume-data").textContent),
    labels: JSON.parse(document.getElementById("section-labels").textContent),
    locked: cfg.locked,
    label: cfg.label,
    dirty: false,
    saving: false,
    tab: "edit",
    ai: { jd: "", instructions: "", job_title: "", company: "", busy: false, applying: false, error: "", result: null, elapsed: 0,
          live: { score: 0, pages: 1, matched: [] } },

    init() {
      // Only edits to resume content count as unsaved changes (not the version picker or the AI form).
      this.$el.addEventListener("change", (e) => { if (e.target.closest("[data-track]")) this.dirty = true; });
      window.addEventListener("beforeunload", (e) => {
        if (this.dirty) { e.preventDefault(); e.returnValue = ""; }
      });
      document.addEventListener("keydown", (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); this.save(this.locked); }
      });
    },

    uid() { return Math.random().toString(36).slice(2, 12); },
    splitList(text, maxLen, maxItems) {
      return text.split(",").map((s) => s.trim().slice(0, maxLen)).filter(Boolean).slice(0, maxItems);
    },
    move(i, delta) {
      const o = this.d.section_order, j = i + delta;
      if (j < 0 || j >= o.length) return;
      [o[i], o[j]] = [o[j], o[i]];
      this.dirty = true;
    },
    hasContent(key) {
      const d = this.d;
      return {
        summary: d.summary.trim(),
        skills: d.skills.some((g) => g.items.length),
        experience: d.experience.length,
        projects: d.projects.length,
        education: d.education.length,
        certifications: d.certifications.length,
      }[key];
    },
    warnIfDirty(e) {
      if (this.dirty && !confirm("You have unsaved changes. The export uses the last saved version. Download anyway?")) e.preventDefault();
    },

    async save(asNew) {
      this.saving = true;
      const { ok, data } = await window.kaam.post(cfg.saveUrl, { data: this.d, label: this.label, as_new: !!asNew });
      this.saving = false;
      if (!ok) { window.kaam.toast(data.error || "Could not save.", "bad", 7000); return; }
      this.dirty = false;
      if (data.created) { window.location.href = data.url; return; }
      window.kaam.toast("Saved.", "ok", 2000);
    },

    kindLabel(s) {
      if (s.type === "headline") return "Headline: match the job title";
      if (s.type === "summary") return "Rewrite summary with job keywords";
      if (s.type === "bullet_add") return "New line" + (s.where ? " · " + s.where : "");
      return "Reword bullet " + ((s.target.bullet_index ?? 0) + 1) + (s.where ? " · " + s.where : "");
    },
    byType(t) { return this.ai.result ? this.ai.result.suggestions.filter((s) => s.type === t) : []; },
    decidedCount() { return this.ai.result.suggestions.filter((s) => s.decision).length; },
    pendingCount() { return this.ai.result.suggestions.filter((s) => !s.decision && s.state !== "blocked").length; },
    acceptedCount() { return this.ai.result.suggestions.filter((s) => s.decision === "accept").length; },
    accepted() { return this.ai.result.suggestions.filter((s) => s.decision === "accept" && s.state !== "blocked"); },
    kwState(k) {
      if (k.matched) return "matched";
      return this.ai.live.matched.includes(k.term) ? "added" : "missing";
    },
    decide(s, d) {
      if (d === "accept" && s.state === "blocked") return;
      s.decision = s.decision === d ? null : d; // clicking again undoes the choice
      this.recheck();
    },
    decideAll(d) {
      this.ai.result.suggestions.forEach((s) => { if (s.state !== "blocked") s.decision = d; else s.decision = "decline"; });
      this.recheck();
    },
    recheck() {
      clearTimeout(this._recheckTimer);
      this._recheckTimer = setTimeout(async () => {
        const { ok, data } = await window.kaam.post(cfg.checkUrl, { suggestions: this.accepted(), keywords: this.ai.result.keywords });
        if (ok) this.ai.live = { score: data.score, pages: data.pages, matched: data.matched };
      }, 250);
    },

    async tailor() {
      this.ai.busy = true;
      this.ai.error = "";
      this.ai.result = null;
      this.ai.elapsed = 0;
      const timer = setInterval(() => this.ai.elapsed++, 1000);
      const { ok, data } = await window.kaam.post(cfg.tailorUrl, {
        job_description: this.ai.jd, instructions: this.ai.instructions, job_title: this.ai.job_title, company: this.ai.company,
        confirm: true, // the explicit button press is the consent for this request
      });
      clearInterval(timer);
      this.ai.busy = false;
      if (!ok) { this.ai.error = data.error; return; }
      data.suggestions.forEach((s) => (s.decision = s.state === "blocked" ? "decline" : null));
      if (!this.ai.job_title && data.job_title) this.ai.job_title = data.job_title;
      this.ai.live = { score: data.score_before, pages: data.pages_before, matched: data.keywords.filter((k) => k.matched).map((k) => k.term) };
      this.ai.result = data;
    },

    async apply() {
      if (this.dirty && !confirm("Unsaved edits are not included: the tailored version is built from the last saved version. Continue?")) return;
      this.ai.applying = true;
      const { ok, data } = await window.kaam.post(cfg.applyUrl, {
        suggestions: this.accepted(), keywords: this.ai.result.keywords, job_title: this.ai.job_title, company: this.ai.company,
      });
      this.ai.applying = false;
      if (!ok) { window.kaam.toast(data.error || "Could not create the resume.", "bad", 8000); return; }
      this.dirty = false;
      window.location.href = data.url;
    },
  }));
});
