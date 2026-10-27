// Statistics page: one report from /api/analytics/report, period switch on the client.
(function () {
  const REPORT_URL = "/api/analytics/report";
  const PERIOD_NAMES = { today: "dziś", yesterday: "wczoraj", d7: "ostatnie 7 dni", d30: "ostatnie 30 dni", season: "sezon" };
  const MODE_NAMES = { WORK: "praca", IGNITION: "rozpalanie", OFF: "stop", MANUAL: "ręcznie" };
  const STATUS_ICONS = { ok: "✓", warn: "⚠", bad: "✕", info: "ℹ" };

  const $ = (id) => document.getElementById(id);
  let report = null;
  let period = "d7";
  let dailyChart = null;
  let effChart = null;
  let weatherChart = null;
  let hourlyChart = null;

  const num = (v, digits = 1) =>
    v === null || v === undefined ? "--" : Number(v).toLocaleString("pl-PL", { maximumFractionDigits: digits, minimumFractionDigits: digits });

  function tile(label, value, unit) {
    return `<div class="st2-tile"><span class="st2-label">${label}</span><span class="st2-value">${value}</span><span class="st2-unit">${unit || ""}</span></div>`;
  }

  // ---------- render ----------

  function renderPeriod() {
    const p = report.periods[period];
    document.querySelectorAll("#st2-periods button").forEach((b) => b.classList.toggle("active", b.dataset.period === period));

    $("coal-total").textContent = num(p.coal_kg, 1);
    $("coal-day").textContent = num(p.coal_kg_per_day, 1);
    $("coal-hour").textContent = num(p.coal_kg_per_hour, 2);
    $("energy-total").textContent = num(p.energy_kwh, 0);
    $("energy-day").textContent = num(p.energy_kwh_per_day, 0);
    $("kw-avg").textContent = num(p.power_kw_avg, 1);
    $("kw-max").textContent = num(p.power_kw_max, 1);
    const kgph = report.settings?.feeder_kg_per_hour;
    const mj = report.settings?.calorific_mj_per_kg;
    $("coal-note").textContent =
      `Okres: ${PERIOD_NAMES[period]}, dane z ${num(p.hours, 1)} h. ` +
      (kgph ? `Kilogramy liczone z czasu pracy ślimaka × ${kgph} kg/h (ustawienie modułu Statystyki - warto skalibrować). ` : "") +
      (mj ? `Energia = węgiel × ${mj} MJ/kg (energia w opale, bez sprawności kotła).` : "");

    const a = p.avg || {};
    const modes = Object.entries(p.mode_share || {})
      .filter(([m]) => MODE_NAMES[m])
      .map(([m, v]) => `${MODE_NAMES[m]} ${num(v, 0)}%`)
      .join(", ");
    $("work-tiles").innerHTML = [
      tile("Średnia moc", num(a.power, 0), "%"),
      tile("Kocioł", num(a.boiler), "°C"),
      tile("Grzejniki", num(a.radiators), "°C"),
      tile("Spaliny", num(a.flue, 0), "°C"),
      tile("Na zewnątrz", num(a.outside), "°C"),
      tile("Dmuchawa / ślimak", `${num(a.fan, 0)} / ${num(a.feeder, 0)}`, "% mocy / % czasu podawania"),
      tile("Rozpalenia", p.ignitions ?? "--", p.ignitions_per_day != null ? `${num(p.ignitions_per_day, 1)} na dobę` : ""),
      tile("Czas w trybach", "", modes || "--"),
    ].join("");

    const r = p.regulation;
    $("reg-grade").textContent = r ? r.grade : "";
    $("reg-grade").className = "st2-grade" + (r ? ` grade-${r.grade.replace(/\s/g, "-")}` : "");
    $("reg-tiles").innerHTML = r
      ? [
          tile("W ±1°C od zadanej", num(r.within_1, 0), "% czasu"),
          tile("W ±2°C od zadanej", num(r.within_2, 0), "% czasu"),
          tile("Średnio od zadanej", num(r.mean_abs_error, 1), "°C"),
          tile("Rozrzut (σ)", num(r.std, 1), "°C"),
          tile("Najwyżej ponad", num(r.max_over, 1), "°C"),
          tile("Najniżej pod", num(r.max_under, 1), "°C"),
        ].join("")
      : `<div class="st2-empty">Za mało pracy w trybie PRACA w tym okresie (potrzeba min. 30 min).</div>`;
  }

  function renderEfficiency() {
    const e = report.periods[period].efficiency || {};
    const t = report.efficiency_trend || {};
    const a = report.efficiency_assumptions || {};
    const signed = (v, d = 0) => (v == null ? "--" : `${v > 0 ? "+" : ""}${num(v, d)}`);
    $("eff-tiles").innerHTML = [
      tile("Węgiel na stopniodzień", num(e.kg_per_degree_day, 2), `kg (${PERIOD_NAMES[period]})`),
      tile("Stopniodni", num(e.degree_days, 0), `suma (${PERIOD_NAMES[period]})`),
      tile("Zmiana tygodniowa", signed(t.change_pct), "% vs 3 tygodnie wcześniej"),
      tile("Straty kominowe (szac.)", num(e.flue_loss_pct, 1), "% energii"),
      tile("Straty: tydzień / wcześniej", `${num(t.recent_flue_loss_pct, 1)} / ${num(t.before_flue_loss_pct, 1)}`, "%"),
    ].join("");
    $("eff-note").textContent =
      `Stopniodzień = o ile średnia doby była niższa niż ${num(a.degree_day_base_c, 0)}°C (dzień 3°C = 15 stopniodni). ` +
      `Mniej kg na stopniodzień = sprawniej; wzrost przy podobnej pogodzie zwykle oznacza zabrudzony kocioł. ` +
      `Straty kominowe ze wzoru Siegerta z temperatury spalin w pracy, przy założeniu ${num(a.co2_pct, 0)}% CO₂ ` +
      `i ${num(a.room_c, 0)}°C w kotłowni - orientacyjnie, ważny jest trend.`;

    const days = report.daily || [];
    const data = {
      labels: days.map((d) => d.date.slice(8, 10) + "." + d.date.slice(5, 7)),
      datasets: [
        { type: "line", label: "Węgiel na stopniodzień [kg]", data: days.map((d) => d.kg_per_degree_day), borderColor: "#f97316", backgroundColor: "#f97316", yAxisID: "y", tension: 0.3, pointRadius: 2, spanGaps: true },
        { type: "line", label: "Straty kominowe [%]", data: days.map((d) => d.flue_loss_pct), borderColor: "#38bdf8", backgroundColor: "#38bdf8", yAxisID: "y2", tension: 0.3, pointRadius: 2, borderDash: [5, 4], spanGaps: true },
      ],
    };
    const options = {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { labels: { color: "#e5e7eb" } } },
      scales: {
        x: { ticks: { color: "#9ca3af", maxRotation: 0, autoSkip: true }, grid: { color: "rgba(255,255,255,0.05)" } },
        y: { position: "left", beginAtZero: true, ticks: { color: "#fdba74" }, grid: { color: "rgba(255,255,255,0.08)" } },
        y2: { position: "right", beginAtZero: true, ticks: { color: "#7dd3fc" }, grid: { display: false } },
      },
    };
    if (effChart) { effChart.data = data; effChart.update(); }
    else effChart = new Chart($("chart-efficiency"), { data, options });
  }

  // hour-of-day profile (when the boiler doesn't keep up) and the list of drops
  function renderHourly() {
    const p = report.periods[period];
    const hours = p.hourly || [];
    const drops = p.drops || { count: 0, items: [], by_hour: [] };
    const data = {
      labels: hours.map((h) => `${h.hour}`),
      datasets: [
        { type: "line", label: "Zadana [°C]", data: hours.map((h) => h.setpoint), borderColor: "#9ca3af", borderDash: [6, 4], pointRadius: 0, yAxisID: "y", spanGaps: true },
        { type: "line", label: "Kocioł średnio [°C]", data: hours.map((h) => h.boiler), borderColor: "#f97316", backgroundColor: "#f97316", pointRadius: 2, tension: 0.3, yAxisID: "y", spanGaps: true },
        { type: "bar", label: `Czas poniżej zadanej −${num(drops.below_c ?? 4, 0)}°C [%]`, data: hours.map((h) => h.below_pct), backgroundColor: "rgba(248, 113, 113, 0.7)", yAxisID: "y2" },
        { type: "line", label: "Moc średnio [%]", data: hours.map((h) => h.power), borderColor: "#38bdf8", backgroundColor: "#38bdf8", pointRadius: 0, tension: 0.3, yAxisID: "y2", spanGaps: true },
      ],
    };
    const options = {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { labels: { color: "#e5e7eb", boxWidth: 14 } } },
      scales: {
        x: { title: { display: true, text: "Godzina", color: "#9ca3af" }, ticks: { color: "#9ca3af" }, grid: { color: "rgba(255,255,255,0.05)" } },
        y: { position: "left", ticks: { color: "#fdba74" }, grid: { color: "rgba(255,255,255,0.08)" } },
        y2: { position: "right", beginAtZero: true, suggestedMax: 100, ticks: { color: "#7dd3fc" }, grid: { display: false } },
      },
    };
    if (hourlyChart) { hourlyChart.data = data; hourlyChart.update(); }
    else hourlyChart = new Chart($("chart-hourly"), { data, options });

    const worst = hours.filter((h) => h.below_pct != null).sort((a, b) => b.below_pct - a.below_pct)[0];
    $("hourly-note").textContent =
      `Średnio dla każdej godziny doby (${PERIOD_NAMES[period]}), tylko PRACA. ` +
      (worst && worst.below_pct > 0
        ? `Najgorzej o ${worst.hour}:00–${worst.hour + 1}:00: ${num(worst.below_pct, 0)}% czasu ponad ${num(drops.below_c ?? 4, 0)}°C poniżej zadanej, średnio ${num(worst.error, 1)}°C od zadanej.`
        : "Bez wyraźnych spadków.");

    $("drops-head").textContent = `Spadki temperatury (${drops.count})`;
    if (!drops.items.length) {
      $("drops").innerHTML = `<div class="st2-empty">Brak spadków ponad ${num(drops.below_c ?? 4, 0)}°C poniżej zadanej w tym okresie.</div>`;
      return;
    }
    const rows = drops.items.slice(0, 15).map((d) => {
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
    const days = report.daily || [];
    const labels = days.map((d) => d.date.slice(8, 10) + "." + d.date.slice(5, 7));
    const data = {
      labels,
      datasets: [
        { type: "bar", label: "Węgiel [kg/dobę]", data: days.map((d) => d.coal_kg), backgroundColor: "rgba(249, 115, 22, 0.75)", yAxisID: "y" },
        { type: "line", label: "Na zewnątrz [°C]", data: days.map((d) => d.outside), borderColor: "#a855f7", backgroundColor: "#a855f7", yAxisID: "y2", tension: 0.3, pointRadius: 2 },
      ],
    };
    const options = {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { labels: { color: "#e5e7eb" } } },
      scales: {
        x: { ticks: { color: "#9ca3af", maxRotation: 0, autoSkip: true }, grid: { color: "rgba(255,255,255,0.05)" } },
        y: { position: "left", beginAtZero: true, ticks: { color: "#fdba74" }, grid: { color: "rgba(255,255,255,0.08)" } },
        y2: { position: "right", ticks: { color: "#c4b5fd" }, grid: { display: false } },
      },
    };
    if (dailyChart) { dailyChart.data = data; dailyChart.update(); }
    else dailyChart = new Chart($("chart-daily"), { data, options });
  }

  function renderWeather() {
    const w = report.weather;
    const fit = w.coal.fit;
    const pfit = w.power.fit;
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
    const datasets = [{ type: "scatter", label: "Dni", data: pts, backgroundColor: "rgba(249, 115, 22, 0.85)", pointRadius: 4 }];
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
    if (weatherChart) { weatherChart.data = { datasets }; weatherChart.update(); }
    else weatherChart = new Chart($("chart-weather"), { data: { datasets }, options });
  }

  function render() {
    renderPeriod();
    renderHourly();
    renderEfficiency();
    renderChecks();
    renderDaily();
    renderWeather();
  }

  // ---------- data ----------

  async function load(fresh = false) {
    $("st2-status").textContent = "Wczytywanie…";
    try {
      const res = await fetch(fresh ? `${REPORT_URL}?fresh=1` : REPORT_URL, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      report = await res.json();
      render();
      const at = report.generated_at ? new Date(report.generated_at * 1000) : new Date();
      const hhmm = at.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
      // after a restart: the last saved report first, the fresh one comes a moment later
      $("st2-status").textContent = report.stale
        ? `Stan na ${hhmm} • liczę aktualne…`
        : `Stan na ${hhmm} • sezon od ${report.season_start}`;
      clearTimeout(load.retry);
      if (report.stale) load.retry = setTimeout(() => load(), 10000);
    } catch (err) {
      console.error(err);
      $("st2-status").textContent = `Nie udało się wczytać statystyk: ${err.message}`;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("st2-periods").addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-period]");
      if (!btn || !report) return;
      period = btn.dataset.period;
      renderPeriod();
      renderHourly();
      renderEfficiency();
    });
    $("st2-refresh").addEventListener("click", () => load(true));
    load();
  });
})();
