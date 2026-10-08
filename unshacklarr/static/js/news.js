/* What's new: each new option wears a "New" badge (and its Settings section too) until it has been on screen for a
   moment or clicked. What was seen is kept by the server, for the account: seen on the computer, gone on the phone
   too. A new install starts with nothing new. The window shows the release notes, read from the CHANGELOG, one
   release per row; a change whose title is an option's below gets a way to it. */
const NEWS = [
  { id: "download-window", version: "1.5.0", title: "Download window", sec: "automation", target: "#ax-window-card" },
  { id: "learn-release", version: "1.5.0", title: "Auto release times", sec: "automation", target: "#ax-learn-card" },
  { id: "add-audio", version: "1.5.0", title: "Add audio track", sec: "downloads", target: ".up-mode" },
  { id: "fallback-service", version: "1.5.0", title: "Fallback service", series: true, target: "#d-fb" },
  { id: "offsite-backups", version: "1.5.0", title: "Remote backup", sec: "account", target: "#bo-card" },
  { id: "series-notify", version: "1.5.0", title: "Notifications per series", series: true, target: "#d-notify-l" },
  { id: "no-spoilers", version: "1.5.0", title: "No spoilers", sec: "interface", target: "#rg-spoiler-card" },
];
const NEWS_KINDS = { new: "New", improved: "Improved", fix: "Fix", security: "Security", note: "Note" };
const versionKey = (v) => String(v || "0").split(".").map((n) => parseInt(n, 10) || 0).reduce((a, n) => a * 1000 + n, 0);
function newsUnseen() {
  const news = S.news || {}, seen = new Set(news.seen || []);
  return NEWS.filter((n) => !seen.has(n.id) && !(news.installed && versionKey(n.version) <= versionKey(news.installed)));
}
async function markNews(ids, list = false) {
  if (!ids.length && !list) return;
  S.news = { ...(S.news || {}), seen: [...new Set([...(S.news?.seen || []), ...ids])], ...(list ? { list_read: S.version } : {}) };
  renderNews();
  try { await api("/api/news/seen", { method: "POST", body: JSON.stringify({ ids, list }) }); } catch { /* the badge comes back on the next load */ }
}

/* The badges: on each new option's title (its card outlined), on its Settings section in the list, and a strip at
   the top of that section saying how many, with a way down to them. */
const newsTitle = (box) => box.querySelector(".sx-head h3, summary b, .sub") || (box.matches("label, span[id]") ? box : null);  // a field's own label
function renderNews() {
  const unseen = newsUnseen(), ids = new Set(unseen.map((n) => n.id));
  document.querySelectorAll(".new-badge").forEach((b) => { if (!ids.has(b.dataset.news)) b.remove(); });
  document.querySelectorAll(".is-new").forEach((b) => { if (!ids.has(b.dataset.newsId)) b.classList.remove("is-new"); });
  for (const n of unseen) {
    const box = document.querySelector(n.target), title = box && newsTitle(box);
    box?.classList.add("is-new");
    if (title && !title.querySelector(`.new-badge[data-news="${n.id}"]`)) title.append(el("span", { className: "new-badge", textContent: "New", dataset: { news: n.id } }));
    if (box) watchNews(box, n.id);
  }
  document.querySelectorAll(".set-nav button[data-sec]").forEach((b) => {
    const has = unseen.some((n) => n.sec === b.dataset.sec);
    const badge = b.querySelector(":scope > .new-dot");  // beside the name, not in it: a long name wraps alone
    if (has && !badge) b.querySelector(":scope > span").after(el("i", { className: "new-dot", textContent: "New" }));
    if (!has && badge) badge.remove();
  });
  document.querySelectorAll(".set-sec[data-sec]").forEach((sec) => {
    const here = unseen.filter((n) => n.sec === sec.dataset.sec);
    let strip = sec.querySelector(":scope > .new-strip");
    if (!here.length) return strip?.remove();
    if (!strip) sec.firstElementChild.after(strip = el("div", { className: "new-strip" }));
    strip.replaceChildren(el("span", { textContent: here.length === 1 ? "1 new option in this section" : `${here.length} new options in this section` }),
      el("button", { type: "button", className: "btn small", textContent: "Show me",
        onclick: () => document.querySelector(here[0].target)?.scrollIntoView({ behavior: "smooth", block: "center" }) }));
  });
  const version = $("#version"), count = S.news?.list_read !== S.version ? unseen.length : 0;  // how many, until this version's list is read
  version.classList.toggle("has-new", !!count);
  version.querySelector("b")?.remove();
  if (count) version.append(el("b", { textContent: count }));
  const label = count === 1 ? "What's new: 1 new option" : count ? `What's new: ${count} new options` : "What's new in each version";
  version.title = label;
  version.setAttribute("aria-label", label);
}

/* Seen when its section is left: every new option there, looked at or not. */
function leaveNews(test) {
  const ids = newsUnseen().filter(test).map((n) => n.id);
  if (ids.length) markNews(ids);
}
/* Seen: in sight for 2.5 seconds, or clicked. In sight: half of it on screen, or, for one taller than the screen,
   a good part of the screen filled with it. */
const newsTimers = new Map();
const newsSeen = new IntersectionObserver((entries) => {
  for (const e of entries) {
    const id = e.target.dataset.newsId;
    if (e.isIntersecting && (e.intersectionRatio >= 0.5 || e.intersectionRect.height >= innerHeight * 0.4)) {
      e.target.classList.add("new-blink");  // it blinks as it comes into sight, not off screen
      if (!newsTimers.has(id)) newsTimers.set(id, setTimeout(() => { newsSeen.unobserve(e.target); newsTimers.delete(id); markNews([id]); }, 2500));
    } else { clearTimeout(newsTimers.get(id)); newsTimers.delete(id); }
  }
}, { threshold: [0, 0.1, 0.2, 0.3, 0.4, 0.5] });  // tall ones cross the lower steps
function watchNews(box, id) {
  newsSeen.observe(box);  // again when new again: observing twice is harmless
  if (box.dataset.newsId) return;
  box.dataset.newsId = id;
  box.addEventListener("click", () => { if (newsUnseen().some((n) => n.id === id)) markNews([id]); });
}

/* The What's new window: one row per release, the installed one open; a newer one links to GitHub. */
let releaseNotes = null;
const inlineMd = (text) => text.split(/(\*\*[^*]+\*\*|`[^`]+`)/).filter(Boolean).map((t) =>
  t.startsWith("**") ? el("b", { textContent: t.slice(2, -2) }) : t.startsWith("`") ? el("code", { textContent: t.slice(1, -1) }) : t);
function showOption(n) {
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
}
function releaseRow(r, installed, latest) {
  const numbered = r.version !== "Unreleased", name = numbered ? `v${r.version}` : "Unreleased";
  const date = r.date ? new Date(`${r.date}T12:00`).toLocaleDateString(LOCALE, { day: "numeric", month: "short", year: "numeric" }) : "";
  const changes = r.changes.length === 1 ? "1 change" : `${r.changes.length} changes`;
  return el("details", { className: "news-rel", open: r.version === installed },
    el("summary", {}, el("b", { textContent: name }),
      ...(r.version === installed ? [el("span", { className: "news-tag", textContent: "Installed" })] : []),
      ...(r.version === latest ? [el("span", { className: "news-tag latest", textContent: "Latest" })] : []),
      el("small", { textContent: changes }), el("time", { textContent: date })),
    el("ul", { className: "news-list" }, ...r.changes.map((c) => {
      const n = NEWS.find((x) => c.text.startsWith(`**${x.title}.**`));
      return el("li", { className: c.kind }, el("span", { className: "news-kind", textContent: NEWS_KINDS[c.kind] }),
        el("p", {}, ...inlineMd(c.text),
          ...(n ? [" ", el("button", { type: "button", className: "news-go", textContent: "Show me", onclick: () => showOption(n) })] : [])));
    })));
}
async function openNews() {
  const list = $("#news-list"), dlg = $("#news-dlg");
  if (!dlg.open) dlg.showModal();
  markNews([], true);  // the list read: the count goes, each badge stays until its option is seen
  try { releaseNotes ||= await api("/api/changelog"); } catch (e) { return list.replaceChildren(failed(e.message, openNews)); }
  const { installed, releases } = releaseNotes, newer = S.update?.version;
  const latest = newer || installed;
  $("#news-version").textContent = `Release notes for Unshacklarr ${installed}.`;
  const rows = [];
  if (newer) rows.push(el("a", { className: "news-rel news-out", href: S.update.url, target: "_blank", rel: "noopener" },
    el("b", { textContent: `v${newer}` }), el("span", { className: "news-tag latest", textContent: "Latest" }), el("small", { textContent: "See on GitHub ↗" })));
  let earlier = false;
  for (const r of releases) {
    if (!earlier && r.version !== "Unreleased" && versionKey(r.version) < versionKey(installed)) {
      earlier = true;
      rows.push(el("p", { className: "news-earlier", textContent: "Earlier releases" }));
    }
    rows.push(releaseRow(r, installed, latest));
  }
  list.replaceChildren(...rows);
}
$("#version").onclick = $("#set-version").onclick = openNews;
$("#news-close").onclick = $("#news-x").onclick = () => $("#news-dlg").close();
$("#news-dlg").addEventListener("click", (e) => {  // a click outside the window (on the dimmed page) closes it
  const r = e.currentTarget.getBoundingClientRect();
  const outside = e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  if (e.target === e.currentTarget && outside) e.currentTarget.close();  // not a button pressed with the keyboard
});
