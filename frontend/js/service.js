// js/service.js - service page: tuning advice (PID, feeder, blower) and the Raspberry Pi diagnostics
(function () {
  const $ = (id) => document.getElementById(id);
  const ICON = { ok: "✓", warn: "!", bad: "✕", info: "i" };
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

  function tile(label, val, unit, cls = "") {
    return `<div class="st2-tile ${cls}"><span class="st2-label">${label}</span><span class="st2-value">${val}${unit ? `<small>${unit}</small>` : ""}</span></div>`;
  }

  // ---------------------------------------------------------------- tuning

  function renderTuning(r) {
    verdict($("tn-verdict"), r.verdict.status, r.verdict.text);
    const reg = r.regulation;
    if (reg && reg.hours >= 1) {
      const tone = (good, okish) => (good ? "st2-tile--good" : okish ? "" : "st2-tile--bad");
      $("tn-hero").innerHTML = [
        tile("Od zadanej średnio", signed(reg.mean_err), "°C", tone(Math.abs(reg.mean_err) <= 1, Math.abs(reg.mean_err) <= 2)),
        tile("Kołysanie", `±${num(reg.std)}`, "°C", tone(reg.std <= 1, reg.std <= 2)),
        tile("W ±2°C", reg.within_2, "%", tone(reg.within_2 >= 80, reg.within_2 >= 60)),
        tile("Moc średnio", reg.power_avg == null ? "--" : reg.power_avg, "%", tone(reg.power_avg >= 35 && reg.power_avg <= 75, true)),
      ].join("");
      $("tn-hero-note").textContent = `Ostatnie ${num(reg.hours)} h ustabilizowanej pracy (od ${when(r.regulation_since)}). `
        + `Na maksimum ${reg.at_max_pct}% czasu, na minimum ${reg.at_min_pct}%.`
        + (reg.period_min ? ` Jedno wahnięcie co ok. ${reg.period_min} min.` : "");
    } else {
      $("tn-hero").innerHTML = "";
      $("tn-hero-note").textContent = "Za mało ustabilizowanej pracy (liczonej od 2 h po rozpaleniu) do oceny.";
    }

    $("tn-items").innerHTML = r.items.map((i) => `
      <li class="sv-item sv-item--${i.status}">
        <span class="st2-check__icon">${ICON[i.status] || "?"}</span>
        <div class="sv-item__body">
          <div class="sv-item__head"><span class="sv-area">${esc(i.area)}</span><b>${esc(i.title)}</b></div>
          <div class="sv-item__detail">${esc(i.detail)}</div>
          ${i.tip ? `<div class="sv-item__tip">→ ${esc(i.tip)}</div>` : ""}
        </div>
      </li>`).join("");

    renderChanges(r.changes || []);
    renderLive(r.live || {}, r.settings || {});
  }

  function renderChanges(list) {
    if (!list.length) {
      $("tn-changes").innerHTML = `<div class="st2-empty">Brak zmian Kp, Ki, ślimaka, dmuchawy ani zadanej w ostatnich 7 dniach.</div>`;
      return;
    }
    const side = (s) => (s ? `±2°C: <b>${s.within_2}%</b>, kołysanie ±${num(s.std)}°C, moc ${s.power_avg ?? "--"}%` : `<span class="st2-muted">za mało danych</span>`);
    const badge = { better: ["lepiej", "ok"], worse: ["gorzej", "bad"], same: ["bez różnicy", "info"] };
    $("tn-changes").innerHTML = list.map((c) => {
      const b = badge[c.verdict];
      return `<div class="sv-change">
        <div class="sv-change__head"><span class="st2-muted">${when(c.at)}</span> <b>${esc(c.label)}</b>: ${value(c.old)} → <b>${value(c.new)}</b>
          ${b ? `<span class="sv-badge sv-badge--${b[1]}">${b[0]}</span>` : `<span class="sv-badge sv-badge--info">czekam na dane</span>`}</div>
        <div class="sv-change__cmp"><span>przed: ${side(c.before)}</span><span>po: ${side(c.after)}</span></div>
      </div>`;
    }).join("");
  }

  // the PID chain now: deviation -> P + I -> power -> feeder rhythm and blower
  function renderLive(l, s) {
    const el = $("tn-live");
    if (l.mode !== "WORK" || !l.pid || l.pid.error == null) {
      const names = { IGNITION: "rozpalanie", OFF: "wyłączony", MANUAL: "ręczny" };
      el.innerHTML = `<div class="st2-empty">PID pracuje tylko w trybie PRACA (teraz: ${names[l.mode] || l.mode || "--"}).</div>`;
      $("tn-live-note").textContent = "";
      return;
    }
    const p = l.pid;
    const err = p.error;
    const rate = p.rate || 0;
    const trend = Math.abs(rate) < 0.05 ? "stoi" : rate > 0 ? `rośnie ${num(rate, 2)}°C/min` : `spada ${num(-rate, 2)}°C/min`;
    let integral = err > 0.2 ? "rośnie (za zimno)" : err < -0.2 ? "maleje (za ciepło)" : "stoi (w zadanej)";
    if (p.integral_frozen) integral = "wstrzymana - temperatura już idzie do zadanej";
    const pw = s.power_work || {};
    const feed = l.feeder;
    const step = (label, val, sub) => `<div class="sv-step"><span class="st2-label">${label}</span><span class="sv-step__val">${val}</span><span class="sv-step__sub">${sub || ""}</span></div>`;
    el.innerHTML = [
      step("Kocioł", `${num(l.boiler)}°C`, `zadana ${num(l.setpoint, 0)}°C, ${trend}`),
      step("Błąd", `${signed(err)}°C`, err > 0 ? "za zimno" : err < 0 ? "za ciepło" : ""),
      step("P (Kp × błąd)", signed(p.p, 0), `Kp ${value(pw.kp)}`),
      step("I (całka)", num(p.i, 0), integral),
      step("Moc", `${num(l.power, 0)}%`, p.overtemp ? "obniżona - kocioł za gorący" : `zakres ${value(pw.min_power)}-${value(pw.max_power)}%`),
      step("Ślimak", feed ? `${num(feed.on_s, 0)} s` : "stoi", feed ? `co ${num(feed.on_s + feed.off_s, 0)} s (przerwa ${num(feed.off_s, 0)} s)` : "moc poniżej progu"),
      step("Dmuchawa", `${value(l.fan_percent)}%`, l.fan_duty_pct != null ? `przez ${num(l.fan_duty_pct, 0)}% cyklu ${value(l.fan_cycle_s)} s` : "stoi"),
    ].join('<span class="sv-arrow">→</span>');
    $("tn-live-note").textContent = "Moc = P + I (obcięta do zakresu i do szybkości zmian). Moc ustala, jak często idzie ślimak i jak długo dmucha dmuchawa.";
  }

  // ---------------------------------------------------------------- diagnostics

  function renderDiag(d) {
    verdict($("dg-verdict"), d.status);
    $("dg-groups").innerHTML = d.groups.filter((g) => g.items.length).map((g) => `
      <h3 class="st2-subhead">${esc(g.title)}</h3>
      <ul class="st2-checks">${g.items.map((i) => `
        <li class="st2-check st2-check--${i.status}">
          <span class="st2-check__icon">${ICON[i.status] || "?"}</span>
          <span class="st2-check__title">${esc(i.label)}</span>
          <span class="st2-check__detail">${esc(i.value)}${i.note ? `<span class="sv-note">${esc(i.note)}</span>` : ""}</span>
        </li>`).join("")}</ul>`).join("");
  }

  // ---------------------------------------------------------------- loading

  async function load(fresh = false) {
    const url = tab === "tuning" ? `/api/service/tuning${fresh ? "?fresh=true" : ""}` : "/api/service/diagnostics";
    try {
      const r = await fetch(url, { cache: "no-store" });
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
      if (tab === "tuning") renderTuning(data); else renderDiag(data);
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
    let saved = "tuning";
    try { saved = localStorage.getItem("service-tab") || "tuning"; } catch (e) { /* no storage */ }
    showTab(saved === "diag" ? "diag" : "tuning");
    startTimer();
  });
})();
