// js/manual.js
(() => {
  "use strict";

  const API_BASE = "http://127.0.0.1:8000/api";
  const MODE_URL = (mode) => `${API_BASE}/state/mode/${encodeURIComponent(mode)}`;
  const MANUAL_CURRENT_URL = `${API_BASE}/manual/current`;
  const MANUAL_REQ_URL = `${API_BASE}/manual/outputs`;

  const LS_LAST_NON_MANUAL_MODE = "boiler:lastNonManualMode";

  const elStatus = document.getElementById("status-text");

  const modeSwitch = document.getElementById("manual-mode-switch");
  const hint = document.getElementById("manual-hint");
  const fieldset = document.getElementById("manual-controls");

  const fanSlider = document.getElementById("fan-power-slider");
  const fanValue = document.getElementById("fan-power-value");

  const tFeeder = document.getElementById("toggle-feeder");
  const tPumpCO = document.getElementById("toggle-pump-co");
  const tPumpCWU = document.getElementById("toggle-pump-cwu");
  const tMixerOpen = document.getElementById("toggle-mixer-open");
  const tMixerClose = document.getElementById("toggle-mixer-close");

  let busy = false;

  function setStatus(msg, isError = false) {
    if (!elStatus) return;
    elStatus.textContent = msg || "";
    if (isError) elStatus.classList.add("status-error");
    else elStatus.classList.remove("status-error");
  }

  function setControlsEnabled(enabled) {
    if (fieldset) fieldset.disabled = !enabled;
    if (hint) {
      hint.innerHTML = enabled
        ? "Wyjścia są <b>aktywne</b> (tryb ręczny włączony)."
        : "Wyjścia są <b>nieaktywne</b>, gdy tryb ręczny jest wyłączony.";
    }
  }

  function clampInt(v, min, max) {
    const n = Number.parseInt(v, 10);
    if (Number.isNaN(n)) return min;
    return Math.max(min, Math.min(max, n));
  }

  function loadLastNonManualMode() {
    const v = localStorage.getItem(LS_LAST_NON_MANUAL_MODE);
    return v && typeof v === "string" ? v : "OFF";
  }

  function saveLastNonManualMode(mode) {
    if (!mode || mode === "MANUAL") return;
    localStorage.setItem(LS_LAST_NON_MANUAL_MODE, mode);
  }

  async function safeJson(res) {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }

  async function fetchManualCurrent() {
    const res = await fetch(MANUAL_CURRENT_URL, { cache: "no-store" });
    if (!res.ok) {
      const body = await safeJson(res);
      throw new Error(body?.detail?.msg || body?.detail || `GET ${MANUAL_CURRENT_URL} -> ${res.status}`);
    }
    return await res.json();
  }

  async function setMode(mode) {
    const res = await fetch(MODE_URL(mode), { method: "POST" });
    if (!res.ok) {
      const body = await safeJson(res);
      throw new Error(body?.detail?.msg || body?.detail || `POST mode ${mode} -> ${res.status}`);
    }
    return await safeJson(res);
  }

  async function sendManualRequests(payload) {
    const res = await fetch(MANUAL_REQ_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const body = await safeJson(res);
      throw new Error(body?.detail?.msg || body?.detail || `POST ${MANUAL_REQ_URL} -> ${res.status}`);
    }
    return await safeJson(res);
  }

  // ZAWSZE ustawiamy UI z manual.*, nic więcej
  function applyManualToUi(modeName, manual) {
    const isManual = modeName === "MANUAL";
    if (modeSwitch) modeSwitch.checked = isManual;
    setControlsEnabled(isManual);

    const fp = clampInt(manual?.fan_power ?? 0, 0, 100);
    if (fanSlider) fanSlider.value = String(fp);
    if (fanValue) fanValue.textContent = String(fp);

    if (tFeeder) tFeeder.checked = !!manual?.feeder_on;
    if (tPumpCO) tPumpCO.checked = !!manual?.pump_co_on;
    if (tPumpCWU) tPumpCWU.checked = !!manual?.pump_cwu_on;
    if (tMixerOpen) tMixerOpen.checked = !!manual?.mixer_open_on;
    if (tMixerClose) tMixerClose.checked = !!manual?.mixer_close_on;
  }

  function onFanInput() {
    if (!fanSlider || !fanValue) return;
    fanValue.textContent = String(clampInt(fanSlider.value, 0, 100));
  }

  async function onFanCommit() {
    if (busy) return;
    if (!modeSwitch?.checked) {
      setStatus("Tryb ręczny jest wyłączony.", true);
      return;
    }

    const value = clampInt(fanSlider?.value ?? 0, 0, 100);
    busy = true;
    try {
      setStatus(`Ustawiam dmuchawę: ${value}%…`);
      await sendManualRequests({ fan_power: value });
      setStatus("OK.");
    } catch (e) {
      setStatus(String(e?.message || e), true);
    } finally {
      busy = false;
    }
  }

  async function onOutputsChange(e) {
    if (busy) return;
    if (!modeSwitch?.checked) {
      setStatus("Tryb ręczny jest wyłączony.", true);
      return;
    }

    // mutual exclusion mieszacza w UI
    if (e?.target === tMixerOpen && tMixerOpen?.checked && tMixerClose) tMixerClose.checked = false;
    if (e?.target === tMixerClose && tMixerClose?.checked && tMixerOpen) tMixerOpen.checked = false;

    const payload = {
      feeder_on: !!tFeeder?.checked,
      pump_co_on: !!tPumpCO?.checked,
      pump_cwu_on: !!tPumpCWU?.checked,
      mixer_open_on: !!tMixerOpen?.checked,
      mixer_close_on: !!tMixerClose?.checked,
    };

    busy = true;
    try {
      setStatus("Ustawiam wyjścia…");
      await sendManualRequests(payload);
      setStatus("OK.");
    } catch (e2) {
      setStatus(String(e2?.message || e2), true);
    } finally {
      busy = false;
    }
  }

  async function onModeSwitchChange() {
    if (!modeSwitch || busy) return;

    const wantManual = !!modeSwitch.checked;
    busy = true;

    try {
      if (wantManual) {
        // włącz manual — NIE resetujemy manual values
        setStatus("Włączam tryb ręczny…");
        await setMode("MANUAL");

        const data = await fetchManualCurrent();
        applyManualToUi(data.mode, data.manual);

        setStatus("Tryb ręczny włączony.");
      } else {
        // wyłącz manual — wróć do zapamiętanego trybu
        const backMode = loadLastNonManualMode() || "OFF";
        setStatus(`Wyłączam tryb ręczny… (${backMode})`);
        await setMode(backMode);

        const data = await fetchManualCurrent();
        if (data?.mode && data.mode !== "MANUAL") saveLastNonManualMode(data.mode);

        applyManualToUi(data.mode, data.manual);
        setStatus("Tryb ręczny wyłączony.");
      }
    } catch (e) {
      modeSwitch.checked = !wantManual;
      setStatus(String(e?.message || e), true);
    } finally {
      busy = false;
    }
  }

  function bind() {
    if (modeSwitch) modeSwitch.addEventListener("change", onModeSwitchChange);

    if (fanSlider) {
      fanSlider.addEventListener("input", onFanInput);
      fanSlider.addEventListener("change", onFanCommit);
    }

    if (tFeeder) tFeeder.addEventListener("change", onOutputsChange);
    if (tPumpCO) tPumpCO.addEventListener("change", onOutputsChange);
    if (tPumpCWU) tPumpCWU.addEventListener("change", onOutputsChange);
    if (tMixerOpen) tMixerOpen.addEventListener("change", onOutputsChange);
    if (tMixerClose) tMixerClose.addEventListener("change", onOutputsChange);
  }

  async function start() {
    bind();

    // 1× na wejściu: pobierz tryb i manual.* i ustaw UI
    try {
      setStatus("Ładuję…");
      const data = await fetchManualCurrent();

      // zapamiętaj tryb powrotu jeśli nie MANUAL
      if (data?.mode && data.mode !== "MANUAL") saveLastNonManualMode(data.mode);

      applyManualToUi(data.mode, data.manual);
      setStatus("Gotowe.");
    } catch (e) {
      // fallback: OFF/0
      applyManualToUi("OFF", {
        fan_power: 0,
        feeder_on: false,
        pump_co_on: false,
        pump_cwu_on: false,
        mixer_open_on: false,
        mixer_close_on: false,
      });
      setStatus(String(e?.message || e), true);
    }
  }

  window.addEventListener("DOMContentLoaded", start);
})();
