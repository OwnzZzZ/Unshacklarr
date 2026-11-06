/* Several series changed at once: Select, then pick them on the wall, then Edit. Only the fields filled in change,
   in the page's settings: Save keeps them, as for one series. */
const selBar = el("div", { className: "sel-bar", id: "sel-bar", hidden: true },
  el("span", { id: "sel-count" }),
  el("button", { type: "button", className: "btn small", textContent: "All shown", onclick: () => {
    document.querySelectorAll("#wall .poster").forEach((c) => SEL.ids.add(Number(c.dataset.tvdb)));
    renderWall(); renderSel();
  } }),
  el("button", { type: "button", className: "btn small", textContent: "Clear", onclick: () => { SEL.ids.clear(); renderWall(); renderSel(); } }),
  el("button", { type: "button", className: "btn small primary", id: "sel-edit", textContent: "Edit…", onclick: () => openSel() }));
$("#wall").after(selBar);
function renderSel() {
  $("#sel-toggle").setAttribute("aria-pressed", String(SEL.on));
  $("#sel-toggle").textContent = SEL.on ? "Done" : "Select";
  selBar.hidden = !SEL.on;
  $("#wall").classList.toggle("selecting", SEL.on);
  $("#sel-count").textContent = `${SEL.ids.size} picked`;
  $("#sel-edit").disabled = !SEL.ids.size;
}
function pickSeries(tvdb) {
  SEL.ids.has(tvdb) ? SEL.ids.delete(tvdb) : SEL.ids.add(tvdb);
  renderWall();
  renderSel();
}
$("#sel-toggle").onclick = () => {
  SEL.on = !SEL.on;
  if (!SEL.on) SEL.ids.clear();
  renderWall();
  renderSel();
};
let selOptions = {};
function openSel() {
  $("#sel-title").textContent = `Change ${SEL.ids.size} series`;
  $("#sel-service").replaceChildren(el("option", { value: "", textContent: "Unchanged" }), ...S.services.map((t) => el("option", { value: t, textContent: svcName(t) })));
  $("#sel-day").replaceChildren(el("option", { value: "", textContent: "Unchanged" }), ...RELEASE_DAYS.map((d) => el("option", { value: String(d), textContent: releaseDayLabel(d) })));
  for (const id of ["#sel-release", "#sel-audio", "#sel-prefer", "#sel-subs", "#sel-fallback"]) $(id).value = "";
  selOptions = {};
  optionsEditor($("#sel-options"), S.dlOptions, selOptions);
  sayNoService();
  $("#sel-dialog").showModal();
}
/* A series without a service is not saved: said, with how many, until a service is picked here. */
function sayNoService() {
  const none = [...SEL.ids].filter((id) => !S.config.series[id]?.service).length;
  $("#sel-noservice").hidden = !none || Boolean($("#sel-service").value);
  $("#sel-noservice").textContent = none === 1 ? "One of them has no service: pick one above, or its changes are not kept."
    : `${none} of them have no service: pick one above, or their changes are not kept.`;
}
$("#sel-service").onchange = sayNoService;
$("#sel-form").onsubmit = (e) => {
  if (e.submitter?.value !== "apply") return;
  const service = $("#sel-service").value, time = $("#sel-release").value, day = $("#sel-day").value;
  const texts = { audio_accept: "#sel-audio", audio_prefer: "#sel-prefer", subs_accept: "#sel-subs", fallback_profiles: "#sel-fallback" };
  const options = Object.fromEntries(Object.entries(selOptions).filter(([, v]) => v !== "" && v !== undefined));
  for (const id of SEL.ids) {
    const conf = (S.config.series[id] ??= { service: "", title: "", options: {}, service_options: {} });
    if (service && conf.service !== service) { conf.service = service; conf.service_options = {}; }
    if (time) conf.release_time = time;
    if (day !== "") conf.release_day = Number(day);
    for (const [key, sel] of Object.entries(texts)) if ($(sel).value.trim()) conf[key] = $(sel).value.trim();
    conf.options = { ...(conf.options || {}), ...options };
  }
  dirty();
  renderWall();
  toast(`${SEL.ids.size} series changed: save to keep them`);
};
renderSel();
