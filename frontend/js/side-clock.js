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

  // "⚠ Brak nowych danych" when the control loop's timestamp stops changing
  // (backend stuck or down; in the remote view: the Pi stopped sending)
  const STALE_S = window.FURNACE_VIEWER_DB ? 90 : 30;
  let lastTs = null;
  let lastChangeMs = Date.now();
  let staleEl = null;

  function showStale(text) {
    if (!text) {
      if (staleEl) staleEl.style.display = "none";
      return;
    }
    if (!staleEl) {
      staleEl = document.createElement("div");
      staleEl.setAttribute("role", "alert");
      staleEl.style.cssText =
        "position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:3000;pointer-events:none;" +
        "background:#b91c1c;color:#fff;font:700 16px/1.2 system-ui,sans-serif;padding:8px 16px;" +
        "border-radius:999px;box-shadow:0 2px 10px rgba(0,0,0,.5);white-space:nowrap;max-width:94vw;" +
        "overflow:hidden;text-overflow:ellipsis";
      document.body.appendChild(staleEl);
    }
    staleEl.textContent = text;
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
      showStale(null);
      return;
    }
    const since = lastTs !== null ? new Date(lastTs * 1000) : new Date(lastChangeMs);
    const mins = Math.max(1, Math.round((ageS !== null && ageS > silentS ? ageS : silentS) / 60));
    const hhmm = since.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
    showStale(`⚠ Brak nowych danych od ${hhmm} (${mins} min)`);
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

