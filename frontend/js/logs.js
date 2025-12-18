// js/logs.js
(() => {
  "use strict";

  // ====== KONFIG ======
  const API_BASE = "http://127.0.0.1:8000/api";
  const LOGS_RECENT_URL = `${API_BASE}/logs/recent`;
  const LOGS_META_URL = `${API_BASE}/logs/meta`; // opcjonalne (jeśli masz nadal /meta)
  // Uwaga: jeśli przeszedłeś na API czytające z CSV (history-style) i nie masz /meta,
  // to loadMeta() po prostu nic nie zrobi.

  // ====== DOM ======
  const elStatus = document.getElementById("logs-status");
  const elTs = document.getElementById("logs-ts");

  const elRefresh = document.getElementById("logs-refresh-btn");
  const elList = document.getElementById("logs-list");

  const elLevel = document.getElementById("logs-level");
  const elLimit = document.getElementById("logs-limit");

  // NOWE: select źródła (w HTML: <select id="logs-source" ...>)
  const elSource = document.getElementById("logs-source");

  const elClear = document.getElementById("logs-clear-btn");

  // ====== STATE ======
  /** @type {Array<any>} */
  let currentItems = [];

  // ====== UTILS ======
  const escapeHtml = (s) => {
    if (s === null || s === undefined) return "";
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  };

  const safeJson = (obj) => {
    try {
      return JSON.stringify(obj ?? {}, null, 2);
    } catch {
      return "{}";
    }
  };

  const formatTs = (item) => {
    // Preferujemy ts_iso z backendu (z CSV routera), fallback na ts
    if (item?.ts_iso) {
      const d = new Date(item.ts_iso);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    }
    if (typeof item?.ts === "number") {
      const d = new Date(item.ts * 1000);
      if (!isNaN(d.getTime())) return d.toLocaleString();
    }
    if (item?.data_czas) {
      // jeśli backend zwraca data_czas
      const d = new Date(item.data_czas);
      if (!isNaN(d.getTime())) return d.toLocaleString();
      return String(item.data_czas);
    }
    return "--";
  };

  // backend: WARNING -> UI WARN
  const normalizeLevelForUi = (lvl) => {
    const L = String(lvl || "").toUpperCase();
    if (L === "WARNING") return "WARN";
    return L;
  };

  // UI WARN -> API WARNING
  const mapLevelToApi = (uiValue) => {
    const L = String(uiValue || "").toUpperCase();
    if (L === "WARN") return "WARNING";
    return L;
  };

  const setPillsOk = (msg, tsText) => {
    if (elStatus) elStatus.textContent = msg;
    if (elTs) elTs.textContent = tsText;
  };

  const setPillsError = (msg) => {
    if (elStatus) elStatus.textContent = msg;
    if (elTs) elTs.textContent = "--";
  };

  // ====== SOURCE SELECT ======
  function fillSourcesSelect(items) {
    if (!elSource) return;

    const current = elSource.value || "";
    const sources = Array.from(
      new Set((items || []).map((x) => x?.source).filter(Boolean))
    ).sort();

    elSource.innerHTML = "";

    const optAll = document.createElement("option");
    optAll.value = "";
    optAll.textContent = "Wszystkie";
    elSource.appendChild(optAll);

    for (const s of sources) {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      elSource.appendChild(opt);
    }

    if (current && sources.includes(current)) elSource.value = current;
  }

  // ====== META (opcjonalnie) ======
  async function loadMeta() {
    if (!elLevel) return;

    try {
      const res = await fetch(LOGS_META_URL, { cache: "no-store" });
      if (!res.ok) return;

      const meta = await res.json();
      const levels = Array.isArray(meta?.levels) ? meta.levels : null;
      if (!levels || levels.length === 0) return;

      const current = elLevel.value;
      elLevel.innerHTML = "";

      const optAll = document.createElement("option");
      optAll.value = "";
      optAll.textContent = "Wszystkie";
      elLevel.appendChild(optAll);

      for (const raw of levels) {
        const ui = normalizeLevelForUi(raw);
        const opt = document.createElement("option");
        opt.value = ui;
        opt.textContent = ui;
        elLevel.appendChild(opt);
      }

      if (current) elLevel.value = current;
    } catch {
      // meta opcjonalne
    }
  }

  // ====== FETCH ======
  async function fetchLogs() {
    const limit = elLimit ? Number(elLimit.value || 25) : 25;

    const levelUi = elLevel ? elLevel.value : "";
    const levelApi = levelUi ? mapLevelToApi(levelUi) : "";

    const url = new URL(LOGS_RECENT_URL);
    url.searchParams.set("limit", String(isFinite(limit) ? limit : 25));

    // jeśli Twoje /logs/recent (CSV router) wspiera te filtry – zostawiamy:
    if (levelApi) url.searchParams.set("level", levelApi);

    // (opcjonalnie) jeśli chcesz filtrować po source już na backendzie:
    // ale Ty chcesz source lokalnie, więc NIE wysyłamy source do API.

    setPillsOk("pobieranie…", "--");

    try {
      const res = await fetch(url.toString(), { cache: "no-store" });
      if (!res.ok) {
        setPillsError(`błąd HTTP ${res.status}`);
        return;
      }

      const payload = await res.json();
      currentItems = Array.isArray(payload?.items) ? payload.items : [];

      // pill timestamp
      // CSV router /logs/recent zwykle nie zwraca payload.ts — robimy "teraz"
      const tsText =
        typeof payload?.ts === "number"
          ? new Date(payload.ts * 1000).toLocaleString()
          : new Date().toLocaleString();

      setPillsOk("OK", tsText);

      // uzupełnij źródła (z pobranych danych)
      fillSourcesSelect(currentItems);

      // render wg source
      applyLocalFilterAndRender();
    } catch (err) {
      setPillsError("brak połączenia");
      console.error("Logs fetch error:", err);
    }
  }

  // ====== RENDER ======
  function applyLocalFilterAndRender() {
    const src = elSource ? (elSource.value || "") : "";
    let items = currentItems;

    if (src) {
      items = items.filter((it) => String(it?.source || "") === src);
    }

    renderItems(items);
  }

  function renderItems(items) {
    if (!elList) return;

    elList.innerHTML = "";

    if (!items || items.length === 0) {
      const empty = document.createElement("div");
      empty.className = "logs-empty";
      empty.textContent = "Brak logów do wyświetlenia.";
      elList.appendChild(empty);
      return;
    }

    for (const it of items) {
      const lvlUi = normalizeLevelForUi(it?.level || it?.level_display);

      const row = document.createElement("div");
      row.className = `log-row level-${escapeHtml(lvlUi)}`;

      const ts = document.createElement("div");
      ts.className = "log-ts";
      ts.textContent = formatTs(it);

      const level = document.createElement("div");
      const badge = document.createElement("span");
      badge.className = "log-level";
      badge.textContent = lvlUi || "--";
      level.appendChild(badge);

      const src = document.createElement("div");
      src.className = "log-src";
      src.textContent = it?.source ?? "--";

      const msg = document.createElement("div");
      msg.className = "log-msg";
      msg.textContent = it?.message ?? "";

      const details = document.createElement("div");
      details.className = "log-details";
      details.innerHTML = `
        <div style="margin-bottom:8px; opacity:.95;">
          <strong>Typ:</strong> ${escapeHtml(it?.type ?? "--")}
        </div>
        <pre>${escapeHtml(safeJson(it?.data))}</pre>
      `;

      row.appendChild(ts);
      row.appendChild(level);
      row.appendChild(src);
      row.appendChild(msg);
      row.appendChild(details);

      row.addEventListener("click", () => {
        row.classList.toggle("is-expanded");
      });

      elList.appendChild(row);
    }
  }

  // ====== EVENTS (BRAK POLLINGU) ======
  function bindUi() {
    if (elRefresh) elRefresh.addEventListener("click", fetchLogs);

    // Poziom/limit => fetch (akcja usera)
    if (elLevel) elLevel.addEventListener("change", fetchLogs);
    if (elLimit) elLimit.addEventListener("change", fetchLogs);

    // Źródło => lokalnie
    if (elSource) elSource.addEventListener("change", applyLocalFilterAndRender);

    // Wyczyść => reset level + source i fetch
    if (elClear) {
      elClear.addEventListener("click", () => {
        if (elLevel) elLevel.value = "";
        if (elSource) elSource.value = "";
        fetchLogs();
      });
    }
  }

  // ====== INIT ======
  async function init() {
    bindUi();
    await loadMeta();  // opcjonalne
    await fetchLogs(); // jednorazowe wczytanie
  }

  document.addEventListener("DOMContentLoaded", init);
})();
