const $ = (s) => document.querySelector(s);
/* A help text, one sentence a line: easier to read than a block. */
const breakSentences = (text) => text.replace(/([.!?]) (?=\p{Lu})/gu, "$1\n").replace(/([。！？])(?=\S)/gu, "$1\n");
function lineBySentence(node) {
  const walk = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  while (walk.nextNode()) walk.currentNode.nodeValue = breakSentences(walk.currentNode.nodeValue);
  return node;
}
/* Languages. The page is written in English; another language's texts are swapped in as they show up, whoever
   put them there (the markup, el(), a textContent), each text matched whole: "Test all", or "Test {0} ({1})" with
   what fills {0} and {1} translated in turn. i18n/en.json lists them (tools/i18n/extract.py), i18n/<lang>.json
   translates them. The choice is this browser's: the login and the setup need it before any account. */
const LANGS = { en: "English", fr: "Français", es: "Español", de: "Deutsch", "pt-BR": "Português (Brasil)", it: "Italiano",
  ru: "Русский", "zh-CN": "简体中文", ja: "日本語" };
const LANG = (() => {
  let chosen = null;
  try { chosen = localStorage.getItem("lang"); } catch {}
  if (LANGS[chosen]) return chosen;
  for (const wanted of navigator.languages || [navigator.language || "en"]) {
    const hit = LANGS[wanted] ? wanted : Object.keys(LANGS).find((k) => k.split("-")[0] === wanted.split("-")[0]);
    if (hit) return hit;
  }
  return "en";
})();
document.documentElement.lang = LANG;
async function setLang(lang) {
  try { localStorage.setItem("lang", lang); } catch {}
  try { if (setupDraft) sessionStorage.setItem("setup-draft", JSON.stringify(setupDraft())); } catch {}  // the first run goes on where it was
  await api("/api/language", { method: "POST", body: JSON.stringify({ language: lang }) }).catch(() => {});  // the notifications' too; before logging in, nothing to tell
  location.reload();
}
const FLAGS = { en: "🇬🇧", fr: "🇫🇷", es: "🇪🇸", de: "🇩🇪", "pt-BR": "🇧🇷", it: "🇮🇹", ru: "🇷🇺", "zh-CN": "🇨🇳", ja: "🇯🇵" };
/* The language, in a corner of the login and the first run: a flag and its name, the others in a menu. */
function langCorner() {
  const menu = el("div", { className: "lang-menu", role: "menu", hidden: true },
    ...Object.entries(LANGS).map(([k, name]) => el("button", { type: "button", role: "menuitemradio", ariaChecked: String(k === LANG),
      className: k === LANG ? "on" : "", onclick: () => { if (k !== LANG) setLang(k); } }, el("span", { className: "flag", textContent: FLAGS[k] }), name)));
  const open = (on) => { menu.hidden = !on; button.setAttribute("aria-expanded", String(on)); };
  const button = el("button", { type: "button", className: "lang-btn", ariaHasPopup: "menu", ariaExpanded: "false", ariaLabel: `Language: ${LANGS[LANG]}`,
    onclick: () => open(menu.hidden) }, el("span", { className: "flag", textContent: FLAGS[LANG] }), el("span", { textContent: LANGS[LANG] }), el("span", { className: "caret", ariaHidden: "true" }));
  const corner = el("div", { className: "lang-corner", translate: "no" }, button, menu);
  document.addEventListener("click", (e) => { if (!corner.contains(e.target)) open(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") open(false); });
  return corner;
}
let T = null, TP = [];  // exact texts; patterns, the most specific first
const translated = new Set();  // texts already in the language: not taken for English again
function tr(text, depth = 0) {
  const key = text.replace(/\s+/g, " ").trim();
  if (!T || !key) return text;
  let out = T[key];
  if (out === undefined && key.includes(" · ")) {  // pieces joined by " · ": each its own text
    const parts = key.split(" · "), done = parts.map((x) => tr(x, depth + 1));
    if (done.some((x, i) => x !== parts[i])) out = done.join(" · ");
  }
  if (out === undefined && depth < 3) {
    for (const p of TP) {
      if (!key.includes(p.anchor)) continue;
      const m = p.re.exec(key);
      // a template with little text of its own ("{0} ago", "{0} of {1}") takes short fillers only, not a whole sentence
      // ... and only numbers, or texts it has a translation for: "5 min ago", not "Set up Unshacklarr in a few steps"
      if (m && p.weak && m.slice(1).some((x) => x.length > 24 || !(/\d/.test(x) || T[x.trim()] !== undefined || !x.trim()))) continue;
      if (m) { out = p.to.replace(/\{(\d+)\}/g, (_, i) => tr(m[+i + 1] ?? "", depth + 1)); break; }
    }
  }
  return out === undefined ? text : text.match(/^\s*/)[0] + out + text.match(/\s*$/)[0];
}
const I18N_SKIP = ".term, .cm-editor, pre, code, textarea, script, style, [translate=no]", I18N_ATTRS = ["placeholder", "title", "aria-label", "data-tip"];
function trText(node) {
  const v = node.nodeValue;
  if (!/\p{L}/u.test(v) || translated.has(v) || node.parentElement?.closest(I18N_SKIP)) return;
  const t = tr(v);
  if (t === v) return;
  const out = node.parentElement?.closest(".field > small") ? breakSentences(t) : t;
  translated.add(out);
  node.nodeValue = out;
}
function trAttr(e, name) {
  const v = e.getAttribute(name);
  if (!v || translated.has(v)) return;
  const t = tr(v);
  if (t !== v) { translated.add(t); e.setAttribute(name, t); }
}
function trTree(root) {
  if (root.nodeType === 3) return trText(root);
  if (root.nodeType !== 1 || root.closest(I18N_SKIP)) return;
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT,
    { acceptNode: (n) => n.nodeType === 1 && n.matches(I18N_SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
  for (let n = root; n; n = walk.nextNode()) {
    if (n.nodeType === 3) trText(n);
    else for (const a of I18N_ATTRS) if (n.hasAttribute(a)) trAttr(n, a);
  }
}
const i18nReady = (async () => {  // awaited before the first screen; English if the file can't be had
  try {
    if (LANG === "en") return;
    T = await (await fetch(`/i18n/${LANG}.json`, { cache: "no-cache" })).json();
    TP = Object.entries(T).filter(([k]) => /\{\d+\}/.test(k)).map(([k, to]) => {
      const parts = k.split(/\{\d+\}/);
      const re = new RegExp("^" + parts.map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("(.*?)") + "$", "s");  // a hole may be empty: a plural's "s"
      const letters = parts.join("").replace(/[^\p{L}]/gu, "").length;
      return { re, to, anchor: parts.reduce((a, b) => (b.length > a.length ? b : a), ""), weight: parts.join("").length, weak: letters < 10 && parts.filter((x) => x.trim()).length < 2 };
    }).filter((p) => /\p{L}/u.test(p.anchor)).sort((a, b) => b.weight - a.weight);
    trTree(document.body);
    new MutationObserver((changes) => {
      for (const c of changes) {
        if (c.type === "childList") c.addedNodes.forEach(trTree);
        else if (c.type === "characterData") trText(c.target);
        else if (!c.target.closest(I18N_SKIP)) trAttr(c.target, c.attributeName);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: I18N_ATTRS });
    for (const name of ["confirm", "alert", "prompt"]) { const own = window[name].bind(window); window[name] = (m, ...rest) => own(tr(String(m)), ...rest); }
  } catch (e) { T = null; console.warn("No translation:", e); }
  finally { document.documentElement.classList.remove("i18n-wait"); }
})();
const tmdbHint = () => ["Optional and free: ", el("a", { href: "https://www.themoviedb.org/settings/api", target: "_blank", rel: "noopener", textContent: "create one on TMDB" }),
  " (sign in, then Settings, API).\nWith it, the Schedule shows each series' channel and streaming services.\n"
  + "For a series not in English, it also finds episodes by their title: Sonarr only has English titles."];
let S = { series: [], config: { defaults: {}, series: {}, notifications: {} }, services: [], serviceNames: {}, dlOptions: [], health: {} };
const serviceOptions = {};
let current = null;

async function api(path, opts = {}) {
  let r;
  try {
    // 30 s at most: a request lost in a server restart fails visibly instead of spinning forever
    r = await fetch(path, { headers: { "Content-Type": "application/json", "X-Unshackle": "1" }, signal: AbortSignal.timeout(30000), ...opts });
  } catch (e) {
    throw new Error(e.name === "TimeoutError" ? "No answer after 30 s." : "The server can't be reached.");
  }
  if (r.status === 401 && !path.startsWith("/api/login")) { showLogin(); throw new Error("Log in first"); }
  if (!r.ok) throw new Error(await r.text() || `${r.status} ${r.statusText}`);
  return r.json();
}

function toast(msg, err = false, action = null) {  // action: [label, onclick], e.g. ["Undo", …]
  const t = document.createElement("div");
  t.className = "toast" + (err ? " err" : "") + (action ? " act" : "");
  t.setAttribute("role", err ? "alert" : "status");
  t.textContent = msg;
  if (action) t.append(el("button", { textContent: action[0], onclick: () => { t.remove(); action[1](); } }));
  $("#toasts").append(t);
  onTop($("#toasts"));  // above a window open as a modal too
  // it stays while the pointer is on it or its text is selected (an error to copy), then goes a moment later
  const later = () => setTimeout(() => t.matches(":hover") || getSelection().containsNode(t, true) ? later() : t.remove(), err || action ? 6000 : 2500);
  later();
}

let unsaved = false;
let savedConfig = "";  // the settings as last loaded or saved: a discard that brings them back clears the bar
// what a save sends: a series opened without a service is not part of it
const configKey = (c) => JSON.stringify({ ...c, series: Object.fromEntries(Object.entries(c.series || {}).filter(([, x]) => x.service)) });
function dirty() {  // a change put back as it was hides the bar again
  renderCommands();
  unsaved = configKey(S.config) !== savedConfig;
  $("#savebar").hidden = !unsaved;
  $("#d-save").hidden = !unsaved;
  if (current) renderInherited(S.config.series[current.tvdbId]);  // a series option may now hide an inherited one
}

function el(tag, { dataset, ...props } = {}, ...children) {
  const e = Object.assign(document.createElement(tag), props);
  if (dataset) Object.assign(e.dataset, dataset);  // read-only as a whole: filled key by key
  e.append(...children);
  return e;
}

/* One row per option already set, plus a picker listing every other option the CLI knows. */
function optionsEditor(root, specs, values) {
  root.replaceChildren();
  for (const [flag, value] of Object.entries(values)) {
    const spec = specs.find((s) => s.flag === flag) || { flag, help: "", is_flag: typeof value === "boolean", choices: [] };
    let input;
    if (spec.is_flag) {
      input = el("label", { className: "check" }, el("input", { type: "checkbox", checked: !!value, onchange: (e) => { values[flag] = e.target.checked; dirty(); } }), "on");
    } else {
      input = el("input", { type: "text", value: value ?? "", placeholder: spec.choices.length ? spec.choices.join(", ") : spec.help,
        oninput: (e) => { values[flag] = e.target.value; dirty(); } });
      if (spec.choices.length) {
        const id = "dl" + Math.random().toString(36).slice(2);
        input.setAttribute("list", id);
        root.append(el("datalist", { id }, ...spec.choices.map((c) => el("option", { value: c }))));
      }
    }
    root.append(el("div", { className: "opt" },
      el("code", { title: spec.help }, flag),
      input,
      el("button", { className: "rm", title: "Remove", ariaLabel: `Remove ${flag}`, textContent: "×",
        onclick: () => { delete values[flag]; dirty(); optionsEditor(root, specs, values); } })));
  }
  const free = specs.filter((s) => !(s.flag in values)).sort((a, b) => a.flag.localeCompare(b.flag));
  if (!specs.length && !Object.keys(values).length) root.append(el("small", { className: "muted", textContent: "None." }));
  if (!free.length) return;
  root.append(optionPicker(free, (spec) => {
    values[spec.flag] = spec.is_flag ? true : "";
    dirty();
    optionsEditor(root, specs, values);
    // Straight to the new option's value (the last row), or back to the search for a flag.
    const rows = root.querySelectorAll(".opt");
    (spec.is_flag ? root.querySelector(".picker input") : rows[rows.length - 1]?.querySelector("input"))?.focus();
  }));
}

/* Search box over the options not set yet: matches the flag, its short form and its help. */
function optionPicker(free, onPick) {
  const input = el("input", { type: "text", placeholder: "Add an option: type to search, e.g. lang, audio, atmos",
    role: "combobox", ariaAutoComplete: "list", ariaExpanded: "false", autocomplete: "off", spellcheck: false });
  const list = el("ul", { className: "picker-list", role: "listbox", hidden: true });
  list.id = "pl" + Math.random().toString(36).slice(2);
  input.setAttribute("aria-controls", list.id);
  let shown = [], active = 0;

  const rank = (s, q) => !q ? 0 : s.flag.replace(/^-+/, "").startsWith(q) ? 0 : s.flag.includes(q) || s.short.includes(q) ? 1 : 2;
  function render() {
    const q = input.value.trim().toLowerCase().replace(/^-+/, "");
    shown = free
      .filter((s) => !q || s.flag.includes(q) || s.short.includes(q) || s.help.toLowerCase().includes(q))
      .sort((a, b) => rank(a, q) - rank(b, q) || a.flag.localeCompare(b.flag));
    active = Math.min(active, Math.max(shown.length - 1, 0));
    list.replaceChildren(...(shown.length ? shown.map((s, i) => {
      const li = el("li", { role: "option", className: i === active ? "on" : "",
        onmousedown: (e) => { e.preventDefault(); onPick(s); } },
        el("code", { textContent: s.flag }), el("span", { textContent: s.help }));
      li.id = `${list.id}-${i}`;
      li.setAttribute("aria-selected", i === active);
      return li;
    }) : [el("li", { className: "none", textContent: "No option matches." })]));
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-activedescendant", shown.length ? `${list.id}-${active}` : "");
    list.querySelector("li.on")?.scrollIntoView({ block: "nearest" });
  }
  function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); }

  input.onfocus = render;
  input.oninput = () => { active = 0; render(); };
  input.onblur = close;
  input.onkeydown = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) return render();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length;
      render();
    } else if (e.key === "Enter" && !list.hidden && shown[active]) {
      e.preventDefault();
      onPick(shown[active]);
    } else if (e.key === "Escape" && !list.hidden) {
      e.stopPropagation();  // close the list, not the drawer
      close();
    }
  };
  return el("div", { className: "picker" }, input, list);
}

const SEL = { on: false, ids: new Set() };  // the series picked to change together (js/bulk.js)
function renderWall() {
  const q = $("#search").value.trim().toLowerCase();
  const svc = $("#f-service").value;
  const on = (s) => Boolean(S.config.series[s.tvdbId]?.service);
  const failing = (s) => Boolean(S.health[s.tvdbId]) && on(s);
  const counts = { "": S.series.length, "*": S.series.filter(on).length, h: S.config.hidden_series.length, f: S.series.filter(failing).length };
  counts["-"] = counts[""] - counts["*"];
  const hiddenTab = document.querySelector('#tab-series .seg [data-managed="h"]');
  hiddenTab.hidden = !counts.h;
  const failTab = document.querySelector('#tab-series .seg [data-managed="f"]');
  failTab.hidden = !counts.f;
  if ((!counts.h && hiddenTab.getAttribute("aria-pressed") === "true") || (!counts.f && failTab.getAttribute("aria-pressed") === "true")) {
    document.querySelectorAll("#tab-series .seg button").forEach((x) => x.setAttribute("aria-pressed", x.dataset.managed === ""));  // emptied: All again
  }
  document.querySelectorAll("#tab-series .seg button").forEach((b) => b.querySelector("span").textContent = `(${counts[b.dataset.managed]})`);
  $("#f-missing-n").textContent = `(${S.series.filter((s) => s.missing > 0).length})`;
  $("#f-monitored-n").textContent = `(${S.series.filter((s) => s.monitored).length})`;
  const managed = document.querySelector("#tab-series .seg [aria-pressed=true]").dataset.managed;
  const sorts = {
    title: (a, b) => a.title.localeCompare(b.title, "fr"),
    missing: (a, b) => b.missing - a.missing,
    year: (a, b) => (b.year ?? 0) - (a.year ?? 0),
  };
  const shown = S.series
    .filter((s) => {
      const on = S.config.series[s.tvdbId]?.service || "";
      return (!q || s.title.toLowerCase().includes(q))
        && (!managed || (managed === "f" ? failing(s) : managed === "h" ? S.config.hidden_series.includes(s.tvdbId) : managed === "*" ? on : !on))
        && (!svc || on === svc)
        && (!$("#f-missing").checked || s.missing > 0)
        && (!$("#f-monitored").checked || s.monitored);
    })
    .sort(sorts[$("#f-sort").value]);
  $("#count").textContent = `${shown.length} series`;
  const cards = shown.map((s) => {
      const conf = S.config.series[s.tvdbId];
      const art = el("span", { className: "art" });
      if (s.poster) art.append(el("img", { src: s.poster, alt: "", loading: "lazy", decoding: "async" }));
      if (conf?.service) art.append(el("span", { className: "svc", textContent: conf.service }));
      const sick = conf?.service && S.health[s.tvdbId];
      if (sick) art.append(el("span", { className: "sick", title: `Its last ${sick.failing} downloads failed${sick.cause ? `: ${sick.cause}` : ""}`, textContent: "!" }));
      if (S.config.hidden_series.includes(s.tvdbId)) {
        const off = el("span", { className: "off", title: "Hidden from the schedule" });
        off.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">${EYE_SHUT}</svg>`;
        art.append(off);
      }
      const card = el("button", { className: "poster" + (conf?.service ? " on" : "") + (SEL.ids.has(s.tvdbId) ? " picked" : ""),
        ariaPressed: SEL.on ? String(SEL.ids.has(s.tvdbId)) : null, onclick: () => SEL.on ? pickSeries(s.tvdbId) : openDrawer(s) },
        art,
        el("span", { className: "name", textContent: s.title }),
        el("span", { className: "meta" }, `${s.year ?? ""}`,
          ...(s.missing ? [" · ", el("span", { className: "miss", textContent: `${s.missing} missing` })] : [])));
      card.dataset.tvdb = s.tvdbId;
      return card;
    });
  const managed0 = document.querySelector("#tab-series .seg [aria-pressed=true]").dataset.managed === "*" && !counts["*"];
  const empty = managed0
    ? el("div", { className: "empty" }, el("b", { textContent: "No series is downloaded by Unshackle yet." }),
        "Open a series under All, pick a service or one of the links found on TMDB, then save.")
    : el("div", { className: "empty" }, el("b", { textContent: "No series match these filters." }), "Clear the search or pick another filter.");
  $("#wall").removeAttribute("aria-busy");
  $("#wall").replaceChildren(...(cards.length ? cards : [empty]));
}

async function loadServiceOptions(tag) {
  if (!tag) return [];
  serviceOptions[tag] ??= await api(`/api/services/${encodeURIComponent(tag)}`);
  return serviceOptions[tag];
}

async function openDrawer(s, tab, fromRoute = false) {
  current = s;
  $("#d-probe-out").replaceChildren();
  $("#d-url").placeholder = "URL or ID of the series";  // a Crave suggestion changes it for one series only
  showReleaseSeen(s);
  const sick = S.config.series[s.tvdbId]?.service && S.health[s.tvdbId];
  $("#d-sick").hidden = !sick;
  if (sick) $("#d-sick").replaceChildren(el("b", { textContent: `Its last ${sick.failing} downloads failed` }),
    el("span", { textContent: sick.cause || "See the history in Activity for why." }),
    el("span", { textContent: "Test this series below: a URL the service no longer knows, or cookies to update in Settings, Cookies." }));
  $("#d-probe-state").textContent = "Asks the service which episodes it has. Nothing is downloaded.";
  drawerPushed = !fromRoute;
  if (!fromRoute) { drawerTab = tab || "episodes"; navigate(true); }  // back will close it
  const existed = s.tvdbId in S.config.series;
  const conf = (S.config.series[s.tvdbId] ??= { service: "", title: "", options: {}, service_options: {} });
  conf.options ??= {}; conf.service_options ??= {};  // before the snapshot: filling them in is no change
  drawerSnapshot = { id: s.tvdbId, existed, conf: JSON.stringify(conf) };  // what closing it compares with
  if (s.poster) $("#d-poster").src = s.poster; else $("#d-poster").removeAttribute("src");
  $("#d-title").textContent = s.title;
  renderMeta(s);
  $("#d-service").replaceChildren(el("option", { value: "", textContent: "Not downloaded by Unshackle" }),
    ...S.services.map((t) => el("option", { value: t, textContent: t })));
  $("#d-service").value = conf.service || "";
  $("#d-url").value = conf.title || "";
  $("#d-fallback").value = conf.fallback_profiles || "";
  renderFind(conf.service);
  $("#d-subs").value = conf.subs_accept || "";
  $("#d-prefer").value = conf.audio_prefer || "";
  $("#d-prefer").placeholder = S.config.settings?.audio_prefer || "Example: fr";
  $("#d-subs").placeholder = S.config.settings?.subs_accept || "Example: fr";
  knownProfiles(conf.service);
  $("#d-release").value = conf.release_time || "";
  $("#d-release-day").value = String(conf.release_day || 0);
  $("#d-audio").value = conf.audio_accept || "";
  $("#d-audio").placeholder = S.config.settings?.audio_accept || "Example: fr, en";
  bcMonth = null;  // its own schedule's month: from its first day's
  $("#d-bc-form").hidden = true;
  $("#d-bc").open = !!conf.broadcast;  // folded away unless the series has one
  $("#d-since").textContent = conf.since
    ? `New episodes airing from ${new Date(conf.since).toLocaleDateString(LOCALE, { day: "numeric", month: "long", year: "numeric" })} are downloaded automatically. For older ones, tick them in Episodes.`
    : "Once it has a service and you save, new episodes are downloaded automatically. For older ones, tick them in Episodes.";
  fillAdv(conf, s.title);
  fillQualityImport(conf);
  $("#d-adv").open = Boolean(conf.file_name || Object.keys(conf.season_map || {}).length || conf.episode_offset || conf.season_offset
    || Object.keys(conf.episode_map || {}).length || conf.join_parts === false || conf.parts || (conf.episode_name && conf.episode_name !== "joined"));
  optionsEditor($("#d-opts"), S.dlOptions, conf.options);
  renderHideToggle();
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#backdrop").classList.add("open");
  if (!document.body.classList.contains("drawer-open")) wallScroll = scrollY;  // a phone scrolls the page itself: back to it on close
  document.body.classList.add("drawer-open");
  showDrawerTab(tab || (conf.service ? "episodes" : "settings"), true);  // a new series needs its service first
  navigate(false);
  loadEpisodes(s);
  $("#drawer .d-body").scrollTop = 0;
  dsPicked = null;
  requestAnimationFrame(dsLight);  // its first card lit in the shortcuts
  scrollTo({ top: 0 });
  $("#close").focus();
  loadSuggestions(s);
  await renderServiceOptions(conf);
}

/* Where the series stands, above its settings: downloaded from its service, missing its URL, failing, or not downloaded. */
function renderSeriesState() {
  const conf = current && S.config.series[current.tvdbId], box = $("#ds-state");
  if (!conf) return;
  const sick = conf.service && S.health[current.tvdbId];
  const [cls, text] = !conf.service ? ["", "Not downloaded by Unshackle"]
    : sick ? ["down", `Its downloads from ${svcName(conf.service)} fail`]
    : !conf.title ? ["warn", `On ${svcName(conf.service)}: its series URL is missing`]
    : ["ok", `Downloaded from ${svcName(conf.service)}`];
  box.classList.remove("ok", "down", "warn");
  if (cls) box.classList.add(cls);
  $("#ds-state-text").textContent = text;
}
/* Service options only show for a service that has some; "stop" only for a managed series. */
async function renderServiceOptions(conf) {
  renderSeriesState();
  const specs = await loadServiceOptions(conf.service).catch(() => []);  // Unshackle down: no options to offer
  if (current && S.config.series[current.tvdbId] !== conf && oneOff !== conf) return;  // another series opened meanwhile
  $("#d-service-field").hidden = !specs.length && !Object.keys(conf.service_options).length;
  optionsEditor($("#d-service-opts"), specs, conf.service_options);
  $("#d-stop").hidden = !conf.service;
  renderInherited(conf);
  renderBroadcast();
}

/* The logo goes home: the schedule, from wherever you are. */
$("#home").onclick = async (e) => {
  e.preventDefault();
  if (current && !await closeDrawer(true)) return;
  showTab("schedule");
  scrollTo({ top: 0 });
};
let wallScroll = 0;
/* A series' settings changed since its panel opened, and not saved: closing it asks first. */
let drawerSnapshot = null;
const drawerChanged = () => !!current && drawerSnapshot?.id === current.tvdbId && JSON.stringify(S.config.series[current.tvdbId]) !== drawerSnapshot.conf;
function discardDrawer() {  // the series as it was when its panel opened; the bar goes if nothing else waits
  const { id, existed, conf } = drawerSnapshot;
  if (existed) S.config.series[id] = JSON.parse(conf); else delete S.config.series[id];
  if (configKey(S.config) === savedConfig) { unsaved = false; $("#savebar").hidden = true; $("#d-save").hidden = true; }
}
function askUnsaved(name) {
  const dialog = $("#unsaved-dialog");
  $("#unsaved-text").textContent = `${name}: its settings changed and are not saved yet.`;
  dialog.returnValue = "keep";
  dialog.showModal();
  return new Promise((done) => dialog.addEventListener("close", () => done(dialog.returnValue || "keep"), { once: true }));
}
async function closeDrawer(fromRoute = false) {
  if (!$("#drawer").classList.contains("open")) return true;
  if (drawerChanged()) {
    const choice = await askUnsaved(current.title);
    if (choice === "save") {
      try { await save(); } catch (e) { toast(`Could not save: ${e.message}`, true); return keepDrawer(fromRoute); }
    } else if (choice === "discard") discardDrawer();
    else return keepDrawer(fromRoute);
  }
  if (!fromRoute && drawerPushed) { history.back(); return true; }  // popstate closes it, and back stays in step
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#backdrop").classList.remove("open");
  document.body.classList.remove("drawer-open");
  const id = current.tvdbId;
  current = null;
  if (!fromRoute) navigate(false);  // opened from a link: no history entry to go back to
  renderWall();  // rebuilds the cards: focus the new card of the series just closed
  scrollTo({ top: wallScroll });
  document.querySelector(`.poster[data-tvdb="${id}"]`)?.focus({ preventScroll: true });
  return true;
}
function keepDrawer(fromRoute) {  // it stays open; the back button already left its address: back to it
  if (fromRoute) { drawerPushed = true; history.pushState(null, "", routeHash()); }
  return false;
}

$("#d-refresh").onclick = () => current && loadSuggestions(current, true);
/* A spinner with its text while something loads; a message and a retry when it fails. */
function loading(text) {
  return el("div", { className: "loading", role: "status" }, el("span", { className: "spinner", ariaHidden: "true" }), text);
}
function failed(text, retry) {
  return el("div", { className: "failed", role: "alert" }, el("span", { textContent: text }),
    retry ? el("button", { className: "btn small", textContent: "Try again", onclick: retry }) : "");
}

/* No link from TMDB: the series' channel may have a catch-up service here, picked and looked up on by its name. */
function networkPick(s, box, none, lead) {
  const tag = S.networkServices?.[s.tvdbId];
  if (!tag) return box.replaceChildren(el("small", { className: "muted", textContent: none }));
  box.replaceChildren(el("small", { className: "muted", textContent: `${lead} Its channel, ${s.network}, is on ${svcName(tag)}:` }),
    el("button", { type: "button", ariaLabel: `Find it on ${svcName(tag)}`, onclick: () => {
      const sel = $("#d-service");
      if (sel.value !== tag) { sel.value = tag; sel.dispatchEvent(new Event("change")); }
      $("#d-find").hidden = true;
      $("#d-find-q").value = s.title;
      $("#d-find-open").click();
    } }, el("b", { textContent: tag }), el("span", { textContent: `Find ${s.title} on ${svcName(tag)}` })));
}

async function loadSuggestions(s, refresh = false) {
  const box = $("#d-suggest");
  $("#d-checked").textContent = "";
  $("#d-refresh").hidden = !s.tmdbId;
  if (!s.tmdbId) return networkPick(s, box, "Sonarr has no TMDB ID for this series.", "Sonarr has no TMDB ID for this series.");
  box.replaceChildren(loading("Looking up where to watch it…"));
  let links;
  try {
    const found = await api(`/api/suggest/${s.tmdbId}${refresh ? "?refresh=1" : ""}`);
    links = found.links;
    if (current === s) $("#d-checked").textContent = `checked ${ago(found.checked)}`;
  }
  catch (e) { if (current === s) box.replaceChildren(failed(`TMDB lookup failed: ${e.message}`, () => loadSuggestions(s, refresh))); return; }
  if (current !== s) return;  // another series was opened meanwhile
  if (!links.length) return networkPick(s, box, `TMDB lists no supported service in ${S.config.tmdb_countries.join(", ")}. Enter it below by hand.`,
    "TMDB lists no supported service.");
  box.replaceChildren(...links.map((l) => el("button", {
    ariaLabel: `Use ${l.service}: ${l.url}`,
    onclick: () => applySuggestion(l),
  }, el("b", { textContent: l.service }), el("span", { textContent: l.needs_series_url || l.episode ? `episode link · ${l.url}` : l.url }),
    el("small", { textContent: l.country }))));
  box.querySelectorAll("button").forEach((b, i) => b.dataset.url = links[i].url);
}

async function applySuggestion(link) {
  const conf = S.config.series[current.tvdbId];
  if (conf.service !== link.service) conf.service_options = {};
  conf.service = link.service;
  conf.title = link.needs_series_url ? "" : link.url;
  $("#d-service").value = link.service;
  $("#d-url").value = conf.title;
  if (link.needs_series_url) {  // Crave: only an episode is linked; its series page has the URL to use
    if (/^https?:\/\//i.test(link.url)) window.open(link.url, "_blank", "noopener");  // a web page only, never javascript:
    toast("This Crave link points to an episode. On crave.ca, open the series page and paste that URL here.");
    $("#d-url").placeholder = "https://www.crave.ca/en/series/name-12345";
    $("#d-url").focus();
  }
  renderSeriesState();
  optionsEditor($("#d-service-opts"), await loadServiceOptions(conf.service).catch(() => []), conf.service_options);
  dirty();
  renderWall();
}

$("#d-service").onchange = async (e) => {
  const conf = S.config.series[current.tvdbId];
  conf.service = e.target.value;
  conf.service_options = {};
  dirty();
  renderMeta(current);
  renderCheck(current, true);
  knownProfiles(conf.service);
  renderFind(conf.service);
  fillQualityImport(conf);  // its Default is its new service's
  await renderServiceOptions(conf);
  renderWall();
};
$("#d-stop").onclick = async () => {
  const conf = S.config.series[current.tvdbId];
  conf.service = "";
  conf.service_options = {};
  delete conf.since;
  $("#d-service").value = "";
  dirty();
  renderCheck(current, true);
  await renderServiceOptions(conf);
  renderWall();
  toast(`${current.title} will no longer be downloaded once you save`);
};
/* The service a link is on, from the services' sites (www.canalplus.com: CanalPlus); null for none known. */
function serviceOfLink(link) {
  let host;
  try { host = new URL(String(link).trim()).hostname.replace(/^www\./, ""); } catch { return null; }
  const hit = Object.entries(S.domains || {}).find(([domain]) => host === domain || host.endsWith(`.${domain}`));
  return hit ? hit[1] : null;
}
/* The series looked up by its name on its service (unshackle's search): a result picked fills in its URL. */
function renderFind(service) {
  $("#d-find").hidden = true;
  $("#d-find-out").replaceChildren();
  $("#d-find-open").hidden = !service;
  $("#d-find-open").textContent = service ? `Find it on ${svcName(service)}` : "";
}
$("#d-find-open").onclick = () => {
  $("#d-find").hidden = !$("#d-find").hidden;
  if ($("#d-find").hidden) return;
  if (!$("#d-find-q").value) $("#d-find-q").value = current.title;
  $("#d-find-go").click();
};
$("#d-find-q").onkeydown = (e) => { if (e.key === "Enter") $("#d-find-go").click(); };
$("#d-find-go").onclick = async () => {
  const conf = S.config.series[current.tvdbId], s = current, out = $("#d-find-out");
  if (!$("#d-find-q").value.trim()) return out.replaceChildren(el("p", { className: "status-err", textContent: "Type the series' name first" }));
  out.replaceChildren(loading(`Searching ${svcName(conf.service)}…`));
  let r;
  try { r = await api("/api/series/search", { method: "POST", body: JSON.stringify({ show: conf, query: $("#d-find-q").value }) }); }
  catch (e) { if (current === s) out.replaceChildren(el("p", { className: "status-err", textContent: e.message })); return; }
  if (current !== s) return;
  if (!r.results.length) return out.replaceChildren(el("p", { className: "muted", textContent: `Nothing found on ${svcName(conf.service)} under that name.` }));
  out.replaceChildren(...r.results.map((x) => el("button", { type: "button", className: "url-hit", onclick: () => {
    $("#d-url").value = x.url || x.id;
    $("#d-url").dispatchEvent(new Event("input"));
    $("#d-find").hidden = true;
    toast(`${x.title}: its URL is filled in`);
  } }, el("b", { textContent: x.title }), x.label ? el("span", { className: "url-hit-label", textContent: x.label }) : "",
    x.description ? el("small", { textContent: x.description }) : "")));
};
/* The profiles a service logs in with (credentials and cookie files): a click adds one to the series' fallbacks. */
async function knownProfiles(service) {
  const box = $("#d-fallback-known");
  box.hidden = true;
  if (!service) return;
  let r;
  try { r = await api(`/api/profiles/${encodeURIComponent(service)}`); } catch { return; }
  if (S.config.series[current?.tvdbId]?.service !== service || !r.profiles.length) return;
  box.replaceChildren(el("span", { textContent: `On ${svcName(service)}:` }), ...r.profiles.map((p) => el("button", { type: "button", className: "chip-btn", textContent: p,
    onclick: () => {
      const list = $("#d-fallback").value.split(/[\s,]+/).filter(Boolean);
      if (!list.includes(p)) { $("#d-fallback").value = [...list, p].join(", "); $("#d-fallback").dispatchEvent(new Event("input")); }
    } })));
  box.hidden = false;
}
$("#d-prefer").oninput = (e) => { S.config.series[current.tvdbId].audio_prefer = e.target.value.trim() || undefined; dirty(); };
$("#d-subs").oninput = (e) => { S.config.series[current.tvdbId].subs_accept = e.target.value.trim() || undefined; dirty(); };
$("#d-fallback").oninput = (e) => { S.config.series[current.tvdbId].fallback_profiles = e.target.value.trim() || undefined; dirty(); };
$("#d-url").oninput = async (e) => {
  const conf = S.config.series[current.tvdbId], before = serviceOfLink(conf.title);
  conf.title = e.target.value.trim();
  dirty();
  renderMeta(current);
  renderSeriesState();
  renderCheck(current, true);  // Episodes offers to ask the service at once
  // A link to another site: its service, picked for you (a service chosen by hand stays while its link is edited)
  const tag = serviceOfLink(conf.title);
  if (tag && tag !== before && tag !== conf.service && S.services.includes(tag)) {
    conf.service = tag;
    conf.service_options = {};
    $("#d-service").value = tag;
    toast(`${svcName(tag)}: recognised from the link`);
    renderMeta(current);
    renderCheck(current, true);
    await renderServiceOptions(conf);
    renderWall();
  }
};
/* Sonarr as a browser opens it: its public address, else the one Unshacklarr uses unless it's a Docker name (http://sonarr:8989). */
function sonarrWeb() {
  const set = S.config.settings || {};
  if (set.sonarr_public_url) return set.sonarr_public_url.replace(/\/+$/, "");
  try {
    const u = new URL(set.sonarr_url);
    // A Docker name or a LAN address only works at home. Sonarr runs beside Unshacklarr (as in the compose file): the
    // name this page was opened with reaches the same machine (Tailscale, a VPN, another LAN name). A LAN address
    // stays as it is through a reverse proxy (no port in the page's address): Sonarr's port is rarely open there,
    // and the LAN link still works at home. A Docker name always takes the page's: it has no link otherwise.
    const docker = !u.hostname.includes(".") && u.hostname !== "localhost";
    const lan = u.hostname === "localhost" || /^(10|127|192\.168|172\.(1[6-9]|2\d|3[01]))\./.test(u.hostname);
    if (location.hostname !== u.hostname && (docker || (lan && location.port))) u.hostname = location.hostname;
    return u.origin + u.pathname.replace(/\/+$/, "");
  } catch { return ""; }
}
/* Under the series' name: it on TVDB, TMDB, its service (the page Unshackle downloads from) and Sonarr. */
function renderMeta(s) {
  const ext = (label, href) => el("a", { href, target: "_blank", rel: "noopener", textContent: label, title: `This series on ${label}` });
  const conf = S.config.series[s.tvdbId];
  $("#d-meta").replaceChildren(ext("TVDB", `https://thetvdb.com/dereferrer/series/${s.tvdbId}`),
    ...(s.tmdbId ? [" · ", ext("TMDB", `https://www.themoviedb.org/tv/${s.tmdbId}`)] : []),
    ...(conf?.service && /^https?:\/\//.test(conf.title || "") ? [" · ", ext(svcName(conf.service), conf.title)] : []),
    ...(sonarrWeb() && s.titleSlug ? [" · ", ext("Sonarr", `${sonarrWeb()}/series/${s.titleSlug}`)] : []),
    s.year ? ` · ${s.year}` : "");
}
