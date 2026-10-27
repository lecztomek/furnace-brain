// Statistics page: one report from /api/analytics/report, period switch on the client.
(function () {
  const REPORT_URL = "/api/analytics/report";
  const PERIOD_NAMES = { today: "dziś", yesterday: "wczoraj", d7: "ostatnie 7 dni", d30: "ostatnie 30 dni", season: "sezon" };
  const MODE_NAMES = { WORK: "praca", IGNITION: "rozpalanie", OFF: "stop", MANUAL: "ręcznie" };
  const STATUS_ICONS = { ok: "✓", warn: "⚠", bad: "✕", info: "ℹ" };

  const $ = (id) => document.getElementById(id);
  let report = null;
  // each card/tab has its own range (what doesn't depend on a range has none)
  const periods = { usage: "d7", work: "today", eff: "d30" };
  let period = "d7";   // the range of the card being drawn
  let dailyChart = null;
  let effChart = null;
  let weatherChart = null;
  let hourlyChart = null;
  let seasonChart = null;
  const MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];

  // coal price from the Statistics settings (0 = no costs)
  const price = () => Number(report.settings?.coal_price_pln_per_t) || 0;
  const pln = (kg) => (kg == null || !price() ? null : (kg / 1000) * price());

  // coal axis with the cost under each value ("12 kg" / "14 zł") and tooltips with both
  function coalAxisTicks(axis) {
    axis.ticks = { ...(axis.ticks || {}), callback: (v) => (price() ? [`${num(v, 0)} kg`, `${num(pln(v), v > 0 && v < 10 ? 1 : 0)} zł`] : `${num(v, v < 10 ? 1 : 0)}`) };
    return axis;
  }
  const coalTooltip = {
    callbacks: {
      label: (ctx) => {
        const v = ctx.parsed.y;
        if (ctx.dataset.coal && v != null) {
          return `${ctx.dataset.label.replace(/ \[.*\]$/, "")}: ${num(v, v < 10 ? 2 : 0)} kg` + (price() ? ` • ${num(pln(v), v < 10 ? 2 : 0)} zł` : "");
        }
        return `${ctx.dataset.label}: ${v == null ? "--" : num(v, 1)}`;
      },
    },
  };

  const num = (v, digits = 1) =>
    v === null || v === undefined ? "--" : Number(v).toLocaleString("pl-PL", { maximumFractionDigits: digits, minimumFractionDigits: digits });

  function tile(label, value, unit, cls = "") {
    return `<div class="st2-tile ${cls}"><span class="st2-label">${label}</span><span class="st2-value">${value}</span><span class="st2-unit">${unit || ""}</span></div>`;
  }

  // one look for every chart: big labels, thick lines without dots, no legend
  // unless there are two things to tell apart
  function baseOptions(legend = false) {
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0, hoverRadius: 5 }, line: { borderWidth: 3, tension: 0.3 } },
      plugins: { legend: { display: legend, labels: { color: "#e5e7eb", font: { size: 14 }, boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#cbd5e1", font: { size: 13 }, maxRotation: 0, autoSkip: true, autoSkipPadding: 12 }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: "#cbd5e1", font: { size: 13 } }, grid: { color: "rgba(255,255,255,0.08)" } },
      },
    };
  }
  function draw(chart, id, data, options) {
    if (chart) { chart.data = data; chart.options = options; chart.update(); return chart; }
    return new Chart($(id), { data, options });
  }

  // ---------- chart range = the chosen period ----------
  // today: the last 24 hours; yesterday: its 24 hours; longer periods: day by day
  function periodSeries(forPeriod = period) {
    const p = report.periods[forPeriod] || {};
    if (forPeriod === "today" || forPeriod === "yesterday") {
      const rows = (forPeriod === "today" ? p.last24 : p.hours24) || [];
      return { hourly: true, rows, labels: rows.map((h) => `${h.hour}:00`) };
    }
    const days = report.daily || [];
    let from = report.season_start || "";
    if (forPeriod === "d7" || forPeriod === "d30") {
      const d = new Date(report.today + "T12:00:00");
      d.setDate(d.getDate() - (forPeriod === "d7" ? 6 : 29));
      from = d.toISOString().slice(0, 10);
    }
    const rows = days.filter((d) => d.date >= from).map((d) => ({
      ...d, boiler: d.boiler_work, kwh: d.energy_kwh,
    }));
    return { hourly: false, rows, labels: rows.map((d) => d.date.slice(8, 10) + "." + d.date.slice(5, 7)) };
  }

  // ---------- render ----------

  function markRanges() {
    document.querySelectorAll(".st2-range").forEach((g) =>
      g.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.period === periods[g.dataset.scope])));
  }

  function renderUsage() {
    period = periods.usage;
    const p = report.periods[period];

    const perDayUnit = price() && p.coal_kg_per_day != null ? `kg na dobę • ${num(pln(p.coal_kg_per_day), 0)} zł` : "kg na dobę";
    $("usage-hero").innerHTML = [
      tile(`Węgiel (${PERIOD_NAMES[period]})`, num(p.coal_kg, p.coal_kg != null && p.coal_kg < 100 ? 1 : 0), "kg"),
      ...(price() ? [tile("Koszt", num(pln(p.coal_kg), 0), "zł", "st2-tile--cost")] : []),
      tile("Średnio", num(p.coal_kg_per_day, 1), perDayUnit),
    ].join("");
    const kgph = report.settings?.feeder_kg_per_hour;
    $("coal-note").textContent =
      `Okres: ${PERIOD_NAMES[period]}, dane z ${num(p.hours, 1)} h. ` +
      (kgph ? `Kilogramy liczone z czasu pracy ślimaka × ${kgph} kg/h (ustawienie modułu Statystyki - warto skalibrować). ` : "") +
      (price() ? `Koszt przy cenie ${num(price(), 0)} zł/t.` : "Koszt w zł: wpisz cenę węgla w Ustawienia → Statystyki.");

    renderDaily();
  }

  function renderWork() {
    period = periods.work;
    const p = report.periods[period];
    const a = p.avg || {};
    const modes = Object.entries(p.mode_share || {})
      .filter(([m]) => MODE_NAMES[m])
      .map(([m, v]) => `${MODE_NAMES[m]} ${num(v, 0)}%`)
      .join(", ");
    $("work-tiles").innerHTML = [
      tile("Średnia moc", num(p.power_kw_avg, 1), a.power != null ? `kW (sterownik ${num(a.power, 0)}%)` : "kW"),
      tile("Kocioł", num(a.boiler), "°C"),
      tile("Grzejniki", num(a.radiators), "°C"),
      tile("Na zewnątrz", num(a.outside), "°C"),
      tile("Rozpalenia", p.ignitions ?? "--", modes ? `czas: ${modes}` : ""),
    ].join("");

    const r = p.regulation;
    $("reg-grade").textContent = r ? r.grade : "";
    $("reg-grade").className = "st2-grade" + (r ? ` grade-${r.grade.replace(/\s/g, "-")}` : "");
    $("reg-tiles").innerHTML = r
      ? [
          tile("W normie (zadana ±2°C)", `${num(r.within_2, 0)}<small>%</small>`, "czasu pracy",
               r.within_2 >= 80 ? "st2-tile--good" : r.within_2 < 60 ? "st2-tile--bad" : ""),
          tile("Średnio od zadanej", `${num(r.mean_abs_error, 1)}<small>°C</small>`, `najniżej ${num(r.max_under, 1)}°C pod zadaną`),
        ].join("")
      : `<div class="st2-empty">Za mało pracy w trybie PRACA w tym okresie (potrzeba min. 30 min).</div>`;
    renderHourly();
  }

  function renderEfficiency() {
    period = periods.eff;
    const e = report.periods[period].efficiency || {};
    const t = report.efficiency_trend || {};
    const a = report.efficiency_assumptions || {};
    const signed = (v, d = 0) => (v == null ? "--" : `${v > 0 ? "+" : ""}${num(v, d)}`);
    const ch = t.change_pct;
    $("eff-tiles").innerHTML = [
      tile("Węgiel na stopniodzień", num(e.kg_per_degree_day, 2), `kg (${PERIOD_NAMES[period]})`),
      tile("Ostatni tydzień", ch == null ? "--" : `${ch > 0 ? "▲" : "▼"} ${num(Math.abs(ch), 0)}<small>%</small>`,
           ch == null ? "za mało dni z pogodą" : ch > 0 ? "więcej węgla niż wcześniej przy tej samej pogodzie" : "mniej węgla niż wcześniej przy tej samej pogodzie",
           ch == null ? "" : ch > 10 ? "st2-tile--bad" : ch <= 0 ? "st2-tile--good" : ""),
    ].join("");
    $("eff-note").textContent =
      `Stopniodzień = o ile średnia doby była niższa niż ${num(a.degree_day_base_c, 0)}°C (dzień 3°C = 15 stopniodni). ` +
      `Mniej kg na stopniodzień = sprawniej; wzrost przy podobnej pogodzie zwykle oznacza zabrudzony kocioł.`;

    const { rows: days, labels } = periodSeries(period);
    const data = {
      labels,
      datasets: [{ type: "line", label: "kg na stopniodzień", data: days.map((d) => d.kg_per_degree_day), borderColor: "#f97316", backgroundColor: "#f97316", spanGaps: true }],
    };
    // nothing to draw without outside temperatures - say so instead of an empty chart
    const any = days.some((d) => d.kg_per_degree_day != null);
    $("eff-chart-box").style.display = any ? "" : "none";
    $("eff-empty").style.display = any ? "none" : "";
    effChart = draw(effChart, "chart-efficiency", data, baseOptions());
  }

  // hour-of-day profile (when the boiler doesn't keep up) and the list of drops
  function renderHourly() {
    const p = report.periods[period];
    const { hourly, rows: hours, labels } = periodSeries();
    const last24 = hourly;
    const drops = p.drops || { count: 0, items: [], by_hour: [] };
    const band = (d) => hours.map((h) => (h.setpoint == null || h.boiler == null ? null : h.setpoint + d));
    const data = {
      labels,
      datasets: [
        { type: "line", label: "zadana -2°C", data: band(-2), borderWidth: 0, pointHoverRadius: 0, spanGaps: true },
        { type: "line", label: "zadana +2°C", data: band(2), borderWidth: 0, pointHoverRadius: 0, spanGaps: true,
          fill: "-1", backgroundColor: "rgba(74, 222, 128, 0.22)" },
        { type: "line", label: "Kocioł [°C]", data: hours.map((h) => h.boiler), borderColor: "#f97316", backgroundColor: "#f97316", spanGaps: false },
      ],
    };
    const options = baseOptions();
    options.scales.y.beginAtZero = false;
    options.scales.y.ticks.callback = (v) => `${v}°C`;
    // some room around the band and the line, so the band doesn't fill the chart
    const vals = hours.flatMap((h) => [h.boiler, h.setpoint != null ? h.setpoint - 2 : null, h.setpoint != null ? h.setpoint + 2 : null]).filter((v) => v != null);
    if (vals.length) {
      options.scales.y.suggestedMin = Math.floor(Math.min(...vals) - 2);
      options.scales.y.suggestedMax = Math.ceil(Math.max(...vals) + 2);
    }
    options.plugins.tooltip = { filter: (item) => item.datasetIndex === 2, callbacks: { label: (ctx) => {
      const h = hours[ctx.dataIndex] || {};
      return `kocioł ${num(ctx.parsed.y, 1)}°C (zadana ${num(h.setpoint, 0)}°C)`;
    } } };
    $("hourly-title").textContent =
      `Kocioł na tle normy (zielony pas = zadana ±2°C) - ${period === "today" ? "ostatnie 24 godziny" : period === "yesterday" ? "wczoraj, godzina po godzinie" : PERIOD_NAMES[period] + ", dzień po dniu"}`;
    hourlyChart = draw(hourlyChart, "chart-hourly", data, options);

    // worst hour of day over the chosen period (average per hour of day)
    const worst = (p.hourly || []).filter((h) => h.below_pct != null).sort((a, b) => b.below_pct - a.below_pct)[0];
    $("hourly-note").textContent =
      "Tylko czas w trybie PRACA (przerwy w linii: piec nie pracował). " +
      (worst && worst.below_pct > 0
        ? `Najgorsza godzina (${PERIOD_NAMES[period]}): ${worst.hour}:00–${worst.hour + 1}:00, ${num(worst.below_pct, 0)}% czasu ponad ${num(drops.below_c ?? 4, 0)}°C poniżej zadanej, średnio ${num(worst.error, 1)}°C od zadanej.`
        : `Bez wyraźnych spadków (${PERIOD_NAMES[period]}).`);

    $("drops-head").textContent = drops.count ? `Spadki temperatury: ${drops.count} (ostatnie 5)` : "Spadki temperatury";
    if (!drops.items.length) {
      $("drops").innerHTML = `<div class="st2-empty">Brak spadków ponad ${num(drops.below_c ?? 4, 0)}°C poniżej zadanej w tym okresie.</div>`;
      return;
    }
    const rows = drops.items.slice(0, 5).map((d) => {
      const dt = new Date(d.start);
      const when = dt.toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit" }) + " " + d.start.slice(11, 16);
      const deficit = d.setpoint - d.min_temp;
      return `<tr><td>${when}</td><td>${d.minutes} min</td><td class="${deficit >= 6 ? "bad" : ""}">${num(d.min_temp, 1)}°C</td>` +
        `<td>${num(d.setpoint, 0)}°C</td><td>${d.power_before ?? "--"}% → ${d.power_max ?? "--"}%</td></tr>`;
    }).join("");
    $("drops").innerHTML =
      `<table><thead><tr><th>Kiedy</th><th>Trwał</th><th>Najniżej</th><th>Zadana</th><th>Moc przed → maks.</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  function renderChecks() {
    $("checks").innerHTML = report.checks
      .map((c) => `<li class="st2-check st2-check--${c.status}"><span class="st2-check__icon">${STATUS_ICONS[c.status] || "•"}</span><span class="st2-check__title">${c.title}</span><span class="st2-check__detail">${c.detail}</span></li>`)
      .join("");
  }

  function renderDaily() {
    const { hourly, rows, labels } = periodSeries();
    const data = {
      labels,
      datasets: [{ type: "bar", label: "Węgiel", data: rows.map((d) => d.coal_kg), backgroundColor: "rgba(249, 115, 22, 0.8)", borderRadius: 4 }],
    };
    const options = baseOptions();
    coalAxisTicks(options.scales.y);
    options.plugins.tooltip = { callbacks: { label: (ctx) => {
      const d = rows[ctx.dataIndex] || {};
      const v = ctx.parsed.y;
      return [`${num(v, v < 10 ? 2 : 1)} kg` + (price() ? ` • ${num(pln(v), v < 10 ? 2 : 0)} zł` : ""),
              ...(d.kwh != null ? [`${num(d.kwh, 0)} kWh`] : []),
              ...(!hourly && d.outside != null ? [`na dworze ${num(d.outside, 1)}°C`] : [])];
    } } };
    $("daily-title").textContent = hourly
      ? `Węgiel w każdej godzinie - ${period === "today" ? "ostatnie 24 godziny" : "wczoraj"}`
      : `Węgiel każdego dnia - ${PERIOD_NAMES[period]}`;
    dailyChart = draw(dailyChart, "chart-daily", data, options);
  }

  function renderWeather() {
    const w = report.weather;
    const fit = w.coal.fit;
    const pfit = w.power.fit;
    $("weather-chart-box").style.display = fit ? "" : "none";
    if (!fit) {
      $("weather-text").innerHTML =
        `Zbieram dane: <b>${w.days_with_data} z ${w.days_needed}</b> pełnych dni z temperaturą zewnętrzną ` +
        `(i potrzebna różnica temperatur min. 3°C między dniami).`;
    } else {
      const at0 = fit.intercept;
      const perDeg = -fit.slope;
      $("weather-text").innerHTML =
        `Przy <b>0°C</b> spalasz ok. <b>${num(at0, 1)} kg</b> na dobę, przy <b>-10°C</b> ok. <b>${num(at0 - 10 * fit.slope, 1)} kg</b>. ` +
        `Każdy stopień chłodniej to <b>${perDeg >= 0 ? "+" : ""}${num(perDeg, 2)} kg</b> na dobę` +
        (w.energy?.fit ? ` (<b>${-w.energy.fit.slope >= 0 ? "+" : ""}${num(-w.energy.fit.slope, 1)} kWh</b>; przy 0°C ok. <b>${num(w.energy.fit.intercept, 0)} kWh</b> na dobę)` : "") +
        (pfit ? ` i <b>${-pfit.slope >= 0 ? "+" : ""}${num(-pfit.slope, 1)}%</b> mocy` : "") +
        `. <span class="st2-muted">Na podstawie ${fit.n} dni, dopasowanie R² = ${num(fit.r2, 2)}.</span>`;
    }

    const pts = w.coal.points.map((p) => ({ x: p.x, y: p.y }));
    const datasets = [{ type: "scatter", label: "Dni", data: pts, backgroundColor: "rgba(249, 115, 22, 0.85)", pointRadius: 5 }];
    if (fit && pts.length) {
      const xs = pts.map((p) => p.x);
      const lo = Math.min(...xs) - 1, hi = Math.max(...xs) + 1;
      datasets.push({ type: "line", label: "Trend", data: [{ x: lo, y: fit.intercept + fit.slope * lo }, { x: hi, y: fit.intercept + fit.slope * hi }], borderColor: "#e5e7eb", borderDash: [6, 4], pointRadius: 0 });
    }
    const options = {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { labels: { color: "#e5e7eb" } } },
      scales: {
        x: { type: "linear", title: { display: true, text: "Średnia temperatura zewnętrzna [°C]", color: "#9ca3af" }, ticks: { color: "#9ca3af" }, grid: { color: "rgba(255,255,255,0.05)" } },
        y: { beginAtZero: true, title: { display: true, text: "Węgiel [kg/dobę]", color: "#9ca3af" }, ticks: { color: "#fdba74" }, grid: { color: "rgba(255,255,255,0.08)" } },
      },
    };
    options.plugins.legend.display = false;
    weatherChart = draw(weatherChart, "chart-weather", { datasets }, options);
  }

  // this season so far vs the previous one, month by month; forecast for the season
  function renderSeasons() {
    const s = report.seasons;
    if (!s) return;
    const f = s.forecast || {};
    // tonnes from 1 t up, kilograms below
    const mass = (kg) => (kg == null ? ["--", "t"] : kg >= 1000 ? [num(kg / 1000, 2), "t"] : [num(kg, 0), "kg"]);
    const [usedV, usedU] = mass(s.this.coal_kg);
    const [fcV, fcU] = mass(f.forecast_kg);
    const [prevV, prevU] = mass(s.previous.coal_kg);
    const vsPrev = s.previous.coal_kg_to_date != null && s.previous.coal_kg_to_date > 0
      ? `${num(100 * (s.this.coal_kg - s.previous.coal_kg_to_date) / s.previous.coal_kg_to_date, 0)}% vs rok temu (${mass(s.previous.coal_kg_to_date).join(" ")})`
      : "rok temu w tym czasie: brak danych";
    const zl = (kg) => (price() && kg != null ? ` • ${num(pln(kg), 0)} zł` : "");
    $("season-tiles").innerHTML = [
      tile("Ten sezon do dziś", `${usedV}<small>${usedU}</small>`, `${zl(s.this.coal_kg).slice(3)}${zl(s.this.coal_kg) ? " • " : ""}${vsPrev}`),
      tile("Prognoza na cały sezon", f.forecast_kg != null ? `${fcV}<small>${fcU}</small>` : "--",
           f.forecast_kg != null ? `${zl(f.forecast_kg).slice(3)} przy typowej pogodzie` : "za mało dni z pogodą", "st2-tile--cost"),
      tile("Zeszły sezon", `${prevV}<small>${prevU}</small>`, `${zl(s.previous.coal_kg).slice(3)} (z dni, z których są dane)`),
    ].join("");
    $("season-note").textContent =
      (f.forecast_kg != null
        ? `Prognoza: zużyte ${num(f.used_kg, 0)} kg + ${num(f.kg_per_degree_day, 2)} kg na stopniodzień (ostatnie ${f.based_on_days} pełnych dni) ` +
          `× ${num(f.degree_days_left, 0)} stopniodni do końca maja przy typowej pogodzie dla Nowego Sącza. `
        : "Prognoza pojawi się po kilku pełnych dniach z temperaturą zewnętrzną. ") +
      "";

    const months = s.this.months.map((m) => MONTHS[m.month - 1]);
    const data = {
      labels: months,
      datasets: [
        { type: "bar", label: `Sezon ${s.previous.start.slice(0, 4)}/${String(Number(s.previous.start.slice(0, 4)) + 1).slice(2)} [kg]`, data: s.previous.months.map((m) => m.coal_kg), backgroundColor: "rgba(156, 163, 175, 0.6)", coal: true },
        { type: "bar", label: `Sezon ${s.this.start.slice(0, 4)}/${String(Number(s.this.start.slice(0, 4)) + 1).slice(2)} [kg]`, data: s.this.months.map((m) => m.coal_kg), backgroundColor: "rgba(249, 115, 22, 0.8)", coal: true },
      ],
    };
    const options = baseOptions(true);
    coalAxisTicks(options.scales.y);
    options.plugins.tooltip = coalTooltip;
    seasonChart = draw(seasonChart, "chart-seasons", data, options);
  }

  function render() {
    markRanges();
    renderUsage();
    renderSeasons();
    renderWork();
    renderEfficiency();
    renderChecks();
    renderWeather();
  }

  // ---------- sub-pages ----------

  function showTab(tab) {
    if (!document.querySelector(`#st2-tabs button[data-tab="${tab}"]`)) tab = "usage";
    document.querySelectorAll("#st2-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".st2-card[data-tab]").forEach((c) => c.classList.toggle("is-hidden", c.dataset.tab !== tab));
    // charts drawn while hidden have no size - redraw the visible ones
    [dailyChart, seasonChart, hourlyChart, effChart, weatherChart].forEach((c) => c && c.resize());
    try { localStorage.setItem("stats-tab", tab); } catch (err) { /* private mode */ }
  }

  // "120 z 420 dni (29%)" while a build reads the days
  function progressText(p) {
    if (!p || !p.total) return "";
    return ` (${p.done} z ${p.total} dni, ${Math.round((100 * p.done) / p.total)}%)`;
  }

  // ---------- data ----------

  async function load(fresh = false) {
    $("st2-status").textContent = "Wczytywanie…";
    try {
      const res = await fetch(fresh ? `${REPORT_URL}?fresh=1` : REPORT_URL, { cache: "no-store" });
      if (res.status === 503) {
        // the first report is still being built (after an update) - ask again soon
        const pr = (await res.json().catch(() => ({})))?.detail?.progress;
        $("st2-status").textContent = "Liczę statystyki (pierwszy raz po aktualizacji może to potrwać kilka minut)" + progressText(pr) + "…";
        clearTimeout(load.retry);
        load.retry = setTimeout(() => load(), 15000);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      report = await res.json();
      render();
      const at = report.generated_at ? new Date(report.generated_at * 1000) : new Date();
      const hhmm = at.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
      // after a restart: the last saved report first, the fresh one comes a moment later
      $("st2-status").textContent = report.stale
        ? `Stan na ${hhmm} • liczę aktualne${progressText(report.progress)}…`
        : `Stan na ${hhmm} • sezon od ${report.season_start}`;
      clearTimeout(load.retry);
      if (report.stale) load.retry = setTimeout(() => load(), 10000);
    } catch (err) {
      console.error(err);
      $("st2-status").textContent = `Nie udało się wczytać statystyk: ${err.message}`;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".st2-range").forEach((g) => g.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-period]");
      if (!btn || !report) return;
      periods[g.dataset.scope] = btn.dataset.period;
      markRanges();
      ({ usage: renderUsage, work: renderWork, eff: renderEfficiency })[g.dataset.scope]();
    }));
    $("st2-refresh").addEventListener("click", () => load(true));
    $("st2-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-tab]");
      if (btn) showTab(btn.dataset.tab);
    });
    let saved = null;
    try { saved = localStorage.getItem("stats-tab"); } catch (err) { /* private mode */ }
    showTab(saved || "usage");
    load();
  });
})();
