/* Numbering and file names: the series' own, or one download's while its dialog is open (not saved). */
let oneOff = null;
const advConf = () => oneOff || S.config.series[current.tvdbId];
const advDirty = () => { if (oneOff) renderPreview(); else dirty(); };
function fillAdv(conf, title) {
  $("#d-filename").value = conf.file_name || "";
  $("#d-filename").placeholder = title;
  $("#d-seasons").value = Object.entries(conf.season_map || {}).map(([a, b]) => `${a}=${b}`).join(", ");
  $("#d-offset").value = conf.episode_offset || "";
  $("#d-soffset").value = conf.season_offset || "";
  $("#d-sfrom").value = conf.season_offset ? conf.season_offset_from || 1 : "";
  $("#d-epmap").value = Object.entries(conf.episode_map || {}).map(([a, b]) => `${a}=${b}`).join(", ");
  $("#d-join").checked = conf.join_parts !== false;
  $("#d-epname").value = conf.episode_name || "joined";
  $("#d-parts").value = String(conf.parts || "");
}
$("#d-offset").oninput = (e) => { advConf().episode_offset = parseInt(e.target.value, 10) || 0; advDirty(); };
$("#d-soffset").oninput = (e) => { advConf().season_offset = parseInt(e.target.value, 10) || 0; advDirty(); };
$("#d-sfrom").oninput = (e) => { advConf().season_offset_from = Math.max(1, parseInt(e.target.value, 10) || 1); advDirty(); };
$("#d-epmap").oninput = (e) => {
  const pairs = [...e.target.value.toUpperCase().matchAll(/(S\d+E\d+)\s*=\s*(S\d+E\d+(?:\.\d+)?)/g)];
  advConf().episode_map = Object.fromEntries(pairs.map((m) => [m[1], m[2]]));
  advDirty();
};
$("#d-join").onchange = (e) => { advConf().join_parts = e.target.checked; advDirty(); };
$("#d-epname").onchange = (e) => { advConf().episode_name = e.target.value; advDirty(); };
$("#d-parts").onchange = (e) => { advConf().parts = Number(e.target.value) || undefined; advDirty(); };
$("#d-release").oninput = (e) => { S.config.series[current.tvdbId].release_time = e.target.value; dirty(); renderReleaseNext(); };  // at once: Save shows then, not on leaving the field
$("#d-audio").oninput = (e) => { S.config.series[current.tvdbId].audio_accept = e.target.value.trim() || undefined; dirty(); };
$("#d-release-day").onchange = (e) => { S.config.series[current.tvdbId].release_day = Number(e.target.value); dirty(); renderReleaseNext(); };
/* The day a service publishes, from the day Sonarr says an episode airs: that day, the day after, or up to two weeks
   before (a platform ahead of the channel: sync.EARLY_DAYS_MAX). */
const RELEASE_DAYS = [-14, -7, -6, -5, -4, -3, -2, -1, 0, 1];
const releaseDayLabel = (d) => d === 0 ? "the day it airs" : d === 1 ? "the day after" : d === -1 ? "the day before"
  : d === -7 ? "a week before" : d === -14 ? "two weeks before" : `${-d} days before`;
$("#d-release-day").replaceChildren(...RELEASE_DAYS.map((d) => el("option", { value: String(d), textContent: releaseDayLabel(d) })));
/* A series' own broadcast schedule (sync.broadcast_dates, in the browser's time): episode id -> when it airs. From its
   first episode on, every one after it; its first evening always counts, then its days (or every day), so many an
   evening except the days changed on its calendar. */
const isoLocal = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const weekday = (d) => (d.getDay() + 6) % 7;  // Monday 0, as Python counts
function broadcastCount(plan, d, first) {
  const regular = plan.every === "daily" || (plan.days || []).includes(weekday(d)) || isoLocal(d) === first;
  return Number((plan.evenings || {})[isoLocal(d)] ?? (regular ? plan.per_evening || 1 : 0));
}
function broadcastDates(plan, eps) {
  const out = new Map();
  if (!plan?.start || !plan.time) return out;
  const [fs, fe] = (/S(\d+)E(\d+)/i.exec(plan.from || "S01E01") || [0, 1, 1]).slice(1).map(Number);
  let todo = eps.filter((e) => e.seasonNumber > 0 && (e.seasonNumber > fs || (e.seasonNumber === fs && e.episodeNumber >= fe)))
    .sort((a, b) => a.seasonNumber - b.seasonNumber || a.episodeNumber - b.episodeNumber);
  const [h, m] = plan.time.split(":").map(Number), day = new Date(`${plan.start}T00:00`);
  for (let i = 0; i < 3 * 366 && todo.length; i++, day.setDate(day.getDate() + 1)) {
    const n = broadcastCount(plan, day, plan.start);
    const at = new Date(day); at.setHours(h, m, 0, 0);
    todo.slice(0, n).forEach((e) => out.set(e.id, at));
    todo = todo.slice(n);
  }
  return out;
}
function applyBroadcast(conf) {  // the episodes' dates as the series stands, its own schedule first
  const eps = Object.values(epSeasons || {}).flat(), dates = broadcastDates(conf?.broadcast, eps);
  for (const e of eps) e.airDateUtc = dates.get(e.id)?.toISOString() ?? e.sonarrAir;
}
/* The schedule's editor: its fields, its days, a month to click (each day: 0, 1, 2 or 3 episodes; back to the usual),
   and the dates it gives the episodes. */
let bcMonth = null;
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
function renderBroadcast() {
  const conf = current && S.config.series[current.tvdbId], plan = conf?.broadcast;
  $("#d-bc").hidden = !conf?.service;
  $("#d-bc-on").checked = !!plan;
  $("#d-bc-form").hidden = !plan;
  if (!plan) return;
  const eps = Object.values(epSeasons || {}).flat().filter((e) => e.seasonNumber > 0).sort((a, b) => a.seasonNumber - b.seasonNumber || a.episodeNumber - b.episodeNumber);
  $("#d-bc-from").replaceChildren(...eps.map((e) => { const k = `S${pad2(e.seasonNumber)}E${pad2(e.episodeNumber)}`;
    return el("option", { value: k, textContent: `${k}${e.title && e.title !== "TBA" ? ` · ${e.title}` : ""}` }); }));
  if (!eps.length) $("#d-bc-from").append(el("option", { value: plan.from, textContent: plan.from }));
  $("#d-bc-from").value = plan.from;
  $("#d-bc-start").value = plan.start;
  $("#d-bc-time").value = plan.time;
  $("#d-bc-every").value = plan.every;
  $("#d-bc-per").value = String(plan.per_evening || 1);
  $("#d-bc-days").hidden = plan.every === "daily";
  $("#d-bc-days").replaceChildren(...DAY_NAMES.map((name, i) => el("button", { type: "button", textContent: name,
    ariaPressed: String((plan.days || []).includes(i)), onclick: () => {
      const days = new Set(plan.days || []);
      days.has(i) ? days.delete(i) : days.add(i);
      plan.days = [...days].sort();
      changedBroadcast();
    } })));
  // the month: from the first evening's, one at a time
  bcMonth ??= new Date(`${plan.start}T00:00`);
  const first = new Date(bcMonth.getFullYear(), bcMonth.getMonth(), 1), today = isoLocal(new Date());
  const cells = [];
  for (let i = 0; i < weekday(first); i++) cells.push(el("span"));
  for (let d = new Date(first); d.getMonth() === first.getMonth(); d.setDate(d.getDate() + 1)) {
    const iso = isoLocal(d), n = iso < plan.start ? 0 : broadcastCount(plan, d, plan.start), changed = iso in (plan.evenings || {});
    const usual = Number(((plan.every === "daily" || (plan.days || []).includes(weekday(d)) || iso === plan.start) ? plan.per_evening || 1 : 0));
    const say = `${n} episode${n === 1 ? "" : "s"} that day${changed ? " (changed)" : ""}: click to change`;
    cells.push(el("button", { type: "button", className: `${n ? "on" : ""}${changed ? " changed" : ""}${iso === today ? " today" : ""}`,
      disabled: iso < plan.start, title: say, ariaLabel: `${d.toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long" })} · ${say}`,
      onclick: () => {
        const next = (n + 1) % 4;  // 0, 1, 2, 3, round again
        plan.evenings = { ...(plan.evenings || {}) };
        if (next === usual) delete plan.evenings[iso]; else plan.evenings[iso] = next;
        changedBroadcast();
      } }, String(d.getDate()), ...(n ? [el("b", { textContent: n > 1 || changed ? String(n) : "" })] : [])));
  }
  const move = (step) => { bcMonth = new Date(bcMonth.getFullYear(), bcMonth.getMonth() + step, 1); renderBroadcast(); };
  $("#d-bc-cal").replaceChildren(
    el("div", { className: "bc-cal-head" }, el("button", { type: "button", ariaLabel: "Previous month", textContent: "‹", onclick: () => move(-1) }),
      el("span", { textContent: first.toLocaleDateString(LOCALE, { month: "long", year: "numeric" }) }),
      el("button", { type: "button", ariaLabel: "Next month", textContent: "›", onclick: () => move(1) })),
    el("div", { className: "bc-cal-grid" }, ...DAY_NAMES.map((n) => el("small", { textContent: n.slice(0, 2) })), ...cells));
  // what it gives: the episodes around now, dated
  const dates = broadcastDates(plan, eps), dated = eps.filter((e) => dates.has(e.id));
  const from = Math.max(0, dated.findIndex((e) => dates.get(e.id) > Date.now() - 86400000) - 2);
  $("#d-bc-list").replaceChildren(...dated.slice(from, from + 10).map((e) => {
    const at = dates.get(e.id);
    return el("li", { className: at < Date.now() ? "past" : "" }, el("b", { textContent: `S${pad2(e.seasonNumber)}E${pad2(e.episodeNumber)}` }),
      el("span", { textContent: `${day(at)} ${time(at)}` }), ...(e.hasFile ? [el("small", { textContent: "on disk" })] : []));
  }));
}
function changedBroadcast() {
  const conf = S.config.series[current.tvdbId];
  applyBroadcast(conf);
  dirty();
  renderBroadcast();
  renderReleaseNext();
  if ($("#ep-list")) renderSeason();
}
$("#d-bc-on").onchange = (e) => {
  const conf = S.config.series[current.tvdbId];
  if (e.target.checked) {  // a start: the next episode not on disk, tonight, at the release time or 20:00, weekly that day
    const eps = Object.values(epSeasons || {}).flat().filter((x) => x.seasonNumber > 0).sort((a, b) => a.seasonNumber - b.seasonNumber || a.episodeNumber - b.episodeNumber);
    const next = eps.find((x) => !x.hasFile) || eps[0], now = new Date();
    conf.broadcast = { from: next ? `S${pad2(next.seasonNumber)}E${pad2(next.episodeNumber)}` : "S01E01", start: isoLocal(now),
      time: conf.release_time || "20:00", every: "weekly", days: [weekday(now)], per_evening: 1, evenings: {} };
    bcMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    $("#d-bc").open = true;
  } else delete conf.broadcast;
  changedBroadcast();
};
$("#d-bc-from").onchange = (e) => { S.config.series[current.tvdbId].broadcast.from = e.target.value; changedBroadcast(); };
$("#d-bc-start").oninput = (e) => { if (!e.target.value) return; const plan = S.config.series[current.tvdbId].broadcast; plan.start = e.target.value; bcMonth = new Date(`${plan.start}T00:00`); changedBroadcast(); };
$("#d-bc-time").oninput = (e) => { if (e.target.value) { S.config.series[current.tvdbId].broadcast.time = e.target.value; changedBroadcast(); } };
$("#d-bc-every").onchange = (e) => { S.config.series[current.tvdbId].broadcast.every = e.target.value; changedBroadcast(); };
$("#d-bc-per").onchange = (e) => { S.config.series[current.tvdbId].broadcast.per_evening = Number(e.target.value); changedBroadcast(); };
/* When the series' next episode will be tried, as the settings stand: what they change, in sight. */
function releaseSlot(conf, ep) {
  const day = Number(conf.release_day || 0), time = conf.release_time || "";
  if (!ep.airDateUtc || (!time && day >= 0)) return null;
  const at = new Date(ep.airDateUtc);
  at.setDate(at.getDate() + day);
  const [h, m] = (time || "00:00").split(":").map(Number);
  at.setHours(h, m, 0, 0);
  return at;
}
function renderReleaseNext() {
  const box = $("#d-release-next"), conf = current && S.config.series[current.tvdbId];
  const soon = Object.values(epSeasons || {}).flat().filter((e) => e.seasonNumber > 0 && e.airDateUtc && new Date(e.airDateUtc) > Date.now() - 86400000)
    .sort((a, b) => new Date(a.airDateUtc) - new Date(b.airDateUtc));
  const ep = soon.find((e) => !e.hasFile) || soon[0];
  box.hidden = !conf?.service || !ep;
  if (box.hidden) return;
  const aired = new Date(ep.airDateUtc), slot = releaseSlot(conf, ep);
  const when = (d) => `${day(d)} ${time(d)}`;
  box.replaceChildren(el("span", { textContent: "Next" }), el("b", { textContent: `S${pad2(ep.seasonNumber)}E${pad2(ep.episodeNumber)}` }),
    el("span", { textContent: `airs ${when(aired)}` }), el("span", { className: "arrow", textContent: "→" }),
    el("b", { textContent: slot ? `tried ${when(slot)}` : "tried at the first sync after it airs" }));
}
$("#d-filename").oninput = (e) => { advConf().file_name = e.target.value.trim(); advDirty(); };
$("#d-seasons").oninput = (e) => {
  const pairs = [...e.target.value.matchAll(/(\d+)\s*=\s*(\d+)/g)];
  advConf().season_map = Object.fromEntries(pairs.map((m) => [m[1], +m[2]]));
  advDirty();
};
$("#close").onclick = () => closeDrawer();  // not closeDrawer itself: it would get the click event as fromRoute
function showDrawerTab(name, fromRoute = false) {
  if (!["episodes", "settings"].includes(name)) name = "episodes";
  drawerTab = name;
  if (!fromRoute) navigate(false);
  document.querySelectorAll(".d-tabs button").forEach((b) => b.setAttribute("aria-selected", b.dataset.dtab === name));
  $("#dt-episodes").hidden = name !== "episodes";
  $("#dt-settings").hidden = name !== "settings";
  $("#ep-bar").classList.toggle("off", name !== "episodes");
}
document.querySelectorAll(".d-tabs button").forEach((b) => b.onclick = () => showDrawerTab(b.dataset.dtab));
$("#d-save").onclick = () => saveHere($("#d-save"));

/* Sonarr-like episode list: tick episodes or whole seasons, then download them now. */
const picked = new Set();
let epSeasons = {}, epSeason = null, hideOnDisk = false;
async function loadEpisodes(s, keep = false) {  // keep: a reload after a download ended, same season and selection
  const box = $("#d-episodes");
  if (!keep) {
    epAvail = epTitles = null;
    epSeasons = {}; epSeason = null; epBusy = new Map();  // nothing of the series open before
    picked.clear();
    updateEpBar();
    box.replaceChildren(loading("Loading episodes from Sonarr…"));
    refreshEpBusy(s);
    epChecked = null;
    api(`/api/probe/${s.tvdbId}`).then((hit) => {  // the last check of the service, kept 12 h: no need to ask again
      if (!hit || current !== s || epAvail) return;
      epAvail = hit.available; epTitles = hit.titles; epChecked = hit.checked; localTitles(hit.local_titles);
      if ($("#ep-list")) { $("#ep-avail").hidden = false; renderSeason(); checkLabel(`Refresh what's on ${svcName(S.config.series[s.tvdbId]?.service)}`); }
    }).catch(() => {});
  }
  let eps;
  try { eps = await api(`/api/series/${s.id}/episodes`); }
  catch (e) { if (current === s) box.replaceChildren(failed(`Could not load the episodes: ${e.message}`, () => loadEpisodes(s))); return; }
  if (current !== s) return;
  epSeasons = {};
  for (const e of eps) { e.sonarrAir = e.airDateUtc; (epSeasons[e.seasonNumber] ??= []).push(e); }
  applyBroadcast(S.config.series[s.tvdbId]);  // a series with its own schedule: its dates, not Sonarr's
  renderReleaseNext();  // Timing says when the next one is tried
  renderBroadcast();
  const numbers = Object.keys(epSeasons).map(Number).sort((a, b) => (a === 0) - (b === 0) || b - a);  // newest first, specials last
  if (!numbers.length) return box.replaceChildren(el("p", { className: "muted", textContent: "Sonarr lists no episode for this series." }));
  if (!(keep && numbers.includes(epSeason))) epSeason = numbers[0];  // the newest; seasons with gaps stand out in the strip
  box.replaceChildren(
    el("div", { className: "seasons", role: "group", ariaLabel: "Seasons" }, ...numbers.map((n) => {
      const list = epSeasons[n], disk = list.filter((e) => e.hasFile).length, miss = list.filter((e) => state(e) === "miss").length;
      const b = el("button", { onclick: () => { epSeason = n; renderSeason(); } },
        el("b", { textContent: n === 0 ? "Specials" : `S${String(n).padStart(2, "0")}` }),
        el("small", { className: miss ? "gap" : "", textContent: miss ? `${miss} missing` : `${disk}/${list.length}` }));
      b.dataset.season = n;
      return b;
    })),
    cdmNote(S.config.series[s.tvdbId]?.service),
    el("div", { className: "ep-catchup", id: "ep-catchup", hidden: true }),
    /* Two lines: the season and what the service has; then what to select, and how to show the list. */
    el("div", { className: "ep-tools" },
      el("div", { className: "ep-top" }, el("h3", { id: "ep-title" })),
      el("div", { className: "ep-row" },
        el("div", { className: "ep-sel", role: "group", ariaLabel: "Select" },
          el("span", { textContent: "Select" }),
          el("button", { type: "button", textContent: "Missing", title: "Every missing episode of this season", onclick: () => selectSeason((e) => state(e) === "miss") }),
          el("button", { type: "button", textContent: "All", onclick: () => selectSeason(() => true) }),
          el("button", { type: "button", id: "ep-odd", hidden: true, className: "odd", title: "The episodes in orange: to download again, replacing Sonarr's file",
            onclick: () => selectSeason(isOutlier) })),
        el("div", { className: "ep-view" },
          el("label", { className: "pill" }, el("input", { type: "checkbox", checked: hideOnDisk,
            onchange: (e) => { hideOnDisk = e.target.checked; renderSeason(); } }), "Hide on disk"),
          colsButton()))),
    el("ul", { className: "ep-list", id: "ep-list" }));
  renderCheck(s);
  renderSeason();
}
/* "What's on <service>?" and its pick of the missing episodes it has, for the series' service and URL as they stand:
   set or changed in its Settings after the list was drawn, they show at once, and what the old link listed is forgotten. */
function renderCheck(s, changed = false) {
  if (current !== s || !$("#ep-list")) return;
  $(".ep-check")?.remove();
  $("#ep-avail")?.remove();
  if (changed && epAvail) { epAvail = epTitles = epChecked = null; renderSeason(); }
  const conf = S.config.series[s.tvdbId];
  if (!conf?.service || !conf.title) return;  // what the service has, listed (unshackle's --list-titles): nothing downloaded
  const svc = svcName(conf.service);
  const pick = el("button", { type: "button", id: "ep-avail", className: "svc", hidden: !epAvail,
    title: `The missing episodes ${svc} has`, onclick: () => selectSeason((e) => state(e) === "miss" && epAvail?.[e.id]) });
  const check = el("button", { className: "btn ep-check", title: `List what ${svc} has of this series, and mark it on the episodes`,
    onclick: () => askService(s).then((r) => r && toast(`${svc} has ${Object.keys(r.available).length} of this series' episodes`)) });
  $(".ep-top").append(check);
  checkLabel(epAvail ? `Refresh what's on ${svc}` : `What's on ${svc}?`);
  $("#ep-odd").before(pick);
}
let epAvail = null;  // episode id -> what the service calls it, once "What's on <service>?" asked it
let epTitles = null;  // everything the service lists then: [{key: "S26E11", part, name}]
let epChecked = null;  // when that was
/* TVDB's titles in your language (its website), over Sonarr's where it has none ("TBA"): a French series reads. */
function localTitles(names) {
  for (const e of Object.values(epSeasons || {}).flat()) {
    const name = names?.[e.id];
    if (name && (!e.title || e.title === "TBA")) { e.title = name; e.localTitle = true; }
  }
}
const svcName = (tag) => S.serviceNames?.[tag] || tag;  // "RMC+" for RMCP
/* This series' downloads going on now: shown on their episodes, which can't be picked again meanwhile. */
let epBusy = new Map(), epBusyTimer = null;
async function refreshEpBusy(s) {
  let cards;
  try { cards = await api("/api/runs"); } catch { cards = null; }
  if (current !== s) return;
  if (cards) {
    const was = epBusy;
    epBusy = new Map(cards.filter((c) => isLive(c) && c.tvdbId === s.tvdbId).map((c) => [c.episodeId, c]));
    epBusy.forEach((_, id) => picked.delete(id));
    if ([...was.keys()].some((id) => !epBusy.has(id))) loadEpisodes(s, true);  // one ended: on disk now, or not
    else if ($("#ep-list")) renderSeason();
    updateEpBar();
  }
  // ponytail: polls /api/runs while the series is open; Activity's live feed if this gets heavy
  clearTimeout(epBusyTimer);  // after the await: one loop, however many calls overlapped
  epBusyTimer = setTimeout(() => refreshEpBusy(s), epBusy.size ? 3000 : 20000);
}
function state(e) {
  if (epBusy.has(e.id)) return "busy";
  return e.hasFile ? "disk" : e.airDateUtc && new Date(e.airDateUtc) <= Date.now() ? "miss" : "soon";
}
/* An episode on disk whose file is unlike most of its season (the orange ones). */
function isOutlier(e) {
  if (!e.file) return false;
  const odd = oddities(e.file, seasonNorm(epSeasons[epSeason]));
  return Object.values(odd).some(Boolean);
}
function selectSeason(test) {
  const list = epSeasons[epSeason].filter((e) => !epBusy.has(e.id) && test(e));
  const all = list.every((e) => picked.has(e.id));
  list.forEach((e) => all ? picked.delete(e.id) : picked.add(e.id));  // a second click unselects
  renderSeason();
  updateEpBar();
}
/* An episode on disk: its file's specs, from Sonarr's media info, set against the rest of
   its season. What most episodes have is the norm; a value off it is flagged, with why. */
const rate = (bps) => bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} Mb/s` : `${Math.round(bps / 1e3)} kb/s`;
const layout = (n) => ({ 1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1" })[n] || (Number.isInteger(n) ? `${n} ch` : n ? String(n) : "");  // Sonarr gives 6 or 5.1
const langSet = (list) => [...list].sort().join(", ");  // the same languages in another order are the same
const size = (b) => `${(b / 2 ** 30).toFixed(b >= 10 * 2 ** 30 ? 0 : 1)} GB`;
const bitrate = (f) => f.videoBitrate || f.averageBitrate || 0;
function seasonNorm(list) {
  const files = list.map((e) => e.file).filter(Boolean);
  const mode = (values) => {
    const counts = new Map();
    values.forEach((v) => counts.set(v, (counts.get(v) || 0) + 1));
    return [...counts].sort((a, b) => b[1] - a[1])[0]?.[0];
  };
  const rates = files.map(bitrate).filter(Boolean).sort((a, b) => a - b);
  return files.length < 3 ? null : {  // two files make no majority
    height: mode(files.map((f) => f.height || 0)),
    audio: mode(files.map((f) => langSet(f.audioLanguages))),
    subs: mode(files.map((f) => langSet(f.subtitles))),
    rate: rates[Math.floor(rates.length / 2)],
  };
}
function oddities(f, norm) {
  if (!norm) return {};
  return {
    height: f.height && norm.height && f.height < norm.height ? `Most of this season is ${norm.height}p` : "",
    audio: langSet(f.audioLanguages) !== norm.audio ? `Most of this season has audio in ${norm.audio || "no language tagged"}` : "",
    subs: langSet(f.subtitles) !== norm.subs ? (norm.subs ? `Most of this season has subtitles in ${norm.subs}` : "Most of this season has no subtitles") : "",
    rate: norm.rate && bitrate(f) && bitrate(f) < norm.rate * 0.6 ? `Well below the season's usual ${rate(norm.rate)}` : "",
  };
}
function resBadge(f, why) {
  const tier = f.height >= 1000 ? "r1080" : f.height >= 700 ? "r720" : "rsd";
  return el("span", { className: `res ${tier}${why ? " odd" : ""}`, title: why || f.resolution, textContent: f.height ? `${f.height}p` : "?" });
}
const flagged = (text, why) => why ? el("span", { className: "odd", title: why, textContent: text }) : text;
/* The episode list's columns, as in Sonarr: which ones and in what order, kept in this browser. */
const COLUMNS = [
  { id: "res", label: "Res.", file: true, on: true, cell: (f, odd) => resBadge(f, odd.height) },
  { id: "video", label: "Video", file: true, on: true, cell: (f, odd) => el("span", { className: "spec",
      title: f.videoBitrate ? "" : "The file's average bitrate: Sonarr has none for its video" },
      f.video || "?", " ", el("small", {}, flagged(bitrate(f) ? `${f.videoBitrate ? "" : "≈"}${rate(bitrate(f))}` : "", odd.rate))) },
  { id: "audio", label: "Audio", file: true, on: true, cell: (f, odd) => el("span", { className: "spec" },
      [f.audio, layout(f.channels)].filter(Boolean).join(" "), " ", el("small", {}, flagged(f.audioLanguages.join(", "), odd.audio))) },
  { id: "subs", label: "Subtitles", file: true, on: true, cell: (f, odd) => el("span",
      { className: "spec" + (f.subtitles.length ? "" : " blank") }, flagged(f.subtitles.join(", ") || "none", odd.subs)) },
  { id: "size", label: "Size", file: true, on: true, cell: (f) => el("span", { className: "spec", textContent: f.size ? size(f.size) : "" }) },
  { id: "quality", label: "Quality", file: true, on: false, cell: (f) => el("span", { className: "spec", textContent: f.quality }) },
  { id: "group", label: "Group", file: true, on: false, cell: (f) => el("span", { className: "spec", textContent: f.releaseGroup }) },
  { id: "runtime", label: "Duration", file: true, on: false, cell: (f) => el("span", { className: "spec", textContent: f.runTime }) },
  { id: "aired", label: "Aired", file: false, on: true,
    cell: (_, __, e) => el("time", { textContent: e.airDateUtc ? new Date(e.airDateUtc).toLocaleDateString(LOCALE) : "" }) },
];
const COLS_KEY = "unshacklarr.episodeColumns";
function colPrefs() {  // [{id, on}] in display order; columns added since are appended as they default
  let saved = [];
  try { saved = JSON.parse(localStorage.getItem(COLS_KEY)) || []; } catch { /* private window: the defaults */ }
  const known = saved.filter((p) => COLUMNS.some((c) => c.id === p.id));
  return [...known, ...COLUMNS.filter((c) => !known.some((p) => p.id === c.id)).map((c) => ({ id: c.id, on: c.on }))];
}
function saveColPrefs(prefs) {
  try { localStorage.setItem(COLS_KEY, JSON.stringify(prefs)); } catch { /* kept for this visit only */ }
  colsNow = prefs;
  renderSeason();
}
let colsNow = colPrefs();
function colsButton() {
  const b = el("button", { className: "btn cols-btn", type: "button", onclick: openColumns }, "Columns");
  b.insertAdjacentHTML("afterbegin", `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/></svg>`);
  return b;
}
function openColumns() {
  const draw = () => $("#col-rows").replaceChildren(...colsNow.map((p, i) => {
    const c = COLUMNS.find((x) => x.id === p.id);
    const box = el("input", { type: "checkbox", checked: p.on, onchange: () => { p.on = box.checked; saveColPrefs(colsNow); } });
    const move = (d) => { const next = [...colsNow]; [next[i], next[i + d]] = [next[i + d], next[i]]; saveColPrefs(next); draw(); $("#col-rows").children[i + d]?.querySelector(d < 0 ? ".up" : ".down")?.focus(); };
    return el("li", {}, el("label", {}, box, c.label, c.file ? "" : el("small", { textContent: "any episode" })),
      el("button", { type: "button", className: "mv up", textContent: "↑", ariaLabel: `Move ${c.label} left`, disabled: i === 0, onclick: () => move(-1) }),
      el("button", { type: "button", className: "mv down", textContent: "↓", ariaLabel: `Move ${c.label} right`, disabled: i === colsNow.length - 1, onclick: () => move(1) }));
  }));
  $("#cols-reset").onclick = (e) => { e.preventDefault(); try { localStorage.removeItem(COLS_KEY); } catch { /* nothing kept */ } colsNow = colPrefs(); renderSeason(); draw(); };
  draw();
  $("#cols-dialog").showModal();
}
function fileLine(f, odd) {  // the same, in one line (phones)
  const parts = [f.video, bitrate(f) ? flagged(`${f.videoBitrate ? "" : "≈"}${rate(bitrate(f))}`, odd.rate) : "",
    [f.audio, layout(f.channels)].filter(Boolean).join(" "), flagged(f.audioLanguages.join(", "), odd.audio),
    flagged(f.subtitles.length ? `subs ${f.subtitles.join(", ")}` : "no subs", odd.subs), f.size ? size(f.size) : ""].filter(Boolean);
  return el("small", {}, resBadge(f, odd.height), ...parts.flatMap((p, i) => i ? [" · ", p] : [p]));
}
function fileTooltip(e) {
  const f = e.file;
  if (!f) return e.title || "";
  return [e.title, `${f.quality}${f.releaseGroup ? ` · ${f.releaseGroup}` : ""}`, `${f.resolution}${f.runTime ? ` · ${f.runTime}` : ""}`].filter(Boolean).join("\n");
}
const STATE_ICONS = {
  disk: '<circle cx="12" cy="12" r="10" fill="currentColor"/><path d="m7.5 12.5 3 3 6-6.5" fill="none" stroke="#10151d" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
  miss: '<circle cx="12" cy="12" r="9.2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 7.5v8M8.5 12.5l3.5 3.5 3.5-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  busy: '<circle cx="12" cy="12" r="9.2" fill="none" stroke="currentColor" stroke-width="1.8" opacity=".3"/><path d="M12 2.8a9.2 9.2 0 0 1 9.2 9.2" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>',
  soon: '<circle cx="12" cy="12" r="9.2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 7v5.2l3.2 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
};
function stateIcon(st, label) {
  const i = el("span", { className: `ep-state ${st}`, title: label, role: "img", ariaLabel: label });
  if (st === "busy") i.style.setProperty("--spin", `-${Date.now() % 1000}ms`);  // redrawn every 3 s: it keeps turning
  i.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${STATE_ICONS[st]}</svg>`;
  return i;
}
/* The columns under the title only when, side by side, they would overflow the list. */
function fitEpList(list) {
  list.classList.remove("stack");
  if (innerWidth > 700 && list.scrollWidth > list.clientWidth + 1) list.classList.add("stack");
}
const epFit = new ResizeObserver(([e]) => fitEpList(e.target));
/* A tooltip over everything (the list clips its cells), fading in on hover or focus. */
let tipEl = null, tipFor = null;
const hideTip = () => { tipEl?.remove(); tipEl = tipFor = null; };
/* What floats (the tooltip, the toasts) goes to the browser's top layer, above any window or menu opened
   before it: no z-index beats a dialog opened as a modal. Shown again, it comes above the newest. */
function onTop(node) {
  if (!node.showPopover) return;  // an older browser: its z-index, as before
  if (!node.popover) node.popover = "manual";
  if (node.matches(":popover-open")) node.hidePopover();
  node.showPopover();
}
{  // a window opened as a modal while a tooltip or a toast shows: they come above it again
  const showModal = HTMLDialogElement.prototype.showModal;
  HTMLDialogElement.prototype.showModal = function () {
    showModal.call(this);
    if ($("#toasts").children.length) onTop($("#toasts"));
    if (tipEl) onTop(tipEl);
  };
}
function showTip(target, nodes) {
  hideTip();
  tipFor = target;
  tipEl = el("div", { className: "tip", role: "tooltip" }, ...nodes);
  document.body.append(tipEl);
  onTop(tipEl);
  const r = target.getBoundingClientRect(), t = tipEl.getBoundingClientRect();
  tipEl.style.left = `${Math.min(Math.max(8, r.left + r.width / 2 - t.width / 2), innerWidth - t.width - 8)}px`;
  tipEl.style.top = `${r.top - t.height - 10 < 8 ? r.bottom + 10 : r.top - t.height - 10}px`;
  requestAnimationFrame(() => tipEl?.classList.add("on"));
}
function hoverTip(target, build) {
  target.addEventListener("mouseenter", () => showTip(target, build()));
  target.addEventListener("mouseleave", hideTip);
  return target;
}
/* Every button or link with only an icon in sight (no letter) shows what it does in the common tooltip, on hover
   or keyboard focus: its title (taken off, so the browser's own doesn't show too), else its aria-label.
   Delegated: the ones added later have it as well. */
function iconOnly(target) {
  const b = target.closest?.("button, a[href], [role=button]");
  if (!b || /\p{L}/u.test(b.innerText)) return null;  // the text in sight: a phone may hide a button's words
  if (b.title) { b.dataset.tip = b.title; b.removeAttribute("title"); }
  const label = b.dataset.tip || b.getAttribute("aria-label");  // a short title for the eye, the full aria-label for a screen reader
  return label ? [b, label] : null;
}
for (const [kind, leave] of [["mouseover", "mouseout"], ["focusin", "focusout"]]) {
  document.addEventListener(kind, (e) => {
    const found = iconOnly(e.target);
    if (!found && tipFor && !tipFor.isConnected) return hideTip();  // its button went (a row removed): nothing left to leave
    if (!found || found[0] === tipFor || (kind === "focusin" && !found[0].matches(":focus-visible"))) return;
    if (setupPop && found[0].classList.contains("setup-btn")) return;  // its menu open: no tooltip over it
    if (tipEl && !tipFor?.isConnected && tipEl.textContent === found[1]) tipFor = found[0];  // the same button, redrawn: the tip stays
    else showTip(found[0], [found[1]]);
  });
  document.addEventListener(leave, (e) => {
    if (tipFor && !tipFor.contains(e.relatedTarget) && e.target.closest?.("button, a[href], [role=button]") === tipFor) hideTip();
  });
}
document.addEventListener("click", (e) => { if (tipFor?.contains(e.target)) hideTip(); }, true);  // clicked: said what it does
/* The resolution asked of Unshackle for the series: its --quality, else its service's, else the defaults'
   (the highest listed); 1080 when none is set. What a service can give, unlike Sonarr's cutoff (a Blu-ray). */
function askedHeight() {
  const conf = S.config.series[current.tvdbId] || {};
  const q = [conf.options, S.config.service_defaults?.[conf.service]?.options, S.config.defaults].map((o) => o?.["--quality"]).find((v) => v != null && v !== "");
  const heights = String(q ?? "").match(/\d{3,4}/g)?.map(Number) || [];
  return heights.length ? Math.max(...heights) : 1080;
}
const belowAsked = (e) => e.file?.height && e.file.height < askedHeight() * 0.9;  // 1072 or 1040 lines are 1080p all the same
/* Once the service was asked, every aired episode says where it stands there: on it (maybe under another
   number, or better than the file on disk) or not; one not aired yet says nothing. */
function serviceBadge(e) {
  if (epAvail[e.id]) return [e.hasFile && belowAsked(e) ? upgradeBadge(e) : onBadge(e)];
  return e.hasFile || state(e) === "miss" ? [el("em", { className: "ep-off", textContent: `not on ${svcName(S.config.series[current.tvdbId]?.service)}` })] : [];
}
/* On disk under the resolution asked of the service, and on the service: worth downloading again. */
function upgradeBadge(e) {
  const svc = svcName(S.config.series[current.tvdbId]?.service);
  return hoverTip(el("em", { className: "ep-up", textContent: `upgrade on ${svc}` }), () => [
    el("b", { textContent: `${e.file.height}p here, ${askedHeight()}p asked of ${svc}` }),
    el("small", { textContent: `${svc} has this episode. Downloaded again, it replaces the file only if it ranks higher in Sonarr.` }),
  ]);
}
/* "on RMC+": what the service calls the episode, and its number there when not Sonarr's. */
function onBadge(e) {
  const svc = svcName(S.config.series[current.tvdbId]?.service), found = epAvail[e.id];
  const sxxeyy = `S${String(e.seasonNumber).padStart(2, "0")}E${String(e.episodeNumber).padStart(2, "0")}`;
  const other = found.service !== sxxeyy;  // under another number there: Download asks for it as found
  return hoverTip(el("em", { className: "ep-on" + (other ? " other" : ""), textContent: other ? `on ${svc} as ${found.service}` : `on ${svc}` }), () => [
    el("b", { textContent: found.name || `On ${svc}` }),
    el("small", {}, `${svc} lists it as `, el("code", { textContent: found.service })),
    ...(found.match === "title" ? [el("small", { className: "diff", textContent: `Found by its title, whatever its number: a Download asks for it as ${found.service}` })]
      : found.match === "absolute" ? [el("small", { className: "diff", textContent: `Numbered there from the series' first episode, as Sonarr's absolute number: a Download asks for it as ${found.service}` })]
      : found.service !== sxxeyy ? [el("small", { className: "diff", textContent: `Sonarr numbers it ${sxxeyy}: this series' numbering maps it there` })] : []),
  ]);
}
function checkLabel(text) {
  const check = $(".ep-check");
  if (!check) return;
  check.innerHTML = `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`;
  check.append(text, ...(epChecked ? [el("small", { textContent: `checked ${ago(epChecked)}` })] : []));
}
/* What the service has of the missing episodes: under each season, and in one line for them all with a way to pick them. */
function renderCatchUp() {
  const box = $("#ep-catchup");
  if (!box) return;
  const svc = svcName(S.config.series[current.tvdbId]?.service), missing = Object.values(epSeasons).flat().filter((e) => state(e) === "miss");
  const there = epAvail ? missing.filter((e) => epAvail[e.id]) : [];
  const upgrades = Object.values(epSeasons).flat().filter((e) => belowAsked(e) && epAvail?.[e.id] && !epBusy.has(e.id));
  document.querySelectorAll(".seasons button").forEach((b) => {
    const n = there.filter((e) => e.seasonNumber === Number(b.dataset.season)).length;
    b.querySelector(".avail")?.remove();
    b.classList.toggle("has-avail", n > 0);
    if (epAvail && missing.some((e) => e.seasonNumber === Number(b.dataset.season))) b.append(el("small", { className: "avail" + (n ? "" : " none"), textContent: n ? `${n} on ${svc}` : `none on ${svc}` }));
  });
  box.hidden = !epAvail || !(missing.length || upgrades.length);
  if (box.hidden) return;
  const bySeason = Object.entries(Object.groupBy(there, (e) => e.seasonNumber)).sort((a, b) => b[0] - a[0]);
  const free = there.filter((e) => !epBusy.has(e.id));
  const renumbered = there.filter((e) => epAvail[e.id].service !== `S${pad2(e.seasonNumber)}E${pad2(e.episodeNumber)}`).length;
  box.classList.toggle("none", !there.length && !upgrades.length);
  box.replaceChildren(
    el("div", {}, el("b", { textContent: !missing.length ? `${upgrades.length} episode${upgrades.length > 1 ? "s" : ""} could be better from ${svc}`
      : there.length ? `${svc} has ${there.length} of your ${missing.length} missing episode${missing.length > 1 ? "s" : ""}` : `${svc} has none of your ${missing.length} missing episode${missing.length > 1 ? "s" : ""}` }),
      ...(bySeason.length ? [el("small", { textContent: bySeason.map(([n, list]) => `${Number(n) === 0 ? "Specials" : `S${pad2(n)}`} (${list.length})`).join(" · ") })] : []),
      ...(upgrades.length && missing.length ? [el("small", { className: "renum", textContent: `${upgrades.length} on disk in a lower resolution than asked could be better from ${svc}` })] : []),
      ...(renumbered ? [el("small", { className: "renum", textContent: renumbered > 1 ? `${renumbered} under another number on ${svc}: Download handles them` : `1 under another number on ${svc}: Download handles it` })] : []),),
    el("div", { className: "catchup-acts" },
      ...(upgrades.length ? [el("button", { className: "btn small", type: "button", textContent: `Select ${upgrades.length} upgrade${upgrades.length > 1 ? "s" : ""}`,
        title: `On disk under the resolution asked of ${svc}, and on ${svc}`, onclick: () => { upgrades.forEach((e) => picked.add(e.id)); renderSeason(); updateEpBar(); } })] : []),
      ...(free.length ? [el("button", { className: "btn primary small", type: "button", textContent: `Select all ${free.length}`,
        onclick: () => { free.forEach((e) => picked.add(e.id)); renderSeason(); updateEpBar(); } })] : [])));
}
/* What the service lists of the series (unshackle's --list-titles): marked on the episodes, and for a download's preview. */
async function askService(s) {
  const conf = S.config.series[s.tvdbId], svc = svcName(conf.service), check = $(".ep-check");
  if (check) { check.disabled = true; check.replaceChildren(el("span", { className: "spinner", ariaHidden: "true" }), ` Asking ${svc}…`); }
  let r = null;
  try {
    r = await api("/api/probe", { method: "POST", signal: AbortSignal.timeout(120000), body: JSON.stringify({ show: conf, seriesId: s.id, tvdbId: s.tvdbId, title: s.title }) });
    if (current === s) { epAvail = r.available; epTitles = r.titles || []; epChecked = r.checked; localTitles(r.local_titles); $("#ep-avail").hidden = false; renderSeason(); renderPreview(); }
  } catch (e) { toast(e.message, true); }
  if (current === s && $(".ep-check")) { $(".ep-check").disabled = false; checkLabel(epAvail ? `Refresh what's on ${svc}` : `What's on ${svc}?`); }
  return r;
}
/* The season picked in sight in its strip: the strip scrolls sideways, never the page (a redraw every 20 s
   brought the page back up to the strip). */
function keepInStrip(b) {
  const strip = b?.parentElement;
  if (!strip) return;
  const r = b.getBoundingClientRect(), sr = strip.getBoundingClientRect();
  if (r.left < sr.left) strip.scrollLeft += r.left - sr.left - 8;
  else if (r.right > sr.right) strip.scrollLeft += r.right - sr.right + 8;
}
function renderSeason() {
  renderCatchUp();
  document.querySelectorAll(".seasons button").forEach((b) => b.setAttribute("aria-pressed", Number(b.dataset.season) === epSeason));
  keepInStrip(document.querySelector(".seasons [aria-pressed=true]"));
  const list = epSeasons[epSeason];
  const labels = { disk: "On disk", miss: "Missing", soon: "Not aired", busy: "Downloading now" };
  const norm = seasonNorm(list), shown = list.filter((e) => !hideOnDisk || !e.hasFile);
  const specs = shown.some((e) => e.file);  // file columns only where some episode has a file
  const cols = colsNow.filter((p) => p.on).map((p) => COLUMNS.find((c) => c.id === p.id)).filter((c) => specs || !c.file);
  $("#ep-list").style.setProperty("--cols", cols.map(() => "auto").join(" ") || "0px");
  $("#ep-list").style.setProperty("--cols-s", cols.map(() => "minmax(0, auto)").join(" ") || "0px");
  const rows = shown.map((e) => {
    const odd = e.file ? oddities(e.file, norm) : {};
    const run = epBusy.get(e.id);
    const box = el("input", { type: "checkbox", checked: picked.has(e.id), disabled: !!run, title: run ? "Downloading now" : "",
      onchange: () => { box.checked ? picked.add(e.id) : picked.delete(e.id); updateEpBar(); } });
    return el("li", {}, el("label", { className: "ep" },
      box,
      el("span", { className: "num", textContent: `E${String(e.episodeNumber).padStart(2, "0")}` }),
      el("span", { className: "t", title: fileTooltip(e) }, el("span", { className: "tt", textContent: e.title || "TBA" }),  // the title gives way, its badge doesn't
        ...(run ? [el("em", { className: "ep-busy", textContent: isQueued(run) ? "Queued" : afterTracks(run) || `Downloading ${Math.round(Number(run.live?.progress) || 0)}%` })] : []),
        ...(!run && epAvail ? serviceBadge(e) : []),
        ...(e.file ? [fileLine(e.file, odd)] : [])),
      ...cols.map((c, i) => { const cell = c.file && !e.file ? el("span", { className: "spec blank" }) : c.cell(e.file, odd, e); cell.classList.add("col"); cell.style.setProperty("--c", i + 3); return cell; }),
      stateIcon(state(e), labels[state(e)])));
  });
  const head = el("li", { className: "ep-head", ariaHidden: "true" }, ...["", "", "Title", ...cols.map((c) => c.label), ""]
    .map((t, i) => el("span", { className: i === 0 ? "cb" : i === 2 ? "th" : i > 2 && i < cols.length + 3 ? "col" : "", style: `--c: ${i}`, textContent: t })));
  const flagged = rows.some((r) => r.querySelector(".odd"));
  const outliers = list.filter(isOutlier).length;
  $("#ep-odd").hidden = !outliers;
  $("#ep-odd").textContent = `Outliers (${outliers})`;
  if ($("#ep-avail")) {
    const onService = list.filter((e) => state(e) === "miss" && epAvail?.[e.id]).length;
    $("#ep-avail").textContent = `On ${svcName(S.config.series[current.tvdbId]?.service)} (${onService})`;
    $("#ep-avail").disabled = !onService;
  }
  $("#ep-title").replaceChildren(el("span", { textContent: epSeason === 0 ? "Specials" : `Season ${epSeason}` }),
    el("small", { textContent: `${list.filter((e) => e.hasFile).length} of ${list.length} on disk` }),
    ...(flagged ? [el("small", { className: "odd-legend", textContent: "Orange: unlike most of this season (hover it for why)" })] : []));
  $("#ep-list").replaceChildren(...(rows.length ? [head, ...rows] : [el("li", { className: "none", textContent: "Every episode of this season is on disk." })]));
  epFit.observe($("#ep-list"));  // on a resize; now, for new rows or columns
  fitEpList($("#ep-list"));
  if (tipFor && !tipFor.isConnected) hideTip();  // its badge went with the redraw
}
/* unshackle.yaml gives the series' service no CDM: the default one decrypts it, and fails when it holds the other
   DRM (FRTV wants Widevine; a PlayReady default gets "Invalid challenge format"). Said on the series and before a download. */
function cdmMissing(tag) {
  const cdm = S.cdm || {};
  if (!tag || !Object.keys(cdm).length) return "";  // Unshackle unreachable: nothing to say
  if (Object.keys(cdm).some((k) => k !== "default" && k.toLowerCase() === tag.toLowerCase())) return "";
  if (!cdm.default) return "unshackle.yaml gives it no device and has no default one: its downloads are refused. "
    + "Pick one in Settings, CDM, or No CDM if it has no DRM.";
  return `unshackle.yaml gives it none, so the default one${cdm.default ? ` (${cdm.default})` : ""} decrypts it: a download fails `
    + "if the service wants the other DRM (Widevine or PlayReady). Set one under cdm: in Settings, unshackle.yaml.";
}
function cdmNote(tag) {
  const text = cdmMissing(tag);
  return text ? el("div", { className: "cd-note warn cdm-note" }, el("b", { textContent: `No CDM for ${svcName(tag)}` }), el("span", { textContent: text })) : "";
}
function updateEpBar() {
  $("#ep-bar").hidden = !picked.size;
  $("#ep-count").textContent = `${picked.size} selected`;
  const missing = current && cdmMissing(S.config.series[current.tvdbId]?.service);
  $("#ep-cdm").hidden = !missing;
  $("#ep-cdm").title = missing || "";
  $("#ep-go").textContent = "Download";
}
function askReplace(title) {
  const dialog = $("#replace-dialog");
  $("#replace-title").textContent = title;
  dialog.returnValue = "cancel";
  dialog.showModal();
  return new Promise((done) => dialog.addEventListener("close", () => done(dialog.returnValue || "cancel"), { once: true }));
}
$("#ep-clear").onclick = () => { picked.clear(); renderSeason(); updateEpBar(); };
/* The section itself moves into the dialog, filled from a copy of the series' numbering; back in place after. */
const NUMBERING = ["season_map", "season_offset", "season_offset_from", "episode_offset", "episode_map", "file_name", "join_parts", "parts", "episode_name"];
const advHome = document.createComment("numbering");
$("#ep-go-edit").onclick = () => {
  const conf = S.config.series[current.tvdbId];
  oneOff = structuredClone(Object.fromEntries(NUMBERING.filter((k) => conf[k] !== undefined).map((k) => [k, conf[k]])));
  $("#d-adv").before(advHome);
  $("#oneoff-slot").append($("#d-adv"));
  $("#d-adv").open = true;
  oneOff.join_parts = false;  // not ticked at first here: the preview says what joining would change
  for (const e of Object.values(epSeasons).flat().filter((e) => picked.has(e.id) && elsewhere(e)))
    oneOff.episode_map = { ...(oneOff.episode_map || {}), [`S${pad2(e.seasonNumber)}E${pad2(e.episodeNumber)}`]: epAvail[e.id].service };  // found by its title or absolute number
  fillAdv(oneOff, current.title);
  renderPreview();
  $("#oneoff-cdm").replaceChildren(cdmNote(conf.service));
  $("#oneoff-dialog").showModal();
  if (!epTitles) askService(current);  // the preview says what the service has
};
/* Sonarr's episode as the service numbers it, the rule sync.service_episode applies: the episode table, else offset and seasons. */
const pad2 = (n) => String(n).padStart(2, "0");
function serviceEpisode(conf, season, number) {
  const mapped = (conf.episode_map || {})[`S${pad2(season)}E${pad2(number)}`];
  if (mapped) return mapped;
  number += Number(conf.episode_offset || 0);
  season = (conf.season_map || {})[season] != null ? Number(conf.season_map[season])
    : season >= Number(conf.season_offset_from || 1) ? season + Number(conf.season_offset || 0) : season;
  return number >= 0 ? `S${pad2(season)}E${pad2(number)}` : null;
}
const dotted = (text) => String(text).replace(/[^\p{L}\p{N}_-]+/gu, ".").replace(/^\.+|\.+$/g, "");  // finalize()'s dots
/* Which of the service's episodes a Sonarr episode is, picked by name: written into the episode table. */
function pickServiceEpisode(e, sx, theirs, svc, missing) {
  const byKey = new Map();
  for (const t of epTitles) byKey.set(t.key, [...(byKey.get(t.key) || []), t]);
  const opts = [];
  for (const key of [...byKey.keys()].sort().reverse()) {  // newest first
    const items = byKey.get(key), parts = items.filter((t) => t.part).sort((a, b) => a.part - b.part);
    if (parts.length > 1) {
      const name = (items[0].name || "").replace(/\s*\(\d+\s*\/\s*\d+\)\s*$/, "");
      opts.push([key, `${key} · ${name ? `${name} · ` : ""}parts ${parts.map((t) => t.part).join(" + ")}`]);
      for (const t of parts) opts.push([`${key}.${t.part}`, `${key}.${t.part} · ${t.name || `part ${t.part}`}`]);
    } else opts.push([key, `${key}${items[0].name ? ` · ${items[0].name}` : ""}`]);
  }
  if (!opts.some(([v]) => v === (theirs || ""))) opts.unshift([theirs || "", theirs ? `${theirs} · not on ${svc}` : "Nothing: before the first episode"]);
  const titled = elsewhere(e) && epAvail[e.id].service;
  if (titled) opts.forEach((o) => { if (o[0] === titled) o[1] += " ✓"; });  // the one with the same title, in the list too
  const select = el("select", { className: missing ? "bad" : "", ariaLabel: `${sx} on ${svc}`, onchange: () => {
    const map = { ...(oneOff.episode_map || {}) };
    const bare = serviceEpisode({ ...oneOff, episode_map: {} }, e.seasonNumber, e.episodeNumber);  // what the rules give anyway
    if (!select.value || select.value === bare) delete map[sx]; else map[sx] = select.value;
    oneOff.episode_map = map;
    $("#d-epmap").value = Object.entries(map).map(([a, b]) => `${a}=${b}`).join(", ");
    renderPreview();
  } }, ...opts.map(([value, text]) => el("option", { value, textContent: text })));
  select.value = theirs || "";
  return select;
}
/* The dialog's preview, per episode picked: what is asked of the service, the file it becomes, its parts. */
function renderPreview() {
  const conf = oneOff;
  if (!conf || !current) return;
  const svc = svcName(S.config.series[current.tvdbId]?.service), name = conf.file_name || current.title;
  const eps = Object.values(epSeasons).flat().filter((e) => picked.has(e.id))
    .sort((a, b) => a.seasonNumber - b.seasonNumber || a.episodeNumber - b.episodeNumber);
  const rows = eps.map((e) => {
    const sx = `S${pad2(e.seasonNumber)}E${pad2(e.episodeNumber)}`, theirs = serviceEpisode(conf, e.seasonNumber, e.episodeNumber);
    const [plain, part] = (theirs || "").split(".");
    const same = epTitles?.filter((t) => t.key === plain) || [];
    const found = !epTitles ? null : same.filter((t) => !part || String(t.part) === part);
    const parts = same.filter((t) => t.part).length;
    const ask = epTitles ? el("span", { className: "pick" }, pickServiceEpisode(e, sx, theirs, svc, found && !found.length), agreement(e, theirs, found, svc))
      : !theirs ? el("span", { className: "bad", textContent: `Before ${svc}'s first episode: nothing to ask for` })
      : el("span", {}, el("code", { textContent: theirs }), " · checking…");
    const joined = parts > 1 && !part && conf.join_parts !== false;
    const dropName = conf.episode_name === "always" || ((conf.episode_name || "joined") === "joined" && joined);
    const epName = found?.[0]?.name && !(parts > 1 && !part && joined) ? found[0].name : "";
    const notes = [];
    if (parts > 1 && !part) notes.push(conf.join_parts === false
      ? [`${parts} parts on ${svc} and joining is off: it stops there, unless each part is mapped (${sx}=${plain}.1)`, "bad"]
      : [`${parts} parts on ${svc}: joined into one file`, ""]);
    if (conf.parts) notes.push([`Waits for ${conf.parts} parts before importing`, ""]);
    const named = e.title && e.title !== "TBA" ? e.title : "";
    return el("li", {}, el("div", { className: "ours" }, el("b", { textContent: sx }), ...(named ? [el("small", { textContent: named, title: named })] : [])),
      el("span", { className: "arrow", textContent: "→" }), ask,
      el("div", { className: "side" }, el("small", { className: "file", title: "The file it becomes",
        textContent: `${dotted(name)}.${sx}${!dropName && epName ? `.${dotted(epName)}` : ""}….mkv` }),
        ...notes.map(([text, cls]) => el("small", { className: cls, textContent: text }))));
  });
  $("#oneoff-preview").replaceChildren(...(rows.length ? [el("li", { className: "head" }, el("span", { textContent: "In Sonarr" }), el("span", { textContent: `On ${svc}` }))] : []), ...rows);
}
/* Whether the service's episode picked is Sonarr's: the same title, another one, or none there. */
const plainTitle = (t) => (t || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/\(\s*\d+\s*\/\s*\d+\s*\)/g, "").replace(/[^a-z0-9]+/g, " ").trim();
/* Found on the service under another number than the series' numbering gives: by its title, or by Sonarr's
   absolute number (a service numbering an anime from its first episode). A Download asks for it as found. */
const elsewhere = (e) => ["title", "absolute"].includes(epAvail?.[e.id]?.match);
function agreement(e, theirs, found, svc) {
  if (!theirs) return "";
  if (!found.length) return el("span", { className: "st bad", textContent: `Not on ${svc}` });
  const ours = plainTitle(e.title), other = plainTitle(found[0].name), generic = /^(tba|tbd|episode( \d+)?)$/;
  const titled = epAvail?.[e.id]?.match === "title" && epAvail[e.id].service === theirs;
  if (titled || (ours && other && (ours === other || ours.includes(other) || other.includes(ours))))
    return el("span", { className: "st ok", textContent: "✓ Same title" });
  if (!ours || !other || generic.test(ours) || generic.test(other)) return "";
  return el("span", { className: "st warn", textContent: "Other title", title: `In Sonarr: ${e.title}\nOn ${svc}: ${found[0].name}` });
}
function closeOneOff() {  // at once, not on the dialog's close event: that comes later, after a quick reopening
  if (!oneOff) return;
  oneOff = null;
  advHome.replaceWith($("#d-adv"));
  if (current) fillAdv(S.config.series[current.tvdbId], current.title);
  $("#oneoff-dialog").close();
}
$("#oneoff-cancel").onclick = closeOneOff;
$("#oneoff-x").onclick = closeOneOff;
$("#oneoff-dialog").addEventListener("click", (e) => {  // a click outside the window (on the backdrop) closes it
  const r = $("#oneoff-dialog").getBoundingClientRect();
  if (e.target === $("#oneoff-dialog") && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) closeOneOff();
});
$("#oneoff-go").onclick = async () => { const numbering = oneOff; closeOneOff(); await startDownload(numbering); };
$("#oneoff-dialog").addEventListener("close", () => { if (!$("#oneoff-dialog").open) closeOneOff(); });  // Escape; not a late one after reopening
$("#ep-go").onclick = () => startDownload();
async function startDownload(numbering = null) {
  const conf = S.config.series[current.tvdbId];
  if (!conf.service || !conf.title) return toast("Pick a service and the series URL first, then save.", true);
  if (!$("#savebar").hidden) return toast("Save your changes first: the download reads the saved settings.", true);
  let replace = false;
  const onDisk = Object.values(epSeasons).flat().filter((e) => picked.has(e.id) && e.hasFile).length;
  if (onDisk) {
    const choice = await askReplace(onDisk === picked.size
      ? `${onDisk > 1 ? `These ${onDisk} episodes are` : "This episode is"} already on disk`
      : onDisk > 1 ? `${onDisk} of these ${picked.size} episodes are already on disk` : `1 of these ${picked.size} episodes is already on disk`);
    if (choice === "cancel") return;
    replace = choice === "force";
  }
  if (!numbering) {  // episodes found by their title or absolute number where the series' numbering says otherwise: asked as found, not saved
    const byTitle = Object.values(epSeasons).flat().filter((e) => picked.has(e.id) && elsewhere(e)
      && epAvail[e.id].service !== serviceEpisode(conf, e.seasonNumber, e.episodeNumber));
    if (byTitle.length) {
      numbering = structuredClone(Object.fromEntries(NUMBERING.filter((k) => conf[k] !== undefined).map((k) => [k, conf[k]])));
      numbering.episode_map = { ...(numbering.episode_map || {}),
        ...Object.fromEntries(byTitle.map((e) => [`S${pad2(e.seasonNumber)}E${pad2(e.episodeNumber)}`, epAvail[e.id].service])) };
      toast(`Under the service's own number: ${byTitle.map((e) => `S${pad2(e.seasonNumber)}E${pad2(e.episodeNumber)} as ${epAvail[e.id].service}`).join(", ")}`);
    }
  }
  try {
    await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: [...picked], replace, ...(numbering ? { numbering } : {}) }) });
    toast(`Downloading ${picked.size} episode${picked.size > 1 ? "s" : ""}: follow it in Activity`);
    $("#ep-clear").click();
    const s = current;
    setTimeout(() => { if (s && current === s) refreshEpBusy(s); }, 1500);
  } catch (e) { toast(e.message, true); }
}
$("#backdrop").onclick = () => closeDrawer();
$("#d-back").onclick = () => closeDrawer();
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !document.querySelector("dialog[open]")) closeDrawer(); });  // a dialog's Escape closes it alone

/* Settings' shortcuts: straight to a card, opened if folded, the page's address left as it is. */
$(".ds-nav").onclick = (e) => {
  const a = e.target.closest("a[data-to]");
  if (!a) return;
  e.preventDefault();
  const card = $(`#${a.dataset.to}`);
  if (card.tagName === "DETAILS") card.open = true;
  card.scrollIntoView({ behavior: "smooth", block: "start" });
};
new ResizeObserver(([e]) => document.documentElement.style.setProperty("--d-head", `${Math.round(e.target.getBoundingClientRect().height)}px`)).observe($(".d-head"));
