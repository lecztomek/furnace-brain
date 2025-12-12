// js/mode-control.js

// Jeśli router jest np. pod /api/state, zmień na "/api/state"
const CONTROL_API_BASE = "http://127.0.0.1:8000/api/state";

/**
 * Uaktualnia tekst w dolnym pasku statusu.
 */
function updateStatus(text, isError = false) {
  const el = document.getElementById("status-text");
  if (!el) return;

  el.textContent = text || "";
  if (isError) {
    el.classList.add("status-error");
  } else {
    el.classList.remove("status-error");
  }
}

/**
 * Pobiera aktualny stan kotła z backendu.
 * GET /state/current
 */
async function fetchCurrentState() {
  try {
    const res = await fetch(`${CONTROL_API_BASE}/current`);
    if (!res.ok) {
      throw new Error("HTTP " + res.status);
    }
    return await res.json();
  } catch (err) {
    console.error("fetchCurrentState error:", err);
    updateStatus("Nie mogę pobrać aktualnego stanu kotła.", true);
    return null;
  }
}

/**
 * Ustawia tryb pracy kotła.
 * POST /state/mode/{mode}
 * mode: "OFF" | "IGNITION" | "WORK" | ...
 */
async function setMode(mode) {
  try {
    updateStatus(`Ustawiam tryb: ${mode}...`);

    const res = await fetch(`${CONTROL_API_BASE}/mode/${mode}`, {
      method: "POST",
    });

    if (!res.ok) {
      let detail = "Błąd zmiany trybu.";
      try {
        const data = await res.json();
        if (data && data.detail) detail = data.detail;
      } catch (_) {
        // ignorujemy błąd parsowania JSON
      }
      updateStatus(detail, true);
      return null;
    }

    const state = await res.json();
    const label = state.mode_display || state.mode || mode;
    updateStatus(`Aktualny tryb: ${label}.`);

    // Zaktualizuj widoczność przycisków w menu
    refreshMenuModeButtons(state);
    return state;
  } catch (err) {
    console.error("setMode error:", err);
    updateStatus("Błąd połączenia z serwerem.", true);
    return null;
  }
}

/**
 * Logika widoczności przycisków ROZPALANIE / PRACA w bocznym menu.
 *
 * Wymagania:
 * - ROZPALANIE widoczne kiedy jest PRACA (mode === "WORK")
 * - PRACA widoczna kiedy jest OFF lub ROZPALANIE
 *   (mode === "OFF" || mode === "IGNITION")
 * - inne tryby (np. MANUAL) -> oba ukryte (na razie)
 */
async function refreshMenuModeButtons(existingState) {
  const igniteLink = document.querySelector('.menu-btn[data-view="ignite"]');
  const workLink = document.querySelector('.menu-btn[data-view="work"]');

  const state = existingState || (await fetchCurrentState());
  if (!state) return;

  const mode = state.mode; // "OFF", "IGNITION", "WORK", "MANUAL"...

  const showIgnite = mode === "WORK";
  const showWork = mode !== "WORK";
  if (igniteLink) igniteLink.style.display = showIgnite ? "" : "none";
  if (workLink) workLink.style.display = showWork ? "" : "none";
}

/**
 * Podpięcie handlerów pod przyciski w menu po załadowaniu DOM.
 */
document.addEventListener("DOMContentLoaded", () => {
  const igniteLink = document.querySelector('.menu-btn[data-view="ignite"]');
  const workLink = document.querySelector('.menu-btn[data-view="work"]');
  const stopLink = document.querySelector('.menu-btn[data-view="emergency"]');

  // 🔥 ROZPALANIE -> tryb IGNICTION
  if (igniteLink) {
    igniteLink.addEventListener("click", async (e) => {
      e.preventDefault();
      await setMode("IGNITION");
      // refreshMenuModeButtons() wywoła się wewnątrz setMode()
    });
  }

  // 🟢 PRACA -> tryb WORK
  if (workLink) {
    workLink.addEventListener("click", async (e) => {
      e.preventDefault();
      await setMode("WORK");
    });
  }

  // ⚠️ STOP -> tryb OFF
  if (stopLink) {
    stopLink.addEventListener("click", async (e) => {
      e.preventDefault();
      const state = await setMode("OFF");

      // Jeśli chcesz tylko OFF bez przejścia na emergency.html – usuń ten blok:
      if (state) {
        const href = stopLink.getAttribute("href") || "emergency.html";
        window.location.href = href;
      }
    });
  }

  // Na starcie pobierz stan i ustaw widoczność przycisków zgodnie z trybem
  refreshMenuModeButtons();
});
