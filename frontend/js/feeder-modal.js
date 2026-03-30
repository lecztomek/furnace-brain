(() => {
  const CONFIG_API_BASE = "/api/config";

  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));
  const roundToStep = (v, step) => {
    const inv = 1 / step;
    return Math.round(v * inv) / inv;
  };

  async function apiGetSingle(moduleId, key) {
    const url = `${CONFIG_API_BASE}/value/${encodeURIComponent(moduleId)}/${encodeURIComponent(key)}`;
    const res = await fetch(url, {
      method: "GET",
      headers: { "Accept": "application/json" },
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`GET ${url} -> ${res.status} ${txt}`);
    }
    const data = await res.json();
    return data?.value;
  }

  async function apiSetSingle(moduleId, key, value) {
    const url = `${CONFIG_API_BASE}/value/${encodeURIComponent(moduleId)}/${encodeURIComponent(key)}`;
    const res = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(value),
    });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`PUT ${url} -> ${res.status} ${txt}`);
    }
    const data = await res.json();
    return data?.value;
  }

  const FEED_ON = {
    moduleId: "feeder",
    key: "feed_on_base_s",
    min: 1,
    max: 120,
    step: 1,
  };

  const FEED_OFF = {
    moduleId: "feeder",
    key: "feed_off_base_s",
    min: 11,
    max: 600,
    step: 1,
  };

  const modal = document.getElementById("feeder-modal");
  if (!modal) return;

  const opener = document.getElementById("auger");

  const onValueEl = document.getElementById("feeder-on-value");
  const offValueEl = document.getElementById("feeder-off-value");
  const hintEl = document.getElementById("feeder-hint");

  const btnOnMinus = document.getElementById("feeder-on-minus");
  const btnOnPlus = document.getElementById("feeder-on-plus");
  const btnOffMinus = document.getElementById("feeder-off-minus");
  const btnOffPlus = document.getElementById("feeder-off-plus");
  const btnSave = document.getElementById("feeder-save");

  const presetButtons = Array.from(document.querySelectorAll(".feeder-preset-btn"));

  let currentOn = null;
  let currentOff = null;
  let isSaving = false;

  function render() {
    onValueEl.textContent = currentOn == null ? "--" : String(currentOn);
    offValueEl.textContent = currentOff == null ? "--" : String(currentOff);

    if (currentOn != null && currentOff != null) {
      hintEl.textContent = `Podawanie: ${currentOn}s, przerwa: ${currentOff}s`;
    }
  }

  function setFeedOn(v) {
    currentOn = roundToStep(clamp(v, FEED_ON.min, FEED_ON.max), FEED_ON.step);
    render();
  }

  function setFeedOff(v) {
    currentOff = roundToStep(clamp(v, FEED_OFF.min, FEED_OFF.max), FEED_OFF.step);
    render();
  }

  async function loadValues() {
    const feedOnRaw = await apiGetSingle(FEED_ON.moduleId, FEED_ON.key);
    const feedOffRaw = await apiGetSingle(FEED_OFF.moduleId, FEED_OFF.key);

    const feedOn = Number(feedOnRaw);
    const feedOff = Number(feedOffRaw);

    if (!Number.isFinite(feedOn)) {
      throw new Error(`Nieprawidłowe feeder.feed_on_base_s: ${JSON.stringify(feedOnRaw)}`);
    }
    if (!Number.isFinite(feedOff)) {
      throw new Error(`Nieprawidłowe feeder.feed_off_base_s: ${JSON.stringify(feedOffRaw)}`);
    }

    currentOn = roundToStep(clamp(feedOn, FEED_ON.min, FEED_ON.max), FEED_ON.step);
    currentOff = roundToStep(clamp(feedOff, FEED_OFF.min, FEED_OFF.max), FEED_OFF.step);
  }

  function onEsc(e) {
    if (e.key === "Escape") closeModal();
  }

  async function openModal() {
    currentOn = null;
    currentOff = null;
    onValueEl.textContent = "--";
    offValueEl.textContent = "--";
    hintEl.textContent = "Wczytywanie…";
    btnSave.disabled = true;

    modal.classList.remove("hidden");
    window.addEventListener("keydown", onEsc);

    try {
      await loadValues();
      render();
      btnSave.disabled = false;
    } catch (err) {
      console.error(err);
      hintEl.textContent = `Błąd wczytywania: ${err?.message || err}`;
      btnSave.disabled = true;
    }
  }

  function closeModal() {
    modal.classList.add("hidden");
    isSaving = false;
    btnSave.disabled = false;
    btnSave.textContent = "Zapisz";
    window.removeEventListener("keydown", onEsc);
  }

  modal.addEventListener("click", (e) => {
    const close = e.target?.getAttribute?.("data-feeder-close");
    if (close) closeModal();
  });

  btnOnMinus.addEventListener("click", () => {
    if (currentOn == null) return;
    setFeedOn(currentOn - FEED_ON.step);
  });

  btnOnPlus.addEventListener("click", () => {
    if (currentOn == null) return;
    setFeedOn(currentOn + FEED_ON.step);
  });

  btnOffMinus.addEventListener("click", () => {
    if (currentOff == null) return;
    setFeedOff(currentOff - FEED_OFF.step);
  });

  btnOffPlus.addEventListener("click", () => {
    if (currentOff == null) return;
    setFeedOff(currentOff + FEED_OFF.step);
  });

  presetButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      setFeedOn(Number(btn.dataset.feedOn));
      setFeedOff(Number(btn.dataset.feedOff));
      hintEl.textContent = `Wybrano preset: ${currentOn}s / ${currentOff}s`;
    });
  });

  btnSave.addEventListener("click", async () => {
    if (isSaving) return;
    if (currentOn == null || currentOff == null) return;

    isSaving = true;
    btnSave.disabled = true;
    btnSave.textContent = "Zapisywanie…";

    try {
      await apiSetSingle(FEED_ON.moduleId, FEED_ON.key, currentOn);
      await apiSetSingle(FEED_OFF.moduleId, FEED_OFF.key, currentOff);

      closeModal();
    } catch (err) {
      console.error(err);
      hintEl.textContent = `Błąd zapisu: ${err?.message || err}`;
      isSaving = false;
      btnSave.disabled = false;
      btnSave.textContent = "Zapisz";
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    if (opener) {
      opener.style.cursor = "pointer";
      opener.addEventListener("click", openModal);
    }
  });
})();
