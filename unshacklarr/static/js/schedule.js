/* Schedule: the late episodes, then Sonarr's calendar as an endless list or a month grid.
   Episodes come from /api/calendar in chunks; the filter and views only re-render them. */
const CHUNK_DAYS = 14, LIST_DAYS_MAX = 365;
const cal = { byDay: new Map(), listFrom: null, listUntil: null, loading: false, done: false, month: null, pick: null, fetched: new Set() };

async function loadSchedule() {
  loadLate();
  renderHiddenButton();
  cal.byDay.clear(); cal.fetched.clear();
  cal.listFrom = cal.listUntil = startOfDay(new Date()); cal.done = false;
  cal.month ??= new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  $("#sched-upcoming").replaceChildren();
  if (schedView() === "calendar") await loadMonth(); else await loadMore();
}

async function loadLate() {
  let data;
  try { data = await api("/api/schedule"); }
  catch (e) { $("#next-sync").replaceChildren(failed(`Could not load the schedule: ${e.message}`, () => loadSchedule())); return; }
  const next = new Date(data.nextSync);
  $("#next-sync").replaceChildren("Next automatic sync at ", el("b", { textContent: time(next) }), " · in ", el("b", { textContent: until(next) }));
  const newestFirst = [...data.missing].sort((a, b) => (b.airDateUtc || "").localeCompare(a.airDateUtc || ""));
  const names = newestFirst.map((d) => `${d.series} ${d.sxxeyy}`);
  $("#late-n").textContent = `${names.length} late`;
  const who = `${names.slice(0, 2).join(", ")}${names.length > 2 ? ` and ${names.length - 2} more` : ""}`;
  $("#late-sum").textContent = names.length > 1 ? `${who} aired but are not on the service yet. They are tried again at every sync for 14 days.`
    : `${who} aired but is not on the service yet. It is tried again at every sync for 14 days.`;
  $("#sched-missing").replaceChildren(...newestFirst.map((d) => schedRow({ ...d, fromLate: true }, d.airDateUtc ? day(new Date(d.airDateUtc)) : "")));
  $("#sched-late").hidden = !newestFirst.length;
  $("#late-toggle").onclick = () => {
    const open = $("#sched-missing").hidden;
    $("#sched-missing").hidden = !open;
    $("#late-toggle").textContent = open ? "Hide" : "Show";
    $("#late-toggle").setAttribute("aria-expanded", open);
  };
}

const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const schedView = () => document.querySelector("[data-view][aria-pressed=true]").dataset.view;
const schedScope = () => document.querySelector(".sched-filter [aria-pressed=true]").dataset.scope;
const isHidden = (d) => S.config.hidden_series.includes(d.tvdbId);
const inScope = (d) => !isHidden(d) && ({ all: true, mine: d.managed, other: !d.managed })[schedScope()];

/* Hidden series: out of the calendar (not out of the downloads), saved at once. */
async function hideSeries(d, hidden, quiet = false) {
  try { S.config.hidden_series = await api("/api/hidden", { method: "POST", body: JSON.stringify({ tvdbId: d.tvdbId, hidden }) }); }
  catch (e) { return toast(`Could not ${hidden ? "hide" : "show"} ${d.series}: ${e.message}`, true); }
  if (!quiet) toast(hidden ? `${d.series} hidden from the schedule` : `${d.series} is back in the schedule`, false,
    ["Undo", () => hideSeries(d, !hidden)]);
  renderHideToggle();
  renderWall();  // its Hidden filter and badges
  if (!$("#tab-schedule").hidden) rerenderSchedule(); else renderHiddenButton();
}
/* The series page's own switch, saved at once like the Schedule's. */
const EYE_OPEN = '<path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8Z"/><circle cx="12" cy="12" r="3"/>', EYE_SHUT = '<path d="M9.9 4.2A10.9 10.9 0 0 1 12 4c7 0 10 8 10 8a17.6 17.6 0 0 1-2.2 3.3M6.6 6.6C3.6 8.6 2 12 2 12s3 8 10 8a10 10 0 0 0 5.4-1.6M14.1 14.1a3 3 0 0 1-4.2-4.2M2 2l20 20"/>';
function renderHideToggle() {
  if (!current) return;
  const hidden = isHidden(current), b = $("#d-hide");
  b.setAttribute("aria-pressed", hidden);
  b.title = hidden ? "Hidden from the schedule: show it again" : "Hide this series from the schedule";
  b.ariaLabel = b.title;
  b.querySelector("svg").innerHTML = hidden ? EYE_SHUT : EYE_OPEN;
  b.querySelector(".long").textContent = hidden ? "Hidden in schedule" : "In schedule";
}
$("#d-hide").onclick = () => hideSeries({ tvdbId: current.tvdbId, series: current.title }, !isHidden(current));

/* The counter by the filters, and its list: each row keeps its place while the list is open,
   so a switch flipped by mistake flips back. */
function renderHiddenButton() {
  const n = S.config.hidden_series.length, b = $("#hidden-open");
  b.hidden = !n;
  b.querySelector("span").textContent = n;
  b.title = b.ariaLabel = `${n} series hidden from the schedule`;
}
$("#hidden-open").onclick = () => {
  const rows = S.config.hidden_series.map((tvdbId) => {
    const series = S.series.find((x) => x.tvdbId === tvdbId), name = series?.title || `TVDB ${tvdbId}`;
    const input = el("input", { type: "checkbox", checked: false, ariaLabel: `Show ${name} in the schedule` });
    const li = el("li", { className: "off" }, el("img", { alt: "", src: series?.poster || "" }), el("span", { className: "n", textContent: name }),
      el("label", { className: "switch" }, "In schedule", input, el("i")));
    input.onchange = async () => {
      li.classList.toggle("off", !input.checked);
      await hideSeries({ tvdbId, series: name }, !input.checked, true);
      input.checked = !S.config.hidden_series.includes(tvdbId);  // as saved, should the save fail
      li.classList.toggle("off", !input.checked);
    };
    return li;
  }).sort((a, b) => a.textContent.localeCompare(b.textContent));
  $("#hidden-rows").replaceChildren(...rows);
  $("#hidden-dialog").showModal();
};
function rerenderSchedule() {
  renderHiddenButton();
  if (schedView() === "calendar") renderMonth(); else { renderList(); if (moreInSight()) loadMore(); }
}
function hideButton(d) {  // hidden series are not listed here: Series, Hidden brings them back
  const b = el("button", { className: "icon-hide", title: "Hide this series from the schedule (Series, Hidden brings it back)", ariaLabel: `Hide ${d.series} from the schedule`,
    onclick: (e) => { e.preventDefault(); e.stopPropagation(); hideSeries(d, true); } });
  b.innerHTML = `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M9.9 4.2A10.9 10.9 0 0 1 12 4c7 0 10 8 10 8a17.6 17.6 0 0 1-2.2 3.3M6.6 6.6C3.6 8.6 2 12 2 12s3 8 10 8a10 10 0 0 0 5.4-1.6M14.1 14.1a3 3 0 0 1-4.2-4.2M2 2l20 20"/></svg>`;
  return b;
}

/* Networks (FX, Apple TV+…) by TMDB id, fetched for the series a calendar chunk brings. */
const networksById = {};
async function fetchNetworks(episodes) {
  const ids = [...new Set(episodes.map((d) => S.series.find((x) => x.tvdbId === d.tvdbId)?.tmdbId).filter((i) => i && !(i in networksById)))];
  if (!ids.length) return;
  ids.forEach((i) => networksById[i] = null);  // asked: don't ask twice
  try { Object.assign(networksById, await api(`/api/networks?tmdb=${ids.join(",")}`)); }
  catch { return; }  // just no logos this time
  if (schedView() === "calendar") renderMonth(); else renderList();
}
function networkBadge(d, extra = "") {
  const tmdbId = S.series.find((x) => x.tvdbId === d.tvdbId)?.tmdbId;
  const net = networksById[tmdbId];
  if (!net) return "";
  // A channel of your country (M6 in FR) is watched there anyway, on air or its replay: no warning then
  const home = net.network_country && net.network_country === net.country;
  const networkTip = home ? `On ${net.name}, a channel in ${net.country}. TMDB lists no streaming service for it` : `Network: ${net.name}`;
  const network = !net.name ? "" : net.logo
    ? el("span", { className: "net", title: networkTip }, el("img", { src: net.logo, alt: net.name, loading: "lazy" }))
    : el("span", { className: "net text", title: networkTip, textContent: net.name });
  // Where it streams in the first "Where to watch" country; else a warning, but not for a channel of that
  // country nor for a series Unshackle already downloads (its service is what counts then)
  const local = net.providers.length
    ? el("span", { className: "prov", title: `In ${net.country} on ${net.providers.map((p) => p.name).join(", ")}` },
        ...net.providers.slice(0, 3).map((p) => p.logo ? el("img", { src: p.logo, alt: p.name, loading: "lazy" })
          : el("i", { textContent: p.name.slice(0, 2) })))
    : home || d.managed ? ""
    : el("span", { className: "chip nofr", textContent: `No streaming in ${net.country}`,
        title: `TMDB (from JustWatch) lists no streaming service in ${net.country} for this series` });
  // The French services say where to watch it; the original network only helps when there are none
  // (which also folds a network that is its own service, like Canal+, into one logo).
  return el("span", { className: `badges ${extra}` }, ...(net.providers.length ? [local] : [network, local]));
}

/* Fetch [from, from + days) once; episodes land in cal.byDay by local day. */
async function fetchDays(from, days) {
  const key = `${isoDay(from)}+${days}`;
  if (cal.fetched.has(key)) return;
  const eps = await api(`/api/calendar?start=${isoDay(from)}&days=${days}`);
  cal.fetched.add(key);
  for (let i = 0; i < days; i++) cal.byDay.set(isoDay(addDays(from, i)), []);
  for (const d of eps) {  // on the day it airs; one out days before (a platform ahead of the channel) on the day it's tried
    const aired = isoDay(new Date(d.airDateUtc)), out = d.release && new Date(d.release) < new Date(d.airDateUtc) ? isoDay(new Date(d.release)) : aired;
    (cal.byDay.get(out) || cal.byDay.get(aired))?.push(d);
  }
  fetchNetworks(eps);
}

/* List view: 14 more days each time the bottom comes into sight. */
async function loadMore() {
  if (cal.loading || cal.done || !cal.listUntil) return;  // not before loadSchedule() set the start
  cal.loading = true;
  $("#sched-more").replaceChildren(loading("Loading more of the calendar…"));
  try {
    await fetchDays(cal.listUntil, CHUNK_DAYS);
    cal.listUntil = addDays(cal.listUntil, CHUNK_DAYS);
    cal.done = cal.listUntil - cal.listFrom >= LIST_DAYS_MAX * 86400000;
    renderList();
    $("#sched-more").replaceChildren(cal.done ? el("p", { className: "muted", textContent: "That's a year ahead: the end of the list." }) : "");
  } catch (e) {
    cal.loading = false;
    $("#sched-more").replaceChildren(failed(`Could not load the calendar: ${e.message}`, loadMore));
    return;  // never chain after a failure: that looped without end
  }
  cal.loading = false;
  if (!cal.done && moreInSight()) setTimeout(loadMore, 0);  // a short page, or a filter that hides a lot: keep going
}
const moreInSight = () => !$("#sched-list").hidden && $("#sched-more").getBoundingClientRect().top < innerHeight + 400;
new IntersectionObserver((entries) => { if (entries[0].isIntersecting && schedView() === "list") loadMore(); },
  { rootMargin: "400px" }).observe($("#sched-more"));

function renderList() {
  const groups = [];
  for (let d = cal.listFrom; d < cal.listUntil; d = addDays(d, 1)) {
    const list = (cal.byDay.get(isoDay(d)) || []).filter(inScope).sort(byWhen);
    if (!list.length) continue;
    const mine = list.filter((e) => e.managed).length, isToday = isoDay(d) === isoDay(new Date());
    // the full date once; "Today" and the like keep it beside them
    const rel = day(d), full = d.toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long" });
    const named = ["Today", "Tomorrow", "Yesterday"].map((x) => tr(x)).includes(rel);
    groups.push(el("div", {}, el("div", { className: "sched-dayh" + (isToday ? " today" : "") },
      el("b", { textContent: named ? rel : full }), named ? el("small", { textContent: full }) : "",
      el("span", { className: "n", textContent: `${list.length} episode${list.length > 1 ? "s" : ""}${!mine ? "" : mine === list.length ? (mine > 1 ? ", all managed" : ", managed") : ` · ${mine} managed`}` })),
      el("div", { className: "sched-list" }, ...list.map((e) => schedRow(e, whenText(e))))));
  }
  $("#sched-upcoming").replaceChildren(...(groups.length ? groups
    : [el("p", { className: "muted", textContent: "Nothing matches in the days loaded so far." })]));
}
/* When something happens to an episode: its release time for a series that has one (Unshackle tries then), else airing. */
const whenOf = (d) => d.release || d.airDateUtc || "";
const byWhen = (a, b) => new Date(whenOf(a)) - new Date(whenOf(b));  // instants: release times carry an offset, airings a Z
const whenText = (d) => {
  const aired = new Date(d.airDateUtc);
  if (d.release) {
    const at = new Date(d.release), other = isoDay(at) !== isoDay(aired), early = other && at < aired;
    // out days before it airs: listed on the day it's tried, so the airing says its day; the day after: the try does
    return el("span", { className: "tm", title: at > Date.now() ? `Tries in ${until(at)}` : "" },
      `${other && !early ? `${day(at)} ` : ""}${time(at)}`, el("small", { textContent: `airs ${early ? `${day(aired)} ` : ""}${time(aired)}` }));
  }
  // "next sync": tried at the first sync after airing; already on disk, nothing will be tried: when it airs
  return el("span", { className: "tm" }, time(aired), el("small", { className: "m", textContent: d.managed && !d.hasFile ? "next sync" : "airs" }));
};
/* Where one of your episodes stands, in a word and a colour. */
function schedStatus(d) {
  if (d.hasFile) return ["ok", d.managed ? "Downloaded" : "On disk"];
  if (!d.managed) return null;
  if (d.waitingImport) return ["wait", "Waiting for Sonarr"];
  if (d.fromLate) return ["late", `Late · not on ${d.service} yet`];
  if (d.firstTry && new Date(d.firstTry) > Date.now()) return null;  // the time says it: "try 09:00"
  return new Date(d.airDateUtc) < Date.now() ? ["try", "Tries at the next sync"] : null;
}
const statusPill = (d, extra = "") => { const st = schedStatus(d); return st ? el("span", { className: `sched-st ${st[0]} ${extra}` }, el("i"), st[1]) : ""; };

/* Calendar view: a month grid from Monday, six weeks; phones show counts and a day's list below. */
async function loadMonth() {
  const first = cal.month;
  const gridStart = addDays(first, -((first.getDay() + 6) % 7));
  $("#cal-month").textContent = first.toLocaleDateString(LOCALE, { month: "long", year: "numeric" });
  $("#cal-grid").replaceChildren(loading("Loading the month…"));
  try { await fetchDays(gridStart, 42); }
  catch (e) { $("#cal-grid").replaceChildren(failed(`Could not load the calendar: ${e.message}`, loadMonth)); return; }
  renderMonth();
}
function renderMonth() {
  const first = cal.month;
  const gridStart = addDays(first, -((first.getDay() + 6) % 7));
  const today = isoDay(new Date());
  const heads = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => el("div", { className: "cal-head", textContent: d }));
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const date = addDays(gridStart, i), key = isoDay(date);
    const list = (cal.byDay.get(key) || []).filter(inScope);
    const mine = list.filter((d) => d.managed).length;
    // the three shown: yours first, never in "+N more"; then in the order they air
    const shown = [...list].sort((a, b) => b.managed - a.managed).slice(0, 3).sort(byWhen);
    const cell = el("button", {
      className: "cal-cell" + (date.getMonth() !== first.getMonth() ? " out" : "") + (key === today ? " today" : "") + (key === cal.pick ? " picked" : ""),
      ariaLabel: `${date.toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long" })}: ${list.length} episode${list.length === 1 ? "" : "s"}`,
      onclick: () => { cal.pick = key; renderMonth(); $("#cal-day").scrollIntoView({ behavior: "smooth", block: "nearest" }); },
    },
      el("span", { className: "cal-date", textContent: date.getDate() }),
      el("span", { className: "cal-count" }, ...(mine ? [el("i", { className: "mine", textContent: mine })] : []),
        ...(list.length - mine ? [el("i", { textContent: list.length - mine })] : [])),
      el("span", { className: "cal-eps" }, ...shown.map((d) => el("span", { className: "cal-ep" + (d.managed ? " mine" : "") },
        el("b", { textContent: time(new Date(whenOf(d))) }), ` ${d.series}`)),
        ...(list.length > 3 ? [el("span", { className: "cal-ep more", textContent: `+${list.length - 3} more` })] : [])));
    cells.push(cell);
  }
  $("#cal-grid").replaceChildren(...heads, ...cells);
  const picked = cal.pick && (cal.byDay.get(cal.pick) || []).filter(inScope);
  $("#cal-day").replaceChildren(...(picked ? [
    el("h3", { className: "sched-day", textContent: day(new Date(cal.pick + "T12:00")) }),
    picked.length ? el("div", { className: "sched-list" }, ...picked.sort(byWhen).map((d) => schedRow(d, whenText(d))))
      : el("p", { className: "muted", textContent: "Nothing that day." })] : []));
}
function moveMonth(step) {
  cal.month = step ? new Date(cal.month.getFullYear(), cal.month.getMonth() + step, 1)
    : new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  cal.pick = step ? null : isoDay(new Date());
  loadMonth();
}
$("#cal-prev").onclick = () => moveMonth(-1);
$("#cal-next").onclick = () => moveMonth(1);
$("#cal-today").onclick = () => moveMonth(0);

function schedRow(d, when) {
  const quote = (a) => /^[\w@%+=:,./-]+$/.test(a) ? a : `'${a.replaceAll("'", "'\\''")}'`;
  const series = S.series.find((x) => x.tvdbId === d.tvdbId);
  const thumb = el("img", { className: "thumb", alt: "", loading: "lazy", src: series?.poster || "" });
  const hideCtl = d.fromLate ? "" : hideButton(d);
  const what = el("span", { className: "what" }, el("b", { textContent: `${d.series} ${d.sxxeyy}` }),
    el("small", { textContent: d.title && d.title !== "TBA" ? d.title : "" }), d.managed ? statusPill(d, "m-only") : "");
  if (!d.managed) {  // not downloaded by Unshackle: one tap to its settings, to add it
    return el("div", { className: "sched-row other" }, el("div", { className: "row-main" },
      thumb, el("span", { className: "when" }, when, networkBadge(d, "in-when")), what,
      el("span", { className: "row-actions" }, networkBadge(d), hideCtl,
        d.hasFile ? statusPill(d)
          : el("button", { className: "btn small add", textContent: "+ Add", ariaLabel: `Add ${d.series} to Unshackle`,
              onclick: () => series && openDrawer(series, "settings") }))));
  }
  const command = d.command ? d.command.map(quote).join(" ") : "";
  const onService = d.serviceEpisode && d.serviceEpisode !== d.sxxeyy ? d.serviceEpisode : "";
  const facts = el("div", { className: "facts" },
    ...(d.firstTry ? [el("span", {}, new Date(d.firstTry) > Date.now() ? "Tries " : "Tried ", el("b", { textContent: `${day(new Date(d.firstTry))} ${time(new Date(d.firstTry))}` }),
      series && S.config.series[d.tvdbId]?.release_time ? `, then every ${S.config.settings?.burst_every_seconds ?? 30} s for ${S.config.settings?.burst_minutes ?? 10} min` : ", at the sync")] : []),
    ...(onService ? [el("span", {}, "Asked for as ", el("b", { textContent: onService }))] : []),
    ...(d.waitingImport ? [el("span", { textContent: "Already downloaded: waiting for Sonarr's import" })] : []));
  const copy = el("button", { className: "btn small", textContent: "Copy", onclick: async () => {
    try { await navigator.clipboard.writeText(command); toast("Command copied"); } catch { toast("Could not copy: select the command instead", true); }
  } });
  const now = el("button", { className: "btn small primary", textContent: "Download now", onclick: async () => {
    now.disabled = true;
    try { await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: [d.episodeId] }) }); toast(`${d.series} ${d.sxxeyy}: downloading`); }
    catch (e) { now.disabled = false; toast(e.message, true); }
  } });
  return el("details", { className: "sched-row" },
    el("summary", {}, thumb, el("span", { className: "when" }, when, networkBadge(d, "in-when")), what,
      el("span", { className: "row-actions" }, networkBadge(d), statusPill(d), hideCtl, el("span", { className: "svc", textContent: d.service }))),
    el("div", { className: "sched-xp" }, facts,
      command ? el("div", { className: "cmd" }, el("code", { textContent: command }), copy)
        : el("p", { className: "muted", style: "margin:0", textContent: "Skipped: the episode offset puts it before the service's first episode." }),
      el("div", { className: "btns" }, ...(d.episodeId && !d.hasFile && command ? [now] : []),
        d.run ? el("button", { className: "btn small", textContent: "See in Activity", title: "Open its last download: its steps and output",
          onclick: () => { showTab("log"); pickRun(d.run); } }) : "",
        series ? el("button", { className: "btn small", textContent: "Open the series", onclick: () => openDrawer(series) }) : "")));
}

const LOCALE = LANG === "en" ? "en-GB" : LANG;  // English: a 24-hour clock, the day before the month
const time = (d) => isNaN(d) ? "" : d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
function day(d) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((new Date(d).setHours(0, 0, 0, 0) - today) / 86400000);
  if (diff >= -1 && diff <= 1) return tr(["Yesterday", "Today", "Tomorrow"][diff + 1]);  // translated here: often glued to a time
  return d.toLocaleDateString(LOCALE, { weekday: "short", day: "numeric", month: "short",
        year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}
function until(d) {
  const min = Math.max(0, Math.round((d - Date.now()) / 60000));
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${String(min % 60).padStart(2, "0")}`;
}
$("#sched-refresh").onclick = () => loadSchedule();
document.querySelectorAll(".sched-filter .pill").forEach((b) => b.onclick = () => {
  document.querySelectorAll(".sched-filter .pill").forEach((x) => x.setAttribute("aria-pressed", x === b));
  if (schedView() === "calendar") renderMonth(); else { renderList(); if (moreInSight()) loadMore(); }
});
document.querySelectorAll("[data-view]").forEach((b) => b.onclick = () => {
  document.querySelectorAll("[data-view]").forEach((x) => x.setAttribute("aria-pressed", x === b));
  const calendar = b.dataset.view === "calendar";
  $("#sched-list").hidden = calendar;
  $("#sched-cal").hidden = !calendar;
  if (calendar) loadMonth(); else if (cal.listUntil) { renderList(); if (moreInSight()) loadMore(); } else loadMore();
});

/* Health: the header's lights, from the server's background check; details in Settings. */
let healthTimer = null;
/* Downloads going on: counted on the menu's Activity, every 5 s while the page is in sight. */
let busyStarted = false;
/* Back in sight: the lights, the bell and the sync state at once, not at their next round. */
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && busyStarted) { refreshHealth(); refreshInbox(); refreshLog(); }
});
async function refreshBusy() {
  if (!document.hidden) {
    try {
      const b = await api("/api/busy"), badge = $("#nav-busy");
      badge.hidden = !(b.running || b.queued);
      badge.textContent = b.running || b.queued;
      badge.classList.toggle("queued", !b.running);
      badge.title = [b.running && `${b.running} downloading`, b.queued && `${b.queued} queued`].filter(Boolean).join(", ");
    } catch { /* offline a moment: the next round */ }
  }
  setTimeout(refreshBusy, 5000);
}
async function refreshHealth(full = false) {
  if (document.hidden && !full) { clearTimeout(healthTimer); healthTimer = setTimeout(refreshHealth, 60000); return; }  // out of sight
  try { renderHealth(await api(`/api/status${full ? "?full=1" : ""}`)); } catch { /* the lights keep their last state */ }
  clearTimeout(healthTimer);  // after the await: calls that overlapped leave one loop, not one each
  healthTimer = setTimeout(refreshHealth, 60000);
}
function renderHealth(h) {
  for (const [name, label] of [["unshackle", "Unshackle"], ["sonarr", "Sonarr"]]) {
    const st = h[name] || {}, b = document.querySelector(`[data-health=${name}]`);
    b.querySelector(".dot").className = "dot " + (st.ok ? (st.latest ? "warn" : "ok") : st.ok === false ? "down" : "");
    b.title = st.ok ? `${label} ${st.version || ""} is running${st.latest ? ` (${st.latest} is out)` : ""}`
      : st.ok === false ? st.error || `${label} is unreachable` : `Checking ${label}…`;
    b.ariaLabel = b.title;
  }
  if (h.unshackle && ("tools" in h.unshackle || h.unshackle.ok === false)) renderUnshackleStatus(h.unshackle);  // down: its actions and why
  renderSonarrState(h.sonarr);
  renderUnshackleState(h.unshackle);
  paintServers(h.servers);
}
function renderUnshackleState(st = S.health?.unshackle || {}, tested = false) {
  const set = S.config.settings || {}, box = $("#ux-state");
  if (!box) return;
  const local = (st.mode || set.unshackle_mode || UNSHACKLE_MODE) === "local";
  $("#ux-where").textContent = `${local ? "Local" : "Remote"} · ${st.address || (local ? set.unshackle_command || "unshackle" : set.unshackle_url || "")}`;
  box.className = `sx-state ${st.ok ? "ok" : st.ok === false ? "down" : ""}`;
  box.querySelector("b").textContent = st.ok ? `${local ? "Running" : "Connected"}${st.version ? ` · Unshackle ${st.version}` : ""}` : st.ok === false ? (local ? "Not running" : "Unreachable") : "Checking…";
  const since = st.since ? `since ${day(new Date(st.since))} ${time(new Date(st.since))}` : "";
  box.querySelector("small").textContent = st.ok ? [st.latest && `${st.latest} is out`, tested ? "Tested just now" : st.services && `${st.services} services`, since]
    .filter(Boolean).join(" · ") : st.ok === false ? String(st.error || "") : "";
}
const UNSHACKLE_MODE = "local";
