document.addEventListener("DOMContentLoaded", () => {
  const timeEl = document.getElementById("side-clock-time");
  if (!timeEl) return;

  // --- Virtual clock (symulowany czas z backendu) ---
  let clockOffsetMs = 0;

  // after an update (new version on the backend) the page reloads itself,
  // so the kiosk screen never keeps showing the old GUI
  let loadedVersion = null;

  function reloadOnNewVersion(st) {
    const version = st && st.app && st.app.version;
    if (!version) return;
    if (loadedVersion === null) {
      loadedVersion = version;
      return;
    }
    const a = document.activeElement;
    const typing = a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);
    if (version !== loadedVersion && !typing) window.location.reload();
  }

  // warning sign "Brak nowych danych" when the control loop's timestamp stops changing
  // (backend stuck or down; in the remote view: the Pi stopped sending)
  const STALE_S = window.FURNACE_VIEWER_DB ? 90 : 30;
  let lastTs = null;
  let lastChangeMs = Date.now();
  let staleEl = null;

  // big warning sign (like a road sign) on the left of the main page; a tap
  // opens a popup with the list of problems (no new data, flame-out, ...)
  let problemsNow = [];
  let popupEl = null;

  function renderPopup() {
    if (!popupEl) return;
    const list = popupEl.querySelector(".problems-list");
    list.innerHTML = "";
    (problemsNow.length ? problemsNow : ["Brak problemów"]).forEach((t) => {
      const li = document.createElement("li");
      li.textContent = t;
      list.appendChild(li);
    });
  }

  function openPopup() {
    if (!popupEl) {
      popupEl = document.createElement("div");
      popupEl.className = "problems-popup";
      popupEl.innerHTML =
        '<div class="problems-card" role="dialog" aria-label="Problemy">' +
        '<div class="problems-head"><span>⚠ Problemy</span><button type="button" class="problems-close" aria-label="Zamknij">✕</button></div>' +
        '<ul class="problems-list"></ul></div>';
      popupEl.addEventListener("click", (e) => {
        if (e.target === popupEl || e.target.closest(".problems-close")) popupEl.style.display = "none";
      });
      document.body.appendChild(popupEl);
    }
    renderPopup();
    popupEl.style.display = "flex";
  }

  function showProblems(list) {
    problemsNow = list;
    renderPopup();
    if (!document.getElementById("mode-badge")) return; // main page only
    if (!list.length) {
      if (staleEl) staleEl.style.display = "none";
      return;
    }
    if (!staleEl) {
      const style = document.createElement("style");
      style.textContent =
        "@keyframes stale-pulse{0%,100%{opacity:1}50%{opacity:.6}}" +
        ".stale-sign{position:fixed;left:24px;bottom:44px;z-index:3000;cursor:pointer;" +
        "width:min(120px,24vh);filter:drop-shadow(0 3px 8px rgba(0,0,0,.65));animation:stale-pulse 2s ease-in-out infinite}" +
        ".stale-sign svg{display:block;width:100%;height:auto}" +
        ".problems-popup{position:fixed;inset:0;z-index:4000;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.55)}" +
        ".problems-card{background:#111a22;border:1px solid #2c3b47;border-radius:16px;padding:14px 18px;max-width:min(560px,92vw);max-height:80vh;overflow:auto;color:#f9fafb}" +
        ".problems-head{display:flex;justify-content:space-between;align-items:center;font-size:22px;font-weight:800;color:#fde047;margin-bottom:8px}" +
        ".problems-close{background:none;border:0;color:#f9fafb;font-size:26px;cursor:pointer;padding:4px 10px}" +
        ".problems-list{margin:0;padding-left:20px;font-size:18px;line-height:1.4}.problems-list li{margin:6px 0}";
      document.head.appendChild(style);
      staleEl = document.createElement("div");
      staleEl.className = "stale-sign";
      staleEl.setAttribute("role", "button");
      staleEl.setAttribute("aria-label", "Problemy - pokaż listę");
      staleEl.innerHTML =
        '<svg viewBox="0 0 100 90">' +
        '<path d="M50 5 L96 85 L4 85 Z" fill="#fde047" stroke="#dc2626" stroke-width="7" stroke-linejoin="round"/>' +
        '<rect x="45" y="27" width="10" height="33" rx="4" fill="#111"/>' +
        '<circle cx="50" cy="71" r="6" fill="#111"/></svg>';
      staleEl.addEventListener("click", openPopup);
      document.body.appendChild(staleEl);
    }
    staleEl.title = list.join("\n");
    staleEl.style.display = "block";
  }

  function checkStale(st) {
    const now = Date.now();
    if (st && typeof st.ts === "number" && st.ts !== lastTs) {
      lastTs = st.ts;
      lastChangeMs = now;
    }
    const silentS = (now - lastChangeMs) / 1000;
    // data older than 5 min already at page load (e.g. the Pi stopped long ago)
    const ageS = typeof lastTs === "number" ? now / 1000 - lastTs : null;
    if (silentS < STALE_S && !(ageS !== null && ageS > 300)) {
      // fresh data: what the controller found (flame-out, overheating, a missing sensor, a module error)
      showProblems(((st && st.problems) || []).map((p) => p.text));
      return;
    }
    const since = lastTs !== null ? new Date(lastTs * 1000) : new Date(lastChangeMs);
    const mins = Math.max(1, Math.round((ageS !== null && ageS > silentS ? ageS : silentS) / 60));
    const hhmm = since.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
    showProblems([`Brak nowych danych od ${hhmm} (${mins} min) - sterownik nie odpowiada albo nie wysyła danych`]);
  }

  async function syncClockOffset() {
    try {
      const st = await fetch("/api/state/current").then((r) => r.json());
      checkStale(st);
      reloadOnNewVersion(st);
      if (st && typeof st.ts === "number") {
        const serverNowMs = Math.round(st.ts * 1000);
        clockOffsetMs = serverNowMs - Date.now();
      }
    } catch (e) {
      clockOffsetMs = 0; // fallback: czas przeglądarki
      checkStale(null);  // no answer at all counts as no new data
    }
  }

  function virtualNow() {
    return new Date(Date.now() + clockOffsetMs);
  }

  const updateClock = () => {
    const now = virtualNow();
    const hh = String(now.getHours()).padStart(2, "0");
    const mm = String(now.getMinutes()).padStart(2, "0");
    timeEl.textContent = `${hh}:${mm}`;
  };

  (async () => {
    await syncClockOffset();
    updateClock();

    // odświeżaj offset i zegar (symulacja może przyspieszać)
    setInterval(syncClockOffset, 10000);
    setInterval(updateClock, 30 * 1000);
  })();
});

