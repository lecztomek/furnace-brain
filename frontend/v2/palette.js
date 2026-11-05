// New GUI (v2) - one palette (docs/concept/palette.md). The current GUI's statistics script
// colours its charts itself; here every chart's colours are mapped onto the palette, once,
// when the chart is made (nothing runs while it is shown).
(function () {
  "use strict";
  const HEX = {"#4ade80": "#66cc99", "#22c55e": "#66cc99", "#7ccf9a": "#66cc99", "#3ddc7a": "#66cc99", "#86efac": "#66cc99", "#166534": "#2d5d45", "#2a5c40": "#2d5d45", "#15803d": "#2d5d45", "#1f9d4c": "#2d5d45", "#2f7a52": "#2d5d45", "#16a34a": "#2d5d45", "#106a31": "#1c3b2b", "#1d4f35": "#1c3b2b", "#145a2c": "#1c3b2b", "#24563b": "#1c3b2b", "#0a2f17": "#1c3b2b", "#13301f": "#1c3b2b", "#14532d": "#1c3b2b", "#f87171": "#cc7466", "#ef4444": "#cc7466", "#ff5a5a": "#cc7466", "#ff4d4d": "#cc7466", "#ff6b6b": "#cc7466", "#e53935": "#cc7466", "#ff8a80": "#cc7466", "#fca5a5": "#cc7466", "#fecaca": "#cc7466", "#b91c1c": "#5d332d", "#991b1b": "#5d332d", "#dc2626": "#5d332d", "#a31515": "#5d332d", "#d32f2f": "#5d332d", "#6b1414": "#5d332d", "#7f1d1d": "#5d332d", "#340909": "#301b17", "#fbbf24": "#f0c033", "#f59e0b": "#f0c033", "#fde047": "#f0c033", "#facc15": "#f0c033", "#eab308": "#f0c033", "#ffd21f": "#f0c033", "#f5c518": "#f0c033", "#e6b800": "#f0c033", "#f4c000": "#f0c033", "#e0b000": "#f0c033", "#fde68a": "#f6cf5a", "#ffd84d": "#f6cf5a", "#ffe066": "#f6cf5a", "#ffe680": "#f6cf5a", "#fff3b0": "#f6cf5a", "#ffe9a0": "#f6cf5a", "#fff4b8": "#f6cf5a", "#fff6c0": "#f6cf5a", "#fef08a": "#f6cf5a", "#b45309": "#605329", "#92400e": "#605329", "#854d0e": "#605329", "#b88a00": "#605329", "#5a4a0c": "#605329", "#a16207": "#605329", "#2d2508": "#272211", "#f97316": "#f0c033", "#fb923c": "#f0c033", "#ea580c": "#f0c033", "#c2410c": "#f0c033", "#ff9a1a": "#f0c033", "#ff8c1a": "#f0c033", "#fdba74": "#f6cf5a", "#ffb38a": "#f6cf5a", "#fed7aa": "#f6cf5a", "#9a3412": "#5d432d", "#7c2d12": "#5d432d", "#60a5fa": "#669ccc", "#3b82f6": "#669ccc", "#38bdf8": "#669ccc", "#4aa8ff": "#669ccc", "#1d4ed8": "#669ccc", "#2563eb": "#669ccc", "#93c5fd": "#a0caee", "#bae6fd": "#a0caee", "#7cc4ff": "#a0caee", "#9fd2ff": "#a0caee", "#dbeafe": "#a0caee", "#1e40af": "#2d465d", "#0f2747": "#2d465d", "#1e3a8a": "#2d465d", "#1f2937": "#2b2f36", "#233241": "#2b2f36", "#2c3b47": "#2b2f36", "#293545": "#2b2f36", "#253243": "#2b2f36", "#334155": "#2b2f36", "#3f4b5f": "#2b2f36", "#0d151b": "#16191d", "#111a22": "#16191d", "#0f172a": "#16191d", "#111827": "#16191d", "#1f2933": "#16191d", "#0f1620": "#16191d", "#12151a": "#16191d", "#374151": "#383e47", "#475569": "#383e47", "#4b5563": "#383e47", "#3b4254": "#383e47", "#64748b": "#89929f", "#66708a": "#89929f", "#6b7280": "#89929f", "#9ca3af": "#b0b6bf", "#cbd5e1": "#caced3", "#e2e8f0": "#caced3"};
  const RGB = {"34, 197, 94": "102, 204, 153", "74, 222, 128": "102, 204, 153", "124, 207, 154": "102, 204, 153", "61, 220, 122": "102, 204, 153", "249, 115, 22": "240, 192, 51", "245, 158, 11": "240, 192, 51", "250, 204, 21": "240, 192, 51", "251, 191, 36": "240, 192, 51", "255, 210, 31": "240, 192, 51", "239, 68, 68": "204, 116, 102", "248, 113, 113": "204, 116, 102", "255, 77, 77": "204, 116, 102", "96, 165, 250": "102, 156, 204", "59, 130, 246": "102, 156, 204", "74, 168, 255": "102, 156, 204", "17, 24, 39": "22, 25, 29", "10, 18, 26": "22, 25, 29", "17, 26, 34": "22, 25, 29", "148, 163, 184": "176, 182, 191", "156, 163, 175": "176, 182, 191"};
  const map = (v) => {
    if (typeof v !== "string") return v;
    return v.replace(/#[0-9a-fA-F]{6}\b/g, (h) => HEX[h.toLowerCase()] || h)
            .replace(/(rgba?\()\s*(\d+),\s*(\d+),\s*(\d+)/g, (all, p, r, g, b) => {
              const k = r + ", " + g + ", " + b;
              return RGB[k] ? p + RGB[k] : all;
            });
  };
  function walk(o, depth) {
    if (!o || typeof o !== "object" || depth > 6) return;
    for (const k of Object.keys(o)) {
      const v = o[k];
      if (typeof v === "string") o[k] = map(v);
      else if (Array.isArray(v)) v.forEach((x, i) => { if (typeof x === "string") v[i] = map(x); else walk(x, depth + 1); });
      else if (v && typeof v === "object" && !(v instanceof HTMLElement)) walk(v, depth + 1);
    }
  }
  const C = window.Chart;
  if (!C) return;
  function PaletteChart(item, cfg) { walk(cfg, 0); return new C(item, cfg); }
  Object.setPrototypeOf(PaletteChart, C);
  PaletteChart.prototype = C.prototype;
  window.Chart = PaletteChart;
})();
