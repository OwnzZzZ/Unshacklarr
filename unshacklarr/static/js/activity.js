/* Activity. The list holds what runs, then the history; the picked download shows
   its steps, a bar per track and why it ended, with Unshackle's raw output (xterm) folded underneath. */
const TERM_COLS = 110;
const OUTCOME = { running: "Running", downloaded: "Downloaded", failed: "Failed", kept: "Kept", interrupted: "Interrupted", stopped: "Stopped", unavailable: "Not out yet", cancelled: "Cancelled" };
const KIND = { auto: "automatic sync", burst: "release time", manual: "started by hand", retry: "retried by hand", upgrade: "better language" };
let term = null, termFit = null, termSocket = null, termFor = null, runsShown = 50, runsTimer = null;
let runCards = [], picked_run = null, conFilter = "all";
let landOnLive = false;  // the menu's Activity was clicked: the next list shows what runs
const speedLog = {};  // run id -> [[time, progress]], for the time left

function duration(ms) {
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")}` : `${Math.floor(s / 3600)} h ${String(Math.round(s / 60) % 60).padStart(2, "0")}`;
}
const posterOf = (c) => S.series.find((x) => x.tvdbId === c.tvdbId)?.poster || "";
const isLive = (c) => c.outcome === "running";
const speedOf = (c) => (c.live?.speed || "").replace(/^[A-Za-z]+\s+(?=\d)/, "");  // serve writes "DASH 8.4 MB/s"
const isQueued = (c) => isLive(c) && (c.live?.status || "queued") === "queued" && c.step === "queued";
function timeLeft(c) {
  const p = Number(c.live?.progress) || 0, log = (speedLog[c.id] ||= []), now = Date.now();
  if (!log.length || log[log.length - 1][1] !== p) log.push([now, p]);
  while (log.length > 20) log.shift();
  const [t0, p0] = log[0], rate = (p - p0) / ((now - t0) / 1000);
  return rate > 0 && p < 100 ? duration(((100 - p) / rate) * 1000) : "";
}

let runsSeq = 0;
async function openActivity() {
  const seq = ++runsSeq;
  let cards;
  try { cards = await api("/api/runs"); }
  catch (e) { if (seq === runsSeq) $("#runs-live").replaceChildren(failed(`Could not load the downloads: ${e.message}`, openActivity)); return; }
  if (seq !== runsSeq) return;  // a newer call is on its way: this answer may predate a Stop
  runCards = cards;
  clearTimeout(runsTimer);
  renderConsole();
  api("/api/leftovers").then((r) => { $("#con-left-n").textContent = r.items.length ? `(${r.items.length})` : ""; }).catch(() => {});
  if ($("#tab-log").hidden) return;
  followLive();
  runsTimer = setTimeout(openActivity, liveFeed ? 15000 : runCards.some(isLive) ? 2000 : 15000);  // without the feed: polling
}
/* The downloads going on now, pushed by the server as they move; the whole list again when one starts or ends. */
let liveFeed = null;
function followLive() {
  if (liveFeed || !window.EventSource) return;
  liveFeed = new EventSource("/api/runs/live");
  liveFeed.onmessage = (e) => {
    if ($("#tab-log").hidden) { liveFeed.close(); liveFeed = null; return; }
    if (document.hidden) return;  // nothing drawn out of sight: the next message, once back, draws it
    const live = JSON.parse(e.data), ids = new Set(live.map((c) => c.id));
    const known = new Set(runCards.filter((c) => isLive(c) && !c.waiting).map((c) => c.id));  // the queued aren't in the feed
    if (live.length !== known.size || live.some((c) => !known.has(c.id))) return openActivity();  // one started or ended
    runCards = runCards.map((c) => (ids.has(c.id) ? live.find((x) => x.id === c.id) : c));
    renderConsole(true);
  };
  liveFeed.onerror = () => { liveFeed?.close(); liveFeed = null; };  // polling takes over; the next load tries again
}
/* A bar redrawn with the page keeps moving from where it was, instead of jumping. */
const barWidths = new Map();
function glideBars(root) {
  const bars = [...root.querySelectorAll(".bar > i[data-k]")];
  const targets = bars.map((b) => b.style.width);
  bars.forEach((b) => { const was = barWidths.get(b.dataset.k); if (was) b.style.width = was; });
  root.offsetWidth;  // the start widths are laid out before the targets are set
  bars.forEach((b, i) => { b.style.width = targets[i]; barWidths.set(b.dataset.k, targets[i]); });
}
function renderConsole(liveOnly = false) {  // liveOnly: a tick of the live feed, the same downloads moving on
  const live = runCards.filter(isLive);
  // A job (episodes picked together) is running while one of its episodes is; the others wait in it, not in the history
  const liveJobs = new Set(live.map((c) => c.batch).filter(Boolean));
  const liveItems = jobsOf(runCards.filter((c) => isLive(c) || liveJobs.has(c.batch)));
  $("#con-live-n").textContent = live.length || "";
  $("#runs-live").replaceChildren(...(liveItems.length ? liveItems.map(itemRow) : [el("p", { className: "con-empty", textContent: "Nothing is downloading right now." })]));
  glideBars($("#runs-live"));
  if (!liveOnly) {  // the history and its counts: unchanged while the same downloads only move on
    const q = $("#con-search").value.trim().toLowerCase();
    const groups = { all: () => true, downloaded: (c) => c.outcome === "downloaded", failed: (c) => c.outcome === "failed",
      kept: (c) => c.outcome === "kept", stopped: (c) => ["stopped", "interrupted", "cancelled"].includes(c.outcome) };
    const searched = runCards.filter((c) => !isLive(c) && (!q || c.series.toLowerCase().includes(q)));
    document.querySelectorAll("#tab-log .con-filters button").forEach((b) => b.querySelector("span").textContent = `(${searched.filter(groups[b.dataset.f]).length})`);
    const pastItems = jobsOf(searched.filter(groups[conFilter]).filter((c) => !liveJobs.has(c.batch)));
    $("#runs-history").replaceChildren(...(pastItems.length ? pastItems.slice(0, runsShown).map(itemRow)
      : [el("p", { className: "con-empty", textContent: q || conFilter !== "all" ? "No download matches." : "No download yet." })]));
    $("#runs-more").hidden = pastItems.length <= runsShown;
  }
  // Activity opened from the menu while a download runs: straight to it, a phone too.
  if (landOnLive && liveItems.length) { picked_run = itemKey(liveItems[0]); $("#con").classList.add("open"); }
  landOnLive = false;
  // On a wide screen, something is always shown: what runs first, else the latest download.
  if (!picked_run && innerWidth > 700) picked_run = itemKey(liveItems[0] || jobsOf(runCards)[0]) || null;
  if (picked_run === "serve" || picked_run === "stats" || picked_run === "left") return;
  if (picked_run?.startsWith("job:")) {
    const job = jobsOf(runCards).find((i) => i.job === picked_run.slice(4));
    if (job) return renderJob(job);
  }
  const card = runCards.find((c) => c.id === picked_run);
  if (card) renderDetail(card);
}
/* Episodes picked together, as one job: [{job, cards}] in place of their cards, in the list's order. */
function jobsOf(cards) {
  const items = [], jobs = new Map();
  for (const c of cards) {
    if (!c.batch) { items.push(c); continue; }
    if (!jobs.has(c.batch)) { const job = { job: c.batch, cards: [] }; jobs.set(c.batch, job); items.push(job); }
    jobs.get(c.batch).cards.push(c);
  }
  jobs.forEach((j) => {  // one card per episode: its try going on or queued, else its latest
    const best = new Map();
    for (const c of j.cards) {
      const was = best.get(c.sxxeyy), rank = (x) => (x.waiting || isLive(x) ? 2 : 1);
      if (!was || rank(c) > rank(was) || (rank(c) === rank(was) && c.started > was.started)) best.set(c.sxxeyy, c);
    }
    j.cards = [...best.values()].sort((a, b) => a.sxxeyy.localeCompare(b.sxxeyy));
  });
  return items.map((i) => (i.job && i.cards.length === 1 ? i.cards[0] : i));  // a job left with one episode is that episode
}
/* What the pointer is on, kept across redraws: a redrawn element isn't :hover until the pointer moves (its border blinked). */
let hoverKey = null;
const hovering = (key) => (key === hoverKey ? " is-hover" : "");
document.addEventListener("mouseover", (e) => {
  const on = e.target.closest?.("[data-hk]");
  if ((on?.dataset.hk || null) === hoverKey) return;
  hoverKey = on?.dataset.hk || null;
  document.querySelectorAll(".is-hover").forEach((x) => x !== on && x.classList.remove("is-hover"));
  on?.classList.add("is-hover");
});
/* Lists are redrawn at each update: an animation there starts where the clock has it, not over (no blink). */
const phase = (ms) => `animation-delay: -${Date.now() % ms}ms`;
const pulse = () => el("span", { className: "pulse", ariaLabel: "Running", style: phase(1600) });
/* A row's poster, the same element from one render to the next: a redrawn row doesn't reload it (no blink). */
const thumbs = new Map();
function thumb(key, src) {
  let img = thumbs.get(key);
  if (!img || img.getAttribute("src") !== src) thumbs.set(key, img = el("img", { alt: "", loading: "lazy", src }));
  return img;
}
const itemKey = (i) => (i?.job ? `job:${i.job}` : i?.id);
const itemRow = (i) => (i.job ? jobRow(i) : listRow(i));
const FINISHING = new Set(["joining", "finishing", "renaming", "importing"]);
/* Where a job's episodes stand: counts, and how far it is as a whole. */
function jobState(j) {
  const n = { done: 0, failed: 0, stopped: 0, cancelled: 0, downloading: 0, finishing: 0, queued: 0 };
  let progress = 0;
  for (const c of j.cards) {
    if (c.waiting) n.queued++;
    else if (isLive(c)) { if (FINISHING.has(c.step)) { n.finishing++; progress += 0.95; } else { n.downloading++; progress += (Number(c.live?.progress) || 0) / 100 * 0.9; } }
    else if (c.outcome === "downloaded" || c.outcome === "kept") { n.done++; progress += 1; }
    else if (c.outcome === "failed") { n.failed++; progress += 1; }
    else if (c.outcome === "cancelled") { n.cancelled++; progress += 1; }
    else { n.stopped++; progress += 1; }
  }
  const live = n.queued + n.downloading + n.finishing > 0;
  const paused = j.cards.some((c) => c.waiting && c.paused);
  const words = [paused && "paused", n.done && `${n.done} done`, n.downloading && `${n.downloading} downloading`, n.finishing && `${n.finishing} finishing`,
    n.queued && `${n.queued} queued`, n.failed && `${n.failed} failed`, n.stopped && `${n.stopped} stopped`, n.cancelled && `${n.cancelled} cancelled`].filter(Boolean);
  return { n, live, paused, words, progress: Math.round(100 * progress / j.cards.length) };
}
function jobRow(j) {
  const first = j.cards[0], st = jobState(j), key = `job:${j.job}`;
  const series = new Set(j.cards.map((c) => c.series)).size > 1 ? "Several series" : first.series;
  return el("button", { className: "crow job" + (key === picked_run ? " sel" : "") + hovering(`row:${key}`), dataset: { hk: `row:${key}` }, onclick: () => pickRun(key) },
    thumb(`row:${key}`, posterOf(first)),
    el("span", {}, el("b", {}, el("span", { textContent: series }), el("em", { textContent: `${j.cards.length} episodes` })),
      el("small", { textContent: (st.live ? st.words : [...st.words, ...jobTotals(j)]).join(" · ") }),
      ...(st.live ? [el("span", { className: "bar" }, el("i", { style: `width:${st.progress}%`, dataset: { k: `row:${key}` } }))] : [])),
    st.live ? pulse()
      : el("span", { className: `chip o-${st.n.failed ? "failed" : st.n.stopped && !st.n.done ? "stopped" : "downloaded"}`,
          textContent: st.n.failed ? `${st.n.failed} failed` : !st.n.done && (st.n.stopped || st.n.cancelled) ? "Stopped" : `${st.n.done}/${j.cards.length}` }));
}
const openCards = new Set();  // a job's cards unfolded: they stay so as the job moves on
let justOpened = null;  // the card unfolding now: it alone fades in
let following = null;  // a card unfolded by hand while downloading: once done, the next download unfolds in its place
const ICON_SPEED = '<path d="M13 3 4 14h7l-1 7 9-11h-7z"/>', ICON_CLOCK = '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>';
const ICON_GLOBE = '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>', ICON_SWAP = '<path d="M4 8h13l-3-3M20 16H7l3 3"/>';
/* What serve does once every track is in, before the file is made: decrypting them (a DRM title), repackaging,
   muxing. "" while it still downloads. Without it, a card showed 96% and a stale time left, and seemed stuck. */
function afterTracks(c) {
  const lv = c.live || {}, phase = String(lv.phase || "");
  if (phase.startsWith("mux")) return "Muxing";
  if (phase.startsWith("repack")) return "Repackaging";
  if (lv.total_tracks && Number(lv.completed_tracks) >= Number(lv.total_tracks)) return c.cdm ? "Decrypting" : "Processing the tracks";
  return "";
}
/* The proxy a download goes through: serve's word once it has one, else what was asked; "" for none. */
function proxyOf(c) {
  if (c.proxy) return c.proxy.provider ? [c.proxy.provider, c.proxy.region?.toUpperCase()].filter(Boolean).join(" · ") : "Custom proxy";
  const asked = c.setup?.proxy;
  return !asked || asked === "none" || "proxy" in c ? "" : asked.includes("//") ? "Custom proxy" : asked.toUpperCase();
}
/* The CDM a download decrypts with: its device, its DRM and level (L3, SL2000), or the server's. */
function cdmOf(c) {
  const d = c.cdm;
  if (!d) return "";
  const drm = [d.drm, d.level != null ? `${d.drm === "PlayReady" ? "SL" : "L"}${d.level}` : ""].filter(Boolean).join(" ");
  return d.server ? `The server's (${drm})` : [d.name, drm].filter(Boolean).join(" · ");
}
function metaChip(icon, text) {
  const chip = el("span", { className: "mchip" });
  chip.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg>`;
  chip.append(text);
  return chip;
}
/* A job: a card per episode, in order, unfolding to its details, and one output for them all. */
function renderJob(j) {
  const first = j.cards[0], st = jobState(j), started = new Date(Math.min(...j.cards.map((c) => new Date(c.started))));
  const series = S.series.find((x) => x.tvdbId === first.tvdbId);
  const retryable = j.cards.filter((c) => c.episodeId && ["failed", "stopped", "interrupted"].includes(c.outcome));  // not the cancelled ones: taken out on purpose
  const title = new Set(j.cards.map((c) => c.series)).size > 1 ? `${j.cards.length} episodes` : `${first.series} · ${j.cards.length} episodes`;
  $("#cd-top-title").textContent = title;
  const acts = [series ? el("button", { className: "btn small", textContent: "Open series", onclick: () => openDrawer(series) }) : "",
    st.live && st.n.queued ? el("button", { className: `btn small${st.paused ? " primary" : ""}`, textContent: st.paused ? "Resume" : "Pause",
      title: st.paused ? "The next episode starts" : "The current download finishes; the next one waits",
      onclick: (e) => pauseJob(j, !st.paused, e.target) }) : "",
    st.live ? el("button", { className: "btn small danger", textContent: "Stop the job", title: "Stops the current download. The queued episodes don't start",
      onclick: (e) => stopJob(j, e.target) }) : "",
    !st.live && retryable.length ? el("button", { className: "btn small primary", textContent: `Retry ${retryable.length} failed`,
      title: "Downloads them again, in this job", onclick: (e) => retryJob(j, retryable, e.target) }) : ""].filter(Boolean);
  $("#cd-foot").replaceChildren(...acts.map((b) => { const copy = b.cloneNode(true); copy.onclick = b.onclick; return copy; }));
  const head = el("div", { className: "cd-head" }, thumb("detail", posterOf(first)),
    el("div", {}, el("h3", { textContent: title }),
      el("small", { className: "line1", textContent: `${first.service} · ${KIND[first.kind] || first.kind} · started ${day(started)} ${started.toLocaleTimeString(LOCALE)}` }),
      el("small", { className: "line2" }, el("b", { textContent: first.service }), `${KIND[first.kind] || first.kind} · ${day(started)} ${time(started)}`)),
    el("div", { className: "act" }, ...acts));
  const overall = el("div", { className: "job-sum" },
    el("div", { className: "job-counts" }, ...(st.paused ? [el("span", { className: "paused", textContent: "⏸ Paused: the next episode waits" })] : []), ...[["done", "Done", "ok"], ["downloading", "Downloading", "now"], ["finishing", "Finishing", "now"],
      ["queued", "Queued", ""], ["failed", "Failed", "err"], ["stopped", "Stopped", ""], ["cancelled", "Cancelled", ""]]
      .filter(([k]) => st.n[k]).map(([k, label, cls]) => el("span", { className: cls }, el("b", { textContent: st.n[k] }), label))),
    el("span", { className: "bar" }, el("i", { style: `width:${st.progress}%`, dataset: { k: `job:${j.job}` } })));
  const isDownloading = (c) => isLive(c) && !c.waiting && !FINISHING.has(c.step) && !isQueued(c);
  const followed = j.cards.find((c) => c.id === following);
  const next = followed && !isDownloading(followed) && !isQueued(followed) && j.cards.find(isDownloading);
  if (next) { openCards.delete(followed.id); openCards.add(next.id); following = next.id; }
  const cards = el("ol", { className: "job-cards" }, ...j.cards.map((c) => {
    const live = isLive(c);
    const label = c.waiting ? "Queued" : live ? (FINISHING.has(c.step) ? { joining: "Joining parts", finishing: "Finishing", renaming: "Renaming", importing: "Importing" }[c.step]
      : isQueued(c) ? "Waiting for a slot" : afterTracks(c) || `Downloading ${Math.round(Number(c.live?.progress) || 0)}%`) : OUTCOME[c.outcome] || c.outcome;
    const tone = c.waiting ? "" : live ? "now" : c.outcome === "downloaded" ? "ok" : c.outcome === "failed" ? "err" : c.outcome === "kept" ? "warn" : "";
    const downloading = isDownloading(c);
    const detail = downloading ? el("div", { className: "jmeta" },  // how fast, how long still: in sight, not a grey line
        ...[...(afterTracks(c) ? [[ICON_CLOCK, `${afterTracks(c)}: the tracks are in`]] : [[ICON_SPEED, speedOf(c)], [ICON_CLOCK, timeLeft(c) && `${timeLeft(c)} left`]]), [ICON_GLOBE, proxyOf(c), "extra"],
          [ICON_SWAP, c.serviceEpisode && c.serviceEpisode !== c.sxxeyy && `${c.serviceEpisode} on ${c.service}`, "extra"]]
          .filter(([, t]) => t).map(([icon, t, extra]) => { const chip = metaChip(icon, t); if (extra) chip.classList.add(extra); return chip; }))
      : el("small", { textContent: !live && c.ended ? [c.cause, `took ${duration(new Date(c.ended) - new Date(c.started))}`, c.size && size(c.size)].filter(Boolean).join(" · ") : "" });
    const open = openCards.has(c.id) && !c.waiting;
    const acts = open ? cardActs(c) : [];
    const fold = () => {
      if (openCards.has(c.id)) { openCards.delete(c.id); if (following === c.id) following = null; }
      else { openCards.add(c.id); justOpened = c.id; if (isDownloading(c)) following = c.id; }
      renderConsole(); justOpened = null;
    };
    if (c.outcome === "cancelled") return el("li", { className: "jcard cancelled" },  // never started: kept in sight, struck
      el("div", { className: "jhead" }, el("b", { className: "ep", textContent: c.sxxeyy }),
        el("div", { className: "mid" }, el("small", { textContent: `Cancelled ${time(new Date(c.ended))}` })),
        el("span", { className: "jstate", textContent: "Cancelled" }),
        el("button", { type: "button", className: "jx again", title: "Queue it again", ariaLabel: `Queue ${c.sxxeyy} again`, onclick: () => requeue(c) }, "↺")));
    if (c.waiting) return el("li", { className: "jcard queued" },  // not started: nothing to unfold, but it can be cancelled
      el("div", { className: "jhead" }, el("b", { className: "ep", textContent: c.sxxeyy }),
        el("div", { className: "mid" }, el("div", { className: "steps mini", ariaHidden: "true" }, ...stepStates(c).filter((x) => x !== "skip").map(() => el("span")))),
        el("span", { className: "jstate", textContent: "Queued" }),
        el("button", { type: "button", className: "jx", title: "Cancel", ariaLabel: `Cancel ${c.sxxeyy}`,
          onclick: () => unqueue(c) }, "×")));
    return el("li", { className: `jcard ${tone}${open ? " open" : ""}${hovering(`card:${c.id}`)}`, dataset: { hk: `card:${c.id}` } },
      el("div", { className: "jrow" }, el("button", { type: "button", className: "jhead", ariaExpanded: String(open), onclick: fold },
        el("b", { className: "ep", textContent: c.sxxeyy }),
        el("div", { className: "mid" },
          el("div", { className: "steps mini", ariaHidden: "true" }, ...stepStates(c).filter((x) => x !== "skip")
            .map((x) => downloading && x === "now"  // the step going on fills as it downloads: one bar, not two
              ? el("span", { className: "now fill", style: `--p:${Math.round(Number(c.live?.progress) || 0)}%` })
              : el("span", { className: c.waiting ? "" : x, style: `--t:-${Date.now() % 1600}ms` }))),  // the shimmer goes on, not from the start
          detail),
        el("span", { className: `jstate ${tone}`, textContent: label }),
        ),
        ...(acts.length ? [el("div", { className: "jacts" }, ...acts)] : []),  // unfolded: its actions in its head, not a line of their own
        el("span", { className: "chev", ariaHidden: "true", textContent: "›", onclick: fold })),  // last, after the actions
      ...(open ? [el("div", { className: "jbody" + (justOpened === c.id ? " opening" : "") }, ...runBody(c))] : []));
  }));
  keepPromptFocus(() => $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:18px" }, head, st.live ? overall : jobDone(j, st), cards)));
  glideBars($("#cd-main"));
  $("#cd-raw").hidden = false;
  if ($("#cd-raw").open && termFor !== `job:${j.job}`) streamJob(j.job);
}
/* A job over, in a few words: how long, how big. */
function jobTotals(j) {
  const tried = j.cards.filter((c) => c.ended && c.outcome !== "cancelled");
  if (!tried.length) return [];
  const took = Math.max(...tried.map((c) => new Date(c.ended))) - Math.min(...tried.map((c) => new Date(c.started)));
  const bytes = j.cards.reduce((n, c) => n + (c.size || 0), 0);
  return [duration(took), bytes && size(bytes)].filter(Boolean);
}
/* A job over: how long it took from the first start to the last end, what it brought, and what didn't come. */
function jobDone(j, st) {
  const tried = j.cards.filter((c) => c.ended && c.outcome !== "cancelled");
  const first = Math.min(...tried.map((c) => new Date(c.started))), last = Math.max(...tried.map((c) => new Date(c.ended)));
  const done = j.cards.filter((c) => c.outcome === "downloaded" || c.outcome === "kept"), bytes = done.reduce((n, c) => n + (c.size || 0), 0);
  const took = tried.length ? last - first : 0;
  const per = done.length ? done.reduce((n, c) => n + (new Date(c.ended) - new Date(c.started)), 0) / done.length : 0;
  const tone = st.n.failed ? "err" : done.length === j.cards.length ? "ok" : "mut";
  const stats = [["Took", took ? duration(took) : "–"], ["Downloaded", `${done.length} of ${j.cards.length}`], ["Size", bytes ? size(bytes) : "–"],
    ["Per episode", per ? duration(per) : "–"], ...(bytes && took ? [["Average", `${(bytes / 1e6 / (took / 1000)).toFixed(1)} MB/s`]] : [])];
  const rest = [st.n.failed && `${st.n.failed} failed`, st.n.stopped && `${st.n.stopped} stopped`, st.n.cancelled && `${st.n.cancelled} cancelled`].filter(Boolean);
  return el("div", { className: `job-done ${tone}` },
    el("b", { textContent: tone === "ok" ? "Job done" : rest.length ? `Job over · ${rest.join(" · ")}` : "Job over" }),
    el("div", { className: "cd-stats" }, ...stats.map(([k, v]) => el("div", {}, el("small", { textContent: k }), el("b", { textContent: v })))));
}
function listRow(c) {
  const started = new Date(c.started);
  const sub = isLive(c)
    ? (c.action ? `${c.service} · waiting for you: code ${c.action.code}` : c.prompt ? `${c.service} · waiting for your answer`
      : c.waiting ? `${c.service} · in the queue` : isQueued(c) ? `${c.service} · waiting for a slot` : `${c.service} · ${afterTracks(c) || speedOf(c) || c.step}`)
    : c.outcome === "unavailable" && c.checks > 1 ? `${c.service} · checked ${c.checks} times, last ${day(started)} ${time(started)}`
    : `${day(started)} ${time(started)}${c.ended ? ` · ${duration(new Date(c.ended) - started)}` : ""}`;
  const side = isLive(c) ? (isQueued(c) ? el("span", { className: "chip", textContent: "Queued" }) : pulse())
    : el("span", { className: `chip o-${c.outcome}`, textContent: OUTCOME[c.outcome] || c.outcome });
  const row = el("button", { className: "crow" + (c.id === picked_run ? " sel" : "") + hovering(`row:${c.id}`), dataset: { hk: `row:${c.id}` }, onclick: () => pickRun(c.id) },
    thumb(`row:${c.id}`, posterOf(c)),
    el("span", {}, el("b", {}, el("span", { textContent: c.series }), el("em", { textContent: c.sxxeyy })),
      el("small", { textContent: c.attempts > 1 ? `${sub} · attempt ${c.attempts}` : sub }),
      ...(isLive(c) && !isQueued(c) ? [el("span", { className: "bar" }, el("i", { style: `width:${Number(c.live?.progress) || 0}%`, dataset: { k: `row:${c.id}` } }))] : [])),
    side);
  return row;
}
function pickRun(id) {
  picked_run = id;
  $("#con").classList.add("open");
  renderConsole();
  if (innerWidth < 700) scrollTo({ top: 0 });
}
/* Waiting in downloads: folders Sonarr did not take, with why, imported anyway or deleted. */
async function showLeftovers() {
  picked_run = "left";
  $("#con").classList.add("open");
  document.querySelectorAll(".crow").forEach((r) => r.classList.toggle("sel", r.id === "con-left"));
  $("#cd-top-title").textContent = "Waiting in downloads";
  $("#cd-raw").hidden = true;
  $("#cd-foot").replaceChildren();
  let r;
  try { r = await api("/api/leftovers"); } catch (e) { return $("#cd-main").replaceChildren(failed(e.message, showLeftovers)); }
  $("#con-left-n").textContent = r.items.length ? `(${r.items.length})` : "";
  const act = (item, action, label, cls) => el("button", { className: `btn small ${cls}`, textContent: label, onclick: async (e) => {
    if (action === "delete" && e.target.dataset.sure !== "1") { e.target.dataset.sure = "1"; e.target.textContent = "Sure?"; return; }
    e.target.disabled = true;
    try {
      await api(`/api/leftovers/${action}`, { method: "POST", signal: AbortSignal.timeout(360000), body: JSON.stringify({ folder: item.folder }) });
      toast(action === "delete" ? `${item.folder} deleted` : `${item.series || item.folder}: imported by Sonarr`);
      showLeftovers();
    } catch (err) { e.target.disabled = false; toast(err.message, true); }
  } });
  const rows = r.items.map((it) => {
    const poster = S.series.find((x) => x.tvdbId === it.tvdbId)?.poster || "";
    const age = Math.floor((Date.now() / 1000 - it.since) / 86400);
    return el("div", { className: "left-row" }, el("img", { alt: "", loading: "lazy", src: poster }),
      el("div", {}, el("b", { textContent: `${it.series || `TVDB ${it.tvdbId}`} ${it.sxxeyy}` }),
        el("small", { className: "why", textContent: it.cause || (it.outcome ? OUTCOME[it.outcome] : "No download history for it") }),
        el("small", { textContent: `${it.size >= 2 ** 30 ? `${(it.size / 2 ** 30).toFixed(1)} GB` : `${Math.max(1, Math.round(it.size / 2 ** 20))} MB`} · waiting ${age < 1 ? "since today" : `${age} day${age > 1 ? "s" : ""}`} · ${it.sonarr_path}` }),
        ...(it.by_hand ? [el("small", { className: "left-hand", textContent: "Download only: kept until you import or delete it" })] : [])),
      el("div", { className: "btns" }, act(it, "import", it.by_hand ? "Import" : "Import anyway", it.by_hand ? "primary" : ""), act(it, "delete", "Delete", "danger")));
  });
  $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:14px" },
    el("div", { className: "cd-head" }, el("div", {}, el("h3", { textContent: "Waiting in downloads" }),
      el("small", { className: "line1", textContent: r.days > 0 ? `Deleted on their own after ${r.days} days (Settings, Automation).` : "Kept until you delete them (Settings, Automation)." }))),
    ...(rows.length ? [el("div", {}, ...rows)] : [el("p", { className: "muted", textContent: "Nothing waits: every download went to Sonarr." })])));
}
$("#con-left").onclick = showLeftovers;

/* Catch up: the aired episodes Unshacklarr's series still miss (those before a series got its service, or the
   automatic sync gave up on), downloaded together as one job. Within so many days, or all. */
let missingDays = 30;
async function showMissing() {
  picked_run = "missing";
  $("#con").classList.add("open");
  document.querySelectorAll(".crow").forEach((r) => r.classList.toggle("sel", r.id === "con-missing"));
  $("#cd-top-title").textContent = "Catch up";
  $("#cd-raw").hidden = true;
  $("#cd-foot").replaceChildren();
  $("#cd-main").replaceChildren(loading("Asking Sonarr what your series miss…"));
  let r;
  try { r = await api(`/api/missing${missingDays ? `?days=${missingDays}` : ""}`); } catch (e) { return $("#cd-main").replaceChildren(failed(e.message, showMissing)); }
  if (picked_run !== "missing") return;
  const ages = el("div", { className: "ax-seg", role: "radiogroup", ariaLabel: "Aired within" }, ...[[7, "7 days"], [30, "30 days"], [90, "90 days"], [0, "All"]].map(([d, label]) =>
    el("button", { type: "button", role: "radio", textContent: label, ariaChecked: String(missingDays === d), onclick: () => { missingDays = d; showMissing(); } })));
  const go = el("button", { className: "btn primary", textContent: r.items.length === 1 ? "Download it" : `Download the ${r.items.length}`, hidden: !r.items.length,
    onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: r.items.map((x) => x.episodeId) }) });
        toast(r.items.length === 1 ? "1 episode queued" : `${r.items.length} episodes queued, one after the other`);
      } catch (err) { e.target.disabled = false; toast(err.message, true); }
    } });
  const rows = r.items.map((it) => {
    const poster = S.series.find((x) => x.tvdbId === it.tvdbId)?.poster || "";
    return el("div", { className: "left-row" }, el("img", { alt: "", loading: "lazy", src: poster }),
      el("div", {}, el("b", { textContent: `${it.series} ${it.sxxeyy}` }),
        el("small", {}, ...(it.title ? [episodeTitle(it.tvdbId, it.sxxeyy, it.title), " · "] : []), `aired ${ago(it.aired)}`)));
  });
  $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:14px" },
    el("div", { className: "cd-head" }, el("div", {}, el("h3", { textContent: "Catch up" }),
      el("small", { className: "line1", textContent: "Episodes that aired, are monitored and have no file in Sonarr, for the series Unshackle downloads. The automatic sync only looks at recent ones." }))),
    el("div", { className: "miss-bar" }, ages, go),
    ...(rows.length ? [el("div", {}, ...rows)] : [el("p", { className: "muted", textContent: missingDays ? `Nothing missing from the last ${missingDays} days.` : "Nothing missing: every aired episode has its file." })])));
}
$("#con-missing").onclick = showMissing;

/* Upgrades: the files of the series with a quality ladder that are not on its first step, checked against the
   service's tracks (one episode at a time, in the background); those it has on an earlier step are replaced. */
let upgradeTimer = null, upgradeSkip = new Set();
async function showUpgrades() {
  picked_run = "upgrades";
  clearTimeout(upgradeTimer);
  $("#con").classList.add("open");
  document.querySelectorAll(".crow").forEach((r) => r.classList.toggle("sel", r.id === "con-upgrades"));
  $("#cd-top-title").textContent = "Upgrades";
  $("#cd-raw").hidden = true;
  $("#cd-foot").replaceChildren();
  let r;
  try { r = await api("/api/upgrades"); } catch (e) { return $("#cd-main").replaceChildren(failed(e.message, showUpgrades)); }
  if (picked_run !== "upgrades") return;
  const scan = r.scan, picked = r.items.filter((i) => !upgradeSkip.has(i.episodeId) && !i.running);
  const check = el("button", { className: "btn" + (scan.running ? " danger" : ""), textContent: scan.running ? "Stop the check" : r.checked ? "Check again" : "Check now",
    onclick: async (e) => {
      e.target.disabled = true;
      try { await api(scan.running ? "/api/upgrades/stop" : "/api/upgrades/check", { method: "POST" }); } catch (err) { toast(err.message, true); }
      showUpgrades();
    } });
  const go = el("button", { className: "btn primary", hidden: !picked.length || scan.running,
    textContent: picked.length === 1 ? "Replace it" : `Replace the ${picked.length}`, onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: picked.map((i) => i.episodeId), replace: true }) });
        toast(picked.length === 1 ? "1 episode queued, replacing its file" : `${picked.length} episodes queued, replacing their files`);
        showUpgrades();
      } catch (err) { e.target.disabled = false; toast(err.message, true); }
    } });
  const state = scan.running ? `Checking ${scan.done} of ${scan.total}${scan.series ? ` · ${scan.series}` : ""}…`
    : r.checked ? `Checked ${ago(r.checked)}${r.stopped ? ", stopped before the end" : ""}${scan.errors ? ` · ${scan.errors} could not be asked` : ""}` : "Not checked yet.";
  const rows = r.items.map((it) => {
    const poster = S.series.find((x) => x.tvdbId === it.tvdbId)?.poster || "";
    const box = el("input", { type: "checkbox", checked: !upgradeSkip.has(it.episodeId), disabled: it.running, ariaLabel: `Replace ${it.series} ${it.sxxeyy}`,
      onchange: (e) => { if (e.target.checked) upgradeSkip.delete(it.episodeId); else upgradeSkip.add(it.episodeId); showUpgrades(); } });
    return el("label", { className: "left-row up-row" }, box, el("img", { alt: "", loading: "lazy", src: poster }),
      el("div", {}, el("b", { textContent: `${it.series} ${it.sxxeyy}` }),
        el("small", { textContent: `${it.file} here${it.fileStep ? ` (step ${it.fileStep})` : " (outside the ladder)"} → ${it.better} on the service (step ${it.betterStep} of ${it.ladder})` }),
        ...(it.running ? [el("small", { className: "left-hand", textContent: "Being replaced now" })] : [])));
  });
  $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:14px" },
    el("div", { className: "cd-head" }, el("div", {}, el("h3", { textContent: "Upgrades" }),
      el("small", { className: "line1", textContent: "Files your quality ladder says could be better. Check now asks each service, one episode at a time, what it has. Replace downloads the better ones over your files, even when Sonarr ranks both the same (H.264 and H.265 in 1080p)." }))),
    el("div", { className: "miss-bar" }, el("span", { className: "muted", textContent: state }), el("span", { className: "up-acts" }, check, go)),
    ...(rows.length ? [el("div", {}, ...rows)] : [el("p", { className: "muted", textContent: scan.running ? "Nothing better found yet." : r.checked ? "Nothing better on the services: every file is already on the best step they have." : "Check now asks each service what it has, one episode at a time." })])));
  if (scan.running) upgradeTimer = setTimeout(() => picked_run === "upgrades" && showUpgrades(), 3000);
}
$("#con-upgrades").onclick = showUpgrades;

/* Stats: what the history adds up to, in the detail pane. */
async function showStats() {
  picked_run = "stats";
  $("#con").classList.add("open");
  document.querySelectorAll(".crow").forEach((r) => r.classList.toggle("sel", r.id === "con-stats"));
  $("#cd-top-title").textContent = "Stats";
  $("#cd-raw").hidden = true;
  $("#cd-foot").replaceChildren();
  let st;
  try { st = await api("/api/stats"); } catch (e) { return $("#cd-main").replaceChildren(failed(e.message, showStats)); }
  const m = st.month, fmt = (v, unit = "") => v === null || v === undefined ? "–" : `${v}${unit}`;
  const max = Math.max(1, ...st.weeks.map((w) => w.downloaded + w.failed + w.kept));
  const weeks = el("div", { className: "weeks", role: "img", ariaLabel: "Downloads per week, the last 8 weeks" }, ...st.weeks.map((w) => {
    const n = w.downloaded + w.failed + w.kept, h = (k) => `height:${(w[k] / max) * 130}px`;
    return el("div", { className: "w", title: `Week of ${new Date(w.start).toLocaleDateString(LOCALE, { day: "numeric", month: "short" })}: ${w.downloaded} downloaded, ${w.kept} kept, ${w.failed} failed` },
      el("span", { className: "stack" }, el("i", { className: "ok", style: h("downloaded") }), el("i", { className: "kp", style: h("kept") }), el("i", { className: "ko", style: h("failed") })),
      el("span", {}, el("b", { textContent: n || "" }), el("br"), el("small", { textContent: new Date(w.start).toLocaleDateString(LOCALE, { day: "numeric", month: "short" }) })));
  }));
  const table = el("table", { className: "svc-table" }, el("thead", {}, el("tr", {}, ...["Service", "Downloaded", "Failed", "Kept", "Success", "Last success", "Average"].map((t) => el("th", { textContent: t })))),
    el("tbody", {}, ...st.services.map((v) => el("tr", {}, el("td", {}, el("b", { textContent: v.service })), el("td", { textContent: v.downloaded }),
      el("td", { className: v.failed ? "bad" : "", textContent: v.failed }), el("td", { textContent: v.kept }),
      el("td", { className: v.success === null ? "" : v.success < 70 ? "bad" : "fine", textContent: fmt(v.success, "%") }),
      el("td", { textContent: v.last_success ? ago(v.last_success) : "never" }), el("td", { textContent: v.average ? duration(v.average * 1000) : "–" })))));
  $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:22px" },
    el("div", { className: "cd-head" }, el("div", {}, el("h3", { textContent: "Stats" }),
      el("small", { className: "line1", textContent: st.since ? `From the history kept since ${day(new Date(st.since))}` : "Nothing in the history yet" }))),
    el("div", { className: "kpis" }, ...[["Downloaded, 30 days", fmt(m.downloaded)], ["Success rate", fmt(m.success, "%")], ["Failed", fmt(m.failed)],
      ["Kept by Sonarr", fmt(m.kept)], ["Average time", m.average ? duration(m.average * 1000) : "–"]]
      .map(([k, v]) => el("div", {}, el("small", { textContent: k }), el("b", { textContent: v })))),
    el("div", { style: "display:grid;gap:8px" }, weeks, el("div", { className: "legend" }, el("span", {}, el("i", { className: "ok", style: "background:var(--ok)" }), "Downloaded"),
      el("span", {}, el("i", { style: "background:var(--warn)" }), "Kept by Sonarr"), el("span", {}, el("i", { style: "background:var(--err)" }), "Failed"))),
    st.services.length ? el("div", { style: "overflow-x:auto" }, table) : ""));
}
$("#con-stats").onclick = showStats;
$("#cd-back").onclick = () => { $("#con").classList.remove("open"); picked_run = null; renderConsole(); };
$("#con-search").oninput = renderConsole;
document.querySelectorAll("#tab-log .con-filters button").forEach((b) => b.onclick = () => {
  conFilter = b.dataset.f;
  document.querySelectorAll("#tab-log .con-filters button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  renderConsole();
});
const moreRuns = () => { if (!$("#runs-more").hidden) { runsShown += 50; renderConsole(); } };
$("#runs-more").onclick = moreRuns;
$("#runs-history").addEventListener("scroll", (e) => { const h = e.target; if (h.scrollTop + h.clientHeight > h.scrollHeight - 300) moreRuns(); }, { passive: true });
addEventListener("scroll", () => {  // a phone: the list is the page
  if (!$("#tab-log").hidden && innerWidth <= 700 && scrollY + innerHeight > document.documentElement.scrollHeight - 400) moreRuns();
}, { passive: true });

const STEPS = ["Queued", "Downloading", "Joining parts", "Renaming", "Sonarr import"];
const STEPS_SHORT = ["Queued", "Download", "Join", "Rename", "Import"];
function stepStates(c) {
  const at = { queued: 0, downloading: 1, joining: 2, finishing: 2, renaming: 3, importing: 4, done: 5 }[c.step] ?? 0;
  return STEPS.map((_, i) => {
    if (i === 2 && c.parts === 1) return "skip";  // one file: nothing to join
    if (c.outcome === "downloaded") return "done";
    if (c.outcome === "kept") return i < 4 ? "done" : "warn";
    if (i < at) return "done";
    if (i === at) return { running: "now", failed: "fail", stopped: "halt", interrupted: "halt", unavailable: "halt" }[c.outcome] || "halt";
    return "";
  });
}
function renderDetail(c) {
  const series = S.series.find((x) => x.tvdbId === c.tvdbId);
  const started = new Date(c.started);
  const acts = [series ? el("button", { className: "btn small", textContent: "Open series", onclick: () => openDrawer(series) }) : "", ...runActs(c)].filter(Boolean);
  const onService = c.serviceEpisode && c.serviceEpisode !== c.sxxeyy ? `, as ${c.serviceEpisode}` : "";
  const head = el("div", { className: "cd-head" }, thumb("detail", posterOf(c)),
    el("div", {}, el("h3", { textContent: `${c.series} ${c.sxxeyy}` }),
      el("small", { className: "line1", textContent: `${c.service}${onService} · ${KIND[c.kind] || c.kind} · started ${day(started)} ${started.toLocaleTimeString(LOCALE)}` }),
      el("small", { className: "line2" }, el("b", { textContent: `${c.service}${onService}` }), `${KIND[c.kind] || c.kind} · ${day(started)} ${time(started)}`)),
    el("div", { className: "act" }, ...acts));
  $("#cd-top-title").textContent = `${c.series} ${c.sxxeyy}`;
  $("#cd-foot").replaceChildren(...acts.map((b) => { const copy = b.cloneNode(true); copy.onclick = b.onclick; return copy; }));
  const parts = [head, ...runBody(c)];
  keepPromptFocus(() => $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:18px" }, ...parts)));
  glideBars($("#cd-main"));
  $("#cd-raw").hidden = !!c.waiting;  // not started: no output yet
  if ($("#cd-raw").open && !c.waiting && termFor !== c.id) streamRun(c);
}
/* The same, as round icon buttons for a job's card (the common tooltip says what each does). */
const ACT_ICONS = {
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>', import: '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
  retry: '<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.7"/><path d="M4 4v4.7h4.7"/>', delete: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
};
function cardActs(c) {
  const live = isLive(c);
  const act = (kind, label, run, extra = "") => {
    const b = el("button", { type: "button", className: `jact ${extra}`, ariaLabel: label, onclick: (e) => run(c, e.currentTarget) });
    b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ACT_ICONS[kind]}</svg>`;
    return b;
  };
  return [
    setupButton(c),
    live && c.job_id ? act("stop", "Stop", stopRun, "danger") : "",
    !live && c.outcome === "kept" ? act("import", "Import anyway", importKept, "primary") : "",
    !live && c.episodeId && c.outcome !== "downloaded" && c.outcome !== "kept" ? act("retry", "Retry", retryRun, "primary") : "",
    !live ? act("delete", "Delete", deleteRun, "danger") : "",
  ].filter(Boolean);
}
/* What can be done with an attempt: stop it, import it anyway, retry it, forget it. */
function runActs(c) {
  const live = isLive(c);
  return [
    setupButton(c),
    live && c.job_id ? el("button", { className: "btn small danger", textContent: "Stop", onclick: (e) => stopRun(c, e.target) }) : "",
    !live && c.outcome === "kept" ? el("button", { className: "btn small primary", textContent: "Import anyway", onclick: (e) => importKept(c, e.target) }) : "",
    !live && c.episodeId && c.outcome !== "downloaded" && c.outcome !== "kept" ? el("button", { className: "btn small primary", textContent: "Retry", onclick: (e) => retryRun(c, e.target) }) : "",
    !live ? el("button", { className: "btn small", textContent: "Delete", onclick: (e) => deleteRun(c, e.target) }) : "",
  ].filter(Boolean);
}
/* A track as serve names it ("Part 1 · audio fr 2.0"): an icon for its kind, the kind, then what it is in chips. */
const TRACK_KINDS = {
  video: ["Video", '<rect x="3" y="5" width="18" height="12" rx="2"/><path d="M8 21h8M12 17v4"/>'],
  audio: ["Audio", '<path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"/>'],
  subtitle: ["Subtitles", '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 13h4M13 13h4M7 16h10"/>'],
  chapter: ["Chapters", '<path d="M4 6h16M4 12h16M4 18h10"/>'],
};
/* An episode in parts: the part downloading and how many; serve's track count is that part's alone. */
function partStat(c) {
  const numbers = [...new Set((c.tracks || []).map((t) => Number(/^Part (\d+)/.exec(t.label)?.[1] || 0)).filter(Boolean))].sort((a, b) => a - b);
  if (numbers.length < 2) return [];
  const going = numbers.find((n) => c.tracks.some((t) => t.label.startsWith(`Part ${n} ·`) && t.progress < 100)) || numbers.at(-1);
  return [["Part", `${going} of ${numbers.length}`]];
}
function trackLabel(label, underPart = false) {  // underPart: its part's head says "Part N" already
  const part = /^Part (\d+) · /.exec(label), rest = part ? label.slice(part[0].length) : label;
  const [kind, ...bits] = rest.split(" ");
  const [name, icon] = TRACK_KINDS[kind.replace(/s$/, "")] || [kind[0].toUpperCase() + kind.slice(1), TRACK_KINDS.chapter[1]];
  const ico = el("span", { className: `tk ${kind.replace(/s$/, "")}`, ariaHidden: "true" });
  ico.innerHTML = `<svg viewBox="0 0 24 24">${icon}</svg>`;
  return el("span", { className: "tlabel", title: label }, ico,
    ...(part && !underPart ? [el("small", { textContent: `Part ${part[1]}` })] : []),
    el("b", { textContent: name }),
    ...bits.map((b) => el("i", { textContent: /^[a-z]{2,3}(-[a-z]{2,4})?$/i.test(b) ? b.toUpperCase() : b })));  // fr → FR
}
/* A track done: a tick, and how long it took. */
function trackDone(t) {
  const span = el("span", { className: "ok tdone", title: "Done" });
  span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${STATE_ICONS.disk}</svg>`;  // the same round tick as an episode on disk
  if (t.took != null) span.append(duration(t.took * 1000));
  return span;
}
/* An attempt's steps, what waits for you, its figures or its end, and its tracks: in its own view and in a job's card. */
function runBody(c) {
  const started = new Date(c.started), live = isLive(c);
  const steps = el("div", { className: "steps", role: "list", ariaLabel: "Steps" },
    ...stepStates(c).flatMap((st, i) => st === "skip" ? [] : [el("span", { className: st, role: "listitem", style: `--t:-${Date.now() % 1600}ms` },
      el("span", { className: "l", textContent: STEPS[i] }),
      el("span", { className: "s", textContent: STEPS_SHORT[i] }))]));
  const parts = [steps];
  if (live && c.action) parts.push(actionBox(c));
  if (live && c.prompt) parts.push(promptBox(c.prompt));
  if (live) {
    const lv = c.live || {};
    parts.push(el("div", { className: "cd-stats" },
      ...[["Progress", `${Math.round(Number(lv.progress) || 0)}%`], ...(afterTracks(c) ? [["Now", afterTracks(c)]] : [["Speed", speedOf(c) || "–"], ["Left", timeLeft(c) || "–"]]),
        ...partStat(c), [partStat(c).length ? "Tracks in it" : "Tracks", lv.total_tracks ? `${lv.completed_tracks || 0} of ${lv.total_tracks}` : "–"]]
        .map(([k, v]) => el("div", {}, el("small", { textContent: k }), el("b", { textContent: v })))));
  } else {
    const note = { downloaded: ["ok", "Downloaded"], failed: ["err", "Failed"], kept: ["warn", "Sonarr kept its own file"],
      stopped: ["mut", "Stopped"], interrupted: ["mut", "Interrupted: Unshacklarr stopped during it"],
      unavailable: ["mut", "Not out yet: nothing to download"] }[c.outcome] || ["mut", c.outcome];
    if (c.outcome === "downloaded") {  // all went well: one quiet line, not a block
      parts.push(el("div", { className: "cd-note ok inline" }, el("b", { textContent: "✓ Imported" }),  // imported says downloaded too
        ...[c.ended && duration(new Date(c.ended) - started), c.size && size(c.size), checksNote(c)].filter(Boolean).map((t) => el("span", { textContent: t }))));
    } else parts.push(el("div", { className: `cd-note ${note[0]}` }, el("b", { textContent: note[1] }),
      ...[c.cause, c.detail, c.ended ? `Took ${duration(new Date(c.ended) - started)}` : "",
        checksNote(c)]
        .filter(Boolean).map((t) => el("span", { textContent: t }))));
  }
  if (c.tracks?.length) {
    // Unshackle's own order, part by part: video, audio, subtitles, chapters
    const rank = (t) => [Number(/^Part (\d+)/.exec(t.label)?.[1] || 0),
      ["video", "audio", "subtitle", "chapter"].findIndex((k) => t.label.replace(/^Part \d+ · /, "").startsWith(k)) >>> 0];
    const tracks = [...c.tracks].sort((a, b) => { const [pa, ta] = rank(a), [pb, tb] = rank(b); return pa - pb || ta - tb; });
    const row = (t) => el("div", { className: "t" },
      trackLabel(t.label, true),
      el("span", { className: "bar" + (t.progress >= 100 ? " done" : "") }, el("i", { style: `width:${t.progress}%`, dataset: { k: `t:${c.id}:${t.label}` } })),
      t.progress >= 100 ? trackDone(t) : el("span", { textContent: `${Math.round(t.progress)}%` }));
    const byPart = Object.groupBy(tracks, (t) => /^Part (\d+)/.exec(t.label)?.[1] || "");
    const numbers = Object.keys(byPart).filter(Boolean);
    // An episode in parts (Koh-Lanta on Molotov): each part under its own head, how far it is, then its tracks
    parts.push(numbers.length > 1 ? el("div", { className: "cd-tracks" }, ...numbers.map((n) => {
      const list = byPart[n], done = list.every((t) => t.progress >= 100);
      const pct = Math.round(list.reduce((sum, t) => sum + Math.min(100, t.progress), 0) / list.length);
      return el("div", { className: `tpart${done ? " done" : ""}` },
        el("div", { className: "tpart-head" }, el("b", { textContent: `Part ${n}` }), el("small", { textContent: `of ${numbers.length}` }),
          done ? (() => { const d = trackDone({ took: null }); d.append("Done"); return d; })() : el("span", { className: "now", textContent: `${pct}%` })),
        ...list.map(row));
    })) : el("div", { className: "cd-tracks" }, ...tracks.map(row)));
  }
  return parts;
}
/* How a download runs, out of the way: an ⓘ opening a small menu (where, proxy, CDM, the episode on the service,
   the attempt, and the same as a command to copy). Closed by a click elsewhere or Escape. */
let setupPop = null;
const closeSetup = () => { setupPop?.remove(); setupPop = null; };
document.addEventListener("pointerdown", (e) => { if (setupPop && !setupPop.contains(e.target) && !e.target.closest?.(".setup-btn")) closeSetup(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSetup(); });
function setupButton(c, className = "jact") {
  if (!c.setup) return "";
  const b = el("button", { type: "button", className: `${className} setup-btn`, ariaLabel: "How it runs",
    onclick: (e) => { e.stopPropagation(); openSetup(c, e.currentTarget); } });
  b.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>';
  return b;
}
function openSetup(c, at) {
  const again = setupPop?.dataset.id === c.id;
  closeSetup();
  if (again) return;
  hideTip();
  setupPop = el("div", { className: "setup-pop", dataset: { id: c.id } }, ...setupBox(c));
  document.body.append(setupPop);
  onTop(setupPop);
  const r = at.getBoundingClientRect(), p = setupPop.getBoundingClientRect();
  setupPop.style.left = `${Math.min(Math.max(8, r.right - p.width), innerWidth - p.width - 8)}px`;
  setupPop.style.top = `${r.bottom + 8 + p.height > innerHeight - 8 ? Math.max(8, r.top - p.height - 8) : r.bottom + 8}px`;
}
function setupBox(c) {
  const known = "proxy" in c || c.setup.proxy === "none";
  const rows = [["Runs on", c.setup.via], ["On the service", c.serviceEpisode !== c.sxxeyy && c.serviceEpisode],
    ["Proxy", proxyOf(c) || (known ? "None" : "")], ["CDM", cdmOf(c)], ["Attempt", c.attempts > 1 && String(c.attempts)]].filter(([, v]) => v);
  const copy = el("button", { type: "button", className: "jact", ariaLabel: "Copy the command",
    onclick: () => navigator.clipboard.writeText(c.setup.command).then(() => toast("Command copied"), () => toast("The browser refused to copy", true)) });
  copy.innerHTML = ICON_COPY;
  return [el("dl", {}, ...rows.flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })])),
    el("div", { className: "cmd" }, el("code", { textContent: c.setup.command }), copy)];
}
$("#cd-raw").addEventListener("toggle", () => {
  if (!$("#cd-raw").open) return;
  if (picked_run?.startsWith("job:")) { if (termFor !== picked_run) streamJob(picked_run.slice(4)); return; }
  const c = runCards.find((x) => x.id === picked_run);
  if (c && !c.waiting && termFor !== c.id) streamRun(c);
});
/* The output in full (Debug) or its essentials: what happens, warnings and errors, without Unshackle's own log. */
let termMode = (() => { try { return localStorage.getItem("unshacklarr.termMode") || "essentials"; } catch { return "essentials"; } })();
const DROPPED = /\x1b\[90m {2}\d\d:\d\d:\d\d (?:DEBUG|INFO) |\x1b\[90mdebug · /;  // Unshackle's INFO and DEBUG lines, the debug traces
function termFilter() {
  const decoder = new TextDecoder();
  let carry = "";
  return (bytes) => {
    const text = carry + decoder.decode(bytes, { stream: true }), cut = text.lastIndexOf("\n");
    let out = cut < 0 ? "" : text.slice(0, cut + 1);
    carry = cut < 0 ? text : text.slice(cut + 1);
    if (termMode === "essentials") out = out.split(/(?<=\n)/).filter((line) => !DROPPED.test(line)).join("");
    if (carry && !/\x1b\[90m {2}\d\d:|\x1b\[90mdebug/.test(carry)) { out += carry; carry = ""; }  // a progress line, redrawn in place: now
    return out;
  };
}
function showTermMode() { document.querySelectorAll(".term-mode button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === termMode))); }
document.querySelectorAll(".term-mode button").forEach((b) => b.onclick = (e) => {
  e.preventDefault();  // in the output's head: a click here doesn't fold it
  termMode = b.dataset.mode;
  try { localStorage.setItem("unshacklarr.termMode", termMode); } catch { /* this visit only */ }
  showTermMode();
  const was = termFor;
  termFor = null;
  if (was?.startsWith("job:")) streamJob(was.slice(4));
  else { const c = runCards.find((x) => x.id === was); if (c) streamRun(c); }
});
showTermMode();
async function pauseJob(j, pause, button) {
  button.disabled = true;
  runCards = runCards.map((c) => (c.batch === j.job && c.waiting ? { ...c, paused: pause } : c));  // at once
  renderConsole();
  try {
    await api(`/api/jobs/${encodeURIComponent(j.job)}/${pause ? "pause" : "resume"}`, { method: "POST" });
    toast(pause ? "Job paused: the current download finishes, then the job waits" : "Job resumed");
  } catch (e) { toast(e.message, true); }
  openActivity();
}
async function stopJob(j, button) {
  button.disabled = true;
  const queued = j.cards.filter((c) => c.waiting).length;
  cancelLocally((c) => c.batch === j.job && c.waiting);  // at once, not at the next reload
  try {
    await api(`/api/jobs/${encodeURIComponent(j.job)}/stop`, { method: "POST" });
    toast(["Job stopped", queued && `${queued} cancelled`].filter(Boolean).join(" · "));
  } catch (e) { toast(e.message, true); }
  openActivity();
}
/* Queued episodes cancelled: shown so at once (a redraw would otherwise bring back a live button to click twice). */
function cancelLocally(test) {
  const now = new Date().toISOString();
  runCards = runCards.map((c) => (test(c) ? { ...c, waiting: false, outcome: "cancelled", ended: now } : c));
  renderConsole();
}
async function requeue(c) {
  const was = c.id;
  runCards = runCards.map((x) => (x.id === was ? { ...x, id: `waiting-${c.episodeId}`, waiting: true, outcome: "running", ended: null, step: "queued" } : x));
  renderConsole();  // queued at once
  try {
    await api(`/api/queue/${c.episodeId}/add`, { method: "POST", body: JSON.stringify({ batch: c.batch, run: was }) });
    toast(`${c.sxxeyy} queued`);
  } catch (e) { toast(e.message, true); }
  openActivity();
}
async function unqueue(c) {
  cancelLocally((x) => x.id === c.id);
  try {
    await api(`/api/queue/${c.episodeId}/remove`, { method: "POST" });
    toast(`${c.sxxeyy} cancelled`);
  } catch {
    toast(`${c.sxxeyy} had already started: stop it from its card`);
  }
  openActivity();
}
async function stopRun(c, button) {
  button.disabled = true;
  try { await api(`/api/runs/${encodeURIComponent(c.id)}/stop`, { method: "POST" }); toast(`Stopping ${c.series} ${c.sxxeyy}`); }
  catch (e) { button.disabled = false; toast(e.message, true); }
}
/* What the service waits for from you (a TV login): the link to open and the code to enter. */
function actionBox(c) {
  const a = c.action, left = a.expires_at ? Math.max(0, Math.round(a.expires_at - Date.now() / 1000)) : null;
  const code = el("button", { className: "act-code", type: "button", textContent: a.code, title: "Copy the code", onclick: async () => {
    try { await navigator.clipboard.writeText(a.code); toast("Code copied"); } catch { /* no clipboard: it is on screen */ }
  } });
  return el("div", { className: "act-box", role: "status" },
    el("b", { textContent: `${a.service || c.service}: ${a.message || "action needed"}` }),
    el("span", { textContent: "Open the link, log in if asked, and enter this code. The download goes on by itself." }),
    code,
    el("div", { className: "act-row" },
      // a link only to a web page: the service's words are data, never a javascript: URL
      /^https:\/\/[^\s"<>]+$/.test(a.url || "") ? el("a", { className: "btn primary", href: a.url, target: "_blank", rel: "noopener", textContent: `Open ${a.url.replace(/^https:\/\/(www\.)?/, "")}` })
        : el("span", { textContent: a.url || "" }),
      left !== null ? el("small", { textContent: left ? `expires in ${Math.ceil(left / 60)} min` : "expired: the next try asks for a new code" }) : ""));
}
/* A question the service asks while downloading (an OTP code, a PIN): a field, and the answer sent to it. */
const promptDrafts = {};  // what was typed, kept across the page's redraws
function promptBox(p) {
  const field = el("input", { type: "text", autocomplete: "one-time-code", inputMode: /code|pin/i.test(p.text) ? "numeric" : "text",
    ariaLabel: p.text, placeholder: "Your answer", value: promptDrafts[p.id] || "", spellcheck: false, dataset: { promptId: p.id } });
  field.oninput = () => { promptDrafts[p.id] = field.value; };
  const send = el("button", { className: "btn primary", type: "submit", textContent: "Send" });
  const form = el("form", { className: "act-row", onsubmit: async (e) => {
    e.preventDefault();
    if (!field.value.trim()) return field.focus();
    send.disabled = true;
    try {
      await api(`/api/runs/${encodeURIComponent(p.run)}/input`, { method: "POST", body: JSON.stringify({ response: field.value }) });
      delete promptDrafts[p.id];
      toast("Sent: the download goes on");
    } catch (err) { send.disabled = false; toast(err.message, true); }
  } }, field, send);
  return el("div", { className: "act-box", role: "status" },
    el("b", { textContent: `${p.service} asks` }), el("span", { className: "q", textContent: p.text }), form);
}
/* A redraw rebuilds the answer field: the one being typed in keeps its focus and caret. */
function keepPromptFocus(draw) {
  const was = document.activeElement?.dataset?.promptId, at = document.activeElement?.selectionStart;
  draw();
  const field = was && document.querySelector(`input[data-prompt-id="${was}"]`);
  if (field) { field.focus(); field.setSelectionRange(at ?? field.value.length, at ?? field.value.length); }
}
