// js/stats.js
(function () {
  const STATS_API_BASE = "http://127.0.0.1:8000/api/stats";

  function setStatus(text, isError = false) {
    const el = document.getElementById("status-text");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("status-error", !!isError);
  }

  const el = (id) => document.getElementById(id);

  function fmtNum(v, digits = 2) {
    if (v === null || v === undefined) return "—";
    const n = Number(v);
    if (Number.isNaN(n)) return "—";
    return n.toFixed(digits);
  }

  function fmtTs(tsIso, tsUnix) {
    if (tsIso) return String(tsIso).replace("T", " ").slice(0, 19);
    const n = Number(tsUnix);
    if (!Number.isFinite(n) || n <= 0) return "—";
    const d = new Date(n * 1000);
    return d.toISOString().replace("T", " ").slice(0, 19);
  }

  function setText(id, text) {
    const node = el(id);
    if (!node) return;
    node.textContent = text;
  }

  function setCardValue(id, value, digits) {
    setText(id, fmtNum(value, digits));
  }

  function setFoot(id, label, value, digits, unit) {
    const txt = fmtNum(value, digits);
    setText(id, txt === "—" ? "—" : `${label}: ${txt} ${unit}`);
  }

  async function fetchStatsData() {
    const url = `${STATS_API_BASE}/data`;
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) {
      const txt = await res.text().catch(() => "");
      throw new Error(`Błąd pobierania stats: HTTP ${res.status} ${txt}`);
    }
    const payload = await res.json();
    if (!payload || typeof payload !== "object" || !payload.data) {
      throw new Error("Nieprawidłowa odpowiedź API /stats/data");
    }
    return payload.data;
  }

  function applyStats(data) {
    // top pills
    setText("stats-status", data.enabled ? "OK" : "WYŁĄCZONE");
    setText("stats-ts", fmtTs(data.ts_iso, data.ts_unix));

    const cal = data.calorific_mj_per_kg;
    const feeder = data.feeder_kg_per_hour;

    setText(
      "stats-cal",
      cal === null || cal === undefined ? "—" : `${fmtNum(cal, 1)} MJ/kg`
    );
    setText(
      "stats-feeder",
      feeder === null || feeder === undefined ? "—" : `${fmtNum(feeder, 2)} kg/h`
    );

    // spalanie (kg/h)
    setCardValue("burn_kgph_5m", data.burn_kgph_5m, 2);
    setCardValue("burn_kgph_1h", data.burn_kgph_1h, 2);
    setCardValue("burn_kgph_4h", data.burn_kgph_4h, 2);
    setCardValue("burn_kgph_24h", data.burn_kgph_24h, 2);
    setCardValue("burn_kgph_7d", data.burn_kgph_7d, 2);

    // zużycie (kg) – sumy
    setFoot("coal_kg_5m", "Zużycie", data.coal_kg_5m, 3, "kg");
    setFoot("coal_kg_1h", "Zużycie", data.coal_kg_1h, 3, "kg");
    setFoot("coal_kg_4h", "Zużycie", data.coal_kg_4h, 3, "kg");
    setFoot("coal_kg_24h", "Zużycie", data.coal_kg_24h, 3, "kg");
    setFoot("coal_kg_7d", "Zużycie", data.coal_kg_7d, 3, "kg");

    // moc (kW)
    setCardValue("power_kw_5m", data.power_kw_5m, 2);
    setCardValue("power_kw_1h", data.power_kw_1h, 2);
    setCardValue("power_kw_4h", data.power_kw_4h, 2);
    setCardValue("power_kw_24h", data.power_kw_24h, 2);
    setCardValue("power_kw_7d", data.power_kw_7d, 2);

    // energia (kWh)
    setFoot("energy_kwh_5m", "Energia", data.energy_kwh_5m, 3, "kWh");
    setFoot("energy_kwh_1h", "Energia", data.energy_kwh_1h, 3, "kWh");
    setFoot("energy_kwh_4h", "Energia", data.energy_kwh_4h, 3, "kWh");
    setFoot("energy_kwh_24h", "Energia", data.energy_kwh_24h, 3, "kWh");
    setFoot("energy_kwh_7d", "Energia", data.energy_kwh_7d, 3, "kWh");
  }

  async function reloadStats() {
    try {
      setStatus("Ładowanie statystyk...");
      const data = await fetchStatsData();

      applyStats(data);

      // drobny komunikat w stylu historii
      setStatus(`Załadowano statystyki (${fmtTs(data.ts_iso, data.ts_unix)}).`);
    } catch (err) {
      console.error(err);
      setStatus(
        err && err.message ? `Błąd statystyk: ${err.message}` : "Błąd odczytu statystyk.",
        true
      );
    }
  }

  function initRefreshButton() {
    // jak w historii: szukamy po klasie
    const btn = document.querySelector(".history-refresh-btn");
    if (!btn) return;

    btn.addEventListener("click", () => {
      reloadStats();
    });
  }

  async function initStatsView() {
    try {
      setStatus("Inicjalizacja widoku statystyk...");
      initRefreshButton();
      await reloadStats();
    } catch (err) {
      console.error(err);
      setStatus(
        err && err.message
          ? `Błąd inicjalizacji statystyk: ${err.message}`
          : "Błąd inicjalizacji widoku statystyk.",
        true
      );
    }
  }

  document.addEventListener("DOMContentLoaded", initStatsView);
})();

