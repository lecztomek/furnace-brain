// The new look (/v2/): the "NOWY WYGLĄD" icon on the main page asks first, then goes there;
// and when the setting Wygląd -> "nowy" is chosen, the main page opens the new look by itself
// (except when it came from the new look's "Stary panel": index.html?stary).
(function () {
  const modal = document.getElementById("gui-modal");
  const btn = document.getElementById("gui-new-btn");
  if (!modal || !btn) return;
  const go = () => {
    try { sessionStorage.removeItem("gui-stary"); } catch (e) { /* private mode */ }
    location.assign("v2/");
  };
  btn.addEventListener("click", () => modal.classList.remove("hidden"));
  modal.addEventListener("click", (e) => { if (e.target.closest("[data-gui-close]")) modal.classList.add("hidden"); });
  document.getElementById("gui-go").addEventListener("click", go);

  // "Stary panel" from the new look: stay on the old look in this window (also when going back
  // to the main page from the other old pages); a restart of the browser forgets it
  try {
    if (/[?&]stary\b/.test(location.search)) sessionStorage.setItem("gui-stary", "1");
    if (sessionStorage.getItem("gui-stary")) return;
  } catch (e) {
    if (/[?&]stary\b/.test(location.search)) return;
  }
  fetch("/api/config/value/gui/default_gui", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => { if (d && d.value === "nowy") location.replace("v2/"); })
    .catch(() => {});
})();
