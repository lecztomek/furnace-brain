// Remote read-only view (GitHub Pages). Loaded before every other script of the
// unchanged GUI: answers its /api/... requests from Firebase Realtime Database,
// where the Pi's "Podgląd zdalny" module writes the data. Every change is
// ignored (the reply carries the furnace's real values) - nothing goes back to the Pi.
// Link: https://<user>.github.io/furnace-brain/#k=<FURNACE_BRAIN_VIEWER_KEY>
(function () {
  "use strict";

  const DB = String(window.FURNACE_VIEWER_DB || "").replace(/\/+$/, "");
  const RAW_MAX_SPAN_MS = 30 * 3600 * 1000; // longer ranges use the 5 min averages
  const realFetch = window.fetch.bind(window);

  // ---------- key: from the link, remembered for the other pages ----------
  function readKey() {
    const m = /[#&]k=([^&]+)/.exec(window.location.hash || "");
    let key = m ? decodeURIComponent(m[1]) : "";
    try {
      if (key) localStorage.setItem("furnace_viewer_key", key);
      else key = localStorage.getItem("furnace_viewer_key") || "";
    } catch (e) { /* storage blocked - links keep the key below */ }
    return key;
  }
  const KEY = readKey();

  // menu links keep the key, in case storage is blocked
  document.addEventListener("DOMContentLoaded", () => {
    if (!KEY) return;
    document.querySelectorAll("a[href$='.html']").forEach((a) => {
      a.setAttribute("href", a.getAttribute("href").split("#")[0] + "#k=" + encodeURIComponent(KEY));
    });
  });

  // ---------- Firebase ----------
  const cache = new Map(); // path -> {at, promise}

  function fb(path, params, maxAgeMs) {
    const qs = new URLSearchParams(params || {}).toString();
    const url = `${DB}/furnace/${encodeURIComponent(KEY)}/${path}.json${qs ? "?" + qs : ""}`;
    const hit = cache.get(url);
    if (hit && Date.now() - hit.at < (maxAgeMs || 0)) return hit.promise;
    const promise = realFetch(url, { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new HttpError(r.status === 401 ? 403 : r.status, "Firebase: HTTP " + r.status);
      return r.json();
    });
    cache.set(url, { at: Date.now(), promise });
    promise.catch(() => cache.delete(url));
    return promise;
  }

  // values are JSON strings (see backend/modules/remote_view)
  const parse = (v) => (typeof v === "string" ? JSON.parse(v) : v);

  async function fbValue(path, maxAgeMs) {
    const v = await fb(path, null, maxAgeMs);
    if (v === null || v === undefined) throw new HttpError(503, "Brak danych z pieca");
    return parse(v);
  }

  class HttpError extends Error {
    constructor(status, detail) { super(detail); this.status = status; }
  }

  function json(body, status) {
    return new Response(JSON.stringify(body), {
      status: status || 200,
      headers: { "Content-Type": "application/json" },
    });
  }

  // ---------- history ----------
  const pad = (n) => String(n).padStart(2, "0");
  const dayStr = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const isoLocal = (d) => `${dayStr(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;

  function daysBetween(from, to) {
    const out = [];
    const d = new Date(from.getFullYear(), from.getMonth(), from.getDate());
    while (d <= to && out.length < 400) {
      out.push(dayStr(d));
      d.setDate(d.getDate() + 1);
    }
    return out;
  }

  // same algorithm as backend/core/downsample.py
  function downsample(items, maxPoints) {
    if (!maxPoints || items.length <= maxPoints) return items;
    const times = items.map((r) => new Date(r.data_czas).getTime());
    const t0 = times[0];
    const width = Math.max((times[times.length - 1] - t0) / maxPoints, 1e-9);
    const buckets = new Map();
    items.forEach((row, i) => {
      const idx = Math.min(Math.floor((times[i] - t0) / width), maxPoints - 1);
      if (!buckets.has(idx)) buckets.set(idx, []);
      buckets.get(idx).push(row);
    });
    const out = [];
    [...buckets.keys()].sort((a, b) => a - b).forEach((idx) => {
      const rows = buckets.get(idx);
      const merged = { data_czas: rows[Math.floor(rows.length / 2)].data_czas };
      Object.keys(rows[0]).forEach((key) => {
        if (key === "data_czas") return;
        const values = rows.map((r) => r[key]).filter((v) => v !== null && v !== undefined && v !== "");
        if (!values.length) { merged[key] = ""; return; }
        const nums = values.map((v) => (typeof v === "number" ? v : (/^\s*-?[\d.]+(e-?\d+)?\s*$/i.test(v) ? parseFloat(v) : NaN)));
        if (nums.every((n) => !isNaN(n))) {
          merged[key] = Math.round((nums.reduce((a, b) => a + b, 0) / nums.length) * 100) / 100;
        } else {
          const counts = new Map();
          values.forEach((v) => counts.set(v, (counts.get(v) || 0) + 1));
          merged[key] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
        }
      });
      out.push(merged);
    });
    return out;
  }

  async function historyData(q) {
    const from = new Date(q.get("from_ts"));
    const to = new Date(q.get("to_ts"));
    if (isNaN(from) || isNaN(to)) throw new HttpError(422, "from_ts / to_ts");
    const lo = isoLocal(from), hi = isoLocal(to);
    const node = to - from > RAW_MAX_SPAN_MS ? "history5m" : "history";
    const today = dayStr(new Date());

    const days = await Promise.all(daysBetween(from, to).map((day) =>
      // past days don't change - cache them for a while
      fb(`${node}/${day}`, null, day === today ? 0 : 10 * 60 * 1000).then((v) => v || {})
    ));
    let rows = [];
    days.forEach((obj) => {
      Object.keys(obj).sort().forEach((k) => {
        const r = parse(obj[k]);
        if (r && r.data_czas >= lo && r.data_czas <= hi) rows.push(r);
      });
    });

    const fields = q.getAll("fields");
    if (fields.length) {
      rows = rows.map((r) => {
        const o = { data_czas: r.data_czas };
        fields.forEach((f) => { if (f in r) o[f] = r[f]; });
        return o;
      });
    }
    const rawCount = rows.length;
    const maxPoints = parseInt(q.get("max_points") || "0", 10);
    if (maxPoints) rows = downsample(rows, maxPoints);
    return { from_ts: q.get("from_ts"), to_ts: q.get("to_ts"), count: rows.length, raw_count: rawCount, items: rows };
  }

  // ---------- logs ----------
  async function logsRecent(q) {
    const limit = Math.min(Math.max(parseInt(q.get("limit") || "100", 10) || 100, 1), 2000);
    const level = q.get("level"), source = q.get("source"), type = q.get("type");
    let days = Object.keys((await fb("logs", { shallow: "true" }, 30 * 1000)) || {}).sort().reverse();
    // with a filter only the newest 2 hours, like on the Pi (backend/api/logs_api.py)
    const filtered = !!(level || source || type);
    let cutoff = 0;
    if (filtered) days = days.slice(0, 2);
    const out = [];
    for (const day of days) {
      const obj = (await fb(`logs/${day}`, null, 15 * 1000)) || {};
      const items = Object.keys(obj).sort().reverse().map((k) => parse(obj[k]));
      if (filtered && !cutoff && items.length) cutoff = items[0].ts - 2 * 3600;
      for (const item of items) {
        if (filtered && item.ts < cutoff) return { count: out.length, items: out };
        if (level && item.level !== level) continue;
        if (source && item.source !== source) continue;
        if (type && item.type !== type) continue;
        out.push(item);
        if (out.length >= limit) return { count: out.length, items: out };
      }
    }
    return { count: out.length, items: out };
  }

  // ---------- writes: ignored, answered with the real values ----------
  async function ignoredWrite(parts) {
    try {
      if (parts[1] === "config" && parts[2] === "value" && parts.length === 5) {
        const values = await fbValue(`config/values/${parts[3]}`, 0);
        return json({ key: parts[4], value: values[parts[4]] });
      }
      if (parts[1] === "config" && parts[2] === "values" && parts.length === 4) {
        return json(await fbValue(`config/values/${parts[3]}`, 0));
      }
      if (parts[1] === "state" && parts[2] === "mode") {
        return json(await fbValue("state", 0));
      }
    } catch (e) { /* fall through */ }
    return json({ ok: true, ignored: true });
  }

  // ---------- router ----------
  async function handle(url, method) {
    const parts = url.pathname.replace(/^.*?\/api\//, "api/").split("/").filter(Boolean).map(decodeURIComponent);
    if (!DB || !KEY) {
      throw new HttpError(503, "Podgląd: brak klucza - otwórz link z #k=...");
    }
    if (method !== "GET" && method !== "HEAD") return ignoredWrite(parts);

    const p = parts.slice(1).join("/");
    if (p === "state/current") return json(await fbValue("state", 2000));
    if (p === "manual/current") return json(await fbValue("manual", 2000));
    if (p === "history/fields") return json({ fields: await fbValue("history_fields", 60 * 1000) });
    if (p === "history/data") return json(await historyData(url.searchParams));
    if (p === "logs/meta") return json(await fbValue("logs_meta", 60 * 1000));
    if (p === "logs/recent") return json(await logsRecent(url.searchParams));
    if (p === "analytics/report") return json(await fbValue("report", 60 * 1000));
    // service page and forecast: sent by the Pi every 10 min
    if (p === "analytics/forecast") return json(await fbValue("service/forecast", 60 * 1000));
    if (p === "service/tuning") return json(await fbValue("service/tuning", 60 * 1000));
    if (p === "service/diagnostics") return json(await fbValue("service/diagnostics", 60 * 1000));
    if (p === "service/changelog") return json(await fbValue("service/changelog", 60 * 1000));
    if (p === "config/modules") return json(await fbValue("config/modules", 60 * 1000));
    if (parts[1] === "config" && (parts[2] === "schema" || parts[2] === "values") && parts.length === 4) {
      const node = parts[2] === "schema" ? "schemas" : "values";
      return json(await fbValue(`config/${node}/${parts[3]}`, 10 * 1000));
    }
    if (parts[1] === "config" && parts[2] === "value" && parts.length === 5) {
      const values = await fbValue(`config/values/${parts[3]}`, 10 * 1000);
      if (!(parts[4] in values)) throw new HttpError(404, `Unknown key '${parts[4]}'`);
      return json({ key: parts[4], value: values[parts[4]] });
    }
    throw new HttpError(404, "Niedostępne w podglądzie");
  }

  window.fetch = async function (input, init) {
    const raw = typeof input === "string" ? input : (input && input.url) || String(input);
    const url = new URL(raw, window.location.href);
    if (url.origin !== window.location.origin || !/\/api\//.test(url.pathname)) {
      return realFetch(input, init);
    }
    const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
    try {
      return await handle(url, method);
    } catch (e) {
      return json({ detail: e.message || String(e) }, e.status || 502);
    }
  };
})();
