// furnace-ui-utils.js
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
    // (animacje usunięte) — zostawiamy tylko tekst/pola i strzałki
    blowerPowerText: null,

    cwuArrowHot: null,
    cwuArrowCold: null,
    coArrowHot: null,
    coArrowCold: null,

    // kropki statusu
    augerStatusDot: null,
    pumpCoStatusDot: null,
    pumpCwuStatusDot: null,
    blowerStatusDot: null,

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

  function clamp(num, min, max) {
    return Math.min(max, Math.max(min, num));
  }

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
  function tempColor(valueC) {
    if (valueC == null) return "#ccc";
    if (valueC < 30) return "#3fa9f5";
    if (valueC < 60) return "#f6c151";
    return "#e74c3c";
  }

  // ---------- STATUS DOTS ----------
  const DOT_ON_FILL = "#2ecc71";
  const DOT_ON_STROKE = "#1b6f3a";
  const DOT_OFF_FILL = "#e74c3c";
  const DOT_OFF_STROKE = "#7a1f17";

  function setStatusDot(dotEl, isOn) {
    if (!dotEl) return;
    dotEl.setAttribute("fill", isOn ? DOT_ON_FILL : DOT_OFF_FILL);
    dotEl.setAttribute("stroke", isOn ? DOT_ON_STROKE : DOT_OFF_STROKE);
  }

  function syncStatusDots() {
    setStatusDot(els.augerStatusDot, uiState.augerOn);
    setStatusDot(els.pumpCoStatusDot, uiState.pumps.co);
    setStatusDot(els.pumpCwuStatusDot, uiState.pumps.cwu);
    setStatusDot(els.blowerStatusDot, uiState.blowerPower > 0);
  }

  // ---------- STRZAŁKI (bez animacji) ----------
  function setArrowGroupActive(arrowEls, isOn) {
    arrowEls.forEach((groupEl) => {
      if (!groupEl) return;

      const poly = groupEl.querySelector("polygon") || groupEl;
      const color = groupEl.dataset.color;

      // WAŻNE: zero animacji, tylko statyczny kolor
      poly.style.animation = "none";

      if (isOn) {
        if (color === "red") {
          poly.style.fill = "#e74c3c";
          poly.style.stroke = "#e74c3c";
        } else if (color === "blue") {
          poly.style.fill = "#3fa9f5";
          poly.style.stroke = "#3fa9f5";
        } else {
          poly.style.fill = "#000";
          poly.style.stroke = "#000";
        }
      } else {
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

  // ---------- INIT REFS ----------
  function initSvgRefs() {
    // tekst mocy dmuchawy
    els.blowerPowerText = $("#blower-pwm-text") || $("#blower-pwn-text");

    // strzałki
    els.cwuArrowHot = $("#cwu-arrow-hot");
    els.cwuArrowCold = $("#cwu-arrow-cold");
    els.coArrowHot = $("#co-arrow-hot");
    els.coArrowCold = $("#co-arrow-cold");

    // kropki statusu (zgodnie z Twoim HTML: <g id="...-status"><circle .../></g>)
    els.augerStatusDot = $("#auger-status circle");
    els.pumpCoStatusDot = $("#pump-co-status circle");
    els.pumpCwuStatusDot = $("#pump-cwu-status circle");
    els.blowerStatusDot = $("#blower-status circle");

    els.modeText = $("#mode-text");

    els.fireScale = $("#fire-scale");
    if (els.fireScale) {
      els.fireBaseTransform = els.fireScale.getAttribute("transform") || "";
    }

    els.smokeGroup = $("#smoke");

    els.furnaceTempText = $("#furnace-temp-text");
    els.radiatorsTempText = $("#radiators-temp-text");
    els.mixerTempText = $("#mixer-temp-text");
    els.augerTempText = $("#auger-temp-text") || $("#auger-temp--text");
    els.exhaustTempText = $("#exhaust-temp-text");
    els.statusText = $("#status-text");
    els.clockText = $("#clock");
    els.powerPercentText = $("#power-percent-text");

    els.fuelInTankText = $("#fuel-in-tank-text");
    els.augerCorrectionText = $("#auger-correction-text");

    // stan początkowy UI
    setCwuArrows(false);
    setCoArrows(false);
    syncStatusDots();

    refsReady = true;
  }

  function ensureSvgRefs() {
    if (!refsReady) initSvgRefs();
  }

  // ---------- POMPY ----------
  function setPumpState(pumpId, isOn) {
    ensureSvgRefs();
    const on = !!isOn;
    uiState.pumps[pumpId] = on;

    if (pumpId === "cwu") setCwuArrows(on);
    else if (pumpId === "co") setCoArrows(on);

    syncStatusDots();
  }

  function togglePump(pumpId) {
    const next = !uiState.pumps[pumpId];
    setPumpState(pumpId, next);
    return next;
  }

  // ---------- ŚLIMAK ----------
  function setAugerStateInternal(isOn) {
    ensureSvgRefs();
    uiState.augerOn = !!isOn;
    syncStatusDots();
  }

  function toggleAuger() {
    const next = !uiState.augerOn;
    setAugerStateInternal(next);
    return next;
  }

  // ---------- DMUCHAWA ----------
  function setBlowerPower(power) {
    ensureSvgRefs();
    const val = clamp(Number(power) || 0, 0, 100);
    uiState.blowerPower = val;

    if (els.blowerPowerText) els.blowerPowerText.textContent = `${val}%`;
    syncStatusDots();
  }

  // ---------- TEMPERATURY / PARAMETRY ----------
  function setMode(mode) {
    ensureSvgRefs();
    uiState.mode = mode ?? null;
    if (els.modeText) els.modeText.textContent = formatMode(uiState.mode);
  }

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
    const newTransform = (baseNoScale ? baseNoScale + " " : "") + `scale(${s})`;
    fireScale.setAttribute("transform", newTransform);
  }

  function setFurnaceTemp(valueC) {
    ensureSvgRefs();
    uiState.temperatures.furnace = valueC;
    if (els.furnaceTempText) {
      els.furnaceTempText.textContent = formatTemp(valueC);
      els.furnaceTempText.style.fill = tempColor(valueC);
    }
    updateFireFromTemp(valueC);
  }

  function setRadiatorsTemp(valueC) {
    ensureSvgRefs();
    uiState.temperatures.radiators = valueC;
    if (els.radiatorsTempText) {
      els.radiatorsTempText.textContent = formatTemp(valueC);
      els.radiatorsTempText.style.fill = tempColor(valueC);
    }
  }

  function setMixerTemp(valueC) {
    ensureSvgRefs();
    uiState.temperatures.mixer = valueC;
    if (els.mixerTempText) {
      els.mixerTempText.textContent = formatTemp(valueC);
      els.mixerTempText.style.fill = tempColor(valueC);
    }
  }

  function setAugerTemp(valueC) {
    ensureSvgRefs();
    uiState.temperatures.auger = valueC;
    if (els.augerTempText) els.augerTempText.textContent = formatTemp(valueC);
  }

  function setExhaustTemp(valueC) {
    ensureSvgRefs();
    uiState.temperatures.exhaust = valueC;
    if (els.exhaustTempText) {
      els.exhaustTempText.textContent = formatTemp(valueC);
      els.exhaustTempText.style.fill = tempColor(valueC);
    }
    if (els.smokeGroup) {
      const t = Number(valueC);
      els.smokeGroup.style.display = !Number.isFinite(t) || t <= 80 ? "none" : "";
    }
  }

  function setFuelInTank(kg) {
    ensureSvgRefs();
    uiState.fuelKg = kg;
    if (els.fuelInTankText) els.fuelInTankText.textContent = formatFuel(kg);
  }

  function setAugerCorrection(seconds) {
    ensureSvgRefs();
    uiState.augerCorrectionSec = seconds;
    if (els.augerCorrectionText) els.augerCorrectionText.textContent = formatSeconds(seconds);
  }

  function setPowerPercent(value) {
    ensureSvgRefs();
    uiState.powerPercent = clamp(Number(value) || 0, 0, 100);
    if (els.powerPercentText) els.powerPercentText.textContent = `${Math.round(uiState.powerPercent)}%`;
  }

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

  // ---------- API ----------
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
    // zgodność — realnie sterujemy przez blowerPower
    uiState.blowerPower = isOn ? Math.max(uiState.blowerPower, 1) : 0;
    ensureSvgRefs();
    syncStatusDots();
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

