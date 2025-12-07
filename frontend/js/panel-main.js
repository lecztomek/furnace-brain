// main.js

const STATE_API_BASE = "http://127.0.0.1:8000/api/state";

async function fetchState() {
  try {
    // jeśli FastAPI ma prefix /api, to:
    const response = await fetch(`${STATE_API_BASE}/current`, {
      headers: {
        "Accept": "application/json",
      },
    });

    if (!response.ok) {
      throw new Error(`Błąd HTTP: ${response.status}`);
    }

    const data = await response.json();
    updateUIFromState(data);
  } catch (err) {
    console.error("Nie udało się pobrać stanu kotła:", err);
    FurnaceUI.ui.setStatus("Błąd komunikacji z serwerem");
  }
}

function updateUIFromState(state) {
  const sensors = state.sensors;
  const outputs = state.outputs;

  // --- temperatury ---
  // Czujniki z backendu:
  // boiler_temp, return_temp, radiators_temp, cwu_temp, flue_gas_temp,
  // hopper_temp, outside_temp, mixer_temp

  // Zakładam takie mapowanie:
  // - kocioł = boiler_temp
  // - grzejniki = radiators_temp
  // - mieszacz = mixer_temp (a jeśli brak, to fallback na return_temp)
  // - ślimak = hopper_temp (temperatura zasobnika)
  FurnaceUI.temps.setFurnace(sensors.boiler_temp);
  FurnaceUI.temps.setRadiators(sensors.radiators_temp);
  const mixerTemp = sensors.mixer_temp != null ? sensors.mixer_temp : sensors.return_temp;
  FurnaceUI.temps.setMixer(mixerTemp);
  FurnaceUI.temps.setAuger(sensors.hopper_temp);
  FurnaceUI.temps.setExhaust(sensors.flue_gas_temp);

  // --- wyjścia / pompy / dmuchawa / podajnik ---
  // outputs:
  // fan_power, feeder_on, pump_co_on, pump_cwu_on, pump_circ_on,
  // mixer_open_on, mixer_close_on, alarm_buzzer_on, alarm_relay_on

  // pompy CO i CWU
  FurnaceUI.pumps.set("co",  !!outputs.pump_co_on);
  FurnaceUI.pumps.set("cwu", !!outputs.pump_cwu_on);

  // jeśli masz też pompę cyrkulacji w UI, możesz ją tu dopiąć
  // FurnaceUI.pumps.set("cyrkulacja", !!outputs.pump_circ_on);

  // ślimak (podajnik)
  FurnaceUI.auger.set(!!outputs.feeder_on);

  // dmuchawa – zakładam, że 0–100%
  FurnaceUI.blower.setPower(outputs.fan_power || 0);
  
  const modeDisplay = state.mode_display || state.mode || "nieznany";
  FurnaceUI.ui.setMode(modeDisplay);

  // --- status / tryb / alarm ---
  if (state.alarm_active) {
    FurnaceUI.ui.setStatus(`ALARM: ${state.alarm_message || "Nieznany błąd"}`);
  }

  // --- paliwo i korekcja ---
  // Tego API na razie nie masz w backendzie, więc:
  // - możesz tu zostawić wartości „dummy”
  // - albo po prostu to usunąć, dopóki backend tego nie wystawia

  // Przykład: tymczasowe wartości lub ostatnio znane z localStorage
  // FurnaceUI.fuel.setKg(130);
  // FurnaceUI.corrections.setAugerSeconds(6);
}

document.addEventListener("DOMContentLoaded", () => {
  // pierwszy strzał do backendu
  fetchState();

  // opcjonalne odświeżanie co 5s
  setInterval(fetchState, 5000);
});
