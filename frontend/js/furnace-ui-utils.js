// furnace-ui-utils.js
// Proste sterowanie animacjami kotła (FurnaceUI) + stany/liczby

(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);

  // --- STAN (opcjonalnie, jak chcesz tego używać z zewnątrz) ---

  const uiState = {
    pumps: {
      cwu: false,
      co: false,
    },
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
	fireWrapper: null,
	smokeGroup: null,
  };

  // --- FORMATERY / POMOCNICZE ---

  function formatMode(mode) {
    if (!mode) return "TRYB: --";
    // ujednolicamy zapis – np. wielkie litery
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
// UWAGA: animujemy <polygon> wewnątrz grup, bo CSS jest zdefiniowany na polygonach

function setArrowGroupActive(arrowEls, isOn) {
  arrowEls.forEach((groupEl) => {
    if (!groupEl) return;

    // bierzemy poligon w środku <g>, a jakby go nie było, to samą grupę
    const poly = groupEl.querySelector("polygon") || groupEl;
    const color = groupEl.dataset.color; // "red" / "blue"

    if (isOn) {
      // WŁĄCZONE: czyścimy inline-style i przywracamy klasy animujące
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
      // WYŁĄCZONE: bez animacji, czarna strzałka
      poly.classList.remove("red-arrow", "blue-arrow");
      poly.style.animation = "none";
      poly.style.fill = "#000";
      poly.style.stroke = "#000";
    }
  });
}

function setCwuArrows(isOn) {
  setArrowGroupActive(
    [els.cwuArrowHot, els.cwuArrowCold],
    isOn
  );
}

function setCoArrows(isOn) {
  setArrowGroupActive(
    [els.coArrowHot, els.coArrowCold],
    isOn
  );
}



  // --- INICJALIZACJA REFERENCJI DO SVG ---

  function initSvgRefs() {
    // POMPY – rotor jest <g id="rotor" class="pump-running"> wewnątrz #pump-cwu / #pump-co
    const pumpCwu = $("#pump-cwu");
    const pumpCo = $("#pump-co");

    if (pumpCwu) {
      // UWAGA: id="rotor" jest zdublowane, więc bierzemy po klasie, nie po #id
      els.pumpCwuRotor =
        pumpCwu.querySelector(".pump-running") || pumpCwu.querySelector("g");
    }

    if (pumpCo) {
      els.pumpCoRotor =
        pumpCo.querySelector(".pump-running") || pumpCo.querySelector("g");
    }

    // ŚLIMAK – <g id="auger-screw" class="auger-running"> wewnątrz #auger
    const auger = $("#auger");
    if (auger) {
      els.augerScrew =
        auger.querySelector(".auger-running") || $("#auger-screw");
    }

    // DMUCHAWA – <g id="fan-rotor" class="fan-running">
    els.fanRotor = $("#fan-rotor");

    // Tekst mocy dmuchawy – w SVG masz **pwn**, nie pwm
    els.blowerPowerText = $("#blower-pwn-text");
	
    els.cwuArrowHot  = $("#cwu-arrow-hot");
    els.cwuArrowCold = $("#cwu-arrow-cold");
    els.coArrowHot   = $("#co-arrow-hot");
    els.coArrowCold  = $("#co-arrow-cold");
	els.modeText  = $("#mode-text");
	els.fireWrapper  = $("#fire-wrapper"); 
	
      // DYM
      els.smokeGroup   = $("#smoke");
      if (els.smokeGroup) {
        // na starcie dym wyłączony
        els.smokeGroup.style.display = "none";
      }

    // Na starcie — strzałki bez migania (czarne)
    setCwuArrows(false);
    setCoArrows(false);
  }

  // --- POMPY ---

  function setPumpState(pumpId, isOn) {
    const on = !!isOn;
    uiState.pumps[pumpId] = on;

    const rotor =
      pumpId === "cwu" ? els.pumpCwuRotor : pumpId === "co" ? els.pumpCoRotor : null;

    if (rotor) {
      // KLUCZ: tylko dodajemy / usuwamy klasę .pump-running
      rotor.classList.toggle("pump-running", on);
    }
	
    // STRZAŁKI POWIĄZANE Z POMPAMI
    if (pumpId === "cwu") {
      setCwuArrows(on);
    } else if (pumpId === "co") {
      setCoArrows(on);
    }
  }

  function togglePump(pumpId) {
    const next = !uiState.pumps[pumpId];
    setPumpState(pumpId, next);
    return next;
  }

  // --- ŚLIMAK / PODAJNIK ---

  function setAugerStateInternal(isOn) {
    const on = !!isOn;
    uiState.augerOn = on;
    if (els.augerScrew) {
      // CSS: .auger-running .auger-spiral { animation: ... }
      els.augerScrew.classList.toggle("auger-running", on);
    }
  }

  function toggleAuger() {
    const next = !uiState.augerOn;
    setAugerStateInternal(next);
    return next;
  }

  // --- DMUCHAWA ---

  function setFanStateInternal(isOn) {
    const on = !!isOn;
    if (els.fanRotor) {
      // ODTWARZAMY ORYGINAŁ:
      // document.getElementById('fan-rotor').classList.toggle('fan-running', isOn);
      els.fanRotor.classList.toggle("fan-running", on);
    }
  }

  function setBlowerPower(power) {
    const val = clamp(Number(power) || 0, 0, 100);
    uiState.blowerPower = val;

    if (els.blowerPowerText) {
      els.blowerPowerText.textContent = `${val}%`;
    }

    // animacja ON tylko gdy moc > 0
    setFanStateInternal(val > 0);
  }

  // --- TEMPERATURY / PARAMETRY ---

  function setMode(mode) {
    const el = els.modeText || $("#mode-text");
    if (!el) return;

    uiState.mode = mode ?? null;
    el.textContent = formatMode(mode);
  }
  
  function setFurnaceTemp(valueC) {
    const text = $("#furnace-temp-text");
    if (!text) return;
    uiState.temperatures.furnace = valueC;
    text.textContent = formatTemp(valueC);
    text.style.fill = tempColor(valueC);
    
    updateFireFromTemp(valueC);
  }

  function setRadiatorsTemp(valueC) {
    const text = $("#radiators-temp-text");
    if (!text) return;
    uiState.temperatures.radiators = valueC;
    text.textContent = formatTemp(valueC);
    text.style.fill = tempColor(valueC);
  }

  function setMixerTemp(valueC) {
    const text = $("#mixer-temp-text");
    if (!text) return;
    uiState.temperatures.mixer = valueC;
    text.textContent = formatTemp(valueC);
    text.style.fill = tempColor(valueC);
  }

  function setAugerTemp(valueC) {
    const text = $("#auger-temp--text"); // tak masz w SVG
    if (!text) return;
    uiState.temperatures.auger = valueC;
    text.textContent = formatTemp(valueC);
  }

  function setFuelInTank(kg) {
    const text = $("#fuel-in-tank-text");
    if (!text) return;
    uiState.fuelKg = kg;
    text.textContent = formatFuel(kg);
  }
  
function setExhaustTemp(valueC) {
  const text = $("#exhaust-temp-text");  // <- ID tekstu od spalin w SVG
  uiState.temperatures.exhaust = valueC;

  if (text) {
    text.textContent = formatTemp(valueC);
    text.style.fill = tempColor(valueC);
  }

  // sterowanie dymem
  const smoke = els.smokeGroup || $("#smoke");
  if (!smoke) return;

  const t = Number(valueC);

  // brak danych lub <= 80°C -> dym niewidoczny
  if (!Number.isFinite(t) || t <= 80) {
    smoke.style.display = "none";
  } else {
    // powyżej 80°C – dym leci (CSS już ma animację)
    smoke.style.display = "";
  }
}


  function setAugerCorrection(seconds) {
    const text = $("#auger-correction-text");
    if (!text) return;
    uiState.augerCorrectionSec = seconds;
    text.textContent = formatSeconds(seconds);
  }
  
    function updateFireFromTemp(valueC) {
      const wrapper = els.fireWrapper || $("#fire-wrapper");
      if (!wrapper) return;

      const t = Number(valueC);

      // brak danych / zimno – ognia nie ma
      if (!Number.isFinite(t) || t < 30) {
        wrapper.style.display = "none";     // całkowicie ukryty
        return;
      }

      wrapper.style.display = "";           // pokaż ogień

      // 30°C -> 0.0, 50°C -> 1.0 (liniowo)
      const s = clamp((t - 30) / (50 - 30), 0, 1);

      // sterujemy bazową skalą całego płomienia
      wrapper.setAttribute("transform", `translate(533,310) scale(${s})`);
    }

  // --- STATUS / ZEGAR ---

  function setStatus(text) {
    const el = $("#status-text");
    if (!el) return;
    el.textContent = text;
  }

  function startClock() {
    const el = $("#clock");
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
    // NIC tutaj nie pauzuję – wszystko kręci się zgodnie z klasami w SVG
    // Sterujesz wyłącznie przez wywołania poniżej (FurnaceUI / globalne funkcje).
  });

  // --- API FurnaceUI ---

  window.FurnaceUI = {
    state: uiState,
    pumps: {
      set: setPumpState,
      toggle: togglePump,
    },
    auger: {
      set: setAugerStateInternal,
      toggle: toggleAuger,
    },
    blower: {
      setPower: setBlowerPower,
    },
    temps: {
      setFurnace: setFurnaceTemp,
      setRadiators: setRadiatorsTemp,
      setMixer: setMixerTemp,
      setAuger: setAugerTemp,
      setExhaust: setExhaustTemp,
    },
    fuel: {
      setKg: setFuelInTank,
    },
    corrections: {
      setAugerSeconds: setAugerCorrection,
    },
    ui: {
      setStatus,
	  setMode,
    },
  };

  // --- FUNKCJE KOMPATYBILNE ZE STARYM KODEM ---

  // dokładnie jak w oryginale:
  // function setFanState(isOn) {
  //   document.getElementById('fan-rotor').classList.toggle('fan-running', isOn);
  // }
  window.setFanState = function (isOn) {
    setFanStateInternal(isOn);
  };

  // backend woła np. setPower(65)
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
