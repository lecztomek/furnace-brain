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
    "Grzejniki": ["radiator", "Grzejniki"],
    "Ustawienia": ["wrench", "Ustawienia"],
    "Zdarzenia": ["alert", "Zdarzenia"],
  };
  const WORD = { ok: "OK", warn: "Zmień", bad: "Pilne", info: "Sprawdź" };
  const PID_AREAS = ["PID", "Całka (Ki)", "Wzmocnienie (Kp)", "Ślimak", "Dmuchawa"];

  function bigTile(icon, label, val, status, sub) {
    return `<div class="sv-tile sv-tile--${status}"><span class="sv-tile__icon">${FBIcon(icon)}</span>
      <span class="sv-tile__label">${label}</span><span class="sv-tile__val">${val}</span>${sub ? `<span class="sv-tile__sub">${sub}</span>` : ""}</div>`;
  }

  function renderTuning(r) {
    verdict($("tn-verdict"), r.verdict.status, r.verdict.text);
    const reg = r.regulation;
    if (reg && reg.hours >= 1) {
      const st = (good, okish) => (good ? "ok" : okish ? "warn" : "bad");
      $("tn-period").textContent = `Liczone z: ostatnich ${num(reg.hours, 0)} h spokojnej pracy (od ${when(r.regulation_since)}, bez 2 h po rozpaleniu)`;
      $("tn-hero").innerHTML = [
        bigTile("target", "Od zadanej", `${signed(reg.mean_err)}°`, st(Math.abs(reg.mean_err) <= 1, Math.abs(reg.mean_err) <= 2)),
        bigTile("activity", "Kołysanie", `±${num(reg.std)}°`, st(reg.std <= 1, reg.std <= 2)),
        bigTile("gauge", "Moc średnio", `${reg.power_avg ?? "--"}%`, st(reg.power_avg >= 35 && reg.power_avg <= 75, true)),
      ].join("");
    } else {
      $("tn-period").textContent = "Liczone z: ostatnich 7 dni, praca od 2 h po rozpaleniu";
      $("tn-hero").innerHTML = `<div class="sv-empty">${FBIcon("hourglass", "ic--inline")} Za mało spokojnej pracy do oceny</div>`;
    }

    // the PID parts always, the rest only when something is wrong
    const items = r.items.filter((i) => PID_AREAS.includes(i.area) || i.status === "warn" || i.status === "bad");
    $("tn-items").innerHTML = items.map(adviceCard).join("");
    renderChanges(r.changes || []);
    renderLive(r.live || {}, r.settings || {});
  }

  // "Wydłuż podanie ślimaka (8 → 9 s)." -> action "Wydłuż podanie ślimaka", change "8 → 9 s"
  function splitTip(tip) {
    const m = /^(.*?)\s*\(([^()]*→[^()]*)\)/.exec(tip || "");
    return m ? { action: m[1], change: m[2] } : { action: (tip || "").replace(/\.$/, ""), change: "" };
  }

  function cardClass(status, key) {
    return `sv-card sv-card--${status}${opened.has(key) ? " is-open" : ""}`;
  }

  function adviceCard(i) {
    const [icon, name] = AREA[i.area] || ["info", i.area];
    const t = splitTip(i.tip);
    return `<button type="button" class="${cardClass(i.status, i.area + i.title)}" data-key="${esc(i.area + i.title)}">
      <span class="sv-card__top"><span class="sv-card__icon">${FBIcon(icon)}</span><span class="sv-card__name">${esc(name)}</span>
        <span class="sv-card__word">${WORD[i.status] || ""}</span></span>
      <span class="sv-card__title">${esc(i.title)}</span>
      ${t.change ? `<span class="sv-card__change">${esc(t.change)}</span>` : ""}
      ${i.tip && (t.change || i.status === "warn" || i.status === "bad") ? `<span class="sv-card__action">${esc(t.action)}</span>` : ""}
      <span class="sv-card__more">${esc(i.detail)}${i.tip && !t.change && i.status === "info" ? `<br>${esc(i.tip)}` : ""}</span>
    </button>`;
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

  const TILE_ICON = { "Procesor": "cpu", "Zasilanie": "plug", "Wolne miejsce": "drive", "Sterownik działa": "clock", "Internet": "globe", "Czujniki": "sensor" };

  function renderDiag(d) {
    verdict($("dg-verdict"), d.status);
    $("dg-tiles").innerHTML = (d.tiles || []).map((t) => bigTile(TILE_ICON[t.label] || "info", t.label, esc(t.value), t.status)).join("");
    const problems = d.groups.flatMap((g) => g.items).filter((i) => i.status === "warn" || i.status === "bad");
    $("dg-problems").innerHTML = problems.map((i) => `<button type="button" class="${cardClass(i.status, i.label)}" data-key="${esc(i.label)}">
      <span class="sv-card__top"><span class="sv-card__icon">${FBIcon(i.status === "bad" ? "stop" : "alert")}</span><span class="sv-card__name">${esc(i.label)}</span></span>
      <span class="sv-card__title">${esc(i.value)}</span>
      ${i.note ? `<span class="sv-card__more">${esc(i.note)}</span>` : ""}</button>`).join("");
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
    }).join("")}</div>` + list.filter((c) => c.status === "warn" || c.status === "bad").map((c) =>
      `<div class="sv-card sv-card--${c.status} is-open ck-problem"><span class="sv-card__top"><span class="sv-card__icon">${FBIcon(c.status === "bad" ? "stop" : "alert")}</span>` +
      `<span class="sv-card__name">${esc(c.title)}</span></span><span class="sv-card__more">${esc(c.detail)}</span></div>`).join("");
  }

  // ---------------------------------------------------------------- loading

  async function load(fresh = false) {
    const url = { tuning: `/api/service/tuning${fresh ? "?fresh=true" : ""}`, checks: "/api/analytics/report",
      diag: "/api/service/diagnostics" }[tab];
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
      if (tab === "tuning") renderTuning(data); else if (tab === "checks") renderChecks(data); else renderDiag(data);
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
    // the advice is rebuilt on the server every 30 min - this refreshes the live PID and the Pi state
    timer = setInterval(() => { if (!document.hidden) load(); }, 30000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("sv-tabs").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) showTab(b.dataset.tab);
    });
    $("sv-refresh").addEventListener("click", () => load(true));
    document.addEventListener("click", (e) => {
      const card = e.target.closest(".sv-card");
      if (!card) return;
      const open = card.classList.toggle("is-open");
      if (open) opened.add(card.dataset.key); else opened.delete(card.dataset.key);
    });
    let saved = "tuning";
    try { saved = localStorage.getItem("service-tab") || "tuning"; } catch (e) { /* no storage */ }
    showTab(["diag", "checks"].includes(saved) ? saved : "tuning");
    startTimer();
  });
})();
