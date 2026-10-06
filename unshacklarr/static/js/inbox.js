/* The bell: every notification kept by the server; what waits for you (a code to enter) first. */
let inboxData = null;
async function refreshInbox() {
  try { inboxData = await api("/api/inbox"); } catch { return; }
  const n = $("#inbox-dialog").open ? 0 : inboxData.unread;
  $("#bell-count").hidden = !n;
  $("#bell-count").textContent = n > 99 ? "99+" : String(n);
  const unread = inboxData.items.filter((i) => i.at > (inboxData.read_at || ""));
  const worst = ["error", "warning", "success", "info"].find((lv) => unread.some((i) => (i.level || "info") === lv)) || "info";
  $("#bell-count").className = `count ${worst}`;
  $("#bell").setAttribute("aria-label", n ? `Notifications, ${n} new` : "Notifications");
  if ($("#inbox-dialog").open) renderInbox();
}
/* Text with its https links clickable; nothing of it ever read as HTML. */
function linkified(text) {
  return String(text || "").split(/(https:\/\/[^\s<>"]+[^\s<>".,;:!?)])/).map((part, i) =>
    i % 2 ? el("a", { href: part, target: "_blank", rel: "noopener", textContent: part }) : part);
}
const copyCode = async (code) => { try { await navigator.clipboard.writeText(code); toast("Code copied"); } catch { /* on screen anyway */ } };
/* One line per series and kind of news on a given day: "Downloaded · Faites entrer l'accusé" with its episodes. */
const INBOX_LEVELS = { success: "Downloaded", warning: "Warnings", error: "Failed" };
const INBOX_ICONS = {
  success: '<path d="M5 12.5l4.5 4.5L19 7.5"/>', warning: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  error: '<path d="M7 7l10 10M17 7L7 17"/>', info: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>',
};
let inboxFilter = "all", inboxSeenAt = "";
function inboxGroups(items) {
  const day = (iso) => new Date(iso).toDateString();
  const groups = [];
  for (const i of items) {
    const m = /^([^:]+): (.+?)(?: (S\d+E\d+(?:\.\d+)?))?$/.exec(i.title || "");
    const kind = m ? m[1] : "", name = m ? m[2] : i.title, ep = m?.[3];
    // a job's lines go together wherever they are; others with the line before, same series and day
    const same = (g) => g.ep && g.kind === kind && g.name === name && g.level === i.level && (g.batch || null) === (i.batch || null);
    const into = ep && (i.batch ? groups.find(same) : [groups.at(-1)].find((g) => g && same(g) && day(g.items[0].at) === day(i.at)));
    if (into) { into.items.push(i); into.eps.push(ep); continue; }
    groups.push({ kind, name, ep, eps: ep ? [ep] : [], level: i.level, batch: i.batch, items: [i], series: S.series.find((x) => x.title === name) });
  }
  return groups;
}
function inboxDay(iso) {
  const d = new Date(iso), today = new Date();
  const days = Math.round((new Date(today.toDateString()) - new Date(d.toDateString())) / 86400000);
  return days === 0 ? "Today" : days === 1 ? "Yesterday" : d.toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "long" });
}
function inboxRow(g, n) {
  const i = g.items[0], icon = el("span", { className: "lv", ariaHidden: "true" });
  icon.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">${INBOX_ICONS[g.level] || INBOX_ICONS.info}</svg>`;
  const art = el("span", { className: "inbox-art" }, ...(g.series?.poster ? [el("img", { src: g.series.poster, alt: "", loading: "lazy", onerror: (e) => e.target.remove() })] : []), icon);
  const when = new Date(i.at);
  const body = el("span", {},
    ...(g.kind ? [el("span", { className: "inbox-kind", textContent: g.batch && g.items.length > 1 ? `${g.kind} · job` : g.kind })] : []),
    el("b", { textContent: g.name }),
    ...(g.eps.length ? [el("span", { className: "inbox-eps" }, ...g.eps.slice().sort().map((e) => el("span", { textContent: e })))] : []),
    ...(i.message ? [el("p", {}, ...linkified(g.items.length > 1 ? i.message.replace(/^Aired \S+, /, "") : i.message))] : []),
    ...(i.action?.code ? [el("button", { className: "code-chip", type: "button", title: "Copy the code", textContent: `${i.action.code} · ${i.action.done ? "done" : "expired"}`, onclick: (e) => { e.stopPropagation(); copyCode(i.action.code); } })] : []));
  const time = el("time", { dateTime: i.at, title: when.toLocaleString(LOCALE, { dateStyle: "full", timeStyle: "short" }),
    textContent: Date.now() - when < 86400000 ? ago(i.at) : when.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" }) });
  const isNew = inboxSeenAt !== null && g.items.some((x) => x.at > inboxSeenAt);
  const props = { className: `inbox-item ${g.level}${isNew ? " new" : ""}`, style: `--i: ${n}` };
  // ponytail: a series is found by its exact title in Sonarr; a renamed one just isn't clickable
  if (!g.series || i.action?.code) return el("div", props, art, body, time);
  // not a <button>: its text can be selected (an error to copy); a click that selects doesn't open the series
  const open = () => { if (String(getSelection()).trim()) return; $("#inbox-dialog").close(); openDrawer(g.series); };
  return el("div", { ...props, className: `${props.className} link`, role: "button", tabIndex: 0, title: `Open ${g.name}`, onclick: open,
    onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } } }, art, body, time);
}
function renderInbox(animate = false) {
  const items = inboxData?.items || [];
  const now = Date.now() / 1000;
  const waiting = items.filter((i) => i.action && !i.action.done && (!i.action.expires_at || i.action.expires_at > now));
  const rest = items.filter((i) => !waiting.includes(i));
  const count = (lv) => rest.filter((i) => i.level === lv).length;
  const fresh = rest.filter((i) => inboxSeenAt !== null && i.at > inboxSeenAt).length;
  $("#inbox-summary").textContent = !items.length ? "" : [fresh && `${fresh} new`, `${rest.length} in all`].filter(Boolean).join(" · ");
  if (inboxFilter !== "all" && !count(inboxFilter)) inboxFilter = "all";
  $("#inbox-filters").hidden = rest.length < 4;
  $("#inbox-filters").replaceChildren(...[["all", "All", rest.length], ...Object.entries(INBOX_LEVELS).map(([lv, label]) => [lv, label, count(lv)])]
    .filter(([, , n]) => n).map(([lv, label, n]) => el("button", { type: "button", ariaPressed: String(inboxFilter === lv), style: `--lv: var(--${{ success: "ok", warning: "warn", error: "err" }[lv] || "muted"})`,
      onclick: () => { inboxFilter = lv; renderInbox(true); } }, ...(lv === "all" ? [] : [el("i")]), label, el("b", { textContent: String(n) }))));
  const shown = rest.filter((i) => inboxFilter === "all" || i.level === inboxFilter);
  const days = [];
  for (const g of inboxGroups(shown)) {
    const label = inboxDay(g.items[0].at);
    if (days.at(-1)?.label !== label) days.push({ label, groups: [] });
    days.at(-1).groups.push(g);
  }
  let n = 0;
  const empty = el("div", { className: "inbox-empty" });
  empty.innerHTML = `<svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${INBOX_ICONS.info}<path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/></svg><p>All quiet. Downloads, failures and anything that waits for you show up here.</p>`;
  keepPromptFocus(() => $("#inbox-list").replaceChildren(
    ...(waiting.length ? [el("div", { className: "inbox-sub", textContent: "Waiting for you" }),
      ...waiting.map((i) => (i.action.kind === "input" ? promptBox(i.action) : actionBox({ action: i.action, service: i.action.service })))] : []),
    ...days.flatMap((d) => [el("div", { className: "inbox-sub", textContent: d.label }), ...d.groups.map((g) => inboxRow(g, n++))]),
    ...(!items.length ? [empty] : [])));
  $("#inbox-list").classList.toggle("enter", animate);
  $("#inbox-clear").hidden = !rest.length;
}
/* The bell opens a menu under it; its ⤢ (or a push notification) the whole list, as a window over the page. */
async function openInbox(mode = "full") {
  const dialog = $("#inbox-dialog");
  if (dialog.open && dialog.classList.contains("menu") !== (mode === "menu")) dialog.close();
  dialog.classList.toggle("menu", mode === "menu");
  const bell = $("#bell").getBoundingClientRect();  // the menu hangs from the bell
  dialog.style.top = mode === "menu" ? `${bell.bottom + 8}px` : "";
  dialog.style.right = mode === "menu" && !phone() ? `${Math.max(8, innerWidth - bell.right)}px` : "";
  if (!dialog.open) mode === "menu" ? dialog.show() : dialog.showModal();
  if (!inboxData) $("#inbox-list").replaceChildren(loading("Reading the notifications…"));
  inboxFilter = "all";
  await refreshInbox();
  inboxSeenAt = inboxData?.read_at ?? null;  // what came since the last look is marked new while the list is open
  renderInbox(true);
  try { inboxData = await api("/api/inbox/read", { method: "POST" }); } catch { /* the badge stays until next time */ }
  $("#bell-count").hidden = true;
}
$("#bell").onclick = (e) => {
  e.stopPropagation();
  $("#inbox-dialog").open ? $("#inbox-dialog").close() : openInbox("menu");
};
$("#inbox-full").onclick = () => openInbox("full");
document.addEventListener("click", (e) => {  // the menu closes with a click anywhere else
  const menu = $("#inbox-dialog");
  if (menu.open && menu.classList.contains("menu") && !menu.contains(e.target)) menu.close();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#inbox-dialog").classList.contains("menu")) $("#inbox-dialog").close(); });
$("#inbox-close").onclick = () => $("#inbox-dialog").close();
$("#inbox-dialog").addEventListener("click", (e) => {  // a click outside the panel (on the backdrop) closes it
  const r = $("#inbox-dialog").getBoundingClientRect();
  if (e.target === $("#inbox-dialog") && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) $("#inbox-dialog").close();
});
$("#inbox-dialog").addEventListener("close", refreshInbox);
$("#inbox-clear").onclick = async () => {
  try { inboxData = await api("/api/inbox/clear", { method: "POST" }); renderInbox(true); toast("Notifications cleared"); }
  catch (e) { toast(e.message, true); }
};
setInterval(() => { if (!document.hidden) refreshInbox(); }, 30000);  // a tab out of sight asks nothing
