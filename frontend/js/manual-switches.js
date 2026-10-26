// "ŚLIMAK RĘCZNIE" and "DMUCHAWA RĘCZNIE" switches on the main page - feeding
// fuel / blowing by hand during ignition, each one alone or both together.
// Only from STOP: the first switch on goes to MANUAL with just that output
// running (pumps keep their current state), the second one joins it; when
// the last one goes off, the boiler goes back to STOP.
(function () {
  const API = "/api";
  const FAN_DEFAULT = 45; // when the blower setting can't be read

  const SWITCHES = [
    { id: "feeder-manual", onText: "podaje", isOn: (o) => !!o.feeder_on,
      patch: async (on) => ({ feeder_on: on }) },
    { id: "blower-manual", onText: "dmucha", isOn: (o) => Number(o.fan_power) > 0,
      // the blower runs at its normal speed from the settings (Dmuchawa -> Stałe obroty)
      patch: async (on) => ({ fan_power: on ? await fanPercent() : 0 }) },
  ]
    .map((s) => ({ ...s, el: document.getElementById(s.id) }))
    .filter((s) => s.el);
  if (!SWITCHES.length) return;

  let mode = null;
  let outputs = {};
  let busy = null; // the switch being changed
  let error = null;

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

  async function fanPercent() {
    try {
      const r = await call(`${API}/config/value/blower/base_fan_percent`);
      const v = Math.round(Number(r.value));
      return v > 0 && v <= 100 ? v : FAN_DEFAULT;
    } catch (e) {
      return FAN_DEFAULT;
    }
  }

  const on = (s) => mode === "MANUAL" && s.isOn(outputs);
  const anyOn = () => SWITCHES.some(on);

  function render() {
    SWITCHES.forEach((s) => {
      const isOn = on(s);
      // in MANUAL started from these switches the other one can join
      const enabled = !busy && (mode === "OFF" || (mode === "MANUAL" && anyOn()));
      const hint = s.el.querySelector(".feeder-manual__hint");
      s.el.classList.toggle("is-on", isOn);
      s.el.querySelector(".feeder-manual__knob").setAttribute("cx", isOn ? "74" : "48");
      s.el.classList.toggle("is-disabled", !enabled);
      s.el.setAttribute("aria-checked", String(isOn));
      s.el.setAttribute("aria-disabled", String(!enabled));
      if (busy === s) hint.textContent = "…";
      else if (error && error.s === s) hint.textContent = `błąd: ${error.msg}`;
      else if (isOn) hint.textContent = s.onText;
      else if (enabled) hint.textContent = "wyłączony";
      else hint.textContent = "tylko w STOP";
    });
  }

  async function toggle(s) {
    if (busy) return;
    const wasOn = on(s);
    const othersOn = SWITCHES.some((x) => x !== s && on(x));
    if (!wasOn && !(mode === "OFF" || (mode === "MANUAL" && othersOn))) return;

    busy = s;
    error = null;
    render();
    try {
      const patch = await s.patch(!wasOn);
      if (!wasOn && mode === "OFF") {
        await setMode("MANUAL");
        await setManual({
          feeder_on: false,
          fan_power: 0,
          mixer_open_on: false,
          mixer_close_on: false,
          pump_co_on: !!outputs.pump_co_on,
          pump_cwu_on: !!outputs.pump_cwu_on,
          ...patch,
        });
        mode = "MANUAL";
      } else if (!wasOn) {
        await setManual(patch);
      } else {
        await setManual(patch);
        if (!othersOn) {
          await setMode("OFF");
          mode = "OFF";
        }
      }
      // show the new position right away, the next state poll confirms it
      outputs = { ...outputs, ...patch };
    } catch (err) {
      console.error(err);
      error = { s, msg: err?.message || String(err) };
    }
    busy = null;
    render();
  }

  SWITCHES.forEach((s) => s.el.addEventListener("click", () => toggle(s)));

  // state from the main page polling (panel-main.js)
  document.addEventListener("furnace:state", (e) => {
    const state = e.detail || {};
    mode = state.mode || null;
    outputs = state.outputs || {};
    if (!busy) render();
  });

  render();
})();
