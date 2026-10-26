// Setpoint popup for the main page: tap an element of the diagram, adjust
// one or more settings with -/+ and save. Ranges, steps, units and labels
// come from the module's schema.yaml, so the popup can't offer a value the
// backend would reject.
(() => {
  const CONFIG_API_BASE = "/api/config";

  // ===== Targets: element on the diagram -> settings shown in the popup =====
  // A field with "also" is written to every listed module (one setting shared
  // by several regulators); it is read from the first one.
  // "label" overrides the schema label; "atLeast" keeps the field >= another
  // field of the same popup (by "id").
  const TARGETS = {
    boiler: {
      opener: "furnace-temp",
      title: "Zadana temp kotła",
      fields: [
        {
          id: "work",
          label: "Praca (PID, fuzzy, predykcyjny)",
          moduleId: "power_work",
          key: "boiler_set_temp",
          also: [
            { moduleId: "power_work_fuzzy", key: "boiler_set_temp" },
            { moduleId: "power_work_predictive", key: "boiler_set_temp" },
          ],
        },
        {
          id: "ignition",
          label: "Rozpalanie (nie niżej niż praca)",
          moduleId: "power_ignition",
          key: "boiler_set_temp",
          atLeast: "work",
        },
      ],
    },
    radiators: {
      opener: "radiators-temp",
      title: "Ustaw temp grzejników",
      fields: [{ moduleId: "mixer", key: "target_temp" }],
    },
    pump_co: {
      opener: "pump-co",
      title: "Pompa CO",
      fields: [{ moduleId: "pump_co", key: "boiler_on_temp" }],
    },
    pump_cwu: {
      opener: "pump-cwu",
      title: "Pompa CWU",
      fields: [{ moduleId: "pump_cwu", key: "boiler_on_temp" }],
    },
    blower: {
      opener: "blower",
      title: "Dmuchawa",
      fields: [
        { moduleId: "blower", key: "cycle_time_s" },
        { moduleId: "blower", key: "base_fan_percent" },
      ],
    },
  };

  // ===== Helpers =====
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  const roundToStep = (v, step) => {
    // stabilne zaokrąglanie do kroku (np. 0.5)
    const inv = 1 / step;
    return Math.round(v * inv) / inv;
  };

  const fmt = (v) => String(Math.round(v * 100) / 100);

  async function apiJson(url, options = {}) {
    const res = await fetch(url, { headers: { "Accept": "application/json" }, ...options });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`${options.method || "GET"} ${url} -> ${res.status} ${txt}`);
    }
    return res.json();
  }

  const schemaCache = {};
  async function fieldDef(moduleId, key) {
    if (!schemaCache[moduleId]) {
      schemaCache[moduleId] = apiJson(`${CONFIG_API_BASE}/schema/${encodeURIComponent(moduleId)}`);
    }
    const schema = await schemaCache[moduleId];
    const def = (schema.fields || []).find((f) => f.key === key);
    if (!def) throw new Error(`Brak pola ${moduleId}.${key} w schema`);
    return def;
  }

  function valueUrl(moduleId, key) {
    return `${CONFIG_API_BASE}/value/${encodeURIComponent(moduleId)}/${encodeURIComponent(key)}`;
  }

  // ===== Modal elements =====
  const modal = document.getElementById("temp-modal");
  const fieldsEl = document.getElementById("temp-fields");
  const hintEl = document.getElementById("temp-hint");
  const btnSave = document.getElementById("temp-save");
  const titleEl = document.getElementById("temp-modal-title");

  // ===== State =====
  let active = null; // { cfg, fields: [{ moduleId, key, def, value, loaded, valueEl }] }
  let isSaving = false;

  function setValue(field, v) {
    const min = Number(field.def.min ?? -Infinity);
    const max = Number(field.def.max ?? Infinity);
    const step = Number(field.def.step ?? 1);
    field.value = roundToStep(clamp(v, min, max), step);
    field.valueEl.textContent = fmt(field.value);
    applyConstraints();
  }

  function applyConstraints() {
    if (!active) return;
    active.fields.forEach((f) => {
      const other = f.atLeast && active.fields.find((o) => o.id === f.atLeast);
      if (other && other.value !== null && f.value !== null && f.value < other.value) {
        f.value = other.value;
        f.valueEl.textContent = fmt(f.value);
      }
    });
  }

  function renderField(field, withLabel) {
    const wrap = document.createElement("div");
    wrap.className = "setpoint-field";

    if (withLabel) {
      const label = document.createElement("div");
      label.className = "setpoint-field__label";
      label.textContent = field.label || field.def.label || field.key;
      wrap.appendChild(label);
    }

    const stepper = document.createElement("div");
    stepper.className = "temp-stepper";

    const minus = document.createElement("button");
    minus.type = "button";
    minus.className = "temp-stepper__btn";
    minus.textContent = "−";

    const valueBox = document.createElement("div");
    valueBox.className = "temp-stepper__value";
    field.valueEl = document.createElement("span");
    field.valueEl.textContent = "--";
    const unit = document.createElement("span");
    unit.className = "temp-stepper__unit";
    unit.textContent = field.def.unit || "";
    valueBox.append(field.valueEl, unit);

    const plus = document.createElement("button");
    plus.type = "button";
    plus.className = "temp-stepper__btn";
    plus.textContent = "+";

    const step = Number(field.def.step ?? 1);
    minus.addEventListener("click", () => field.value !== null && setValue(field, field.value - step));
    plus.addEventListener("click", () => field.value !== null && setValue(field, field.value + step));

    stepper.append(minus, valueBox, plus);
    wrap.appendChild(stepper);
    fieldsEl.appendChild(wrap);
  }

  function rangeHint(fields) {
    const range = (f) => {
      const u = f.def.unit || "";
      return `${f.def.min}–${f.def.max}${u}, krok ${f.def.step ?? 1}${u}`;
    };
    const ranges = fields.map(range);
    if (ranges.every((r) => r === ranges[0])) return `Zakres: ${ranges[0]}`;
    return fields.map((f, i) => `${f.label || f.def.label || f.key}: ${ranges[i]}`).join(" • ");
  }

  async function openModal(cfg) {
    active = { cfg, fields: [] };
    titleEl.textContent = cfg.title;
    fieldsEl.innerHTML = "";
    hintEl.textContent = "Wczytywanie…";
    btnSave.disabled = true; // dopóki nie ma wartości, nie zapisujemy

    modal.classList.remove("hidden");
    window.addEventListener("keydown", onEsc);

    const opened = active;
    try {
      const fields = await Promise.all(
        cfg.fields.map(async (f) => {
          const [def, data] = await Promise.all([
            fieldDef(f.moduleId, f.key),
            apiJson(valueUrl(f.moduleId, f.key)),
          ]);
          const num = Number(data?.value);
          if (!Number.isFinite(num)) {
            throw new Error(`Backend zwrócił nie-liczbę dla ${f.moduleId}.${f.key}: ${JSON.stringify(data?.value)}`);
          }
          // shared setting: are the other modules on the same value?
          const others = await Promise.all(
            (f.also || []).map((o) => apiJson(valueUrl(o.moduleId, o.key)).then((d) => Number(d?.value)))
          );
          const inSync = others.every((v) => v === num);
          return { ...f, def, value: null, loaded: num, inSync, others, valueEl: null };
        })
      );
      if (active !== opened) return; // zamknięte w trakcie wczytywania

      active.fields = fields;
      fields.forEach((f) => {
        renderField(f, fields.length > 1);
        const raw = f.loaded;
        setValue(f, raw);
        // what the backend has (rounded), so a value lifted by a constraint gets saved
        f.loaded = roundToStep(clamp(raw, Number(f.def.min ?? -Infinity), Number(f.def.max ?? Infinity)), Number(f.def.step ?? 1));
      });
      const outOfSync = fields.filter((f) => !f.inSync);
      hintEl.textContent = outOfSync.length
        ? `${rangeHint(fields)} • teraz różne: ${outOfSync
            .map((f) => [f.loaded, ...f.others].map(fmt).join(" / "))
            .join(", ")} - zapis ustawi wszystkie`
        : rangeHint(fields);
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
    active = null;
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

  btnSave.addEventListener("click", async () => {
    if (!active || isSaving || !active.fields.length) return;

    isSaving = true;
    btnSave.disabled = true;
    btnSave.textContent = "Zapisywanie…";

    try {
      // only changed fields (or shared ones whose modules differ)
      for (const f of active.fields.filter((f) => f.value !== f.loaded || !f.inSync)) {
        for (const t of [f, ...(f.also || [])]) {
          await apiJson(valueUrl(t.moduleId, t.key), {
            method: "PUT",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify(f.value),
          });
        }
        f.loaded = f.value;
        f.inSync = true;
      }
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
  Object.values(TARGETS).forEach((cfg) => {
    const el = document.getElementById(cfg.opener);
    if (!el) {
      console.warn(`Brak elementu #${cfg.opener} dla okna "${cfg.title}"`);
      return;
    }
    el.style.cursor = "pointer";
    el.addEventListener("click", () => openModal(cfg));
  });
})();
