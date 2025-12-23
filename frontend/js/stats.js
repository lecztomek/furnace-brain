// js/stats.js
(function () {
  const STATS_API_BASE = "/api/stats";
  const el = (id) => document.getElementById(id);

  function setStatus(text, isError = false) {
    const node = el("status-text");
    if (!node) return;
    node.textContent = text || "";
    node.classList.toggle("status-error", !!isError);
  }

  function fmtNum(v, digits = 2) {
    if (v === null || v === undefined) return "—";
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
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

  function clearSvg(svg) {
    while (svg && svg.firstChild) svg.removeChild(svg.firstChild);
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

  // ========= SVG helpers =========

  function svgEl(tag, attrs = {}) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
    return n;
  }

  // line chart (kg/h)
  function renderLine(svgId, points) {
    const svg = el(svgId);
    if (!svg) return;
    clearSvg(svg);

    // bierz faktyczny rozmiar z DOM
    const rect = svg.getBoundingClientRect();
    const w = Math.max(320, Math.floor(rect.width || 560));
    const h = 180;

    // ustaw viewBox tak, żeby współrzędne mapowały się na całą szerokość
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("preserveAspectRatio", "none");

    const padL = 46, padR = 12, padT = 14, padB = 24;
    const innerW = w - padL - padR;
    const innerH = h - padT - padB;

    // grid
    for (let i = 0; i <= 3; i++) {
      const y = padT + (innerH * i) / 3;
      svg.appendChild(svgEl("line", {
        x1: padL, x2: w - padR, y1: y, y2: y,
        stroke: "rgba(255,255,255,0.10)", "stroke-width": 1
      }));
    }

    const vals = points.map(p => p.v).filter(v => Number.isFinite(v));
    const max = Math.max(1, ...vals);
    const min = 0;

    // y labels 0 / mid / max
    const labels = [
      { t: `${Math.round(max)} kg/h`, y: padT },
      { t: `${Math.round(max/2)} kg/h`, y: padT + innerH/2 },
      { t: `0 kg/h`, y: padT + innerH }
    ];
    labels.forEach(L => {
      const tx = svgEl("text", {
        x: 8, y: L.y + 5,
        fill: "rgba(255,255,255,0.65)",
        "font-size": 14,
        "font-weight": 800
      });
      tx.textContent = L.t;
      svg.appendChild(tx);
    });

    // path
    const n = points.length;
    if (n < 2) return;

    const xy = points.map((p, i) => {
      const x = padL + (innerW * i) / (n - 1);
      const v = Number.isFinite(p.v) ? p.v : 0;
      const y = padT + innerH - ((v - min) / (max - min)) * innerH;
      return { x, y, v, label: p.label };
    });

    const d = xy.map((p, i) => (i === 0 ? `M ${p.x.toFixed(1)} ${p.y.toFixed(1)}` : `L ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)).join(" ");
    svg.appendChild(svgEl("path", {
      d,
      fill: "none",
      stroke: "rgba(96,165,250,0.85)",
      "stroke-width": 3,
      "stroke-linecap": "round",
      "stroke-linejoin": "round"
    }));

    // dots
    xy.forEach((p) => {
      svg.appendChild(svgEl("circle", {
        cx: p.x, cy: p.y, r: 6,
        fill: "rgba(96,165,250,0.85)",
        stroke: "rgba(17,24,39,0.9)",
        "stroke-width": 2
      }));
    });
  }

  // sparkline for totals (kg)
  function renderSpark(svgId, series, key) {
    const svg = el(svgId);
    if (!svg) return;
    clearSvg(svg);

    const w = 120, h = 34;
    const pad = 4;

    if (!Array.isArray(series) || series.length < 2) {
      // placeholder line
      svg.appendChild(svgEl("line", { x1: pad, x2: w-pad, y1: h/2, y2: h/2, stroke: "rgba(255,255,255,0.15)", "stroke-width": 2 }));
      return;
    }

    const vals = series.map(s => Number(s?.[key])).filter(v => Number.isFinite(v));
    const max = Math.max(1e-9, ...vals);
    const n = series.length;

    const pts = series.map((s, i) => {
      const x = pad + ((w - 2*pad) * i) / (n - 1);
      const v = Number.isFinite(Number(s?.[key])) ? Number(s[key]) : 0;
      const y = (h - pad) - (v / max) * (h - 2*pad);
      return { x, y };
    });

    const d = pts.map((p, i) => (i === 0 ? `M ${p.x.toFixed(1)} ${p.y.toFixed(1)}` : `L ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)).join(" ");
    svg.appendChild(svgEl("path", {
      d, fill: "none",
      stroke: "rgba(251,191,36,0.90)", /* bursztyn */
      "stroke-width": 2.6,
      "stroke-linecap": "round",
      "stroke-linejoin": "round"
    }));

    // last dot
    const last = pts[pts.length - 1];
    svg.appendChild(svgEl("circle", {
      cx: last.x, cy: last.y, r: 3.5,
      fill: "rgba(251,191,36,0.95)"
    }));
  }

  function setXLabels(containerId, labels) {
    const box = el(containerId);
    if (!box) return;
    box.innerHTML = "";
    (labels || []).forEach(t => {
      const s = document.createElement("div");
      s.textContent = t;
      box.appendChild(s);
    });
  }

  // ========= apply =========

function applyStats(data) {
  setText("stats-status", data.enabled ? "OK" : "WYŁĄCZONE");
  setText("stats-ts", fmtTs(data.ts_iso, data.ts_unix));

  // OBECNIE (kg/h)
  setText("burn-now", fmtNum(data.burn_kgph_5m, 2));
  setText("coal-now-5m", fmtNum(data.coal_kg_5m, 3));

  // totals (kg) — liczby bez “Zużycie:”
  setText("coal-5m", fmtNum(data.coal_kg_5m, 3));
  setText("coal-1h", fmtNum(data.coal_kg_1h, 3));
  setText("coal-4h", fmtNum(data.coal_kg_4h, 3));
  setText("coal-24h", fmtNum(data.coal_kg_24h, 3));
  setText("coal-7d", fmtNum(data.coal_kg_7d, 3));

  // WYKRES (kg/h) — bierzemy compare_bars.minutes_5m + TERAZ
  const cmp = data.compare_bars || {};
  const s5m = Array.isArray(cmp.minutes_5m) ? cmp.minutes_5m : [];

  // punkty do linii: minutes_5m + TERAZ
  const pts = [];
  const rawLabels = [];

  for (const it of s5m) {
    const v = Number(it?.burn_kgph_avg ?? it?.burn_kgph);
    pts.push({ label: String(it?.label ?? ""), v: Number.isFinite(v) ? v : 0 });
    rawLabels.push(String(it?.label ?? ""));
  }
  pts.push({ label: "TERAZ", v: Number(data.burn_kgph_5m) || 0 });
  rawLabels.push("TERAZ");

  // pokaż tylko 3 labelki z serii + TERAZ (reszta pusta, żeby się mieściło)
  const n = rawLabels.length;
  const keepIdx = new Set([
    0,
    Math.floor((n - 2) / 2), // środek (bez TERAZ)
    n - 2,                  // ostatni z serii
    n - 1                   // TERAZ
  ]);
  const xlabels = rawLabels.map((t, i) => (keepIdx.has(i) ? t : ""));

  renderLine(
    "burn-line",
    pts.length >= 2
      ? pts
      : [
          { label: "TERAZ", v: Number(data.burn_kgph_5m) || 0 },
          { label: "TERAZ", v: Number(data.burn_kgph_5m) || 0 }
        ]
  );
  setXLabels("burn-xlabels", xlabels);

  // SPARKLINES (kg): 5m z minutes_5m, 1h z hours_1h, reszta jeśli backend da
  const s1h = Array.isArray(cmp.hours_1h) ? cmp.hours_1h : [];
  const s4h = Array.isArray(cmp.blocks_4h) ? cmp.blocks_4h : [];
  const s24 = Array.isArray(cmp.days_24h) ? cmp.days_24h : [];
  const s7d = Array.isArray(cmp.days_7d) ? cmp.days_7d : [];

  renderSpark("spark-5m", s5m, "coal_kg_sum");
  renderSpark("spark-1h", s1h, "coal_kg_sum");
  renderSpark("spark-4h", s4h, "coal_kg_sum");
  renderSpark("spark-24h", s24, "coal_kg_sum");
  renderSpark("spark-7d", s7d, "coal_kg_sum");

  // ===== PODSUMOWANIE (today + 1h + ratio podajnika) =====
  const today = (data && data.calendar && data.calendar.today) ? data.calendar.today : {};

  const activeSeconds = Number(today.active_seconds ?? today.seconds_sum);
  const activeRatio = Number(today.active_ratio);

  function fmtDur(sec) {
    if (!Number.isFinite(sec) || sec <= 0) return "—";
    const s = Math.floor(sec);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h <= 0) return `${m} min`;
    return `${h}h ${m}m`;
  }

  const burnTodayAvg = Number(today.burn_kgph_avg);
  const coalTodaySum = Number(today.coal_kg_sum);

  // stabilność 1h: zakres i % względem avg 1h
  const burn1h = Number(data.burn_kgph_1h);
  const burn1hMin = Number(data.burn_kgph_min_1h);
  const burn1hMax = Number(data.burn_kgph_max_1h);

  let stability1h = "—";
  if (Number.isFinite(burn1h) && burn1h > 0 && Number.isFinite(burn1hMin) && Number.isFinite(burn1hMax)) {
    const range = burn1hMax - burn1hMin;
    const pct = (range / burn1h) * 100;
    stability1h = `${range.toFixed(2)} kg/h (${pct.toFixed(0)}%)`;
  }

  // wykorzystanie podajnika "teraz"
  const feeder = Number(data.feeder_kg_per_hour);
  const burnNow = Number(data.burn_kgph_5m);
  let feederNow = "—";
  if (Number.isFinite(feeder) && feeder > 0 && Number.isFinite(burnNow)) {
    feederNow = `${((burnNow / feeder) * 100).toFixed(0)}%`;
  }

  // PRACA DZIŚ: czas + % (jeśli jest)
  let activeText = fmtDur(activeSeconds);
  if (Number.isFinite(activeRatio)) activeText += ` (${(activeRatio * 100).toFixed(0)}%)`;

  setText("sum-active-today", activeText);
  setText("sum-burn-today", Number.isFinite(burnTodayAvg) ? burnTodayAvg.toFixed(2) : "—");
  setText("sum-coal-today", Number.isFinite(coalTodaySum) ? coalTodaySum.toFixed(2) : "—");
  setText("sum-stability-1h", stability1h);
  setText("sum-feeder-now", feederNow);
}

  async function reloadStats() {
    try {
      setStatus("Ładowanie statystyk...");
      const data = await fetchStatsData();
      applyStats(data);
      setStatus(`Załadowano statystyki (${fmtTs(data.ts_iso, data.ts_unix)}).`);
    } catch (err) {
      console.error(err);
      setStatus(err && err.message ? `Błąd statystyk: ${err.message}` : "Błąd odczytu statystyk.", true);
    }
  }

  function initRefreshButton() {
    const btn = document.querySelector(".history-refresh-btn");
    if (!btn) return;
    btn.addEventListener("click", () => reloadStats());
  }

  function initStatsView() {
    setStatus("Inicjalizacja widoku statystyk...");
    initRefreshButton();
    reloadStats();
  }

  document.addEventListener("DOMContentLoaded", initStatsView);
})();

