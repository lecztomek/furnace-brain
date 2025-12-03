// Prosty zegar w górnym pasku
function updateClock() {
  const el = document.getElementById("clock");
  if (!el) return;
  const now = new Date();
  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  el.textContent = `${hh}:${mm}`;
}

// Obsługa przycisków menu (podświetlanie + status)
function setupMenu() {
  const buttons = document.querySelectorAll(".menu-btn");
  const status = document.getElementById("status-text");

  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.view;

      // aktywny przycisk
      buttons.forEach((b) => b.classList.remove("active"));
      if (!btn.classList.contains("danger")) {
        btn.classList.add("active");
      }

      // prosta reakcja – później podłączysz tu logikę przełączania widoków
      switch (view) {
        case "history":
          status.textContent = "Widok: historia pracy.";
          break;
        case "manual":
          status.textContent = "Widok: tryb ręczny.";
          break;
        case "ignite":
          status.textContent = "Widok: rozpalanie.";
          break;
        case "settings":
          status.textContent = "Widok: ustawienia.";
          break;
        case "emergency":
          status.textContent = "ALARM: STOP AWARYJNY!";
          // tutaj wywołasz backend (fetch POST /api/emergency)
          break;
        default:
          status.textContent = "System gotowy.";
      }
    });
  });
}

// Inicjalizacja po załadowaniu
document.addEventListener("DOMContentLoaded", () => {
  setupMenu();
  updateClock();
  setInterval(updateClock, 30000); // aktualizacja co 30 s
});
