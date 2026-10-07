/* First-run setup and login: a full-screen card over the app until the password is known. */
function gate(subtitle, ...content) {
  document.body.classList.add("gated");
  $("#gate").hidden = false;
  if (!$("#gate .lang-corner")) $("#gate").prepend(langCorner());
  $("#gate-sub").textContent = subtitle;
  $("#gate-form").replaceChildren(...content);
}
/* An address typed by hand: what is wrong with it, in words, or null (empty is not wrong: a field may be optional). */
function urlProblem(value) {
  const v = String(value || "").trim();
  if (!v) return null;
  if (/\s/.test(v)) return "The address has a space in it";
  if (!/^https?:\/\//i.test(v)) return "The address must start with http:// or https://";
  let u;
  try { u = new URL(v); } catch { return "This is not a valid address"; }
  if (!u.hostname || /^[.-]|[.-]$/.test(u.hostname)) return "The address needs a host, like http://sonarr:8989";
  return null;
}
/* Every address field says what is wrong once left, and stops saying it once right */
document.addEventListener("focusout", (e) => {
  const x = e.target;
  if (!(x instanceof HTMLInputElement) || x.type !== "url" || x.closest(".wiz-step")) return;  // the setup says it on Next
  const problem = urlProblem(x.value);
  x.classList.toggle("bad", !!problem);
  let hint = x.nextElementSibling?.classList.contains("url-hint") ? x.nextElementSibling : null;
  if (problem && !hint) x.after(hint = el("small", { className: "url-hint", role: "alert" }));
  if (hint) problem ? hint.textContent = problem : hint.remove();
});
document.addEventListener("input", (e) => {
  const x = e.target;
  if (x instanceof HTMLInputElement && x.type === "url" && x.classList.contains("bad") && !urlProblem(x.value)) {
    x.classList.remove("bad");
    if (x.nextElementSibling?.classList.contains("url-hint")) x.nextElementSibling.remove();
  }
});

/* A field: its name (a label: a click on it goes to the input), the input, and help that stays text to select. */
let fieldIds = 0;
function field(label, input, help, ...more) {
  input.id ||= `field-${++fieldIds}`;
  return el("div", { className: "field" }, el("label", { htmlFor: input.id, textContent: label }), input, ...more,
    help ? lineBySentence(el("small", {}, ...[help].flat())) : "");
}
function showLogin() {
  if (!$("#gate").hidden) return;
  const pw = el("input", { type: "password", name: "password", autocomplete: "current-password", required: true, autofocus: true });
  const error = el("div");
  gate("Log in to continue.", el("div", { className: "card" }, field("Password", pw), error),
    el("button", { className: "btn primary", type: "submit", textContent: "Log in" }));
  $("#gate-form").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/login", { method: "POST", body: JSON.stringify({ password: pw.value }) }); location.reload(); }
    catch (err) { error.replaceChildren(failed(err.message)); pw.select(); }
  };
  pw.focus();
}
/* A country, searched by its name (in the page's language) with its flag; its two-letter code is what is kept,
   in `real` (hidden), whose input event the page listens to as before. Ten at a time, the rest by scrolling. */
const COUNTRIES = "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW";
const flagOf = (code) => String.fromCodePoint(...[...code].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
const plain = (text) => text.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
function countryPicker(real) {
  const names = new Intl.DisplayNames([LOCALE, "en"], { type: "region" });
  return listPicker(real, COUNTRIES.split(" ").map((code) => ({ code, name: names.of(code) || code, flag: flagOf(code), small: code }))
    .sort((a, b) => a.name.localeCompare(b.name, LOCALE)), { placeholder: "Search a country", none: "No country matches" });
}
/* The time zone, picked the same way: its name, its offset from UTC now. */
function zonePicker(real) {
  const offset = (z) => new Date().toLocaleString("en", { timeZone: z, timeZoneName: "shortOffset" }).split(" ").pop().replace("GMT", "UTC");
  return listPicker(real, ZONES.map((code) => ({ code, name: code.replaceAll("_", " "), flag: "", small: offset(code) })),
    { placeholder: "Search a city or a region", none: "No time zone matches", exact: true });
}
/* A searchable list in place of a hidden input: the items are {code, name, flag, small}, the input keeps the code. */
function listPicker(real, all, { placeholder, none, exact = false }) {
  real.type = "hidden";
  const input = el("input", { type: "text", className: "cp-input", autocomplete: "off", spellcheck: false, role: "combobox",
    ariaAutoComplete: "list", ariaExpanded: "false", placeholder, dataset: { bwignore: "", "1pIgnore": "", lpignore: "true" } });
  const list = el("ul", { className: "cp-list", role: "listbox", hidden: true });
  let shown = [], active = -1;
  const current = () => all.find((c) => c.code === (exact ? String(real.value || "") : String(real.value || "").toUpperCase()));
  const sync = () => { const c = current(); input.value = c ? (c.flag ? `${c.flag}  ${c.name}` : c.name) : real.value || ""; };
  const close = () => { list.hidden = true; input.setAttribute("aria-expanded", "false"); };
  const draw = (query) => {
    const q = plain(query.trim());
    shown = all.filter((c) => !q || plain(c.name).includes(q) || c.code.toLowerCase() === q);
    shown.sort((a, b) => (plain(b.name).startsWith(q) || b.code.toLowerCase() === q) - (plain(a.name).startsWith(q) || a.code.toLowerCase() === q));
    active = shown.length ? Math.max(0, shown.findIndex((c) => c.code === current()?.code)) : -1;
    list.replaceChildren(...shown.map((c, i) => el("li", { role: "option", className: i === active ? "on" : "", ariaSelected: String(i === active),
      onmousedown: (e) => { e.preventDefault(); pick(c); } }, ...(c.flag ? [el("span", { className: "flag", textContent: c.flag })] : []), el("span", { textContent: c.name }), el("small", { textContent: c.small }))));
    if (!shown.length) list.append(el("li", { className: "cp-none", textContent: none }));
    list.style.top = `${input.offsetTop + input.offsetHeight + 4}px`;  // right under the input, over its help
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    list.children[active]?.scrollIntoView({ block: "nearest" });
  };
  const pick = (c) => {
    real.value = c.code;
    input.classList.remove("bad");
    real.dispatchEvent(new Event("input", { bubbles: true }));
    sync();
    close();
  };
  const move = (by) => {
    if (!shown.length) return;
    list.children[active]?.classList.remove("on");
    active = (active + by + shown.length) % shown.length;
    list.children[active].classList.add("on");
    list.children[active].scrollIntoView({ block: "nearest" });
  };
  input.addEventListener("focus", () => input.select());
  input.addEventListener("mousedown", () => { if (list.hidden) setTimeout(() => draw(""), 0); });  // a click opens it, a focus from a step does not
  input.addEventListener("input", (e) => { e.stopPropagation(); draw(input.value); });  // what is kept is the pick, not the search
  input.addEventListener("blur", () => { close(); sync(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); if (list.hidden) draw(""); else move(e.key === "ArrowDown" ? 1 : -1); }
    else if (e.key === "Enter" && !list.hidden && shown[active]) { e.preventDefault(); pick(shown[active]); }
    else if (e.key === "Escape") { close(); sync(); }
  });
  sync();
  return { input, list, sync };
}
let settingsCountry = null, settingsZone = null;

/* The first run, one step at a time: each is checked before the next (Unshackle and Sonarr answer, the
   password is typed twice), and a language change on the way keeps what was typed (secrets aside). */
function showSetup(found) {
  // addresses and folders come with the setup code (step one): empty until then
  found = { sonarr_url: "", sonarr_downloads: "", unshackle_command: "", unshackle_url: "", downloads: "", unshackle_downloads: "", ...found };
  let draft = {};
  try { draft = JSON.parse(sessionStorage.getItem("setup-draft") || "{}"); } catch {}  // this tab's, through a reload or a language change
  const input = (props) => el("input", { type: "text", ...props });
  // An API key is no password: password managers (Bitwarden, Vaultwarden, 1Password, LastPass) leave it alone
  const apiKey = (props) => input({ type: "password", autocomplete: "off", spellcheck: false, dataset: { bwignore: "", "1pIgnore": "", lpignore: "true" }, ...props });
  const f = {
    setup_code: input({ autocomplete: "off", spellcheck: false }),
    password: input({ type: "password", autocomplete: "new-password", minLength: 8 }),
    confirm: input({ type: "password", autocomplete: "new-password" }),
    sonarr_url: input({ type: "url", value: found.sonarr_url, placeholder: "http://sonarr:8989" }),
    sonarr_api_key: apiKey({
      placeholder: found.sonarr_api_key_set ? "Found in the environment for the URL above. Leave empty to use it" : "" }),
    sonarr_downloads: input({ value: found.sonarr_downloads, placeholder: "/downloads" }),
    unshackle_mode: el("select", {}, el("option", { value: "local", textContent: "Local" }), el("option", { value: "remote", textContent: "Remote" })),
    unshackle_command: input({ value: found.unshackle_command, placeholder: "unshackle", spellcheck: false }),
    unshackle_url: input({ type: "url", value: found.unshackle_url, placeholder: "http://unshackle:8786" }),
    unshackle_api_key: apiKey({
      placeholder: found.unshackle_api_key_set ? "Found in the environment for the address above. Leave empty to use it" : "" }),
    downloads: input({ value: found.downloads, placeholder: "In Unshacklarr's settings folder", spellcheck: false }),
    unshackle_downloads: input({ value: found.unshackle_downloads, placeholder: "Same path", spellcheck: false }),
    country: input({ value: found.country }),
    timezone: el("select", {}, ...(Intl.supportedValuesOf?.("timeZone") || ["UTC"]).map((z) => el("option", { value: z, textContent: z.replaceAll("_", " ") }))),
    tmdb_api_key: apiKey({ placeholder: "Optional" }),
  };
  const SECRET = ["password", "confirm", "sonarr_api_key", "unshackle_api_key", "tmdb_api_key"];
  f.unshackle_mode.value = found.unshackle_mode || "local";
  f.timezone.value = found.timezone !== "UTC" ? found.timezone : Intl.DateTimeFormat().resolvedOptions().timeZone;
  for (const [k, v] of Object.entries(draft.values || {})) if (f[k] && !SECRET.includes(k)) f[k].value = v;
  setupDraft = () => ({ step: at, reached, values: Object.fromEntries(Object.entries(f).filter(([k]) => !SECRET.includes(k)).map(([k, x]) => [k, x.value])) });
  const keep = () => { try { sessionStorage.setItem("setup-draft", JSON.stringify(setupDraft())); } catch {} };

  // The password: its strength, and the second one the same
  const meter = el("div", { className: "pw-meter", dataset: { score: "0" } }, el("i"), el("i"), el("i"), el("i"), el("span"));
  const match = el("small", { className: "pw-match" });
  const pwLive = () => {
    const pw = f.password.value, again = f.confirm.value, score = pwStrength(pw);
    meter.dataset.score = score;
    meter.lastChild.textContent = ["", pw.length < 8 ? "Too short" : "Weak", "Fair", "Good", "Strong"][score];
    const same = again && again === pw, typing = again && !same && pw.startsWith(again);
    f.confirm.classList.toggle("good", !!same);
    f.confirm.classList.toggle("bad", !!again && !same && !typing);
    match.className = `pw-match ${!again || typing ? "" : same ? "good" : "bad"}`;
    match.textContent = !again || typing ? "" : same ? "✓ Passwords match" : "Passwords do not match";
  };
  f.password.oninput = f.confirm.oninput = pwLive;

  const wrong = (error, ...fields) => ({ error, fields });  // what went wrong, and the fields it is about
  const ICONS = {
    key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
    unshackle: '<path d="M12 3v12m-5-5 5 5 5-5"/><path d="M5 21h14"/>',
    sonarr: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="m17 2-5 5-5-5"/>',
    region: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/>',
    alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5h.01"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    local: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    remote: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
  };
  const ico = (name, className = "wz-ico") => {
    const x = el("span", { className, ariaHidden: "true" });
    x.innerHTML = `<svg viewBox="0 0 24 24">${ICONS[name]}</svg>`;
    return x;
  };
  const tile = (value, name, sub) => el("button", { type: "button", className: "wz-tile", role: "radio", dataset: { value },
    onclick: () => { f.unshackle_mode.value = value; f.unshackle_mode.dispatchEvent(new Event("change")); keep(); } },
    ico(value, "wz-tile-ico"), el("b", { textContent: name }), el("small", { textContent: sub }));
  const tiles = el("div", { className: "wz-tiles", role: "radiogroup", ariaLabel: "Unshackle runs" },
    tile("local", "Local", "Unshacklarr starts it on this machine"), tile("remote", "Remote", "unshackle serve runs elsewhere"));
  const country = countryPicker(f.country);
  const unshackleParams = () => Object.fromEntries(["unshackle_mode", "unshackle_command", "unshackle_url", "unshackle_api_key"].map((k) => [k, f[k].value]));
  f.unshackle_mode.hidden = true;
  const uPanel = el("div", { className: "wiz-group" },
    el("div", { className: "field" }, el("label", { textContent: "Unshackle runs" }), tiles, f.unshackle_mode),
    el("div", { className: "only-local" }, field("Unshackle command", f.unshackle_command, "Empty: unshackle from the PATH. Otherwise its full path, e.g. the .venv/bin/unshackle of a git clone.")),
    el("div", { className: "only-remote" }, field("Address of unshackle serve", f.unshackle_url),
      field("API key", f.unshackle_api_key, "The serve: api_secret of its unshackle.yaml.")),
    field("Downloads folder", f.downloads, "Where episodes land before Sonarr imports them, as Unshacklarr sees it."),
    el("div", { className: "only-remote" }, field("Downloads folder, as Unshackle sees it", f.unshackle_downloads, "Its path on the machine where unshackle serve runs. Empty: the same path as above.")));
  const showMode = () => {
    uPanel.dataset.mode = f.unshackle_mode.value;
    tiles.querySelectorAll(".wz-tile").forEach((t) => t.setAttribute("aria-checked", String(t.dataset.value === f.unshackle_mode.value)));
  };
  f.unshackle_mode.onchange = showMode;
  showMode();

  /* A rebuild: the settings from a backup (one found in the data folder's backups folder, or a file), with the
     setup code and a new password; Sonarr and Unshackle are tested with its addresses, and the restore is done
     either way (one of them may not be back yet). */
  const restoreBox = el("div", { className: "wz-restore" });
  const restoreFile = el("input", { type: "file", accept: ".yaml,.yml,text/yaml", hidden: true });
  const restoreSay = el("div", { className: "wz-restore-say" });
  const restoreFrom = async (pick) => {
    const bad = !f.setup_code.value.trim() ? wrong("Enter the setup code", f.setup_code)
      : f.password.value.length < 8 ? wrong("Password must be at least 8 characters", f.password)
      : f.password.value !== f.confirm.value ? wrong("Passwords do not match", f.confirm) : null;
    if (bad) return showWrong(bad);
    restoreSay.replaceChildren(loading("Restoring, then testing Sonarr and Unshackle…"));
    try {
      const r = await api("/api/setup/restore", { method: "POST", signal: AbortSignal.timeout(180000),
        body: JSON.stringify({ setup_code: f.setup_code.value, password: f.password.value, ...pick }) });
      try { sessionStorage.removeItem("setup-draft"); } catch {}
      restored(r);
    } catch (e) { restoreSay.replaceChildren(failed(e.message)); }
  };
  restoreFile.onchange = async () => { const file = restoreFile.files[0]; restoreFile.value = ""; if (file) restoreFrom({ text: await file.text() }); };
  const openRestore = async () => {
    if (!f.setup_code.value.trim()) return showWrong(wrong("Enter the setup code first: it opens the backups", f.setup_code));
    restoreSay.replaceChildren(loading("Looking for backups…"));
    let saved = [];
    try { saved = (await api("/api/setup/backups", { method: "POST", body: JSON.stringify({ setup_code: f.setup_code.value }) })).saved; }
    catch (e) { return restoreSay.replaceChildren(failed(e.message)); }
    const when = (at) => new Date(at).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" });
    restoreSay.replaceChildren(
      el("p", { className: "muted", textContent: saved.length ? "Backups found in the data folder, newest first. The password above becomes yours." : "No backup in the data folder's backups folder. Pick a backup file instead." }),
      ...saved.map((b) => el("button", { type: "button", className: "wz-restore-row", onclick: () => restoreFrom({ name: b.name }) },
        el("b", { textContent: when(b.at) }), el("small", { textContent: `${Math.max(1, Math.round(b.size / 1024))} KB` }), el("span", { textContent: "Restore" }))),
      el("button", { type: "button", className: "btn small", textContent: "Restore from a file…", onclick: () => restoreFile.click() }));
  };
  restoreBox.append(el("b", { textContent: "Rebuilding an install?" }),
    el("span", { className: "muted", textContent: " Restore its settings from a backup instead of setting it up again." }),
    el("button", { type: "button", className: "btn small", textContent: "Restore from a backup", onclick: openRestore }), restoreFile, restoreSay);
  function restored(r) {
    reached = steps.length;
    drawRail(null);
    progress.firstChild.textContent = "Done";
    progress.querySelector("b").style.width = "100%";
    const line = (ok, text) => el("li", { className: ok ? "" : "wz-bad" }, ico(ok ? "check" : "alert", ""), el("span", { textContent: text }));
    panel.replaceChildren(el("div", { className: "wiz-step wz-done fwd" }, ico("check", "wz-done-ico"),
      el("h2", { textContent: "Settings restored" }),
      el("p", { className: "lead", textContent: r.series === 1 ? "1 series is back, with its options." : `${r.series} series are back, with their options.` }),
      el("ul", { className: "wz-sum" },
        line(r.sonarr.ok, r.sonarr.ok ? "Sonarr answers" : r.sonarr.error),
        line(r.unshackle.ok, r.unshackle.ok ? "Unshackle answers" : r.unshackle.error))));
    note.replaceChildren(r.sonarr.ok && r.unshackle.ok ? "" : el("p", { className: "muted", textContent: "Fix what does not answer in Settings: the restore is done." }));
    back.hidden = true;
    next.type = "button";
    next.disabled = false;
    next.replaceChildren("Open Unshacklarr", el("span", { className: "arr", textContent: "→", ariaHidden: "true" }));
    next.onclick = () => location.reload();
    next.focus();
  }
  const steps = [
    { name: "Welcome", icon: "key", title: "Welcome to Unshacklarr", sub: "Setup code and password",
      lead: "Enter the setup code, then choose the password you will sign in with.",
      body: [field("Setup code", f.setup_code, "Shown in Unshacklarr's log when it starts (docker logs unshacklarr), or the SETUP_TOKEN you set."),
        field("Password", f.password, "At least 8 characters.", meter), field("Confirm password", f.confirm, "", match), restoreBox],
      check: async () => {
        if (!f.setup_code.value.trim()) return wrong("Enter the setup code", f.setup_code);
        if (!f.password.value && draft.step) return wrong("Type your password again: it is not kept when the page reloads", f.password);
        if (f.password.value.length < 8) return wrong("Password must be at least 8 characters", f.password);
        if (f.password.value !== f.confirm.value) return wrong("Passwords do not match", f.confirm);
        // What the environment already says, given only with the right code: it fills the fields still empty
        let more;
        try { more = await api("/api/setup/found", { method: "POST", body: JSON.stringify({ setup_code: f.setup_code.value }) }); }
        catch (e) { return wrong(e.message, f.setup_code); }
        for (const k of ["sonarr_url", "sonarr_downloads", "unshackle_command", "unshackle_url", "downloads", "unshackle_downloads"]) {
          if (!f[k].value && more[k]) f[k].value = more[k];
        }
        if (more.sonarr_api_key_set) f.sonarr_api_key.placeholder = "Found in the environment for the URL above. Leave empty to use it";
        if (more.unshackle_api_key_set) f.unshackle_api_key.placeholder = "Found in the environment for the address above. Leave empty to use it";
      } },
    { name: "Unshackle", icon: "unshackle", title: "Connect Unshackle", sub: "What downloads the episodes",
      lead: "Unshackle downloads the episodes: here, started by Unshacklarr, or elsewhere, as unshackle serve.",
      body: [uPanel], fields: () => f.unshackle_mode.value === "local" ? [f.unshackle_command] : [f.unshackle_url, f.unshackle_api_key],
      check: async () => {
        if (f.unshackle_mode.value === "remote") {
          if (!f.unshackle_url.value.trim()) return wrong("Enter the address of unshackle serve", f.unshackle_url);
          const bad = urlProblem(f.unshackle_url.value);
          if (bad) return wrong(bad, f.unshackle_url);
        }
        const { services } = await api("/api/unshackle/test", { method: "POST", body: JSON.stringify({ ...unshackleParams(), setup_code: f.setup_code.value }) });
        return { ok: `Connected to Unshackle (${services} services)` };
      }, checking: "Testing… (a first start of Unshackle takes a few seconds)" },
    { name: "Sonarr", icon: "sonarr", title: "Connect Sonarr", sub: "Your library",
      lead: "Unshacklarr finds what Sonarr is missing, then hands it the downloads to import.",
      body: [field("Sonarr URL", f.sonarr_url, "The address Unshacklarr uses to reach Sonarr, e.g. http://sonarr:8989 on a shared Docker network."),
        field("API key", f.sonarr_api_key, "In Sonarr: Settings, General."),
        field("Downloads folder, as Sonarr sees it", f.sonarr_downloads, "Its path on Sonarr's side, e.g. inside Sonarr's container."),
        field("TMDB API key", f.tmdb_api_key, tmdbHint())],
      fields: () => [f.sonarr_url, f.sonarr_api_key],
      check: async () => {
        if (!f.sonarr_url.value.trim()) return wrong("Enter Sonarr's URL", f.sonarr_url);
        const bad = urlProblem(f.sonarr_url.value);
        if (bad) return wrong(bad, f.sonarr_url);
        if (!f.sonarr_downloads.value.trim()) return wrong("Enter the downloads folder as Sonarr sees it", f.sonarr_downloads);
        const { version } = await api("/api/sonarr/test", { method: "POST", body: JSON.stringify({
          sonarr_url: f.sonarr_url.value, sonarr_api_key: f.sonarr_api_key.value, setup_code: f.setup_code.value }) });
        return { ok: `Connected to Sonarr ${version}` };
      }, checking: "Testing the connection to Sonarr…" },
    { name: "Region", icon: "region", title: "Your region", sub: "Country and time zone",
      lead: "Your country picks the streaming services the Schedule shows; the time zone, when episodes are tried.",
      body: [field("Country", country.input, "The Schedule shows its streaming services.", country.list, f.country),
        field("Time zone", f.timezone, "For release times and the sync clock.")],
      check: async () => { if (!/^[A-Za-z]{2}$/.test(f.country.value.trim())) return wrong("Pick a country in the list", country.input); } },
  ];
  let at = Math.min(draft.step || 0, steps.length - 1), reached = Math.max(at, Math.min(draft.reached || 0, steps.length - 1));
  const rail = el("ol", { className: "wz-steps" });
  const progress = el("div", { className: "wz-progress" }, el("span"), el("i", {}, el("b")));
  const panel = el("div", { className: "wiz-panel" });
  const note = el("div", { className: "wiz-note", ariaLive: "polite" });
  const back = el("button", { type: "button", className: "btn wz-back", onclick: () => go(at - 1) }, el("span", { className: "arr", textContent: "←", ariaHidden: "true" }), "Back");
  const next = el("button", { type: "submit", className: "btn primary wz-next" });
  const oks = {};  // what each step's check found: the end screen sums it up
  const drawRail = (current) => rail.replaceChildren(...steps.map((x, i) => {
    const done = i !== current && i < reached || i < current;
    return el("li", { className: i === current ? "on" : done ? "done" : "" },
      el("button", { type: "button", disabled: i > reached || current === null, ariaCurrent: i === current ? "step" : null, onclick: () => go(i) },
        el("span", { className: "wz-dot" }, ico(done ? "check" : x.icon, "")), el("span", { className: "wz-txt" }, el("b", { textContent: x.name }), el("small", { textContent: x.sub }))));
  }));
  function go(to, forward = to > at) {
    at = Math.max(0, Math.min(to, steps.length - 1));
    reached = Math.max(reached, at);
    const s = steps[at];
    drawRail(at);
    progress.firstChild.textContent = `Step ${at + 1} of ${steps.length}`;
    progress.querySelector("b").style.width = `${(at + 1) / steps.length * 100}%`;
    panel.replaceChildren(el("div", { className: `wiz-step ${forward ? "fwd" : "bwd"}` },
      el("div", { className: "wz-head" }, ico(s.icon, "wz-badge"), el("div", {}, el("h2", { textContent: s.title }), el("p", { className: "lead", textContent: s.lead }))),
      ...s.body));
    note.replaceChildren();
    back.hidden = at === 0;
    const last = at === steps.length - 1;
    next.replaceChildren(last ? "Finish the setup" : "Next", el("span", { className: "arr", textContent: last ? "✓" : "→", ariaHidden: "true" }));
    panel.querySelector("input, select")?.focus({ preventScroll: true });
    keep();
  }
  /* The end: what was checked, and the way in */
  function allSet() {
    reached = steps.length;
    drawRail(null);
    progress.firstChild.textContent = "Done";
    progress.querySelector("b").style.width = "100%";
    const code = f.country.value.toUpperCase();
    const c = /^[A-Z]{2}$/.test(code) ? { flag: flagOf(code), name: new Intl.DisplayNames([LOCALE, "en"], { type: "region" }).of(code) } : null;
    panel.replaceChildren(el("div", { className: "wiz-step wz-done fwd" }, ico("check", "wz-done-ico"),
      el("h2", { textContent: "You're all set" }),
      el("p", { className: "lead", textContent: "Pick a service for your series in Series: Unshacklarr takes it from there." }),
      el("ul", { className: "wz-sum" }, ...[oks[1], oks[2], `${c ? `${c.flag} ${c.name}` : f.country.value} · ${f.timezone.value.replaceAll("_", " ")}`]
        .filter(Boolean).map((t) => el("li", {}, ico("check", ""), el("span", { textContent: t }))))));
    note.replaceChildren();
    back.hidden = true;
    next.type = "button";
    next.disabled = false;
    next.replaceChildren("Open Unshacklarr", el("span", { className: "arr", textContent: "→", ariaHidden: "true" }));
    next.onclick = () => location.reload();
    next.focus();
  }
  /* What is wrong, by the field it is about: red, a small shake, and the message in a bubble at its right
     (under it on a narrow screen); with no field to point at, under the step. */
  function showWrong({ error, fields }) {
    const [first] = fields;
    fields.forEach((x) => x.classList.add("bad"));
    if (!first) return note.replaceChildren(failed(error));
    note.replaceChildren();  // said in the bubble
    panel.querySelector(".field-error")?.remove();
    const bubble = el("div", { className: "field-error", role: "alert", textContent: error });
    first.after(bubble);
    bubble.style.setProperty("--at", `${first.offsetTop + first.offsetHeight / 2}px`);
    bubble.style.setProperty("--x", `${first.offsetLeft + first.offsetWidth + 14}px`);  // by the input's own right edge
    first.classList.remove("shake");
    void first.offsetWidth;  // the shake starts again
    first.classList.add("shake");
    first.focus({ preventScroll: true });
  }
  panel.addEventListener("input", keep);
  panel.addEventListener("input", (e) => {  // a mistake being fixed: its red goes, its bubble and the message
    if (e.target !== f.confirm) e.target.classList.remove("bad");
    panel.querySelector(".field-error")?.remove();
    if (note.textContent) note.replaceChildren();
  });
  const logo = $("#gate .logo").cloneNode(true);
  $("#gate").classList.add("wizard");
  gate("", el("div", { className: "wz" },
    el("aside", { className: "wz-side" },
      el("div", { className: "wz-brand" }, logo, el("div", {}, el("b", { textContent: "Unshacklarr" }), el("small", { textContent: "The episodes Sonarr is missing, downloaded by Unshackle." }))),
      el("p", { className: "wz-hello", textContent: "Set up Unshacklarr in a few steps." }), rail, progress),
    el("section", { className: "wz-main" }, panel, note, el("div", { className: "wiz-nav" }, back, next))));
  $("#gate-form").noValidate = true;  // each step says what is wrong itself, in words, not in a browser bubble
  $("#gate-form").onsubmit = async (e) => {
    e.preventDefault();
    const s = steps[at];
    next.disabled = back.disabled = true;
    note.replaceChildren(loading(s.checking || "Checking…"));
    let result;
    try { result = await s.check(); } catch (err) { result = wrong(err.message, ...(s.fields?.() || [])); }  // refused: its address, its key
    next.disabled = back.disabled = false;
    if (result?.error) return showWrong(result);
    if (result?.ok) oks[at] = result.ok;
    if (at < steps.length - 1) {
      go(at + 1);
      if (result?.ok) note.replaceChildren(el("p", { className: "ok-text", textContent: `✓ ${result.ok}` }));
      return;
    }
    const first = await steps[0].check();  // after a language change the password is typed again (it is never kept)
    if (first) { go(0); return showWrong(first); }
    const body = Object.fromEntries(Object.entries(f).filter(([k]) => k !== "confirm").map(([k, x]) => [k, x.value.trim()]));
    body.country = body.country.toUpperCase();
    body.language = LANG;
    next.disabled = true;
    note.replaceChildren(loading("Saving…"));
    try {
      await api("/api/setup", { method: "POST", body: JSON.stringify(body) });
      try { sessionStorage.removeItem("setup-draft"); } catch {}
      setupDraft = null;
      allSet();
    }
    catch (err) { next.disabled = false; note.replaceChildren(failed(err.message)); }
  };
  go(at);
}
let setupDraft = null;  // what setLang keeps for the page to come back to, on the first run

$("#lang-row").append(...Object.entries(LANGS).map(([k, name]) => el("button", { type: "button", role: "radio", className: "rg-lang", ariaChecked: String(k === LANG),
  onclick: () => { if (k !== LANG) setLang(k); } }, el("span", { className: "flag", textContent: FLAGS[k] }), el("b", { textContent: name }))));
settingsCountry = countryPicker($("[data-set=country]"));
$("[data-set=country]").after(settingsCountry.input, settingsCountry.list);
settingsZone = zonePicker($("#tz-select"));
$("#tz-select").after(settingsZone.input, settingsZone.list);
(async () => {
  await i18nReady;
  let session;
  try { session = await api("/api/session"); }
  catch (e) { return $("#wall").replaceChildren(failed(`Unshacklarr can't be reached: ${e.message}`, () => location.reload())); }
  if (!session.configured) return showSetup(session.setup);
  if (!session.logged_in) return showLogin();
  try {
    const data = await api("/api/state");
    S = { series: data.series, config: data.config, services: data.services, serviceNames: data.service_names || {}, dlOptions: data.dl_options, health: data.health || {}, cdm: data.cdm || {}, domains: data.service_domains || {}, builtinLadders: data.builtin_ladders || [], networkServices: data.network_services || {}, backups: data.backups || null };
    savedConfig = configKey(S.config);
    $("#set-version").textContent = data.version ? `Unshacklarr ${data.version}` : "";
    const update = $("#update");  // a newer release: in sight on every page, its notes one click away
    update.hidden = !data.update;
    if (data.update) {
      update.href = data.update.url;
      update.setAttribute("aria-label", `Unshacklarr ${data.update.version} is out: see what's new`);
      update.querySelector("span").textContent = `Update ${data.update.version}`;
    }
    if (data.unshackle_error) toast(`Unshackle: ${data.unshackle_error}`, true);
    renderServiceFilter();
    renderWall();
    optionsEditor($("#defaults"), S.dlOptions, S.config.defaults);
    renderSettings();
    refreshLog();
    refreshInbox();
    if (!busyStarted) { busyStarted = true; refreshBusy(); }  // one loop, however often the page reloads its state
    refreshHealth(currentTab === "settings" && settingsSec === "unshackle");
    if (currentTab === "settings" && settingsSec === "cookies") loadCookies();
    if (currentTab === "settings" && settingsSec === "cdm") loadCdm();
    if (currentTab === "settings" && settingsSec === "notifications") renderPush().catch(() => {});
    applyRoute();
  } catch (e) {
    $("#wall").replaceChildren(el("p", { textContent: `Could not load series: ${e.message}` }));
  }
})();
