// js/settings-config.js

// Backend FastAPI z prefiksem /api
const CONFIG_API_BASE = "http://127.0.0.1:8000/api/config";

const configState = {};
const schemaCache = {};
const modulesById = {};

/**
 * Zaokrąglanie do zadanej liczby miejsc po przecinku.
 */
function roundTo(num, decimals = 2) {
  const n = Number(num);
  if (!isFinite(n)) return 0;
  const factor = Math.pow(10, decimals);
  return Math.round((n + Number.EPSILON) * factor) / factor;
}

/**
 * Wyznacz liczbę miejsc po przecinku dla pola typu number.
 * - najpierw patrzymy na def.precision (jeśli jest),
 * - potem na def.step (np. 0.001 → 3 miejsca),
 * - na końcu fallback: 2 miejsca.
 */
function getNumberPrecision(def) {
  if (!def) return 2;

  if (typeof def.precision === "number") {
    return Math.max(0, def.precision);
  }

  if (typeof def.step === "number") {
    const s = String(def.step);
    const dot = s.indexOf(".");
    if (dot >= 0) {
      return s.length - dot - 1; // "0.001" → 3
    }
    return 0; // np. step: 1 → 0 miejsc po przecinku
  }

  return 2;
}

function setStatus(text, isError = false) {
  const el = document.getElementById("status-text");
  if (!el) return;
  el.textContent = text || "";
  if (isError) {
    el.classList.add("status-error");
  } else {
    el.classList.remove("status-error");
  }
}

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  let payload = null;
  try {
    payload = await res.json();
  } catch (e) {}

  if (!res.ok) {
    const detail =
      (payload && (payload.detail || payload.message)) ||
      `HTTP ${res.status}`;
    throw new Error(detail);
  }

  return payload;
}

document.addEventListener("DOMContentLoaded", () => {
  initConfigUI().catch((err) => {
    console.error(err);
    setStatus(`Błąd inicjalizacji: ${err.message}`, true);
  });
});

async function initConfigUI() {
  const tabsHeader = document.getElementById("config-tabs-header");
  const tabsBody = document.getElementById("config-tabs-body");
  const configRoot = document.getElementById("config-root");

  if (!tabsHeader || !tabsBody || !configRoot) {
    console.warn("Brak elementów zakładek konfiguracji");
    return;
  }

  setStatus("Ładowanie modułów konfiguracji...");

  // /api/config/modules → lista {id, name, description}
  const modules = await fetchJson(`${CONFIG_API_BASE}/modules`);

  if (!modules || modules.length === 0) {
    setStatus("Brak dostępnych modułów konfiguracji.");
    tabsHeader.innerHTML = "<p>Brak modułów.</p>";
    return;
  }

  modules.forEach((m) => {
    modulesById[m.id] = m;
  });

  // Taby
  modules.forEach((module, idx) => {
    const btn = document.createElement("button");
    btn.className = "config-tab-button";
    btn.type = "button";
    btn.textContent = (module.name || module.id || "").toUpperCase();
    btn.dataset.moduleId = module.id;
    if (idx === 0) btn.classList.add("active");
    tabsHeader.appendChild(btn);

    const panel = document.createElement("div");
    panel.className = "config-tab-panel";
    panel.id = `config-tab-panel-${module.id}`;
    if (idx === 0) panel.classList.add("active");
    tabsBody.appendChild(panel);

    btn.addEventListener("click", () => {
      activateTab(module.id);
    });
  });

  // Globalny przycisk ZAPISZ na samym dole lewej części
  const actions = document.createElement("div");
  actions.className = "config-actions config-actions-global";

  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "config-btn config-btn-primary";
  saveBtn.textContent = "ZAPISZ ZMIANY";

  saveBtn.addEventListener("click", async () => {
    const activeTab = document.querySelector(".config-tab-button.active");
    if (!activeTab) return;
    const moduleId = activeTab.dataset.moduleId;
    if (!moduleId) return;
    await saveModuleConfig(moduleId, saveBtn);
  });

  actions.appendChild(saveBtn);
  configRoot.appendChild(actions);

  // Załaduj schema + values dla każdego modułu
  for (const module of modules) {
    try {
      await loadModuleSchemaAndValues(module.id);
    } catch (err) {
      console.error(`Błąd ładowania modułu ${module.id}:`, err);
      const panel = document.getElementById(
        `config-tab-panel-${module.id}`
      );
      if (panel) {
        panel.innerHTML = `<p style="color:#ff4b4b;">Błąd ładowania konfiguracji modułu: ${escapeHtml(
          err.message
        )}</p>`;
      }
    }
  }

  setStatus("Konfiguracja załadowana.");
}

function activateTab(moduleId) {
  document
    .querySelectorAll(".config-tab-button")
    .forEach((btn) => {
      btn.classList.toggle(
        "active",
        btn.dataset.moduleId === moduleId
      );
    });

  document
    .querySelectorAll(".config-tab-panel")
    .forEach((panel) => {
      panel.classList.toggle(
        "active",
        panel.id === `config-tab-panel-${moduleId}`
      );
    });
}

async function loadModuleSchemaAndValues(moduleId) {
  const [schema, values] = await Promise.all([
    fetchJson(`${CONFIG_API_BASE}/schema/${moduleId}`),
    fetchJson(`${CONFIG_API_BASE}/values/${moduleId}`),
  ]);

  schemaCache[moduleId] = schema;
  configState[moduleId] = { ...(values || {}) };

  renderModulePanel(moduleId, schema, values || {});
}

function renderModulePanel(moduleId, schema, values) {
  const panel = document.getElementById(`config-tab-panel-${moduleId}`);
  if (!panel) return;

  panel.innerHTML = "";

  // Bez nagłówka modułu – od razu lista pól
  const fieldsContainer = document.createElement("div");
  fieldsContainer.className = "config-fields";
  panel.appendChild(fieldsContainer);

  const fields = schema.fields || [];

  if (!fields.length) {
    fieldsContainer.innerHTML =
      "<p>Brak zdefiniowanych pól konfiguracji w schemie.</p>";
  } else {
    // IGNORUJEMY GRUPY – po prostu lecimy po wszystkich fields
    fields.forEach((fieldDef) => {
      const key = fieldDef.key;
      const rawValue = values[key];
      const fieldEl = renderField(moduleId, key, fieldDef, rawValue);
      if (fieldEl) fieldsContainer.appendChild(fieldEl);
    });
  }

  // brak przycisku ZAPISZ tutaj – jest globalny
}

/**
 * Render jednego pola:
 * LABEL : − WARTOŚĆ + ?
 * długi opis pod spodem po kliknięciu „?”
 */
function renderField(moduleId, key, def, rawValue) {
  const type = def.type; // "number" albo "text"
  const title = (def.label || def.name || key || "").toUpperCase();
  const description = def.description || "";
  const unit = def.unit || "";
  const options = def.options || def.choices || null;

  let value = rawValue;
  if (value === undefined || value === null) {
    if (def.default !== undefined) {
      value = def.default;
    } else if (type === "number") {
      value = def.min !== undefined ? def.min : 0;
    } else if (type === "text" && options && options.length) {
      value = options[0];
    }
  }

  // wstępne zaokrąglenie liczby zgodnie z precyzją
  if (type === "number") {
    const precision = getNumberPrecision(def);
    value = roundTo(value, precision);
  }

  if (!configState[moduleId]) configState[moduleId] = {};
  configState[moduleId][key] = value;

  const field = document.createElement("div");
  field.className = "config-field";
  field.dataset.moduleId = moduleId;
  field.dataset.key = key;

  const mainRow = document.createElement("div");
  mainRow.className = "config-field-main";
  field.appendChild(mainRow);

  const label = document.createElement("div");
  label.className = "config-field-label";
  label.textContent = title;
  mainRow.appendChild(label);

  const controls = document.createElement("div");
  controls.className = "config-field-controls";
  mainRow.appendChild(controls);

  const valueEl = document.createElement("div");
  valueEl.className = "config-field-value";

  // długi opis – jeśli jest
  let helpEl = null;
  if (description) {
    helpEl = document.createElement("div");
    helpEl.className = "config-field-help";
    helpEl.textContent = description;
    field.appendChild(helpEl);
  }

  function appendHelpButtonIfNeeded() {
    if (!helpEl) return;
    const helpBtn = document.createElement("button");
    helpBtn.type = "button";
    helpBtn.className = "config-btn config-btn-help";
    helpBtn.textContent = "?";
    helpBtn.addEventListener("click", () => {
      helpEl.classList.toggle("visible");
    });
    controls.appendChild(helpBtn);
  }

  // === NUMBER: − [val] + ===
  if (type === "number") {
    const precision = getNumberPrecision(def);
    const min = def.min !== undefined ? Number(def.min) : null;
    const max = def.max !== undefined ? Number(def.max) : null;

    // NIE zaokrąglamy step – używamy tak, jak w schemie
    const step =
      def.step !== undefined
        ? Number(def.step)
        : Math.pow(10, -precision);

    const minusBtn = document.createElement("button");
    minusBtn.type = "button";
    minusBtn.className = "config-btn config-btn-icon";
    minusBtn.textContent = "−";

    const plusBtn = document.createElement("button");
    plusBtn.type = "button";
    plusBtn.className = "config-btn config-btn-icon";
    plusBtn.textContent = "+";

    function renderNumber() {
      let val = Number(configState[moduleId][key]) || 0;
      val = roundTo(val, precision);
      configState[moduleId][key] = val;

      const text =
        unit && unit.trim().length
          ? `${val.toFixed(precision)} ${unit}`
          : `${val.toFixed(precision)}`;
      valueEl.textContent = text;
    }

    minusBtn.addEventListener("click", () => {
      let current = Number(configState[moduleId][key]) || 0;
      current -= step;
      if (min !== null && current < min) current = min;
      if (max !== null && current > max) current = max;
      current = roundTo(current, precision);
      configState[moduleId][key] = current;
      renderNumber();
    });

    plusBtn.addEventListener("click", () => {
      let current = Number(configState[moduleId][key]) || 0;
      current += step;
      if (min !== null && current < min) current = min;
      if (max !== null && current > max) current = max;
      current = roundTo(current, precision);
      configState[moduleId][key] = current;
      renderNumber();
    });

    renderNumber();

    controls.appendChild(minusBtn);
    controls.appendChild(valueEl);
    controls.appendChild(plusBtn);
    appendHelpButtonIfNeeded();

    return field;
  }

  // === BOOL: suwak ON / OFF (przycisk .config-toggle) ===
  if (type === "bool") {
    // ustaw domyślnie wartość bool w stanie
    configState[moduleId][key] = !!configState[moduleId][key];

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "config-toggle";

    function renderBool() {
      const val = !!configState[moduleId][key];

      // opcjonalne label_on / label_off z schemy (jak chcesz)
      const labelOn = def.label_on || "ON";
      const labelOff = def.label_off || "OFF";

      toggleBtn.textContent = val ? labelOn : labelOff;

      toggleBtn.classList.toggle("on", val);
      toggleBtn.classList.toggle("off", !val);
    }

    toggleBtn.addEventListener("click", () => {
      const current = !!configState[moduleId][key];
      configState[moduleId][key] = !current;
      renderBool();
    });

    renderBool();

    controls.appendChild(toggleBtn);
    appendHelpButtonIfNeeded();

    return field;
  }

  // === TEXT + OPTIONS: ◀ [val] ▶ ===
  if (type === "text" && Array.isArray(options)) {
    const prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "config-btn config-btn-icon";
    prevBtn.textContent = "◀";

    const nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "config-btn config-btn-icon";
    nextBtn.textContent = "▶";

    function renderText() {
      const val = String(configState[moduleId][key] ?? "");
      valueEl.textContent = val.toUpperCase();
    }

    prevBtn.addEventListener("click", () => {
      const current = String(configState[moduleId][key] ?? "");
      let idx = options.indexOf(current);
      if (idx === -1) idx = 0;
      idx = (idx - 1 + options.length) % options.length;
      configState[moduleId][key] = options[idx];
      renderText();
    });

    nextBtn.addEventListener("click", () => {
      const current = String(configState[moduleId][key] ?? "");
      let idx = options.indexOf(current);
      if (idx === -1) idx = 0;
      idx = (idx + 1) % options.length;
      configState[moduleId][key] = options[idx];
      renderText();
    });

    renderText();

    controls.appendChild(prevBtn);
    controls.appendChild(valueEl);
    controls.appendChild(nextBtn);
    appendHelpButtonIfNeeded();

    return field;
  }

  // === Fallback – nieobsługiwany typ, sam podgląd ===
  valueEl.textContent = String(configState[moduleId][key] ?? "");
  controls.appendChild(valueEl);
  appendHelpButtonIfNeeded();

  return field;
}

async function saveModuleConfig(moduleId, saveButtonEl) {
  try {
    saveButtonEl.disabled = true;
    setStatus(
      `Zapisywanie konfiguracji modułu ${moduleId}...`
    );

    const body = configState[moduleId] || {};
    const saved = await fetchJson(
      `${CONFIG_API_BASE}/values/${moduleId}`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      }
    );

    configState[moduleId] = { ...(saved || {}) };

    const moduleName =
      (modulesById[moduleId] &&
        modulesById[moduleId].name) ||
      moduleId;
    setStatus(
      `Zapisano konfigurację modułu: ${moduleName}.`
    );

    const schema = schemaCache[moduleId];
    if (schema) {
      renderModulePanel(moduleId, schema, saved || {});
    }
  } catch (err) {
    console.error(err);
    setStatus(
      `Błąd zapisu konfiguracji modułu: ${err.message}`,
      true
    );
  } finally {
    saveButtonEl.disabled = false;
  }
}

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
