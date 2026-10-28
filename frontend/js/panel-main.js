// main.js
const STATE_API_BASE = "/api/state";

let timer = null;
let inFlight = false;
let controller = null;

// cache poprzedniego stanu (do porównywania i ograniczania DOM update)
let prev = {
  temps: {},
  outputs: {},
  mode: null,
  power: null,
  alarm: null,
};

// prosta funkcja: aktualizuj tylko jeśli wartość się zmieniła
function setIfChanged(bucket, key, value, setter) {
  if (bucket[key] !== value) {
    bucket[key] = value;
    setter(value);
  }
}

function fmtTime(d = new Date()) {
  // 12:34:56 (lokalny czas przeglądarki)
  return d.toLocaleTimeString("pl-PL", { hour12: false });
}

async function fetchState() {
  if (inFlight) return;
  inFlight = true;

  if (controller) controller.abort();
  controller = new AbortController();

  const stamp = () => ` • ostatni polling: ${fmtTime()}`;

  try {
    const response = await fetch(`${STATE_API_BASE}/current`, {
      headers: { "Accept": "application/json" },
      signal: controller.signal,
      cache: "no-store",
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const data = await response.json();
    updateUIFromState(data);
    showAppInfo(data.app);
    showOutside(data.sensors?.outside_temp);
    document.dispatchEvent(new CustomEvent("furnace:state", { detail: data }));

    // status na sukces (jeśli chcesz, możesz to usunąć żeby nie spamowało)
    // problems found by the controller (also in the warning sign) in words
    const problems = (data.problems || []).map((p) => p.text);
    FurnaceUI.ui.setStatus(problems.length ? `⚠ problemy: ${problems.length} - dotknij wykrzyknika${stamp()}` : `OK${stamp()}`);
  } catch (err) {
    if (err?.name !== "AbortError") {
      console.error("Nie udało się pobrać stanu kotła:", err);
      FurnaceUI.ui.setStatus(`Błąd komunikacji z serwerem${stamp()}`);
    }
  } finally {
    inFlight = false;
  }
}

// top-left corner: "wersja 1.0.0" / "działa od 26.10 08:45 (2 h 13 min)"
function showAppInfo(app) {
  const versionEl = document.getElementById("app-version");
  const uptimeEl = document.getElementById("app-uptime");
  if (!versionEl || !uptimeEl || !app) return;
  versionEl.textContent = app.version ? `wersja ${app.version}` : "";
  document.getElementById("app-info")?.classList.toggle("is-empty", !app.version);
  if (app.started_at) {
    const started = new Date(app.started_at * 1000);
    const date = started.toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit" });
    const time = started.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit", hour12: false });
    uptimeEl.textContent = `działa od ${date} ${time} (${fmtUptime(Date.now() / 1000 - app.started_at)})`;
  }
}

// window on the diagram: outside temperature + sun or moon by the local hour
function showOutside(temp) {
  const textEl = document.getElementById("outside-temp-text");
  const group = document.getElementById("outside-weather");
  if (!textEl || !group) return;
  textEl.textContent = temp == null ? "--°C" : `${Math.round(temp)}°C`;
  const hour = new Date().getHours();
  group.classList.toggle("is-night", hour < 6 || hour >= 19);
}

function fmtUptime(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min`;
}

let raf = null;
let queued = null;

function updateUIFromState(state) {
  // jeśli state jest obiektem, który potem mutujesz gdzie indziej:
  // queued = structuredClone(state); // albo {...state} jeśli płytko wystarczy
  queued = state;

  if (raf) return;

  raf = requestAnimationFrame(() => {
    raf = null;
    if (!queued) return;
    applyState(queued);
    queued = null;
  });
}

function applyState(state) {
  const sensors = state.sensors || {};
  const outputs = state.outputs || {};

  // --- temperatury (zaokrąglij, żeby nie trzepać DOM od 0.01°C) ---
  const round1 = (v) => (v == null ? null : Math.round(v * 10) / 10);

  setIfChanged(prev.temps, "boiler",   round1(sensors.boiler_temp),     FurnaceUI.temps.setFurnace);
  setIfChanged(prev.temps, "rads",     round1(sensors.radiators_temp),  FurnaceUI.temps.setRadiators);

  const mixerTemp = sensors.mixer_temp != null ? sensors.mixer_temp : sensors.return_temp;
  setIfChanged(prev.temps, "mixer",    round1(mixerTemp),               FurnaceUI.temps.setMixer);

  setIfChanged(prev.temps, "auger",    round1(sensors.hopper_temp),     FurnaceUI.temps.setAuger);
  setIfChanged(prev.temps, "exhaust",  round1(sensors.flue_gas_temp),   FurnaceUI.temps.setExhaust);

  // --- wyjścia ---
  setIfChanged(prev.outputs, "pump_co",  !!outputs.pump_co_on,  (v) => FurnaceUI.pumps.set("co", v));
  setIfChanged(prev.outputs, "pump_cwu", !!outputs.pump_cwu_on, (v) => FurnaceUI.pumps.set("cwu", v));

  setIfChanged(prev.outputs, "feeder",   !!outputs.feeder_on,   FurnaceUI.auger.set);

  // fan_power: normalizuj do int, żeby nie zmieniać co chwilę o ułamki
  const fan = outputs.fan_power == null ? 0 : Math.round(outputs.fan_power);
  setIfChanged(prev.outputs, "fan", fan, FurnaceUI.blower.setPower);

  const modeDisplay = modeLabel(state);
  setIfChanged(prev, "mode", modeDisplay, FurnaceUI.ui.setMode);

  const power = (outputs.power_percent != null) ? Math.round(outputs.power_percent) : 0;
  setIfChanged(prev, "power", power, FurnaceUI.power.setPercent);

  // heat output from the coal fed (calorific value x feeder time, last 10 min)
  const kw = state.power_kw == null ? "-- kW" : `${state.power_kw.toFixed(1).replace(".", ",")} kW`;
  setIfChanged(prev, "power_kw", kw, (v) => {
    const el = document.getElementById("power-kw-text");
    if (el) el.textContent = v;
  });

  // coal burned now (same 10 min window)
  const kgph = state.coal_kgph == null ? "-- kg/h" : `${state.coal_kgph.toFixed(1).replace(".", ",")} kg/h`;
  setIfChanged(prev, "coal_kgph", kgph, (v) => {
    const el = document.getElementById("coal-kgph-text");
    if (el) el.textContent = v;
  });

  // --- alarm/status tylko jeśli się zmieniło ---
  const alarmText = state.alarm_active
    ? `ALARM: ${state.alarm_message || "Nieznany błąd"}`
    : null;

  if (prev.alarm !== alarmText) {
    prev.alarm = alarmText;
    if (alarmText) FurnaceUI.ui.setStatus(alarmText);
  }
}

// short names of the power regulators shown next to the WORK mode
const POWER_SOURCE_LABELS = {
  power_work: "PID",
};

// e.g. "praca (PID)" - every enabled power regulator
function modeLabel(state) {
  const base = state.mode_display || state.mode || "nieznany";
  if (state.mode !== "WORK") return base;
  const sources = (state.power_regulators || []).map((id) => POWER_SOURCE_LABELS[id] || id);
  return sources.length ? `${base} (${sources.join(", ")})` : base;
}

function startPolling(ms = 5000) {
  if (window.__pollerRunning) return;  
  window.__pollerRunning = true;

  stopPolling();
  const loop = async () => {
    await fetchState();
    timer = setTimeout(loop, ms);
  };
  loop();
}

function stopPolling() {
  if (timer) { clearTimeout(timer); timer = null; }
  if (controller) controller.abort();
  window.__pollerRunning = false;      
}

document.addEventListener("visibilitychange", () => {
  // 3) pauza gdy niewidoczne (na RPi to duża ulga)
  if (document.hidden) stopPolling();
  else startPolling(5000);
});


function forceAllOnAtStart() {
  // UI na start: wszystko ON (optymistycznie)
  prev.outputs.pump_co  = true;
  prev.outputs.pump_cwu = true;
  prev.outputs.feeder   = true;
  prev.outputs.fan      = 100;

  prev.power = 100;
  prev.mode  = "START";

  FurnaceUI.pumps.set("co", true);
  FurnaceUI.pumps.set("cwu", true);
  FurnaceUI.auger.set(true);
  FurnaceUI.blower.setPower(100);
  FurnaceUI.power.setPercent(100);
  FurnaceUI.ui.setMode("START");
  FurnaceUI.ui.setStatus(`START: pompy/ślimak/dmuchawa ON • ${fmtTime()}`);
}

document.addEventListener("DOMContentLoaded", () => {
  forceAllOnAtStart(); 
  startPolling(5000);
});

// radiators setpoint (mixer target) under the measured temperature - so a
// change made in the popup is visible at once, before the water warms up.
// With the heating curve on it is the target computed from the outside temperature.
(function () {
  const el = document.getElementById("radiators-set-text");
  if (!el) return;
  const fmt = (v) => String(Math.round(v * 10) / 10).replace(".", ",");
  async function refresh() {
    try {
      const r = await fetch("/api/state/current", { cache: "no-store" });
      if (!r.ok) return;
      const rad = (await r.json()).radiators || {};
      showMixerMoves(rad);
      showOutsideAvg(rad);
      let v = Number(rad.target);
      if (rad.target == null || isNaN(v)) {
        // mixer not running yet - show the configured value
        const c = await fetch("/api/config/value/mixer/target_temp", { cache: "no-store" });
        if (!c.ok) return;
        v = Number((await c.json()).value);
      }
      if (!isNaN(v)) el.textContent = `${rad.curve_active ? "krzywa" : "zadana"} ${fmt(v)}°C`;
    } catch (e) { /* keep the last value */ }
  }
  // under the outside temperature: the average the heating curve uses and its window ("śr. 4,2° / 12 h")
  function showOutsideAvg(rad) {
    const t = document.getElementById("outside-avg-text");
    if (!t) return;
    const avg = Number(rad.outside_avg);
    t.textContent = rad.outside_avg == null || isNaN(avg) ? "śr. --"
      : `śr. ${fmt(avg)}° z ${String(rad.outside_avg_h ?? "").replace(".", ",")} h`;
  }

  // mixing valve: net seconds of opening (+) / closing (-) in the last 30 min
  function showMixerMoves(rad) {
    const g = document.getElementById("mixer-moves");
    const t = document.getElementById("mixer-moves-text");
    if (!g || !t) return;
    const sec = Math.round(Number(rad.moves_30m_s));
    if (rad.moves_30m_s == null || isNaN(sec)) { t.textContent = "--"; g.setAttribute("class", "mixer-moves"); return; }
    t.textContent = `${sec > 0 ? "+" : sec < 0 ? "−" : ""}${Math.abs(sec)}s`;
    const dir = sec > 0 ? " is-open" : sec < 0 ? " is-close" : "";
    g.setAttribute("class", `mixer-moves${dir}`);
  }

  refresh();
  setInterval(refresh, 10000);
  // right after saving in the popup
  document.addEventListener("click", (e) => {
    if (e.target && e.target.closest && e.target.closest("#temp-save")) setTimeout(refresh, 1500);
  });
})();
