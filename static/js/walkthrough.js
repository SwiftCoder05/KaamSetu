// Landing page "how it works" walkthrough: click a step, or let it auto-advance.
(function () {
  const steps = document.querySelectorAll(".wt-step");
  const img = document.getElementById("wt-img");
  if (!steps.length || !img) return;

  let active = 0;
  let timer = null;

  function show(i) {
    active = i;
    steps.forEach((s, j) => {
      s.classList.toggle("active", j === i);
      s.setAttribute("aria-selected", j === i ? "true" : "false");
    });
    const next = steps[i].dataset.img;
    if (img.src.endsWith(next)) return;
    img.style.opacity = "0";
    setTimeout(() => {
      img.src = next;
      img.style.opacity = "1";
    }, 150);
  }

  function restart() {
    clearInterval(timer);
    timer = setInterval(() => show((active + 1) % steps.length), 4500);
  }

  steps.forEach((s, i) =>
    s.addEventListener("click", () => {
      show(i);
      restart();
    }),
  );

  img.style.transition = "opacity .15s ease";
  if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) restart();
})();
