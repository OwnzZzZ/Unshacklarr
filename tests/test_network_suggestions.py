"""The series' own network catch-up service is offered beside TMDB's links, not only when TMDB has none."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

CORE = Path(__file__).parents[1] / "unshacklarr" / "static" / "js" / "core.js"

# Runs the page's real loadSuggestions against a minimal fake DOM and prints the buttons it rendered.
HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[1], "utf8");
const grab = (name) => src.match(new RegExp(`(?:async )?function ${name}\\b[\\s\\S]*?\\n}\\n`))[0];
const scenario = JSON.parse(process.argv[2]);
const node = (tag, props, kids) => ({ tag, ...props, kids, dataset: {}, hidden: false, textContent: props.textContent ?? "" });
const series = { tvdbId: 1, tmdbId: scenario.tmdbId, title: "Highway Patrol", network: "Seven Network" };
const box = { kids: [], replaceChildren(...k) { this.kids = k; }, querySelectorAll: () => [], append(...k) { this.kids.push(...k); } };
const stub = { textContent: "", hidden: false, dataset: {}, value: "", dispatchEvent() {}, click() {} };
const ctx = {
  el: (tag, props = {}, ...kids) => node(tag, props, kids),
  $: (sel) => (sel === "#d-suggest" ? box : stub),
  Event: class {},
  loading: (t) => ({ loading: t }),
  failed: (t) => ({ failed: t }),
  ago: () => "now",
  svcName: (t) => t,
  S: { networkServices: scenario.networkServices, config: { tmdb_countries: ["AU"] } },
  api: async () => ({ links: scenario.links, checked: "x" }),
  current: series,
};
vm.createContext(ctx);
vm.runInContext(`${grab("networkButton")}\n${grab("networkPick")}\n${grab("loadSuggestions")}`, ctx);
ctx.loadSuggestions(series).then(() => {
  const label = (k) => k.tag === "button" ? k.kids[0].textContent : k.textContent ?? "";
  console.log(JSON.stringify(box.kids.map((k) => ({ tag: k.tag, text: label(k), aria: k.ariaLabel ?? null }))));
});
"""


def render(links, network_services, tmdb_id: int | None = 1):
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    scenario = {"links": links, "networkServices": network_services, "tmdbId": tmdb_id}
    out = subprocess.run(["node", "-e", HARNESS, "--", str(CORE), json.dumps(scenario)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def link(service):
    return {"service": service, "url": f"https://{service}.example/x", "country": "AU"}


def buttons(rendered):
    return [k["text"] for k in rendered if k["tag"] == "button"]


def test_network_service_is_offered_after_the_tmdb_links():
    assert buttons(render([link("BNGE")], {"1": "SEVEN"})) == ["BNGE", "SEVEN"]


def test_network_service_is_not_repeated_when_tmdb_already_has_it():
    assert buttons(render([link("SEVEN"), link("BNGE")], {"1": "SEVEN"})) == ["SEVEN", "BNGE"]


def test_no_network_button_when_its_service_is_not_installed():
    assert buttons(render([link("BNGE")], {})) == ["BNGE"]


def test_network_service_alone_when_tmdb_lists_none():
    assert buttons(render([], {"1": "TEN"})) == ["TEN"]


def test_network_service_without_a_tmdb_id():
    assert buttons(render([], {"1": "TEN"}, tmdb_id=None)) == ["TEN"]
