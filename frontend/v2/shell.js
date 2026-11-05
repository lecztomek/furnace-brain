// New GUI (v2) - the frame of the sub-pages: the header (title, status sign, outside, clock, ☰),
// the menu, and fitting the 1200x704 drawing to the screen. The page content stays the current
// GUI's (same element ids, same scripts) - only the look is new.
(function () {
  "use strict";
  const stage = document.getElementById("stage");
  const title = stage.dataset.title || "";
  const here = location.pathname.split("/").pop() || "index.html";
  const DAY_NAMES = ["Nd", "Pon", "Wt", "Śr", "Czw", "Pt", "Sob"];
  const MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];

  const ICONS = {
    home: '<path d="M3 11 12 4l9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    settings: '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    stats: '<path d="M5 20v-6M10 20V9M15 20v-9M20 20V4"/>',
    service: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z"/>',
    logs: '<path d="M6 3h9l4 4v14H6z"/><path d="M9 11h7M9 15h7M9 7h3"/>',
    manual: '<path d="M8 13V5a2 2 0 0 1 4 0v6M12 11V4a2 2 0 0 1 4 0v7M16 11V7a2 2 0 0 1 4 0v7a7 7 0 0 1-7 7h-1a7 7 0 0 1-6-3.5L4 14a2 2 0 0 1 3.4-2L8 13"/>',
    back: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
  };
  // pages already made in the new look link here, the rest to the current GUI
  const MENU = [
    ["Przegląd", "index.html", "home"],
    ["Ustawienia", "settings.html", "settings"],
    ["Statystyki", "stats.html", "stats"],
    ["Serwis", "service.html", "service"],
    ["Logi", "logs.html", "logs"],
    ["Ręczne", "manual.html", "manual"],
    ["Stary panel", "../index.html", "back"],
  ];
  const icon = (k, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round">${ICONS[k]}</svg>`;

  stage.insertAdjacentHTML("afterbegin", `
    <svg class="wedge" style="left:0;bottom:0" width="110" height="200" viewBox="0 0 110 200"><path d="M0 60 L100 0 L30 200 L0 200Z" fill="#f0c033" opacity=".9"/></svg>
    <div class="head-glass"></div>
    <header>
      <div class="slashbar"></div>
      <div class="title"><b>${title}</b><span><span id="date"></span><span class="ver" id="app-ver"></span></span></div>
      <button class="sign" id="sign"><svg id="sign-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"></svg><span id="sign-text">…</span></button>
      <div class="hdr-right">
        <svg width="72" height="72" viewBox="0 0 24 24" fill="none" stroke="#e6e8eb" stroke-width="1.6"><path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.4 1.5A3.3 3.3 0 0 0 7 18z"/></svg>
        <span class="outside" id="outside">--</span>
        <span class="clock" id="clock"></span>
        <button class="burger" id="burger" aria-label="Menu"><svg viewBox="0 0 24 24" fill="none" stroke="#f2f3f5" stroke-width="2.4" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>
      </div>
    </header>`);
  stage.insertAdjacentHTML("beforeend", `
    <div class="sign-pop" id="sign-pop"></div>
    <div class="shade" id="shade"></div>
    <nav class="drawer" id="drawer">${MENU.map(([name, href, ic]) =>
      `<a href="${href}" class="${href === here ? "active" : ""}">${icon(ic)}${name}</a>`).join("")}</nav>`);

  const $ = (id) => document.getElementById(id);
  const num = (v, d = 1) => (v == null || Number.isNaN(Number(v)) ? "--" : Number(v).toFixed(d).replace(".", ","));

  // the panel and landscape screens: 1200x704; a phone held upright: 720 wide, as tall as the screen
  function fit() {
    const portrait = innerHeight > innerWidth * 1.15;
    // sub-pages are lists and tables: on a phone drawn less shrunk than the main screen
    const PW = 540;
    const ratio = innerHeight / innerWidth;
    let W = portrait ? PW : 1200;
    let H = portrait ? Math.round(PW * ratio) : Math.round(1200 * ratio);
    if (!portrait && H < 704) { H = 704; W = Math.round(704 / ratio); }
    stage.classList.toggle("portrait", portrait);
    stage.style.width = `${W}px`;
    stage.style.height = `${H}px`;
    const k = Math.min(innerWidth / W, innerHeight / H);
    stage.style.transform = `translate(${(innerWidth - W * k) / 2}px, ${(innerHeight - H * k) / 2}px) scale(${k})`;
  }
  addEventListener("resize", fit);
  fit();

  const toggleMenu = () => ["drawer", "shade"].forEach((id) => $(id).classList.toggle("open"));
  $("burger").addEventListener("click", toggleMenu);
  $("shade").addEventListener("click", toggleMenu);
  $("sign").addEventListener("click", () => $("sign-pop").classList.toggle("open"));
  $("sign-pop").addEventListener("click", () => $("sign-pop").classList.remove("open"));

  function clock() {
    const d = new Date();
    $("clock").textContent = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    $("date").textContent = `${DAY_NAMES[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  }
  clock();
  setInterval(clock, 5000);

  async function state() {
    try {
      const s = await fetch("/api/state/current", { cache: "no-store" }).then((r) => r.json());
      const problems = s.problems || [], warnings = s.warnings || [];
      const sign = $("sign");
      sign.classList.toggle("bad", problems.length > 0);
      sign.classList.toggle("warn", !problems.length && warnings.length > 0);
      $("sign-icon").innerHTML = problems.length ? '<path d="M8 2.5h8L21.5 8v8L16 21.5H8L2.5 16V8z"/><path d="M12 7v6M12 16.5v.5"/>'
        : warnings.length ? '<path d="M12 3 2 20h20z"/><path d="M12 9.5v5M12 17.5v.5"/>' : '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>';
      const first = problems[0] || warnings[0];
      $("sign-text").textContent = first ? (first.short || (problems.length ? "PROBLEM" : "UWAGA")) : "OK";
      $("sign-pop").innerHTML = [...problems.map((p) => ["bad", p]), ...warnings.map((p) => ["warn", p])]
        .map(([cls, p]) => `<p class="${cls}"><b>${p.short || ""}</b> ${p.text || ""}</p>`).join("") || "<p>Wszystko w porządku.</p>";
      const out = (s.sensors || {}).outside_temp;
      $("outside").textContent = out == null ? "--" : `${num(out)} °C`;
      const app = s.app || {};
      $("app-ver").textContent = app.version ? ` • v${app.version}` : "";
    } catch (err) {
      $("sign-text").textContent = "BRAK POŁĄCZENIA";
      $("sign").classList.add("bad");
    }
  }
  state();
  setInterval(state, 5000);
})();
