// Wait panel for slow AI calls: estimated step tracker and elapsed time, plus a one-off floating
// mascot popup offering a quick memory game. Usage: const w = window.kaamWait.start(el, "email"); ... w.stop();
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

  const h = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  };
  const fmt = (s) => (s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`);

  // A small memory-match game: flip two tiles at a time, find every pair. Classic "Concentration",
  // easy to build with plain buttons and a couple of timeouts, no library needed.
  function buildMemoryGame() {
    const ICONS = ["🎯", "🍀", "⭐", "🎲", "🧩", "🎳", "🏆", "🎮", "🚀", "🔥"];
    const PAIRS = 6;
    const wrap = h("div", "memory-game");
    const grid = h("div", "memory-grid");
    const status = h("p", "tiny muted memory-status");
    const again = h("button", "btn sm ghost", "Shuffle again");
    again.type = "button";

    const shuffle = (list) => {
      list = [...list];
      for (let i = list.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [list[i], list[j]] = [list[j], list[i]]; }
      return list;
    };

    function setup() {
      let first = null, second = null, busy = false, moves = 0, matched = 0;
      grid.replaceChildren();
      status.textContent = `0 moves · 0 / ${PAIRS} matched`;
      const icons = shuffle(ICONS).slice(0, PAIRS);
      const deck = shuffle([...icons, ...icons]);
      deck.forEach((icon) => {
        const tile = h("button", "memory-card", "?");
        tile.type = "button";
        tile.dataset.icon = icon;
        tile.addEventListener("click", () => {
          if (busy || tile.classList.contains("flipped") || tile.classList.contains("matched")) return;
          tile.textContent = icon;
          tile.classList.add("flipped");
          if (!first) { first = tile; return; }
          second = tile;
          moves++;
          busy = true;
          const ok = first.dataset.icon === second.dataset.icon;
          setTimeout(() => {
            if (ok) {
              first.classList.add("matched");
              second.classList.add("matched");
              matched++;
            } else {
              first.textContent = "?";
              second.textContent = "?";
              first.classList.remove("flipped");
              second.classList.remove("flipped");
            }
            status.textContent = matched === PAIRS ? `Solved in ${moves} moves! 🎉` : `${moves} moves · ${matched} / ${PAIRS} matched`;
            first = second = null;
            busy = false;
          }, ok ? 300 : 650);
        });
        grid.append(tile);
      });
    }

    again.addEventListener("click", setup);
    setup();
    wrap.append(grid, status, again);
    return wrap;
  }

  // A one-off floating card in the left margin: a mascot asks if you want a quick brain game,
  // and only builds the game once you say yes, so it never gets in the way uninvited.
  function spawnWaitNudge() {
    const pop = h("div", "wait-game-popup");
    const head = h("div", "wait-game-head");
    head.append(h("strong", null, "A quick break?"));
    const close = h("button", "wait-game-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss");
    close.addEventListener("click", () => pop.remove());
    head.append(close);

    const ask = h("div", "wait-nudge");
    const mascot = h("div", "wait-mascot", "🙂");
    const bubble = h("div", "wait-bubble", "Want to relax your mind while waiting?");
    const row = h("div", "row");
    const yes = h("button", "btn sm primary", "Yes, play");
    const no = h("button", "btn sm ghost", "No thanks");
    yes.type = no.type = "button";
    row.append(yes, no);
    ask.append(mascot, bubble, row);

    no.addEventListener("click", () => pop.remove());
    yes.addEventListener("click", () => {
      ask.replaceWith(buildMemoryGame());
    });

    pop.append(head, ask);
    document.body.appendChild(pop);
    return pop;
  }

  function start(el, kind) {
    if (!el) return { stop() {} };
    const popup = spawnWaitNudge();
    const steps = STEPS[kind] || STEPS.email;
    let elapsed = 0;

    el.replaceChildren();
    el.classList.add("wait-panel");
    el.setAttribute("aria-live", "off");

    const head = h("div", "wait-head");
    const time = h("span", "wait-time", "0s");
    head.append(h("strong", null, "Working on it"), time);

    const list = h("ol", "wait-steps");
    const items = steps.map(([, label]) => { const li = h("li", null, label); list.append(li); return li; });
    const note = h("p", "tiny muted wait-note", "Steps are estimated from typical timings. Your result appears here as soon as it's ready.");
    el.append(head, list, note);

    function paintSteps() {
      let cur = 0;
      steps.forEach(([at], i) => { if (elapsed >= at) cur = i; });
      items.forEach((li, i) => { li.className = i < cur ? "done" : i === cur ? "now" : ""; });
    }

    paintSteps();
    const timer = setInterval(() => {
      elapsed++;
      time.textContent = fmt(elapsed);
      paintSteps();
    }, 1000);

    return { stop() { clearInterval(timer); el.replaceChildren(); popup?.remove(); } };
  }

  window.kaamWait = { start };
})();
