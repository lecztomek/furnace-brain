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
  let weatherChart = null;

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
    const kgph = report.settings?.feeder_kg_per_hour;
    $("coal-note").textContent =
      `Okres: ${PERIOD_NAMES[period]}, dane z ${num(p.hours, 1)} h. ` +
      (kgph ? `Kilogramy liczone z czasu pracy ślimaka × ${kgph} kg/h (ustawienie modułu Statystyki - warto skalibrować).` : "");

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
    renderChecks();
    renderDaily();
    renderWeather();
  }

  // ---------- data ----------

  async function load() {
    $("st2-status").textContent = "Wczytywanie…";
    try {
      const res = await fetch(REPORT_URL, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      report = await res.json();
      render();
      $("st2-status").textContent = `Stan na ${new Date().toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })} • sezon od ${report.season_start}`;
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
    });
    $("st2-refresh").addEventListener("click", load);
    load();
  });
})();
