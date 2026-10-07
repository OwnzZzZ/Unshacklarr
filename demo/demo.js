/* Unshacklarr's demo: the page as it is, with this file standing in for the server.
   Everything comes from data.json (recorded by record.py against a made-up Sonarr and Unshackle), its dates moved
   to today; what you change lives in this tab only. Nothing reaches Sonarr, Unshackle or any service. */
(() => {
  "use strict";
  const DAY = 86400000;
  try { if (!localStorage.getItem("lang")) localStorage.setItem("lang", "en"); } catch {}  // English, unless picked
  const KEY = "unshacklarr-demo";
  const load = () => { try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch { return {}; } };
  const mem = { configured: false, logged: false, ...load() };
  const keep = () => { try { sessionStorage.setItem(KEY, JSON.stringify(mem)); } catch {} };

  /* ---- The recorded answers, moved to today ---- */
  const raw = new XMLHttpRequest();
  raw.open("GET", "data.json", false);  // before the page's own script runs: it asks at once
  raw.setRequestHeader("Cache-Control", "no-cache");  // a new recording shows at once
  raw.send();
  const data = JSON.parse(raw.responseText);
  const shiftMs = Math.round((Date.now() - Date.parse(data.recorded)) / DAY) * DAY;  // whole days: times of day stay
  const ISO = /^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?$/;
  const EPOCH_KEYS = new Set(["expires", "updated", "since", "created"]);
  function moved(v, key) {
    if (typeof v === "string" && ISO.test(v)) {
      if (v.length === 10) return new Date(Date.parse(v + "T12:00:00Z") + shiftMs).toISOString().slice(0, 10);
      const zone = v.match(/(Z|[+-]\d{2}:\d{2})$/)?.[1] || "";
      const out = new Date(Date.parse(v) + shiftMs).toISOString();
      return zone === "Z" ? out.replace(/\.\d+Z$/, "Z") : out.replace(/Z$/, "+00:00");
    }
    if (typeof v === "number" && EPOCH_KEYS.has(key) && v > 1e9 && v < 1e10) return v + shiftMs / 1000;
    if (Array.isArray(v)) return v.map((x) => moved(x));
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, moved(x, k)]));
    return v;
  }
  const R = moved(data.routes);
  const copy = (x) => structuredClone(x);
  const now = () => new Date().toISOString();

  /* ---- What this tab changed: the config, the bell, the downloads started here ---- */
  const config = () => mem.config || R["/api/state"].config;
  const inbox = () => (mem.inbox ||= copy(R["/api/inbox"]));
  function notify(level, title, message) {
    const box = inbox();
    box.items.unshift({ id: Math.random().toString(36).slice(2, 10), at: now(), level, title, message });
    keep();
  }

  // Episodes by Sonarr id, for a download picked anywhere
  const series = R["/api/state"].series;
  const episodes = new Map();
  for (const s of series) for (const e of R[`/api/series/${s.id}/episodes`] || []) episodes.set(e.id, { ...e, serie: s });

  const TRACKS = ["Video 1080p H.264", "Audio English 5.1", "Subtitles English", "Subtitles French", "Subtitles Spanish"];
  const fresh = !mem.sim;
  const sim = (mem.sim ||= []);  // downloads going on or done in this tab, through a reload: {card, speed, paused}
  const recordedLive = data.live;
  function startCard(card) {
    Object.assign(card, { waiting: undefined, id: card.id.startsWith("waiting-") ? runId(card) : card.id, started: now(),
      step: "downloading", outcome: "running", live: { status: "downloading", progress: 0 }, tracks: [] });
    delete card.waiting;
  }
  const runId = (c) => `${new Date().toISOString().replace(/[-:T]/g, "").slice(0, 15).replace(/^(\d{8})/, "$1-")}-${String(Math.random()).slice(2, 8)}-${c.tvdbId}-${c.sxxeyy}`;
  function queue(ids, batch) {
    const cards = [];
    for (const id of ids) {
      const e = episodes.get(id);
      if (!e) continue;
      const sxxeyy = `S${String(e.seasonNumber).padStart(2, "0")}E${String(e.episodeNumber).padStart(2, "0")}`;
      if (sim.some((x) => x.card.outcome === "running" && x.card.episodeId === id)) continue;
      const service = config().series[e.serie.tvdbId]?.service || "DSNP";
      cards.push({ id: `waiting-${id}`, series: e.serie.title, tvdbId: e.serie.tvdbId, sxxeyy, service, kind: "manual",
        started: now(), ended: null, outcome: "running", episodeId: id, step: "queued", live: {}, tracks: [], waiting: true,
        serviceEpisode: sxxeyy, attempts: 1, checks: 1, cdm: "samsung_sm-a536b_l3", proxy: null,
        setup: { via: "unshackle", proxy: null, command: `unshackle dl -w ${sxxeyy} --quality 1080 ${service} ${config().series[e.serie.tvdbId]?.title || ""}` },
        ...(batch ? { batch } : {}) });
    }
    sim.push(...cards.map((card) => ({ card, speed: 1.2 + Math.random() * 1.5 })));
    return cards.length;
  }
  // The recorded download going on: it moves here, from a third of the way
  const recorded = (R["/api/runs"] || []).find((c) => c.id === recordedLive);
  if (recorded && fresh) sim.push({ card: { ...recorded, started: new Date(Date.now() - 100000).toISOString(), outcome: "running", live: { status: "downloading", progress: 35 }, tracks: [] }, speed: 1 });

  function tick() {
    // each job downloads one episode at a time; a job just started doesn't wait for the others
    const jobs = new Set(sim.map((x) => x.card.batch || x.card.id));
    for (const job of jobs) {
      const own = sim.filter((x) => (x.card.batch || x.card.id) === job && !x.paused);
      if (!own.some((x) => x.card.outcome === "running" && !x.card.waiting)) {
        const next = own.find((x) => x.card.waiting && x.card.outcome === "running");
        if (next) startCard(next.card);
      }
    }
    const running = sim.filter((x) => x.card.outcome === "running" && !x.card.waiting && !x.paused);
    for (const x of running) {
      const c = x.card, p = Math.min(100, (c.live.progress || 0) + x.speed * (0.6 + Math.random()));
      const done = Math.floor(p / (100 / TRACKS.length));
      c.live = { status: "downloading", progress: Math.round(p * 10) / 10, speed: `${(8 + Math.random() * 9).toFixed(1)} MB/s`,
        completed_tracks: Math.min(done, TRACKS.length), total_tracks: TRACKS.length, phase: p < 95 ? "downloading" : "decrypting" };
      c.tracks = TRACKS.slice(0, Math.min(TRACKS.length, done + 1)).map((label, i) => ({ label, progress: i < done ? 100 : Math.round((p % 20) * 5),
        ...(i < done ? { took: 20 + i * 7 } : {}) }));
      if (p >= 100) {
        Object.assign(c, { outcome: "downloaded", step: "done", ended: now(), size: 1_400_000_000 + Math.round(Math.random() * 3e8),
          tracks: TRACKS.map((label, i) => ({ label, progress: 100, took: 20 + i * 7 })) });
        notify("success", `Downloaded: ${c.series} ${c.sxxeyy}`, "Imported by Sonarr (in the demo: nothing was downloaded).");
      }
    }
  }
  setInterval(() => { tick(); if (sim.length) keep(); }, 700);

  const runs = () => {
    const mine = sim.map((x) => x.card);
    const ids = new Set(mine.map((c) => c.id));
    const waiting = mine.filter((c) => c.waiting);
    const rest = mine.filter((c) => !c.waiting).sort((a, b) => b.started.localeCompare(a.started));
    return copy([...waiting, ...rest, ...(mem.cleared ? [] : R["/api/runs"].filter((c) => !ids.has(c.id)))]);
  };
  const liveCards = () => copy(sim.map((x) => x.card).filter((c) => c.outcome === "running" && !c.waiting));

  /* ---- The routes ---- */
  class Refused extends Error { constructor(status, text) { super(text); this.status = status; } }
  const NOT_HERE = "Not in the demo: this would change real files of Unshackle.";
  const nextSync = () => { const d = new Date(); d.setMinutes(0, 0, 0); d.setHours(d.getHours() + 2 - (d.getHours() % 2)); return d.toISOString(); };

  function route(method, path, body) {
    const url = new URL(path, location.href), p = url.pathname.replace(/^.*?\/api\//, "/api/");
    let m;
    if (p === "/api/session") {
      if (!mem.configured) return { configured: false, logged_in: false, setup: { country: "US", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, unshackle_mode: "remote" } };
      return mem.logged ? { ...R["/api/session"], configured: true, logged_in: true, expires: Math.floor(Date.now() / 1000) + 30 * 86400 } : { configured: true, logged_in: false };
    }
    if (p === "/api/login") { mem.logged = true; keep(); return { logged_in: true }; }
    if (p === "/api/setup/found") {
      if (String(body?.setup_code || "").trim().toUpperCase() !== "DEMO") throw new Refused(403, "Wrong setup code: in the demo, it is DEMO");
      return { sonarr_url: "http://sonarr:8989", sonarr_downloads: "/downloads", unshackle_url: "http://unshackle:8786", unshackle_command: "",
        downloads: "/downloads", unshackle_downloads: "", sonarr_api_key_set: true, unshackle_api_key_set: true };
    }
    if (p === "/api/unshackle/test") return { services: 4 };
    if (p === "/api/sonarr/test") return { version: "4.0.15.2941" };
    if (p === "/api/setup") {
      mem.configured = mem.logged = true;
      mem.config = copy(R["/api/state"].config);
      Object.assign(mem.config.settings, { country: body.country || "US", timezone: body.timezone || "UTC", language: body.language || "en" });
      keep();
      return { configured: true };
    }
    // The setup from a backup: two made-up ones, restored in words (nothing is written)
    if (p === "/api/setup/backups") {
      if (String(body?.setup_code || "").trim().toUpperCase() !== "DEMO") throw new Refused(403, "Wrong setup code: in the demo, it is DEMO");
      return { saved: [{ name: "unshacklarr-2026-10-06-0300.yaml", at: new Date(Date.now() - 864e5).toISOString(), size: 7300 },
        { name: "unshacklarr-2026-10-05-0300.yaml", at: new Date(Date.now() - 2 * 864e5).toISOString(), size: 7100 }] };
    }
    if (p === "/api/setup/restore") {
      mem.configured = mem.logged = true;
      mem.config = copy(R["/api/state"].config);
      keep();
      return { logged_in: true, series: Object.keys(mem.config.series || {}).length, sonarr: { ok: true }, unshackle: { ok: false, error: "Not in the demo: there is no Unshackle to test" } };
    }
    if (!mem.configured || !mem.logged) throw new Refused(401, "Log in first");
    if (p === "/api/logout") { mem.logged = false; keep(); return { logged_in: false }; }
    // Backups: two made-up ones, the setup restores from them in words (nothing is written)
    const demoBackups = [{ name: "unshacklarr-2026-10-06-0300.yaml", at: new Date(Date.now() - 864e5).toISOString(), size: 7300 },
      { name: "unshacklarr-2026-10-05-0300.yaml", at: new Date(Date.now() - 2 * 864e5).toISOString(), size: 7100 }];
    if (p === "/api/state") return { ...copy(R["/api/state"]), config: copy(config()), backups: { count: 2, folder: "/data/backups", latest: demoBackups[0].at, saved: demoBackups } };
    if (p === "/api/config") { mem.config = body; keep(); return { saved: true }; }
    if (p === "/api/hidden") {
      const c = config(), hidden = new Set(c.hidden_series || []);
      body.hidden ? hidden.add(body.tvdbId) : hidden.delete(body.tvdbId);
      c.hidden_series = [...hidden]; mem.config = c; keep();
      return c.hidden_series;
    }
    if (p === "/api/schedule") {
      const s = copy(R["/api/schedule"]);
      s.nextSync = nextSync();
      return s;
    }
    if (p === "/api/calendar") {
      const from = new Date(`${url.searchParams.get("start")}T00:00:00`), days = +url.searchParams.get("days") || 14;
      const to = new Date(from.getTime() + days * DAY);
      return copy(R["/api/calendar"].filter((e) => e.airDateUtc && new Date(e.airDateUtc) >= from && new Date(e.airDateUtc) < to));
    }
    if (p === "/api/runs") return runs();
    if (p === "/api/runs/clear") { mem.cleared = true; keep(); return { deleted: R["/api/runs"].length }; }
    if (p === "/api/busy") return { running: liveCards().length, queued: sim.filter((x) => x.card.waiting).length };
    if (p === "/api/download") {
      const batch = body.batch || (body.episodeIds.length > 1 ? `${new Date().toISOString().replace(/[-:T]/g, "").slice(0, 15).replace(/^(\d{8})/, "$1-")}-${Math.random().toString(16).slice(2, 8)}` : null);
      if (!queue(body.episodeIds, batch)) throw new Refused(409, "Already downloading");
      return { running: true };
    }
    if (p === "/api/sync") { notify("info", "Sync done", "Nothing new to download (in the demo, the sync looks at nothing)."); return { started: true }; }
    if ((m = p.match(/^\/api\/runs\/([^/]+)\/stop$/)) || (m = p.match(/^\/api\/jobs\/([^/]+)\/stop$/))) {
      for (const x of sim) if ((x.card.id === m[1] || x.card.batch === m[1]) && x.card.outcome === "running")
        Object.assign(x.card, { outcome: x.card.waiting ? "cancelled" : "stopped", ended: now(), waiting: undefined });
      return { stopped: true };
    }
    if ((m = p.match(/^\/api\/jobs\/([^/]+)\/(pause|resume)$/))) {
      for (const x of sim) if (x.card.batch === m[1]) x.paused = m[2] === "pause";
      return { paused: m[2] === "pause" };
    }
    if ((m = p.match(/^\/api\/queue\/(\d+)\/(remove|add)$/))) {
      const x = sim.find((y) => y.card.episodeId === +m[1] && y.card.waiting);
      if (x && m[2] === "remove") Object.assign(x.card, { outcome: "cancelled", ended: now() });
      return { done: true };
    }
    if ((m = p.match(/^\/api\/runs\/([^/]+)$/)) && method === "DELETE") return { deleted: 1 };
    if (p === "/api/inbox") { const b = inbox(); return { ...copy(b), unread: b.items.filter((i) => i.at > (b.read_at || "")).length }; }
    if (p === "/api/inbox/read") { inbox().read_at = now(); keep(); return route("GET", "/api/inbox"); }
    if (p === "/api/inbox/clear") { inbox().items = []; keep(); return route("GET", "/api/inbox"); }
    if (p === "/api/probe") {
      const hit = R[`POST /api/probe ${body.tvdbId}`];
      if (!hit) throw new Refused(502, "In the demo, only the series already set up can be looked up on their service.");
      return { ...copy(hit), checked: now() };
    }
    if (p.startsWith("/api/profiles/")) return { profiles: ["alt", "default", "family"] };  // a made-up account or three
    if (p === "/api/series/search") return { results: [  // what a service's search gives: the series, and others like it
      { id: "demo-1", title: body.query, label: "SERIES", description: "The one you look for.", url: `https://example.com/series/${encodeURIComponent(body.query.toLowerCase().replace(/\s+/g, "-"))}` },
      { id: "demo-2", title: `${body.query}: the documentary`, label: "MOVIE", description: "Another title with the same name.", url: "https://example.com/movie/demo-2" }] };
    if (p === "/api/status") return copy(R[url.searchParams.get("full") ? "/api/status?full=1" : "/api/status"]);
    if (p.startsWith("/api/cdm") && method === "POST") {
      if (p === "/api/cdm/test") return { ok: true, revoked: null, error: null, keys: 1, at: now() };
      throw new Refused(400, NOT_HERE);
    }
    if (p.startsWith("/api/cookies") && method === "POST") throw new Refused(400, NOT_HERE);
    if (p === "/api/unshackle/config-file/save") throw new Refused(400, "Not in the demo: unshackle.yaml stays as it is.");
    if (p === "/api/unshackle/config-file/version") return copy(R["/api/unshackle/config-file/open"]);
    if (p === "/api/unshackle/restart" || p.startsWith("/api/unshackle/maintenance") || /\/cancel$/.test(p)) return copy(R["/api/status?full=1"].unshackle);
    if (p === "/api/unshackle/log") return { lines: ["The demo has no Unshackle: its log is empty."] };
    if (p === "/api/leftovers/import" || p === "/api/leftovers/delete") return { done: true };
    if (p === "/api/notifications/test") return { sent: [{ app: "Discord", ok: true }] };
    if (p === "/api/push/test") throw new Refused(502, "Not in the demo: there is no server to send it.");
    if (p.startsWith("/api/push/")) return { devices: 0, key: R["/api/push/key"]?.key };
    if (p === "/api/api-key/new") return { key: "demo-" + Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join(""), created: now() };
    if (p.startsWith("/api/suggest")) return { links: [], checked: now() };
    if (p.startsWith("/api/networks")) return {};
    if (p === "/api/backups/download") return { name: body.name, text: "# A made-up backup: this is the demo\n" };
    if (method !== "GET") return {};  // the password, the language, logging out others: done, in words
    if (path in R) return copy(R[path]);
    if (p in R) return copy(R[p]);
    throw new Refused(404, "Not in the demo");
  }

  /* ---- fetch, EventSource and WebSocket, answered here ---- */
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, opts = {}) => {
    const path = typeof input === "string" ? input : input.url;
    if (!/^\/api\//.test(path) && !/\/api\//.test(new URL(path, location.href).pathname)) {
      return realFetch(path.startsWith("/") ? path.slice(1) : input, opts);  // the page's own files, next to it
    }
    await new Promise((r) => setTimeout(r, 120 + Math.random() * 200));  // a server's pace
    try {
      const body = opts.body ? JSON.parse(opts.body) : null;
      return new Response(JSON.stringify(route((opts.method || "GET").toUpperCase(), path, body)), { headers: { "Content-Type": "application/json" } });
    } catch (e) {
      if (!(e instanceof Refused)) console.error("demo:", e);
      return new Response(e.message, { status: e.status || 500 });
    }
  };

  window.EventSource = class {
    constructor() {
      this.timer = setInterval(() => this.onmessage?.({ data: JSON.stringify(liveCards()) }), 700);
    }
    close() { clearInterval(this.timer); }
  };

  const RealSocket = window.WebSocket;
  const enc = new TextEncoder();
  function logOf(c) {
    const when = new Date(c.started).toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }).replace(",", "");
    const ended = sim.some((x) => x.card.id === c.id && x.card.outcome === "downloaded")  // done here: its end too
      ? `  \x1b[32m${"█".repeat(20)}\x1b[0m 100.0%  ${TRACKS.length}/${TRACKS.length} tracks\r\n\x1b[36mcompleted\x1b[0m\r\n\x1b[32m✓ Imported by Sonarr (in the demo: nothing was)\x1b[0m\r\n` : "";
    if (data.logs[c.id]) return data.logs[c.id].replace(/^(\x1b\[90m── ).*?( · )/, `$1${when}$2`) + ended;  // the day it is moved to
    return `\x1b[90m── ${when} · ${c.kind} ──\x1b[0m\r\n\x1b[36m▸\x1b[0m ${c.series} ${c.sxxeyy} on ${c.service}\r\n`
      + `\x1b[33m  This is the demo: nothing is downloaded, the bars below are made up.\x1b[0m\r\n\x1b[36mdownloading\x1b[0m ${c.series} ${c.sxxeyy}\r\n` + ended;
  }
  window.WebSocket = class {
    constructor(url) {
      if (!url.includes("/api/terminal")) return new RealSocket(url);
      const q = new URL(url).searchParams, run = q.get("run"), batch = q.get("batch");
      this.readyState = 1;
      setTimeout(() => {
        const cards = runs().filter((c) => (run && c.id === run) || (batch && c.batch === batch)).reverse();
        for (const c of cards) this.send_(logOf(c));
        const live = cards.find((c) => c.outcome === "running" && !c.waiting);
        if (!live) return this.close();
        this.timer = setInterval(() => {
          const p = live.live?.progress || 0;
          if (live.outcome !== "running") {
            this.send_(`\r\x1b[2K\x1b[36mcompleted\x1b[0m\r\n\x1b[32m✓ Imported by Sonarr (in the demo: nothing was)\x1b[0m\r\n`);
            return this.close();
          }
          this.send_(`\r\x1b[2K  \x1b[32m${"█".repeat(Math.floor(p / 5)).padEnd(20)}\x1b[0m ${p.toFixed(1).padStart(5)}%  ${live.live.completed_tracks || 0}/${TRACKS.length} tracks  ${live.live.speed || ""}`);
        }, 700);
      }, 50);
    }
    send_(text) { this.onmessage?.({ data: enc.encode(text).buffer }); }
    send() {}
    close() { clearInterval(this.timer); if (this.readyState !== 3) { this.readyState = 3; this.onclose?.({}); } }
  };

  try { delete window.PushManager; } catch {}  // no server to send them: the page says this device can't
  if (navigator.serviceWorker) navigator.serviceWorker.register = () => Promise.reject(new Error("no service worker in the demo"));

  /* ---- Links: TVDB and TMDB searched by title (the ids are made up), Sonarr and the services are nowhere ---- */
  document.addEventListener("click", (e) => {
    const a = e.target.closest?.("a[href]");
    if (!a) return;
    const url = new URL(a.href, location.href);
    const id = +(url.pathname.match(/\/(\d{6})\b/)?.[1] || url.searchParams.get("id") || 0);
    const serie = series.find((s) => s.tvdbId === id || s.tmdbId === id);
    if (/thetvdb\.com$/.test(url.hostname) && serie) a.href = `https://thetvdb.com/search?query=${encodeURIComponent(serie.title)}`;
    else if (/themoviedb\.org$/.test(url.hostname) && serie) a.href = `https://www.themoviedb.org/search/tv?query=${encodeURIComponent(serie.title)}`;
    else if (/^(sonarr|unshackle)$/.test(url.hostname) || Object.values(config().series).some((s) => s.title === a.href)) {
      e.preventDefault();
      window.toast?.("In the demo, this leads nowhere: Sonarr and the series' pages on their service are made up.");
    }
  }, true);

  /* ---- The guided tour: the project in seven steps, each on its page (skippable, again from the strip) ---- */
  const TOUR = [
    { hero: true, title: "Unshacklarr in one minute", lead: "The episodes Sonarr is missing, downloaded by Unshackle." },
    { route: "#/schedule", target: "#tab-schedule", icon: "nav [data-tab=schedule] svg", title: "Schedule", lead: "When each episode will be tried.",
      points: ["A release time per series, or the next sync after airing", "Late episodes on top, Download now in one tap",
        "Sonarr's other series shown too: add one in a click"] },
    { route: "#/series", target: "#wall", icon: "nav [data-tab=series] svg", title: "Series", lead: "Your Sonarr library, a card per series.",
      points: ["A service and a link: all a series needs", "Paste a link: its service is picked for you", "A series that keeps failing stands out"] },
    { route: "#/series/s/900004/episodes", target: "#ep-catchup", icon: "nav [data-tab=series] svg", title: "A series", lead: "What the service has, set against Sonarr.",
      points: ["Missing episodes found by their number or their title", "Pick a few and Download: one job", "Its Settings: numbering, release time, episodes in parts (Koh-Lanta airs in two)"] },
    { route: "#/activity", target: "#tab-log", icon: "nav [data-tab=log] svg", title: "Activity", lead: "Every download, live. One is running now.",
      points: ["Tracks, speed and Unshackle's own output", "Pause, stop or retry a whole job", "A history, with stats per service"] },
    { route: "#/settings/cdm", target: "#tab-settings", icon: "nav [data-tab=settings] svg", title: "Settings", lead: "Set up once, then checked for you.",
      points: ["Cookies and CDM devices tested, a warning before they expire", "unshackle.yaml edited with checks and its last versions", "An API key for scripts and Home Assistant"] },
    { route: "#/schedule", target: "#bell", icon: "#bell svg", title: "Notifications", lead: "Nothing to watch: you are told.",
      points: ["Downloads, failures, cookies about to expire", "Discord, Telegram, ntfy, e-mail, or this device", "Quiet hours hold them for one summary"], last: true },
  ];
  const make = (tag, props = {}, ...kids) => { const x = Object.assign(document.createElement(tag), props); x.append(...kids); return x; };
  const svg = (paths, cls = "") => { const s = make("span", { className: cls }); s.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`; return s; };
  const ICON = { check: '<path d="M20 6 9 17l-5-5"/>', sonarr: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="m17 2-5 5-5-5"/>',
    down: '<path d="M12 3v12m-5-5 5 5 5-5"/><path d="M5 21h14"/>', play: '<circle cx="12" cy="12" r="10"/><path d="m10 8 6 4-6 4z"/>' };
  let tourAt = -1, tourTimer = null;
  const ring = make("div", { id: "tour-ring" });
  const card = make("div", { id: "tour-card" });
  card.setAttribute("role", "dialog");
  card.setAttribute("aria-modal", "false");
  function tourPlace() {
    const step = TOUR[tourAt], target = step?.target && document.querySelector(step.target);
    const r = target?.getBoundingClientRect(), shown = r && r.width && r.height;
    ring.classList.toggle("bare", !shown);
    if (shown) Object.assign(ring.style, { left: `${r.left - 6}px`, top: `${r.top - 6}px`, width: `${r.width + 12}px`, height: `${r.height + 12}px` });
    const phone = innerWidth < 760;
    card.classList.toggle("sheet", phone && !step.hero);
    if (phone && !step.hero) return Object.assign(card.style, { left: "", top: "" });  // a sheet above the tab bar, by CSS
    const w = card.offsetWidth, h = card.offsetHeight, gap = 16;
    let left, top;
    if (!shown) { left = (innerWidth - w) / 2; top = (innerHeight - h) / 2; }
    else if (r.height < innerHeight * 0.4 && r.bottom + gap + h < innerHeight) { left = r.right - w; top = r.bottom + gap; }  // a small one: under it
    else if (r.left > w + 48) { left = r.left - w - 24; top = innerHeight - h - 24; }  // room beside it (a series' panel): not over it
    else { left = innerWidth - w - 24; top = innerHeight - h - 24; }
    Object.assign(card.style, { left: `${Math.max(12, Math.min(left, innerWidth - w - 12))}px`, top: `${Math.max(50, top)}px` });
  }
  function flow() {  // Sonarr → Unshacklarr → Unshackle, and back: what the project is, in one look
    const node = (icon, name, what, main) => make("div", { className: `tf-node${main ? " core" : ""}` }, icon, make("b", { textContent: name }), make("small", { textContent: what }));
    const logo = document.querySelector("#home .logo")?.cloneNode(true) || svg(ICON.down);
    const arrow = (text) => make("div", { className: "tf-arrow" }, make("small", { textContent: text }), make("i"));
    return make("div", { className: "tf" }, node(svg(ICON.sonarr), "Sonarr", "knows what is missing"), arrow("missing"),
      node(logo, "Unshacklarr", "picks, names, hands over", true), arrow("download"), node(svg(ICON.down), "Unshackle", "gets it from the service"));
  }
  function tourShow(i) {
    tourAt = i;
    const step = TOUR[i];
    if (step.route && location.hash !== step.route) location.hash = step.route;
    const button = (text, onclick, cls = "") => make("button", { type: "button", textContent: text, onclick, className: `tb ${cls}` });
    const dots = make("div", { className: "tour-dots", role: "tablist", ariaLabel: "Tour steps" }, ...TOUR.slice(1).map((s, k) =>
      make("button", { type: "button", className: k + 1 === i ? "on" : k + 1 < i ? "done" : "", ariaLabel: s.title, title: s.title, onclick: () => tourShow(k + 1) })));
    let body;
    if (step.hero) {
      body = [make("div", { className: "tour-hero" }, make("span", { className: "tour-kicker", textContent: "Guided tour · 6 steps" }),
          make("h2", { textContent: step.title }), make("p", { className: "tour-lead", textContent: step.lead })),
        flow(),
        make("p", { className: "tour-note", textContent: "Then Sonarr imports the file. In this demo, all of it is made up: no Sonarr, no Unshackle, nothing downloaded." }),
        make("div", { className: "tour-nav" }, button("Explore on my own", tourEnd, "ghost"), button("Take the tour  →", () => tourShow(1), "main"))];
    } else {
      const icon = document.querySelector(step.icon)?.cloneNode(true);
      body = [make("div", { className: "tour-head" }, make("span", { className: "tour-badge" }, ...(icon ? [icon] : [])),
          make("div", {}, make("b", { textContent: step.title }), make("small", { textContent: step.lead })),
          make("span", { className: "tour-count", textContent: `${i} / ${TOUR.length - 1}` })),
        make("ul", { className: "tour-points" }, ...step.points.map((x) => make("li", {}, svg(ICON.check, "tp-ico"), make("span", { textContent: x })))),
        ...(step.last ? [make("div", { className: "tour-next" }, make("small", { textContent: "Try it" }),
          button("Download an episode", () => { tourEnd(); location.hash = "#/series/s/900002/episodes"; }, "chip"),
          button("Watch Activity", () => { tourEnd(); location.hash = "#/activity"; }, "chip"))] : []),
        make("div", { className: "tour-foot" }, dots,
          make("div", { className: "tour-nav" }, button("Skip", tourEnd, "ghost"), button("Back", () => tourShow(i - 1)),
            button(step.last ? "Finish" : "Next  →", step.last ? tourEnd : () => tourShow(i + 1), "main"))),
        make("p", { className: "tour-keys", textContent: "← → to move · Esc to leave" })];
    }
    card.className = step.hero ? "hero" : "";
    card.setAttribute("aria-label", step.title);
    card.replaceChildren(...body);
    card.animate([{ opacity: 0, transform: "translateY(8px) scale(.98)" }, { opacity: 1, transform: "none" }], { duration: 260, easing: "cubic-bezier(.2,.8,.2,1)" });
    if (!card.isConnected) document.body.append(ring, card);
    tourPlace();
    clearInterval(tourTimer);
    tourTimer = setInterval(tourPlace, 250);  // the page moves under it as it loads
    card.querySelector(".main")?.focus({ preventScroll: true });
  }
  function tourEnd() {
    clearInterval(tourTimer);
    tourAt = -1;
    ring.remove(); card.remove();
    mem.toured = true; keep();
  }
  addEventListener("keydown", (e) => {
    if (tourAt < 0) return;
    if (e.key === "Escape") { e.stopImmediatePropagation(); tourEnd(); }
    else if (e.key === "ArrowRight" && tourAt < TOUR.length - 1) tourShow(tourAt + 1);
    else if (e.key === "ArrowLeft" && tourAt > 1) tourShow(tourAt - 1);
  }, true);
  addEventListener("resize", () => tourAt >= 0 && tourPlace());

  /* ---- The strip that says it all is fake, and the setup's shortcuts ---- */
  const css = document.createElement("style");
  css.textContent = `
    :root { --demo-h: 38px; }
    body { padding-top: var(--demo-h); }
    .gate, .drawer, .backdrop { top: var(--demo-h) !important; }
    .lang-corner { top: calc(var(--demo-h) + 14px) !important; }
    @media (max-width: 760px) { header { top: var(--demo-h) !important; } }
    body.gated > #demo-bar { display: flex !important; }
    #demo-bar { position: fixed; inset: 0 0 auto 0; height: var(--demo-h); z-index: 1000; display: flex; align-items: center; gap: 10px;
      padding: 0 12px; background: repeating-linear-gradient(-45deg, #f59e0b, #f59e0b 14px, #fbbf24 14px, #fbbf24 28px);
      color: #1a1203; font: 600 13px/1.2 system-ui, sans-serif; box-shadow: 0 1px 0 #0003; }
    #demo-bar b { background: #1a1203; color: #fbbf24; padding: 3px 8px; border-radius: 6px; letter-spacing: .08em; flex: none; }
    #demo-bar span { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    #demo-bar button { flex: none; font: inherit; font-size: 12px; padding: 5px 10px; border-radius: 7px; border: 1px solid #1a1203;
      background: #fffbeb; color: #1a1203; cursor: pointer; }
    #demo-bar button.main { background: #1a1203; color: #fbbf24; }
    #demo-bar i { font-style: normal; } #demo-bar .narrow { display: none; }
    #tour-ring { position: fixed; z-index: 1001; pointer-events: none; border: 2px solid var(--accent); border-radius: 14px;
      box-shadow: 0 0 0 9999px rgb(5 8 14 / .58), 0 0 24px rgb(79 179 191 / .45); transition: left .3s, top .3s, width .3s, height .3s;
      animation: tour-pulse 2.2s ease-in-out infinite; }
    #tour-ring.bare { left: 50% !important; top: 50% !important; width: 0 !important; height: 0 !important; border: 0; animation: none;
      box-shadow: 0 0 0 9999px rgb(5 8 14 / .7); }
    @keyframes tour-pulse { 50% { box-shadow: 0 0 0 9999px rgb(5 8 14 / .58), 0 0 0 6px rgb(79 179 191 / .18), 0 0 30px rgb(79 179 191 / .55); } }
    #tour-card { position: fixed; z-index: 1002; width: min(400px, calc(100vw - 24px)); padding: 18px 18px 12px; border-radius: 16px;
      background: linear-gradient(180deg, #243047, var(--panel)); border: 1px solid #3a4862; color: var(--text);
      box-shadow: 0 24px 60px rgb(0 0 0 / .55), inset 0 1px 0 rgb(255 255 255 / .05); font-size: 14px; line-height: 1.5;
      transition: left .3s cubic-bezier(.2,.8,.2,1), top .3s cubic-bezier(.2,.8,.2,1); }
    #tour-card.hero { width: min(620px, calc(100vw - 24px)); padding: 28px 28px 18px; text-align: center; }
    #tour-card.sheet { left: 8px !important; right: 8px; top: auto !important; bottom: calc(72px + env(safe-area-inset-bottom)); width: auto; }
    .tour-kicker { display: inline-block; font-size: 11.5px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: var(--accent);
      background: rgb(79 179 191 / .12); border: 1px solid rgb(79 179 191 / .3); padding: 3px 10px; border-radius: 999px; }
    #tour-card h2 { margin: 12px 0 4px; font-size: 24px; letter-spacing: -.01em; }
    .tour-lead { margin: 0; color: var(--muted); font-size: 15px; }
    .tour-note { margin: 0 0 18px; color: var(--muted); font-size: 13px; }
    .tf { display: flex; align-items: stretch; justify-content: center; gap: 0; margin: 22px 0 16px; }
    .tf-node { flex: 1; min-width: 0; display: grid; justify-items: center; gap: 4px; padding: 14px 10px; border-radius: 12px;
      background: var(--field-bg); border: 1px solid var(--line); }
    .tf-node.core { border-color: var(--accent); background: rgb(79 179 191 / .08); box-shadow: 0 0 0 4px rgb(79 179 191 / .08); }
    .tf-node > span, .tf-node > svg { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 10px; background: var(--panel); color: var(--accent); }
    .tf-node svg { width: 22px; height: 22px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
    .tf-node > svg.logo { width: 38px; height: 38px; background: none; }
    .tf-node b { font-size: 14px; } .tf-node small { color: var(--muted); font-size: 12px; line-height: 1.35; }
    .tf-arrow { flex: none; width: 74px; display: grid; align-content: center; justify-items: center; gap: 4px; color: var(--muted); font-size: 11px; }
    .tf-arrow i { width: 100%; height: 2px; background: linear-gradient(90deg, transparent, var(--accent)); position: relative; }
    .tf-arrow i::after { content: ""; position: absolute; right: -1px; top: -4px; border: 5px solid transparent; border-left-color: var(--accent); border-right: 0; }
    .tour-head { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 12px; }
    .tour-head > div { flex: 1; min-width: 0; display: grid; } .tour-head b { font-size: 16.5px; } .tour-head small { color: var(--muted); font-size: 13px; }
    .tour-badge { flex: none; width: 40px; height: 40px; display: grid; place-items: center; border-radius: 11px; color: var(--accent);
      background: rgb(79 179 191 / .12); border: 1px solid rgb(79 179 191 / .3); }
    .tour-badge svg { width: 20px; height: 20px; }
    .tour-count { flex: none; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; padding-top: 2px; }
    .tour-points { list-style: none; margin: 0 0 14px; padding: 0; display: grid; gap: 7px; }
    .tour-points li { display: flex; gap: 9px; align-items: flex-start; font-size: 13.5px; }
    .tp-ico { flex: none; width: 18px; height: 18px; margin-top: 1px; display: grid; place-items: center; border-radius: 50%; background: rgb(63 185 122 / .15); color: var(--ok); }
    .tp-ico svg { width: 12px; height: 12px; fill: none; stroke: currentColor; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; }
    .tour-next { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: -2px 0 14px; padding: 10px 12px; border-radius: 10px; background: var(--field-bg); }
    .tour-next small { color: var(--muted); font-size: 12px; margin-right: 2px; }
    .tour-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
    .tour-dots { display: flex; gap: 5px; }
    .tour-dots button { width: 8px; height: 8px; padding: 0; border: 0; border-radius: 999px; background: var(--line); cursor: pointer; transition: width .25s, background .25s; }
    .tour-dots button.done { background: rgb(79 179 191 / .45); } .tour-dots button.on { width: 22px; background: var(--accent); }
    .tour-nav { display: flex; gap: 8px; align-items: center; }
    #tour-card.hero .tour-nav { justify-content: center; }
    #tour-card .tb { font: 600 13px/1 system-ui, sans-serif; padding: 9px 14px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel); color: var(--text); cursor: pointer; white-space: pre; }
    #tour-card .tb:hover { border-color: var(--field-line-hover); }
    #tour-card .tb.main { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
    #tour-card .tb.main:hover { filter: brightness(1.08); }
    #tour-card .tb.ghost { background: none; border-color: transparent; color: var(--muted); }
    #tour-card .tb.ghost:hover { color: var(--text); }
    #tour-card .tb.chip { padding: 7px 12px; border-radius: 999px; border-color: rgb(79 179 191 / .45); color: var(--accent); background: none; }
    #tour-card .tb:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    #tour-card.hero .tb { padding: 11px 18px; font-size: 14px; }
    .tour-keys { margin: 8px 0 0; color: #66728a; font-size: 11.5px; text-align: right; }
    @media (max-width: 760px) {
      .tour-keys { display: none; }
      #tour-card.hero { padding: 22px 16px 14px; } #tour-card h2 { font-size: 21px; }
      .tf { flex-direction: column; align-items: stretch; margin: 16px 0 12px; }
      .tf-node { grid-template-columns: 38px 1fr; justify-items: start; text-align: left; column-gap: 12px; row-gap: 0; padding: 10px 12px; }
      .tf-node > span, .tf-node > svg { grid-row: span 2; }
      .tf-arrow { width: auto; height: 22px; grid-auto-flow: column; justify-content: center; }
      .tf-arrow i { width: 2px; height: 14px; background: var(--accent); }
      .tf-arrow i::after { right: -4px; top: auto; bottom: -6px; border: 5px solid transparent; border-top-color: var(--accent); border-bottom: 0; }
      #tour-card.hero .tour-nav { flex-direction: column-reverse; align-items: stretch; }
    }
    @media (max-width: 760px) { #demo-bar .wide { display: none; } #demo-bar .narrow { display: inline; } #demo-bar { gap: 8px; padding: 0 8px; } }`;
  document.head.append(css);

  function fillStep() {  // the visible step of the setup, filled with made-up values
    const panel = document.querySelector(".wiz-panel");
    if (!panel) return;
    const set = (input, value) => { input.value = value; input.dispatchEvent(new Event("input", { bubbles: true })); };
    const step = [...document.querySelectorAll(".wz-steps li")].findIndex((li) => li.classList.contains("on"));
    const shown = [...panel.querySelectorAll("input")].filter((i) => i.offsetParent !== null);
    if (step === 0) [["DEMO"], ["demo-password"], ["demo-password"]].forEach(([v], i) => shown[i] && set(shown[i], v));
    else {  // the first key still empty (Unshackle's, Sonarr's: never the optional TMDB one after it)
      const key = shown.find((i) => i.type === "password");
      if (key && !key.value) set(key, "demo-key-0000000000000000");
    }
  }
  function skipSetup() {
    mem.configured = mem.logged = true;
    mem.config = copy(R["/api/state"].config);
    keep();
    try { sessionStorage.removeItem("setup-draft"); } catch {}
    location.reload();
  }
  function restart() {
    try { sessionStorage.removeItem(KEY); sessionStorage.removeItem("setup-draft"); } catch {}
    location.reload();
  }
  function bar() {
    const b = document.createElement("div");
    b.id = "demo-bar";
    b.setAttribute("role", "note");
    const button = (text, onclick, main) => Object.assign(document.createElement("button"), { type: "button", textContent: text, onclick, className: main ? "main" : "" });
    const words = document.createElement("span");
    const draw = () => {
      const setup = !mem.configured, login = mem.configured && !mem.logged;
      const [wide, narrow] = setup ? ["Everything here is fake. Setup code: DEMO, any password: nothing is checked for real.", "All fake · code DEMO"]
        : login ? ["Everything here is fake. Any password logs in.", "All fake · any password"]
        : ["Everything here is fake: no real Sonarr, Unshackle, device or cookie behind it. Nothing is downloaded, nothing leaves this tab.", "All fake"];
      words.replaceChildren(Object.assign(document.createElement("i"), { className: "wide", textContent: wide }),
        Object.assign(document.createElement("i"), { className: "narrow", textContent: narrow }));
      b.replaceChildren(Object.assign(document.createElement("b"), { textContent: "DEMO" }), words,
        ...(setup ? [button("Fill this step", fillStep), button("Skip setup", skipSetup, true)]
          : login ? [] : [button("Tour", () => tourShow(0)), button("Start over", restart)]));
    };
    draw();
    document.body.prepend(b);
    // the first time in the app: the tour offers itself, once the page has drawn
    if (mem.configured && mem.logged && !mem.toured) setTimeout(() => tourAt < 0 && tourShow(0), 1500);
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", bar) : bar();
})();
