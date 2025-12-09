// js/history.js

(function () {
  const HISTORY_API_BASE = "http://127.0.0.1:8000/api/history";

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

  async function fetchHistoryData(rangeHours) {
    const now = new Date();
    const to = isoNoMs(now);
    const from = isoNoMs(new Date(now.getTime() - rangeHours * 3600 * 1000));

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

  async function reloadHistory(rangeHours) {
    try {
      setStatus("Ładowanie danych historii...");
      const items = await fetchHistoryData(rangeHours);
      if (!items.length) {
        setStatus("Brak danych w wybranym zakresie.");
      } else {
        setStatus(
          `Załadowano ${items.length} punktów (zakres ${rangeHours} h).`
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
        reloadHistory(hours);
      });
    });
  }

  function initSeriesChangeHandler() {
    const container = document.getElementById("history-series-container");
    if (!container) return;
    container.addEventListener("change", () => {
      const hours = getCurrentRangeHours();
      reloadHistory(hours);
    });
  }

  function initRefreshButton() {
    const btn = document.querySelector(".history-refresh-btn");
    if (!btn) return;

    btn.addEventListener("click", () => {
      const hours = getCurrentRangeHours();
      reloadHistory(hours);
    });
  }

  async function initHistoryView() {
    try {
      setStatus("Inicjalizacja widoku historii...");
      initRangeButtons();
      initRefreshButton();

      const fields = await fetchFields();
      buildSeriesCheckboxes(fields);
      initSeriesChangeHandler();

      // Ustawiamy domyślnie zakres 6h
      const defaultBtn = document.querySelector(
        `.history-range-btn[data-range-hours="${DEFAULT_RANGE_HOURS}"]`
      );
      if (defaultBtn) {
        document
          .querySelectorAll(".history-range-btn")
          .forEach((b) => b.classList.remove("active"));
        defaultBtn.classList.add("active");
      }

      await reloadHistory(DEFAULT_RANGE_HOURS);
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
