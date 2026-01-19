(() => {
  const CONFIG_API_BASE = "/api/config";

  // ===== Helpers =====
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  const roundToStep = (v, step) => {
    // stabilne zaokrąglanie do kroku (np. 0.5)
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

  // ===== Modal elements =====
  const modal = document.getElementById("temp-modal");
  const tempValueEl = document.getElementById("temp-value");
  const hintEl = document.getElementById("temp-hint");
  const btnMinus = document.getElementById("temp-minus");
  const btnPlus = document.getElementById("temp-plus");
  const btnSave = document.getElementById("temp-save");
  const titleEl = document.getElementById("temp-modal-title");

  const radiatorsOpener = document.getElementById("radiators-temp");
  const radiatorsSetpointText = document.getElementById("radiators-setpoint-text");

  // ===== State =====
  let activeKey = null;
  let current = null; // null = jeszcze nie wczytano
  let isSaving = false;

  // ===== Targets =====
  const TARGETS = {
    radiators_setpoint: {
      title: "Ustaw temp grzejników",
      min: 10,
      max: 80,
      step: 0.5,

      moduleId: "mixer",
      configKey: "target_temp",

      openerEl: radiatorsOpener,

      // TYLKO backend. Jak się nie uda -> błąd, bez defaultów.
      read: async () => {
        const v = await apiGetSingle("mixer", "target_temp");
        const num = Number(v);
        if (!Number.isFinite(num)) {
          throw new Error(
            `Backend zwrócił nie-liczbę dla mixer.target_temp: ${JSON.stringify(v)}`
          );
        }
        return num;
      },

      write: (v) => {
        // format: pokaż 1 miejsce po przecinku tylko gdy potrzeba
        const s = (Math.round(v * 2) / 2).toString(); // 0.5 kroki
        if (radiatorsSetpointText) {
          radiatorsSetpointText.textContent = `${s}°C`;
        } else {
          hintEl.textContent = `Ustawiono (GUI): ${s}°C`;
        }
      }
    }
  };

  // ===== Modal functions =====
  function setModalValue(v, cfg) {
    const clamped = clamp(v, cfg.min, cfg.max);
    const stepped = roundToStep(clamped, cfg.step);
    current = stepped;

    // wyświetlanie: 0.5 ma sens pokazać bez trailing .0, np. 45 zamiast 45.0
    const display = (Math.round(stepped * 2) / 2).toString();
    tempValueEl.textContent = display;
    hintEl.textContent = `Zakres: ${cfg.min}–${cfg.max}°C, krok: ${cfg.step}°C`;
  }

  async function openModal(key) {
    const cfg = TARGETS[key];
    if (!cfg) return;

    activeKey = key;
    titleEl.textContent = cfg.title;

    // placeholder zanim dojdzie backend
    current = null;
    tempValueEl.textContent = "--";
    hintEl.textContent = "Wczytywanie…";
    btnSave.disabled = true; // dopóki nie ma wartości, nie zapisujemy

    modal.classList.remove("hidden");
    window.addEventListener("keydown", onEsc);

    try {
      const initial = await cfg.read();
      setModalValue(initial, cfg);
      btnSave.disabled = false;
    } catch (err) {
      console.error(err);
      // brak defaulta: zostaje "--"
      hintEl.textContent = `Błąd wczytywania: ${err?.message || err}`;
      btnSave.disabled = true;
    }
  }

  function closeModal() {
    modal.classList.add("hidden");
    activeKey = null;
    current = null;
    isSaving = false;
    btnSave.disabled = false;
    btnSave.textContent = "Zapisz";
    window.removeEventListener("keydown", onEsc);
  }

  function onEsc(e) {
    if (e.key === "Escape") closeModal();
  }

  // overlay / X close
  modal.addEventListener("click", (e) => {
    const close = e.target?.getAttribute?.("data-close");
    if (close) closeModal();
  });

  btnMinus.addEventListener("click", () => {
    if (!activeKey) return;
    const cfg = TARGETS[activeKey];
    if (current === null) return; // jeszcze nie wczytano
    setModalValue(current - cfg.step, cfg);
  });

  btnPlus.addEventListener("click", () => {
    if (!activeKey) return;
    const cfg = TARGETS[activeKey];
    if (current === null) return; // jeszcze nie wczytano
    setModalValue(current + cfg.step, cfg);
  });

  btnSave.addEventListener("click", async () => {
    if (!activeKey) return;
    if (isSaving) return;

    const cfg = TARGETS[activeKey];
    if (current === null) return;

    isSaving = true;
    btnSave.disabled = true;
    btnSave.textContent = "Zapisywanie…";

    try {
      const saved = await apiSetSingle(cfg.moduleId, cfg.configKey, current);
      const num = Number(saved);
      if (!Number.isFinite(num)) {
        throw new Error(`Backend zwrócił nie-liczbę po zapisie: ${JSON.stringify(saved)}`);
      }

      const finalValue = roundToStep(clamp(num, cfg.min, cfg.max), cfg.step);
      cfg.write(finalValue);
      closeModal();
    } catch (err) {
      console.error(err);
      hintEl.textContent = `Błąd zapisu: ${err?.message || err}`;
      isSaving = false;
      btnSave.disabled = false;
      btnSave.textContent = "Zapisz";
    }
  });

  // ===== Wire openers =====
  Object.entries(TARGETS).forEach(([key, cfg]) => {
    if (!cfg.openerEl) {
      console.warn(`Brak openerEl dla targetu: ${key}`);
      return;
    }
    cfg.openerEl.style.cursor = "pointer";
    cfg.openerEl.addEventListener("click", () => openModal(key));
  });
})();

