/* Settings sections: one shown at a time, remembered in the URL (#/settings/automation). On a phone,
   #/settings is the list of sections, and a section is a page of its own that goes back to it. */
let settingsSec = "unshackle", settingsList = false, secFromList = false;
const phone = () => matchMedia("(max-width: 700px)").matches;
const inSettingsSec = () => currentTab === "settings" && !settingsList;  // a section on screen, not the phone's list
function showSettingsList(on) {
  if (on && phone() && inSettingsSec()) leaveNews((n) => n.sec === settingsSec);  // back to the list: the section is left
  settingsList = on && phone();
  $(".set-layout").classList.toggle("at-list", settingsList);
}
function showSettingsSec(name, fromRoute = false) {
  if (!document.querySelector(`.set-sec[data-sec="${name}"]`)) name = "unshackle";
  if (name !== settingsSec && inSettingsSec()) leaveNews((n) => n.sec === settingsSec);
  const fromList = settingsList;
  secFromList = fromList && !fromRoute;
  showSettingsList(false);
  settingsSec = name;
  document.querySelectorAll(".set-sec").forEach((x) => x.hidden = x.dataset.sec !== name);
  document.querySelectorAll(".set-nav button").forEach((x) => x.setAttribute("aria-current", x.dataset.sec === name));
  document.querySelector(`.set-nav [data-sec="${name}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest" });
  if (name === "unshackle" && S.dlOptions.length && currentTab === "settings") loadUnshackleStatus();
  if (name === "cookies" && S.dlOptions.length) loadCookies();
  if (name === "cdm" && S.dlOptions.length) loadCdm();
  if (name === "history" && S.dlOptions.length) loadHistory();
  if (name === "account") loadAccount();
  if (name === "notifications") { renderPush().catch(() => {}); if (S.dlOptions.length) loadSent(); }
  if (name === "automation") { renderAutomation(); renderImportMode(); renderLearnWindow(); }
  if (name === "quality") renderQuality();
  if (name === "upgrades") { renderUpgradeSeries(); renderUpgradeGroups(); }
  if (name === "interface") renderInterface();
  if (!fromRoute && currentTab === "settings") navigate(fromList);  // from the list: back returns to it
  if (fromList) scrollTo({ top: 0 });
}
document.querySelectorAll(".set-nav button").forEach((x) => x.onclick = () => showSettingsSec(x.dataset.sec));
$("#set-back").onclick = () => {
  if (secFromList) return history.back();  // the list is the page before: the phone's back does the same
  showSettingsList(true);
  navigate(false);
  scrollTo({ top: 0 });
};
showSettingsSec("unshackle", true);

/* The URL holds where you are (#/schedule, #/series/s/111421/settings…): a refresh
   lands on the same page, and back (or the phone's back gesture) closes a series. */
let currentTab = "schedule", drawerTab = null, drawerPushed = false;
const HASH_OF = { log: "activity", yaml: "unshackle.yaml" }, TAB_OF = { activity: "log", terminal: "log", defaults: "settings", "unshackle.yaml": "yaml" };  // #/terminal: its name before
function routeHash() {
  const sub = currentTab === "settings" && !settingsList ? `/${settingsSec}` : "";
  return `#/${HASH_OF[currentTab] || currentTab}${sub}` + (current ? `/s/${current.tvdbId}/${drawerTab}` : "");
}
function navigate(push) {
  const hash = routeHash();
  if (location.hash !== hash) history[push ? "pushState" : "replaceState"](null, "", hash);
}
function parseRoute() {
  let [tab, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const name = TAB_OF[tab] || tab;
  const sec = name === "settings" && rest[0] !== "s" ? rest.shift() : null;
  const [s, id, view] = rest;
  return { tab: ["series", "schedule", "settings", "log", "yaml"].includes(name) ? name : "schedule", id: s === "s" ? id : null, view, sec };
}
let routed = false;
function applyRoute() {
  if (location.hash.startsWith("#/inbox")) {  // a push notification touched: the bell's list over the current page
    history.replaceState(null, "", `#/${HASH_OF[currentTab] || currentTab}`);
    openInbox();
  }
  const route = parseRoute();
  if (route.sec) showSettingsSec(route.sec, true);
  else if (route.tab === "settings") showSettingsList(true);  // a phone: the list of sections
  if (!routed || route.tab !== currentTab) showTab(route.tab, true);  // closing a series keeps its tab as it was
  routed = true;
  const series = route.id && S.series.find((x) => String(x.tvdbId) === route.id);
  if (series && current?.tvdbId !== series.tvdbId) openDrawer(series, route.view, true);
  else if (series && route.view) showDrawerTab(route.view, true);
  else if (!series && current) closeDrawer(true);
}
window.addEventListener("popstate", applyRoute);

function showTab(name, fromRoute = false, quiet = false) {
  if (currentTab === "yaml" && name !== "yaml") {  // leaving the editor: it forgets the password and the file
    if (uyDirty() && !confirm("Leave unshackle.yaml without saving your changes?")) { if (fromRoute) navigate(true); return; }
    uyReset();
  }
  if (name !== currentTab && inSettingsSec()) leaveNews((n) => n.sec === settingsSec);
  currentTab = name;
  if (name === "settings" && !fromRoute) showSettingsList(true);  // a phone: the Settings tab opens on its list
  document.querySelectorAll("nav button").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === (name === "yaml" ? "settings" : name)));
  document.querySelectorAll("main > section").forEach((s) => s.hidden = s.id !== "tab-" + name);
  if (!fromRoute) navigate(true);
  if (quiet) return;
  if (name === "log") { refreshLog(); openActivity(); }
  else if (termSocket) { termSocket.close(); termSocket = null; termFor = null; }  // Activity left: its output stops streaming
  if (name === "settings" && settingsSec === "unshackle" && S.dlOptions.length) loadUnshackleStatus();
  if (name === "settings" && settingsSec === "cookies" && S.dlOptions.length) loadCookies();
  if (name === "schedule") loadSchedule();
}
document.querySelectorAll("nav button").forEach((b) => b.onclick = () => { if (b.dataset.tab === "log") landOnLive = true; showTab(b.dataset.tab); });
$("#search").oninput = renderWall;
document.querySelectorAll("#tab-series .seg button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#tab-series .seg button").forEach((x) => x.setAttribute("aria-pressed", x === b));
  renderWall();
});
["#f-service", "#f-library", "#f-sort", "#f-missing", "#f-monitored"].forEach((id) => $(id).onchange = renderWall);

/* The service filter lists only the services some series actually uses. */
function renderServiceFilter() {
  const used = [...new Set(Object.values(S.config.series).map((c) => c.service).filter(Boolean))].sort();
  const f = $("#f-service"), keep = f.value;
  f.querySelectorAll("option.svc-opt").forEach((o) => o.remove());
  const n = (t) => S.series.filter((s) => S.config.series[s.tvdbId]?.service === t).length;
  f.options[0].textContent = `All services (${S.series.length})`;
  f.append(...used.map((t) => el("option", { className: "svc-opt", value: t, textContent: `${t} (${n(t)})` })));
  f.value = [...f.options].some((o) => o.value === keep) ? keep : "";
  renderLibraryFilter();
}
/* The Sonarr library filter: shown once another Sonarr is set up; each with the number of series it shares. */
function renderLibraryFilter() {
  const f = $("#f-library"), keep = f.value, names = (S.config.settings?.sonarrs || []).map((i) => i.name);
  f.hidden = !names.length;
  const n = (name) => S.series.filter((s) => (S.sonarrsOf?.[s.tvdbId] || []).some((o) => o.name === name)).length;
  const main = S.series.filter((s) => s.id).length;
  f.querySelectorAll("option.lib-opt").forEach((o) => o.remove());
  f.options[0].textContent = `All libraries (${S.series.length})`;
  f.append(el("option", { className: "lib-opt", value: "-", textContent: `Sonarr (${main})` }),  // "-": the main one
    ...names.map((name) => el("option", { className: "lib-opt", value: name, textContent: `${name} (${n(name)})` })));
  f.value = [...f.options].some((o) => o.value === keep) ? keep : "";
}
window.addEventListener("beforeunload", (e) => { if (!$("#savebar").hidden) e.preventDefault(); });

showTab(parseRoute().tab, true, true);  // the right tab straight away, before the data comes
if (parseRoute().sec) showSettingsSec(parseRoute().sec, true);

/* Pull to reload, in the installed app (iPhone, Android): the browser gives it in a tab, not there. From the top of a
   page, pulled down far enough and let go: the page loads again. Not over an open window, nor from a list that
   scrolls on its own, nor with unsaved changes (said instead). A badge follows the finger, slowing down as a rubber
   band would, its ring filling up; past the mark it turns, and once let go it spins until the page is back. */
(() => {
  const PULL = 80, TRAVEL = 210;  // px pulled before letting go reloads; how far the badge could come down at most
  const RING = 2 * Math.PI * 15;  // the ring's length (r = 15)
  const tip = el("div", { className: "ptr", ariaHidden: "true" });
  tip.innerHTML = `<span class="ptr-badge"><svg viewBox="0 0 36 36"><circle class="ptr-track" cx="18" cy="18" r="15"/>
    <circle class="ptr-arc" cx="18" cy="18" r="15" stroke-dasharray="${RING}" stroke-dashoffset="${RING}"/></svg>
    <svg class="ptr-arrow" viewBox="0 0 24 24"><path d="M12 5v13M6 12l6 6 6-6"/></svg></span><span class="ptr-say"></span>`;
  document.body.append(tip);
  const arc = tip.querySelector(".ptr-arc"), say = tip.querySelector(".ptr-say");
  let startY = null, pulled = 0, ready = false;
  const scrolledInside = (node) => {
    for (let n = node; n && n !== document.body; n = n.parentElement)
      if (n.scrollTop > 0 && n.scrollHeight > n.clientHeight) return true;
    return false;
  };
  const show = () => {
    const y = TRAVEL * (1 - Math.exp(-pulled / 90));  // a rubber band: quick at first, slower and slower (126 px at the mark)
    tip.style.setProperty("--y", `${y}px`);
    arc.setAttribute("stroke-dashoffset", String(RING * (1 - Math.min(pulled / PULL, 1))));
    const now = pulled >= PULL;
    if (now !== ready) {
      ready = now;
      tip.classList.toggle("ready", ready);
      say.textContent = ready ? "Release to reload" : "Pull to reload";
      if (ready) navigator.vibrate?.(8);  // Android; an iPhone does not let a page do it
    }
  };
  addEventListener("touchstart", (e) => {
    startY = null;
    if (!standalone() || e.touches.length !== 1 || window.scrollY > 0 || document.querySelector("dialog[open]")
        || scrolledInside(e.target) || tip.classList.contains("loading")) return;
    startY = e.touches[0].clientY;
    pulled = 0; ready = false;
    tip.className = "ptr pulling";
    say.textContent = "Pull to reload";
  }, { passive: true });
  addEventListener("touchmove", (e) => {
    if (startY === null) return;
    if (window.scrollY > 0) { startY = null; tip.className = "ptr"; return; }
    pulled = Math.max(0, e.touches[0].clientY - startY);
    tip.classList.toggle("on", pulled > 6);
    show();
  }, { passive: true });
  addEventListener("touchend", () => {
    if (startY === null) return;
    startY = null;
    tip.classList.remove("pulling");  // from here it moves on its own, with a spring
    if (ready && unsaved) { tip.className = "ptr"; return toast("Save or discard your changes first", true); }
    if (!ready) { tip.className = "ptr"; return; }
    tip.className = "ptr on ready loading";
    say.textContent = "Reloading…";
    tip.style.setProperty("--y", "116px");
    setTimeout(() => location.reload(), 350);  // the spin seen a moment, then the page comes back
  }, { passive: true });
})();
