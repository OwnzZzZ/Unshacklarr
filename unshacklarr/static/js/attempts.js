/* An episode checked before it came out: how often, since when; each check's output is in Activity. */
function checksNote(c) {
  if (!(c.checks > 1)) return "";
  const since = `since ${day(new Date(c.first_check))} ${time(new Date(c.first_check))}`;
  const before = c.checks - 1;
  return c.outcome === "unavailable" ? `Checked ${c.checks} times ${since}. Each check is in the output below.`
    : `Not out yet at the ${before} earlier check${before > 1 ? "s" : ""} ${since}. Each check is in the output below.`;
}
/* Kept: the download waits in the downloads folder; Sonarr takes it in place of its own file. */
async function importKept(c, button) {
  const worded = !button.classList.contains("jact");  // a card's icon button keeps its icon
  button.disabled = true;
  if (worded) button.textContent = "Importing…";
  try {
    await api("/api/leftovers/import", { method: "POST", signal: AbortSignal.timeout(360000), body: JSON.stringify({ folder: `unshackle-${c.tvdbId}-${c.sxxeyy}` }) });
    toast(`${c.series} ${c.sxxeyy}: imported by Sonarr`);
    openActivity();
  } catch (e) { button.disabled = false; if (worded) button.textContent = "Import anyway"; toast(e.message, true); }
}
async function deleteRun(c, button) {
  button.disabled = true;
  try {
    await api(`/api/runs/${encodeURIComponent(c.id)}`, { method: "DELETE" });
    toast(`${c.series} ${c.sxxeyy}: deleted from the history`);
    picked_run = c.batch ? `job:${c.batch}` : null;  // from a job's card: the job stays in view
    if (!c.batch) $("#con").classList.remove("open");
    openActivity();
  } catch (e) { button.disabled = false; toast(e.message, true); }
}
$("#hist-clear").onclick = () => {
  const done = runCards.filter((c) => !isLive(c));
  const failedN = done.filter((c) => c.outcome === "failed").length;
  if (!done.length) return toast("The history is already empty");
  $("#clear-failed").textContent = `Clear failed (${failedN})`;
  $("#clear-failed").disabled = !failedN;
  $("#clear-all").textContent = `Clear all (${done.length})`;
  const dialog = $("#clear-dialog");
  dialog.returnValue = "cancel";
  dialog.showModal();
  dialog.addEventListener("close", async () => {
    const what = dialog.returnValue;
    if (what !== "failed" && what !== "all") return;
    try {
      const { deleted } = await api("/api/runs/clear", { method: "POST", body: JSON.stringify({ what }) });
      toast(`${deleted} download${deleted === 1 ? "" : "s"} cleared from the history`);
      picked_run = null;
      openActivity();
    } catch (e) { toast(e.message, true); }
  }, { once: true });
};
/* A job's failed episodes, again, in one go: in the same job. */
// Another Sonarr's copy is not retried from here yet: its episode ids are its own, the main Sonarr would get another one
const otherSonarr = (cards) => cards.some((c) => c.instance) && (toast("This copy is for another Sonarr: the next sync tries it again.", true), true);
async function retryJob(j, cards, button) {
  if (otherSonarr(cards)) return;
  button.disabled = true;
  try {
    await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: cards.map((c) => c.episodeId), retry: true, batch: j.job }) });
    toast(`${cards.length} episode${cards.length > 1 ? "s" : ""} downloading again`);
    picked_run = `job:${j.job}`;
    setTimeout(openActivity, 1500);
  } catch (e) { button.disabled = false; toast(e.message, true); }
}
async function retryRun(c, button) {
  if (otherSonarr([c])) return;
  button.disabled = true;
  try {
    await api("/api/download", { method: "POST", body: JSON.stringify({ episodeIds: [c.episodeId], retry: true, ...(c.batch ? { batch: c.batch } : {}) }) });
    toast(`${c.series} ${c.sxxeyy}: downloading again`);
    picked_run = c.batch ? `job:${c.batch}` : null;  // a job's episode: the job stays in view
    setTimeout(openActivity, 1500);
  } catch (e) { button.disabled = false; toast(e.message, true); }
}

function openTerm() {
  if (!window.Terminal) { $("#log-state").textContent = "The terminal could not load: reload the page."; return false; }
  if (!term) {
    term = new Terminal({
      convertEol: true, disableStdin: true, scrollback: 20000, fontSize: 13, cursorBlink: false,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      theme: { background: "#0d1219", foreground: "#e7eaf0", cursor: "#0d1219", selectionBackground: "#2c3649" },
    });
    termFit = new FitAddon.FitAddon();
    term.loadAddon(termFit);
    term.open($("#term"));
    new ResizeObserver(fitTerminal).observe($("#term"));
  }
  fitTerminal();
  term.reset();
  termSocket?.close();
  return true;
}
function streamRun(c) { streamTerm(c.id, `run=${encodeURIComponent(c.id)}`); }
function streamJob(batch) { streamTerm(`job:${batch}`, `batch=${encodeURIComponent(batch)}`); }  // every episode's output, one after the other
function streamTerm(key, query) {
  if (!openTerm()) return;
  termFor = key;
  const filter = termFilter();
  termSocket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/terminal?${query}`);
  termSocket.binaryType = "arraybuffer";
  termSocket.onmessage = (m) => term.write(filter(new Uint8Array(m.data)));
}
function fitTerminal() {
  const dims = termFit?.proposeDimensions();  // NaN or fractional while its tab is hidden
  if (dims && Number.isFinite(dims.rows) && dims.rows > 0) {
    term.resize(Math.max(Math.floor(dims.cols) || 0, TERM_COLS), Math.floor(dims.rows));
  }
}
$("#sync").onclick = async () => {
  if (!$("#savebar").hidden) return toast("Save your changes first: the sync reads the saved settings.", true);
  try { await api("/api/sync", { method: "POST" }); showTab("log"); }
  catch (e) { toast(e.message, true); }
};

