// furnace-ui-utils.js
// Proste sterowanie animacjami kotła (FurnaceUI) + stany/liczby

(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);

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

  let refsReady = false;

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

  // --- STRZAŁKI CWU / CO (bez zmian) ---

  function setArrowGroupActive(arrowEls, isOn) {
    arrowEls.forEach((groupEl) => {
      if (!groupEl) return;

      const poly = groupEl.querySelector("polygon") || groupEl;
      const color = groupEl.dataset.color;

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

  // --- INIT REFS ---

  function initSvgRefs() {
    const pumpCwu = $("#pump-cwu");
    const pumpCo = $("#pump-co");

    if (pumpCwu) {
      // najpewniej: <g id="rotor"> ... </g> wewnątrz #pump-cwu
      els.pumpCwuRotor =
        pumpCwu.querySelector('g[id="rotor"]') ||
        pumpCwu.querySelector(".pump-running") ||
        pumpCwu.querySelector("g");
    }

    if (pumpCo) {
      els.pumpCoRotor =
        pumpCo.querySelector('g[id="rotor"]') ||
        pumpCo.querySelector(".pump-running") ||
        pumpCo.querySelector("g");
    }

    const auger = $("#auger");
    if (auger) {
      els.augerScrew =
        auger.querySelector('g[id="auger-screw"]') ||
        auger.querySelector(".auger-running") ||
        $("#auger-screw");
    }

    els.fanRotor = $("#fan-rotor");
    els.blowerPowerText = $("#blower-pwn-text");

    els.cwuArrowHot = $("#cwu-arrow-hot");
    els.cwuArrowCold = $("#cwu-arrow-cold");
    els.coArrowHot = $("#co-arrow-hot");
    els.coArrowCold = $("#co-arrow-cold");

    els.modeText = $("#mode-text");

    els.fireScale = $("#fire-scale");
    if (els.fireScale) {
      els.fireBaseTransform = els.fireScale.getAttribute("transform") || "";
    }

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

    refsReady = true;
  }

  function ensureSvgRefs() {
    if (!refsReady) initSvgRefs();
  }

  // --- POMPY ---

  function setPumpState(pumpId, isOn) {
    ensureSvgRefs();

    const on = !!isOn;
    uiState.pumps[pumpId] = on;

    const rotor =
      pumpId === "cwu"
        ? els.pumpCwuRotor
        : pumpId === "co"
          ? els.pumpCoRotor
          : null;

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
    ensureSvgRefs();

    const on = !!isOn;
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
    ensureSvgRefs();

    const on = !!isOn;
    if (els.fanRotor) els.fanRotor.classList.toggle("fan-running", on);
  }

  function setBlowerPower(power) {
    ensureSvgRefs();

    const val = clamp(Number(power) || 0, 0, 100);
    uiState.blowerPower = val;

    if (els.blowerPowerText) els.blowerPowerText.textContent = `${val}%`;

    setFanStateInternal(val > 0);
  }

  // --- TEMPERATURY / PARAMETRY ---

  function setMode(mode) {
    ensureSvgRefs();

    const m = mode ?? null;
    uiState.mode = m;

    if (els.modeText) els.modeText.textContent = formatMode(m);
  }

  function setFurnaceTemp(valueC) {
    ensureSvgRefs();

    uiState.temperatures.furnace = valueC;

    const text = els.furnaceTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      text.style.fill = tempColor(valueC);
    }

    updateFireFromTemp(valueC);
  }

  function setRadiatorsTemp(valueC) {
    ensureSvgRefs();

    uiState.temperatures.radiators = valueC;

    const text = els.radiatorsTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      text.style.fill = tempColor(valueC);
    }
  }

  function setMixerTemp(valueC) {
    ensureSvgRefs();

    uiState.temperatures.mixer = valueC;

    const text = els.mixerTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      text.style.fill = tempColor(valueC);
    }
  }

  function setAugerTemp(valueC) {
    ensureSvgRefs();

    uiState.temperatures.auger = valueC;

    const text = els.augerTempText;
    if (text) text.textContent = formatTemp(valueC);
  }

  function setExhaustTemp(valueC) {
    ensureSvgRefs();

    uiState.temperatures.exhaust = valueC;

    const text = els.exhaustTempText;
    if (text) {
      text.textContent = formatTemp(valueC);
      text.style.fill = tempColor(valueC);
    }

    // jak nie masz dymu w DOM to i tak nic nie zrobi
    const smoke = els.smokeGroup;
    if (!smoke) return;

    const t = Number(valueC);
    smoke.style.display = !Number.isFinite(t) || t <= 80 ? "none" : "";
  }

  function setFuelInTank(kg) {
    ensureSvgRefs();

    uiState.fuelKg = kg;
    if (els.fuelInTankText) els.fuelInTankText.textContent = formatFuel(kg);
  }

  function setAugerCorrection(seconds) {
    ensureSvgRefs();

    uiState.augerCorrectionSec = seconds;
    if (els.augerCorrectionText)
      els.augerCorrectionText.textContent = formatSeconds(seconds);
  }

  function setPowerPercent(value) {
    ensureSvgRefs();

    const num = clamp(Number(value) || 0, 0, 100);
    uiState.powerPercent = num;

    if (els.powerPercentText)
      els.powerPercentText.textContent = `${Math.round(num)}%`;
  }

  // 🔥 Ogień: tylko skala, ale zachowaj bazowy transform z SVG
  function updateFireFromTemp(valueC) {
    ensureSvgRefs();

    const fireScale = els.fireScale;
    if (!fireScale) return;

    const t = Number(valueC);

    if (!Number.isFinite(t) || t < 30) {
      fireScale.style.display = "none";
      return;
    }

    fireScale.style.display = "";

    let sRaw = (t - 30) / (50 - 30);
    sRaw = clamp(sRaw, 0, 1);
    const s = 0.3 + 0.7 * sRaw;

    const base = (els.fireBaseTransform || "").trim();
    const baseNoScale = base.replace(/scale\([^)]*\)/g, "").trim();

    const newTransform =
      (baseNoScale ? baseNoScale + " " : "") + `scale(${s})`;

    fireScale.setAttribute("transform", newTransform);
  }

  // --- STATUS / ZEGAR ---

  function setStatus(text) {
    ensureSvgRefs();
    if (els.statusText) els.statusText.textContent = text;
  }

  function startClock() {
    ensureSvgRefs();
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

  // kompatybilność
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

