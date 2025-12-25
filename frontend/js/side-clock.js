document.addEventListener("DOMContentLoaded", () => {
  const timeEl = document.getElementById("side-clock-time");
  if (!timeEl) return;

  // --- Virtual clock (symulowany czas z backendu) ---
  let clockOffsetMs = 0;

  async function syncClockOffset() {
    try {
      const st = await fetch("/api/state/current").then((r) => r.json());
      if (st && typeof st.ts === "number") {
        const serverNowMs = Math.round(st.ts * 1000);
        clockOffsetMs = serverNowMs - Date.now();
      }
    } catch (e) {
      clockOffsetMs = 0; // fallback: czas przeglądarki
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

