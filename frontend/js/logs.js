// js/logs.js
(() => {
  "use strict";

  // ====== KONFIG ======
  const API_BASE = "/api";
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

  // --- Virtual clock (symulowany czas z backendu) ---
  let clockOffsetMs = 0;

  async function syncClockOffset() {
    try {
      const st = await fetch("/api/state/current").then((r) => r.json());
      if (st && typeof st.ts === "number") {
        const serverNowMs = Math.round(st.ts * 1000);
        clockOffsetMs = serverNowMs - Date.now();
      }
    } catch (e) {
      clockOffsetMs = 0; // fallback: czas przeglądarki
    }
  }

  function virtualNow() {
    return new Date(Date.now() + clockOffsetMs);
  }

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
  // every module (from the config API) + sources seen in the fetched logs, so a
  // module with rare events (e.g. weather) can be picked even if it isn't in
  // the latest N entries
  let knownSources = ["kernel", "aux_runner"];

  async function loadKnownSources() {
    try {
      const res = await fetch("/api/config/modules", { cache: "no-store" });
      if (!res.ok) return;
      const modules = await res.json();
      knownSources = knownSources.concat((modules || []).map((m) => m?.id).filter(Boolean));
    } catch {
      // optional
    }
  }

  function fillSourcesSelect(items) {
    if (!elSource) return;

    const current = elSource.value || "";
    const sources = Array.from(
      new Set(knownSources.concat((items || []).map((x) => x?.source).filter(Boolean)))
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

    const url = new URL(LOGS_RECENT_URL, window.location.origin);
    url.searchParams.set("limit", String(isFinite(limit) ? limit : 25));

    // jeśli Twoje /logs/recent (CSV router) wspiera te filtry – zostawiamy:
    if (levelApi) url.searchParams.set("level", levelApi);
    // source filtered by the backend, so older entries of a module are found too
    const src = elSource ? elSource.value : "";
    if (src) url.searchParams.set("source", src);

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
          : virtualNow().toLocaleString();

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

      row.appendChild(ts);
      row.appendChild(level);
      row.appendChild(src);
      row.appendChild(msg);

      row.addEventListener("click", () => openDetails(it, lvlUi));

      elList.appendChild(row);
    }
  }

  // ====== DETAILS POPUP ======
  const elPopup = document.getElementById("log-popup");

  function formatValue(v) {
    if (v === null || v === undefined) return "--";
    if (typeof v === "number") return String(Math.round(v * 100) / 100);
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }

  function openDetails(it, lvlUi) {
    if (!elPopup) return;
    const card = elPopup.querySelector(".log-popup");
    card.className = `modal__card log-popup level-${lvlUi}`;

    elPopup.querySelector("#log-popup-level").textContent = lvlUi || "--";
    elPopup.querySelector("#log-popup-ts").textContent = formatTs(it);
    elPopup.querySelector("#log-popup-src").textContent = it?.source ?? "--";
    elPopup.querySelector("#log-popup-type").textContent = it?.type ?? "--";
    elPopup.querySelector("#log-popup-msg").textContent = it?.message ?? "";

    // event data as key/value rows ("seq" is an internal counter)
    const table = elPopup.querySelector("#log-popup-data");
    table.innerHTML = "";
    const data = it?.data && typeof it.data === "object" ? it.data : null;
    const entries = data ? Object.entries(data).filter(([k]) => k !== "seq") : [];
    if (!entries.length) {
      table.innerHTML = `<div class="log-popup__empty">brak dodatkowych danych</div>`;
    }
    for (const [key, value] of entries) {
      const k = document.createElement("div");
      k.className = "log-popup__key";
      k.textContent = key;
      const v = document.createElement("div");
      v.className = "log-popup__value";
      v.textContent = formatValue(value);
      table.append(k, v);
    }

    elPopup.classList.remove("hidden");
    window.addEventListener("keydown", onPopupEsc);
  }

  function closeDetails() {
    if (!elPopup) return;
    elPopup.classList.add("hidden");
    window.removeEventListener("keydown", onPopupEsc);
  }

  function onPopupEsc(e) {
    if (e.key === "Escape") closeDetails();
  }

  if (elPopup) {
    elPopup.addEventListener("click", (e) => {
      if (e.target?.getAttribute?.("data-close")) closeDetails();
    });
  }

  // ====== EVENTS (BRAK POLLINGU) ======
  function bindUi() {
    if (elRefresh) elRefresh.addEventListener("click", fetchLogs);

    // Poziom/limit => fetch (akcja usera)
    if (elLevel) elLevel.addEventListener("change", fetchLogs);
    if (elLimit) elLimit.addEventListener("change", fetchLogs);

    // Źródło => fetch (backend filters, searching all logs)
    if (elSource) elSource.addEventListener("change", fetchLogs);

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
    await loadKnownSources();
    await syncClockOffset();
    setInterval(syncClockOffset, 5000);
    await fetchLogs(); // jednorazowe wczytanie
  }

  document.addEventListener("DOMContentLoaded", init);
})();

