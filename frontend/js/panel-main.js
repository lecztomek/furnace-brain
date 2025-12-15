// main.js
const STATE_API_BASE = "http://127.0.0.1:8000/api/state";

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

    // status na sukces (jeśli chcesz, możesz to usunąć żeby nie spamowało)
    FurnaceUI.ui.setStatus(`OK${stamp()}`);
  } catch (err) {
    if (err?.name !== "AbortError") {
      console.error("Nie udało się pobrać stanu kotła:", err);
      FurnaceUI.ui.setStatus(`Błąd komunikacji z serwerem${stamp()}`);
    }
  } finally {
    inFlight = false;
  }
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

  const modeDisplay = state.mode_display || state.mode || "nieznany";
  setIfChanged(prev, "mode", modeDisplay, FurnaceUI.ui.setMode);

  const power = (outputs.power_percent != null) ? Math.round(outputs.power_percent) : 0;
  setIfChanged(prev, "power", power, FurnaceUI.power.setPercent);

  // --- alarm/status tylko jeśli się zmieniło ---
  const alarmText = state.alarm_active
    ? `ALARM: ${state.alarm_message || "Nieznany błąd"}`
    : null;

  if (prev.alarm !== alarmText) {
    prev.alarm = alarmText;
    if (alarmText) FurnaceUI.ui.setStatus(alarmText);
  }
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

document.addEventListener("DOMContentLoaded", () => {
  startPolling(5000);
});
