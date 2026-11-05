// New GUI (v2) - Ustawienia as tiles: a tile per module; a tap shows its settings (the current
// GUI's script js/settings-config.js builds them - its tab buttons stay hidden and are clicked
// from here), "Wszystkie ustawienia" goes back to the tiles.
(function () {
  "use strict";
  const page = document.querySelector(".settings-page");
  const tiles = document.getElementById("set-tiles");
  const back = document.getElementById("set-back");
  const headerTabs = document.getElementById("config-tabs-header");

  const P = (d) => `<path d="${d}"/>`;
  const ICONS = {
    mode: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
    flameout: P("M12 3c3 4 6 6 6 11a6 6 0 0 1-12 0c0-3 2-5 3-6 0 2 1 3 2 3 0-3-1-5 1-8z") + P("M4 4l16 16"),
    power_ignition: P("M12 3c3 4 6 6 6 11a6 6 0 0 1-12 0c0-3 2-5 3-6 0 2 1 3 2 3 0-3-1-5 1-8z"),
    power_floor: P("M3 20h18M5 16l5-5 4 3 5-7") ,
    power_work: P("M13 2 4 14h7l-1 8 9-12h-7z"),
    blower: '<circle cx="12" cy="12" r="2"/>' + P("M12 10c1-5 6-7 7-4s-3 5-7 4zM14 12c5 1 7 6 4 7s-5-3-4-7zM12 14c-1 5-6 7-7 4s3-5 7-4zM10 12c-5-1-7-6-4-7s5 3 4 7z"),
    feeder: P("M2 12h20M5 8l3 8M10 8l3 8M15 8l3 8"),
    curve: P("M3 4v16h18") + P("M6 8c4 0 6 3 8 6s4 3 6 3"),
    mixer: P("M3 7v10l9-5zM21 7v10l-9-5z") + P("M12 12V5M9 4h6"),
    pump_cwu: P("M4 8h9a4 4 0 0 1 4 4v2M9 4v4M5 4h8") + P("M17 17c-1.5 2-2 3-2 4a2 2 0 0 0 4 0c0-1-.5-2-2-4z"),
    pump_co: P("M4 5v14M8 5v14M12 5v14M16 5v14M20 5v14M3 8h18M3 16h18"),
    stats: P("M5 20v-6M10 20V9M15 20v-9M20 20V4"),
    manual: P("M8 13V5a2 2 0 0 1 4 0v6M12 11V4a2 2 0 0 1 4 0v7M16 11V7a2 2 0 0 1 4 0v7a7 7 0 0 1-7 7h-1a7 7 0 0 1-6-3.5L4 14a2 2 0 0 1 3.4-2L8 13"),
    overheat: P("M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0z") + P("M12 11v6M19 4v5M19 12v.5"),
    safety: P("M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z") + P("m9 12 2 2 4-4"),
    weather: P("M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11.4 1.5A3.3 3.3 0 0 0 7 18z"),
    remote_view: '<circle cx="12" cy="12" r="9"/>' + P("M3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18"),
    history: P("M3 12a9 9 0 1 0 3-6.7L3 8") + P("M3 3v5h5M12 7v5l3 2"),
    eventlog: P("M6 3h9l4 4v14H6z") + P("M9 11h7M9 15h7M9 7h3"),
    hardware: '<rect x="6" y="6" width="12" height="12" rx="2"/>' + P("M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"),
    settings_log: P("M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12") + '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
    trend: P("M3 17l6-6 4 4 8-8") + P("M15 7h6v6"),
    notify: P("M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9") + P("M13.7 21a2 2 0 0 1-3.4 0"),
  };
  const DEFAULT_ICON = '<circle cx="12" cy="12" r="3"/>' + P("M12 2v3M12 19v3M2 12h3M19 12h3");

  function show(detail) {
    page.classList.toggle("detail", detail);
    tiles.scrollTop = 0;
  }

  async function build() {
    let modules = [];
    try { modules = await fetch("/api/config/modules", { cache: "no-store" }).then((r) => r.json()); } catch (e) { return; }
    tiles.innerHTML = modules.map((m) => `
      <button class="set-tile" data-module-id="${m.id}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICONS[m.id] || DEFAULT_ICON}</svg>
        <span>${m.name || m.id}</span>
      </button>`).join("");
    tiles.querySelectorAll(".set-tile").forEach((t) => t.addEventListener("click", () => {
      const tab = headerTabs.querySelector(`.config-tab-button[data-module-id="${t.dataset.moduleId}"]`);
      if (!tab) return;            // the settings are still being read
      tab.click();
      document.getElementById("set-name").textContent = t.querySelector("span").textContent;
      show(true);
    }));
  }

  back.addEventListener("click", () => show(false));
  build();
})();
