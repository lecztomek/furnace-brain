// "ŚLIMAK RĘCZNIE" switch on the main page - feeding fuel by hand during
// ignition. Only from STOP: switching on goes to MANUAL with just the feeder
// running (pumps keep their current state), switching off stops the feeder
// and goes back to STOP.
(function () {
  const API = "/api";

  const el = document.getElementById("feeder-manual");
  if (!el) return;
  const hintEl = el.querySelector(".feeder-manual__hint");

  let mode = null;
  let feederOn = false;
  let outputs = {};
  let busy = false;

  async function call(url, options = {}) {
    const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.detail?.msg || body?.detail || `${url} -> ${res.status}`);
    }
    return res.json();
  }

  const setMode = (m) => call(`${API}/state/mode/${m}`, { method: "POST" });
  const setManual = (patch) => call(`${API}/manual/outputs`, { method: "POST", body: JSON.stringify(patch) });

  function render() {
    const on = mode === "MANUAL" && feederOn;
    const enabled = !busy && (mode === "OFF" || on);
    el.classList.toggle("is-on", on);
    el.classList.toggle("is-disabled", !enabled);
    el.setAttribute("aria-checked", String(on));
    el.setAttribute("aria-disabled", String(!enabled));
    if (busy) hintEl.textContent = "…";
    else if (on) hintEl.textContent = "podaje";
    else if (mode === "OFF") hintEl.textContent = "wyłączony";
    else hintEl.textContent = "tylko w STOP";
  }

  async function toggle() {
    if (busy) return;
    const on = mode === "MANUAL" && feederOn;
    if (!on && mode !== "OFF") return;

    busy = true;
    render();
    try {
      if (!on) {
        await setMode("MANUAL");
        await setManual({
          feeder_on: true,
          fan_power: 0,
          mixer_open_on: false,
          mixer_close_on: false,
          pump_co_on: !!outputs.pump_co_on,
          pump_cwu_on: !!outputs.pump_cwu_on,
        });
        mode = "MANUAL";
        feederOn = true;
      } else {
        await setManual({ feeder_on: false });
        await setMode("OFF");
        mode = "OFF";
        feederOn = false;
      }
    } catch (err) {
      console.error(err);
      hintEl.textContent = `błąd: ${err?.message || err}`;
      busy = false;
      return;
    }
    busy = false;
    render();
  }

  el.addEventListener("click", toggle);

  // state from the main page polling (panel-main.js)
  document.addEventListener("furnace:state", (e) => {
    const state = e.detail || {};
    mode = state.mode || null;
    outputs = state.outputs || {};
    feederOn = !!outputs.feeder_on;
    if (!busy) render();
  });

  render();
})();
