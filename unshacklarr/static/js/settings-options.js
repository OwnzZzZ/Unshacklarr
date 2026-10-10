/* The command a download starts as: for every series, and for each service's. */
const argsOf = (opts) => Object.entries(opts || {}).flatMap(([f, v]) => v === true ? [f] : v === false || v === "" || v == null ? [] : [f, /\s/.test(v) ? `"${v}"` : String(v)]);
function renderCommands() {
  const box = $("#dx-cmd");
  if (!box || !S.config.defaults) return;
  box.textContent = ["unshackle dl", ...argsOf(S.config.defaults), "<service>", "<series>"].join(" ");
  document.querySelectorAll(".dx-svc-cmd").forEach((c) => {
    const l = S.config.service_defaults?.[c.dataset.tag] || {};
    c.textContent = ["unshackle dl", ...argsOf({ ...S.config.defaults, ...l.options }), c.dataset.tag, ...argsOf(l.service_options), "<series>"].join(" ");
  });
}

/* Options per service: defaults < service < series, for every series on that service. */
async function renderServiceDefaults() {
  const all = (S.config.service_defaults ??= {});
  const blocks = [];
  for (const tag of Object.keys(all).sort()) {
    const levels = all[tag];
    levels.options ??= {}; levels.service_options ??= {};
    const own = el("div", { className: "opts" }), dl = el("div", { className: "opts" });
    const specs = await loadServiceOptions(tag);
    optionsEditor(dl, S.dlOptions, levels.options);
    optionsEditor(own, specs, levels.service_options);
    const count = Object.keys(levels.options).length + Object.keys(levels.service_options).length + (levels.backend ? 1 : 0) + (levels.ladder ? 1 : 0);
    const servers = S.config.settings.backends || [];
    const backend = el("select", { onchange: (e) => { if (e.target.value) levels.backend = e.target.value; else delete levels.backend; dirty(); } },
      el("option", { value: "", textContent: "The main one (Settings, Unshackle)" }), ...servers.map((b) => el("option", { value: b.name, textContent: b.name })));
    backend.value = levels.backend || "";
    const ladder = ladderSelect(levels.ladder, S.config.settings.quality_ladder, (v) => { if (v) levels.ladder = v; else delete levels.ladder; dirty(); });
    blocks.push(el("div", { className: "svc-block" },
      el("div", { className: "svc-block-head" }, el("b", { textContent: tag }),
        el("small", { textContent: count ? `${count} option${count > 1 ? "s" : ""}` : "Nothing set yet" }),
        el("button", { className: "btn small danger", textContent: "Remove", onclick: () => { delete all[tag]; dirty(); renderServiceDefaults(); } })),
      el("div", { className: "sx-grid svc-picks" },
        ...(servers.length || levels.backend ? [field("Unshackle server", backend, `Where ${tag} downloads.`)] : []),
        field("Quality ladder", ladder, "Default: the one of Settings, Quality.")),
      el("span", { className: "sub", textContent: "Download options" }), dl,
      ...(specs.length || Object.keys(levels.service_options).length ? [el("span", { className: "sub", textContent: `Options of ${tag}` }), own] : []),
      el("div", { className: "dx-cmd" }, el("span", { textContent: `A ${tag} download starts as` }),
        el("code", { className: "dx-svc-cmd", translate: "no", dataset: { tag } }))));
  }
  $("#svc-defaults").replaceChildren(...(blocks.length ? blocks : [el("p", { className: "dx-empty", textContent: "No service has options of its own yet." })]));
  renderCommands();
  $("#svc-add").replaceChildren(el("option", { value: "", textContent: "+ Add a service" }),
    ...S.services.filter((t) => !all[t]).map((t) => el("option", { value: t, textContent: t })));
}
$("#svc-add").onchange = (e) => {
  if (!e.target.value) return;
  S.config.service_defaults[e.target.value] = { options: {}, service_options: {} };
  dirty();
  renderServiceDefaults();
};

/* What Settings' Per service sets for the series' service, so its page shows it applies; one the series sets
   itself replaces it, struck through here. */
function renderInherited(conf) {
  const svc = conf.service, per = S.config.service_defaults?.[svc] || {}, src = `Settings, Per service: ${svcName(svc)}`;
  const layer = (values) => Object.entries(values || {}).filter(([, v]) => v !== false && v !== "" && v != null).map(([f, v]) => [f, v, src]);
  inheritedRows($("#d-inherited"), layer(svc && per.options), conf.options, () => optionsEditor($("#d-opts"), S.dlOptions, conf.options));
  const own = layer(svc && per.service_options);
  inheritedRows($("#d-service-inherited"), own, conf.service_options, () => renderServiceOptions(conf));
  if (own.length) $("#d-service-field").hidden = false;
}
function inheritedRows(root, from, own, redraw) {
  const last = new Map(from.map((x) => [x[0], x]));
  root.replaceChildren(...[...last.values()].map(([flag, value, src]) => {
    const over = flag in own;
    return el("div", { className: `opt inh${over ? " over" : ""}` },
      el("code", { textContent: flag }),
      el("span", { className: "val" }, el("span", { className: "v", textContent: value === true ? "on" : String(value) }),
        el("small", { textContent: over ? `${src} · replaced below for this series` : src })),
      over ? el("span") : el("button", { type: "button", className: "btn small", textContent: "Change", title: "Set it differently for this series only",
        onclick: () => { own[flag] = value; dirty(); redraw(); } }));
  }));
  root.hidden = !last.size;
}

function renderSettings() {
  renderServiceDefaults();
  const set = S.config.settings;
  document.querySelectorAll("[data-set]").forEach((f) => {
    const key = f.dataset.set;
    if (key.endsWith("api_key")) {  // never shown back: only whether one is saved
      f.value = "";
      f.placeholder = set[`${key}_set`] ? (f.closest(".sx") ? "Type a new key to replace it" : "Saved (leave empty to keep it)") : "";
    } else f.value = set[key] ?? "";
  });
  settingsCountry?.sync();
  settingsZone?.sync();
  $("#sx-key-saved").hidden = !set.sonarr_api_key_set;
  $("#ux-key-saved").hidden = !set.unshackle_api_key_set;
  renderUnshackleState();
  renderAutomation();
  renderBackends();
  renderSonarrs();
  renderQuality();
  renderImportMode();
  renderUpgradeMode();
  renderLearnWindow();
  $("#sx-tmdb-saved").hidden = !set.tmdb_api_key_set;
  renderSonarrState();
  $("#downloads-input").placeholder = "In Unshacklarr's settings folder";
  showMode();
  $("#tmdb-hint").replaceChildren(...tmdbHint());
  $("#countries").value = S.config.tmdb_countries.join(", ");
  renderRegion();
  renderBackups();
  renderOffsite();
  const n = S.config.notifications;
  n.targets ||= [];
  document.querySelectorAll("[data-event]").forEach((c) => { c.checked = (n.events || {})[c.dataset.event] ?? true; });
  document.querySelectorAll("[data-level]").forEach((c) => { c.checked = n[c.dataset.level] ?? true; });
  renderTargets();
  renderNotify();
  $("#debug-mode").checked = set.debug === true;
  renderHistory();
}
$("#debug-mode").onchange = (e) => { S.config.settings.debug = e.target.checked; renderHistory(); dirty(); };
/* Activity history: how much is kept now, against the limits; debug said plainly, since it fills the logs. */
let hsCards = null;
async function loadHistory() {
  try { hsCards = (await api("/api/runs")).filter((c) => c.started); } catch { hsCards = null; }
  renderHistory();
}
function renderHistory() {
  const set = S.config.settings || {}, keep = Number($("#hs-keep").value), days = Number($("#hs-days").value);
  const box = $("#hs-state");
  if (hsCards) {
    const count = (...o) => hsCards.filter((c) => o.includes(c.outcome)).length;
    const done = count("downloaded", "kept", "import"), failed = count("failed", "interrupted"), missing = count("unavailable");
    const oldest = hsCards.map((c) => c.started).sort()[0];
    box.className = "sx-state";  // a count, neither good nor bad
    box.querySelector("b").textContent = hsCards.length ? `${hsCards.length} download${hsCards.length === 1 ? "" : "s"} kept` : "Nothing kept yet";
    box.querySelector("small").textContent = hsCards.length ? [done ? `${done} downloaded` : "", failed ? `${failed} failed` : "", missing ? `${missing} not out yet` : "", oldest ? `the oldest ${ago(oldest)}` : ""].filter(Boolean).join(" · ") : "";
  }
  const full = hsCards && keep && hsCards.length >= keep;
  $("#hs-say").textContent = !(keep && days) ? "" : `Keeps the last ${keep} downloads, none older than ${days} day${days === 1 ? "" : "s"}.`
    + (full ? ` Full now: each new download pushes the oldest out.` : hsCards ? ` Now: ${hsCards.length} of ${keep}.` : "");
  const on = set.debug === true;
  $("#hs-debug-card").classList.toggle("hs-on", on);
  $("#hs-debug-say").replaceChildren(on ? el("span", { className: "hs-warn", textContent: "On: the logs are much longer. Turn it off once the problem is found." })
    : "Off. Turn it on to look into a problem, and send the output with the report.");
}
["#hs-keep", "#hs-days"].forEach((id) => $(id).addEventListener("input", renderHistory));
$("#hs-open").onclick = () => showTab("log");
document.querySelectorAll("[data-set]").forEach((f) => f[f.tagName === "SELECT" ? "onchange" : "oninput"] = () => {
  const key = f.dataset.set;
  S.config.settings[key] = "num" in f.dataset ? Number(f.value) : key === "country" ? f.value.toUpperCase() : f.value.trim();
  dirty();
});
/* The main Sonarr's name: checked as it is typed, like the other Sonarr's names */
$("#sx-name").addEventListener("input", (e) => {
  const v = e.target.value.trim(), help = $("#sx-name-help");
  const why = !v ? "" : (S.config.settings.sonarrs || []).some((i) => i.name === v) ? "Another Sonarr has this name"
    : !/^[A-Za-z][A-Za-z0-9-]{0,23}$/.test(v) ? "A letter, then letters, digits or dashes" : "";
  e.target.classList.toggle("bad", !!why);
  help.classList.toggle("bad", !!why);
  help.textContent = why || "Shown on the series of each library when you have more than one Sonarr. Empty: Sonarr.";
});
function showMode() {
  $(".set-sec[data-sec=unshackle]").dataset.mode = $("#mode-select").value;
  document.querySelectorAll(".ux-tiles .wz-tile").forEach((t) => t.setAttribute("aria-checked", String(t.dataset.value === $("#mode-select").value)));
}
document.querySelectorAll(".ux-tiles .wz-tile").forEach((t) => t.onclick = () => {
  $("#mode-select").value = t.dataset.value;
  $("#mode-select").dispatchEvent(new Event("change"));
});
$("#mode-select").addEventListener("change", showMode);
$("#unshackle-test").onclick = async () => {
  const b = $("#unshackle-test"), box = $("#ux-state"), v = (k) => $(`[data-set=${k}]`).value.trim();
  b.disabled = true;
  box.className = "sx-state";
  box.querySelector("b").textContent = "Testing…";
  box.querySelector("small").textContent = "A first start of Unshackle takes a few seconds";
  try {
    const { services } = await api("/api/unshackle/test", { method: "POST", body: JSON.stringify({
      unshackle_mode: v("unshackle_mode"), unshackle_command: v("unshackle_command"),
      unshackle_url: v("unshackle_url"), unshackle_api_key: v("unshackle_api_key") }) });
    renderUnshackleState({ ...S.health?.unshackle, ok: true, mode: v("unshackle_mode"), services }, true);
  } catch (e) { renderUnshackleState({ ok: false, mode: v("unshackle_mode"), error: e.message }, true); }
  b.disabled = false;
};
$("#sonarr-test").onclick = async () => {
  const b = $("#sonarr-test"), box = $("#sx-state");
  b.disabled = true;
  box.className = "sx-state";
  box.querySelector("b").textContent = "Testing…";
  box.querySelector("small").textContent = $("[data-set=sonarr_url]").value.trim();
  try {
    const { version } = await api("/api/sonarr/test", { method: "POST", body: JSON.stringify({
      sonarr_url: $("[data-set=sonarr_url]").value.trim(), sonarr_api_key: $("[data-set=sonarr_api_key]").value.trim() }) });
    renderSonarrState({ ok: true, version }, true);
  } catch (e) { renderSonarrState({ ok: false, error: e.message }, true); }
  b.disabled = false;
};
/* A new password: its strength while typed (length first, then the kinds of characters), and whether it's typed the same twice. */
function pwStrength(pw) {
  if (!pw) return 0;
  if (pw.length < 8) return 1;
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z\d]/].filter((r) => r.test(pw)).length;
  return Math.min(4, 1 + (pw.length >= 12) + (pw.length >= 16) + (kinds >= 3));
}
function pwCheck() {
  const pw = $("#pw-new").value, again = $("#pw-again").value, score = pwStrength(pw);
  $("#pw-meter").dataset.score = score;
  $("#pw-meter span").textContent = ["", pw.length < 8 ? "Too short" : "Weak", "Fair", "Good", "Strong"][score];
  $("#pw-new").classList.toggle("bad", pw.length > 0 && pw.length < 8);
  $("#pw-new").classList.toggle("good", pw.length >= 8);
  const same = again && again === pw, typing = again && !same && pw.startsWith(again);  // still typing it: neither yet
  $("#pw-again").classList.toggle("good", !!same);
  $("#pw-again").classList.toggle("bad", !!again && !same && !typing);
  $("#pw-match").className = `pw-match ${!again || typing ? "" : same ? "good" : "bad"}`;
  $("#pw-match").textContent = !again || typing ? "" : same ? "✓ Passwords match" : "Passwords do not match";
}
$("#pw-new").oninput = $("#pw-again").oninput = $("#pw-current").oninput = () => { pwCheck(); acReady(); };
/* Account: the button only once it can work; the wrong current password said on its field; the session in words. */
function acReady() {
  const pw = $("#pw-new").value;
  $("#pw-current").classList.remove("bad"); $("#pw-current-say").textContent = "";
  $("#pw-change").disabled = !$("#pw-current").value || pw.length < 8 || pw !== $("#pw-again").value;
}
$("#pw-change").onclick = async () => {
  const b = $("#pw-change");
  b.disabled = true;
  try {
    await api("/api/password", { method: "POST", body: JSON.stringify({ current: $("#pw-current").value, new: $("#pw-new").value }) });
    $("#pw-current").value = $("#pw-new").value = $("#pw-again").value = "";
    pwCheck();
    b.textContent = "✓ Changed";
    setTimeout(() => { b.textContent = "Change the password"; }, 2500);
    loadAccount();
  } catch (e) {
    if (/current password/i.test(e.message)) {
      $("#pw-current").classList.add("bad"); $("#pw-current-say").className = "pw-match bad"; $("#pw-current-say").textContent = e.message; $("#pw-current").focus();
    } else toast(e.message, true);
    b.disabled = false;
  }
};
async function loadAccount() {
  let s;
  try { s = await api("/api/session"); } catch { return; }
  const until = s.expires ? new Date(s.expires * 1000).toLocaleDateString(LOCALE, { day: "numeric", month: "long" }) : "";
  // Let in by the reverse proxy, with no session of its own here: logging out is done there
  $("#ac-state b").textContent = s.proxy_user && !s.expires ? `Signed in by your reverse proxy as ${s.proxy_user}` : "Logged in on this device";
  $("#logout").hidden = Boolean(s.proxy_user && !s.expires);
  $("#ac-state small").textContent = [until && `until ${until}`, s.password_changed ? `password changed ${ago(s.password_changed)}` : "password set at the first run"].filter(Boolean).join(" · ");
  akShow(s.api_key);
}
/* Account, API key: shown once, in the command that uses it; afterwards only "✓ Saved" (the server keeps its hash). */
function akShow(k, key) {
  const set = !!k?.set;
  $("#ak-saved").hidden = $("#ak-delete").hidden = $("#ak-show").hidden = !set;
  $("#ak-new").textContent = set ? "Replace the key" : "Create a key";
  $("#ak-delete").dataset.sure = "";
  $("#ak-pass-row").hidden = true; $("#ak-pass").value = ""; akPassSay();
  $("#ak-delete").textContent = "Delete the key";
  $("#ak-cmd").textContent = `curl -H "X-Api-Key: ${key || "<your key>"}" ${location.origin}/api/v1/status`;
  $("#ak-say").className = `pw-match ${key ? "bad" : ""}`;
  $("#ak-say").textContent = key ? "Copy it now: it will not be shown again." : set && k.created ? `Created ${ago(k.created)}` : "";
}
function akPassSay(text) {
  $("#ak-pass").classList.toggle("bad", !!text);
  $("#ak-pass-say").className = `pw-match ${text ? "bad" : ""}`;
  $("#ak-pass-say").textContent = text || ($("#ak-saved").hidden ? "Asked again before a key is made." : "Asked again: the old key stops working.");
}
/* A new key: the password first (a session left open must not be enough), then the key, once. */
$("#ak-new").onclick = async () => {
  if ($("#ak-pass-row").hidden) { $("#ak-pass-row").hidden = false; akPassSay(); return $("#ak-pass").focus(); }
  if (!$("#ak-pass").value) { akPassSay("Type your password first"); return $("#ak-pass").focus(); }
  try {
    const r = await api("/api/api-key/new", { method: "POST", body: JSON.stringify({ password: $("#ak-pass").value }) });
    akShow({ set: true, created: r.created }, r.key);
  } catch (e) {
    if (/password/i.test(e.message)) { akPassSay(e.message); $("#ak-pass").select(); } else toast(e.message, true);
  }
};
$("#ak-pass").oninput = () => akPassSay();
$("#ak-pass").onkeydown = (e) => { if (e.key === "Enter") $("#ak-new").click(); };
$("#ak-delete").onclick = async (e) => {
  const b = e.currentTarget;
  if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = "Sure? Programs using it will stop working"; return setTimeout(() => { if (b.dataset.sure === "1") { b.dataset.sure = ""; b.textContent = "Delete the key"; } }, 4000); }
  try { await api("/api/api-key/delete", { method: "POST" }); akShow({ set: false }); }
  catch (err) { toast(err.message, true); }
};
$("#ak-cmd").after((() => {
  const copy = el("button", { type: "button", className: "jact", ariaLabel: "Copy the command",
    onclick: () => navigator.clipboard.writeText($("#ak-cmd").textContent).then(() => toast("Command copied"), () => toast("The browser refused to copy", true)) });
  copy.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>';
  return copy;
})());
$("#ac-others").onclick = async (e) => {
  const b = e.currentTarget;
  if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = "Sure? They log in again"; return setTimeout(() => { b.dataset.sure = ""; b.textContent = "Log out other devices"; }, 4000); }
  try { await api("/api/logout-others", { method: "POST" }); b.dataset.sure = ""; b.textContent = "✓ Others logged out"; loadAccount(); }
  catch (err) { toast(err.message, true); }
};
$("#ac-reset").after((() => {
  const copy = el("button", { type: "button", className: "jact", ariaLabel: "Copy the command",
    onclick: () => navigator.clipboard.writeText($("#ac-reset").textContent).then(() => toast("Command copied"), () => toast("The browser refused to copy", true)) });
  copy.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a1 1 0 0 1 1-1h10"/></svg>';
  return copy;
})());
/* Account, backup: the settings to a file and back, the password asked first (the file holds the keys). */
/* Account, automatic backups: the saved ones, newest first, each downloaded with the password (it holds the keys). */
function renderBackups() {
  const saved = S.backups?.saved || [];
  $("#bk-saved").hidden = !saved.length;
  if (!saved.length) return;
  const when = (at) => new Date(at).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" });
  $("#bk-saved-head").replaceChildren("Saved backups", el("small", { textContent: ` · ${saved.length} in ${S.backups.folder}` }));
  $("#bk-list").replaceChildren(...saved.map((f) => el("div", { className: "bk-row" },
    el("span", { textContent: when(f.at) }), el("small", { textContent: `${Math.max(1, Math.round(f.size / 1024))} KB` }),
    el("button", { className: "btn small", type: "button", textContent: "Download", onclick: () => backupFile(f.name) }))));
}
/* Account, backups sent elsewhere: WebDAV or S3, its fields, the passphrase, and how the last one went. Saved with
   its own button and the password, never by the settings' Save: a session alone must not send the keys elsewhere. */
let boKind = null;
function renderOffsite() {
  const set = S.config.settings;
  if (boKind === null) {
    boKind = set.backup_remote || "";
    $("#bo-url").value = set.backup_remote_url || ""; $("#bo-bucket").value = set.backup_remote_bucket || "";
    $("#bo-user").value = set.backup_remote_user || ""; $("#bo-region").value = set.backup_remote_region || "";
  }
  const s3 = boKind === "s3";
  $("#bo-kind").replaceChildren(...[["", "Off"], ["webdav", "WebDAV"], ["s3", "S3-compatible"]].map(([v, label]) => el("button", {
    type: "button", role: "radio", textContent: label, ariaChecked: String(boKind === v),
    onclick: () => { boKind = v; renderOffsite(); } })));
  $("#bo-fields").hidden = !boKind && !set.backup_remote;
  $("#bo-fields .sx-grid").hidden = !boKind;
  document.querySelectorAll(".bo-s3").forEach((x) => x.hidden = !s3);
  $("#bo-url-l").textContent = s3 ? "Endpoint URL" : "Folder URL";
  $("#bo-url").placeholder = s3 ? "https://s3.eu-west-3.amazonaws.com" : "https://cloud.example.com/remote.php/dav/files/me/backups";
  $("#bo-url-help").textContent = s3 ? "Backblaze B2, Cloudflare R2, Wasabi, Scaleway, OVH, AWS or MinIO." : "Nextcloud, Synology, QNAP, kDrive, pCloud… The folder must already exist.";
  $("#bo-user-l").textContent = s3 ? "Access key" : "User name";
  $("#bo-user-help").textContent = s3 ? "" : "Leave empty if the folder needs no login.";
  $("#bo-secret-l").textContent = s3 ? "Secret key" : "Password";
  $("#bo-secret-help").textContent = s3 ? (set.backup_remote_secret_set ? "Leave empty to keep the saved one." : "")
    : set.backup_remote_secret_set ? "An app password is safer than your account's password. Leave empty to keep the saved one."
    : "An app password is safer than your account's password.";
  $("#bo-secret-saved").hidden = !set.backup_remote_secret_set;
  $("#bo-pass-saved").hidden = !set.backup_passphrase_set;
  $("#bo-pass-help").textContent = set.backup_passphrase_set
    ? "At least 12 characters. It encrypts the backups, and you need it to restore one: keep it somewhere safe, outside this server. Leave empty to keep the saved one."
    : "At least 12 characters. It encrypts the backups, and you need it to restore one: keep it somewhere safe, outside this server.";
  $("#bo-send").textContent = boKind ? "Save and test" : "Save";
  const last = S.backups?.offsite || {}, when = last.at && new Date(last.at).toLocaleString(LOCALE, { dateStyle: "medium", timeStyle: "short" });
  $("#bo-say").className = `rt-who${last.at && !last.ok ? " status-err" : ""}`;
  $("#bo-say").textContent = !set.backup_remote ? ""
    : !Number(set.backup_every_days) ? "Automatic backups are off: set how often above, or send one now."
    : !last.at ? "Nothing sent yet. The next automatic backup will be sent here."
    : last.ok ? `Last upload: ${when}` : `Last upload failed (${when}): ${last.error}`;
  $("#bo-say").title = last.ok ? last.name : "";
  $("#bo-say").hidden = !$("#bo-say").textContent;
}
const boWrong = (id, text) => {
  const f = $(id), help = f.closest(".field").querySelector("small");
  f.classList.add("bad");
  help.dataset.was ??= help.textContent;
  help.textContent = text;
  help.classList.add("status-err");
  f.focus();
  f.addEventListener("input", () => { f.classList.remove("bad"); help.textContent = help.dataset.was; help.classList.remove("status-err"); delete help.dataset.was; }, { once: true });
};
$("#bo-send").onclick = async (e) => {
  const set = S.config.settings, s3 = boKind === "s3";
  if (boKind && !/^https?:\/\/\S+$/.test($("#bo-url").value.trim())) return boWrong("#bo-url", s3 ? "Enter the endpoint URL, starting with https://" : "Enter the folder URL, starting with https://");
  if (s3 && !$("#bo-bucket").value.trim()) return boWrong("#bo-bucket", "Enter the bucket.");
  if (s3 && !$("#bo-user").value.trim()) return boWrong("#bo-user", "Enter the access key.");
  if (s3 && !$("#bo-secret").value && !set.backup_remote_secret_set) return boWrong("#bo-secret", "Enter the secret key.");
  const phrase = $("#bo-pass").value;
  if (boKind && !phrase && !set.backup_passphrase_set) return boWrong("#bo-pass", "Choose a passphrase: the backups are encrypted with it.");
  if (phrase && phrase.length < 12) return boWrong("#bo-pass", "At least 12 characters: a few words are easy to remember.");
  if (!$("#bo-pw").value) return boWrong("#bo-pw", "Type your password first.");
  e.target.disabled = true;
  try {
    const settings = { backup_remote: boKind, backup_remote_url: $("#bo-url").value, backup_remote_bucket: $("#bo-bucket").value,
      backup_remote_user: $("#bo-user").value, backup_remote_secret: $("#bo-secret").value, backup_remote_region: $("#bo-region").value,
      backup_passphrase: $("#bo-pass").value };
    const r = await api("/api/backup/offsite", { method: "POST", body: JSON.stringify({ password: $("#bo-pw").value, settings }) });
    for (const k of Object.keys(settings).concat(["backup_remote_secret_set", "backup_passphrase_set"])) S.config.settings[k] = r.settings[k];
    savedConfig = configKey(S.config);  // saved on the server already: no unsaved change
    $("#bo-secret").value = $("#bo-pass").value = $("#bo-pw").value = "";
    if (r.sent.at) S.backups = { ...(S.backups || {}), offsite: r.sent };
    boKind = null;
    renderOffsite();
    toast(!r.sent.at ? "Saved" : r.sent.ok ? `Backup sent: ${r.sent.name}` : r.sent.error, r.sent.at && !r.sent.ok);
  } catch (err) { if (/^Wrong password/.test(err.message)) boWrong("#bo-pw", err.message); else toast(err.message, true); }
  finally { e.target.disabled = false; }
};
async function backupFile(name) {
  if (!bkPassword()) return;
  try {
    const r = await api("/api/backups/download", { method: "POST", body: JSON.stringify({ password: $("#bk-pass").value, name }) });
    const a = el("a", { href: URL.createObjectURL(new Blob([r.text], { type: "text/yaml" })), download: r.name });
    a.click();
    URL.revokeObjectURL(a.href);
    bkSay(`✓ ${r.name} downloaded`, false);
  } catch (e) { if (/password/i.test(e.message)) { bkSay(e.message); $("#bk-pass").select(); } else toast(e.message, true); }
}
const bkSay = (text = "", bad = true) => { $("#bk-say").textContent = text || "Asked again: the file holds secrets."; $("#bk-say").className = `pw-match${text ? (bad ? " bad" : " good") : ""}`; };
const bkPassword = () => { if ($("#bk-pass").value) return true; bkSay("Type your password first"); $("#bk-pass").focus(); return false; };
$("#bk-pass").oninput = () => bkSay();
$("#bk-save").onclick = async () => {
  if (!bkPassword()) return;
  try {
    const r = await api("/api/backup", { method: "POST", body: JSON.stringify({ password: $("#bk-pass").value }) });
    const a = el("a", { href: URL.createObjectURL(new Blob([r.text], { type: "text/yaml" })), download: r.name });
    a.click();
    URL.revokeObjectURL(a.href);
    bkSay(`✓ ${r.name} downloaded`, false);
  } catch (e) { if (/password/i.test(e.message)) { bkSay(e.message); $("#bk-pass").select(); } else toast(e.message, true); }
};
$("#bk-load").onclick = () => { if (bkPassword()) $("#bk-file").click(); };
$("#bk-file").onchange = async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  try {
    const text = await file.text();
    bkFile = text;
    const r = await api("/api/restore", { method: "POST", body: JSON.stringify({ password: $("#bk-pass").value, text, passphrase: $("#bk-phrase").value }) });
    toast(`Settings restored: ${r.series} series`);
    setTimeout(() => location.reload(), 900);  // everything on the page comes from the settings
  } catch (err) {
    if (/passphrase/i.test(err.message)) { $("#bk-phrase-row").hidden = false; $("#bk-phrase").focus(); bkSay(err.message); }
    else if (/password/i.test(err.message)) { bkSay(err.message); $("#bk-pass").select(); } else bkSay(err.message);
  }
};
let bkFile = "";  // an encrypted backup picked: Enter in its passphrase restores it
$("#bk-phrase").onkeydown = async (e) => {
  if (e.key !== "Enter" || !bkFile) return;
  try {
    const r = await api("/api/restore", { method: "POST", body: JSON.stringify({ password: $("#bk-pass").value, text: bkFile, passphrase: e.target.value }) });
    toast(`Settings restored: ${r.series} series`);
    setTimeout(() => location.reload(), 900);
  } catch (err) { bkSay(err.message); }
};
$("#logout").onclick = async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); location.reload(); };
$("#countries").oninput = (e) => {
  S.config.tmdb_countries = e.target.value.toUpperCase().split(/[\s,]+/).filter((c) => COUNTRIES.split(" ").includes(c));
  renderRegion();
  dirty();
};
/* Interface: the language of this browser, and spoilers on or off for the account. */
function renderInterface() {
  const on = S.config.settings.spoiler_free === true;
  $("#rg-spoiler").checked = on;
  $("#if-state b").textContent = LANGS[LANG];
  $("#if-state small").textContent = on ? "Episode titles hidden" : "Episode titles shown";
}
/* Region: the time where you are, the countries as flags, a time zone that exists. */
const ZONES = Intl.supportedValuesOf?.("timeZone") || [];
function renderRegion() {
  const set = S.config.settings || {}, tz = set.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  const names = new Intl.DisplayNames([LOCALE, "en"], { type: "region" });
  const now = new Date(), at = (o) => now.toLocaleString(LOCALE, { timeZone: tz, ...o });
  const state = $("#rg-state");
  state.querySelector("b").textContent = `${at({ weekday: "long" })} ${at({ hour: "2-digit", minute: "2-digit" })}`;
  const offset = at({ timeZoneName: "shortOffset" }).split(" ").pop().replace("GMT", "UTC");
  state.querySelector("small").textContent = [tz.replaceAll("_", " "), offset, set.country ? `${flagOf(set.country)} ${names.of(set.country) || set.country}` : ""].filter(Boolean).join(" · ");
  const typed = $("#countries").value.toUpperCase().split(/[\s,]+/).filter(Boolean);
  $("#countries").classList.toggle("bad", typed.some((c) => !COUNTRIES.split(" ").includes(c)));
  $("#rg-countries").replaceChildren(...typed.map((c) => COUNTRIES.split(" ").includes(c)
    ? el("span", { className: "rg-chip" }, el("span", { className: "flag", textContent: flagOf(c) }), names.of(c) || c)
    : el("span", { className: "rg-chip bad", textContent: `${c}: not a country code` })));
  const mine = Intl.DateTimeFormat().resolvedOptions().timeZone;
  $("#tz-hint").replaceChildren(`Now ${at({ hour: "2-digit", minute: "2-digit" })} there.`,
    ...(mine && mine !== tz ? [" This browser is on ", el("button", { type: "button", className: "linkish", textContent: mine.replaceAll("_", " "), onclick: () => { $("#tz-select").value = mine; $("#tz-select").dispatchEvent(new Event("input")); settingsZone.sync(); } }), "."] : []));
}
$("#tz-select").addEventListener("input", () => renderRegion());
setInterval(() => { if (currentTab === "settings" && settingsSec === "region" && !document.hidden && S.config.settings) renderRegion(); }, 30000);  // its clock, while in sight
/* Notifications: the addresses (each by its app's name: an address holds its secret), what each takes,
   what is sent, the quiet hours, a preview, and what went out lately. */
const LEVEL_NAMES = { success: "Downloaded", warning: "Needs a look", error: "Failed" };
const APPS = [[/discord(app)?\.com\/api\/webhooks|^discord:/, "Discord"], [/^tgram:/, "Telegram"], [/^ntfys?:/, "ntfy"], [/^pover:/, "Pushover"],
  [/^slack:|hooks\.slack\.com/, "Slack"], [/^mailtos?:/, "E-mail"], [/^gotifys?:/, "Gotify"], [/^matrixs?:/, "Matrix"], [/^signals?:/, "Signal"], [/^whatsapp:/, "WhatsApp"]];
const appOf = (u) => APPS.find(([re]) => re.test(u))?.[1] || u.split(":")[0];
const badgeOf = (app) => ({ "E-mail": "@", Other: "…" })[app] || app.slice(0, 2);
const ntTested = new Map();  // address -> what its last test said
const BUILDERS = {
  Discord: { hint: "In Discord: the channel's settings, Integrations, Webhooks, New Webhook, Copy Webhook URL.",
    fields: [["webhook", "Webhook URL", "https://discord.com/api/webhooks/…"]], url: (f) => f.webhook,
    check: (f) => /^https:\/\/([\w-]+\.)?discord(app)?\.com\/api\/webhooks\/\d+\/[\w-]+/.test(f.webhook) || ["webhook", "Paste the whole webhook URL: it starts with https://discord.com/api/webhooks/"] },
  Telegram: { hint: "Create a bot with @BotFather to get its token. Then send your bot a message, and get your chat ID from @userinfobot.",
    fields: [["token", "Bot token", "123456789:AAE…"], ["chat", "Chat ID", "123456789"]], url: (f) => `tgram://${f.token}/${f.chat}`,
    check: (f) => !/^\d+:[\w-]{20,}$/.test(f.token) ? ["token", "A bot token looks like 123456789:AAE…"] : /^-?\d+$|^@\w+$/.test(f.chat) || ["chat", "A chat ID is a number (or @channel)"] },
  ntfy: { hint: "Install the ntfy app and subscribe to a topic. Pick a name that is hard to guess: anyone who knows it can read your messages.",
    fields: [["topic", "Topic", "unshacklarr-7f3k"], ["server", "Server, if not ntfy.sh", "ntfy.example.com"]],
    url: (f) => f.server ? `ntfys://${f.server.replace(/^https?:\/\//, "").replace(/\/+$/, "")}/${f.topic}` : `ntfy://${f.topic}`,
    check: (f) => /^[\w-]{1,64}$/.test(f.topic) || ["topic", "A topic: letters, digits, - and _"] },
  Pushover: { hint: "Your user key is shown on pushover.net once you log in. Create an application there to get the API token.",
    fields: [["user", "User key"], ["token", "API token"]], url: (f) => `pover://${f.user}@${f.token}`,
    check: (f) => !/^\w{30}$/.test(f.user) ? ["user", "A user key is 30 letters and digits"] : /^\w{30}$/.test(f.token) || ["token", "An API token is 30 letters and digits"] },
  "E-mail": { hint: "For Gmail, Outlook or iCloud: use an app password, created in your account's security settings (not your usual password).",
    fields: [["email", "Your address", "you@gmail.com"], ["password", "App password", "", "password"]],
    url: (f) => { const [user, domain] = f.email.split("@"); return `mailtos://${encodeURIComponent(user)}:${encodeURIComponent(f.password)}@${domain}?to=${encodeURIComponent(f.email)}`; },
    check: (f) => !/^[^@\s]+@[^@\s]+\.\w+$/.test(f.email) ? ["email", "An e-mail address, like you@gmail.com"] : Boolean(f.password) || ["password", "The app password"] },
  Gotify: { hint: "In Gotify: Apps, Create Application, then copy its token.",
    fields: [["server", "Server", "https://gotify.example.com"], ["token", "App token"]],
    url: (f) => { const u = new URL(f.server); return `${u.protocol === "https:" ? "gotifys" : "gotify"}://${u.host}${u.pathname.replace(/\/+$/, "")}/${f.token}`; },
    check: (f) => { try { new URL(f.server); } catch { return ["server", "The server's address, with https://"]; } return Boolean(f.token) || ["token", "The app token"]; } },
  Other: { hint: "Any of Apprise's 100+ services. Find its address format in Apprise's list (linked at the top of the page).",
    fields: [["url", "Apprise address", "slack://…"]], url: (f) => f.url.trim(),
    check: (f) => /^[a-z][a-z0-9+.-]*:\/\/\S+$/i.test(f.url.trim()) || ["url", "An address like slack://…"] },
};
let ntApp = "Discord";
function renderTargets() {
  const targets = S.config.notifications.targets;
  $("#nt-targets").replaceChildren(...(targets.length ? targets.map((t, i) => {
    const tested = ntTested.get(t.url);
    const chips = Object.entries(LEVEL_NAMES).map(([lv, name]) => el("button", { type: "button", role: "checkbox", className: `nt-pick ${lv}`,
      ariaChecked: String(t.levels.includes(lv)), onclick: () => {
        t.levels = t.levels.includes(lv) ? t.levels.filter((x) => x !== lv) : [...t.levels, lv];
        renderTargets(); renderNotify(); dirty();
      } }, el("i", { ariaHidden: "true" }), name));
    const test = el("button", { type: "button", className: "btn small", textContent: tested === "…" ? "Sending…" : "Test", disabled: tested === "…", onclick: () => testTargets([t.url]) });
    const remove = el("button", { type: "button", className: "btn small danger", textContent: "Remove", onclick: () => { targets.splice(i, 1); renderTargets(); renderNotify(); dirty(); } });
    return el("div", { className: `nt-target${t.levels.length ? "" : " off"}` },
      el("span", { className: "nt-badge", textContent: badgeOf(appOf(t.url)), ariaHidden: "true" }),
      el("span", { className: "nt-who" }, el("b", { textContent: appOf(t.url) + (targets.filter((x) => appOf(x.url) === appOf(t.url)).length > 1 ? ` ${targets.filter((x, j) => j <= i && appOf(x.url) === appOf(t.url)).length}` : "") }),
        ...(tested && tested !== "…" ? [el("em", { className: tested.ok ? "ok" : "bad", textContent: tested.ok ? "✓ Delivered" : `✗ ${tested.error}` })] : [])),
      el("span", { className: "nt-chips", role: "group", ariaLabel: `What ${appOf(t.url)} gets` }, el("small", { textContent: "Sends" }), ...chips),
      el("span", { className: "btns" }, test, remove));
  }) : [el("p", { className: "muted cdm-empty", textContent: "No address yet: add Discord, Telegram, ntfy or another." })]));
}
async function testTargets(urls) {
  urls.forEach((u) => ntTested.set(u, "…"));
  renderTargets();
  try {
    const { results } = await api("/api/notifications/test", { method: "POST", body: JSON.stringify({ urls }) });
    urls.forEach((u, i) => ntTested.set(u, results[i]));
  } catch (e) { urls.forEach((u) => ntTested.set(u, { ok: false, error: e.message })); }
  renderTargets();
  loadSent();
}
$("#test-notif").onclick = () => S.config.notifications.targets.length ? testTargets(S.config.notifications.targets.map((t) => t.url)) : toast("Add an address first", true);
function renderNotify() {
  const n = S.config.notifications || {}, targets = n.targets || [];
  const on = Object.keys(LEVEL_NAMES).filter((k) => n[k] ?? true), used = targets.filter((t) => t.levels.some((lv) => on.includes(lv)));
  const box = $("#nt-state");
  box.className = `sx-state ${used.length ? "ok" : targets.length ? "warn" : ""}`;
  box.querySelector("b").textContent = !targets.length ? "No address yet" : !used.length ? "Nothing goes out" : used.length === 1 ? "Sending to 1 address" : `Sending to ${used.length} addresses`;
  box.querySelector("small").textContent = [[...new Set(used.map((t) => appOf(t.url)))].join(", "), n.quiet ? `quiet ${n.quiet.from}–${n.quiet.to}` : ""].filter(Boolean).join(" · ");
  const late = Number($("#nt-late").value), warnOff = !(n.warning ?? true);
  $("#nt-late-say").replaceChildren(late > 0 ? `Now: a message when an episode is still missing ${late} hour${late === 1 ? "" : "s"} after airing.` : "Now: no message for late episodes.",
    ...(late > 0 && warnOff ? [el("span", { className: "status-err", textContent: " Needs a look is turned off above, so this message will not be sent." })] : []));
  $("#nt-quiet-on").checked = Boolean(n.quiet);
  $("#nt-quiet").hidden = !n.quiet;
  if (n.quiet) { $("#nt-quiet-from").value = n.quiet.from; $("#nt-quiet-to").value = n.quiet.to; }
  $("#nt-quiet-say").textContent = n.quiet ? `Messages are held, then sent as one summary at ${n.quiet.to} (${S.config.settings.timezone.replaceAll("_", " ")}). A code to enter is sent at once.`
    : "Messages are held, then sent as one summary when they end.";
  renderNotifPreview();
}
function renderNotifPreview() {
  const lv = document.querySelector("#nt-pv-pick [aria-checked=true]")?.dataset.pv || "success";
  const show = S.series.find((x) => x.poster) || { title: "The Series", poster: "" };
  const label = `${show.title} S02E05`;
  const [kind, text, fields] = {
    success: ["Downloaded", "Imported by Sonarr.", [["Service", "RMCP"], ["Size", "1.4 GB"], ["Took", "38 s"]]],
    warning: ["Still unavailable", "Aired 2026-09-28, still not on RMCP.", [["Service", "RMCP"]]],
    error: ["Failed", "No stream in the requested quality.\nThe history in Activity has Unshackle's full output.", [["Service", "RMCP"]]],
  }[lv];
  const now = new Date().toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" });
  $("#nt-preview").replaceChildren(
    el("div", { className: `nt-discord ${lv}` },
      el("div", { className: "nt-dc-body" }, el("small", { textContent: kind }), el("b", { textContent: label }), el("p", { textContent: text }),
        el("div", { className: "nt-dc-fields" }, ...fields.map(([k, v]) => el("span", {}, el("b", { textContent: k }), v))),
        el("small", { className: "nt-dc-foot", textContent: `Unshacklarr · Today at ${now}` })),
      ...(show.poster ? [el("img", { src: show.poster, alt: "", loading: "lazy", onerror: (e) => e.target.remove() })] : [])),
    el("div", { className: "nt-phone" }, el("img", { src: "/icon-192.png", alt: "" }),
      el("span", {}, el("span", { className: "nt-ph-top" }, el("b", { textContent: "Unshacklarr" }), el("small", { textContent: "now" })),
        el("b", { textContent: `${kind}: ${label}` }), el("small", { textContent: text.split("\n")[0] }))));
}
document.querySelectorAll("#nt-pv-pick button").forEach((b) => b.onclick = () => {
  document.querySelectorAll("#nt-pv-pick button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
  renderNotifPreview();
});
document.querySelector("#nt-pv-pick button").setAttribute("aria-checked", "true");
async function loadSent() {
  let data;
  try { data = await api("/api/notifications/sent"); } catch (e) { return $("#nt-sent").replaceChildren(failed(e.message, loadSent)); }
  $("#nt-sent").replaceChildren(
    ...(data.held ? [el("p", { className: "nt-held", textContent: `${data.held} message${data.held === 1 ? "" : "s"} waiting for the end of the quiet hours` })] : []),
    ...(data.sent.length ? data.sent.map((m) => el("div", { className: `nt-msg ${m.level}` }, el("i"),
      el("span", { className: "nt-who" }, el("b", { textContent: m.title }), el("small", { textContent: ago(m.at) })),
      el("span", { className: "nt-to" }, ...m.to.map((t) => t.ok ? el("span", { className: "rg-chip", textContent: `✓ ${t.app}` })
        : hoverTip(el("span", { className: "rg-chip bad", textContent: `✗ ${t.app}` }), () => [t.error || "Not delivered"])))))
      : [el("p", { className: "muted cdm-empty", textContent: "Nothing sent yet." })]));
}
document.querySelectorAll("[data-level]").forEach((c) => c.onchange = () => { S.config.notifications[c.dataset.level] = c.checked; renderNotify(); dirty(); });
document.querySelectorAll("[data-event]").forEach((c) => c.onchange = () => { S.config.notifications.events = { ...(S.config.notifications.events || {}), [c.dataset.event]: c.checked }; dirty(); });
$("#nt-late").addEventListener("input", () => renderNotify());
$("#nt-quiet-on").onchange = (e) => { S.config.notifications.quiet = e.target.checked ? { from: $("#nt-quiet-from").value || "23:00", to: $("#nt-quiet-to").value || "08:00" } : null; renderNotify(); dirty(); };
["#nt-quiet-from", "#nt-quiet-to"].forEach((id) => $(id).onchange = () => {
  if (!$("#nt-quiet-from").value || !$("#nt-quiet-to").value) return;
  S.config.notifications.quiet = { from: $("#nt-quiet-from").value, to: $("#nt-quiet-to").value };
  renderNotify(); dirty();
});
/* Adding an address: the app, two or three fields said plainly, the Apprise address made from them. */
function ntDraw() {
  const b = BUILDERS[ntApp];
  $("#nt-apps").replaceChildren(...Object.keys(BUILDERS).map((name) => el("button", { type: "button", role: "radio", className: "nt-app", ariaChecked: String(name === ntApp),
    onclick: () => { ntApp = name; ntDraw(); $("#nt-fields input")?.focus(); } }, el("span", { className: "nt-badge", textContent: badgeOf(name) }), name)));
  $("#nt-d-hint").textContent = b.hint;
  $("#nt-fields").replaceChildren(...b.fields.map(([key, label, ph = "", type = "text"]) => el("label", { className: "field" }, label,
    el("input", { type, name: key, placeholder: ph, spellcheck: false, autocomplete: "off", dataset: { bwignore: "", "1pIgnore": "", lpignore: "true" },
      oninput: (e) => { e.target.classList.remove("bad"); $("#nt-d-result").replaceChildren(); } }))));
  $("#nt-d-result").replaceChildren();
}
function ntBuilt() {
  const b = BUILDERS[ntApp], f = Object.fromEntries([...$("#nt-fields").querySelectorAll("input")].map((i) => [i.name, i.value.trim()]));
  const ok = b.check(f);
  if (ok !== true) {
    const input = $(`#nt-fields [name=${ok[0]}]`);
    input.classList.add("bad"); input.focus();
    $("#nt-d-result").replaceChildren(el("span", { className: "status-err", textContent: ok[1] }));
    return null;
  }
  return b.url(f);
}
$("#nt-add").onclick = () => { ntApp = "Discord"; ntDraw(); $("#nt-dialog").showModal(); $("#nt-fields input").focus(); };
$("#nt-d-test").onclick = async () => {
  const url = ntBuilt();
  if (!url) return;
  $("#nt-d-result").replaceChildren(el("span", { className: "muted", textContent: "Sending…" }));
  try {
    const { results: [r] } = await api("/api/notifications/test", { method: "POST", body: JSON.stringify({ urls: [url] }) });
    $("#nt-d-result").replaceChildren(el("span", { className: r.ok ? "ok" : "status-err", textContent: r.ok ? "✓ Delivered: look for it, then Add." : `✗ ${r.error}` }));
  } catch (e) { $("#nt-d-result").replaceChildren(el("span", { className: "status-err", textContent: e.message })); }
};
$("#nt-form").onsubmit = (e) => {
  if (e.submitter?.value !== "add") return;
  e.preventDefault();
  const url = ntBuilt();
  if (!url) return;
  if (S.config.notifications.targets.some((t) => t.url === url)) return $("#nt-d-result").replaceChildren(el("span", { className: "status-err", textContent: "This address is already there." }));
  S.config.notifications.targets.push({ url, levels: Object.keys(LEVEL_NAMES) });
  $("#nt-dialog").close();
  renderTargets(); renderNotify(); dirty();
};

async function save() {
  // Drop series with no service so the file only lists what Unshackle downloads.
  const series = Object.fromEntries(Object.entries(S.config.series).filter(([, c]) => c.service));
  // Keep S.config as is: the open editors hold references into it.
  await api("/api/config", { method: "PUT", body: JSON.stringify({ ...S.config, series }) });
  unsaved = false;
  savedConfig = configKey(S.config);
  if (current) drawerSnapshot = { id: current.tvdbId, existed: true, conf: JSON.stringify(S.config.series[current.tvdbId]) };
  $("#savebar").hidden = true;
  $("#d-save").hidden = true;
  if (currentTab === "schedule") loadSchedule();  // a series just added shows up as managed at once
  renderServiceFilter();
  renderWall();
}
/* The button pressed says it is done ("Saved", in green) rather than a notice elsewhere, then
   leaves as before, unless something was changed meanwhile. */
async function saveHere(button, bar = null) {
  button.disabled = true;
  try { await save(); }
  catch (e) { button.disabled = false; return toast(`Could not save: ${e.message}`, true); }
  const label = button.innerHTML;
  const shown = bar || button;  // what save() just hid
  shown.hidden = false;
  button.classList.add("saved");
  button.textContent = "✓ Saved";
  setTimeout(() => {
    button.classList.remove("saved");
    button.innerHTML = label;
    button.disabled = false;
    if (!unsaved) shown.hidden = true;
  }, 1600);
}
$("#save").onclick = () => saveHere($("#save"), $("#savebar"));

let logTimer = null;
function ago(iso) {
  const min = Math.round((Date.now() - new Date(iso)) / 60000);
  return min < 1 ? "just now" : min < 60 ? `${min} min ago` : min < 1440 ? `${Math.round(min / 60)} h ago` : `${Math.round(min / 1440)} d ago`;
}
async function refreshLog() {
  if (document.hidden) { clearTimeout(logTimer); logTimer = setTimeout(refreshLog, 60000); return; }  // out of sight: back on sight
  let running, updated;
  try { ({ running, updated } = await api("/api/log")); }
  catch { clearTimeout(logTimer); logTimer = setTimeout(refreshLog, 15000); return; }  // a restart: try again, never stop
  $("#last-sync").textContent = running ? "Sync running…" : updated ? `Last sync ${ago(updated)}` : "";
  $("#log-state").textContent = running
    ? "A sync is running."
    : `No sync running. One starts automatically every ${S.config?.settings?.sync_every_hours == 1 ? "hour" : `${S.config?.settings?.sync_every_hours ?? 2} hours`}, or run one now.`;
  // another Sonarr whose ladder was never chosen: nothing downloads for it, said here too
  const paused = (S.config?.settings?.sonarrs || []).filter((i) => !i.quality_ladder).map((i) => i.name);
  if (paused.length) $("#log-state").append(el("span", { className: "status-err", textContent:
    ` Paused for ${paused.join(", ")}: choose a quality ladder in Settings, Sonarr.` }));
  $("#sync").disabled = running;
  $("#sync span").textContent = running ? "Sync running…" : "Run sync now";
  $("#sync").classList.toggle("spin", running);
  clearTimeout(logTimer);
  logTimer = setTimeout(refreshLog, running ? 3000 : 60000);
}

