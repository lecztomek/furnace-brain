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
    pumpCwuRotor: null,
    pumpCoRotor: null,
    augerScrew: null,
    augerSpiral: null,
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

  // ---------- STRZAŁKI ----------
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

  // ---------- POMOCNICZE: znajdź “rotor” i obracaj po bbox ----------
  function findRotorLike(rootEl) {
    if (!rootEl) return null;

    // kolejność: najpewniejsze selektory -> coraz bardziej ogólne
    return (
      rootEl.querySelector(".rotor") ||
      rootEl.querySelector('g[id*="rotor" i]') ||
      rootEl.querySelector('[data-rotor="1"]') ||
      rootEl.querySelector("g") || // last resort: pierwsza grupa
      null
    );
  }

  function rotateAboutSelf(el, deg) {
    if (!el) return;
    // getBBox działa dla elementów SVG w DOM
    const bb = el.getBBox();
    const cx = bb.x + bb.width / 2;
    const cy = bb.y + bb.height / 2;
    el.setAttribute("transform", `rotate(${deg} ${cx} ${cy})`);
  }

  // ---------- INIT REFS ----------
  function initSvgRefs() {
    const pumpCwu = $("#pump-cwu");
    const pumpCo = $("#pump-co");

    els.pumpCwuRotor = findRotorLike(pumpCwu);
    els.pumpCoRotor = findRotorLike(pumpCo);

    const auger = $("#auger");
    if (auger) {
      els.augerScrew =
        auger.querySelector("#auger-screw") ||
        auger.querySelector('g[id="auger-screw"]') ||
        $("#auger-screw");

      els.augerSpiral = auger.querySelector(".auger-spiral");
    }

    // DMUCHAWA: spróbuj kilka popularnych id (bo często się rozjeżdżają po edycji SVG)
    els.fanRotor =
      $("#fan-rotor") ||
      $("#blower-rotor") ||
      $("#fanRotor") ||
      $("#blowerRotor") ||
      $("#fan")?.querySelector(".rotor") ||
      $("#blower")?.querySelector(".rotor") ||
      null;

    // UWAGA: u Ciebie było "#blower-pwn-text" (pwn) – często to literówka.
    // Zostawiamy oba, żeby zadziałało niezależnie jak jest w SVG.
    els.blowerPowerText = $("#blower-pwm-text") || $("#blower-pwn-text");

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
    els.augerTempText = $("#auger-temp-text") || $("#auger-temp--text"); // obsłuż oba warianty
    els.exhaustTempText = $("#exhaust-temp-text");
    els.statusText = $("#status-text");
    els.clockText = $("#clock");
    els.powerPercentText = $("#power-percent-text");

    els.fuelInTankText = $("#fuel-in-tank-text");
    els.augerCorrectionText = $("#auger-correction-text");

    setCwuArrows(false);
    setCoArrows(false);

    refsReady = true;
  }

  function ensureSvgRefs() {
    if (!refsReady) initSvgRefs();
  }

  // ---------- ANIMACJE “KROKOWE” ----------
  const ANIM_MS = 200;
  let animTimer = null;
  let animStep = 0;

  const ROT_STEPS = [0, 10, 20, 30, 40, 50, 60, 70, 80];
  const AUGER_STEPS = [0, 15, 30, 45];

  function anyAnimActive() {
    return (
      !!uiState.augerOn ||
      !!uiState.pumps.cwu ||
      !!uiState.pumps.co ||
      uiState.blowerPower > 0
    );
  }

  function animTick() {
    ensureSvgRefs();
    animStep = (animStep + 1) % ROT_STEPS.length;
    const a = ROT_STEPS[animStep];

    // POMPY – obrót “własny” (bbox)
    if (uiState.pumps.cwu && els.pumpCwuRotor) rotateAboutSelf(els.pumpCwuRotor, a);
    else if (els.pumpCwuRotor) rotateAboutSelf(els.pumpCwuRotor, 0);

    if (uiState.pumps.co && els.pumpCoRotor) rotateAboutSelf(els.pumpCoRotor, a);
    else if (els.pumpCoRotor) rotateAboutSelf(els.pumpCoRotor, 0);

    // DMUCHAWA – obrót “własny” (bbox)
    if (uiState.blowerPower > 0 && els.fanRotor) rotateAboutSelf(els.fanRotor, a);
    else if (els.fanRotor) rotateAboutSelf(els.fanRotor, 0);

    // ŚLIMAK – przesuw
    if (uiState.augerOn && els.augerSpiral) {
      const x = AUGER_STEPS[animStep];
      els.augerSpiral.setAttribute("transform", `translate(${x} 0)`);
    } else if (els.augerSpiral) {
      els.augerSpiral.setAttribute("transform", `translate(0 0)`);
    }
  }

  function updateAnimTicker() {
    const on = anyAnimActive();

    if (on && !animTimer) {
      animStep = 0;
      animTick();
      animTimer = setInterval(animTick, ANIM_MS);
    } else if (!on && animTimer) {
      clearInterval(animTimer);
      animTimer = null;
      animStep = 0;

      if (els.pumpCwuRotor) rotateAboutSelf(els.pumpCwuRotor, 0);
      if (els.pumpCoRotor) rotateAboutSelf(els.pumpCoRotor, 0);
      if (els.fanRotor) rotateAboutSelf(els.fanRotor, 0);
      if (els.augerSpiral) els.augerSpiral.setAttribute("transform", `translate(0 0)`);
    }
  }

  // ---------- POMPY ----------
  function setPumpState(pumpId, isOn) {
    ensureSvgRefs();
    const on = !!isOn;
    uiState.pumps[pumpId] = on;

    if (pumpId === "cwu") setCwuArrows(on);
    else if (pumpId === "co") setCoArrows(on);

    updateAnimTicker();
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
    updateAnimTicker();
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
    updateAnimTicker();
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
    // zostawione dla zgodności – prawdziwy ruch idzie od blowerPower
    uiState.blowerPower = isOn ? Math.max(uiState.blowerPower, 1) : 0;
    updateAnimTicker();
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

