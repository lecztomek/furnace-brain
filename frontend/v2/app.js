// New GUI (v2) - the main screen. The same API as the current GUI; nothing here changes the controller.
(function () {
  "use strict";

  const STATE_MS = 2000;            // the state, like the current main page
  const REPORT_MS = 5 * 60 * 1000;  // coal of the last days and the forecast change slowly
  const FAN_DEFAULT = 45;           // when the blower setting can't be read (as manual-switches.js)
  const ARC = 528;                  // 2πr of the power ring (r = 84)
  const DAYS = ["nd", "pn", "wt", "śr", "cz", "pt", "sb"];          // under the coal bars
  const DAY_NAMES = ["Nd", "Pon", "Wt", "Śr", "Czw", "Pt", "Sob"];  // in the header
  const MONTHS = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"];

  const $ = (id) => document.getElementById(id);
  const num = (v, d = 1) => (v == null || Number.isNaN(Number(v)) ? "--" : Number(v).toFixed(d).replace(".", ","));

  let state = null;
  let busy = null;      // a mode button or a manual switch being sent

  // ---------- fit the 1200x704 drawing (the 1024x600 panel) to the screen ----------
  // the panel and landscape screens: 1200x704; a phone held upright: 720 wide, as tall as the screen
  function fit() {
    const st = $("stage");
    const portrait = innerHeight > innerWidth * 1.15;
    // landscape: the 1200x704 drawing grows to the screen's shape - a taller screen gets a taller
    // drawing, a wider one a wider drawing - so it always fills the screen, no bars
    const ratio = innerHeight / innerWidth;
    let W = portrait ? 640 : 1200;
    let H = portrait ? Math.max(980, Math.round(640 * ratio)) : Math.round(1200 * ratio);
    // a not-so-tall portrait window: a wider drawing instead of bars at the sides
    if (portrait && H > 640 * ratio) W = Math.round(H / ratio);
    // that wider drawing: the power and the usage side by side, each the full height
    st.classList.toggle("wide", portrait && ratio < 1.6);   // not a phone: a tablet or a window
    if (!portrait && H < 704) { H = 704; W = Math.round(704 / ratio); }
    st.classList.toggle("portrait", portrait);
    st.style.width = `${W}px`;
    st.style.height = `${H}px`;
    const k = Math.min(innerWidth / W, innerHeight / H);
    st.style.transform = `translate(${(innerWidth - W * k) / 2}px, ${(innerHeight - H * k) / 2}px) scale(${k})`;
  }
  addEventListener("resize", fit);
  fit();

  // ---------- menu ----------
  const toggleMenu = () => ["drawer", "shade"].forEach((id) => $(id).classList.toggle("open"));
  $("burger").addEventListener("click", toggleMenu);
  $("shade").addEventListener("click", toggleMenu);

  // the sign: a tap shows what is wrong
  $("sign").addEventListener("click", () => $("sign-pop").classList.toggle("open"));
  $("sign-pop").addEventListener("click", () => $("sign-pop").classList.remove("open"));
  // open: a tap anywhere only closes it - it never reaches a button under it
  document.addEventListener("click", (e) => {
    const pop = $("sign-pop");
    if (!pop.classList.contains("open") || pop.contains(e.target) || $("sign").contains(e.target)) return;
    e.stopPropagation();
    e.preventDefault();
    pop.classList.remove("open");
  }, true);

  // ---------- clock ----------
  function clock() {
    const d = new Date();
    $("clock").textContent = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    $("date").textContent = `${DAY_NAMES[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  }
  clock();
  setInterval(clock, 5000);

  // ---------- API ----------
  async function call(url, options = {}) {
    const res = await fetch(url, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...options });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body?.detail?.msg || body?.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }
  const setMode = (m) => call(`/api/state/mode/${m}`, { method: "POST" });
  const setManual = (patch) => call("/api/manual/outputs", { method: "POST", body: JSON.stringify(patch) });
  const configValue = (module, key) => call(`/api/config/value/${module}/${key}`).then((r) => r.value).catch(() => null);

  // ---------- state ----------
  function pill(el, on, text) {
    el.textContent = text ?? (on ? "ON" : "OFF");
    el.classList.toggle("on", !!on);
  }

  function render() {
    const s = state;
    if (!s) return;
    const sens = s.sensors || {};
    const out = s.outputs || {};
    const stop = s.mode === "OFF" || s.mode === "MANUAL";
    $("stage").classList.toggle("stop", stop);

    // header: one sign - problems win over warnings
    const problems = s.problems || [];
    const warnings = s.warnings || [];
    const sign = $("sign");
    sign.classList.toggle("bad", problems.length > 0);
    sign.classList.toggle("warn", !problems.length && warnings.length > 0);
    const icon = problems.length ? '<path d="M8 2.5h8L21.5 8v8L16 21.5H8L2.5 16V8z"/><path d="M12 7v6M12 16.5v.5"/>'
      : warnings.length ? '<path d="M12 3 2 20h20z"/><path d="M12 9.5v5M12 17.5v.5"/>' : '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>';
    if ($("sign-icon").dataset.k !== icon) { $("sign-icon").innerHTML = icon; $("sign-icon").dataset.k = icon; }
    const first = problems[0] || warnings[0];
    $("sign-text").textContent = first ? (first.short || (problems.length ? "PROBLEM" : "UWAGA")) : "OK";
    // rebuilt only when it changes - the state comes every 2 s
    const pop = [...problems.map((p) => ["bad", p]), ...warnings.map((p) => ["warn", p])]
      .map(([cls, p]) => `<p class="${cls}"><b>${p.short || ""}</b> ${p.text || ""}</p>`).join("") || "<p>Wszystko w porządku.</p>";
    if ($("sign-pop").dataset.k !== pop) { $("sign-pop").innerHTML = pop; $("sign-pop").dataset.k = pop; }
    // version and uptime, as the current GUI
    const app = s.app || {};
    let up = "";
    if (app.started_at) {
      const m = Math.max(0, Math.floor((Date.now() / 1000 - app.started_at) / 60));
      up = m >= 1440 ? `${Math.floor(m / 1440)} d ${Math.floor((m % 1440) / 60)} h` : m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
    }
    $("app-ver").textContent = app.version ? ` • v${app.version}${up ? ` • ${up}` : ""}` : "";
    $("outside").textContent = sens.outside_temp == null ? "--" : `${num(sens.outside_temp)} °C`;

    // devices
    const fan = Number(out.fan_power) || 0;
    pill($("fan-pill"), fan > 0);
    // off: the speed it is set to (the blower's setting)
    $("fan-sub").textContent = fan > 0 ? `${Math.round(fan)}%` : fanSet != null ? `${fanSet}%` : "";
    pill($("feeder-pill"), !!out.feeder_on);
    $("hopper").textContent = sens.hopper_temp == null ? "--" : `${num(sens.hopper_temp, 0)}°`;
    pill($("co-pill"), !!out.pump_co_on);
    pill($("cwu-pill"), !!out.pump_cwu_on);
    // the valve: net seconds of opening (+) / closing (-) in the last 30 min, as the current GUI
    const mv = Math.round(Number((s.radiators || {}).moves_30m_s));
    const mp = $("mixer-pill");
    mp.textContent = Number.isFinite(mv) ? `${mv > 0 ? "+" : mv < 0 ? "−" : ""}${Math.abs(mv)}s` : "--";
    mp.classList.toggle("open", mv > 0);
    mp.classList.toggle("close", mv < 0);
    renderToggles();

    // the picture
    $("boiler").textContent = num(sens.boiler_temp, 0);   // whole degrees are enough
    const tr = (s.trend || {}).boiler_per_10m;
    const trend = $("trend");
    trend.textContent = tr == null ? "" : `${tr > 0 ? "+" : tr < 0 ? "-" : "±"}${num(Math.abs(tr))}°/10 min`;
    trend.setAttribute("fill", tr == null || Math.abs(tr) < 0.05 ? "#aeb5be" : tr > 0 ? "#66cc99" : "#a0caee");
    $("flue").textContent = sens.flue_gas_temp == null ? "--" : `${num(sens.flue_gas_temp, 0)} °C`;
    $("rad").textContent = sens.radiators_temp == null ? "--" : `${num(sens.radiators_temp, 0)}°`;
    const rad = s.radiators || {};
    // the curve switched on shows "krzywa" at once (without the outside temperature it holds the fixed target)
    // a sunny day: the curve is lower the whole day
    const sun = Number(rad.sun_drop) > 0 ? ` ☀−${num(rad.sun_drop, 0)}°` : "";
    $("rad-label").textContent = (rad.curve_enabled || rad.curve_active ? "krzywa" : "zadana") + sun;
    $("rad-set").textContent = rad.target == null ? "--" : `${num(rad.target)}°`;
    // the fire follows the boiler temperature, as the current GUI: none below 30°C, full at 50°C
    const bt = Number(sens.boiler_temp);
    const fire = $("fire");
    if (!Number.isFinite(bt) || bt < 30) fire.style.display = "none";
    else {
      const k = 0.3 + 0.7 * Math.min(1, Math.max(0, (bt - 30) / 20));
      fire.style.display = "";
      fire.setAttribute("transform", `translate(134 356) scale(${k.toFixed(2)}) translate(-134 -356)`);
    }
    $("scene").classList.toggle("no-co", !out.pump_co_on);

    // power
    const pct = Number(out.power_percent);
    $("power-arc").setAttribute("stroke-dasharray", `${Number.isFinite(pct) ? (ARC * Math.max(0, Math.min(100, pct))) / 100 : 0} ${ARC}`);
    $("power-kw").textContent = num(s.power_kw);
    $("coal-now").textContent = num(s.coal_kgph, 2);

    // modes: the current one lit (MANUAL = Stop with the switches on)
    const lit = s.mode === "MANUAL" ? "OFF" : s.mode;
    document.querySelectorAll(".mbtn").forEach((b) => {
      b.classList.toggle("active", b.dataset.mode === lit);
      b.classList.toggle("busy", busy === b);
    });
  }

  async function loadState() {
    try {
      state = await call("/api/state/current");
      render();
    } catch (err) {
      $("sign-text").textContent = "BRAK POŁĄCZENIA";
      $("sign").classList.add("bad");
    }
  }

  let fanSet = null;
  async function loadSetpoint() {
    const v = await configValue("power_work", "boiler_set_temp");
    $("boiler-set").textContent = v == null ? "--" : `${num(v, 0)} °C`;
    const f = Math.round(Number(await configValue("blower", "base_fan_percent")));
    fanSet = f > 0 && f <= 100 ? f : null;
  }

  // ---------- modes ----------
  function modeMsg(text, err = false) {
    const el = $("mode-msg");
    el.textContent = text;
    el.classList.toggle("err", err);
  }

  document.querySelectorAll(".mbtn").forEach((b) => b.addEventListener("click", async () => {
    if (busy || !state) return;
    const want = b.dataset.mode;
    if (state.mode === want) return;
    busy = b;
    render();
    modeMsg("zmieniam tryb…");
    try {
      // leaving MANUAL: switch the outputs off first, as the manual switches do
      if (state.mode === "MANUAL") await setManual({ feeder_on: false, fan_power: 0 });
      state = await setMode(want);
      modeMsg("");
    } catch (err) {
      modeMsg(`błąd: ${err.message}`, true);
    }
    busy = null;
    render();
  }));

  // ---------- manual switches (only from STOP), the same rules as the current GUI ----------
  async function fanPercent() {
    const v = Math.round(Number(await configValue("blower", "base_fan_percent")));
    return v > 0 && v <= 100 ? v : FAN_DEFAULT;
  }
  const SWITCHES = [
    { el: $("fan-toggle"), isOn: (o) => Number(o.fan_power) > 0, patch: async (on) => ({ fan_power: on ? await fanPercent() : 0 }) },
    { el: $("feeder-toggle"), isOn: (o) => !!o.feeder_on, patch: async (on) => ({ feeder_on: on }) },
  ];
  const switchOn = (sw) => state && state.mode === "MANUAL" && sw.isOn(state.outputs || {});

  function renderToggles() {
    SWITCHES.forEach((sw) => {
      sw.el.classList.toggle("on", switchOn(sw));
      sw.el.classList.toggle("busy", busy === sw);
    });
  }

  SWITCHES.forEach((sw) => sw.el.addEventListener("click", async (e) => {
    e.stopPropagation();   // the card around opens the settings popup
    if (busy || !state) return;
    const wasOn = switchOn(sw);
    const othersOn = SWITCHES.some((x) => x !== sw && switchOn(x));
    if (!wasOn && !(state.mode === "OFF" || (state.mode === "MANUAL" && othersOn))) return;
    busy = sw;
    renderToggles();
    try {
      const patch = await sw.patch(!wasOn);
      const outs = state.outputs || {};
      if (!wasOn && state.mode === "OFF") {
        await setMode("MANUAL");
        await setManual({ feeder_on: false, fan_power: 0, mixer_open_on: false, mixer_close_on: false,
                          pump_co_on: !!outs.pump_co_on, pump_cwu_on: !!outs.pump_cwu_on, ...patch });
        state.mode = "MANUAL";
      } else {
        await setManual(patch);
        if (wasOn && !othersOn) {
          await setMode("OFF");
          state.mode = "OFF";
        }
      }
      state.outputs = { ...outs, ...patch };   // right away; the next poll confirms it
      modeMsg("");
    } catch (err) {
      modeMsg(`błąd: ${err.message}`, true);
    }
    busy = null;
    render();
  }));

  // ---------- coal: today, now, the last two days + today's expected total ----------
  async function loadCoal() {
    let report = null;
    let forecast = null;
    clearTimeout(loadCoal.retry);
    try { report = await call("/api/analytics/report"); } catch (err) { /* statistics still being counted */ }
    try { forecast = await call("/api/analytics/forecast"); } catch (err) { /* no forecast */ }
    // after a start the statistics are counted for a while: ask again soon, not in 5 minutes
    if (!report || report.stale) loadCoal.retry = setTimeout(loadCoal, 15000);
    if (!report) return;
    const today = report.today;
    const burnt = report.periods?.today?.coal_kg;
    $("coal-today").textContent = num(burnt);
    const days = (report.daily || []).filter((d) => d.date < today).slice(-1);   // yesterday
    const rest = (forecast?.days || []).find((d) => d.date === today)?.coal_kg;
    const expected = burnt != null && rest != null ? burnt + rest : null;
    drawBars(days, burnt, expected);
  }

  function drawBars(days, burnt, expected) {
    const H = 104;                                   // bar height of the biggest value
    const max = Math.max(1, ...days.map((d) => d.coal_kg || 0), expected || 0, burnt || 0);
    const h = (v) => Math.max(4, (H * (v || 0)) / max);
    const base = 146;
    const label = (iso) => DAYS[new Date(`${iso}T12:00:00`).getDay()];
    let svg = '<g font-size="30" font-weight="700" text-anchor="middle">';
    days.forEach((d, i) => {
      const x = 6 + i * 86;
      svg += `<rect x="${x}" y="${base - h(d.coal_kg)}" width="64" height="${h(d.coal_kg)}" rx="9" fill="#4b525c"/>` +
             `<text x="${x + 32}" y="${base - h(d.coal_kg) - 10}" fill="#c3c8cf">${Math.round(d.coal_kg || 0)}</text>` +
             `<text x="${x + 32}" y="178" fill="#aeb5be" font-weight="600">${label(d.date)}</text>`;
    });
    const x = 92;
    if (expected != null) {
      svg += `<rect x="${x}" y="${base - h(expected)}" width="64" height="${h(expected)}" rx="9" fill="none" stroke="#f6cf5a" stroke-width="2.5" stroke-dasharray="6 5" opacity=".7"/>`;
    }
    svg += `<rect x="${x}" y="${base - h(burnt)}" width="64" height="${h(burnt)}" rx="9" fill="#f6cf5a"/>` +
           `<text x="${x + 32}" y="${base - h(expected ?? burnt) - 10}" fill="#f6cf5a">${expected != null ? `~${Math.round(expected)}` : num(burnt)}</text>` +
           `<text x="${x + 32}" y="178" fill="#f6cf5a">dziś</text></g>`;
    $("coal-bars").innerHTML = svg;
  }

  // feeder presets: the most power each gives (at 100% the feeder runs on/(on+off) of the time)
  async function presetPower() {
    const [kgph, mj] = await Promise.all([configValue("stats", "feeder_kg_per_hour"), configValue("stats", "calorific_mj_per_kg")]);
    document.querySelectorAll(".feeder-preset-btn").forEach((b) => {
      const on = Number(b.dataset.feedOn), off = Number(b.dataset.feedOff);
      const kw = Number(kgph) * (on / (on + off)) * Number(mj) / 3.6;
      b.querySelector(".preset-sub").textContent = `${Number.isFinite(kw) && kw > 0 ? `maks. ~${num(kw, 0)} kW • ` : ""}${on}s / ${off}s`;
    });
  }
  presetPower();

  // after saving in a popup: show the new setpoint right away
  document.addEventListener("click", (e) => {
    if (e.target.closest && e.target.closest("#temp-save, #feeder-save")) setTimeout(loadSetpoint, 1500);
  });

  loadState();
  loadSetpoint();
  loadCoal();
  setInterval(loadState, STATE_MS);
  setInterval(loadSetpoint, 60 * 1000);
  setInterval(loadCoal, REPORT_MS);
})();
