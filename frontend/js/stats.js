// Statistics page: one report from /api/analytics/report, period switch on the client.
(function () {
  const REPORT_URL = "/api/analytics/report";
  const PERIOD_NAMES = { today: "dziś", yesterday: "wczoraj", d7: "ostatnie 7 dni", d30: "ostatnie 30 dni", season: "sezon" };
  const MODE_NAMES = { WORK: "praca", IGNITION: "rozpalanie", OFF: "stop", MANUAL: "ręcznie" };

  const $ = (id) => document.getElementById(id);
  let report = null;
  // each card/tab has its own range (what doesn't depend on a range has none)
  const periods = { usage: "d7", work: "today", eff: "d30" };
  let period = "d7";   // the range of the card being drawn
  let dailyChart = null;
  let effChart = null;
  let weatherChart = null;
  let hourlyChart = null;
  let powerKwhChart = null;
  let powerPctChart = null;
  let seasonChart = null;
  let forecastChart = null;
  let forecast = null;
  const MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];

  // coal price from the Statistics settings (0 = no costs)
  const price = () => Number(report.settings?.coal_price_pln_per_t) || 0;
  const pln = (kg) => (kg == null || !price() ? null : (kg / 1000) * price());

  // coal charts: kg on the left axis, the same scale in zł on the right one
  function coalAxisTicks(axis, options) {
    axis.ticks = { ...(axis.ticks || {}), callback: (v) => `${num(v, v > 0 && v < 10 ? 1 : 0)} kg` };
    if (options && price()) {
      options.scales.yzl = {
        position: "right",
        bounds: "data",          // exactly the converted kg range, not rounded up to "nice" ticks
        grid: { display: false },
        ticks: { color: "#fde68a", font: { size: 15 }, callback: (v) => `${num(v, v > 0 && v < 10 ? 1 : 0)} zł` },
        // the kg axis range converted to zł, so both sides point at the same bar height
        afterDataLimits: (scale) => {
          const y = scale.chart.scales.y;
          scale.min = (y.min / 1000) * price();
          scale.max = (y.max / 1000) * price();
        },
        // the zł labels sit on the kg grid lines (30 kg | 36 zł)
        afterBuildTicks: (scale) => {
          scale.ticks = scale.chart.scales.y.ticks.map((t) => ({ value: (t.value / 1000) * price() }));
        },
      };
    }
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

  // big tile with an icon (same look as the service page): a short unit goes next to
  // the value, anything longer in one line under it
  const TILE_ICONS = [["Węgiel na", "target"], ["Węgiel", "coal"], ["Koszt", "coins"], ["Średnia moc", "zap"], ["Szczyt", "trendUp"], ["Moc sterownika", "gauge"],
    ["Na pełnej mocy", "flame"], ["Na minimum", "power"], ["Średnio", "bars"],
    ["Kocioł", "flame"], ["Grzejniki", "radiator"], ["Na zewnątrz", "thermometer"], ["Rozpalenia", "flame"], ["W normie", "target"],
    ["Od zadanej", "upDown"], ["Ostatni tydzień", "trendUp"], ["Ten sezon", "calendar"], ["Prognoza", "sparkles"], ["Zeszły sezon", "history"],
    ["Przy 0", "cloudSun"], ["Przy -10", "snowflake"], ["Każdy stopień", "thermometer"]];
  function tile(label, value, unit, cls = "") {
    const icon = FBIcon((TILE_ICONS.find(([k]) => label.startsWith(k)) || ["", "info"])[1]);
    const st = cls.includes("good") ? "ok" : cls.includes("bad") ? "bad" : cls.includes("cost") ? "cost" : "plain";
    const short = unit && unit.length <= 3;
    return `<div class="sv-tile sv-tile--${st}"><span class="sv-tile__icon">${icon}</span><span class="sv-tile__label">${label}</span>` +
      `<span class="sv-tile__val">${value}${short ? `<small>${unit}</small>` : ""}</span>${unit && !short ? `<span class="sv-tile__sub">${unit}</span>` : ""}</div>`;
  }

  // one look for every chart: big labels, thick lines without dots, no legend
  // unless there are two things to tell apart
  function baseOptions(legend = false) {
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0, hoverRadius: 5 }, line: { borderWidth: 3, tension: 0.3 } },
      plugins: { legend: { display: legend, labels: { color: "#e5e7eb", font: { size: 15 }, boxWidth: 16 } } },
      scales: {
        x: { ticks: { color: "#cbd5e1", font: { size: 15 }, maxRotation: 0, autoSkip: true, autoSkipPadding: 12 }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: "#cbd5e1", font: { size: 15 } }, grid: { color: "rgba(255,255,255,0.08)" } },
      },
    };
  }
  // STOP breaks (coal added, ash taken out) as a strip under the chart, below the hour labels -
  // nothing is drawn over the lines. options.plugins.stopBands = { flags: [bool per label] }
  // (stopOptions() also makes room for the strip)
  const STOP_STRIP_H = 10;
  const STOP_COLOR = "#cbd5e1";
  Chart.register({
    id: "stopBands",
    afterDraw(chart, args, opts) {
      const flags = opts && opts.flags;
      if (!flags || !flags.some(Boolean)) return;
      const x = chart.scales.x, area = chart.chartArea, c = chart.ctx;
      const step = flags.length > 1 ? x.getPixelForValue(1) - x.getPixelForValue(0) : area.width;
      const top = chart.height - STOP_STRIP_H - 2;
      c.save();
      // the track: where there was no STOP
      c.fillStyle = "rgba(255, 255, 255, 0.07)";
      c.fillRect(area.left, top, area.right - area.left, STOP_STRIP_H);
      c.fillStyle = STOP_COLOR;
      for (let i = 0; i < flags.length; i++) {
        if (!flags[i]) continue;
        let j = i;
        while (j + 1 < flags.length && flags[j + 1]) j++;
        // a STOP of a few minutes is 1-2 points: at least 8 px wide so it shows
        const mid = (x.getPixelForValue(i) + x.getPixelForValue(j)) / 2;
        const w = Math.max(8, (j - i + 1) * step);
        const left = Math.max(area.left, mid - w / 2);
        c.fillRect(left, top, Math.min(area.right, mid + w / 2) - left, STOP_STRIP_H);
        i = j;
      }
      c.restore();
    },
  });
  function stopOptions(options, fine) {
    options.plugins.stopBands = { flags: fine.map((f) => !!f.stop) };
    options.layout = { padding: { bottom: STOP_STRIP_H + 8 } };
  }

  // the last STOP breaks as a table: when, until, how long
  function renderStops(stops) {
    const list = (stops || []).slice().reverse();
    $("stops-box").style.display = list.length ? "" : "none";
    if (!list.length) return;
    $("stops-head").textContent = `Postoje (STOP): ${list.length} • ostatnie 24 h`;
    const hm = (iso) => iso.slice(11, 16);
    const rows = list.map((x) => {
      const day = `${x.start.slice(8, 10)}.${x.start.slice(5, 7)}`;
      return `<tr><td>${day} ${hm(x.start)}</td><td>${hm(x.end)}</td><td>${x.minutes} min</td></tr>`;
    }).join("");
    $("stops").innerHTML = `<table><thead><tr><th>Od</th><th>Do</th><th>Trwał</th></tr></thead><tbody>${rows}</tbody></table>`;
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

  // "dziś od północy", "ostatnie 7 dni (od 22.10)" - what a card counts from, next to its title
  function periodText(key) {
    if (!report) return "";
    if (key === "today") return "dziś od północy";
    if (key === "yesterday") return "wczoraj";
    const d = new Date(report.today + "T12:00:00");
    if (key === "season") d.setTime(new Date((report.season_start || report.today) + "T12:00:00").getTime());
    else d.setDate(d.getDate() - (key === "d7" ? 6 : 29));
    const from = `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
    return `${PERIOD_NAMES[key]} (od ${from})`;
  }

  function markRanges() {
    document.querySelectorAll(".st2-range").forEach((g) =>
      g.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.period === periods[g.dataset.scope])));
    document.querySelectorAll("[data-period-of]").forEach((el) => { el.textContent = periodText(periods[el.dataset.periodOf]); });
  }

  function renderUsage() {
    period = periods.usage;
    const p = report.periods[period];

    const perDayUnit = price() && p.coal_kg_per_day != null ? `kg/doba • ${num(pln(p.coal_kg_per_day), 0)} zł` : "kg na dobę";
    $("usage-hero").innerHTML = [
      tile("Węgiel", num(p.coal_kg, p.coal_kg != null && p.coal_kg < 100 ? 1 : 0), "kg"),
      ...(price() ? [tile("Koszt", num(pln(p.coal_kg), 0), "zł", "st2-tile--cost")] : []),
      tile("Średnio", num(p.coal_kg_per_day, 1), perDayUnit),
    ].join("");
    $("coal-note").textContent = price() ? `Cena węgla: ${num(price(), 0)} zł/t` : "Wpisz cenę węgla w Ustawienia → Statystyki, żeby widzieć złotówki.";

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
      tile("Kocioł", num(a.boiler), "°C"),
      tile("Grzejniki", num(a.radiators), "°C"),
      tile("Na zewnątrz", num(a.outside), "°C"),
      tile("Rozpalenia", p.ignitions ?? "--", ""),
    ].join("");

    const r = p.regulation;
    $("reg-grade").textContent = r ? r.grade : "";
    $("reg-grade").className = "st2-grade" + (r ? ` grade-${r.grade.replace(/\s/g, "-")}` : "");
    $("reg-tiles").innerHTML = r
      ? [
          tile("W normie ±2°C", `${num(r.within_2, 0)}<small>%</small>`, "",
               r.within_2 >= 80 ? "st2-tile--good" : r.within_2 < 60 ? "st2-tile--bad" : ""),
          tile("Od zadanej", `${num(r.mean_abs_error, 1)}<small>°</small>`, `najniżej −${num(r.max_under, 1)}°`),
        ].join("")
      : `<div class="st2-empty">${FBIcon("hourglass", "ic--inline")} Za mało pracy w tym okresie</div>`;
    renderHourly();
    renderPower();
  }

  function renderEfficiency() {
    period = periods.eff;
    const e = report.periods[period].efficiency || {};
    const t = report.efficiency_trend || {};
    const a = report.efficiency_assumptions || {};
    const signed = (v, d = 0) => (v == null ? "--" : `${v > 0 ? "+" : ""}${num(v, d)}`);
    const ch = t.change_pct;
    $("eff-tiles").innerHTML = [
      tile("Węgiel na stopniodzień", num(e.kg_per_degree_day, 2), "kg"),
      tile("Ostatni tydzień", ch == null ? "--" : `${ch > 0 ? "▲" : "▼"} ${num(Math.abs(ch), 0)}<small>%</small>`,
           ch == null ? "za mało dni" : ch > 0 ? "więcej węgla" : "mniej węgla",
           ch == null ? "" : ch > 10 ? "st2-tile--bad" : ch <= 0 ? "st2-tile--good" : ""),
    ].join("");
    $("eff-note").textContent = `Stopniodzień: dzień ze średnią 3°C = ${num((a.degree_day_base_c ?? 18) - 3, 0)}. Mniej kg = sprawniej.`;

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

  // heat output in kWh, the controller's power in % and how steady it was
  function renderPower() {
    const p = report.periods[period];
    const ps = p.power_stats;
    const { hourly, rows, labels } = periodSeries();
    const grade = $("power-grade");
    grade.textContent = ps ? ps.grade : "";
    grade.className = "st2-grade" + (ps ? ` grade-${{ stabilnie: "dobra", umiarkowanie: "przeciętna", nerwowo: "słaba" }[ps.grade]}` : "");
    $("power-tiles").innerHTML = [
      tile("Średnia moc", num(p.power_kw_avg, 1), "kW"),
      tile("Szczyt", num(p.power_kw_max, 1), "kW"),
      tile("Moc sterownika", ps ? `${num(ps.avg_pct, 0)}<small>%</small>` : "--", ps ? `wahania ±${num(ps.std_pct, 0)}%` : "",
           ps ? (ps.std_pct < 12 ? "st2-tile--good" : ps.std_pct >= 22 ? "st2-tile--bad" : "") : ""),
      tile("Na pełnej mocy", ps ? `${num(ps.at_max_pct, 0)}<small>%</small>` : "--", ps ? `czasu (≥${num(ps.high_pct, 0)}%)` : "",
           ps && ps.at_max_pct >= 40 ? "st2-tile--bad" : ""),
      tile("Na minimum", ps ? `${num(ps.at_min_pct, 0)}<small>%</small>` : "--", ps ? `czasu (≤${num(ps.low_pct, 0)}%)` : "",
           ps && ps.at_min_pct >= 40 ? "st2-tile--bad" : ""),
    ].join("");

    // kWh: per hour for the last day, per day for longer periods
    const span = period === "today" ? " • ostatnie 24 h" : "";
    $("power-kwh-title").textContent = (hourly ? "Ciepło w każdej godzinie [kWh] = średnia moc [kW]" : "Ciepło każdego dnia [kWh]") + span;
    const kwhOpts = baseOptions();
    kwhOpts.scales.y.ticks.callback = (v) => `${num(v, 0)} kWh`;
    powerKwhChart = draw(powerKwhChart, "chart-power-kwh", {
      labels,
      datasets: [{ type: "bar", label: "kWh", data: rows.map((r) => r.kwh), backgroundColor: "rgba(250, 204, 21, 0.75)", borderRadius: 4 }],
    }, kwhOpts);

    // %: 10 min averages for the last day, daily average in WORK for longer periods
    const fine = period === "today" ? p.fine24 : period === "yesterday" ? p.fine : null;
    const useFine = fine && fine.length && fine.some((f) => f.power != null);
    const pctLabels = useFine ? fine.map((f) => f.time) : labels;
    const pct = useFine ? fine.map((f) => f.power) : rows.map((r) => (hourly ? r.power : r.power_work));
    $("power-pct-title").textContent = `Moc sterownika [%]${useFine ? " co 5 min" : hourly ? " w każdej godzinie" : " - średnia w pracy każdego dnia"}${span}`;
    const pctOpts = baseOptions();
    pctOpts.scales.y.max = 100;
    pctOpts.scales.y.ticks.callback = (v) => `${v}%`;
    if (useFine) {
      const every = window.innerWidth < 700 ? 6 : 3;
      pctOpts.scales.x.ticks = { ...pctOpts.scales.x.ticks, autoSkip: false,
        callback: (v, i) => (pctLabels[i].endsWith(":00") && Number(pctLabels[i].slice(0, 2)) % every === 0 ? pctLabels[i].replace(/^0/, "") : "") };
    }
    if (useFine) stopOptions(pctOpts, fine);
    powerPctChart = draw(powerPctChart, "chart-power-pct", {
      labels: pctLabels,
      datasets: [{ type: "line", label: "moc %", data: pct, borderColor: "#60a5fa", backgroundColor: "rgba(96, 165, 250, 0.15)",
        fill: true, spanGaps: false, borderWidth: 2 }],
    }, pctOpts);
  }

  // hour-of-day profile (when the boiler doesn't keep up) and the list of drops
  function renderHourly() {
    const p = report.periods[period];
    let { hourly, rows: hours, labels } = periodSeries();
    // the last day: 10 min averages (a half-hour dip disappears in an hour's average)
    const fine = ((period === "today" ? p.fine24 : period === "yesterday" ? p.fine : null) || []).length
      ? (period === "today" ? p.fine24 : p.fine) : null;
    if (fine) {
      hours = fine;
      labels = fine.map((f) => f.time);
    }
    const last24 = hourly;
    const drops = p.drops || { count: 0, items: [], by_hour: [] };
    const band = (d) => hours.map((h) => (h.setpoint == null || h.boiler == null ? null : h.setpoint + d));
    const data = {
      labels,
      datasets: [
        { type: "line", label: "zadana -2°C", data: band(-2), borderWidth: 0, pointHoverRadius: 0, spanGaps: !fine },
        { type: "line", label: "zadana +2°C", data: band(2), borderWidth: 0, pointHoverRadius: 0, spanGaps: !fine,
          fill: "-1", backgroundColor: "rgba(74, 222, 128, 0.22)" },
        { type: "line", label: "Kocioł [°C]", data: hours.map((h) => h.boiler), borderColor: "#f97316", backgroundColor: "#f97316", spanGaps: false },
      ],
    };
    // hour by hour: also the lowest reading of each hour - a short dip (a bath) hides in the hour's average
    const lows = hours.map((h) => (h.boiler != null && h.boiler_min != null ? h.boiler_min : null));
    if (hourly && !fine && lows.some((v) => v != null)) {
      data.datasets.push({ type: "line", label: "najniżej w godzinie", data: lows, borderColor: "rgba(248, 113, 113, 0.9)",
        backgroundColor: "rgba(248, 113, 113, 0.9)", borderWidth: 2, borderDash: [5, 4], spanGaps: false });
    }
    const options = baseOptions();
    options.scales.y.beginAtZero = false;
    options.scales.y.ticks.callback = (v) => `${v}°C`;
    // some room around the band and the line, so the band doesn't fill the chart
    const vals = hours.flatMap((h, i) => [h.boiler, hourly ? lows[i] : null, h.setpoint != null ? h.setpoint - 2 : null, h.setpoint != null ? h.setpoint + 2 : null]).filter((v) => v != null);
    if (vals.length) {
      options.scales.y.suggestedMin = Math.floor(Math.min(...vals) - 2);
      options.scales.y.suggestedMax = Math.ceil(Math.max(...vals) + 2);
    }
    options.plugins.tooltip = { filter: (item) => item.datasetIndex >= 2, callbacks: { label: (ctx) => {
      const h = hours[ctx.dataIndex] || {};
      return ctx.datasetIndex === 3 ? `najniżej ${num(ctx.parsed.y, 1)}°C`
        : `kocioł ${num(ctx.parsed.y, 1)}°C (zadana ${num(h.setpoint, 0)}°C)`;
    } } };
    $("hourly-title").textContent = `Kocioł na tle zadanej ±2°C (zielony pas)${hourly && !fine ? ", przerywana = najniżej w godzinie" : hourly ? ", co 5 min" : ""} • ${period === "today" ? "ostatnie 24 h" : PERIOD_NAMES[period]}`;
    if (fine) {
      // 288 points: a label every 3 hours (6 on a phone)
      const every = window.innerWidth < 700 ? 6 : 3;
      options.scales.x.ticks = { ...options.scales.x.ticks, autoSkip: false,
        callback: (v, i) => (labels[i].endsWith(":00") && Number(labels[i].slice(0, 2)) % every === 0 ? labels[i].replace(/^0/, "") : "") };
    }
    if (fine) stopOptions(options, fine);
    hourlyChart = draw(hourlyChart, "chart-hourly", data, options);

    // worst hour of day over the chosen period (average per hour of day)
    const worst = (p.hourly || []).filter((h) => h.below_pct != null).sort((a, b) => b.below_pct - a.below_pct)[0];
    $("hourly-note").textContent = worst && worst.below_pct > 0
      ? `Najsłabiej o ${worst.hour}:00–${worst.hour + 1}:00`
      : "Bez wyraźnych spadków";
    renderStops(fine ? (period === "today" ? p.stops24 : p.stops) : null);

    $("drops-head").innerHTML = (drops.count ? `Spadki temperatury: ${drops.count}` : "Spadki temperatury") +
      ` <span class="st2-sub">${periodText(period)}</span>`;
    if (!drops.items.length) {
      $("drops").innerHTML = `<div class="st2-empty">${FBIcon("check", "ic--inline ic--ok")} Brak spadków ponad ${num(drops.below_c ?? 4, 0)}°C pod zadaną</div>`;
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

  function renderDaily() {
    const { hourly, rows, labels } = periodSeries();
    const data = {
      labels,
      datasets: [{ type: "bar", label: "Węgiel", data: rows.map((d) => d.coal_kg), backgroundColor: "rgba(249, 115, 22, 0.8)", borderRadius: 4 }],
    };
    const options = baseOptions();
    coalAxisTicks(options.scales.y, options);
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
        `${FBIcon("hourglass", "ic--inline")} Zbieram dane: <b>${w.days_with_data} z ${w.days_needed}</b> pełnych dni z pogodą`;
    } else {
      $("weather-text").innerHTML = `<div class="st2-hero">${[
        tile("Przy 0°C", num(fit.intercept, 0), "kg"),
        tile("Przy -10°C", num(fit.intercept - 10 * fit.slope, 0), "kg"),
        tile("Każdy stopień mrozu", `+${num(-fit.slope, 1)}`, "kg"),
      ].join("")}</div><div class="st2-note">Węgla na dobę • z ${fit.n} dni</div>`;
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
    $("season-period").textContent = `od ${report.season_start.slice(8, 10)}.${report.season_start.slice(5, 7)}.${report.season_start.slice(0, 4)}`;
    const f = s.forecast || {};
    // tonnes from 1 t up, kilograms below
    const mass = (kg) => (kg == null ? ["--", "t"] : kg >= 1000 ? [num(kg / 1000, 2), "t"] : [num(kg, 0), "kg"]);
    const [usedV, usedU] = mass(s.this.coal_kg);
    const [fcV, fcU] = mass(f.forecast_kg);
    const [prevV, prevU] = mass(s.previous.coal_kg);
    const vsPrev = s.previous.coal_kg_to_date != null && s.previous.coal_kg_to_date > 0
      ? `${num(100 * (s.this.coal_kg - s.previous.coal_kg_to_date) / s.previous.coal_kg_to_date, 0)}% vs rok temu`
      : "";
    const zl = (kg) => (price() && kg != null ? ` • ${num(pln(kg), 0)} zł` : "");
    $("season-tiles").innerHTML = [
      tile("Ten sezon do dziś", `${usedV}<small>${usedU}</small>`, [zl(s.this.coal_kg).slice(3), vsPrev].filter(Boolean).join(" • ")),
      tile("Prognoza na cały sezon", f.forecast_kg != null ? `${fcV}<small>${fcU}</small>` : "--",
           f.forecast_kg != null ? zl(f.forecast_kg).slice(3) : "za mało dni", "st2-tile--cost"),
      tile("Zeszły sezon", `${prevV}<small>${prevU}</small>`, zl(s.previous.coal_kg).slice(3)),
    ].join("");
    $("season-note").textContent = f.forecast_kg != null ? "Prognoza: do końca maja, przy typowej pogodzie" : "";

    const months = s.this.months.map((m) => MONTHS[m.month - 1]);
    const data = {
      labels: months,
      datasets: [
        { type: "bar", label: `Sezon ${s.previous.start.slice(0, 4)}/${String(Number(s.previous.start.slice(0, 4)) + 1).slice(2)} [kg]`, data: s.previous.months.map((m) => m.coal_kg), backgroundColor: "rgba(156, 163, 175, 0.6)", coal: true },
        { type: "bar", label: `Sezon ${s.this.start.slice(0, 4)}/${String(Number(s.this.start.slice(0, 4)) + 1).slice(2)} [kg]`, data: s.this.months.map((m) => m.coal_kg), backgroundColor: "rgba(249, 115, 22, 0.8)", coal: true },
      ],
    };
    const options = baseOptions(true);
    coalAxisTicks(options.scales.y, options);
    options.plugins.tooltip = coalTooltip;
    seasonChart = draw(seasonChart, "chart-seasons", data, options);
  }

  function render() {
    markRanges();
    renderUsage();
    renderSeasons();
    renderWork();
    renderEfficiency();
    renderWeather();
  }

  // ---------- forecast: weather for the next days and the coal it should take ----------

  // WMO weather codes (Open-Meteo)
  function weatherLook(code) {
    const c = Number(code);
    // [icon, colour class, text]
    const w = (icon, tone, text) => [FBIcon(icon, `wx wx--${tone}`), text];
    if (c === 0) return w("sun", "sun", "bezchmurnie");
    if (c === 1) return w("cloudSun", "sun", "przeważnie pogodnie");
    if (c === 2) return w("cloudSun", "cloud", "częściowe zachmurzenie");
    if (c === 3) return w("cloud", "cloud", "pochmurno");
    if (c === 45 || c === 48) return w("cloudFog", "cloud", "mgła");
    if (c >= 51 && c <= 55) return w("cloudDrizzle", "rain", "mżawka");
    if (c === 56 || c === 57 || c === 66 || c === 67) return w("cloudRain", "snow", "marznący deszcz");
    if (c >= 61 && c <= 65) return w("cloudRain", "rain", c === 65 ? "ulewa" : "deszcz");
    if ((c >= 71 && c <= 77) || c === 85 || c === 86) return w("cloudSnow", "snow", c === 75 || c === 86 ? "śnieżyca" : "śnieg");
    if (c >= 80 && c <= 82) return w("cloudRain", "rain", "przelotny deszcz");
    if (c >= 95) return w("cloudBolt", "storm", c > 95 ? "burza z gradem" : "burza");
    return w("thermometer", "cloud", "");
  }
  const DAY_NAMES = ["niedziela", "poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota"];
  function dayName(iso, i) {
    const d = new Date(iso + "T12:00:00");
    const label = i === 0 ? "Dziś" : i === 1 ? "Jutro" : i === 2 ? "Pojutrze" : DAY_NAMES[d.getDay()];
    return { label, sub: `${DAY_NAMES[d.getDay()]} ${iso.slice(8, 10)}.${iso.slice(5, 7)}` };
  }
  const temp = (v) => (v == null ? "--" : `${num(Math.round(v) + 0, 0)}°`);  // + 0: no "-0°"

  function renderForecast() {
    if (!forecast) return;
    const f = forecast;
    const zl = (v) => (v == null ? "--" : num(v, v < 10 ? 2 : 0));
    if (!f.available) {
      $("fc-tomorrow").innerHTML = `<div class="st2-empty">${FBIcon("globe", "ic--inline")} Brak prognozy - sprawdź internet (pobierana co 3 h)</div>`;
      $("fc-days").innerHTML = "";
      $("fc-note").textContent = "";
      return;
    }
    const days = f.days || [];
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    const idx = (d) => Math.round((new Date(d + "T12:00:00") - new Date(today + "T12:00:00")) / 86400000);
    const tomorrow = days.find((d) => idx(d.date) === 1) || days[0];
    if (tomorrow) {
      const [icon, text] = weatherLook(tomorrow.code);
      $("fc-title").textContent = `Jutro • ${dayName(tomorrow.date, 1).sub}`;
      $("fc-tomorrow").innerHTML = `
        <div class="fc-sky"><span class="fc-icon">${icon}</span>
          <div><div class="fc-temps"><b>${temp(tomorrow.tmax)}</b> <span>${temp(tomorrow.tmin)}</span></div>
          <div class="fc-sky-text">${text}${tomorrow.wind_max != null ? ` • wiatr do ${num(tomorrow.wind_max, 0)} km/h` : ""}</div></div></div>
        <div class="st2-hero fc-hero">
          ${tile("Węgiel", tomorrow.coal_kg == null ? "--" : `${num(tomorrow.coal_kg, 0)}<small>kg</small>`, "")}
          ${tile("Koszt", tomorrow.cost_pln == null ? "--" : `${zl(tomorrow.cost_pln)}<small>zł</small>`, "", "st2-tile--cost")}
          ${tile("Średnio", tomorrow.power_kw == null ? "--" : `${num(tomorrow.power_kw, 1)}<small>kW</small>`, tomorrow.energy_kwh == null ? "" : `${num(tomorrow.energy_kwh, 0)} kWh ciepła`)}
        </div>`;
    }
    $("fc-days").innerHTML = days.map((d) => {
      const [icon, text] = weatherLook(d.code);
      const n = dayName(d.date, idx(d.date));
      return `<div class="fc-day ${idx(d.date) === 1 ? "is-tomorrow" : ""}">
        <div class="fc-day__name">${n.label}<span>${d.partial ? `reszta dnia (${d.hours} h)` : n.sub}</span></div>
        <div class="fc-day__icon" title="${text}">${icon}</div>
        <div class="fc-day__temps"><b>${temp(d.tmax)}</b> / ${temp(d.tmin)}</div>
        <div class="fc-day__coal">${d.coal_kg == null ? "--" : `${num(d.coal_kg, 0)} kg`}</div>
        <div class="fc-day__cost">${d.cost_pln == null ? "" : `${zl(d.cost_pln)} zł`}</div>
        <div class="fc-day__more">${d.rain_pct ? `opady ${d.rain_pct}%` : text}</div>
      </div>`;
    }).join("");

    const hours = f.hours || [];
    const labels = hours.map((h) => (h.time.slice(11, 13) === "00" ? `${h.time.slice(8, 10)}.${h.time.slice(5, 7)}` : `${Number(h.time.slice(11, 13))}:00`));
    const options = baseOptions(true);
    options.scales.y = { position: "right", beginAtZero: true, grid: { display: false },
      ticks: { color: "#fdba74", font: { size: 15 }, callback: (v) => `${num(v, 1)} kg` } };
    options.scales.yt = { position: "left", grid: { color: "rgba(255,255,255,0.08)" },
      ticks: { color: "#93c5fd", font: { size: 15 }, callback: (v) => `${num(v, 0)}°C` } };
    options.plugins.tooltip = { callbacks: { label: (ctx) => ctx.dataset.yAxisID === "yt"
      ? `Temperatura: ${num(ctx.parsed.y, 1)}°C` : `Węgiel: ${num(ctx.parsed.y, 2)} kg/h` } };
    forecastChart = draw(forecastChart, "chart-forecast", {
      labels,
      datasets: [
        { type: "line", label: "Temperatura [°C]", yAxisID: "yt", data: hours.map((h) => h.temp), borderColor: "#60a5fa", backgroundColor: "#60a5fa" },
        { type: "bar", label: "Węgiel [kg/h]", yAxisID: "y", data: hours.map((h) => h.kg), backgroundColor: "rgba(249, 115, 22, 0.55)", borderRadius: 3 },
      ],
    }, options);

    const m = f.model || {};
    const model = m.method ? `węgiel z ${m.days} dni pracy pieca` : "za mało dni pracy pieca, żeby liczyć węgiel";
    $("fc-note").innerHTML = `${FBIcon("pin", "ic--inline")} ${f.location || "--"} • ${model}`;
  }

  async function loadForecast() {
    try {
      const res = await fetch("/api/analytics/forecast", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      forecast = await res.json();
      renderForecast();
    } catch (err) {
      $("fc-tomorrow").innerHTML = `<div class="st2-empty">Nie udało się wczytać prognozy: ${err.message}</div>`;
    }
  }

  // ---------- sub-pages ----------

  function showTab(tab) {
    if (!document.querySelector(`#st2-tabs button[data-tab="${tab}"]`)) tab = "usage";
    document.querySelectorAll("#st2-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".st2-card[data-tab]").forEach((c) => c.classList.toggle("is-hidden", c.dataset.tab !== tab));
    // charts drawn while hidden have no size - redraw the visible ones
    [dailyChart, seasonChart, hourlyChart, effChart, weatherChart, forecastChart, powerKwhChart, powerPctChart].forEach((c) => c && c.resize());
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
    $("st2-refresh").addEventListener("click", () => { load(true); loadForecast(); });
    $("st2-tabs").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-tab]");
      if (btn) showTab(btn.dataset.tab);
    });
    let saved = null;
    try { saved = localStorage.getItem("stats-tab"); } catch (err) { /* private mode */ }
    showTab(saved || "usage");
    load();
    // the model behind the forecast comes from the report - asked again once it is fresh
    loadForecast();
    setTimeout(loadForecast, 20000);
  });
})();
