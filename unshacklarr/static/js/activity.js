/* Activity. The list holds what runs, then the history; the picked download shows
   its steps, a bar per track and why it ended, with Unshackle's raw output (xterm) folded underneath. */
const TERM_COLS = 110;
const OUTCOME = { running: "Running", downloaded: "Downloaded", failed: "Failed", kept: "Kept", import: "Import needed", interrupted: "Interrupted", stopped: "Stopped", unavailable: "Not out yet", cancelled: "Cancelled" };
/* A separate programme no search found has aired: it is not on the service, rather than not out yet */
const outcomeOf = (c) => c.outcome === "unavailable" && c.notOn ? `Not on ${c.notOn}` : OUTCOME[c.outcome] || c.outcome;
const KIND = { auto: "automatic sync", burst: "release time", manual: "started by hand", retry: "retried by hand", upgrade: "better language" };
let term = null, termFit = null, termSocket = null, termFor = null, runsShown = 50, runsTimer = null;
let runCards = [], picked_run = null, conFilter = "all";
let landOnLive = false;  // the menu's Activity was clicked: the next list shows what runs
const speedLog = {};  // run id -> [[time, progress]], for the time left

function duration(ms) {
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")}` : `${Math.floor(s / 3600)} h ${String(Math.round(s / 60) % 60).padStart(2, "0")}`;
}
const posterOf = (c) => { const s = S.series.find((x) => x.tvdbId === c.tvdbId); return posterSrc(s?.poster, s?.title || c.series); };
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
  catch (e) {
    if (seq !== runsSeq) return;
    $("#runs-live").replaceChildren(failed(`Could not load the downloads: ${e.message}`, openActivity));
    clearTimeout(runsTimer);
    runsTimer = setTimeout(activityLater, 10000);  // back from a sleep, the network may not be up yet: tried again
    return;
  }
  if (seq !== runsSeq) return;  // a newer call is on its way: this answer may predate a Stop
  runCards = cards;
  clearTimeout(runsTimer);
  renderConsole();
  api("/api/leftovers").then((r) => { $("#con-left-n").textContent = r.items.length ? `(${r.items.length})` : ""; }).catch(() => {});
  if ($("#tab-log").hidden) return;
  followLive();
  runsTimer = setTimeout(activityLater, liveFeed ? 15000 : runCards.some(isLive) ? 2000 : 15000);  // without the feed: polling
}
function activityLater() {  // the history asked again, not while the tab is out of sight (left open for days)
  if (document.hidden) runsTimer = setTimeout(activityLater, 15000); else openActivity();
}
/* The downloads going on now, pushed by the server as they move; the whole list again when one starts or ends. */
let liveFeed = null, liveNext = null, liveDrawn = 0;
function followLive() {
  if (liveFeed || !window.EventSource || document.hidden) return;
  liveFeed = new EventSource("/api/runs/live");
  liveFeed.onmessage = (e) => {
    if ($("#tab-log").hidden) { liveFeed.close(); liveFeed = null; return; }
    if (document.hidden) return;  // nothing drawn out of sight: the next message, once back, draws it
    // several messages waiting (a slow phone, a tab just back, a long job): only the last one is drawn, once a second
    if (liveNext === null) setTimeout(() => requestAnimationFrame(() => { const data = liveNext; liveNext = null; liveDrawn = Date.now(); liveTick(data); }),
      Math.max(0, 1000 - (Date.now() - liveDrawn)));
    liveNext = e.data;
  };
  liveFeed.onerror = () => { liveFeed?.close(); liveFeed = null; };  // polling takes over; the next load tries again
}
function liveTick(data) {
  if (data === null || $("#tab-log").hidden) return;
  const live = JSON.parse(data), ids = new Set(live.map((c) => c.id));
  const known = new Set(runCards.filter((c) => isLive(c) && !c.waiting).map((c) => c.id));  // the queued aren't in the feed
  if (live.length !== known.size || live.some((c) => !known.has(c.id))) return openActivity();  // one started or ended
  runCards = runCards.map((c) => (ids.has(c.id) ? live.find((x) => x.id === c.id) : c));
  renderConsole(true);
}
/* Out of sight, Activity stops its live feed and Unshackle's output: a hidden tab runs its timers once a minute
   after 5 minutes, and the output piled up meanwhile froze the page when it came back. Back in sight, it starts
   again from what is now, the output from its end. */
let termResume = null;
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    liveFeed?.close(); liveFeed = null; liveNext = null;
    if (termSocket) { termResume = termQuery; termSocket.onmessage = null; termSocket.close(); termSocket = null; }
    return;
  }
  if ($("#tab-log").hidden) { termResume = null; return; }
  if (termResume && termFor) streamTerm(termFor, termResume);
  termResume = null;
  openActivity();
});
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
      kept: (c) => c.outcome === "kept" || c.outcome === "import", stopped: (c) => ["stopped", "interrupted", "cancelled"].includes(c.outcome) };
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
  if (!img || img.getAttribute("src") !== src) thumbs.set(key, img = el("img", { alt: "", loading: "lazy", src, onerror: posterFail("") }));
  return img;
}
const itemKey = (i) => (i?.job ? `job:${i.job}` : i?.id);
const itemRow = (i) => (i.job ? jobRow(i) : listRow(i));
const FINISHING = new Set(["joining", "finishing", "renaming", "importing"]);
/* Where a job's episodes stand: counts, and how far it is as a whole. */
/* Where an episode of a job stands, as its counts say it: waiting for a slot counts as queued (by mj23au, #10). */
function cardKind(c) {
  if (c.waiting || (isLive(c) && c.step === "queued")) return "queued";
  if (isLive(c)) return FINISHING.has(c.step) ? "finishing" : "downloading";
  if (["downloaded", "kept", "import"].includes(c.outcome)) return "done";
  return c.outcome === "failed" || c.outcome === "cancelled" ? c.outcome : "stopped";
}
function jobState(j) {
  const n = { done: 0, failed: 0, stopped: 0, cancelled: 0, downloading: 0, finishing: 0, queued: 0 };
  let progress = 0;
  for (const c of j.cards) {
    const kind = cardKind(c);
    n[kind]++;
    progress += kind === "queued" ? 0 : kind === "finishing" ? 0.95 : kind === "downloading" ? (Number(c.live?.progress) || 0) / 100 * 0.9 : 1;
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
const jobHidden = new Set();  // the kinds of episodes hidden in a job (done, queued…), from its counts
const jobCards = new Map();  // card id -> {sig, li}: the cards of the job shown, kept while they don't change
const openCards = new Set();  // a job's cards unfolded: they stay so as the job moves on
let justOpened = null;  // the card unfolding now: it alone fades in
let following = null;  // a card unfolded by hand while downloading: once done, the next download unfolds in its place
const ICON_SPEED = '<path d="M13 3 4 14h7l-1 7 9-11h-7z"/>', ICON_CLOCK = '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>';
const ICON_GLOBE = '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>', ICON_SWAP = '<path d="M4 8h13l-3-3M20 16H7l3 3"/>';
const ICON_FILM = '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 5v14M17 5v14M3 12h4M17 12h4"/>';
/* What serve does once every track is in, before the file is made: decrypting them (a DRM title), repackaging,
   muxing. "" while it still downloads. Without it, a card showed 96% and a stale time left, and seemed stuck. */
function afterTracks(c) {
  const lv = c.live || {}, phase = String(lv.phase || "");
  if (phase.startsWith("mux")) return muxPct(c) != null ? `Muxing ${muxPct(c)}%` : "Muxing";
  if (phase.startsWith("repack")) return "Repackaging";
  if (lv.total_tracks && Number(lv.completed_tracks) >= Number(lv.total_tracks)) return c.cdm ? "Decrypting" : "Processing the tracks";
  return "";
}
/* How far serve's mux is, from the file it writes in its work folder (Unshacklarr reads that folder when it is
   mounted, UNSHACKLE_TEMP_DIR): a percentage, and the time left at the pace it goes; null when unknown. */
const muxPct = (c) => c.mux?.total ? Math.min(99, Math.floor((c.mux.done / c.mux.total) * 100)) : null;
const muxLog = {};  // run id -> [[time, bytes written]]
function muxLeft(c) {
  if (!c.mux?.total) return "";
  const log = (muxLog[c.id] ||= []), now = Date.now();
  if (!log.length || log[log.length - 1][1] !== c.mux.done) log.push([now, c.mux.done]);
  while (log.length > 20) log.shift();
  const [t0, d0] = log[0], rate = (c.mux.done - d0) / ((now - t0) / 1000);
  return rate > 0 ? duration(((c.mux.total - c.mux.done) / rate) * 1000) : "";
}
const since = (iso) => iso ? duration(Date.now() - new Date(iso)) : "";
/* The proxy a download goes through: serve's word once it has one, else what was asked; "" for none. */
function proxyOf(c) {
  if (c.proxy) return c.proxy.provider ? [c.proxy.provider, c.proxy.region?.toUpperCase()].filter(Boolean).join(" · ") : "Custom proxy";
  const asked = c.setup?.proxy;
  return !asked || asked === "none" || "proxy" in c ? "" : asked.includes("//") ? "Custom proxy" : asked.toUpperCase();
}
/* The video asked for this download over the series' ladder: "1080p H.264"; "" when none was. */
function videoOf(c) {
  const v = c.video_override;
  if (!v) return "";
  return [v.quality && `${v.quality}p`, { AVC: "H.264", HEVC: "H.265" }[v.vcodec] || v.vcodec].filter(Boolean).join(" ");
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
    st.live && st.n.queued ? el("button", { className: "btn small", textContent: "Options", title: "Download options for the episodes still to come in this job",
      onclick: () => openJobOptions(j) }) : "",
    st.live ? el("button", { className: "btn small danger", textContent: "Stop the job", title: "Stops the current download. The queued episodes don't start",
      onclick: (e) => stopJob(j, e.target) }) : "",
    retryable.length ? el("button", { className: "btn small primary", textContent: `Retry ${retryable.length} failed`,
      title: st.live ? "Downloads them again, before the episodes still to come" : "Downloads them again, in this job",
      onclick: (e) => retryJob(j, retryable, e.target) }) : "",
    !st.live ? el("button", { className: "btn small", textContent: "Delete the job", title: "Removes every episode of this job from the history",
      onclick: (e) => deleteJob(j, e.target) }) : ""].filter(Boolean);
  $("#cd-foot").replaceChildren(...acts.map((b) => { const copy = b.cloneNode(true); copy.onclick = b.onclick; return copy; }));
  const head = el("div", { className: "cd-head" }, thumb("detail", posterOf(first)),
    el("div", {}, el("h3", { textContent: title }),
      el("small", { className: "line1", textContent: `${first.service} · ${KIND[first.kind] || first.kind} · started ${day(started)} ${started.toLocaleTimeString(LOCALE)}` }),
      el("small", { className: "line2" }, el("b", { textContent: first.service }), `${KIND[first.kind] || first.kind} · ${day(started)} ${time(started)}`)),
    el("div", { className: "act" }, ...acts));
  // each count shows or hides its episodes: a job of 200 episodes, its failed ones alone in sight
  const counts = el("div", { className: "job-counts" }, ...(st.paused ? [el("span", { className: "paused", textContent: "⏸ Paused: the next episode waits" })] : []), ...[["done", "Done", "ok"], ["downloading", "Downloading", "now"], ["finishing", "Finishing", "now"],
    ["queued", "Queued", ""], ["failed", "Failed", "err"], ["stopped", "Stopped", ""], ["cancelled", "Cancelled", ""]]
    .filter(([k]) => st.n[k]).map(([k, label, cls]) => el("button", { type: "button", className: `${cls}${jobHidden.has(k) ? " off" : ""}`, ariaPressed: String(!jobHidden.has(k)),
      title: jobHidden.has(k) ? "Show these episodes" : "Hide these episodes", onclick: () => { jobHidden.has(k) ? jobHidden.delete(k) : jobHidden.add(k); renderConsole(); } },
      el("b", { textContent: st.n[k] }), label)));
  const overall = el("div", { className: "job-sum" }, counts,
    el("span", { className: "bar" }, el("i", { style: `width:${st.progress}%`, dataset: { k: `job:${j.job}` } })));
  const isDownloading = (c) => isLive(c) && !c.waiting && !FINISHING.has(c.step) && !isQueued(c);
  const followed = j.cards.find((c) => c.id === following);
  const next = followed && !isDownloading(followed) && !isQueued(followed) && j.cards.find(isDownloading);
  if (next) { openCards.delete(followed.id); openCards.add(next.id); following = next.id; }
  const shown = j.cards.filter((c) => !jobHidden.has(cardKind(c)));
  const cards = shown.map((c) => {
    // a card that doesn't move (done, queued, folded) is the same element as at the last draw: a job of 200 episodes
    // redrawn each second rebuilt 200 cards and froze the page
    const still = !openCards.has(c.id) && (c.waiting || !isLive(c));
    const sig = still && JSON.stringify([c.outcome, c.ended, c.cause, c.size, c.waiting, c.paused]);
    const kept = still && jobCards.get(c.id);
    if (kept && kept.sig === sig) return kept.li;
    const li = jobCard(c);
    if (still) jobCards.set(c.id, { sig, li }); else jobCards.delete(c.id);
    return li;
  });
  function jobCard(c) {
    const live = isLive(c);
    const label = c.waiting ? "Queued" : live ? (FINISHING.has(c.step) ? { joining: "Joining parts", finishing: "Finishing", renaming: "Renaming", importing: "Importing" }[c.step]
      : isQueued(c) ? "Waiting for a slot" : afterTracks(c) || `Downloading ${Math.round(Number(c.live?.progress) || 0)}%`) : outcomeOf(c);
    const tone = c.waiting ? "" : live ? "now" : c.outcome === "downloaded" ? "ok" : c.outcome === "failed" ? "err" : c.outcome === "kept" || c.outcome === "import" ? "warn" : "";
    const downloading = isDownloading(c);
    const detail = downloading ? el("div", { className: "jmeta" },  // how fast, how long still: in sight, not a grey line
        ...[...(afterTracks(c) ? [[ICON_CLOCK, muxLeft(c) ? `${afterTracks(c)} · ${muxLeft(c)} left` : `${afterTracks(c)} · ${since(c.mux_from || c.tracks_in)}`]] : [[ICON_SPEED, speedOf(c)], [ICON_CLOCK, timeLeft(c) && `${timeLeft(c)} left`]]), [ICON_GLOBE, proxyOf(c), "extra"], [ICON_FILM, videoOf(c), "extra"],
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
  }
  // the same job drawn again: its list keeps its elements, only the cards that changed are put in
  let list = $("#cd-main").querySelector(`ol.job-cards[data-job="${j.job}"]`);
  if (!list) { list = el("ol", { className: "job-cards", dataset: { job: j.job } }); jobCards.clear(); }
  cards.forEach((li, i) => { if (list.children[i] !== li) list.insertBefore(li, list.children[i] || null); });
  while (list.children.length > cards.length) list.lastChild.remove();
  const sum = st.live ? overall : el("div", { style: "display:grid;gap:12px" }, jobDone(j, st), counts), box = list.parentElement;
  keepPromptFocus(() => box?.isConnected ? (box.children[0].replaceWith(head), box.children[1].replaceWith(sum))  // the list stays where it is
    : $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:18px" }, head, sum, list)));
  glideBars($("#cd-main"));
  $("#cd-raw").hidden = false;
  if ($("#cd-raw").open && termFor !== `job:${j.job}`) streamJob(j.job);
  const now = j.cards.find(isDownloading) || j.cards.find((c) => isLive(c) && !c.waiting && !isQueued(c));
  renderDock({ ...(now ? dockOf(now) : { ep: `${j.cards.length} episodes`, line: st.words.join(" · "),
    pct: st.live ? `${st.progress}%` : st.n.failed ? `${st.n.failed} failed` : "✓", tone: st.live ? "" : st.n.failed ? "err" : "ok", progress: st.progress }),
    acts: [
      st.live && st.n.queued ? { key: "pause", label: st.paused ? "Resume the job" : "Pause the job", icon: st.paused ? DOCK.play : DOCK.pause, cls: st.paused ? "on" : "",
        go: (b) => pauseJob(j, !st.paused, b) } : null,
      st.live && st.n.queued ? { key: "opts", label: "Options of this job", icon: DOCK.opts, go: () => openJobOptions(j) } : null,
      retryable.length ? { key: "retry", label: `Retry ${retryable.length} failed`, icon: DOCK.retry, cls: "retry", go: (b) => retryJob(j, retryable, b) } : null,
      st.live ? { key: "stop", label: "Stop the job", icon: DOCK.stop, cls: "stop", go: (b) => stopJob(j, b) } : null].filter(Boolean) });
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
  const done = j.cards.filter((c) => ["downloaded", "kept", "import"].includes(c.outcome)), bytes = done.reduce((n, c) => n + (c.size || 0), 0);
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
    : el("span", { className: `chip o-${c.outcome}`, textContent: outcomeOf(c) });
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
  $("#cd-raw").hidden = true; renderDock(null);
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
    const poster = posterSrc(S.series.find((x) => x.tvdbId === it.tvdbId)?.poster, it.series);
    const age = Math.floor((Date.now() / 1000 - it.since) / 86400);
    return el("div", { className: "left-row" }, el("img", { alt: "", loading: "lazy", src: poster, onerror: posterFail(it.series) }),
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
let missingDays = 30, missingSonarr = "";
/* Catch up and Upgrades for another Sonarr (a 4K one) too: which one, beside the rest; one whose ladder was never chosen
   says so, and nothing is downloaded for it. */
function sonarrPicker(picked, pick) {
  const others = S.config.settings.sonarrs || [];
  if (!others.length) return null;
  return el("div", { className: "ax-seg", role: "radiogroup", ariaLabel: "Which Sonarr" },
    ...["", ...others.map((i) => i.name)].map((n) => el("button", { type: "button", role: "radio", textContent: n || mainSonarr(),
      ariaChecked: String(picked === n), onclick: () => pick(n) })));
}
function unsetNote(name) {
  const inst = (S.config.settings.sonarrs || []).find((i) => i.name === name);
  return inst && !inst.quality_ladder ? el("p", { className: "status-err", textContent: `Sonarr ${name} has no quality ladder chosen: nothing is downloaded for it until you choose one in Settings, Sonarr.` }) : null;
}
async function showMissing() {
  picked_run = "missing";
  $("#con").classList.add("open");
  document.querySelectorAll(".crow").forEach((r) => r.classList.toggle("sel", r.id === "con-missing"));
  $("#cd-top-title").textContent = "Catch up";
  $("#cd-raw").hidden = true; renderDock(null);
  $("#cd-foot").replaceChildren();
  $("#cd-main").replaceChildren(loading("Asking Sonarr what your series miss…"));
  let r;
  if (missingSonarr && !(S.config.settings.sonarrs || []).some((i) => i.name === missingSonarr)) missingSonarr = "";
  const q = new URLSearchParams({ ...(missingDays ? { days: missingDays } : {}), ...(missingSonarr ? { sonarr: missingSonarr } : {}) });
  try { r = await api(`/api/missing${q.size ? `?${q}` : ""}`); } catch (e) { return $("#cd-main").replaceChildren(failed(e.message, showMissing)); }
  if (picked_run !== "missing") return;
  const ages = el("div", { className: "ax-seg", role: "radiogroup", ariaLabel: "Aired within" }, ...[[7, "7 days"], [30, "30 days"], [90, "90 days"], [0, "All"]].map(([d, label]) =>
    el("button", { type: "button", role: "radio", textContent: label, ariaChecked: String(missingDays === d), onclick: () => { missingDays = d; showMissing(); } })));
  const unset = unsetNote(missingSonarr);
  const go = el("button", { className: "btn primary", textContent: r.items.length === 1 ? "Download it" : `Download the ${r.items.length}`, hidden: !r.items.length || !!unset,
    onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: r.items.map((x) => x.episodeId), ...(missingSonarr ? { sonarr: missingSonarr } : {}) }) });
        toast(r.items.length === 1 ? "1 episode queued" : `${r.items.length} episodes queued, one after the other`);
      } catch (err) { e.target.disabled = false; toast(err.message, true); }
    } });
  const rows = r.items.map((it) => {
    const poster = posterSrc(S.series.find((x) => x.tvdbId === it.tvdbId)?.poster, it.series);
    return el("div", { className: "left-row" }, el("img", { alt: "", loading: "lazy", src: poster, onerror: posterFail(it.series) }),
      el("div", {}, el("b", { textContent: `${it.series} ${it.sxxeyy}` }),
        el("small", {}, ...(it.title ? [episodeTitle(it.tvdbId, it.sxxeyy, it.title), " · "] : []), `aired ${ago(it.aired)}`)));
  });
  $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:14px" },
    el("div", { className: "cd-head" }, el("div", {}, el("h3", { textContent: "Catch up" }),
      el("small", { className: "line1", textContent: "Episodes that aired, are monitored and have no file in Sonarr, for the series Unshackle downloads. The automatic sync only looks at recent ones." }))),
    ...[sonarrPicker(missingSonarr, (n) => { missingSonarr = n; showMissing(); }), unset].filter(Boolean),
    el("div", { className: "miss-bar" }, ages, go),
    ...(rows.length ? [el("div", {}, ...rows)] : [el("p", { className: "muted", textContent: missingDays ? `Nothing missing from the last ${missingDays} days.` : "Nothing missing: every aired episode has its file." })])));
}
$("#con-missing").onclick = showMissing;

/* Upgrades: the files of the series with a quality ladder that are not on its first step, checked against the
   service's tracks (a series' episodes 10 at a time, in the background; answers kept); those it has on an earlier step are replaced. */
let upgradeTimer = null, upgradePick = new Set(), upgradeOpen = new Set(), upgradeSonarr = "";
let upgradeLast = null;  // the last answer: a tick of a box draws again from it, without asking the server
async function showUpgrades(fresh = true) {
  picked_run = "upgrades";
  clearTimeout(upgradeTimer);
  $("#con").classList.add("open");
  document.querySelectorAll(".crow").forEach((r) => r.classList.toggle("sel", r.id === "con-upgrades"));
  $("#cd-top-title").textContent = "Upgrades";
  $("#cd-raw").hidden = true; renderDock(null);
  $("#cd-foot").replaceChildren();
  let r;
  if (upgradeSonarr && !(S.config.settings.sonarrs || []).some((i) => i.name === upgradeSonarr)) upgradeSonarr = "";
  if (fresh !== false || !upgradeLast) {
    try { r = upgradeLast = await api(`/api/upgrades${upgradeSonarr ? `?sonarr=${encodeURIComponent(upgradeSonarr)}` : ""}`); } catch (e) { return $("#cd-main").replaceChildren(failed(e.message, showUpgrades)); }
  } else r = upgradeLast;
  if (picked_run !== "upgrades") return;
  const scan = r.scan, free = r.items.filter((i) => !i.running), picked = free.filter((i) => upgradePick.has(i.episodeId));
  // every series, or one from its card (asked afresh); the ticks only choose what is replaced (by mj23au, #18)
  const checkUp = async (button, only = []) => {
    button.disabled = true;
    try { await api(scan.running ? "/api/upgrades/stop" : "/api/upgrades/check", { method: "POST", body: JSON.stringify({ sonarr: upgradeSonarr, series: only }) }); } catch (err) { toast(err.message, true); }
    showUpgrades();
  };
  // a stopped check ends the batch it is asking (up to 10 episodes of a series): said, so Stop is seen to work
  const check = el("button", { className: "btn" + (scan.running ? " danger" : ""), disabled: !!scan.stopping,
    textContent: scan.stopping ? "Stopping after this batch…" : scan.running ? "Stop the check" : r.checked ? "Check again" : "Check now", onclick: (e) => checkUp(e.target) });
  const go = el("button", { className: "btn primary", hidden: !picked.length || scan.running,
    textContent: picked.length === 1 ? "Replace it" : `Replace the ${picked.length} selected`, onclick: async (e) => {
      e.target.disabled = true;
      try {
        await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: picked.map((i) => i.episodeId), replace: true, ...(upgradeSonarr ? { sonarr: upgradeSonarr } : {}) }) });
        toast(picked.length === 1 ? "1 episode queued, replacing its file" : `${picked.length} episodes queued, replacing their files`);
        picked.forEach((i) => upgradePick.delete(i.episodeId));
        showUpgrades();
      } catch (err) { e.target.disabled = false; toast(err.message, true); }
    } });
  const state = scan.running ? `Checking ${scan.done} of ${scan.total}${scan.sonarr ? ` in ${scan.sonarr}` : ""}${scan.series ? ` · ${scan.series}` : ""}…`
    : r.checked ? `Checked ${ago(r.checked)}${r.stopped ? ", stopped before the end" : ""}${scan.errors ? ` · ${scan.errors} could not be asked` : ""}` : "Not checked yet.";
  const selAll = el("button", { className: "btn", hidden: !free.length || scan.running,
    textContent: picked.length === free.length ? "Select none" : "Select all",
    onclick: () => { if (picked.length === free.length) upgradePick.clear(); else free.forEach((i) => upgradePick.add(i.episodeId)); showUpgrades(false); } });
  const bySeries = new Map();
  for (const it of r.items) { const k = `${it.tvdbId}|${it.series}`; if (!bySeries.has(k)) bySeries.set(k, []); bySeries.get(k).push(it); }
  const FIN = { joining: "Joining parts", finishing: "Finishing", renaming: "Renaming", importing: "Importing" };
  const liveBy = new Map(runCards.filter((c) => isLive(c) && (c.instance || "") === upgradeSonarr).map((c) => [`${c.tvdbId}|${c.sxxeyy}`, c]));
  const liveOf = (it) => liveBy.get(`${it.tvdbId}|${it.sxxeyy}`);  // one lookup per row, not a scan of every card
  const stateOf = (c) => !c ? "" : c.waiting || isQueued(c) ? "Queued" : FIN[c.step] || `Downloading ${Math.round(Number(c.live?.progress) || 0)}%`;
  const rows = [...bySeries].map(([k, eps]) => {
    const poster = posterSrc(S.series.find((x) => x.tvdbId === eps[0].tvdbId)?.poster, eps[0].series);
    const ids = eps.filter((i) => !i.running).map((i) => i.episodeId), n = ids.filter((id) => upgradePick.has(id)).length;
    const all = el("input", { type: "checkbox", checked: ids.length > 0 && n === ids.length, indeterminate: n > 0 && n < ids.length, disabled: !ids.length,
      ariaLabel: `Select every ${eps[0].series} episode`,
      onclick: (e) => e.stopPropagation(),
      onchange: (e) => { ids.forEach((id) => (e.target.checked ? upgradePick.add(id) : upgradePick.delete(id))); showUpgrades(false); } });
    const busy = eps.filter((i) => i.running), queued = busy.filter((i) => { const c = liveOf(i); return !c || c.waiting || isQueued(c); }).length;
    const busyText = [busy.length - queued && `${busy.length - queued} downloading`, queued && `${queued} queued`].filter(Boolean).join(", ");
    const list = eps.map((it) => {
      const box = el("input", { type: "checkbox", checked: upgradePick.has(it.episodeId), disabled: it.running, ariaLabel: `Replace ${it.series} ${it.sxxeyy}`,
        onchange: (e) => { if (e.target.checked) upgradePick.add(it.episodeId); else upgradePick.delete(it.episodeId); showUpgrades(false); } });
      return el("label", { className: "left-row up-row up-ep" }, box,
        el("div", {}, el("b", { textContent: it.sxxeyy }),
          el("small", { textContent: `${it.file} here${it.fileStep ? ` (step ${it.fileStep})` : " (outside the ladder)"} → ${it.better} on the service (step ${it.betterStep} of ${it.ladder})` }),
          ...(it.running ? [el("small", { className: "left-hand up-live", textContent: `↓ ${stateOf(liveOf(it)) || "Being replaced now"}` })] : [])));
    });
    const card = el("details", { className: "up-card", open: upgradeOpen.has(k), ontoggle: (e) => { if (e.target.open) upgradeOpen.add(k); else upgradeOpen.delete(k); } },
      el("summary", { className: "left-row up-row" }, all, thumb(`up:${eps[0].tvdbId}`, poster),  // the same image every 3 s
        el("div", {}, el("b", { textContent: eps[0].series }),
          el("small", { textContent: [eps.length === 1 ? "1 episode" : `${eps.length} episodes`, n && `${n} selected`, busyText,
            r.seriesChecked?.[eps[0].tvdbId] && `checked ${ago(r.seriesChecked[eps[0].tvdbId])}`].filter(Boolean).join(" · ") }),
          ...(busy.length ? [el("small", { className: "left-hand up-live", textContent: "↓ Replacing" })] : [])),
        el("button", { type: "button", className: "btn small", textContent: "Check", hidden: scan.running,
          title: `Ask the service again about ${eps[0].series}`,
          onclick: (e) => { e.preventDefault(); e.stopPropagation(); checkUp(e.target, [eps[0].tvdbId]); } })),
      ...list);
    return card;
  });
  $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:14px" },
    el("div", { className: "cd-head" }, el("div", {}, el("h3", { textContent: "Upgrades" }),
      el("small", { className: "line1", textContent: "Files your quality ladder says could be better. Check now asks each service what it has, up to 10 episodes of a series at a time, and keeps each answer for the days set in Settings, Upgrades. Replace downloads the better ones over your files, even when Sonarr ranks both the same (H.264 and H.265 in 1080p)." }))),
    ...[sonarrPicker(upgradeSonarr, (n) => { upgradeSonarr = n; upgradePick.clear(); upgradeOpen.clear(); showUpgrades(); }), unsetNote(upgradeSonarr)].filter(Boolean),
    el("div", { className: "miss-bar" }, el("span", { className: "muted", textContent: state }), el("span", { className: "up-acts" }, selAll, check, go)),
    ...(rows.length ? [el("div", {}, ...rows)] : [el("p", { className: "muted", textContent: scan.running ? "Nothing better found yet." : r.checked ? "Nothing better on the services: every file is already on the best step they have." : "Check now asks each service what it has, one episode at a time." })])));
  // asked again every 3 s while something runs, but not while Activity or the tab is out of sight
  const tick = () => { if (picked_run !== "upgrades") return; if (document.hidden || $("#tab-log").hidden) upgradeTimer = setTimeout(tick, 3000); else showUpgrades(); };
  if (scan.running || r.items.some((i) => i.running)) upgradeTimer = setTimeout(tick, 3000);
}
$("#con-upgrades").onclick = showUpgrades;

/* Languages (Sonarr 5): the library's files without the preferred audio language, or without any accepted one, read
   from Sonarr's own tracks (nothing opened). Nothing ticked at first: the first are watched for their language (added,
   or the episode got again, once their service has it), the others downloaded again. */
let langTimer = null, langPick = new Set(), langLast = null;
async function showLanguages(fresh = true) {
  picked_run = "languages";
  clearTimeout(langTimer);
  $("#con").classList.add("open");
  document.querySelectorAll(".crow").forEach((r) => r.classList.toggle("sel", r.id === "con-languages"));
  $("#cd-top-title").textContent = "Languages";
  $("#cd-raw").hidden = true;
  $("#cd-foot").replaceChildren();
  let r;
  if (fresh !== false || !langLast) {
    try { r = langLast = await api("/api/languages"); } catch (e) { return $("#cd-main").replaceChildren(failed(e.message, showLanguages)); }
  } else r = langLast;
  if (picked_run !== "languages") return;
  const scan = r.scan;
  const check = el("button", { className: "btn" + (scan.running ? " danger" : ""), textContent: scan.running ? "Stop the check" : r.checked ? "Check again" : "Check now",
    onclick: async (e) => {
      e.target.disabled = true;
      try { await api(scan.running ? "/api/languages/stop" : "/api/languages/check", { method: "POST", body: "{}" }); } catch (err) { toast(err.message, true); }
      showLanguages();
    } });
  const state = scan.running ? `Checking ${scan.done} of ${scan.total}${scan.series ? ` · ${scan.series}` : ""}…`
    : r.checked ? `Checked ${ago(r.checked)}${r.stopped ? ", stopped before the end" : ""}${scan.errors ? ` · ${scan.errors} could not be asked` : ""}` : "Not checked yet.";
  const group = (lacks) => r.items.filter((i) => i.lacks === lacks);
  const section = (lacks, title, help, act) => {
    const items = group(lacks), free = items.filter((i) => !i.waiting), picked = free.filter((i) => langPick.has(i.episodeId));
    if (!items.length) return [];
    const go = el("button", { className: "btn primary small", hidden: !picked.length, textContent: act(picked.length), onclick: async (e) => {
      e.target.disabled = true;
      const ids = picked.map((i) => i.episodeId);
      try {
        if (lacks === "preferred") await api("/api/languages/wait", { method: "POST", body: JSON.stringify({ episodeIds: ids }) });
        else await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: ids, replace: true }) });
        toast(lacks === "preferred" ? `${ids.length} episode${ids.length > 1 ? "s" : ""} watched for their audio` : `${ids.length} episode${ids.length > 1 ? "s" : ""} queued again`);
        ids.forEach((id) => langPick.delete(id));
        showLanguages();
      } catch (err) { e.target.disabled = false; toast(err.message, true); }
    } });
    const all = el("button", { className: "btn small", hidden: !free.length, textContent: picked.length === free.length ? "Select none" : "Select all",
      onclick: () => { if (picked.length === free.length) free.forEach((i) => langPick.delete(i.episodeId)); else free.forEach((i) => langPick.add(i.episodeId)); showLanguages(false); } });
    const rows = items.map((it) => el("label", { className: "left-row up-row" },
      el("input", { type: "checkbox", checked: langPick.has(it.episodeId), disabled: it.waiting, ariaLabel: `${it.series} ${it.sxxeyy}`,
        onchange: (e) => { if (e.target.checked) langPick.add(it.episodeId); else langPick.delete(it.episodeId); showLanguages(false); } }),
      thumb(`lang:${it.episodeId}`, S.series.find((x) => x.tvdbId === it.tvdbId)?.poster || ""),  // one image per row
      el("div", {}, el("b", { textContent: `${it.series} ${it.sxxeyy}` }),
        el("small", { textContent: [`Audio: ${it.audio.join(", ")}`, it.forced.length && `forced subtitles: ${it.forced.join(", ")}`, it.file].filter(Boolean).join(" · ") }),
        ...(it.waiting ? [el("small", { className: "left-hand", textContent: `Watched for the ${it.want} audio` })] : []))));
    return [el("div", { className: "miss-bar" }, el("span", {}, el("b", { textContent: `${title} (${items.length})` }), el("br"), el("small", { className: "muted", textContent: help })),
      el("span", { className: "up-acts" }, all, go)), el("div", {}, ...rows)];
  };
  const parts = [
    ...section("preferred", "Without the preferred audio", "Watch for it: once the series' service has that language, it is added to the file or the episode is downloaded again, as set in Settings, Download options.",
      (n) => (n === 1 ? "Watch for it" : `Watch for the ${n}`)),
    ...section("accepted", "Without an accepted audio", "None of the series' accepted audio languages: download the episode again from its service.",
      (n) => (n === 1 ? "Download it again" : `Download the ${n} again`)),
  ];
  $("#cd-main").replaceChildren(el("div", { style: "display:grid;gap:14px" },
    el("div", { className: "cd-head" }, el("div", {}, el("h3", { textContent: "Languages" }),
      el("small", { className: "line1", textContent: "Your files without your audio languages, read from Sonarr 5's own tracks: no file is opened, and nothing is downloaded until you choose. Only the series set up here are checked, against their accepted and preferred audio languages." }))),
    el("div", { className: "miss-bar" }, el("span", { className: "muted", textContent: state }), el("span", { className: "up-acts" }, check)),
    ...(parts.length ? parts : [el("p", { className: "muted", textContent: scan.running ? "Nothing found yet." : r.checked ? "Every file has your audio languages." : "Check now reads each series' files from Sonarr." })])));
  const tick = () => { if (picked_run !== "languages") return; if (document.hidden || $("#tab-log").hidden) langTimer = setTimeout(tick, 3000); else showLanguages(); };
  if (scan.running) langTimer = setTimeout(tick, 3000);
}
$("#con-languages").onclick = showLanguages;

/* Stats: what the history adds up to, in the detail pane. */
async function showStats() {
  picked_run = "stats";
  $("#con").classList.add("open");
  document.querySelectorAll(".crow").forEach((r) => r.classList.toggle("sel", r.id === "con-stats"));
  $("#cd-top-title").textContent = "Stats";
  $("#cd-raw").hidden = true; renderDock(null);
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
let searchTimer = null;  // typed: the history filtered once the typing pauses, not at each key
$("#con-search").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(renderConsole, 150); };
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

const STEPS = ["Queued", "Downloading", "Decrypting and muxing", "Joining parts", "Renaming", "Sonarr import"];
const STEPS_SHORT = ["Queued", "Download", "Mux", "Join", "Rename", "Import"];
function stepStates(c) {
  const at = c.step === "downloading" && afterTracks(c) ? 2  // every track in: serve makes the file
    : { queued: 0, downloading: 1, joining: 3, finishing: 3, renaming: 4, importing: 5, done: 6 }[c.step] ?? 0;
  return STEPS.map((_, i) => {
    if (i === 3 && c.parts === 1) return "skip";  // one file: nothing to join
    if (c.outcome === "downloaded") return "done";
    if (c.outcome === "kept" || c.outcome === "import") return i < 5 ? "done" : "warn";
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
  renderDock(c.waiting ? null : dockOf(c));
}
/* The bar at the bottom of a job or a download: the episode going on (or how it ended), the job's actions on a phone
   (its head has them on a wide screen), and the output, opened over the page. Its elements stay from one draw to the
   next (a live download redraws each second): a press started on a button is not lost. */
const DOCK = {
  term: '<path d="m5 8 4 4-4 4M12 16h7"/>', up: '<path d="M6 15l6-6 6 6"/>', pause: '<path d="M8 5v14M16 5v14"/>', play: '<path d="M7 5v14l11-7z"/>',
  opts: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
  retry: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>', stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
};
function dockOf(c) {  // one download: where it stands, in a line
  const live = isLive(c), pct = Math.round(Number(c.live?.progress) || 0);
  const track = (c.tracks || []).find((t) => t.progress < 100)?.label;
  const line = live ? [afterTracks(c) || (FINISHING.has(c.step) ? c.step : track && `downloading ${track}`), speedOf(c), timeLeft(c) && `${timeLeft(c)} left`].filter(Boolean).join(" · ")
    : [outcomeOf(c), c.cause].filter(Boolean).join(" · ");
  return { ep: c.sxxeyy, line, pct: live ? `${pct}%` : c.outcome === "downloaded" ? "✓" : "", tone: live ? "" : c.outcome === "downloaded" ? "ok" : c.outcome === "failed" ? "err" : "",
    progress: live ? pct : 100, acts: [] };
}
function renderDock(d) {
  const dock = $("#dock"), detail = $("#con-detail");
  detail.classList.toggle("docked", !!d);
  detail.classList.toggle("docked-acts", !!d?.acts.length);
  dock.hidden = !d;
  if (!d) return closeSheet();
  if (!dock.firstChild) {
    const svg = (path) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true"); s.innerHTML = path; return s; };
    dock.append(el("span", { className: "d-ico" }, svg(DOCK.term)),
      el("div", { className: "d-mid" }, el("div", { className: "d-l1" }, el("b"), el("span"), el("em")), el("span", { className: "bar" }, el("i"))),
      el("div", { className: "d-acts" }), el("span", { className: "d-sep" }),
      el("button", { type: "button", className: "d-open", ariaLabel: "Show the output", onclick: () => ($("#cd-raw").classList.contains("sheet") ? closeSheet() : openSheet()) }, svg(DOCK.up)));
  }
  const [b, span, em] = dock.querySelector(".d-l1").children;
  b.textContent = d.ep; span.textContent = d.line; span.title = d.line; em.textContent = d.pct; em.className = d.tone || "";
  dock.querySelector(".bar i").style.width = `${d.progress}%`;
  const acts = dock.querySelector(".d-acts"), key = d.acts.map((a) => `${a.key}:${a.label}`).join("|");
  if (acts.dataset.key !== key) {  // rebuilt only when they change: a press is not lost to a redraw
    acts.dataset.key = key;
    acts.replaceChildren(...d.acts.map((a) => { const btn = el("button", { type: "button", className: a.cls || "", ariaLabel: a.label }); btn.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${a.icon}</svg>`; return btn; }));
  }
  [...acts.children].forEach((btn, i) => { btn.onclick = () => d.acts[i].go(btn); });  // the latest job state
  dock.querySelector(".d-sep").hidden = !d.acts.length;
}
function openSheet() {
  const raw = $("#cd-raw");
  raw.classList.add("sheet");
  raw.open = true;  // its toggle streams the output
}
function closeSheet() {
  const raw = $("#cd-raw");
  if (!raw.classList.contains("sheet")) return;
  raw.classList.remove("sheet");
  raw.open = false;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#cd-raw").classList.contains("sheet")) closeSheet(); });
document.addEventListener("click", (e) => {  // a click beside the output closes it
  const raw = $("#cd-raw");
  if (raw.classList.contains("sheet") && !raw.contains(e.target) && !$("#dock").contains(e.target)) closeSheet();
});
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
    !live && c.outcome === "import" ? act("import", "Import", importKept, "primary") : "",
    !live && c.episodeId && !["downloaded", "kept", "import"].includes(c.outcome) ? act("retry", "Retry", retryRun, "primary") : "",
    !live ? act("delete", "Delete", deleteRun, "danger") : "",
  ].filter(Boolean);
}
/* What can be done with an attempt: stop it, import it anyway, retry it, forget it. */
function runActs(c) {
  const live = isLive(c);
  return [
    setupButton(c),
    live && c.job_id ? el("button", { className: "btn small danger", textContent: "Stop", onclick: (e) => stopRun(c, e.target) }) : "",
    !live && (c.outcome === "kept" || c.outcome === "import") ? el("button", { className: "btn small primary", textContent: c.outcome === "kept" ? "Import anyway" : "Import", onclick: (e) => importKept(c, e.target) }) : "",
    !live && c.episodeId && !["downloaded", "kept", "import"].includes(c.outcome) ? el("button", { className: "btn small primary", textContent: "Retry", onclick: (e) => retryRun(c, e.target) }) : "",
    !live ? el("button", { className: "btn small", textContent: "Diagnostic", title: "A file to attach to a GitHub issue: versions, settings and this attempt's log, without passwords, keys or tokens",
      onclick: () => downloadDiagnostic(c) }) : "",
    !live ? el("button", { className: "btn small", textContent: "Delete", onclick: (e) => deleteRun(c, e.target) }) : "",
  ].filter(Boolean);
}
async function downloadDiagnostic(c) {
  try {
    const r = await fetch(`/api/runs/${encodeURIComponent(c.id)}/diagnostic`);
    if (!r.ok) throw new Error(await r.text() || r.statusText);
    const a = el("a", { href: URL.createObjectURL(await r.blob()), download: `unshacklarr-diagnostic-${c.id}.md` });
    a.click();
    URL.revokeObjectURL(a.href);
    toast("Diagnostic downloaded: check it, then attach it to the issue");
  } catch (e) { toast(e.message, true); }
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
const tracksOpen = new Set();  // attempts whose downloaded tracks were unfolded: they stay so as it moves on
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
  if (live && c.notice) parts.push(el("div", { className: "cd-note warn" },  // Unshackle 6: waits for an approval, nothing to type
    el("b", { textContent: `${c.service || "The service"} is waiting for you` }), el("span", { textContent: c.notice })));
  if (live) {
    const lv = c.live || {};
    const muxing = String(lv.phase || "").startsWith("mux");
    parts.push(el("div", { className: "cd-stats" },
      ...[["Progress", `${Math.round(Number(lv.progress) || 0)}%`],
        ...(muxing ? [["Now", "Muxing"], ["Muxed", c.mux?.total ? `${size(Math.min(c.mux.done, c.mux.total))} of ${size(c.mux.total)}` : since(c.mux_from)], ["Left", muxLeft(c) || "–"]]
          : afterTracks(c) ? [["Now", afterTracks(c)], ["For", since(c.tracks_in) || "–"]] : [["Speed", speedOf(c) || "–"], ["Left", timeLeft(c) || "–"]]),
        ...partStat(c), ...(muxing ? [] : [[partStat(c).length ? "Tracks in it" : "Tracks", lv.total_tracks ? `${lv.completed_tracks || 0} of ${lv.total_tracks}` : "–"]])]
        .map(([k, v]) => el("div", {}, el("small", { textContent: k }), el("b", { textContent: v })))));
    if (muxing) {  // the file being made: how far, or, without its work folder in reach, that it goes on
      const pct = muxPct(c);
      parts.push(el("div", { className: "cd-mux" },
        el("div", {}, el("b", { textContent: "Muxing" }), el("small", { textContent: pct != null
          ? `${pct}%${muxLeft(c) ? ` · ${muxLeft(c)} left` : ""}` : `for ${since(c.mux_from)}` })),
        el("span", { className: `bar${pct == null ? " busy" : ""}` }, el("i", { style: `width:${pct ?? 100}%`, dataset: { k: `mux:${c.id}` } }))));
    }
  } else {
    const note = { downloaded: ["ok", "Downloaded"], failed: ["err", "Failed"], kept: ["warn", "Sonarr kept its own file"], import: ["warn", "Downloaded, import needed"],
      stopped: ["mut", "Stopped"], interrupted: ["mut", "Interrupted: Unshacklarr stopped during it"],
      unavailable: ["mut", c.notOn ? `${outcomeOf(c)}: nothing to download` : "Not out yet: nothing to download"] }[c.outcome] || ["mut", c.outcome];
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
    const list = numbers.length > 1 ? el("div", { className: "cd-tracks" }, ...numbers.map((n) => {
      const list = byPart[n], done = list.every((t) => t.progress >= 100);
      const pct = Math.round(list.reduce((sum, t) => sum + Math.min(100, t.progress), 0) / list.length);
      return el("div", { className: `tpart${done ? " done" : ""}` },
        el("div", { className: "tpart-head" }, el("b", { textContent: `Part ${n}` }), el("small", { textContent: `of ${numbers.length}` }),
          done ? (() => { const d = trackDone({ took: null }); d.append("Done"); return d; })() : el("span", { className: "now", textContent: `${pct}%` })),
        ...list.map(row));
    })) : el("div", { className: "cd-tracks" }, ...tracks.map(row));
    // every track in: folded into one line, the steps after it in sight; a click shows them again
    if (tracks.every((t) => t.progress >= 100)) {
      const took = c.tracks_in && duration(new Date(c.tracks_in) - started);
      const head = el("summary", {}, trackDone({ took: null }),
        el("b", { textContent: tracks.length === 1 ? "1 track downloaded" : `${tracks.length} tracks downloaded` }),
        ...(took ? [el("small", { textContent: took })] : []));
      parts.push(el("details", { className: "cd-tracks-fold", open: tracksOpen.has(c.id),
        ontoggle: (e) => (e.target.open ? tracksOpen.add(c.id) : tracksOpen.delete(c.id)) }, head, list));
    } else parts.push(list);
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
    ["Proxy", proxyOf(c) || (known ? "None" : "")], ["Video asked", videoOf(c)], ["CDM", cdmOf(c)], ["Attempt", c.attempts > 1 && String(c.attempts)]].filter(([, v]) => v);
  const copy = el("button", { type: "button", className: "jact", ariaLabel: "Copy the command",
    onclick: () => navigator.clipboard.writeText(c.setup.command).then(() => toast("Command copied"), () => toast("The browser refused to copy", true)) });
  copy.innerHTML = ICON_COPY;
  return [el("dl", {}, ...rows.flatMap(([k, v]) => [el("dt", { textContent: k }), el("dd", { textContent: v })])),
    el("div", { className: "cmd" }, el("code", { textContent: c.setup.command }), copy)];
}
$("#cd-raw").addEventListener("toggle", () => {
  if (!$("#cd-raw").open) return closeSheet();  // its head pressed: down again
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
/* A job's own download options, for its episodes still to come: over the series' options, the series unchanged. */
let jobOpts = null;
async function openJobOptions(j) {
  const service = j.cards[0].service;
  $("#jobopts-err").textContent = "";
  try {
    const [now, specs] = await Promise.all([api(`/api/jobs/${encodeURIComponent(j.job)}/options`), loadServiceOptions(service).catch(() => [])]);
    // the options its episodes have now: the series' and the ones above it, then the job's own; one the job turned off
    // (false, "") is not shown
    const shownNow = (k) => Object.fromEntries(Object.entries({ ...(now.base?.[k] || {}), ...(now[k] || {}) }).filter(([, v]) => v !== false && v !== ""));
    jobOpts = { job: j.job, service, base: now.base || {}, options: shownNow("options"), service_options: shownNow("service_options") };
    optionsEditor($("#jobopts-dl"), S.dlOptions, jobOpts.options);
    optionsEditor($("#jobopts-own"), specs, jobOpts.service_options, true);
    $("#jobopts-dialog").showModal();
  } catch (e) { toast(e.message, true); }
}
$("#jobopts-form").onsubmit = async (e) => {
  if (e.submitter?.value !== "apply" || !jobOpts) return;
  e.preventDefault();  // open until the server takes them: a wrong value is said here
  // only what differs from what its episodes would have anyway; one taken away turns that option off for the job
  const changes = (k) => {
    const base = jobOpts.base[k] || {}, now = Object.fromEntries(Object.entries(jobOpts[k]).filter(([, v]) => v !== "" && v !== undefined));
    const out = Object.fromEntries(Object.entries(now).filter(([f, v]) => base[f] !== v));
    for (const [f, v] of Object.entries(base)) if (!(f in now) && v !== false && v !== "") out[f] = v === true ? false : "";
    return out;
  };
  try {
    await api(`/api/jobs/${encodeURIComponent(jobOpts.job)}/options`, { method: "POST",
      body: JSON.stringify({ service: jobOpts.service, options: changes("options"), service_options: changes("service_options") }) });
    $("#jobopts-dialog").close();
    toast("Options applied to the episodes still to come in this job");
  } catch (err) { $("#jobopts-err").textContent = err.message; }
};
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
