// js/history.js

(function () {
  const HISTORY_API_BASE = "/api/history";

  const DEFAULT_RANGE_HOURS = 6;

  // Mapowanie klucz -> ładna etykieta
  const FIELD_LABELS = {
    temp_pieca: "Temp. pieca",
    power: "Moc [%]",
    temp_grzejnikow: "Temp. grzejników",
    temp_spalin: "Temp. spalin",
    tryb_pracy: "Tryb pracy",
  };

  // Kolory serii – dla czytelności
  const FIELD_COLORS = {
    temp_pieca: "#f97316",      // pomarańcz
    power: "#22c55e",           // zielony
    temp_grzejnikow: "#3b82f6", // niebieski
    temp_spalin: "#ef4444",     // czerwony
    tryb_pracy: "#eab308",      // żółty
  };

  // Opisy wartości dla osi trybu
  const MODE_TICKS = {
    0: "OFF",
    1: "IGN",
    2: "WORK",
    3: "MAN",
  };

  let historyChart = null;
  let availableFields = [];
  let currentFrom = null;  // Date
  let currentTo = null;    // Date

  function setStatus(text) {
    const el = document.getElementById("status-text");
    if (el) el.textContent = text || "";
  }

  // ISO 8601 bez ms i bez strefy – lokalny czas (zgodny z backendem)
  function isoNoMs(date) {
    const pad = (n) => String(n).padStart(2, "0");

    const year = date.getFullYear();
    const month = pad(date.getMonth() + 1);
    const day = pad(date.getDate());
    const hour = pad(date.getHours());
    const minute = pad(date.getMinutes());
    const second = pad(date.getSeconds());

    return `${year}-${month}-${day}T${hour}:${minute}:${second}`;
  }

	function formatDateTimeLabel(date) {
	  const pad = (n) => String(n).padStart(2, "0");
	  const d = pad(date.getDate());
	  const m = pad(date.getMonth() + 1);
	  const h = pad(date.getHours());
	  const min = pad(date.getMinutes());
	  // bez roku – i tak się nie przewijamy na lata wstecz
	  return `${d}.${m} ${h}:${min}`;
	}


  function updateWindowLabel() {
    const labelEl = document.getElementById("history-window-label");
    if (!labelEl) return;

    if (!currentFrom || !currentTo) {
      labelEl.textContent = "brak zakresu";
      return;
    }

    const fromStr = formatDateTimeLabel(currentFrom);
    const toStr = formatDateTimeLabel(currentTo);
    labelEl.textContent = `${fromStr} – ${toStr}`;
  }

  function setInitialWindow(rangeHours) {
    const now = new Date();
    currentTo = now;
    currentFrom = new Date(now.getTime() - rangeHours * 3600 * 1000);
    updateWindowLabel();
  }

  function shiftWindow(direction) {
    const hours = getCurrentRangeHours();
    if (!currentFrom || !currentTo) {
      setInitialWindow(hours);
      return;
    }
    const deltaMs = direction * hours * 3600 * 1000;
    let newFrom = new Date(currentFrom.getTime() + deltaMs);
    let newTo = new Date(currentTo.getTime() + deltaMs);
    const now = new Date();

    // nie wychodzimy w przyszłość
    if (newTo > now) {
      const diff = newTo.getTime() - now.getTime();
      newTo = now;
      newFrom = new Date(newFrom.getTime() - diff);
    }

    currentFrom = newFrom;
    currentTo = newTo;
    updateWindowLabel();
  }

  async function fetchFields() {
    const res = await fetch(`${HISTORY_API_BASE}/fields`);
    if (!res.ok) {
      throw new Error(`Błąd pobierania pól: ${res.status}`);
    }
    const data = await res.json();
    return Array.isArray(data.fields) ? data.fields : [];
  }

  function buildSeriesCheckboxes(fields) {
    const container = document.getElementById("history-series-container");
    if (!container) return;

    container.innerHTML = "";
    const withoutTimestamp = fields.filter((f) => f !== "data_czas");

    // Jeśli backend zwrócił tylko data_czas – załóż standardowy zestaw
    const finalFields =
      withoutTimestamp.length > 0
        ? withoutTimestamp
        : [
            "temp_pieca",
            "power",
            "temp_grzejnikow",
            "temp_spalin",
            "tryb_pracy",
          ];

    availableFields = finalFields;

    finalFields.forEach((field) => {
      const labelText = FIELD_LABELS[field] || field;

      const wrapper = document.createElement("label");
      wrapper.className = "history-series-toggle";

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = field;

      // domyślnie: wszystkie oprócz trybu są włączone
      checkbox.checked = field !== "tryb_pracy";

      const span = document.createElement("span");
      span.className = "history-series-label";
      span.textContent = labelText;

      wrapper.appendChild(checkbox);
      wrapper.appendChild(span);
      container.appendChild(wrapper);
    });
  }

  function getSelectedFields() {
    const container = document.getElementById("history-series-container");
    if (!container) return [];

    const checkboxes = container.querySelectorAll(
      'input[type="checkbox"]:checked'
    );
    return Array.from(checkboxes).map((cb) => cb.value);
  }

  function getCurrentRangeHours() {
    const activeBtn = document.querySelector(".history-range-btn.active");
    return Number(
      activeBtn?.getAttribute("data-range-hours") || DEFAULT_RANGE_HOURS
    );
  }

  async function fetchHistoryData(fromDate, toDate) {
    const from = isoNoMs(fromDate);
    const to = isoNoMs(toDate);

    const fields = getSelectedFields();
    const params = new URLSearchParams({
      from_ts: from,
      to_ts: to,
    });

    // Zawsze wysyłamy jakieś pola (poza data_czas)
    const effectiveFields =
      fields.length === 0 && availableFields.length > 0
        ? availableFields
        : fields;

    effectiveFields.forEach((f) => params.append("fields", f));

    const url = `${HISTORY_API_BASE}/data?${params.toString()}`;
    const res = await fetch(url);

    if (!res.ok) {
      throw new Error(`Błąd pobierania danych: ${res.status}`);
    }

    const data = await res.json();
    return data.items || [];
  }

  // mapowanie trybu pracy na liczbę
  function mapModeToNumber(raw) {
    if (raw === "" || raw === undefined || raw === null) return null;
    const s = String(raw).toUpperCase();

    // wspieramy zarówno "IGNITION", jak i "BoilerMode.IGNITION"
    if (s.includes("OFF")) return 0;
    if (s.includes("IGNITION")) return 1;
    if (s.includes("WORK")) return 2;
    if (s.includes("MANUAL")) return 3;

    return null;
  }

  function buildChartData(items) {
    if (!Array.isArray(items)) items = [];

    const labels = items.map((row) => {
      const ts = row.data_czas || "";
      const parts = ts.split("T");
      const timePart = parts[1] || parts[0] || "";
      return timePart.substring(0, 5); // HH:MM
    });

    const fields = getSelectedFields();
    const datasets = [];

    fields.forEach((field) => {
      const values = items.map((row) => {
        const raw = row[field];

        if (field === "tryb_pracy") {
          return mapModeToNumber(raw);
        }

        if (raw === "" || raw === undefined || raw === null) return null;

        const n = Number(raw);
        if (Number.isNaN(n)) return null;
        return n;
      });

      // jeśli cała seria to null -> nie dodajemy datasetu
      const hasAnyValue = values.some((v) => v !== null);
      if (!hasAnyValue) return;

      const isMode = field === "tryb_pracy";

      datasets.push({
        label: FIELD_LABELS[field] || field,
        data: values,
        borderColor: FIELD_COLORS[field] || undefined,
        backgroundColor: FIELD_COLORS[field] || undefined,
        borderWidth: 3,
        radius: 0,
        spanGaps: true,
        tension: 0.2,
        yAxisID: isMode ? "y_mode" : "y",
      });
    });

    return { labels, datasets };
  }

  function renderChart(items) {
    const ctx = document.getElementById("history-chart");
    if (!ctx) return;

    const { labels, datasets } = buildChartData(items);

    // GLOBALNE ustawienia Chart.js – większe fonty na tablet
    Chart.defaults.font.size = 14;
    Chart.defaults.color = "#e5e7eb";

    if (historyChart) {
      historyChart.data.labels = labels;
      historyChart.data.datasets = datasets;
      historyChart.update();
      return;
    }

    historyChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "nearest",
          intersect: false,
        },
        plugins: {
          legend: {
            display: false, // legendę zastępują checkboxy
          },
          tooltip: {
            bodyFont: { size: 14 },
            titleFont: { size: 14, weight: "bold" },
            callbacks: {
              title: (items) => {
                if (!items.length) return "";
                const idx = items[0].dataIndex;
                const ts = items[0].chart.data.labels[idx];
                return `Godzina: ${ts}`;
              },
            },
          },
        },
        scales: {
          x: {
            ticks: {
              maxRotation: 0,
              autoSkip: true,
              autoSkipPadding: 12,
              font: {
                size: 14,
              },
            },
            grid: {
              color: "rgba(55, 65, 81, 0.7)",
            },
          },
          // oś dla temperatur i mocy
          y: {
            position: "left",
            ticks: {
              font: {
                size: 14,
              },
            },
            grid: {
              color: "rgba(55, 65, 81, 0.7)",
            },
          },
          // oś dla trybu pracy (0..3)
          y_mode: {
            position: "right",
            min: -0.2,
            max: 3.2,
            ticks: {
              stepSize: 1,
              font: { size: 12 },
              callback: (value) => MODE_TICKS[value] || "",
            },
            grid: {
              drawOnChartArea: false, // nie rysujemy poziomych linii, tylko po lewej
            },
          },
        },
      },
    });
  }

  async function reloadHistoryWithCurrentWindow() {
    try {
      if (!currentFrom || !currentTo) {
        setInitialWindow(getCurrentRangeHours());
      }
      updateWindowLabel();
      setStatus("Ładowanie danych historii...");

      const items = await fetchHistoryData(currentFrom, currentTo);
      if (!items.length) {
        setStatus("Brak danych w wybranym zakresie.");
      } else {
        setStatus(
          `Załadowano ${items.length} punktów (${formatDateTimeLabel(
            currentFrom
          )} – ${formatDateTimeLabel(currentTo)}).`
        );
      }
      renderChart(items);
    } catch (err) {
      console.error(err);
      setStatus(
        err && err.message
          ? `Błąd historii: ${err.message}`
          : "Błąd odczytu historii."
      );
    }
  }

  function initRangeButtons() {
    const buttons = document.querySelectorAll(".history-range-btn");
    buttons.forEach((btn) => {
      btn.addEventListener("click", () => {
        buttons.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const hours = Number(btn.getAttribute("data-range-hours") || "6");

        // zmieniamy szerokość okna, prawy koniec zostaje gdzie był (albo teraz)
        const now = new Date();
        if (!currentTo) {
          currentTo = now;
        }
        currentFrom = new Date(
          currentTo.getTime() - hours * 3600 * 1000
        );
        updateWindowLabel();
        reloadHistoryWithCurrentWindow();
      });
    });
  }

  function initSeriesChangeHandler() {
    const container = document.getElementById("history-series-container");
    if (!container) return;
    container.addEventListener("change", () => {
      reloadHistoryWithCurrentWindow();
    });
  }

  function initRefreshButton() {
    const btn = document.querySelector(".history-refresh-btn");
    if (!btn) return;

    btn.addEventListener("click", () => {
      // przy odświeżeniu trzymamy bieżący zakres (from/to)
      reloadHistoryWithCurrentWindow();
    });
  }

  function initWindowButtons() {
    const leftBtn = document.querySelector(
      '.history-window-btn[data-dir="-1"]'
    );
    const rightBtn = document.querySelector(
      '.history-window-btn[data-dir="1"]'
    );

    if (leftBtn) {
      leftBtn.addEventListener("click", () => {
        shiftWindow(-1);
        reloadHistoryWithCurrentWindow();
      });
    }

    if (rightBtn) {
      rightBtn.addEventListener("click", () => {
        shiftWindow(1);
        reloadHistoryWithCurrentWindow();
      });
    }
  }

  async function initHistoryView() {
    try {
      setStatus("Inicjalizacja widoku historii...");
      initRangeButtons();
      initWindowButtons();
      initRefreshButton();

      const fields = await fetchFields();
      buildSeriesCheckboxes(fields);
      initSeriesChangeHandler();

      // Ustawiamy domyślnie zakres 6h (prawe okno = teraz)
      setInitialWindow(DEFAULT_RANGE_HOURS);

      await reloadHistoryWithCurrentWindow();
    } catch (err) {
      console.error(err);
      setStatus(
        err && err.message
          ? `Błąd inicjalizacji historii: ${err.message}`
          : "Błąd inicjalizacji widoku historii."
      );
    }
  }

  document.addEventListener("DOMContentLoaded", initHistoryView);
})();
