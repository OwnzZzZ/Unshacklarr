/* unshackle.yaml, a page of its own: opened with the password again, checked and kept by the server on save.
   CodeMirror colours the YAML and suggests Unshackle's keys (from its configuration reference), service tags,
   device names and values; the page forgets the password and the file once left. */
const uy = { password: "", base: "", original: "", view: null, keys: null, devices: [] };
let CM = null;
const uyText = () => uy.view ? uy.view.state.doc.toString() : "";
const uyDirty = () => !!uy.view && uyText() !== uy.original;
function uyReset() {
  uy.view?.destroy();
  Object.assign(uy, { password: "", base: "", original: "", view: null });
  $("#uy-auth").hidden = false; $("#uy-editor").hidden = $("#uy-tools").hidden = $("#uy-foot").hidden = true;
  $("#uy-password").value = ""; $("#uy-path").textContent = "";
}
$("#uy-open").onclick = () => { uyReset(); showTab("yaml"); $("#uy-password").focus(); };
$("#uy-back").onclick = () => { showTab("settings", true); navigate(true); };
window.addEventListener("beforeunload", (e) => { if (currentTab === "yaml" && uyDirty()) e.preventDefault(); });
function uyError(text) { $("#uy-error").textContent = text || ""; }
function uyVersions(list) {
  $("#uy-versions").replaceChildren(el("option", { value: "", textContent: list.length ? "Earlier versions…" : "No earlier version yet" }),
    ...list.map((v) => el("option", { value: v.id, textContent: `${new Date(v.at).toLocaleString(LOCALE)} · ${Math.round(v.size / 1024)} KB` })));
}
function uySetText(text) { uy.view.dispatch({ changes: { from: 0, to: uy.view.state.doc.length, insert: text } }); }

/* The keys above the cursor's line, outermost first: ["cdm"], ["services", "NF"]… */
function uyPath(doc, lineNo, indent) {
  const path = [];
  for (let n = lineNo - 1; n >= 1 && indent > 0; n--) {
    const m = /^(\s*)(?:- )?([^\s#:][^:#]*?):(\s|$)/.exec(doc.line(n).text);
    if (m && m[1].length < indent) { path.unshift(m[2].trim()); indent = m[1].length; }
  }
  return path;
}
const SERVICE_KEYED = new Set(["cdm", "credentials", "decryption", "services", "headers"]);
function uyComplete(ctx) {
  const line = ctx.state.doc.lineAt(ctx.pos), before = line.text.slice(0, ctx.pos - line.from);
  if (/#/.test(before)) return null;
  const keyAt = /^(\s*)(- )?([\w.-]*)$/.exec(before);
  if (keyAt) {  // a key
    const indent = keyAt[1].length + (keyAt[2] ? 2 : 0), path = uyPath(ctx.state.doc, line.number, indent);
    if (!keyAt[3] && !ctx.explicit) return null;
    const keys = uy.keys || {};
    let found = [];
    if (!path.length) found = Object.entries(keys).map(([k, v]) => ({ label: k, detail: v.type, info: v.info }));
    else if (path.length === 1) {
      found = Object.entries(keys[path[0]]?.keys || {}).map(([k, v]) => ({ label: k, detail: v.type, info: v.info }));
      if (SERVICE_KEYED.has(path[0])) found.push(...S.services.map((t) => ({ label: t, detail: "service" })));
      if (path[0] === "cdm") found.push({ label: "default", detail: "every other service" });
    }
    if (!found.length) return null;
    return { from: ctx.pos - keyAt[3].length, validFor: /^[\w.-]*$/,
      options: found.map((o) => ({ ...o, type: o.detail === "service" ? "constant" : "property", apply: `${o.label}: ` })) };
  }
  const valueAt = /^(\s*)(?:- )?([\w.-]+):\s+([\w.-]*)$/.exec(before);
  if (!valueAt) return null;
  const [, sp, key, word] = valueAt, path = [...uyPath(ctx.state.doc, line.number, sp.length), key];
  const remoteCdm = [...ctx.state.doc.toString().matchAll(/^\s*-?\s*name:\s*["']?([\w.-]+)/gm)].map((m) => m[1]);
  let values = [];
  if (path[0] === "cdm" && path.length === 2) values = [...uy.devices, ...remoteCdm].map((v) => ({ label: v, detail: "CDM" }));
  else if (path[0] === "decryption") values = [{ label: "shaka", detail: "Shaka Packager" }, { label: "mp4decrypt", detail: "Bento4" }];
  else if (/^bool/.test((path.length === 1 ? uy.keys?.[key] : uy.keys?.[path[0]]?.keys?.[key])?.type || "")) values = ["true", "false"].map((v) => ({ label: v }));
  if (!values.length || (!word && !ctx.explicit)) return values.length && ctx.explicit ? { from: ctx.pos, options: values } : null;
  return { from: ctx.pos - word.length, validFor: /^[\w.-]*$/, options: values.map((v) => ({ ...v, type: "enum" })) };
}
async function uyEditor(text) {
  CM ||= await import("/codemirror.js?v=6.0.2");
  uy.keys ||= await fetch("/unshackle-keys.json?v=5.5.0").then((r) => r.json()).catch(() => ({}));
  api("/api/cdm").then((d) => { uy.devices = d.devices.map((x) => x.name); }).catch(() => {});
  const colours = CM.HighlightStyle.define([
    { tag: [CM.tags.propertyName, CM.tags.definition(CM.tags.propertyName)], color: "var(--accent)" },
    { tag: [CM.tags.string, CM.tags.special(CM.tags.string)], color: "#9fd18b" },
    { tag: [CM.tags.content, CM.tags.number, CM.tags.bool, CM.tags.null], color: "var(--text)" },
    { tag: [CM.tags.comment, CM.tags.lineComment], color: "var(--muted)", fontStyle: "italic" },
    { tag: [CM.tags.labelName, CM.tags.typeName, CM.tags.keyword, CM.tags.meta], color: "var(--warn)" },
    { tag: [CM.tags.punctuation, CM.tags.separator, CM.tags.squareBracket, CM.tags.brace], color: "#8b95a8" },
  ]);
  const theme = CM.EditorView.theme({
    "&": { color: "var(--text)", backgroundColor: "var(--bg)", fontSize: "13px" },
    ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", lineHeight: "1.55" },
    ".cm-content": { caretColor: "var(--accent)" }, ".cm-cursor": { borderLeftColor: "var(--accent)" },
    ".cm-gutters": { backgroundColor: "var(--panel)", color: "#6b778a", border: "none", borderRight: "1px solid var(--line)" },
    ".cm-activeLine": { backgroundColor: "#ffffff08" }, ".cm-activeLineGutter": { backgroundColor: "#ffffff0d", color: "var(--text)" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "#4fb3bf40" },
    ".cm-selectionMatch": { backgroundColor: "#4fb3bf22" }, ".cm-matchingBracket": { backgroundColor: "#4fb3bf33", outline: "none" },
    ".cm-foldPlaceholder": { backgroundColor: "var(--panel)", border: "1px solid var(--line)", color: "var(--muted)" },
    ".cm-tooltip": { backgroundColor: "var(--panel)", border: "1px solid var(--line)", borderRadius: "8px", color: "var(--text)" },
    ".cm-tooltip-autocomplete > ul": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", maxHeight: "16em" },
    ".cm-tooltip-autocomplete > ul > li": { padding: "3px 8px" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "var(--accent)", color: "var(--accent-ink)" },
    ".cm-completionDetail": { color: "var(--muted)", fontStyle: "normal", marginLeft: "1em" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected] .cm-completionDetail": { color: "inherit", opacity: ".7" },
    ".cm-completionInfo": { maxWidth: "340px", padding: "8px 10px", fontFamily: "Figtree, system-ui, sans-serif", lineHeight: "1.45" },
    ".cm-panels": { backgroundColor: "var(--panel)", color: "var(--text)", borderColor: "var(--line)" },
    ".cm-panel input, .cm-panel button": { color: "var(--text)" },
    ".cm-searchMatch": { backgroundColor: "#e6b94a40" }, ".cm-searchMatch-selected": { backgroundColor: "#e6b94a80" },
  }, { dark: true });
  const where = (state) => {
    const head = state.selection.main.head, line = state.doc.lineAt(head);
    $("#uy-where").textContent = `Line ${line.number}, column ${head - line.from + 1}${state.doc.toString() !== uy.original ? " · Unsaved changes" : ""}`;
  };
  const yaml = CM.yaml();
  uy.view?.destroy();
  uy.view = new CM.EditorView({
    parent: $("#uy-cm"),
    state: CM.EditorState.create({ doc: text, extensions: [
      CM.basicSetup, CM.keymap.of([CM.indentWithTab]), yaml, yaml.language.data.of({ autocomplete: uyComplete }),
      CM.syntaxHighlighting(colours), theme, CM.EditorState.tabSize.of(2),
      CM.EditorView.contentAttributes.of({ "aria-label": "unshackle.yaml", spellcheck: "false", autocapitalize: "off", autocorrect: "off" }),
      CM.EditorView.updateListener.of((u) => { if (u.docChanged || u.selectionSet) where(u.state); }),
    ] }),
  });
  where(uy.view.state);
}
$("#uy-auth").onsubmit = async (e) => {
  e.preventDefault();
  const password = $("#uy-password").value;
  try {
    const r = await api("/api/unshackle/config-file/open", { method: "POST", body: JSON.stringify({ password }) });
    Object.assign(uy, { password, base: r.base, original: r.text });
    $("#uy-path").textContent = r.path;
    uyVersions(r.versions);
    $("#uy-auth").hidden = true; $("#uy-editor").hidden = $("#uy-tools").hidden = $("#uy-foot").hidden = false;
    $("#uy-diff").hidden = true; $("#uy-cm").hidden = false; $("#uy-show").textContent = "Show changes";
    uyError("");
    await uyEditor(r.text);
    uy.view.focus();
  } catch (err) { toast(err.message, true); }
};
/* The lines added and removed, with two lines around each change (a line diff by longest common subsequence). */
function lineDiff(a, b) {
  const x = a.split("\n"), y = b.split("\n"), n = x.length, m = y.length;
  const lcs = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) lcs[i][j] = x[i] === y[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && x[i] === y[j]) { out.push([" ", x[i], j + 1]); i++; j++; }
    else if (j < m && (i >= n || lcs[i][j + 1] >= lcs[i + 1][j])) { out.push(["+", y[j], j + 1]); j++; }
    else { out.push(["-", x[i], null]); i++; }
  }
  const near = new Set();
  out.forEach((l, k) => { if (l[0] !== " ") for (let d = -2; d <= 2; d++) near.add(k + d); });
  const lines = [];
  out.forEach((l, k) => {
    if (!near.has(k)) { if (lines.at(-1)?.gap !== true) lines.push({ gap: true }); return; }
    lines.push(l);
  });
  return lines;
}
$("#uy-show").onclick = () => {
  const showing = !$("#uy-diff").hidden;
  if (showing) { $("#uy-diff").hidden = true; $("#uy-cm").hidden = false; $("#uy-show").textContent = "Show changes"; uy.view.focus(); return; }
  const lines = lineDiff(uy.original, uyText());
  const changed = lines.some((l) => !l.gap && l[0] !== " ");
  $("#uy-diff").replaceChildren(...(changed ? lines.map((l) => (l.gap ? el("div", { className: "gap", textContent: "⋯" })
    : el("div", { className: l[0] === "+" ? "add" : l[0] === "-" ? "del" : "", textContent: `${l[0]} ${l[1]}` })))
    : [el("div", { className: "gap", textContent: "No change." })]));
  $("#uy-diff").hidden = false; $("#uy-cm").hidden = true; $("#uy-show").textContent = "Back to the file";
};
$("#uy-versions").onchange = async () => {
  const id = $("#uy-versions").value;
  if (!id) return;
  try {
    const r = await api("/api/unshackle/config-file/version", { method: "POST", body: JSON.stringify({ password: uy.password, id }) });
    uySetText(r.text);
    if (!$("#uy-diff").hidden) $("#uy-show").click();
    toast("That version is in the editor: Save to restore it");
  } catch (err) { toast(err.message, true); }
  $("#uy-versions").value = "";
};
$("#uy-save").onclick = async () => {
  const button = $("#uy-save");
  button.disabled = true;
  try {
    const text = uyText();
    const r = await api("/api/unshackle/config-file/save", { method: "POST", body: JSON.stringify({ password: uy.password, text, base: uy.base }) });
    uyError("");
    uy.base = r.base; uy.original = text;
    uy.view.dispatch({ selection: uy.view.state.selection });  // redraws "Unsaved changes" away
    uyVersions(r.versions);
    if (!$("#uy-diff").hidden) $("#uy-show").click();
    toast(!r.saved ? "No change to save" : r.restart ? "Saved. The serve section changed: restart Unshackle for it to apply"
      : "Saved: the next download uses it");
  } catch (err) { uyError(err.message); }
  button.disabled = false;
};

