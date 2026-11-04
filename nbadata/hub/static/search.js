// Search-as-you-type for every search box marked data-suggest. Without JS the boxes still work (plain form submit).
document.querySelectorAll("input[data-suggest]").forEach((input) => {
  const box = input.parentElement.querySelector(".suggest");
  let timer = null, active = -1, items = [];

  const close = () => { box.hidden = true; active = -1; };
  const highlight = () => items.forEach((a, i) => a.classList.toggle("on", i === active));

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    timer = setTimeout(async () => {
      const res = await fetch("/api/search?q=" + encodeURIComponent(q));
      if (!res.ok || input.value.trim() !== q) return;
      const hits = await res.json();
      box.replaceChildren(...hits.map((h) => {
        const a = document.createElement("a");
        a.href = h.url;
        const name = document.createElement("strong");
        name.textContent = h.name;
        const detail = document.createElement("span");
        detail.textContent = h.detail;
        a.append(name, detail);
        return a;
      }));
      items = [...box.querySelectorAll("a")];
      active = -1;
      box.hidden = items.length === 0;
    }, 150);
  });

  input.addEventListener("keydown", (e) => {
    if (box.hidden) return;
    if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); highlight(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { active = Math.max(active - 1, -1); highlight(); e.preventDefault(); }
    else if (e.key === "Enter" && active >= 0) { location.href = items[active].href; e.preventDefault(); }
    else if (e.key === "Escape") close();
  });
  input.addEventListener("blur", () => setTimeout(close, 150));
});
