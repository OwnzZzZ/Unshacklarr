/* Quality ladders (Settings, Quality), the other Unshackle servers (Settings, Unshackle) and what follows a download
   (Settings, Automation): their editors, and the pickers a service and a series show for them. */
const CODECS = [["", "Any codec"], ["AVC", "H.264"], ["HEVC", "H.265"], ["AV1", "AV1"], ["VP9", "VP9"], ["VP8", "VP8"], ["VC1", "VC-1"]];
const RANGES = [["", "Any range"], ["SDR", "SDR"], ["HDR10", "HDR10"], ["HDR10P", "HDR10+"], ["DV", "Dolby Vision"], ["HLG", "HLG"]];
const ladders = () => (S.config.quality_ladders ??= []);
const nameOf = (list, v) => (list.find(([k]) => k === v) || [v, v])[1];
function stepText(st) {
  const height = st.max && st.max !== st.min ? `${st.min || 0}–${st.max}p` : st.max ? `${st.max}p` : st.min ? `${st.min}p and up` : "Any height";
  return [height, st.codec ? nameOf(CODECS, st.codec) : "", st.range ? nameOf(RANGES, st.range) : ""].filter(Boolean).join(" ");
}

/* Every place a ladder (or a server) is picked by name: a rename follows, a removal falls back to the level before. */
function renameRefs(key, from, to) {
  const set = S.config.settings;
  if (key === "ladder" && set.quality_ladder === from) set.quality_ladder = to;
  for (const level of [...Object.values(S.config.service_defaults || {}), ...(key === "ladder" ? Object.values(S.config.series || {}) : [])])
    if (level[key] === from) { if (to) level[key] = to; else delete level[key]; }
}
function ladderUse(name) {
  const services = Object.entries(S.config.service_defaults || {}).filter(([, l]) => l.ladder === name).map(([t]) => t);
  const series = Object.values(S.config.series || {}).filter((c) => c.service && c.ladder === name).length;
  return [S.config.settings.quality_ladder === name ? "every series" : "", ...services.map(svcName), series ? `${series} series` : ""].filter(Boolean);
}

/* A ladder picker: "" is the level before (named in its label), off is none. */
function ladderSelect(value, inherited, onchange) {
  const sel = el("select", { onchange: (e) => onchange(e.target.value) },
    el("option", { value: "", textContent: `Default: ${inherited && inherited !== "off" ? inherited : "Off"}` }),
    el("option", { value: "off", textContent: "Off" }), ...ladders().map((l) => el("option", { value: l.name, textContent: l.name })));
  sel.value = value || "";
  return sel;
}

function renderQuality() {
  if (!S.config?.settings) return;
  const set = S.config.settings, list = ladders(), def = set.quality_ladder || "";
  const box = $("#ql-state");
  box.className = `sx-state${def && def !== "off" ? " ok" : ""}`;
  box.querySelector("b").textContent = def && def !== "off" ? `Every series: ${def}` : "Off for every series";
  const picked = Object.values(S.config.service_defaults || {}).filter((l) => l.ladder).length + Object.values(S.config.series || {}).filter((c) => c.service && c.ladder).length;
  box.querySelector("small").textContent = [list.length === 1 ? "1 ladder" : `${list.length} ladders`,
    picked === 1 ? "1 service or series picks its own" : picked ? `${picked} services or series pick their own` : ""].filter(Boolean).join(" · ");
  $("#ql-default").replaceChildren(...[["", "Off"], ...list.map((l) => [l.name, l.name])].map(([v, label]) => el("button", {
    type: "button", role: "radio", textContent: label, ariaChecked: String((def === "off" ? "" : def) === v),
    onclick: () => { set.quality_ladder = v; dirty(); renderQuality(); } })));
  $("#ql-list").replaceChildren(...(list.length ? list.map(ladderCard)
    : [el("section", { className: "sx-card" }, el("p", { className: "dx-empty", textContent: "No ladder: New ladder makes one, or bring back the built-in ones." }))]));
}

function ladderCard(lad) {
  const list = ladders(), use = ladderUse(lad.name);
  const say = el("small", { className: "ql-say", textContent: use.length ? `Used by ${use.join(", ")}` : "Not used yet: pick it above, for a service or a series" });
  const name = el("input", { type: "text", className: "ql-name", value: lad.name, ariaLabel: "Ladder name", spellcheck: false, maxLength: 40,
    oninput: (e) => {
      const v = e.target.value.trim(), why = !v ? "Give it a name" : v === "off" ? "Off is taken: it means no ladder"
        : list.some((o) => o !== lad && o.name === v) ? "Another ladder has this name" : "";
      e.target.classList.toggle("bad", !!why);
      say.classList.toggle("bad", !!why);
      say.textContent = why || (use.length ? `Used by ${use.join(", ")}` : "Not used yet: pick it above, for a service or a series");
      if (why) return;
      renameRefs("ladder", lad.name, v);
      lad.name = v;
      dirty();
      $("#ql-default").querySelectorAll("button").forEach((b, i) => { if (i && list[i - 1] === lad) b.textContent = v; });
    } });
  const del = el("button", { type: "button", className: "btn small danger", textContent: "Delete", onclick: (e) => {
    if (e.target.dataset.sure !== "1") { e.target.dataset.sure = "1"; e.target.textContent = use.length ? "Sure? It is in use" : "Sure?"; return; }
    list.splice(list.indexOf(lad), 1);
    renameRefs("ladder", lad.name, "");
    dirty(); renderQuality(); renderServiceDefaults();
  } });
  const copy = el("button", { type: "button", className: "btn small", textContent: "Duplicate", onclick: () => {
    let n = 2;
    while (list.some((o) => o.name === `${lad.name} (${n})`)) n++;
    list.splice(list.indexOf(lad) + 1, 0, { name: `${lad.name} (${n})`, steps: structuredClone(lad.steps) });
    dirty(); renderQuality();
  } });
  const steps = el("ol", { className: "ql-steps" }, ...lad.steps.map((st, i) => stepRow(lad, st, i)));
  return el("section", { className: "sx-card ql-card" },
    el("div", { className: "ql-head" }, el("span", { className: "sx-ico", ariaHidden: "true", innerHTML: '<svg viewBox="0 0 24 24"><path d="M4 20v-5M10 20V10M16 20V6M22 20V3"/></svg>' }),
      el("div", { className: "ql-title" }, name, say), el("div", { className: "ql-acts" }, copy, del)),
    steps,
    el("div", { className: "ql-foot" },
      el("button", { type: "button", className: "btn small", textContent: "Add a step", onclick: () => {
        lad.steps.push({ codec: "", range: "SDR", min: 720, max: 720 }); dirty(); renderQuality();
      } }),
      el("small", { className: "muted", textContent: "Tried from the top: the first step the episode has a track for is downloaded, its best track there." })));
}

function stepRow(lad, st, i) {
  const n = lad.steps.length;
  const sel = (choices, key) => {
    const s = el("select", { ariaLabel: key === "codec" ? "Codec" : "Range", onchange: (e) => { st[key] = e.target.value; dirty(); renderQuality(); } },
      ...choices.map(([v, label]) => el("option", { value: v, textContent: label })));
    s.value = st[key] || "";
    return s;
  };
  const why = el("small", { className: "ql-why" });
  const height = (key, label) => el("input", { type: "number", min: 0, max: 10000, step: 1, inputMode: "numeric", ariaLabel: label,
    placeholder: key === "max" ? "any" : "0", value: st[key] || "", oninput: (e) => {
      st[key] = Math.max(0, parseInt(e.target.value, 10) || 0);
      const bad = st.max && st.min > st.max;
      row.classList.toggle("bad", !!bad);
      why.textContent = bad ? "The lowest height is above the highest" : "";
      dirty();
    } });
  const move = (to, label, path) => el("button", { type: "button", className: "ql-icon", ariaLabel: label, disabled: to < 0 || to >= n,
    innerHTML: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`, onclick: () => {
      lad.steps.splice(to, 0, ...lad.steps.splice(i, 1)); dirty(); renderQuality();
    } });
  const row = el("li", { className: "ql-step" },
    el("span", { className: "ql-n", textContent: String(i + 1) }),
    sel(CODECS, "codec"), sel(RANGES, "range"),
    el("span", { className: "ql-h" }, height("min", "Lowest height"), el("span", { textContent: "to" }), height("max", "Highest height"), el("span", { textContent: "p" }),
      el("span", { className: "ql-btns" }, move(i - 1, "Move up", "m6 15 6-6 6 6"), move(i + 1, "Move down", "m6 9 6 6 6-6"),
      el("button", { type: "button", className: "ql-icon danger", ariaLabel: "Remove this step", disabled: n === 1,
        innerHTML: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>', onclick: () => { lad.steps.splice(i, 1); dirty(); renderQuality(); } }))),
    why);
  return row;
}
$("#ql-add").onclick = () => {
  const list = ladders();
  let n = list.length + 1;
  while (list.some((o) => o.name === `Ladder ${n}`)) n++;
  list.push({ name: `Ladder ${n}`, steps: [{ codec: "AVC", range: "SDR", min: 1080, max: 1080 }] });
  dirty(); renderQuality();
  $("#ql-list .ql-card:last-child .ql-name")?.select();
};
$("#ql-reset").onclick = (e) => {
  if (e.target.dataset.sure !== "1") { e.target.dataset.sure = "1"; e.target.textContent = "Sure? Your own ladders go"; return; }
  delete e.target.dataset.sure;
  e.target.textContent = "Reset to the built-in ladders";
  const keep = new Set(S.builtinLadders.map((l) => l.name));
  for (const l of ladders()) if (!keep.has(l.name)) renameRefs("ladder", l.name, "");
  S.config.quality_ladders = structuredClone(S.builtinLadders);
  dirty(); renderQuality(); renderServiceDefaults();
};

/* Settings, Unshackle: the other servers, each with its own address, key and downloads folder. */
const URL_OK = /^https?:\/\/[^\s/@]+(\/\S*)?$/;
function renderBackends() {
  const list = (S.config.settings.backends ??= []);
  $("#ub-list").replaceChildren(...(list.length ? list.map(backendBlock)
    : [el("p", { className: "dx-empty", textContent: "None: every service downloads with the Unshackle above." })]));
}
function backendBlock(b) {
  const list = S.config.settings.backends;
  const state = el("span", { className: "ub-state" });
  const check = (input, why) => {
    input.classList.toggle("bad", !!why);
    input.nextElementSibling.classList.toggle("bad", !!why);
    input.nextElementSibling.textContent = why || input.dataset.help;
  };
  const text = (key, opts, help, validate) => {
    const input = el("input", { type: "text", spellcheck: false, value: b[key] || "", dataset: { help }, ...opts, oninput: (e) => {
      const v = e.target.value.trim(), why = validate ? validate(v) : "";
      check(e.target, why);
      if (key === "name" && !why) { renameRefs("backend", b.name, v); head.textContent = v; }
      if (!why) b[key] = v;
      dirty();
    }, onchange: () => { if (key === "name") renderServiceDefaults(); } });
    return input;
  };
  const name = text("name", {}, "How Per service names it: vpn, home.", (v) => !v ? "Give it a name"
    : list.some((o) => o !== b && o.name === v) ? "Another server has this name" : !/^\w[\w .-]{0,39}$/.test(v) ? "Letters, digits, spaces, dots and dashes" : "");
  const url = text("url", { type: "url", placeholder: "http://unshackle-vpn:8786" }, "Where Unshacklarr reaches it.",
    (v) => URL_OK.test(v) ? "" : "An address starting with http:// or https://, without a user name");
  const key = el("input", { type: "password", autocomplete: "off",
    placeholder: b.api_key_set ? "Type a new key to replace it" : "", oninput: (e) => { b.api_key = e.target.value.trim() || undefined; dirty(); } });
  const dl = text("downloads", { placeholder: "Same path" }, "The downloads folder, as this server sees it. Empty: the same path.",
    (v) => !v || /^(\/|[A-Za-z]:[\\/])/.test(v) && !/(^|[\\/])\.\.([\\/]|$)/.test(v) ? "" : "A full path, from / (or a drive letter), without ..");
  const head = el("b", { textContent: b.name || "New server" });
  const test = el("button", { type: "button", className: "btn small", textContent: "Test", onclick: async () => {
    if (!URL_OK.test(b.url || "")) return check(url, "An address starting with http:// or https://, without a user name");
    state.className = "ub-state"; state.textContent = "Testing…";
    try {
      const { services } = await api("/api/unshackle/test", { method: "POST", body: JSON.stringify({ unshackle_mode: "remote", unshackle_url: b.url, unshackle_api_key: b.api_key || "" }) });
      state.className = "ub-state ok"; state.textContent = services === 1 ? "✓ Reached: 1 service" : `✓ Reached: ${services} services`;
    } catch (err) { state.className = "ub-state bad"; state.textContent = err.message; }
  } });
  const remove = el("button", { type: "button", className: "btn small danger", textContent: "Remove", onclick: (e) => {
    if (e.target.dataset.sure !== "1") { e.target.dataset.sure = "1"; e.target.textContent = "Sure?"; return; }
    list.splice(list.indexOf(b), 1);
    renameRefs("backend", b.name, "");
    dirty(); renderBackends(); renderServiceDefaults();
  } });
  const withHelp = (label, input, saved) => el("div", { className: "field" },
    el("label", { htmlFor: input.id ||= `ub-${++fieldIds}` }, label, ...(saved ? [" ", el("span", { className: "sx-saved", textContent: "✓ Saved" })] : [])),
    input, el("small", { textContent: input.dataset.help || "The serve: api_secret of its unshackle.yaml. Empty keeps the saved one." }));
  return el("div", { className: "svc-block ub-block" },
    el("div", { className: "svc-block-head" }, head, el("span", { className: "ub-acts" }, state, test, remove)),
    el("div", { className: "sx-grid" }, withHelp("Name", name), withHelp("Address of unshackle serve", url),
      withHelp("API key", key, b.api_key_set), withHelp("Downloads folder, as it sees it", dl)));
}
$("#ub-add").onclick = () => {
  const list = (S.config.settings.backends ??= []);
  let n = list.length + 1;
  while (list.some((o) => o.name === `server${n}`)) n++;
  list.push({ name: `server${n}`, url: "", downloads: "" });
  dirty(); renderBackends(); renderServiceDefaults();
  $("#ub-list .ub-block:last-child input[type=url]")?.focus();
};

/* Settings, Automation: Sonarr imports each download, or it waits for an import by hand. */
function renderImportMode() {
  if (!S.config?.settings) return;
  const only = S.config.settings.download_only === true;
  $("#ax-import").querySelectorAll("button").forEach((b) => b.setAttribute("aria-checked", String((b.dataset.only === "true") === only)));
  const other = Object.values(S.config.series || {}).filter((c) => c.service && typeof c.download_only === "boolean" && c.download_only !== only).length;
  $("#ax-import-say").replaceChildren(el("span", { textContent: only
    ? "Each episode waits in Activity, Waiting in downloads, until you import or delete it; never deleted on its own."
    : "Sonarr imports each episode once it is downloaded and checked." }),
    ...(other ? [" ", el("span", { textContent: other === 1 ? "1 series says otherwise, on its page." : `${other} series say otherwise, on their page.` })] : []));
}
$("#ax-import").querySelectorAll("button").forEach((b) => b.onclick = () => {
  S.config.settings.download_only = b.dataset.only === "true";
  dirty(); renderImportMode();
});

/* A series' page: its ladder and what follows its downloads, Default being its service's, then the settings'. */
function fillQualityImport(conf) {
  const set = S.config.settings, svc = S.config.service_defaults?.[conf.service] || {};
  $("#d-ladder").replaceWith(Object.assign(ladderSelect(conf.ladder, svc.ladder || set.quality_ladder, (v) => {
    if (v) conf.ladder = v; else delete conf.ladder;
    dirty();
  }), { id: "d-ladder" }));
  $("#d-import").options[0].textContent = `Default: ${set.download_only ? "Download only" : "Sonarr imports it"}`;
  $("#d-import").value = conf.download_only === true ? "only" : conf.download_only === false ? "import" : "";
}
$("#d-import").onchange = (e) => {
  const conf = S.config.series[current.tvdbId];
  if (e.target.value) conf.download_only = e.target.value === "only"; else delete conf.download_only;
  dirty();
};
