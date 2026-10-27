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

  // big warning sign (like a road sign) on the left of the main page,
  // how long there's no data written inside it
  function showStale(text, short) {
    if (!document.getElementById("mode-badge")) return; // main page only
    if (!text) {
      if (staleEl) staleEl.style.display = "none";
      return;
    }
    if (!staleEl) {
      const style = document.createElement("style");
      style.textContent =
        "@keyframes stale-pulse{0%,100%{opacity:1}50%{opacity:.6}}" +
        ".stale-sign{position:fixed;left:24px;bottom:44px;z-index:3000;pointer-events:none;" +
        "width:min(160px,30vh);filter:drop-shadow(0 3px 8px rgba(0,0,0,.65));animation:stale-pulse 2s ease-in-out infinite}" +
        ".stale-sign svg{display:block;width:100%;height:auto}";
      document.head.appendChild(style);
      staleEl = document.createElement("div");
      staleEl.className = "stale-sign";
      staleEl.setAttribute("role", "alert");
      staleEl.innerHTML =
        '<svg viewBox="0 0 100 90">' +
        '<path d="M50 5 L96 85 L4 85 Z" fill="#fde047" stroke="#dc2626" stroke-width="7" stroke-linejoin="round"/>' +
        '<rect x="45" y="24" width="10" height="27" rx="4" fill="#111"/>' +
        '<circle cx="50" cy="58" r="5.5" fill="#111"/>' +
        '<text x="50" y="77" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" font-weight="800" fill="#111"></text></svg>';
      document.body.appendChild(staleEl);
    }
    staleEl.querySelector("text").textContent = short;
    staleEl.setAttribute("aria-label", text);
    staleEl.title = text;
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
      // fresh data: the sign shows what the controller found (flame-out,
      // overheating, a missing sensor, a module error), the first one inside it
      const problems = (st && st.problems) || [];
      if (problems.length) {
        const more = problems.length > 1 ? ` +${problems.length - 1}` : "";
        showStale(problems.map((p) => p.text).join("\n"), problems[0].short + more);
      } else {
        showStale(null);
      }
      return;
    }
    const since = lastTs !== null ? new Date(lastTs * 1000) : new Date(lastChangeMs);
    const mins = Math.max(1, Math.round((ageS !== null && ageS > silentS ? ageS : silentS) / 60));
    const hhmm = since.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
    const short = mins < 60 ? `${mins} min` : mins < 48 * 60 ? `${Math.round(mins / 60)} h` : `${Math.round(mins / 1440)} dni`;
    showStale(`Brak nowych danych od ${hhmm} (${mins} min)`, short);
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

