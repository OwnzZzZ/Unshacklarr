"""Real series, as Muts' Sonarr and their services had them on 2026-10-13: the service's listing, Sonarr's episodes and
the titles TMDB and TheTVDB (SkyHook) gave, with how each Sonarr episode is found on the service. Every title-matching
rule meets them all: a new rule that breaks one of them fails here first. US series mostly, Koh-Lanta and a French one.
To add one: capture its inputs the same way (tools/corpus-capture.py, inside a running Unshacklarr) and check the result by hand."""
import json
from pathlib import Path

import pytest

CASES = sorted((Path(__file__).parent / "corpus").glob("*.json"))


def probe(case, monkeypatch):
    from unshacklarr import web
    sync, inputs = web.sonarr_sync, case["inputs"]
    monkeypatch.setitem(sync.SETTINGS, "country", case["country"])
    monkeypatch.setattr(web, "list_titles", lambda show: inputs["service"])
    monkeypatch.setattr(sync, "sonarr_get", lambda path, **p: inputs["episodes"] if path == "episode" else inputs.get("series") or {})
    monkeypatch.setattr(web, "tmdb_key", lambda: "key" if case["tmdb_key"] else "")
    monkeypatch.setattr(web, "tmdb_episodes", lambda tmdb_id: inputs.get("tmdb") or [])
    monkeypatch.setattr(web, "tvdb_titles", lambda tvdb_id: inputs.get("tvdb") or {})
    show = {**case["show"], **({"season_map": {int(k): v for k, v in case["show"]["season_map"].items()}} if case["show"].get("season_map") else {})}
    return web.probe_series(show, 1, case["title"])  # season_map's keys are numbers, as in config.yaml


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_each_episode_is_found_where_it_is_on_the_service(path, monkeypatch):
    case = json.loads(path.read_text())
    if case.get("known_bug_available"):
        pytest.xfail(case["known_bug_available"])
    found = probe(case, monkeypatch)
    # where each episode is asked on the service: what a download does with it (how it was found may say more or less)
    got = {str(k): v["service"] for k, v in found["available"].items()}
    expect = {k: v["service"] for k, v in case["expect"]["available"].items()}
    wrong = {k: (got.get(k), expect.get(k)) for k in set(got) | set(expect) if got.get(k) != expect.get(k)}
    assert not wrong, f"{case['title']}: {len(wrong)} episodes found elsewhere than before (now, before): {dict(list(wrong.items())[:5])}"
    if "titlesAgree" in found:  # said by the page's check since Sonarr 5's titles in your language
        assert found["titlesAgree"] == case["expect"]["titlesAgree"]


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_no_season_map_is_offered_that_the_episodes_dont_need(path, monkeypatch):
    case = json.loads(path.read_text())
    if case.get("known_bug"):
        pytest.xfail(case["known_bug"])
    assert probe(case, monkeypatch)["season_map"] == case["expect"]["season_map"]
