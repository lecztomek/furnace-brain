// furnace-ui-utils.js
// Proste sterowanie animacjami kotła (FurnaceUI) + stany/liczby

(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);

  // --- STAN (opcjonalnie, jak chcesz tego używać z zewnątrz) ---
  const uiState = {
    pumps: { cwu: false, co: false },
    augerOn: false,
    blowerPower: 0,
    temperatures: {
      furnace: null,
      radiators: null,
      mixer: null,
      auger: null,
      exhaust: null,
    },
    fuelKg: null,
    augerCorrectionSec: null,
    mode: null,
    powerPercent: 0,
  };

  // referencje do elementów z Twojego SVG
  const els = {
    pumpCwuRotor: null,
    pumpCoRotor: null,
    augerScrew: null,
    fanRotor: null,
    blowerPowerText: null,

    cwuArrowHot: null,
    cwuArrowCold: null,
    coArrowHot: null,
    coArrowCold: null,

    modeText: null,

    fireScale: null,
    fireBaseTransform: "",

    smokeGroup: null,

    furnaceTempText: null,
    radiatorsTempText: null,
    mixerTempText: null,
    augerTempText: null,
    exhaustTempText: null,
    statusText: null,
    clockText: null,
    powerPercentText: null,

    fuelInTankText: null,
    augerCorrectionText: null,
  };

  // --- FORMATERY / POMOCNICZE ---

  function formatMode(mode) {
    if (!mode) return "TRYB: --";
    return `TRYB: ${String(mode).toUpperCase()}`;
  }

  function formatTemp(v) {
    if (v == null) return "--°C";
    return `${Math.round(v)}°C`;
  }

  function formatFuel(kg) {
    if (kg == null) return "-- kg";
    return `${Math.round(kg)} kg`;
  }

  function formatSeconds(sec) {
    if (sec == null) return "--s";
    const s = Math.round(sec);
    return `${s > 0 ? "+" : ""}${s}s`;
  }

  function clamp(num, min, max) {
    return Math.min(max, Math.max(min, num));
  }

  function tempColor(valueC) {
    if (valueC == null) return "#ccc";
    if (valueC < 30) return "#3fa9f5";
    if (valueC < 60) return "#f6c151";
    return "#e74c3c";
  }

  // --- STRZAŁKI CWU / CO ---
  // animujemy <polygon> wewnątrz grup, bo CSS jest zdefiniowany na polygonach

  function setArrowGroupActive(arrowEls, isOn) {
    arrowEls.forEach((groupEl) => {
      if (!groupEl) return;

      const poly = groupEl.querySelector("polygon") || groupEl;
      const color = groupEl.dataset.color; // "red" / "blue"

      if (isOn) {
        poly.style.animation = "";
        poly.style.fill = "";
        poly.style.stroke = "";

        if (color === "red") {
          poly.classList.add("red-arrow");
          poly.classList.remove("blue-arrow");
        } else if (color === "blue") {
          poly.classList.add("blue-arrow");
          poly.classList.remove("red-arrow");
        }
      } else {
        poly.classList.remove("red-arrow", "blue-arrow");
        poly.style.animation = "none";
        poly.style.fill = "#000";
        poly.style.stroke = "#000";
      }
    });
  }

  function setCwuArrows(isOn) {
    setArrowGroupActive([els.cwuArrowHot, els.cwuArrowCold], isOn);
  }

  function setCoArrows(isOn) {
    setArrowGroupActive([els.coArrowHot, els.coArrowCold], isOn);
  }

  // --- INICJALIZACJA REFERENCJI DO SVG ---

  function initSvgRefs() {
    const pumpCwu = $("#pump-cwu");
    const pumpCo = $("#pump-co");

    if (pumpCwu) {
      els.pumpCwuRotor =
        pumpCwu.querySelector(".pump-running") || pumpCwu.querySelector("g");
    }

    if (pumpCo) {
      els.pumpCoRotor =
        pumpCo.querySelector(".pump-running") || pumpCo.querySelector("g");
    }

    const auger = $("#auger");
    if (auger) {
      els.augerScrew = auger.querySelector(".auger-running") || $("#auger-screw");
    }

    els.fanRotor = $("#fan-rotor");
    els.blowerPowerText = $("#blower-pwn-text");

    els.cwuArrowHot = $("#cwu-arrow-hot");
    els.cwuArrowCold = $("#cwu-arrow-cold");
    els.coArrowHot = $("#co-arrow-hot");
    els.coArrowCold = $("#co-arrow-cold");

    els.modeText = $("#mode-text");

    // Ogień (skalowanie) – zachowaj bazowy transform z SVG (np. translate)
    els.fireScale = $("#fire-scale");
    if (els.fireScale) {
      els.fireBaseTransform = els.fireScale.getAttribute("transform") || "";
    }

    // Dym – jak go nie ma w DOM, to i tak nic się nie stanie
    els.smokeGroup = $("#smoke");

    els.furnaceTempText = $("#furnace-temp-text");
    els.radiatorsTempText = $("#radiators-temp-text");
    els.mixerTempText = $("#mixer-temp-text");
    els.augerTempText = $("#auger-temp--text");
    els.exhaustTempText = $("#exhaust-temp-text");
    els.statusText = $("#status-text");
    els.clockText = $("#clock");
    els.powerPercentText = $("#power-percent-text");

    els.fuelInTankText = $("#fuel-in-tank-text");
    els.augerCorrectionText = $("#auger-correction-text");

    // Na starcie — strzałki bez migania (czarne)
    setCwuArrows(false);
    setCoArrows(false);
  }

  // --- POMPY ---

  function setPumpState(pumpId, isOn) {
    const on = !!isOn;
    if (uiState.pumps[pumpId] === on) return; // early-return
    uiState.pumps[pumpId] = on;

    const rotor =
      pumpId === "cwu" ? els.pumpCwuRotor : pumpId === "co" ? els.pumpCoRotor : null;

    if (rotor) rotor.classList.toggle("pump-running", on);

    if (pumpId === "cwu") setCwuArrows(on);
    else if (pumpId === "co") setCoArrows(on);
  }

  function togglePump(pumpId) {
    const next = !uiState.pumps[pumpId];
    setPumpState(pumpId, next);
    return next;
  }

  // --- ŚLIMAK / PODAJNIK ---

  function setAugerStateInternal(isOn) {
    const on = !!isOn;
    if (uiState.augerOn === on) return; // early-return
    uiState.augerOn = on;

    if (els.augerScrew) els.augerScrew.classList.toggle("auger-running", on);
  }

  function toggleAuger() {
    const next = !uiState.augerOn;
    setAugerStateInternal(next);
    return next;
  }

  // --- DMUCHAWA ---

  function setFanStateInternal(isOn) {
    const on = !!isOn;
    if (els.fanRotor) els.fanRotor.classList.toggle("fan-running", on);
  }

  function setBlowerPower(power) {
    const val = clamp(Number(power) || 0, 0, 100);
    if (uiState.blowerPower === val) return; // early-return
    uiState.blowerPower = val;

    if (els.blowerPowerText) els.blowerPowerText.textContent = `${val}%`;

    setFanStateInternal(val > 0);
  }

  // --- TEMPERATURY / PARAMETRY ---

  function setMode(mode) {
    const m = mode ?? null;
    if (uiState.mode === m) return; // early-return
    uiState.mode = m;

    if (els.modeText) els.modeText.textContent = formatMode(m);
  }

  function setFurnaceTemp(valueC) {
    if (uiState.temperatures.furnace === valueC) return;
    uiState.temperatures.furnace = valueC;

    const text = els.furnaceTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      text.style.fill = tempColor(valueC);
    }

    updateFireFromTemp(valueC);
  }

  function setRadiatorsTemp(valueC) {
    if (uiState.temperatures.radiators === valueC) return;
    uiState.temperatures.radiators = valueC;

    const text = els.radiatorsTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      text.style.fill = tempColor(valueC);
    }
  }

  function setMixerTemp(valueC) {
    if (uiState.temperatures.mixer === valueC) return;
    uiState.temperatures.mixer = valueC;

    const text = els.mixerTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      text.style.fill = tempColor(valueC);
    }
  }

  function setAugerTemp(valueC) {
    if (uiState.temperatures.auger === valueC) return;
    uiState.temperatures.auger = valueC;

    const text = els.augerTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      // jak chcesz kolor też tu:
      // text.style.fill = tempColor(valueC);
    }
  }

  function setExhaustTemp(valueC) {
    if (uiState.temperatures.exhaust === valueC) return;
    uiState.temperatures.exhaust = valueC;

    const text = els.exhaustTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      text.style.fill = tempColor(valueC);
    }

    // Dym – jeśli element istnieje i chcesz nim sterować, zostawiam jak było
    const smoke = els.smokeGroup;
    if (!smoke) return;

    const t = Number(valueC);
    smoke.style.display = !Number.isFinite(t) || t <= 80 ? "none" : "";
  }

  function setFuelInTank(kg) {
    if (uiState.fuelKg === kg) return;
    uiState.fuelKg = kg;

    const text = els.fuelInTankText;
    if (text) text.textContent = formatFuel(kg);
  }

  function setAugerCorrection(seconds) {
    if (uiState.augerCorrectionSec === seconds) return;
    uiState.augerCorrectionSec = seconds;

    const text = els.augerCorrectionText;
    if (text) text.textContent = formatSeconds(seconds);
  }

  function setPowerPercent(value) {
    const num = clamp(Number(value) || 0, 0, 100);
    if (uiState.powerPercent === num) return;
    uiState.powerPercent = num;

    const el = els.powerPercentText;
    if (el) el.textContent = `${Math.round(num)}%`;
  }

  // 🔥 ogień: skala zależna od temp kotła
  // WAŻNE: nie nadpisujemy translate z SVG — zachowujemy bazowy transform i podmieniamy tylko scale()
  function updateFireFromTemp(valueC) {
    const fireScale = els.fireScale;
    if (!fireScale) return;

    const t = Number(valueC);

    // brak danych / zimno – ogień znika (jak miałeś)
    if (!Number.isFinite(t) || t < 30) {
      fireScale.style.display = "none";
      return;
    }

    fireScale.style.display = "";

    // 30°C -> 0, 50°C -> 1
    let sRaw = (t - 30) / (50 - 30);
    sRaw = clamp(sRaw, 0, 1);

    // minimalny ogień
    const s = 0.3 + 0.7 * sRaw;

    // baza = to co było w SVG (np. translate)
    const base = (els.fireBaseTransform || "").trim();

    // usuń stare scale(...) z bazy (na wypadek, gdyby było)
    const baseNoScale = base.replace(/scale\([^)]*\)/g, "").trim();

    const newTransform = (baseNoScale ? baseNoScale + " " : "") + `scale(${s})`;
    fireScale.setAttribute("transform", newTransform);
  }

  // --- STATUS / ZEGAR ---

  function setStatus(text) {
    const el = els.statusText;
    if (!el) return;
    if (el.textContent === text) return;
    el.textContent = text;
  }

  function startClock() {
    const el = els.clockText;
    if (!el) return;

    const update = () => {
      const now = new Date();
      const hh = String(now.getHours()).padStart(2, "0");
      const mm = String(now.getMinutes()).padStart(2, "0");
      el.textContent = `${hh}:${mm}`;
    };

    update();
    setInterval(update, 30 * 1000);
  }

  // --- INIT ---

  document.addEventListener("DOMContentLoaded", () => {
    initSvgRefs();
    startClock();
  });

  // --- API FurnaceUI ---

  window.FurnaceUI = {
    state: uiState,
    pumps: { set: setPumpState, toggle: togglePump },
    auger: { set: setAugerStateInternal, toggle: toggleAuger },
    blower: { setPower: setBlowerPower },
    temps: {
      setFurnace: setFurnaceTemp,
      setRadiators: setRadiatorsTemp,
      setMixer: setMixerTemp,
      setAuger: setAugerTemp,
      setExhaust: setExhaustTemp,
    },
    fuel: { setKg: setFuelInTank },
    corrections: { setAugerSeconds: setAugerCorrection },
    ui: { setStatus, setMode },
    power: { setPercent: setPowerPercent },
  };

  // --- FUNKCJE KOMPATYBILNE ZE STARYM KODEM ---

  window.setFanState = function (isOn) {
    setFanStateInternal(isOn);
  };

  window.setPower = function (power) {
    setBlowerPower(power);
  };

  window.setAugerState = function (isOn) {
    setAugerStateInternal(isOn);
  };

  window.setPumpCwuState = function (isOn) {
    setPumpState("cwu", isOn);
  };

  window.setPumpCoState = function (isOn) {
    setPumpState("co", isOn);
  };

  window.setMode = function (mode) {
    setMode(mode);
  };

  window.setExhaustTemp = function (valueC) {
    setExhaustTemp(valueC);
  };
})();

