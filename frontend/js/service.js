// js/service.js - service page: tuning advice (PID, feeder, blower) and the Raspberry Pi diagnostics
(function () {
  const $ = (id) => document.getElementById(id);
  const opened = new Set();   // cards tapped open stay open after a refresh
  const VERDICT = { ok: "wszystko OK", warn: "są uwagi", bad: "do poprawy" };
  let tab = "tuning";
  let timer = null;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const num = (v, nd = 1) => (v == null || isNaN(v) ? "--" : Number(v).toFixed(nd).replace(".", ","));
  const signed = (v, nd = 1) => (v == null || isNaN(v) ? "--" : (v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v), nd));
  const when = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)} ${iso.slice(11, 16)}` : "--");
  const value = (v) => (typeof v === "boolean" ? (v ? "wł." : "wył.") : typeof v === "number" ? String(v).replace(".", ",") : esc(v));

  function setStatus(text) { $("sv-status").textContent = text; }

  function verdict(el, status, text) {
    el.className = `sv-verdict sv-verdict--${status}`;
    el.textContent = text || VERDICT[status] || "";
  }

  // ---------------------------------------------------------------- tuning

  const AREA = {
    "PID": ["sliders", "PID"],
    "Całka (Ki)": ["target", "Całka (Ki)"],
    "Wzmocnienie (Kp)": ["zap", "Kp"],
    "Ślimak": ["auger", "Ślimak"],
    "Dmuchawa": ["wind", "Dmuchawa"],
    "Rozpalanie": ["flame", "Rozpalanie"],
    "Grzejniki": ["radiator", "Zawór"],
    "Krzywa": ["thermometer", "Krzywa"],
    "Ustawienia": ["wrench", "Ustawienia"],
    "Zdarzenia": ["alert", "Zdarzenia"],
  };
  const WORD = { ok: "OK", warn: "Zmień", bad: "Pilne", info: "Sprawdź" };
  const PID_AREAS = ["PID", "Całka (Ki)", "Wzmocnienie (Kp)", "Ślimak", "Dmuchawa", "Krzywa", "Grzejniki"];

  function bigTile(icon, label, val, status, sub) {
    return `<div class="sv-tile sv-tile--${status}"><span class="sv-tile__icon">${FBIcon(icon)}</span>
      <span class="sv-tile__label">${label}</span><span class="sv-tile__val">${val}</span>${sub ? `<span class="sv-tile__sub">${sub}</span>` : ""}</div>`;
  }

  const TONE = { ok: "#4ade80", warn: "#fbbf24", bad: "#f87171", info: "#cbd5e1" };

  // ring gauge: how full / how good, coloured by the status, the number in the middle
  function ring(pct, status, center, label, sub) {
    const r = 34, c = 2 * Math.PI * r, f = Math.max(0, Math.min(100, Number(pct) || 0)) / 100;
    return `<div class="sv-ring sv-ring--${status}">
      <svg viewBox="0 0 84 84" aria-hidden="true"><circle cx="42" cy="42" r="${r}" class="sv-ring__track"/>
        <circle cx="42" cy="42" r="${r}" class="sv-ring__arc" stroke="${TONE[status] || TONE.info}"
          stroke-dasharray="${(c * f).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 42 42)"/></svg>
      <span class="sv-ring__center">${center}</span>
      <span class="sv-ring__label">${label}</span>${sub ? `<span class="sv-ring__sub">${sub}</span>` : ""}
    </div>`;
  }

  // bar with the good / so-so / bad zones and a marker where the value is
  function bullet(icon, label, valueText, v, lo, hi, zones, status) {
    const pos = (x) => (100 * (Math.max(lo, Math.min(hi, x)) - lo)) / (hi - lo);
    const bands = zones.map(([a, b, st]) => `<span class="sv-zone sv-zone--${st}" style="left:${pos(a)}%;width:${pos(b) - pos(a)}%"></span>`).join("");
    return `<div class="sv-bullet sv-bullet--${status}">
      <span class="sv-bullet__icon">${FBIcon(icon)}</span>
      <span class="sv-bullet__label">${label}</span>
      <span class="sv-bullet__track">${bands}<span class="sv-bullet__mark" style="left:${pos(v)}%"></span></span>
      <span class="sv-bullet__val">${valueText}</span>
    </div>`;
  }

  function renderTuning(r) {
    verdict($("tn-verdict"), r.verdict.status, r.verdict.text);
    const reg = r.regulation;
    if (reg && reg.hours >= 1) {
      const st = (good, okish) => (good ? "ok" : okish ? "warn" : "bad");
      const pc = r.pid_change;
      $("tn-period").textContent = `Liczone z: ostatnich ${num(reg.hours, 0)} h spokojnej pracy (od ${when(r.regulation_since)}` +
        (pc ? `, czyli od zmiany: ${pc.label})` : ", bez 2 h po rozpaleniu i po zmianie zadanej)");
      const pa = reg.power_avg ?? 0;
      $("tn-hero").innerHTML = `<div class="sv-hero2">
        ${ring(reg.within_2, st(reg.within_2 >= 80, reg.within_2 >= 60), `${reg.within_2}%`, "w ±2°C", "od zadanej")}
        <div class="sv-bullets">
          ${bullet("target", "Od zadanej", `${signed(reg.mean_err)}°`, reg.mean_err, -4, 4,
            [[-4, -2, "bad"], [-2, -1, "warn"], [-1, 1, "ok"], [1, 2, "warn"], [2, 4, "bad"]],
            st(Math.abs(reg.mean_err) <= 1, Math.abs(reg.mean_err) <= 2))}
          ${bullet("activity", "Kołysanie", `±${num(reg.std)}°`, reg.std, 0, 4,
            [[0, 1.5, "ok"], [1.5, 2.5, "warn"], [2.5, 4, "bad"]], st(reg.std <= 1.5, reg.std <= 2.5))}
          ${bullet("gauge", "Moc średnio", `${reg.power_avg ?? "--"}%`, pa, 0, 100,
            [[0, 35, "warn"], [35, 75, "ok"], [75, 90, "warn"], [90, 100, "bad"]], st(pa >= 35 && pa <= 75, pa < 90))}
        </div></div>`;
    } else {
      $("tn-period").textContent = "Liczone z: ostatnich 7 dni, praca od 2 h po rozpaleniu i po zmianie zadanej";
      $("tn-hero").innerHTML = `<div class="sv-empty">${FBIcon("hourglass", "ic--inline")} Za mało spokojnej pracy do oceny</div>`;
    }

    // the PID parts always, the rest only when something is wrong
    const items = r.items.filter((i) => PID_AREAS.includes(i.area) || ["warn", "bad", "hint"].includes(i.status));
    $("tn-items").innerHTML = table(items.map((i) => {
      const [icon, name] = AREA[i.area] || ["info", i.area];
      const t = splitTip(i.tip);
      const showSub = i.status !== "ok";   // info rows carry how-to text (e.g. reading the flame)
      return { status: i.status, icon: i.status === "ok" ? "check" : icon, name, text: i.title,
               sub: showSub ? [t.action, i.detail].filter(Boolean).join(" • ") : "", right: t.change || (i.status === "ok" ? "OK" : "") };
    }));
    renderRooms(r.settings || {});
    renderChanges(r.changes || []);
    renderLive(r.live || {}, r.settings || {});
  }

  // what to turn when the rooms are too warm or too cold - with the current curve values
  function renderRooms(s) {
    const m = s.curve || {};
    const at0 = Number(m.curve_at_0), slope = Number(m.curve_slope);
    if (!m.curve_enabled || !isFinite(at0) || !isFinite(slope)) {
      const t = Number(m.target_temp);
      $("tn-rooms").innerHTML = table([
        { status: "info", icon: "snowflake", name: "Za zimno", text: "Podnieś temperaturę grzejników", sub: "target_temp", right: isFinite(t) ? `${num(t, 0)} → ${num(t + 1, 0)}°C` : "+1°C" },
        { status: "info", icon: "sun", name: "Za ciepło", text: "Obniż temperaturę grzejników", sub: "target_temp", right: isFinite(t) ? `${num(t, 0)} → ${num(t - 1, 0)}°C` : "−1°C" },
      ]);
      return;
    }
    const n2 = (v) => num(v, 2);
    // the curve turns around 0 °C: curve_at_0 moves the whole line, curve_slope turns it around 0 °C
    const at = (t) => at0 - slope * t;
    $("tn-rooms").innerHTML = table([
      { status: "info", icon: "snowflake", name: "Za zimno przy 0°C", text: "Podnieś krzywą o 1°C",
        sub: `curve_at_0 - grzejniki przy 0°C ${num(at0, 0)} → ${num(at0 + 1, 0)}°C (cała krzywa o 1°C wyżej)`, right: `${num(at0, 0)} → ${num(at0 + 1, 0)}` },
      { status: "info", icon: "sun", name: "Za ciepło przy 0°C", text: "Obniż krzywą o 1°C",
        sub: `curve_at_0 - grzejniki przy 0°C ${num(at0, 0)} → ${num(at0 - 1, 0)}°C (cała krzywa o 1°C niżej)`, right: `${num(at0, 0)} → ${num(at0 - 1, 0)}` },
      { status: "info", icon: "snowflake", name: "Za zimno przy −10°C", text: "Stromsza krzywa",
        sub: `curve_slope - przy −10°C ${num(at(-10))} → ${num(at(-10) + 0.5)}°C, przy 0°C bez zmian, przy +10°C o 0,5°C chłodniej`,
        right: `${n2(slope)} → ${n2(slope + 0.05)}` },
      { status: "info", icon: "sun", name: "Za ciepło przy −10°C", text: "Łagodniejsza krzywa",
        sub: `curve_slope - przy −10°C ${num(at(-10))} → ${num(at(-10) - 0.5)}°C, przy 0°C bez zmian, przy +10°C o 0,5°C cieplej`,
        right: `${n2(slope)} → ${n2(slope - 0.05)}` },
      { status: "info", icon: "cloudSun", name: "Za ciepło przy +10°C", text: "Stromsza krzywa",
        sub: `curve_slope - przy +10°C ${num(at(10))} → ${num(at(10) - 0.5)}°C, przy 0°C bez zmian, przy −10°C o 0,5°C cieplej`,
        right: `${n2(slope)} → ${n2(slope + 0.05)}` },
      { status: "info", icon: "cloudSun", name: "Za zimno przy +10°C", text: "Łagodniejsza krzywa",
        sub: `curve_slope - przy +10°C ${num(at(10))} → ${num(at(10) + 0.5)}°C, przy 0°C bez zmian, przy −10°C o 0,5°C chłodniej`,
        right: `${n2(slope)} → ${n2(slope - 0.05)}` },
    ]);
  }

  // "Wydłuż podanie ślimaka (8 → 9 s)." -> action "Wydłuż podanie ślimaka", change "8 → 9 s"
  function splitTip(tip) {
    const m = /^(.*?)\s*\(([^()]*→[^()]*)\)/.exec(tip || "");
    return m ? { action: m[1], change: m[2] } : { action: (tip || "").replace(/\.$/, ""), change: "" };
  }

  function cardClass(status, key) {
    return `sv-card sv-card--${status}${opened.has(key) ? " is-open" : ""}`;
  }



  function renderChanges(list) {
    $("tn-changes-card").style.display = list.length ? "" : "none";
    const badge = { better: ["lepiej", "ok"], worse: ["gorzej", "bad"], same: ["bez różnicy", "info"] };
    const bar = (label, s) => s
      ? `<div class="sv-bar"><span class="sv-bar__label">${label}</span><span class="sv-bar__track"><span class="sv-bar__fill" style="width:${s.within_2}%"></span></span><b>${s.within_2}%</b></div>`
      : `<div class="sv-bar"><span class="sv-bar__label">${label}</span><span class="sv-bar__wait">czekam na dane</span></div>`;
    $("tn-changes").innerHTML = list.map((c) => {
      const b = badge[c.verdict];
      return `<div class="sv-change">
        <div class="sv-change__head"><b>${esc(c.label)}</b> <span class="sv-change__val">${value(c.old)} → ${value(c.new)}</span>
          ${b ? `<span class="sv-badge sv-badge--${b[1]}">${b[0]}</span>` : ""}<span class="sv-change__when">${when(c.at)}</span></div>
        ${bar("przed", c.before)}${bar("po", c.after)}
      </div>`;
    }).join("") + (list.length ? `<div class="sv-hint">Pasek = ile czasu kocioł był w ±2°C od zadanej</div>` : "");
  }

  // the PID chain now: boiler -> power (P + I) -> feeder and blower
  function renderLive(l, s) {
    const el = $("tn-live");
    if (l.mode !== "WORK" || !l.pid || l.pid.error == null) {
      const names = { IGNITION: "rozpalanie", OFF: "wyłączony", MANUAL: "ręczny" };
      el.innerHTML = `<div class="sv-empty">PID pracuje tylko w PRACY • teraz: ${names[l.mode] || l.mode || "--"}</div>`;
      return;
    }
    const p = l.pid;
    const rate = p.rate || 0;
    const arrow = Math.abs(rate) < 0.05 ? "→" : rate > 0 ? "↗" : "↘";
    const pw = s.power_work || {};
    const feed = l.feeder;
    const P = Math.max(0, p.p || 0), I = Math.max(0, p.i || 0);
    const iState = p.integral_frozen ? "‖" : p.error > 0.2 ? "▲" : p.error < -0.2 ? "▼" : "•";
    const box = (icon, label, val, sub, extra = "") => `<div class="sv-step"><span class="sv-step__icon">${FBIcon(icon)}</span>
      <span class="sv-step__label">${label}</span><span class="sv-step__val">${val}</span>${extra}<span class="sv-step__sub">${sub}</span></div>`;
    const powerBar = `<span class="sv-pbar" title="P ${num(P, 0)} + I ${num(I, 0)}">
      <span class="sv-pbar__p" style="width:${Math.min(100, P)}%"></span><span class="sv-pbar__i" style="width:${Math.min(100 - Math.min(100, P), I)}%"></span></span>`;
    el.innerHTML = [
      box("flame", "Kocioł", `${num(l.boiler)}° <span class="sv-arrowtrend">${arrow}</span>`, `zadana ${num(l.setpoint, 0)}°`),
      box("gauge", "Moc", `${num(l.power, 0)}%`, `<span class="sv-key sv-key--p">P ${num(P, 0)}</span> <span class="sv-key sv-key--i">I ${num(I, 0)} ${iState}</span>`, powerBar),
      box("auger", "Ślimak", feed ? `${num(feed.on_s, 0)} s` : "stoi", feed ? `co ${num(feed.on_s + feed.off_s, 0)} s` : ""),
      box("wind", "Dmuchawa", `${value(l.fan_percent)}%`, l.fan_duty_pct != null ? `${num(l.fan_duty_pct, 0)}% czasu` : "stoi"),
    ].join('<span class="sv-arrow">➜</span>');
  }

  // ---------------------------------------------------------------- diagnostics

  function statusTile(t) {
    const dots = t.dots ? `<span class="sv-dots">${t.dots.map((st) => `<i class="sv-dot sv-dot--${st}"></i>`).join("")}</span>` : "";
    return `<div class="sv-stat sv-stat--${t.status}"><span class="sv-stat__icon">${FBIcon(t.icon || "info")}</span>
      <span class="sv-stat__text"><span class="sv-stat__label">${esc(t.label)}</span>
      <span class="sv-stat__val">${esc(t.value)}</span>${dots}</span></div>`;
  }

  // what fills up: name, the number that matters in big digits, a bar of how full it is
  function barTile(t) {
    return `<div class="sv-disk sv-disk--${t.status}">
      <span class="sv-stat__icon">${FBIcon(t.icon || "drive")}</span>
      <span class="sv-disk__body">
        <span class="sv-disk__head"><span class="sv-disk__name">${esc(t.label)}</span>
          <span class="sv-disk__free"><b>${esc(t.big)}</b> ${esc(t.small)}</span></span>
        <span class="sv-disk__track"><span class="sv-disk__fill" style="width:${t.pct}%"></span></span>
      </span></div>`;
  }

  // a plain table: status icon | name | what (and why, smaller) | the change, in big digits
  function table(rows) {
    return `<table class="sv-table"><tbody>${rows.map((r) => `
      <tr class="sv-tr sv-tr--${r.status}">
        <td class="sv-td-icon"><span class="sv-tr__icon">${FBIcon(r.icon)}</span></td>
        <td class="sv-td-name">${esc(r.name)}</td>
        <td class="sv-td-text">${esc(r.text)}${r.sub ? `<span class="sv-td-sub">${esc(r.sub)}</span>` : ""}</td>
        <td class="sv-td-right">${r.right ? esc(r.right) : ""}</td>
      </tr>`).join("")}</tbody></table>`;
  }

  // "Do sprawdzenia" heading with the list under it - nothing when all is fine
  function problems(prefix, rows) {
    $(`${prefix}-problems-head`).style.display = rows.length ? "" : "none";
    $(`${prefix}-problems`).innerHTML = rows.length ? table(rows) : "";
  }

  function renderDiag(d) {
    verdict($("dg-verdict"), d.status);
    const tiles = d.tiles || [];
    $("dg-tiles").innerHTML =
      `<div class="sv-disks">${tiles.filter((t) => t.kind === "bar").map(barTile).join("")}</div>` +
      `<div class="sv-stats">${tiles.filter((t) => t.kind === "status").map(statusTile).join("")}</div>`;
    const issues = d.groups.flatMap((g) => g.items).filter((i) => i.status === "warn" || i.status === "bad");
    problems("dg", issues.map((i) => ({ status: i.status, icon: i.status === "bad" ? "stop" : "alert", name: i.label,
                                         text: i.value, sub: i.note })));
    $("dg-groups").innerHTML = d.groups.filter((g) => g.items.length).map((g) => `
      <h3 class="sv-group">${esc(g.title)}</h3>
      ${g.items.map((i) => `<div class="sv-row sv-row--${i.status}"><span class="sv-row__dot"></span>
        <span class="sv-row__label">${esc(i.label)}</span><span class="sv-row__val">${esc(i.value)}</span></div>`).join("")}`).join("");
  }

  const CHECK_ICONS = [["Ciągłość", "database"], ["Czujnik", "sensor"], ["Temperatura zewn", "thermometer"], ["Przegrzanie", "flame"], ["Alarmy", "bell"],
    ["Rozpalenia", "flame"], ["Regulacja", "target"], ["Efektywność", "trendUp"], ["Zużycie", "cloudSun"], ["Brak danych", "hourglass"]];
  // the checks of the statistics report (last 7 days): one small tile each, the problems explained below
  function renderChecks(report) {
    const list = report.checks || [];
    $("ck-period").textContent = report.checks_period ? `Liczone z: ${report.checks_period}` : "";
    const worst = list.some((c) => c.status === "bad") ? "bad" : list.some((c) => c.status === "warn") ? "warn" : "ok";
    const good = list.filter((c) => c.status === "ok").length;
    verdict($("ck-verdict"), worst, worst === "ok" ? `${good} z ${list.length} OK` : "");
    $("checks").innerHTML = `<div class="sv-tiles sv-tiles--checks">${list.map((c) => {
      const icon = (CHECK_ICONS.find(([k]) => c.title.startsWith(k)) || ["", "info"])[1];
      // a check counted over another period than the rest says so on its tile
      const own = c.period && c.period !== list[0].period ? esc(c.period) : "";
      return bigTile(icon, esc(c.label || c.title), esc(c.value || "--"), c.status, own);
    }).join("")}</div>`;
    problems("ck", list.filter((c) => c.status === "warn" || c.status === "bad").map((c) => (
      { status: c.status, icon: c.status === "bad" ? "stop" : "alert", name: c.label || c.title, text: c.detail, right: c.value })));
  }

  // ---------------------------------------------------------------- changelog

  // newest version first, grouped by day; a strip of days on top (how many versions each day)
  function renderChangelog(d) {
    const cur = String(d.current || "");
    $("cl-current").textContent = cur ? `na piecu: ${cur}` : "";
    const versions = (d.versions || []).filter((v) => v.items.length);
    const days = [];
    versions.forEach((v) => {
      const [day, time] = String(v.date || "").split(" ");
      let g = days[days.length - 1];
      if (!g || g.day !== (day || "")) days.push(g = { day: day || "", list: [] });
      g.list.push({ ...v, time: time || "" });
    });
    const today = new Date();
    const dd = (x) => `${String(x.getDate()).padStart(2, "0")}.${String(x.getMonth() + 1).padStart(2, "0")}.${x.getFullYear()}`;
    const yesterday = new Date(today.getTime() - 86400000);
    const dayName = (day) => (day === dd(today) ? "Dziś" : day === dd(yesterday) ? "Wczoraj" : day.slice(0, 5));
    const most = Math.max(1, ...days.map((g) => g.list.length));
    const forms = (n) => (n === 1 ? "wersja" : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? "wersje" : "wersji");

    // the strip: oldest day on the left, bar height = versions that day
    $("cl-strip").innerHTML = days.slice().reverse().map((g) => {
      const has = g.list.some((v) => v.version === cur);
      return `<button type="button" class="sv-strip__day ${has ? "is-now" : ""}" data-day="${esc(g.day)}">
        <span class="sv-strip__n">${g.list.length}</span>
        <span class="sv-strip__bar" style="height:${Math.round(8 + 64 * g.list.length / most)}px"></span>
        <span class="sv-strip__label">${esc(dayName(g.day))}</span></button>`;
    }).join("");

    $("cl-list").innerHTML = days.map((g) => `
      <h3 class="sv-day" id="cl-day-${esc(g.day.replace(/\./g, "-"))}">${esc(dayName(g.day))}
        <span class="sv-sub">${esc(g.day)} • ${g.list.length} ${forms(g.list.length)}</span></h3>
      ${g.list.map((v) => {
        const now = v.version === cur;
        return `<div class="sv-tl ${now ? "is-now" : ""}">
          <span class="sv-tl__time">${esc(v.time)}</span>
          <span class="sv-tl__dot"></span>
          <div class="sv-tl__body">
            <div class="sv-tl__head"><span class="sv-tl__ver">${esc(v.version)}</span>${now ? `<span class="sv-tl__tag">na piecu</span>` : ""}
              ${v.tag || v.hash ? `<span class="sv-tl__ref" title="Do wpisania w GitHub → Actions → Deploy to prod">${esc(v.tag || v.hash)}</span>` : ""}</div>
            <ul class="sv-tl__items">${v.items.map((it, k) => k === 0 ? `<li class="sv-tl__title">${esc(it)}</li>` :
              `<li>${esc(it).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/:$/, "")}</li>`).join("")}</ul>
          </div></div>`;
      }).join("")}`).join("");
  }

  // ---------------------------------------------------------------- modules

  // "14:05" today, "27.10 14:05" earlier
  function stamp(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const hm = d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit", hour12: false });
    return d.toDateString() === new Date().toDateString() ? hm
      : `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")} ${hm}`;
  }

  // the module list: a row each (health, name, key or not, the last error), details on a tap
  const openedMods = new Set();
  function renderModules(d) {
    const mods = d.modules || [];
    const bad = mods.filter((m) => m.health === "ERROR");
    const hadErrors = mods.filter((m) => m.health !== "ERROR" && m.errors > 0);
    verdict($("md-verdict"), bad.length ? "bad" : hadErrors.length ? "warn" : "ok");
    $("md-verdict").textContent = bad.length ? `Błąd: ${bad.length}` : hadErrors.length ? `Były błędy: ${hadErrors.length}` : "Wszystkie działają";
    $("md-sub").textContent = `${mods.length} modułów, ${mods.filter((m) => m.critical).length} kluczowych`;
    const row = (label, value, when) => value
      ? `<div class="sv-mod__row"><span class="sv-mod__k">${label}</span><span class="sv-mod__v">${esc(value)}</span>${when ? `<span class="sv-mod__t">${when}</span>` : ""}</div>` : "";
    $("md-list").innerHTML = mods.map((m) => {
      const st = m.health === "ERROR" ? "bad" : m.errors > 0 ? "warn" : m.health ? "ok" : "off";
      const err = m.health === "ERROR" ? m.error_now : m.last_failure;
      const brief = m.health === "ERROR" ? `błąd: ${m.error_now}` : m.errors > 0 ? `ostatni błąd ${stamp(m.last_failure_at)} (${m.errors}×)` : m.health ? "OK" : "nie działa";
      const info = m.last_info, prob = m.last_problem;
      return `<details class="sv-mod sv-mod--${st}" data-id="${esc(m.id)}"${openedMods.has(m.id) ? " open" : ""}>
        <summary>
          <span class="sv-mod__dot"></span>
          <span class="sv-mod__name">${esc(m.name)}<small>${esc(m.id)}</small></span>
          ${m.critical ? '<span class="sv-mod__key">kluczowy</span>' : '<span class="sv-mod__aux">pomocniczy</span>'}
          <span class="sv-mod__brief">${esc(brief)}</span>
        </summary>
        <div class="sv-mod__body">
          <p class="sv-mod__about">${esc(m.about || "")}</p>
          ${row("Ostatni błąd", err ? `${err}${m.errors ? ` (razem ${m.errors}×)` : ""}` : "", stamp(m.last_failure_at))}
          ${row("Ostatni wpis", info ? info.message : "brak od startu", info ? stamp(info.at) : "")}
          ${row("Ostatni problem", prob ? `${prob.level}: ${prob.message}` : "brak od startu", prob ? stamp(prob.at) : "")}
          ${row("Krok modułu", m.tick_ms != null ? `${String(m.tick_ms).replace(".", ",")} ms` : "", m.last_tick_at ? `ostatnio ${stamp(m.last_tick_at)}` : "")}
        </div>
      </details>`;
    }).join("");
    $("md-list").querySelectorAll("details").forEach((el) => el.addEventListener("toggle", () => {
      if (el.open) openedMods.add(el.dataset.id); else openedMods.delete(el.dataset.id);
    }));
  }

  // ---------------------------------------------------------------- loading

  async function load(fresh = false) {
    const url = { tuning: `/api/service/tuning${fresh ? "?fresh=true" : ""}`, checks: "/api/analytics/report",
      diag: "/api/service/diagnostics", changes: "/api/service/changelog", modules: "/api/service/modules" }[tab];
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (tab === "checks" && r.status === 503) {
        // the statistics are still being counted after a start
        setStatus("Liczę statystyki, potrzebne do kontroli…");
        clearTimeout(load.retry);
        load.retry = setTimeout(() => load(), 10000);
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      const at = `Odświeżono ${new Date().toLocaleTimeString("pl-PL", { hour12: false })}`;
      if (tab === "tuning" && !data.verdict) {
        // the first analysis after a start runs in the background - the live PID is shown meanwhile
        renderLive(data.live || {}, data.settings || {});
        const p = data.progress || {};
        setStatus(`Analizuję dane z ostatnich dni${p.total ? ` (${p.done} z ${p.total})` : ""}…`);
        clearTimeout(load.retry);
        load.retry = setTimeout(() => load(), 3000);
        return;
      }
      if (tab === "tuning") renderTuning(data);
      else if (tab === "checks") renderChecks(data);
      else if (tab === "changes") renderChangelog(data);
      else if (tab === "modules") renderModules(data);
      else renderDiag(data);
      setStatus(at);
    } catch (e) {
      setStatus(`Nie udało się pobrać danych: ${e.message}`);
    }
  }

  function showTab(name) {
    tab = name;
    document.querySelectorAll("#sv-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll("#sv .st2-card").forEach((c) => c.classList.toggle("is-hidden", c.dataset.tab !== name));
    try { localStorage.setItem("service-tab", name); } catch (e) { /* no storage */ }
    load();
  }

  function startTimer() {
    clearInterval(timer);
    // the advice is rebuilt on the server every 10 min - this refreshes the live PID and the Pi state
    timer = setInterval(() => { if (!document.hidden) load(); }, 30000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("sv-tabs").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) showTab(b.dataset.tab);
    });
    $("sv-refresh").addEventListener("click", () => load(true));
    $("cl-strip").addEventListener("click", (e) => {
      const b = e.target.closest("[data-day]");
      const el = b && document.getElementById(`cl-day-${b.dataset.day.replace(/\./g, "-")}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    document.addEventListener("click", (e) => {
      const card = e.target.closest(".sv-card");
      if (!card) return;
      const open = card.classList.toggle("is-open");
      if (open) opened.add(card.dataset.key); else opened.delete(card.dataset.key);
    });
    let saved = "tuning";
    try { saved = localStorage.getItem("service-tab") || "tuning"; } catch (e) { /* no storage */ }
    showTab(["diag", "checks", "changes", "modules"].includes(saved) ? saved : "tuning");
    startTimer();
  });
})();
