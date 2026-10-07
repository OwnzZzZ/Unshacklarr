/* What's new: each new option wears a "New" badge (and its Settings section too) until it has been on screen for a
   moment or clicked; the What's new button lists them, each with a way to it. What was seen is kept by the server,
   for the account: seen on the computer, gone on the phone too. A new install starts with nothing new. To announce
   an option in a release: one line below, its version, and where it is. */
const NEWS = [
  { id: "download-window", version: "1.5.0", title: "Download window", sec: "automation", target: "#ax-window-card",
    text: "Choose the hours when automatic downloads run, at night for example." },
  { id: "learn-release", version: "1.5.0", title: "Learn release times", sec: "automation", target: "#ax-learn-card",
    text: "Unshacklarr can set a series' release time by itself, once 3 episodes came out at the same time." },
  { id: "add-audio", version: "1.5.0", title: "Add the audio track to the existing file", sec: "downloads", target: ".up-mode",
    text: "When your preferred audio language becomes available, download only that audio track instead of the whole episode." },
  { id: "fallback-service", version: "1.5.0", title: "Fallback service", series: true, target: "#d-fb",
    text: "Each series can have a second service, used when the main one doesn't have the episode yet." },
];
const versionKey = (v) => String(v || "0").split(".").map((n) => parseInt(n, 10) || 0).reduce((a, n) => a * 1000 + n, 0);
function newsUnseen() {
  const news = S.news || {}, seen = new Set(news.seen || []);
  return NEWS.filter((n) => !seen.has(n.id) && !(news.installed && versionKey(n.version) <= versionKey(news.installed)));
}
async function markNews(ids, list = false) {
  if (!ids.length && !list) return;
  S.news = { ...(S.news || {}), seen: [...new Set([...(S.news?.seen || []), ...ids])], ...(list ? { list_read: true } : {}) };
  renderNews();
  try { await api("/api/news/seen", { method: "POST", body: JSON.stringify({ ids, list }) }); } catch { /* the badge comes back on the next load */ }
}

/* The badges: on each new option's title, and on its Settings section in the list. */
const newsTitle = (box) => box.querySelector(".sx-head h3, summary b, .sub");
function renderNews() {
  const unseen = newsUnseen(), ids = new Set(unseen.map((n) => n.id));
  document.querySelectorAll(".new-badge").forEach((b) => { if (!ids.has(b.dataset.news)) b.remove(); });
  for (const n of unseen) {
    const box = document.querySelector(n.target), title = box && newsTitle(box);
    if (title && !title.querySelector(`.new-badge[data-news="${n.id}"]`)) title.append(el("span", { className: "new-badge", textContent: "New", dataset: { news: n.id } }));
    if (box) watchNews(box, n.id);
  }
  document.querySelectorAll(".set-nav button[data-sec]").forEach((b) => {
    const has = unseen.some((n) => n.sec === b.dataset.sec);
    const badge = b.querySelector(":scope > span > .new-dot");
    if (has && !badge) b.querySelector(":scope > span").append(el("i", { className: "new-dot", ariaLabel: "New" }));
    if (!has && badge) badge.remove();
  });
  const button = $("#news");
  button.hidden = !unseen.length || !!S.news?.list_read;
  button.querySelector("b").textContent = unseen.length;
}

/* Seen: on screen for a second, or clicked. */
const newsTimers = new Map();
const newsSeen = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const id = e.target.dataset.newsId;
    if (e.isIntersecting && e.intersectionRatio >= 0.5) {
      if (!newsTimers.has(id)) newsTimers.set(id, setTimeout(() => { newsSeen.unobserve(e.target); markNews([id]); }, 1000));
    } else { clearTimeout(newsTimers.get(id)); newsTimers.delete(id); }
  }
}, { threshold: [0, 0.5] });
function watchNews(box, id) {
  if (box.dataset.newsId) return;
  box.dataset.newsId = id;
  newsSeen.observe(box);
  box.addEventListener("click", () => { if (newsUnseen().some((n) => n.id === id)) markNews([id]); });
}

/* The What's new button: the list, each with a way to it. */
function openNews() {
  const unseen = newsUnseen();
  const go = (n) => {
    $("#news-dlg").close();
    if (n.series) {
      const s = S.series.find((x) => S.config.series[x.tvdbId]?.service) || S.series[0];
      if (s) openDrawer(s, "settings");
      setTimeout(() => { const box = document.querySelector(n.target); if (box?.tagName === "DETAILS") box.open = true; box?.scrollIntoView({ behavior: "smooth", block: "center" }); }, 400);
    } else {
      showTab("settings");
      showSettingsSec(n.sec);
      setTimeout(() => document.querySelector(n.target)?.scrollIntoView({ behavior: "smooth", block: "center" }), 200);
    }
  };
  $("#news-list").replaceChildren(...NEWS.filter((n) => unseen.includes(n)).map((n) => el("li", {},
    el("div", {}, el("b", { textContent: n.title }), el("p", { textContent: n.text })),
    el("button", { type: "button", className: "btn small", textContent: "Show me", onclick: () => go(n) }))));
  $("#news-dlg").showModal();
  markNews([], true);  // the list read: the button goes, each badge stays until its option is seen
}
$("#news").onclick = openNews;
$("#news-close").onclick = () => $("#news-dlg").close();
