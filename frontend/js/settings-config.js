// js/settings-config.js

// Główny endpoint API konfiguracji
const CONFIG_API_BASE = "/config";

// Stan aktualnych wartości per moduł
const configState = {};
// Cache schem per moduł
const schemaCache = {};
// Info o modułach (id -> {id, name, description})
const modulesById = {};

// Ustawianie komunikatu w stopce
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

// Bezpieczne pobieranie JSON z obsługą błędów
async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  let payload = null;
  try {
    payload = await res.json();
  } catch (e) {
    // brak body albo nie-JSON
  }

  if (!res.ok) {
    const detail =
      (payload && (payload.detail || payload.message)) ||
      `HTTP ${res.status}`;
    throw new Error(detail);
  }

  return payload;
}

// Inicjalizacja widoku po załadowaniu DOM
document.addEventListener("DOMContentLoaded", () => {
  initConfigUI().catch((err) => {
    console.error(err);
    setStatus(`Błąd inicjalizacji: ${err.message}`, true);
  });
});

async function initConfigUI() {
  const tabsHeader = document.getElementById("config-tabs-header");
  const tabsBody = document.getElementById("config-tabs-body");

  if (!tabsHeader || !tabsBody) {
    console.warn("Brak elementów zakładek konfiguracji");
    return;
  }

  setStatus("Ładowanie modułów konfiguracji...");

  // 1) pobierz listę modułów
  const modules = await fetchJson(`${CONFIG_API_BASE}/modules`);

  if (!modules || modules.length === 0) {
    setStatus("Brak dostępnych modułów konfiguracji.");
    tabsHeader.innerHTML = "<p>Brak modułów.</p>";
    return;
  }

  // zapamiętaj info o modułach
  modules.forEach((m) => {
    modulesById[m.id] = m;
  });

  // 2) wygeneruj taby + puste panele
  modules.forEach((module, idx) => {
    // przycisk taba
    const btn = document.createElement("button");
    btn.className = "config-tab-button";
    btn.type = "button";
    btn.textContent = module.name || module.id;
    btn.dataset.moduleId = module.id;
    if (idx === 0) {
      btn.classList.add("active");
    }
    tabsHeader.appendChild(btn);

    // panel
    const panel = document.createElement("div");
    panel.className = "config-tab-panel";
    panel.id = `config-tab-panel-${module.id}`;
    if (idx === 0) {
      panel.classList.add("active");
    }
    tabsBody.appendChild(panel);

    // kliknięcie taba
    btn.addEventListener("click", () => {
      activateTab(module.id);
    });
  });

  // 3) pobierz schema + values dla każdego modułu i wyrenderuj pola
  for (const module of modules) {
    try {
      await loadModuleSchemaAndValues(module.id);
    } catch (err) {
      console.error(`Błąd ładowania modułu ${module.id}:`, err);
      const panel = document.getElementById(
        `config-tab-panel-${module.id}`
      );
      if (panel) {
        panel.innerHTML = `<p style="color:#ff4b4b;">Błąd ładowania konfiguracji modułu: ${err.message}</p>`;
      }
    }
  }

  setStatus("Konfiguracja załadowana.");
}

// aktywacja zakładki
function activateTab(moduleId) {
  // przyciski
  document
    .querySelectorAll(".config-tab-button")
    .forEach((btn) => {
      btn.classList.toggle(
        "active",
        btn.dataset.moduleId === moduleId
      );
    });

  // panele
  document
    .querySelectorAll(".config-tab-panel")
    .forEach((panel) => {
      panel.classList.toggle(
        "active",
        panel.id === `config-tab-panel-${moduleId}`
      );
    });
}

// pobranie schema + values i wyrenderowanie panelu
async function loadModuleSchemaAndValues(moduleId) {
  const [schema, values] = await Promise.all([
    fetchJson(`${CONFIG_API_BASE}/schema/${moduleId}`),
    fetchJson(`${CONFIG_API_BASE}/values/${moduleId}`),
  ]);

  schemaCache[moduleId] = schema;
  configState[moduleId] = { ...(values || {}) };

  renderModulePanel(moduleId, schema, values || {});
}

// główny renderer panelu modułu
function renderModulePanel(moduleId, schema, values) {
  const panel = document.getElementById(`config-tab-panel-${moduleId}`);
  if (!panel) return;

  const moduleInfo = modulesById[moduleId] || {};
  const moduleTitle =
    schema.title || moduleInfo.name || `Moduł ${moduleId}`;
  const moduleDesc =
    schema.description || moduleInfo.description || "";

  panel.innerHTML = "";

  const header = document.createElement("div");
  header.className = "config-module-header";
  header.innerHTML = `
    <h2 class="config-module-title">${escapeHtml(moduleTitle)}</h2>
    ${
      moduleDesc
        ? `<p class="config-module-description">${escapeHtml(
            moduleDesc
          )}</p>`
        : ""
    }
  `;
  panel.appendChild(header);

  const fieldsContainer = document.createElement("div");
  fieldsContainer.className = "config-fields";
  panel.appendChild(fieldsContainer);

  const properties = schema.properties || {};
  const keys = Object.keys(properties);

  if (keys.length === 0) {
    fieldsContainer.innerHTML =
      "<p>Brak zdefiniowanych pól konfiguracji w schemie.</p>";
  } else {
    keys.forEach((key) => {
      const def = properties[key] || {};
      const fieldEl = renderField(
        moduleId,
        key,
        def,
        values[key]
      );
      if (fieldEl) {
        fieldsContainer.appendChild(fieldEl);
      }
    });
  }

  // przyciski zapisu na dole
  const actions = document.createElement("div");
  actions.className = "config-actions";

  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "config-btn config-btn-primary";
  saveBtn.textContent = "Zapisz zmiany";

  saveBtn.addEventListener("click", async () => {
    await saveModuleConfig(moduleId, saveBtn);
  });

  actions.appendChild(saveBtn);
  panel.appendChild(actions);
}

// utworzenie pojedynczego pola na podstawie definicji z schema
function renderField(moduleId, key, def, rawValue) {
  const type = def.type;
  const title = def.title || key;
  const description = def.description || "";
  const unit = def.unit || ""; // jeśli w schemie jest unit
  const enumValues = def.enum || null;

  // ustal wartość początkową
  let value = rawValue;
  if (value === undefined || value === null) {
    if (def.default !== undefined) {
      value = def.default;
    } else if (type === "integer" || type === "number") {
      value =
        def.minimum !== undefined ? def.minimum : 0;
    } else if (type === "boolean") {
      value = false;
    } else if (type === "string" && enumValues && enumValues.length) {
      value = enumValues[0];
    }
  }

  if (!configState[moduleId]) {
    configState[moduleId] = {};
  }
  configState[moduleId][key] = value;

  const field = document.createElement("div");
  field.className = "config-field";
  field.dataset.moduleId = moduleId;
  field.dataset.key = key;

  const label = document.createElement("div");
  label.className = "config-field-label";
  label.textContent = title;
  field.appendChild(label);

  const controls = document.createElement("div");
  controls.className = "config-field-controls";

  const valueEl = document.createElement("div");
  valueEl.className = "config-field-value";

  // numery (integer/number) -> - [wartość] +
  if (type === "integer" || type === "number") {
    const min =
      def.minimum !== undefined ? def.minimum : null;
    const max =
      def.maximum !== undefined ? def.maximum : null;
    const step =
      def.multipleOf !== undefined
        ? def.multipleOf
        : def.step !== undefined
        ? def.step
        : 1;

    const minusBtn = document.createElement("button");
    minusBtn.type = "button";
    minusBtn.className = "config-btn config-btn-icon";
    minusBtn.textContent = "−";

    const plusBtn = document.createElement("button");
    plusBtn.type = "button";
    plusBtn.className = "config-btn config-btn-icon";
    plusBtn.textContent = "+";

    function renderNumber() {
      const display =
        unit && unit.trim().length
          ? `${configState[moduleId][key]} ${unit}`
          : `${configState[moduleId][key]}`;
      valueEl.textContent = display;
    }

    minusBtn.addEventListener("click", () => {
      let current = Number(configState[moduleId][key]) || 0;
      current -= step;
      if (min !== null && current < min) {
        current = min;
      }
      configState[moduleId][key] = current;
      renderNumber();
    });

    plusBtn.addEventListener("click", () => {
      let current = Number(configState[moduleId][key]) || 0;
      current += step;
      if (max !== null && current > max) {
        current = max;
      }
      configState[moduleId][key] = current;
      renderNumber();
    });

    renderNumber();

    controls.appendChild(minusBtn);
    controls.appendChild(valueEl);
    controls.appendChild(plusBtn);

    const help = document.createElement("div");
    help.className = "config-field-help";
    const rangeParts = [];
    if (min !== null || max !== null) {
      rangeParts.push(
        `zakres: ${min !== null ? min : "−∞"} – ${
          max !== null ? max : "+∞"
        }${unit ? " " + unit : ""}`
      );
    }
    if (step && step !== 1) {
      rangeParts.push(`krok: ${step}`);
    }
    help.textContent =
      description ||
      (rangeParts.length ? rangeParts.join(", ") : "");
    field.appendChild(controls);
    if (help.textContent) {
      field.appendChild(help);
    }
    return field;
  }

  // string + enum -> ◀ [wartość] ▶
  if (type === "string" && Array.isArray(enumValues)) {
    const prevBtn = document.createElement("button");
    prevBtn.type = "button";
    prevBtn.className = "config-btn config-btn-icon";
    prevBtn.textContent = "◀";

    const nextBtn = document.createElement("button");
    nextBtn.type = "button";
    nextBtn.className = "config-btn config-btn-icon";
    nextBtn.textContent = "▶";

    function renderEnum() {
      const val = configState[moduleId][key];
      valueEl.textContent = val;
    }

    prevBtn.addEventListener("click", () => {
      const current = configState[moduleId][key];
      let idx = enumValues.indexOf(current);
      if (idx === -1) idx = 0;
      idx = (idx - 1 + enumValues.length) % enumValues.length;
      configState[moduleId][key] = enumValues[idx];
      renderEnum();
    });

    nextBtn.addEventListener("click", () => {
      const current = configState[moduleId][key];
      let idx = enumValues.indexOf(current);
      if (idx === -1) idx = 0;
      idx = (idx + 1) % enumValues.length;
      configState[moduleId][key] = enumValues[idx];
      renderEnum();
    });

    renderEnum();

    controls.appendChild(prevBtn);
    controls.appendChild(valueEl);
    controls.appendChild(nextBtn);

    const help = document.createElement("div");
    help.className = "config-field-help";
    help.textContent =
      description ||
      `Możliwe wartości: ${enumValues.join(", ")}`;
    field.appendChild(controls);
    field.appendChild(help);
    return field;
  }

  // boolean -> toggle ON/OFF
  if (type === "boolean") {
    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "config-toggle";

    function renderBool() {
      const val = !!configState[moduleId][key];
      if (val) {
        toggleBtn.textContent = "Włączone";
        toggleBtn.classList.add("on");
        toggleBtn.classList.remove("off");
      } else {
        toggleBtn.textContent = "Wyłączone";
        toggleBtn.classList.add("off");
        toggleBtn.classList.remove("on");
      }
    }

    toggleBtn.addEventListener("click", () => {
      configState[moduleId][key] = !configState[moduleId][key];
      renderBool();
    });

    renderBool();
    controls.appendChild(toggleBtn);

    const help = document.createElement("div");
    help.className = "config-field-help";
    help.textContent = description || "";
    field.appendChild(controls);
    if (help.textContent) {
      field.appendChild(help);
    }
    return field;
  }

  // typ nieobsługiwany – pokaż tylko aktualną wartość
  valueEl.textContent = String(
    configState[moduleId][key] ?? ""
  );
  controls.appendChild(valueEl);

  const help = document.createElement("div");
  help.className = "config-field-help";
  help.textContent =
    description ||
    `Typ pola "${type}" nieobsługiwany w edytorze – tylko podgląd.`;

  field.appendChild(controls);
  field.appendChild(help);
  return field;
}

// zapis konfiguracji modułu
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

    // API zwraca zwalidowane wartości – zapisujemy do stanu
    configState[moduleId] = { ...(saved || {}) };

    const moduleName =
      (modulesById[moduleId] &&
        modulesById[moduleId].name) ||
      moduleId;
    setStatus(
      `Zapisano konfigurację modułu: ${moduleName}.`
    );

    // Można odświeżyć panel, jeśli chcesz zaktualizować np. zaokrąglone wartości
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

// prosta funkcja escapująca HTML
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
