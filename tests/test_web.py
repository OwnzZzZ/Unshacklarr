import asyncio
import importlib
import json
import re
from types import SimpleNamespace

import pytest
from aiohttp import client_exceptions


def test_unreachable_is_notified_once_after_two_checks_then_back(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    sent = []
    monkeypatch.setattr(web.sonarr_sync, "notify", lambda settings, level, title, message: sent.append((level, title)))
    monkeypatch.setattr(web, "sonarr_health", lambda: {"ok": True})
    states = iter([{"ok": False, "error": "x"}] * 3 + [{"ok": True}, {"ok": True}])
    monkeypatch.setattr(web, "unshackle_health", lambda: next(states))

    web.check_health()
    assert sent == []  # one failed check could be a blip
    web.check_health()
    assert sent == [("error", "Unshackle is unreachable")]
    web.check_health()
    web.check_health()
    web.check_health()
    assert sent == [("error", "Unshackle is unreachable"), ("success", "Unshackle is back")]


def test_the_unshackle_test_needs_the_setup_code_before_the_setup(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    monkeypatch.setenv("SETUP_TOKEN", "code")
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    started = []
    monkeypatch.setattr(web, "check_unshackle", lambda settings: started.append(settings) or 3)

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            headers = {"X-Unshackle": "1"}
            body = {"unshackle_mode": "local", "unshackle_command": "/bin/anything"}
            r1 = await client.post("/api/unshackle/test", json=body, headers=headers)
            r2 = await client.post("/api/unshackle/test", json={**body, "setup_code": "code"}, headers=headers)
            return r1.status, r2.status

    assert asyncio.run(go()) == (403, 200)
    assert len(started) == 1  # nothing ran for the request without the code


def test_service_sites_come_from_url_and_help_and_survive_odd_urls(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    monkeypatch.setattr(web.UNSHACKLE, "services", lambda: [
        {"tag": "CRAVE", "url": "https://www.crave.ca", "help": "See https://[broken and https://help.crave.ca/x"},
        {"tag": "EXAMPLE", "url": "https://example.com"},
    ])
    domains = web.service_domains()
    assert domains["crave.ca"] == "CRAVE" and domains["help.crave.ca"] == "CRAVE"
    assert "example.com" not in domains
    assert web.service_for("https://www.crave.ca/fr/series/x", domains) == "CRAVE"


def test_a_lost_password_is_replaced_and_sessions_end(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "series": {1: {"service": "X", "title": "t"}},
                      "auth": {"password": web.hash_password("old-password"), "secret": "s1"}})
    try:
        web.reset_password("short")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    web.reset_password("new-password")
    config = web.read_config()
    assert web.password_ok("new-password", config["auth"]["password"])
    assert not web.password_ok("old-password", config["auth"]["password"])
    assert config["auth"]["secret"] != "s1"  # every session signed with the old secret is over
    assert config["series"] == {1: {"service": "X", "title": "t"}}  # the rest stays


def test_an_episode_file_is_summed_up_from_sonarr_media_info(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    f = {"size": 4093382561, "releaseGroup": "YGG", "quality": {"quality": {"name": "HDTV-1080p"}},
         "mediaInfo": {"audioBitrate": 0, "audioChannels": 2, "audioCodec": "AAC", "audioLanguages": "fre/eng/und",
                       "videoBitrate": 0, "videoCodec": "h264", "videoDynamicRangeType": "", "resolution": "1440x1080",
                       "runTime": "2:03:22", "subtitles": "fre"}}
    s = web.file_summary(f)
    assert (s["height"], s["video"], s["audio"], s["channels"]) == (1080, "H.264", "AAC", 2)
    assert s["audioLanguages"] == ["fr", "en"] and s["subtitles"] == ["fr"]
    assert s["videoBitrate"] is None and s["averageBitrate"] == round(4093382561 * 8 / 7402)  # 2:03:22
    assert web.seconds("43:10") == 2590 and web.seconds("") == 0
    assert [web.resolution_height(w, h) for w, h in [(1920, 960), (1920, 800), (1916, 1036), (1280, 536), (720, 576), (0, 0)]] \
        == [1080, 1080, 1080, 720, 576, None]  # the width says a wide picture's class


def test_maintenance_only_runs_serve_actions_and_jobs_are_checked(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    calls = []
    monkeypatch.setattr(web.UNSHACKLE, "call", lambda method, path, **kw: calls.append((method, path)) or {"cleared": True})
    monkeypatch.setattr(web.UNSHACKLE, "cancel", lambda job_id: calls.append(("DELETE", job_id)))
    web.app._middlewares = type(web.app._middlewares)([web.same_origin_only])  # logged in, for this test

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            h = {"X-Unshackle": "1"}
            return [(await client.post(path, headers=h)).status for path in (
                "/api/unshackle/maintenance/clear-temp", "/api/unshackle/maintenance/rm-rf",
                "/api/unshackle/jobs/0f1e2d3c-aaaa-bbbb/cancel", "/api/unshackle/jobs/..%2F..%2Fx/cancel")]

    assert asyncio.run(go()) == [200, 404, 200, 400]
    assert calls == [("POST", "/api/maintenance/clear-temp"), ("DELETE", "0f1e2d3c-aaaa-bbbb")]


def test_stats_add_up_the_history(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
    card = lambda days, service, outcome, secs=60: {"started": (now - timedelta(days=days)).isoformat(),
        "ended": (now - timedelta(days=days) + timedelta(seconds=secs)).isoformat(), "service": service, "outcome": outcome}
    cards = [card(1, "MLT", "downloaded", 100), card(2, "MLT", "failed"), card(3, "ATV", "downloaded", 200),
             card(40, "MLT", "downloaded"), card(1, "ATV", "unavailable"), {"started": now.isoformat(), "ended": None, "service": "ATV", "outcome": "running"}]
    s = web.stats_of(cards, now)
    assert s["month"] == {"downloaded": 2, "failed": 1, "kept": 0, "success": 67, "average": 150}
    mlt = next(v for v in s["services"] if v["service"] == "MLT")
    assert (mlt["downloaded"], mlt["failed"], mlt["success"]) == (2, 1, 67)
    assert len(s["weeks"]) == 8 and sum(w["downloaded"] for w in s["weeks"]) == 3  # the one 40 days ago is 6 weeks back


def test_the_page_script_parses(tmp_path):
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    static = Path(__file__).parents[1] / "unshacklarr" / "static"
    page = (static / "index.html").read_text()
    scripts = re.findall(r'<script src="/js/([\w-]+\.js)"></script>', page)
    assert sorted(scripts) == sorted(f.name for f in (static / "js").glob("*.js"))  # each file loaded, none forgotten
    for name in scripts:
        check = subprocess.run(["node", "--check", str(static / "js" / name)], capture_output=True, text=True)
        assert check.returncode == 0, f"{name}: {check.stderr}"
    whole = tmp_path / "page.js"  # in their order, one scope as the browser runs them: no name declared twice
    whole.write_text("".join((static / "js" / name).read_text() for name in scripts))
    check = subprocess.run(["node", "--check", str(whole)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


def test_season_maps_are_suggested_by_dates_else_by_order(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    ours = [{"season": 34, "air_date": f"2026-09-{d:02}"} for d in (1, 8, 15)] + [{"season": 33, "air_date": "2025-09-02"}]
    dated = [{"season": 29, "number": n, "air_date": f"2026-09-{d:02}"} for n, d in ((1, 2), (2, 9), (3, 15))]
    assert web.suggest_season_map(ours, dated) == {34: 29}  # the dates match, a day off at most
    undated = [{"season": s, "number": 1} for s in (27, 28, 29)]
    assert web.suggest_season_map(ours, undated) == {34: 29, 33: 28}  # the latest seasons, in order
    same = [{"season": s, "number": 1} for s in (33, 34)]
    assert web.suggest_season_map(ours, same) == {}  # numbered alike: nothing to map


def test_a_series_is_failing_after_three_failures_in_a_row(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    card = lambda tvdb, outcome, n, cause="": {"tvdbId": tvdb, "outcome": outcome, "ended": "x", "started": f"2026-09-{n:02}", "cause": cause}
    cards = [card(1, "failed", 9, "401 Unauthorized"), card(1, "failed", 8), card(1, "failed", 7), card(1, "downloaded", 6),
             card(2, "failed", 9), card(2, "failed", 8), card(2, "downloaded", 7),
             card(3, "failed", 9), card(3, "unavailable", 8), card(3, "failed", 7), card(3, "failed", 6)]
    health = web.series_health(cards)  # newest first
    assert health == {1: {"failing": 3, "cause": "401 Unauthorized", "since": "2026-09-07"},
                      3: {"failing": 3, "cause": "", "since": "2026-09-06"}}  # unfinished tries do not break a streak


def test_push_devices_come_and_go(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unshacklarr import push
    p = push.Push(tmp_path)
    assert len(p.public_key()) > 80 and (tmp_path / "vapid.pem").stat().st_mode & 0o077 == 0
    try:
        p.subscribe({"endpoint": "http://evil", "keys": {"p256dh": "x"}})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    p.subscribe({"endpoint": "https://push.example/a", "keys": {"p256dh": "k", "auth": "a"}}, "iPhone")
    p.subscribe({"endpoint": "https://push.example/b", "keys": {"p256dh": "k", "auth": "a"}})
    sent = []

    def fake_webpush(subscription_info, **kw):
        if subscription_info["endpoint"].endswith("/b"):
            raise push.WebPushException("gone", response=SimpleNamespace(status_code=410))
        sent.append((subscription_info["endpoint"], kw["data"]))

    monkeypatch.setattr(push, "webpush", fake_webpush)
    assert p.send("Downloaded: Show S01E01", "From MLT") == 1
    assert '"title": "Downloaded: Show S01E01"' in sent[0][1]
    assert [s["endpoint"] for s in p.subscriptions()] == ["https://push.example/a"]  # the gone one is dropped



def test_crave_series_from_the_sitemap(monkeypatch):
    from types import SimpleNamespace
    from unshacklarr import web
    pages = {
        "https://www.crave.ca/sitemap.xml": "<loc>https://www.crave.ca/crave-content-sitemap-1.xml</loc><loc>https://www.crave.ca/crave-media-sitemap-1.xml</loc>",
        "https://www.crave.ca/crave-media-sitemap-1.xml": "<loc>https://www.crave.ca/en/series/the-rainmaker-58525</loc><loc>https://www.crave.ca/en/movie/weapons-58903</loc>",
    }
    monkeypatch.setattr(web, "crave_ids", {})
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: SimpleNamespace(text=pages[url], raise_for_status=lambda: None))
    assert web.crave_series("https://www.crave.ca/fr/play/the-rainmaker/episode-1-s1e1-3162951") == "https://www.crave.ca/fr/series/the-rainmaker-58525"
    assert web.crave_series("https://www.crave.ca/en/play/weapons/weapons-1") is None  # a movie, not a series


def test_a_saved_key_never_follows_a_new_url(tmp_path, monkeypatch):
    import pytest
    from unshacklarr import web
    saved = {**web.sonarr_sync.SETTINGS_DEFAULTS, "sonarr_url": "http://sonarr:8989", "sonarr_api_key": "k"}
    cleared = web.check_settings({"sonarr_url": ""}, saved)
    assert cleared["sonarr_api_key"] == "k"  # clearing the URL keeps the key…
    with pytest.raises(web.web.HTTPBadRequest):  # …but it goes to no new address unless typed again
        web.check_settings({"sonarr_url": "http://evil:1"}, cleared)
    assert web.check_settings({"sonarr_url": "http://evil:1", "sonarr_api_key": "new"}, cleared)["sonarr_api_key"] == "new"
    monkeypatch.setattr(web, "SERIES_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(web.sonarr_sync, "apply_settings", lambda s: None)
    web.write_config({"settings": {}})
    assert (tmp_path / "config.yaml").stat().st_mode & 0o077 == 0


def test_clearing_the_history_spares_running_downloads(tmp_path, monkeypatch):
    import json
    from unshacklarr import web
    monkeypatch.setattr(web.sonarr_sync, "RUNS_DIR", tmp_path)
    for rid, outcome, ended in (("a", "failed", 1), ("b", "downloaded", 1), ("c", "failed", 1), ("d", "running", None)):
        (tmp_path / f"{rid}.json").write_text(json.dumps({"id": rid, "outcome": outcome, "ended": ended}))
        (tmp_path / f"{rid}.log").write_text("x")
    monkeypatch.setattr(web.sonarr_sync.EpisodeRun, "active", {"c"})  # still finishing
    assert web.forget_runs(lambda c: c.get("outcome") != "failed") == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["b.json", "b.log", "c.json", "c.log", "d.json", "d.log"]
    assert web.forget_runs(lambda c: False) == 1  # b; c is active, d has not ended


def test_one_version_everywhere():
    import re
    from pathlib import Path
    import unshacklarr
    root = Path(__file__).resolve().parent.parent
    project = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", (root / "CHANGELOG.md").read_text(), re.M)
    badge = re.search(r"badge/version-(\d+\.\d+\.\d+)-", (root / "README.md").read_text()).group(1)
    assert unshacklarr.__version__ == project == released[0] == badge  # pyproject, the package, the changelog and the README agree


def test_remote_and_server_reach_serve_as_its_parameters():
    from unshacklarr import options
    params = options.to_params({"--remote": True, "--server": "community", "--require-audio": "fr,en"}, options.dl_specs())
    assert params == {"remote": True, "server": "community", "require_audio": ["fr", "en"]}


def test_a_service_offers_the_options_its_remote_servers_declare(monkeypatch):
    from unshacklarr import web
    local = {"tag": "NF", "cli_params": [{"name": "profile", "kind": "option", "opts": ["-p", "--profile"]}]}
    remote = [{"name": "community", "services": [{"tag": "NF", "cli_params": [
        {"name": "profile", "kind": "option", "opts": ["--profile"]},
        {"name": "server_identity", "kind": "option", "opts": ["--server-identity"], "is_flag": True, "help": "Use the server's device."}]}]}]
    monkeypatch.setattr(web.UNSHACKLE, "services", lambda: [local])
    monkeypatch.setattr(web.UNSHACKLE, "remote_services", lambda: remote)
    specs = web.specs_of("NF")
    assert [s["flag"] for s in specs] == ["--profile", "--server-identity"]  # the local one kept, the remote one added
    assert specs[1]["remote"] == "community" and specs[1]["help"].startswith("On community (--remote).")
    assert web.check_options({"--server-identity": True}, web.known_services()["NF"], "NF") == {"--server-identity": True}


def test_unshackle_yaml_is_checked_kept_and_saved_with_its_permissions(tmp_path, monkeypatch):
    import os
    import stat
    from unshacklarr import web
    assert "line 2" in web.yaml_problem("a: 1\n  b: [\n")
    assert web.yaml_problem("- a\n- b\n").startswith("unshackle.yaml must hold settings")
    assert web.yaml_problem("") is None and web.yaml_problem("cdm:\n  default: x\n") is None
    monkeypatch.setattr(web, "CONFIG_HISTORY", tmp_path / "history")
    conf = tmp_path / "unshackle.yaml"
    conf.write_text("serve:\n  api_secret: a\n")
    os.chmod(conf, 0o664)
    text = conf.read_text()
    for i in range(12):
        new = f"serve:\n  api_secret: a\ntag: T{i}\n"
        answer = web.save_unshackle_yaml(conf, text, new)
        text = new
    assert conf.read_text().endswith("tag: T11\n") and stat.S_IMODE(conf.stat().st_mode) == 0o664  # its own permissions
    assert len(answer["versions"]) == 10 and answer["restart"] is False  # ten kept; serve untouched
    kept = tmp_path / "history" / f"{answer['versions'][0]['id']}.yaml"
    assert kept.read_text().endswith("tag: T10\n") and stat.S_IMODE(kept.stat().st_mode) == 0o600  # a secret: owner only
    assert web.save_unshackle_yaml(conf, text, "serve:\n  api_secret: b\n")["restart"] is True


def test_the_yaml_editor_files_are_served_and_know_unshackle_keys():
    import json
    from pathlib import Path
    from unshacklarr import web
    static = Path(web.__file__).parent / "static"
    assert {"/codemirror.js", "/unshackle-keys.json"} <= set(web.STATIC)
    assert (static / "codemirror.js").read_text().startswith("/* CodeMirror 6")
    keys = json.loads((static / "unshackle-keys.json").read_text())
    assert "network" in keys and "browser" in keys["network"]["keys"] and keys["cdm"]["info"]


def test_a_service_is_named_as_people_know_it():
    from unshacklarr.web import service_name
    name = lambda tag, help_="", url="": service_name({"tag": tag, "help": help_, "url": url})
    assert name("RMCP", "Service code for RMC+ (https://www.rmcplus.fr)") == "RMC+"
    assert name("DSNP", "Service code for Disney+ Streaming Service (https://disneyplus.com).") == "Disney+"
    assert name("MAX", "Service code for MAX's streaming service (https://max.com).") == "MAX"
    assert name("NF", "Service for https://netflix.com", "https://netflix.com") == "netflix.com"  # no name: its site
    assert name("DSCP", "", "https://discoveryplus.com") == "discoveryplus.com"
    assert name("EXAMPLE") == "EXAMPLE"


def test_a_service_list_is_kept_12_hours_for_the_series_as_it_is(tmp_path, monkeypatch):
    import asyncio
    import json
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    show = {"service": "RMCP", "title": "https://rmc/x"}
    monkeypatch.setattr(web, "read_config", lambda: {"series": {84095: show}})
    web.keep_service_list(84095, show, {"available": {"2701": {"service": "S26E11"}}, "titles": []})

    class Request:
        match_info = {"tvdb": "84095"}
    kept = lambda: json.loads(asyncio.run(web.service_list(Request())).text)
    assert kept()["available"] == {"2701": {"service": "S26E11"}}
    show["title"] = "https://rmc/y"  # another URL: another list
    assert kept() is None
    show["title"] = "https://rmc/x"
    lists = json.loads(web.SERVICE_LISTS.read_text())
    lists["84095"]["checked"] = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
    web.SERVICE_LISTS.write_text(json.dumps(lists))
    assert kept() is None  # too old


def test_a_services_episodes_are_found_in_sonarr_by_their_title():
    from unshacklarr.web import match_by_title
    # Faites entrer l'accusé: RMC+ numbers on its own, TMDB has the French titles and dates, Sonarr the dates.
    service = [{"season": 26, "number": 11, "name": "Les démembreuses de Rouen"},
               {"season": 26, "number": 6, "name": "Éric Galo, le double meutre de la rue Gamot"},  # RMC+'s typo
               {"season": 26, "number": 12, "part": 1, "name": "Le tueur (1/2)"}, {"season": 26, "number": 12, "part": 2, "name": "Le tueur (2/2)"},
               {"season": 26, "number": 7, "name": "Les disparus de Mirepoix"},
               {"season": 26, "number": 9, "name": "Episode 9"}]
    tmdb = [{"name": "Les démembreuses de Rouen", "air_date": "2026-09-20"},
            {"name": "Eric Galo, le double meurtre de la rue Gamot", "air_date": "2026-09-27"},
            {"name": "Le tueur", "air_date": "2026-10-04"},
            {"name": "Les disparus de Mirepoix", "air_date": "2026-03-08"},
            {"name": "Épisode 9", "air_date": "2026-05-24"}]
    sonarr = [{"id": 2701, "airDate": "2026-09-20"}, {"id": 2702, "airDate": "2026-09-27"}, {"id": 2703, "airDate": "2026-10-04"},
              {"id": 2606, "airDate": "2026-03-08"}, {"id": 2607, "airDate": "2026-03-08"}, {"id": 2609, "airDate": "2026-05-24"}]
    found = match_by_title(service, sonarr, tmdb)
    assert found["S26E11"]["episodeId"] == 2701
    assert found["S26E06"]["episodeId"] == 2702  # accents and a typo don't matter
    assert found["S26E12"] == {"episodeId": 2703, "name": "Le tueur"}  # its two parts, taken whole
    assert "S26E07" not in found  # two Sonarr episodes that day: none is picked
    assert "S26E09" not in found  # "Episode 9" names nothing


def test_a_country_title_finds_its_episode_and_a_number_saying_another_finds_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    # J'irai dormir chez vous: RMC+ has 17 seasons of its own, TVDB 3 here; the titles are countries
    service = [{"type": "episode", "season": 1, "number": 1, "name": "Japon"}, {"type": "episode", "season": 1, "number": 3, "name": "France"},
               {"type": "episode", "season": 2, "number": 1, "name": "Ghana"}, {"type": "episode", "season": 2, "number": 2, "name": "Chili"},
               {"type": "episode", "season": 3, "number": 1, "name": "Pérou"}, {"type": "episode", "season": 17, "number": 2, "name": "Mali"},
               {"type": "episode", "season": 3, "number": 2, "name": "Episode 2"}]
    sonarr = [{"id": 11, "seasonNumber": 1, "episodeNumber": 1, "title": "Japon"}, {"id": 13, "seasonNumber": 1, "episodeNumber": 3, "title": "Mali"},
              {"id": 21, "seasonNumber": 2, "episodeNumber": 1, "title": "Ghana"}, {"id": 22, "seasonNumber": 2, "episodeNumber": 2, "title": "Chili"},
              {"id": 31, "seasonNumber": 3, "episodeNumber": 1, "title": "Pérou"}, {"id": 32, "seasonNumber": 3, "episodeNumber": 2, "title": "TBA"},
              {"id": 99, "seasonNumber": 1, "episodeNumber": 9, "title": "France"}, {"id": 98, "seasonNumber": 2, "episodeNumber": 9, "title": "France"}]
    monkeypatch.setattr(web.UNSHACKLE, "call", lambda method, path, **k: {"titles": service, "services": [{"tag": "RMCP", "cli_params": []}]})
    monkeypatch.setattr(web.sonarr_sync, "sonarr_get", lambda path, **q: sonarr if path == "episode" else {})
    monkeypatch.setattr(web, "tmdb_key", lambda: "")
    found = web.probe_series({"service": "RMCP", "title": "x"}, 1)["available"]
    assert found[13] == {"service": "S17E02", "name": "Mali", "match": "title"}  # same title, whatever the number
    assert found[11]["service"] == "S01E01"  # "Japon" both sides
    assert found[32]["service"] == "S03E02"  # no title to say otherwise: its number
    assert not ({99, 98} & set(found))  # "France" names two of Sonarr's: none is picked
    assert web.same_title("mali", "mali") and not web.same_title("mali", "malibu")  # a short title only when the same


def test_the_schedule_knows_each_episodes_latest_download(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.sonarr_sync.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    for id_ in ("20260927-100000-1-S01E02", "20260928-100000-1-S01E02", "20260928-110000-1-S01E03"):
        (web.sonarr_sync.RUNS_DIR / f"{id_}.json").write_text(json.dumps(
            {"id": id_, "tvdbId": 1, "sxxeyy": id_[-6:], "ended": "2026-09-28T10:01:00+00:00", "outcome": "downloaded"}))
    assert web.latest_runs() == {(1, "S01E02"): "20260928-100000-1-S01E02", (1, "S01E03"): "20260928-110000-1-S01E03"}


def test_a_service_naming_episodes_in_another_language_is_matched_through_tmdb(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    # Futurama: Disney+ France names it in French and has seasons of its own after S11; Sonarr names it in English
    service = [{"type": "episode", "season": 2, "number": n, "name": name} for n, name in
               [(1, "Titanic 2"), (2, "L'Université martienne"), (3, "Omicron Persei 8 attaque"), (4, "Buvez du Slurm")]]
    service += [{"type": "episode", "season": 11, "number": 1, "name": "Le Seul Amigo"}]
    sonarr = [{"id": 200 + n, "seasonNumber": 2, "episodeNumber": n, "title": t, "airDate": f"1999-10-0{n}"} for n, t in
              [(1, "A Flight to Remember"), (2, "Mars University"), (3, "When Aliens Attack"), (4, "Fry & the Slurm Factory")]]
    sonarr += [{"id": 801, "seasonNumber": 8, "episodeNumber": 1, "title": "The One Amigo", "airDate": "2023-07-24"}]
    monkeypatch.setattr(web.UNSHACKLE, "call", lambda method, path, **k: {"titles": service, "services": [{"tag": "DSNP", "cli_params": []}]})
    monkeypatch.setattr(web.sonarr_sync, "sonarr_get", lambda path, **q: sonarr if path == "episode" else {"tmdbId": 615})
    monkeypatch.setattr(web, "tmdb_key", lambda: "")
    show = {"service": "DSNP", "title": "x", "season_offset": 3, "season_offset_from": 8}
    found = web.probe_series(show, 1)["available"]
    assert {found[i]["service"] for i in (201, 202, 203, 204)} == {"S02E01", "S02E02", "S02E03", "S02E04"}  # titles never meet: numbers
    assert found[801]["service"] == "S11E01"  # the season offset, from S08 on
    tmdb = [{"names": [en, fr], "air_date": f"1999-10-0{n}"} for n, en, fr in
            [(1, "A Flight to Remember", "Titanic 2"), (2, "Mars University", "L'Université martienne"),
             (3, "When Aliens Attack", "Omicron Persei 8 attaque"), (4, "Fry & the Slurm Factory", "Buvez du Slurm")]]
    monkeypatch.setattr(web, "tmdb_key", lambda: "key")
    monkeypatch.setattr(web, "tmdb_episodes", lambda tmdb_id: tmdb)
    found = web.probe_series(show, 1)["available"]
    assert found[202] == {"service": "S02E02", "name": "L'Université martienne", "match": "title"}  # its French name on TMDB
    swapped = [{**t, "number": 5 - t["number"]} if t["season"] == 2 else t for t in service]  # the service's order reversed
    monkeypatch.setattr(web.UNSHACKLE, "call", lambda method, path, **k: {"titles": swapped, "services": [{"tag": "DSNP", "cli_params": []}]})
    found = web.probe_series(show, 1)["available"]
    assert found[202]["service"] == "S02E03" and found[204]["service"] == "S02E01"  # the title wins over the number


def test_two_episodes_aired_the_same_day_keep_their_number_and_their_titles(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    # Futurama S11E01 and E02 came out together; Disney+ France has them as S14E01 and E02, in French
    pairs = [(1, "Beef", "Embrouille et barbaque"), (2, "Catfish Hunter", "Arnaque-moi si tu peux"),
             (3, "Our Flag Means Medical Coverage", "Pirates des Galaxies"), (4, "Lords of the Ring", "Les Seigneurs de l'anneau")]
    service = [{"type": "episode", "season": 14, "number": n, "name": fr} for n, _, fr in pairs]
    sonarr = [{"id": 1100 + n, "seasonNumber": 11, "episodeNumber": n, "title": en, "airDate": "2026-08-03" if n < 3 else f"2026-08-1{n}"}
              for n, en, _ in pairs]
    tmdb = [{"names": [en, fr], "air_date": e["airDate"], "season": 11, "number": n} for (n, en, fr), e in zip(pairs, sonarr)]
    monkeypatch.setattr(web.UNSHACKLE, "call", lambda method, path, **k: {"titles": service, "services": [{"tag": "DSNP", "cli_params": []}]})
    monkeypatch.setattr(web.sonarr_sync, "sonarr_get", lambda path, **q: sonarr if path == "episode" else {"tmdbId": 615})
    monkeypatch.setattr(web, "tmdb_key", lambda: "key")
    monkeypatch.setattr(web, "tmdb_episodes", lambda tmdb_id: tmdb)
    found = web.probe_series({"service": "DSNP", "title": "x", "season_offset": 3, "season_offset_from": 8}, 1)["available"]
    assert [found[1100 + n]["service"] for n in (1, 2, 3, 4)] == ["S14E01", "S14E02", "S14E03", "S14E04"]
    assert found[1101]["name"] == "Embrouille et barbaque"  # TMDB's number told the two apart


def test_the_menu_counts_the_downloads_going_on(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    monkeypatch.setattr(web.sonarr_sync.EpisodeRun, "active", {"a", "b"})
    monkeypatch.setattr(web.sonarr_sync, "waiting", {7: {}})
    r = asyncio.run(web.busy(None))
    assert json.loads(r.body) == {"running": 2, "queued": 1}


def test_a_broadcast_schedule_is_checked_on_save(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    plan = web.check_broadcast({"from": "s01e02", "start": "2026-09-30", "time": "20:39", "every": "weekly", "days": [],
                                "per_evening": "1", "evenings": {"2026-10-07": "2"}}, "Cat's Eyes")
    assert plan == {"from": "S01E02", "start": "2026-09-30", "time": "20:39", "every": "weekly", "days": [2],  # its start day
                    "per_evening": 1, "evenings": {"2026-10-07": 2}}
    for bad in ({"start": "", "time": "20:39"}, {"start": "2026-09-30", "time": "8pm"}, {"start": "2026-09-30", "time": "20:39", "per_evening": 12}):
        try:
            web.check_broadcast(bad, "x")
            raise AssertionError(bad)
        except web.web.HTTPBadRequest:
            pass


def test_a_tmdb_error_never_shows_its_url(tmp_path, monkeypatch):
    import requests
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    response = requests.Response()
    response.status_code, response.url = 404, "https://api.themoviedb.org/3/tv/1?api_key=SECRET"
    error = requests.HTTPError("404 Client Error: Not Found for url: https://api.themoviedb.org/3/tv/1?api_key=SECRET", response=response)
    assert "SECRET" not in web.tmdb_error(error) and "no series" in web.tmdb_error(error)
    assert "SECRET" not in web.tmdb_error(requests.ConnectionError("https://api.themoviedb.org/?api_key=SECRET"))



def test_tvdbs_own_titles_in_your_language_match_a_service_that_numbers_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    # Les Rencontres du Papotin: Molotov numbers nothing, Sonarr's titles are all "TBA", TVDB's website names the guests
    service = [{"type": "episode", "season": 0, "number": n, "name": name} for n, name in
               [(1, "E1 Kad Merad"), (2, "Orelsan"), (3, "E6 Franck Dubosc"), (4, "Omar Sy")]]
    sonarr = [{"id": i, "seasonNumber": s, "episodeNumber": e, "title": "TBA", "airDate": f"2025-0{e}-01"}
              for i, (s, e) in enumerate([(4, 4), (4, 6), (5, 1), (3, 6)], start=10)]
    monkeypatch.setattr(web.UNSHACKLE, "call", lambda method, path, **k: {"titles": service, "services": [{"tag": "MLT", "cli_params": []}]})
    monkeypatch.setattr(web.sonarr_sync, "sonarr_get", lambda path, **q: sonarr if path == "episode" else {"tvdbId": 425943})
    monkeypatch.setattr(web, "tmdb_key", lambda: "")
    monkeypatch.setattr(web, "tvdb_titles", lambda tvdb: {"S04E04": "Orelsan", "S04E06": "Frank Dubosc", "S05E01": "Kad Mérad", "S03E06": "Omar Sy"})
    probe = web.probe_series({"service": "MLT", "title": "x"}, 1)
    assert {i: a["service"] for i, a in probe["available"].items()} == {10: "S00E02", 11: "S00E03", 12: "S00E01", 13: "S00E04"}
    assert probe["local_titles"][10] == "Orelsan"
    assert web.usable_title("E2025 Catherine Deneuve") == "catherine deneuve" and web.usable_title("Episode 3") == ""
    # Ranma 1/2's S03E01 on TMDB before its translations: no title "de" to contradict Netflix's
    assert web.usable_title("修行DEディナー") == "" and web.usable_title("Ранма") == ""
    assert web.usable_title("Été à Tokyo") == "ete a tokyo" and web.usable_title("Ranma ½") != ""


def test_a_series_network_says_its_country(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    answer = {"networks": [{"name": "M6", "logo_path": "/m6.png", "origin_country": "FR"}], "watch/providers": {"results": {}}}
    monkeypatch.setattr(web.requests, "get", lambda *a, **k: type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: answer})())
    found = web.tmdb_network(1)
    assert (found["name"], found["network_country"], found["providers"]) == ("M6", "FR", {})


def test_the_api_key_opens_api_v1_only_and_its_answers_keep_their_shape(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"},
                      "series": {123: {"service": "M6", "title": "t"}}})
    started = []
    monkeypatch.setattr(web, "run_sync", lambda ids, replace=False: started.append((ids, replace)))
    monkeypatch.setattr(web.sonarr_sync, "sonarr_get", lambda path, **p: [{"id": 7}] if path == "series" else
                        [{"id": 70, "seasonNumber": 1, "episodeNumber": 2}, {"id": 71, "seasonNumber": 1, "episodeNumber": 3}])
    web.sonarr_sync.inbox_add("error", "Not imported: X S01E02", "disk full")

    async def go():
        async with TestClient(TestServer(web.app)) as browser, TestClient(TestServer(web.app)) as script:
            page = {"X-Unshackle": "1"}
            assert (await script.get("/api/v1/status")).status == 401
            await browser.post("/api/login", json={"password": "password1"}, headers=page)
            assert (await browser.post("/api/api-key/new", json={"password": "wrong-one"}, headers=page)).status == 403
            key = (await (await browser.post("/api/api-key/new", json={"password": "password1"}, headers=page)).json())["key"]
            assert key not in (tmp_path / "config.yaml").read_text()  # only its hash is kept
            assert (await (await browser.get("/api/session")).json())["api_key"]["set"]
            api = {"X-Api-Key": key}
            assert (await script.get("/api/v1/status", headers={"X-Api-Key": "wrong"})).status == 401
            assert (await script.get("/api/state", headers=api)).status == 401  # the rest stays behind the login
            assert (await script.post("/api/api-key/new", headers=api)).status == 401
            status = await (await script.get("/api/v1/status", headers=api)).json()
            assert set(status) == {"version", "sonarr", "unshackle", "sync", "downloads", "unread"} and status["unread"] >= 1
            notes = await (await script.get("/api/v1/notifications?limit=5", headers=api)).json()
            note = next(i for i in notes["items"] if i["title"] == "Not imported: X S01E02")  # the health checks add theirs
            assert set(note) == {"id", "at", "level", "title", "message", "unread"} and note["unread"] is True
            assert (await script.get("/api/v1/notifications?limit=0", headers=api)).status == 400
            assert (await script.get("/api/v1/notifications?limit=²", headers=api)).status == 400
            assert (await script.post("/api/v1/downloads", json=[1], headers=api)).status == 400
            assert (await script.post("/api/v1/downloads", json={"episodeIds": [True]}, headers=api)).status == 400
            assert (await script.post("/api/v1/downloads", json={"tvdbId": 999, "episodes": ["S01E01"]}, headers=api)).status == 404
            r = await script.post("/api/v1/downloads", json={"tvdbId": 123, "episodes": ["s1e3", "S01E02"]}, headers=api)  # no X-Unshackle needed
            assert (r.status, await r.json()) == (200, {"queued": 2}) and started == [([71, 70], False)]
            assert (await script.post("/api/v1/downloads", json={"tvdbId": 123, "episodes": ["S02E01"]}, headers=api)).status == 404
            assert await (await script.get("/api/v1/downloads", headers=api)).json() == []
            await browser.post("/api/password", json={"current": "password1", "new": "password2"}, headers=page)
            assert (await script.get("/api/v1/status", headers=api)).status == 200  # a new password keeps the key
            await browser.post("/api/api-key/delete", headers=page)
            assert (await script.get("/api/v1/status", headers=api)).status == 401

    asyncio.run(go())
    card = {"id": "waiting-70", "series": "X", "tvdbId": 123, "sxxeyy": "S01E02", "service": "M6", "waiting": True,
            "started": "t", "ended": None, "outcome": "running", "episodeId": 70, "step": "queued"}
    assert web.v1_download(card) == {"id": "waiting-70", "series": "X", "tvdbId": 123, "episode": "S01E02", "episodeId": 70,
                                     "service": "M6", "state": "queued", "step": None, "question": None,
                                     "started": "t", "ended": None, "error": None}


def test_an_episode_table_is_kept_as_the_sync_looks_it_up(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    numbering = web.check_numbering({"episode_map": {"s1e6": "S2E5.2"}}, "x")
    assert numbering["episode_map"] == {"S01E06": "S02E05.2"}
    assert web.sonarr_sync.service_episode(numbering, 1, 6) == "S02E05.2"


def test_wrong_passwords_sent_all_at_once_are_all_counted(tmp_path, monkeypatch):
    import asyncio
    import time
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"}})
    checked = []
    monkeypatch.setattr(web, "password_ok", lambda pw, stored: checked.append(pw) or time.sleep(0.05) or False)

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            tries = [client.post("/api/login", json={"password": f"guess{i}"}, headers={"X-Unshackle": "1"}) for i in range(20)]
            return sorted(r.status for r in await asyncio.gather(*tries))

    statuses = asyncio.run(go())
    assert statuses.count(401) == 5 and statuses.count(429) == 15 and len(checked) == 5  # not one scrypt more than the limit


def test_notification_tokens_and_proxy_passwords_never_reach_the_browser(tmp_path, monkeypatch):
    import copy
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    hook = "https://discord.com/api/webhooks/123/SECRETTOKEN"
    web.write_config({**web.read_config(), "defaults": {"--proxy": "http://user:pw123@proxy:8080"},
                      "series": {1: {"service": "X", "title": "t", "options": {"--proxy": "http://u:pw456@p:1"}}},
                      "notifications": {"targets": [{"url": hook, "levels": ["error"]}, {"url": "tgram://BOTTOKEN/42", "levels": ["error"]}]}})
    shown = web.public_config(web.read_config())
    text = json.dumps(shown)
    assert not any(secret in text for secret in ("SECRETTOKEN", "BOTTOKEN", "pw123", "pw456"))
    assert shown["notifications"]["targets"][0]["url"].startswith("https://discord.com/api/webhooks/")  # the page still knows the app
    back = copy.deepcopy(shown)
    web.real_config(back, web.read_config())  # the page sends what it got: the real values come back
    assert [t["url"] for t in back["notifications"]["targets"]] == [hook, "tgram://BOTTOKEN/42"]
    assert back["defaults"]["--proxy"] == "http://user:pw123@proxy:8080" and back["series"][1]["options"]["--proxy"] == "http://u:pw456@p:1"
    try:
        web.real_url("tgram://•••#0000000000", [hook])
        raise AssertionError("an unknown masked address was accepted")
    except web.web.HTTPBadRequest:
        pass


def test_a_new_password_or_logging_out_others_forgets_every_push_device(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"}})
    plant = lambda: web.sonarr_sync.PUSH.subscribe({"endpoint": "https://push.example/hidden", "keys": {"p256dh": "k", "auth": "a"}})

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            page = {"X-Unshackle": "1"}
            await client.post("/api/login", json={"password": "password1"}, headers=page)
            plant()
            await client.post("/api/logout-others", headers=page)
            after_logout = len(web.sonarr_sync.PUSH.subscriptions())
            plant()
            await client.post("/api/password", json={"current": "password1", "new": "password2"}, headers=page)
            return after_logout, len(web.sonarr_sync.PUSH.subscriptions())

    assert asyncio.run(go()) == (0, 0)


def test_every_answer_refuses_frames_and_the_api_never_forces_a_replacement(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"}})
    started = []
    monkeypatch.setattr(web, "run_sync", lambda ids, **kw: started.append((ids, kw)))

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            page, denied = await client.get("/"), await client.get("/api/state")
            await client.post("/api/login", json={"password": "password1"}, headers={"X-Unshackle": "1"})
            key = (await (await client.post("/api/api-key/new", json={"password": "password1"}, headers={"X-Unshackle": "1"})).json())["key"]
            api = {"X-Api-Key": key}
            r1 = await client.post("/api/v1/downloads", json={"episodeIds": [5], "replace": True}, headers=api)
            r2 = await client.post("/api/v1/downloads", json={"episodeIds": list(range(1, 102))}, headers=api)
            return page.headers, denied.headers, r1.status, r2.status

    page, denied, ok, too_many = asyncio.run(go())
    for headers in (page, denied):
        assert headers["X-Frame-Options"] == "DENY" and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert headers["X-Content-Type-Options"] == "nosniff"
    assert ok == 200 and started == [([5], {})]  # "replace" is not passed on
    assert too_many == 400


def test_paths_must_be_full_an_environment_key_never_follows_a_new_url_and_the_example_key_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    monkeypatch.setenv("SONARR_URL", "http://sonarr:8989")
    monkeypatch.setenv("SONARR_API_KEY", "envkey")
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    refused = lambda body: _raises(lambda: web.check_settings(body, {}))
    assert refused({"downloads": "downloads/../etc"}) and refused({"cookies_dir": "relative/cookies"})
    assert not refused({"downloads": "/srv/downloads", "sonarr_downloads": "D:\\\\Downloads", "unshackle_command": "unshackle"})
    assert refused({"sonarr_url": "http://evil.example"})  # the key from the environment belongs to http://sonarr:8989
    assert not refused({"sonarr_url": "http://sonarr:8989"})
    from unshacklarr.backend import Unshackle, UnshackleError
    u = Unshackle(tmp_path)
    u.configure({"unshackle_mode": "remote", "unshackle_url": "http://unshackle:8786", "unshackle_api_key": "change-me-to-a-long-random-string"})
    assert _raises(u.endpoint, UnshackleError)


def _raises(call, kind=Exception) -> bool:
    try:
        call()
    except kind:
        return True
    return False


def test_a_fresh_install_shows_its_addresses_and_folders_only_with_the_setup_code(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    monkeypatch.setenv("SETUP_TOKEN", "code")
    monkeypatch.setenv("SONARR_URL", "http://sonarr.lan:8989")
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            page = {"X-Unshackle": "1"}
            session = await (await client.get("/api/session")).json()
            wrong = await client.post("/api/setup/found", json={"setup_code": "nope"}, headers=page)
            right = await client.post("/api/setup/found", json={"setup_code": "code"}, headers=page)
            return session, wrong.status, await right.json()

    session, wrong, found = asyncio.run(go())
    assert "sonarr.lan" not in json.dumps(session) and wrong == 403
    assert found["sonarr_url"] == "http://sonarr.lan:8989"


def test_a_session_cookie_copied_before_logging_out_is_refused_after(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"}})

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            await client.post("/api/login", json={"password": "password1"}, headers={"X-Unshackle": "1"})
            copied = client.session.cookie_jar.filter_cookies(client.make_url("/"))[web.SESSION_COOKIE].value
            await client.post("/api/logout", headers={"X-Unshackle": "1"})
            client.session.cookie_jar.update_cookies({web.SESSION_COOKIE: copied})
            return (await (await client.get("/api/session")).json())["logged_in"]

    assert asyncio.run(go()) is False


def test_a_refused_notification_address_is_not_echoed_whole(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    try:
        web.check_urls(["tgram://NOTAVALIDTOKEN/42"])
        raise AssertionError("accepted")
    except web.web.HTTPBadRequest as e:
        assert "NOTAVALIDTOKEN" not in e.text and "tgram://" in e.text
    assert web.check_urls(["tgram://saved-before/1"], saved=["tgram://saved-before/1"]) == ["tgram://saved-before/1"]  # not checked again


def test_a_retry_asks_the_service_what_its_attempt_asked(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"}})
    web.sonarr_sync.RUNS_DIR.mkdir(parents=True)
    by_title = {"episode_map": {"S02E01": "S01E07"}}  # found by its title on the page, for that download only
    for run_id, episode, numbering in (("20261003-093229-000001-442345-S02E01", 70, by_title),
                                       ("20261003-093000-000001-442345-S02E02", 71, None)):
        card = {"id": run_id, "episodeId": episode, "tvdbId": 442345, "sxxeyy": run_id[-6:], "outcome": "failed",
                "started": "2026-10-03T09:32:29+00:00", "ended": "2026-10-03T09:33:00+00:00"}
        if numbering:
            card["numbering"] = numbering
        (web.sonarr_sync.RUNS_DIR / f"{run_id}.json").write_text(json.dumps(card))
    asked = []
    monkeypatch.setattr(web, "run_sync", lambda ids, **kw: asked.append((ids, kw.get("numbering"))))
    monkeypatch.setattr(web, "cdm_refusal", lambda ids: "")

    async def go():
        async with TestClient(TestServer(web.app)) as browser:
            page = {"X-Unshackle": "1"}
            await browser.post("/api/login", json={"password": "password1"}, headers=page)
            for ids in ([70], [71]):
                assert (await browser.post("/api/download", json={"episodeIds": ids, "retry": True}, headers=page)).status == 200
            # a download asked anew, not a retry, uses the series' numbering
            assert (await browser.post("/api/download", json={"episodeIds": [70]}, headers=page)).status == 200

    asyncio.run(go())
    assert asked == [([70], by_title), ([71], None), ([70], None)]


def test_a_newer_release_is_told_and_github_out_of_reach_keeps_the_last_answer(tmp_path, monkeypatch):
    import requests
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    monkeypatch.setattr(web, "__version__", "1.9.0")
    assert web.update_info() is None  # nothing asked yet

    class Answer:  # GitHub's latest-release page: a redirect to its tag
        status_code = 302
        def __init__(self, tag): self.headers = {"Location": f"https://github.com/o/r/releases/tag/{tag}"}

    for tag, told in (("v1.9.0", None), ("v1.8.3", None), ("v1.10.0", "1.10.0")):
        monkeypatch.setattr(web.requests, "get", lambda *a, tag=tag, **k: Answer(tag))
        web.check_update()
        assert (web.update_info() or {}).get("version") == told, tag
    def down(*a, **k):
        raise requests.ConnectionError("no network")
    no_release = type("NoRelease", (), {"status_code": 302, "headers": {"Location": "https://github.com/o/r/releases"}})()
    for answer in (down, lambda *a, **k: no_release):
        monkeypatch.setattr(web.requests, "get", answer)
        try:
            web.check_update()
            raise AssertionError("an answer with no release was taken")
        except (requests.RequestException, ValueError):
            pass  # watch_updates logs it and asks again in an hour
    assert web.update_info() == {"version": "1.10.0", "url": "https://github.com/o/r/releases/tag/v1.10.0"}


def test_an_anime_numbered_from_its_first_episode_is_found_by_its_absolute_number(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    # Ranma 1/2 on Netflix: season 2 numbered on from season 1 (S02E13), its titles in French and Sonarr's in English
    service = [{"type": "episode", "season": s, "number": n, "name": f"Épisode français {n}"} for s, n in
               [(1, 1), (1, 2), (2, 3), (2, 4)]]
    sonarr = [{"id": i, "seasonNumber": s, "episodeNumber": e, "absoluteEpisodeNumber": a, "title": f"English {a}"}
              for i, (s, e, a) in enumerate([(1, 1, 1), (1, 2, 2), (2, 1, 3), (2, 2, 4), (3, 1, 5)], start=10)]
    monkeypatch.setattr(web.UNSHACKLE, "call", lambda method, path, **k: {"titles": service, "services": [{"tag": "NF", "cli_params": []}]})
    monkeypatch.setattr(web.sonarr_sync, "sonarr_get", lambda path, **q: sonarr if path == "episode" else {"tvdbId": 451479})
    monkeypatch.setattr(web, "tmdb_key", lambda: "")
    monkeypatch.setattr(web, "tvdb_titles", lambda tvdb: {})
    probe = web.probe_series({"service": "NF", "title": "x"}, 1)
    found = {i: (a["service"], a.get("match")) for i, a in probe["available"].items()}
    assert found == {10: ("S01E01", None), 11: ("S01E02", None), 12: ("S02E03", "absolute"), 13: ("S02E04", "absolute")}
    # 14 (S03E01, the 5th): the service has nothing as S03E05 or S01E05, so nothing is guessed

    # A single season on the service (6play): S01E<absolute>, but never a number another episode has
    service[:] = [{"type": "episode", "season": 1, "number": n, "name": f"É{n}"} for n in (1, 2, 3, 4)]
    probe = web.probe_series({"service": "NF", "title": "x"}, 1)
    assert {i: a["service"] for i, a in probe["available"].items()} == {10: "S01E01", 11: "S01E02", 12: "S01E03", 13: "S01E04"}
    probe = web.probe_series({"service": "NF", "title": "x", "episode_map": {"S01E02": "S01E03"}}, 1)
    assert 12 not in probe["available"]  # S01E03 is S01E02's here, by the series' own table


def test_a_release_burst_stops_at_a_failure_not_at_not_out_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.sonarr_sync.RUNS_DIR.mkdir(parents=True)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    def card(outcome, kind="burst", ended=now):
        (web.sonarr_sync.RUNS_DIR / "20261005-000100-000001-440843-S02E05.json").write_text(json.dumps(
            {"id": "20261005-000100-000001-440843-S02E05", "episodeId": 9, "kind": kind, "outcome": outcome,
             "started": ended, "ended": ended}))
        web.cards_read = (0, [])  # read afresh

    assert not web.burst_failed(9)  # no try yet
    card("unavailable")
    assert not web.burst_failed(9)  # not out yet: the burst goes on
    card("failed")
    assert web.burst_failed(9)  # refused: the next tries would fail the same way
    card("failed", ended="2026-01-01T00:00:00+00:00")
    assert not web.burst_failed(9)  # a failure from another day says nothing of this burst
    card("failed", kind="manual")
    assert not web.burst_failed(9)


def test_a_streaming_site_maps_to_the_code_this_unshackle_has_for_it(tmp_path, monkeypatch):
    """HBO Max is MAX in one Unshackle, HMAX in another; Apple TV+ ATV or ATVP (cases from PR #3, by Artic0din)."""
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)

    def installed(*tags, help_site=None):
        services = [{"tag": t, "url": "", "help": ""} for t in tags]
        if help_site:
            services.append({"tag": "HBO", "url": "", "help": f"Service code for HBO ({help_site})."})
        monkeypatch.setattr(web.UNSHACKLE, "services", lambda: services)
        return web.service_domains()

    assert installed("HMAX", "ATVP")["hbomax.com"] == "HMAX" and installed("HMAX", "ATVP")["tv.apple.com"] == "ATVP"
    assert installed("MAX", "HMAX")["hbomax.com"] == "MAX"  # both: the first the table names
    assert "hbomax.com" not in installed("DSNP")  # none installed: never a suggestion Unshackle cannot take
    assert installed("MAX", help_site="https://www.hbomax.com")["hbomax.com"] == "HBO"  # a service naming the site wins
    assert "hbomax.com" in installed(help_site="https://www.hbomax.com") and "hbomax.com)." not in installed(help_site="https://www.hbomax.com")

    # A link is put right by its site, whatever the code: an Apple TV+ episode becomes its show, an HBO Max one its series
    assert web.series_title("https://tv.apple.com/us/episode/pilot/umc.cmc.1?showId=umc.cmc.show") == "umc.cmc.show"
    assert web.series_title("https://play.hbomax.com/ch/en/show/8931dfbf-d113-43de-8ee2-43bb594330d1/s1/e1-test") \
        == "https://play.hbomax.com/show/8931dfbf-d113-43de-8ee2-43bb594330d1"

    # Suggestions kept under MAX show under the code installed now, and go when no service takes their site
    kept = [{"service": "MAX", "url": "https://play.hbomax.com/show/x", "country": "AU", "site": "play.hbomax.com"},
            {"service": "ATV", "url": "umc.cmc.show", "country": "AU", "site": "tv.apple.com"}]
    installed("HMAX", "ATVP")
    assert [l["service"] for l in web.installed_links(kept)] == ["HMAX", "ATVP"]
    installed("DSNP")
    assert web.installed_links(kept) == []
    def down():
        raise web.UnshackleError("unreachable")
    monkeypatch.setattr(web, "service_domains", down)
    assert web.installed_links(kept) == kept  # Unshackle out of reach: as kept
    assert web.TMDB_HEADERS["User-Agent"].startswith("Unshacklarr/")  # TMDB's site refuses a fake browser (PR #2)


def test_accepted_audio_languages_are_language_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    assert web.check_audio_accept(" FR,en-US  de ", "x") == "fr, en-us, de" and web.check_audio_accept(None, "x") == ""
    for bad in ("orig", "fr;en", "français", "e"):
        try:
            web.check_audio_accept(bad, "x")
            raise AssertionError(f"expected {bad!r} to be refused")
        except web.web.HTTPBadRequest as e:
            assert "language code" in e.text


def test_a_series_is_checked_hours_before_its_release_and_told_once(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    now = datetime(2026, 10, 5, 18, tzinfo=timezone.utc)
    series = {1: {"service": "TF1", "title": "a", "release_time": "21:00"}, 2: {"service": "MAX", "title": "b", "release_time": "20:30"},
              3: {"service": "NF", "title": "c", "release_time": "20:00"}, 4: {"service": "ATV", "title": "d", "release_time": "23:59"}}
    config = {"auth": {"password": "x"}, "series": series, "notifications": {}}
    monkeypatch.setattr(web, "read_config", lambda: config)
    monkeypatch.setattr(web.sonarr_sync, "LOCAL", timezone.utc)
    ep = lambda i, tvdb: {"id": i, "seasonNumber": 1, "episodeNumber": i, "airDateUtc": "2026-10-05T19:00:00Z", "hasFile": False,
                          "series": {"tvdbId": tvdb, "title": f"Show{tvdb}"}}
    monkeypatch.setattr(web, "calendar_episodes", lambda config, start, end: [ep(1, 1), ep(2, 2), ep(3, 3), ep(4, 4)])
    monkeypatch.setattr(web.sonarr_sync, "no_cdm", lambda tag, config=None: "No CDM for MAX" if tag == "MAX" else "")

    def titles(show):
        if show["service"] == "TF1":
            raise web.UnshackleError("unshackle serve: login refused")
        return [{"type": "episode"}]
    listed = []
    monkeypatch.setattr(web, "list_titles", lambda show: listed.append(show["service"]) or titles(show))
    sent = []
    monkeypatch.setattr(web.sonarr_sync, "notify", lambda settings, level, title, message, **k: sent.append((title, message)))
    assert web.precheck(now) == ["Not ready for its release: Show1 S01E01", "Not ready for its release: Show2 S01E02"]
    assert "login refused" in sent[0][1] and "at 21:00" in sent[0][1] and "No CDM" in sent[1][1]
    assert sorted(listed) == ["NF", "TF1"]  # ATV comes out at 23:59: later; MAX has no CDM: not listed for nothing
    assert web.precheck(now + timedelta(minutes=10)) == [] and sorted(listed) == ["NF", "TF1"]  # each checked once
    config["notifications"]["events"] = {"precheck": False}
    assert web.precheck(now + timedelta(hours=3)) == []  # turned off


def test_a_reverse_proxy_lets_in_only_from_its_own_address(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    assert web.check_proxy_auth(" Remote-User ", "172.18.0.5/16, 10.0.0.1") == ("Remote-User", "172.18.0.0/16, 10.0.0.1/32")
    assert web.check_proxy_auth("", "") == ("", "")
    for header, sources in (("Remote-User", ""), ("Remote User", "10.0.0.1"), ("Remote-User", "anywhere")):
        try:
            web.check_proxy_auth(header, sources)
            raise AssertionError(f"expected {header!r}, {sources!r} to be refused")
        except web.web.HTTPBadRequest:
            pass

    def proxied(sources):
        web.sonarr_sync.SETTINGS.update(proxy_auth_header="Remote-User", proxy_auth_from=sources)

    user = {"Remote-User": "alice"}

    async def go():
        async with TestClient(TestServer(web.app)) as client:  # the test client connects from 127.0.0.1
            async def visit(headers):
                session = await (await client.get("/api/session", headers=headers)).json()
                return session.get("logged_in"), session.get("proxy_user"), (await client.get("/api/inbox", headers=headers)).status

            proxied("127.0.0.1/32")
            assert (await visit(user))[2] == 403  # not set up yet: the proxy opens nothing before the password exists
            web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"}})
            proxied("127.0.0.1/32")  # writing the config read the settings again
            assert await visit(user) == (True, "alice", 200)  # from the proxy's address, with its header: in
            assert (await visit({}))[2] == 401  # from the proxy, without the header: who is it? out
            proxied("10.0.0.0/8")
            assert (await visit({**user, "X-Real-IP": "10.0.0.2", "X-Forwarded-For": "10.0.0.2"}))[2] == 401  # anyone else: out, whatever it claims
            proxied("")
            assert (await visit(user))[2] == 401  # turned off

    asyncio.run(go())


def test_the_page_style_and_scripts_are_served_like_the_page(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"}})

    async def go():
        async with TestClient(TestServer(web.app)) as client:  # not logged in: the page itself is public too
            got = {}
            html = await (await client.get("/")).text()
            got["page"] = html
            for path in ("/app.css", "/js/core.js", "/js/setup.js", "/js/nope.js", "/js/../web.py", "/app.css?v=abc"):
                r = await client.get(path)
                got[path] = (r.status, r.headers.get("Cache-Control"), r.headers.get("Content-Type", "").split(";")[0])
            return got

    got = asyncio.run(go())
    assert got["/app.css"] == (200, "no-cache", "text/css") and got["/js/core.js"] == (200, "no-cache", "text/javascript")
    assert got["/js/setup.js"][0] == 200 and got["/js/nope.js"][0] in (401, 404) and got["/js/../web.py"][0] in (401, 404)
    import re
    assert re.search(r'href="/app\.css\?v=[0-9a-f]{10}"', got["page"]) and re.search(r'src="/js/core\.js\?v=[0-9a-f]{10}"', got["page"])
    assert got["/app.css?v=abc"][0] == 200  # the version is for the browser's cache only: any serves the file


def test_the_settings_are_backed_up_and_restored_but_never_the_password(tmp_path, monkeypatch):
    import asyncio
    import yaml
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    auth = {"password": web.hash_password("password1"), "secret": "s1"}
    web.write_config({**web.read_config(), "auth": auth, "series": {111: {"service": "TF1", "title": "https://tf1/a"}},
                      "settings": {"sonarr_api_key": "sonarr-secret"}})
    web.app._middlewares = type(web.app._middlewares)([web.same_origin_only])  # logged in, for this test
    h = {"X-Unshackle": "1"}

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            wrong = (await client.post("/api/backup", json={"password": "nope"}, headers=h)).status
            r = await client.post("/api/backup", json={"password": "password1"}, headers=h)
            saved = await r.json()
            data = yaml.safe_load(saved["text"])
            assert "auth" not in data and "scrypt" not in saved["text"] and data["settings"]["sonarr_api_key"] == "sonarr-secret"
            data["series"] = {222: {"service": "MAX", "title": "https://max/b"}}
            bad = [(await client.post("/api/restore", json={"password": "password1", "text": text}, headers=h)).status
                   for text in ("- a list", "series: [1, 2]", "series: {abc: {}}", "{unclosed", "other: 1")]
            ok = await client.post("/api/restore", json={"password": "password1", "text": yaml.safe_dump(data)}, headers=h)
            return wrong, bad, ok.status, await ok.json()

    wrong, bad, status, body = asyncio.run(go())
    assert wrong == 403 and bad == [400] * 5 and status == 200 and body == {"series": 1}
    config = web.read_config()
    assert list(config["series"]) == [222] and config["auth"] == auth  # restored, the password and session kept
    assert "111" in (tmp_path / "config-before-restore.yaml").read_text()  # what it replaced, kept aside


def test_an_episode_without_its_preferred_audio_is_got_again_once_it_comes(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    sync = importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    config = {"series": {111: {"service": "TF1", "title": "a", "audio_prefer": "fr"}, 222: {"service": "MAX", "title": "b"}}, "notifications": {}}
    monkeypatch.setattr(web, "read_config", lambda: config)
    ep = lambda i, tvdb: {"id": i, "seasonNumber": 1, "episodeNumber": i, "series": {"tvdbId": tvdb, "title": f"Show{tvdb}"}}
    assert sync.preferred_audio(config["series"][111], config) == "fr" and sync.preferred_audio(config["series"][222], config) == ""
    sync.note_upgrade(ep(1, 111), config["series"][111], "S01E01", "fr")  # imported in English: watched
    sync.note_upgrade(ep(2, 222), config["series"][222], "S01E02", "fr")
    now = datetime.now(timezone.utc)
    later = now + timedelta(days=1)
    monkeypatch.setattr(web.sonarr_sync, "download_request", lambda show, config, wanted, out: {"service": show["service"], "wanted": [wanted]})
    monkeypatch.setattr(web.sonarr_sync, "tracks_on_service", lambda show, config, request: {"audio": {"fr"} if request["service"] == "TF1" else {"en"}, "subtitles": set()})
    started, told = [], []
    monkeypatch.setattr(web, "run_sync", lambda ids, replace=False, kind="manual": started.append((ids, replace, kind)))
    monkeypatch.setattr(web.sonarr_sync, "notify", lambda settings, level, title, *a, **k: told.append(title))
    assert web.check_upgrades(now) == [] and started == []  # checked at its download, a day ago at most: not again yet
    assert web.check_upgrades(later) == ["Show111 S01E01"] and started == [([1], True, "upgrade")]  # French on TF1: got again, in place
    assert web.check_upgrades(later + timedelta(hours=1)) == []  # once a day
    sync.note_upgrade(ep(1, 111), config["series"][111], "S01E01", "")  # imported in French: off the list
    assert list(json.loads(sync.UPGRADES_FILE.read_text())) == ["2"]
    web.check_upgrades(now + timedelta(days=31))
    assert told == ["Kept without fr audio: Show222 S01E02"] and json.loads(sync.UPGRADES_FILE.read_text()) == {}  # past its days: kept, told once


def test_a_series_is_found_by_name_on_its_service(tmp_path, monkeypatch):
    import asyncio
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    asked = []

    def serve(method, path, json=None, **_):
        asked.append(json)
        if json["service"] == "NF":
            raise web.UnshackleError("unshackle serve: Search is not supported by NF")
        return {"results": [{"id": "123", "title": "Show", "label": "SERIES", "description": "x" * 500, "url": "https://tf1/show"}, {"title": "no id"}]}
    monkeypatch.setattr(web.UNSHACKLE, "call", serve)
    web.app._middlewares = type(web.app._middlewares)([web.same_origin_only])  # logged in, for this test

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            async def find(show, query="Show"):
                r = await client.post("/api/series/search", json={"show": show, "query": query}, headers={"X-Unshackle": "1"})
                return r.status, await (r.json() if r.status == 200 else r.text())
            return [await find({"service": "TF1", "options": {"--profile": "alt"}}), await find({"service": "NF"}), await find({"service": "TF1"}, "")]

    found, unsupported, empty = asyncio.run(go())
    assert found[0] == 200 and found[1]["results"] == [{"id": "123", "title": "Show", "label": "SERIES", "description": "x" * 240, "url": "https://tf1/show"}]
    assert asked[0] == {"profile": "alt", "service": "TF1", "query": "Show"}  # the series' own profile
    assert unsupported == (400, "NF can't be searched: paste the series' URL instead") and empty[0] == 400


def test_other_servers_keep_their_key_hidden_and_ladders_are_checked(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    from aiohttp import web as aioweb
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    saved = [{"name": "vpn", "url": "http://vpn:8786", "api_key": "secret-key", "downloads": "/dl"}]
    kept = web.check_backends([{"name": "vpn", "url": "http://vpn:8786/", "downloads": "/dl/"}], saved)
    assert kept == saved  # an empty key keeps the saved one, for the same address
    for bad in ([{"name": "vpn", "url": "http://elsewhere:8786"}],  # a new address never gets the old key
                [{"name": "", "url": "http://vpn:8786"}], [{"name": "a", "url": "ftp://x"}],
                [{"name": "a", "url": "http://x"}, {"name": "a", "url": "http://y"}]):
        try:
            web.check_backends(bad, saved)
            raise AssertionError(bad)
        except aioweb.HTTPBadRequest:
            pass
    shown = web.public_config({**web.read_config(), "settings": {"backends": saved}})["settings"]["backends"]
    assert shown == [{"name": "vpn", "url": "http://vpn:8786", "downloads": "/dl", "api_key_set": True}]
    assert web.stored_key_for("http://vpn:8786", "unshackle") == "" and "secret-key" not in json.dumps(shown)

    assert [lad["name"] for lad in web.check_ladders(None)] == ["1080p", "4K, then 1080p", "Archival"]  # the built-in ones
    ladders = web.check_ladders([{"name": "Mine", "steps": [{"codec": "HEVC", "range": "DV", "min": "2160", "max": ""}]}])
    assert ladders == [{"name": "Mine", "steps": [{"codec": "HEVC", "range": "DV", "min": 2160, "max": 0}]}]
    assert web.check_ladder_name("off", ladders, "x") == "off" and web.check_ladder_name("", ladders, "x") == ""
    for bad in ([{"name": "A", "steps": []}], [{"name": "A", "steps": [{"codec": "MPEG2"}]}],
                [{"name": "A", "steps": [{"min": 1080, "max": 720}]}], [{"name": "off", "steps": [{}]}]):
        try:
            web.check_ladders(bad)
            raise AssertionError(bad)
        except aioweb.HTTPBadRequest:
            pass
    try:
        web.check_ladder_name("Gone", ladders, "S")
        raise AssertionError("a deleted ladder")
    except aioweb.HTTPBadRequest:
        pass


# From #4, by Artic0din: a viewer closing the terminal mid-write
RESET_ERRORS = [ConnectionResetError]
if hasattr(client_exceptions, "ClientConnectionResetError"):
    RESET_ERRORS.append(client_exceptions.ClientConnectionResetError)


@pytest.mark.parametrize("mode", ["run", "batch"])
@pytest.mark.parametrize("failure", [*RESET_ERRORS, RuntimeError, asyncio.CancelledError, None])
def test_a_viewer_closing_the_terminal_leaves_the_download_running(tmp_path, monkeypatch, mode, failure):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    run_id = "20261005-000000-123456-1-S01E01"
    batch = "20261005-000000-abcdef"
    web.sonarr_sync.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (web.sonarr_sync.RUNS_DIR / f"{run_id}.log").write_bytes(b"fixture progress\n")
    (web.sonarr_sync.RUNS_DIR / f"{run_id}.json").write_text(json.dumps({"ended": "fixture"}))
    active = {run_id: object()}
    monkeypatch.setattr(web.sonarr_sync.EpisodeRun, "active", active)
    monkeypatch.setattr(web, "run_cards", lambda: [{"id": run_id, "batch": batch,
        "series": "Fixture", "sxxeyy": "S01E01", "outcome": "completed"}])

    class Socket:
        closed = False

        def __init__(self):
            self.sent = []

        async def prepare(self, request):
            pass

        async def send_bytes(self, data):
            if failure:
                raise failure("fixture transport or application failure")
            self.sent.append(data)

        async def close(self):
            self.closed = True

    socket = Socket()
    monkeypatch.setattr(web.web, "WebSocketResponse", lambda **kwargs: socket)
    request = SimpleNamespace(headers={"Origin": "http://localhost"}, host="localhost",
                              query={"run": run_id} if mode == "run" else {"batch": batch})
    if failure in (RuntimeError, asyncio.CancelledError):
        with pytest.raises(failure):
            asyncio.run(web.terminal(request))
    else:
        assert asyncio.run(web.terminal(request)) is socket
        if failure is None:
            assert b"fixture progress\n" in socket.sent
            assert socket.closed
    assert web.sonarr_sync.EpisodeRun.active is active
    assert run_id in active


def test_ladder_languages_networks_and_the_catch_up_list(tmp_path, monkeypatch):
    # From #11 and #12, by mj23au
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    from aiohttp import web as aioweb
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    steps = [{"codec": "", "range": "SDR", "min": 1080, "max": 1080}]
    assert web.check_ladders([{"name": "A", "steps": steps, "audio": "en-AU, en", "subtitles": []}])[0] == \
        {"name": "A", "steps": steps, "audio": ["en-AU", "en"]}
    try:
        web.check_ladders([{"name": "A", "steps": steps, "audio": ["orig"]}])
        raise AssertionError("orig is not a language")
    except aioweb.HTTPBadRequest:
        pass
    assert web.service_for_network("Channel 4", {"ALL4"}) == "ALL4" and web.service_for_network("E4", {"C4"}) == "C4"
    assert web.service_for_network("BBC One", {"iP"}) == "iP" and web.service_for_network("BBC One", set()) is None
    assert web.service_for_network("Channel 45", {"ALL4"}) is None  # a first word, not any prefix

    now = web.datetime(2026, 10, 7, tzinfo=web.timezone.utc)
    (tmp_path / "config.yaml").write_text("series:\n  1: {service: X, title: t}\n")
    series = {1: {"id": 7, "tvdbId": 1, "title": "Show", "monitored": True}, 2: {"id": 8, "tvdbId": 2, "title": "Other"}}
    ep = lambda i, s, n, aired, **k: {"id": i, "seasonNumber": s, "episodeNumber": n, "airDateUtc": aired, "monitored": True, "hasFile": False, **k}  # noqa: E731
    episodes = [ep(1, 1, 1, "2026-01-01T20:00:00Z"), ep(2, 1, 2, "2026-10-01T20:00:00Z"), ep(3, 1, 3, "2026-10-01T20:00:00Z", hasFile=True),
                ep(4, 1, 4, "2026-12-01T20:00:00Z"), ep(5, 0, 1, "2026-10-01T20:00:00Z"), ep(6, 1, 6, "2026-10-02T20:00:00Z", monitored=False)]
    monkeypatch.setattr(web.sonarr_sync, "sonarr_series", lambda wanted: series)
    monkeypatch.setattr(web.sonarr_sync, "sonarr_get", lambda path, **p: episodes if p.get("seriesId") == 7 else [ep(9, 1, 1, "2026-10-01T20:00:00Z")])
    assert [e["episodeId"] for e in web.missing_of_managed(None, now)] == [1, 2]  # aired, monitored, no file, series managed here
    assert [e["episodeId"] for e in web.missing_of_managed(30, now)] == [2]
    (web.sonarr_sync.DOWNLOADS / "unshackle-1-S01E02").mkdir(parents=True)
    assert [e["episodeId"] for e in web.missing_of_managed(30, now)] == []  # already waiting in the downloads folder


def test_upgrades_find_the_files_a_service_has_on_an_earlier_step(tmp_path, monkeypatch):
    # From #11, by mj23au: judged by the ladder, H.264 to H.265 at the same height included
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    sync = web.sonarr_sync
    assert web.ladder_track({"resolution": "1920x800", "videoCodec": "x265", "videoDynamicRangeType": "DV HDR10"}) == \
        {"height": 800, "width": 1920, "codec": "HEVC", "range": "DV", "layers": ["DV", "HDR10"]}
    assert web.ladder_track({"resolution": "1280x720", "videoCodec": "h264", "videoDynamicRangeType": ""})["range"] == "SDR"
    hevc_first = {"name": "HEVC first", "steps": [{"codec": "HEVC", "range": "SDR", "min": 1080, "max": 1080},
                                                  {"codec": "AVC", "range": "SDR", "min": 1080, "max": 1080}]}
    assert sync.step_of(hevc_first, {"height": 1080, "codec": "AVC", "range": "SDR"}) == 1
    assert sync.step_of(hevc_first, {"height": 720, "codec": "AVC", "range": "SDR"}) == 2  # outside it

    (tmp_path / "config.yaml").write_text("series:\n  1: {service: X, title: t, ladder: HEVC first}\n  2: {service: X, title: u}\n")
    config = sync.read_file()
    config["quality_ladders"] = [hevc_first]
    monkeypatch.setattr(sync, "read_file", lambda: config)
    series = {1: {"id": 7, "tvdbId": 1, "title": "Show"}, 2: {"id": 8, "tvdbId": 2, "title": "No ladder"}}
    avc = {"resolution": "1920x1080", "videoCodec": "h264"}
    files = [{"id": 70, "mediaInfo": avc}, {"id": 71, "mediaInfo": {**avc, "videoCodec": "hevc"}}, {"id": 72, "mediaInfo": avc}]
    eps = [{"id": n, "seasonNumber": 1, "episodeNumber": n, "hasFile": True, "episodeFileId": 69 + n} for n in (1, 2, 3)]
    monkeypatch.setattr(sync, "sonarr_series", lambda wanted: series)
    monkeypatch.setattr(sync, "sonarr_get", lambda path, **p: (files if path == "episodefile" else eps) if p.get("seriesId") == 7 else [])
    monkeypatch.setattr(sync, "download_request", lambda show, config, wanted, out: {"service": "X", "title_id": "t", "wanted": [wanted]})
    asked = []
    on_service = {"S01E01": [{"height": 1080, "codec": "HEVC", "range": "SDR"}], "S01E03": [{"height": 1080, "codec": "AVC", "range": "SDR"}]}

    def list_tracks(show, config, request, ladder):  # one call for the series, each answer titled
        asked.append(request["wanted"])
        return [{"title": {"season": 1, "number": int(w[-2:])}, "video": on_service[w]} for w in request["wanted"]]
    monkeypatch.setattr(sync, "list_tracks", list_tracks)
    monkeypatch.setattr(web, "UPGRADE_PAUSE", 0)
    web.scan_upgrades()
    assert asked == [["S01E01", "S01E03"]]  # S01E02 is on the first step already; the series without a ladder is not asked
    found = web.read_json(web.UPGRADES_FILE, {})["items"]
    assert [(i["sxxeyy"], i["fileStep"], i["betterStep"]) for i in found] == [("S01E01", 2, 1)]  # S01E03: nothing better there
    assert not web.upgrade_scan["running"] and web.upgrade_scan["done"] == 2

    # answers are kept upgrade_recheck_days for the same file, then asked again
    web.scan_upgrades()
    assert len(asked) == 1 and [i["sxxeyy"] for i in web.read_json(web.UPGRADES_FILE, {})["items"]] == ["S01E01"]
    files[0] = {**files[0], "id": 80}
    eps[0] = {**eps[0], "episodeFileId": 80}  # a new file for S01E01
    web.scan_upgrades()
    assert asked[-1] == ["S01E01"]
    monkeypatch.setitem(sync.SETTINGS, "upgrade_recheck_days", 0)
    web.scan_upgrades()
    assert asked[-1] == ["S01E01", "S01E03"]
    config["series"][1]["skip_upgrades"] = True  # left out on its page, or in Settings, Quality, Upgrades
    web.scan_upgrades()
    assert len(asked) == 3 and web.read_json(web.UPGRADES_FILE, {})["items"] == []
    del config["series"][1]["skip_upgrades"]
    monkeypatch.setitem(sync.SETTINGS, "upgrade_max_age_years", 1)
    eps[:] = [{**e, "airDateUtc": "2001-01-01T00:00:00Z"} for e in eps]  # aired too long ago
    web.scan_upgrades()
    assert len(asked) == 3

    # Your release group: a file from another is replaced by the same step too, an empty setting ignores the group
    monkeypatch.setitem(sync.SETTINGS, "upgrade_max_age_years", 0)
    monkeypatch.setitem(sync.SETTINGS, "upgrade_recheck_days", 30)
    on_service["S01E02"] = [{"height": 1080, "codec": "HEVC", "range": "SDR"}]
    files[:] = [{**f, "releaseGroup": "unshackle" if f["id"] == 71 else "NTb"} for f in files]
    monkeypatch.setitem(sync.SETTINGS, "upgrade_other_groups", True)
    monkeypatch.setattr(sync, "backend_for", lambda service, config=None: (_ for _ in ()).throw(sync.UnshackleError("down")))
    asked_before = len(asked)
    web.scan_upgrades()
    assert len(asked) == asked_before  # no group set anywhere (nor in an Unshackle): the switch changes nothing
    config["defaults"] = {"--tag": "Unshackle"}  # Download options, Group Tag, for every series
    assert sync.release_group_of(config["series"][1], config) == ("Unshackle", "Download options")
    web.scan_upgrades()
    assert asked[-1] == ["S01E01", "S01E03"]  # S01E02 is ours on step 1; the rule changed, so nothing kept is reused
    found = web.read_json(web.UPGRADES_FILE, {})["items"]
    assert [(i["sxxeyy"], i["group"], i["fileStep"], i["betterStep"]) for i in found] == [("S01E01", "NTb", 2, 1), ("S01E03", "NTb", 2, 2)]
    assert found[1]["file"] == "1080p AVC SDR from NTb"
    files[1] = {**files[1], "releaseGroup": "RAWR"}
    web.scan_upgrades()
    assert asked[-1] == ["S01E02"]  # E01 and E03 kept, E02 now from another group, the same step on the service
    assert [i["sxxeyy"] for i in web.read_json(web.UPGRADES_FILE, {})["items"]] == ["S01E01", "S01E03", "S01E02"]
    monkeypatch.setattr(web, "read_config", lambda: config)
    groups = asyncio.run(web.upgrade_groups(None))
    assert json.loads(groups.text) == {"groups": [{"group": "Unshackle", "where": "Download options", "series": [1, 2]}], "none": []}


def test_automatic_backups_are_kept_listed_and_restore_a_new_install(tmp_path, monkeypatch):
    # From #15, by mj23au
    import asyncio
    import os
    import yaml
    from datetime import datetime, timedelta, timezone
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    monkeypatch.setenv("SETUP_TOKEN", "code")
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    ladders = [{"name": "Mine", "steps": [{"codec": "", "range": "SDR", "min": 1080, "max": 1080}]}]
    web.write_config({**web.read_config(), "auth": {"password": web.hash_password("password1"), "secret": "s1"},
                      "series": {111: {"service": "iP", "title": "x"}}, "settings": {"sonarr_api_key": "sonarr-secret"},
                      "quality_ladders": ladders})
    sync = web.sonarr_sync
    assert web.make_backup() is None and not (tmp_path / "backups").exists()  # off by default
    sync.SETTINGS.update(backup_every_days=1, backup_keep=3)
    start = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    first = web.make_backup(start)
    saved = yaml.safe_load(first.read_text())
    assert saved["series"] and saved["quality_ladders"] == ladders and "auth" not in saved  # the ladders too, never the password
    assert web.backup_problem(saved) is None and oct(first.stat().st_mode & 0o777) == "0o600"
    os.utime(first, (start.timestamp(), start.timestamp()))
    assert web.make_backup(start + timedelta(hours=12)) is None  # not due yet
    for day in range(1, 6):
        at = start + timedelta(days=day)
        os.utime(web.make_backup(at), (at.timestamp(), at.timestamp()))
    names = [p.name for p in web.saved_backups()]
    assert names == ["unshacklarr-2026-10-06-0900.yaml", "unshacklarr-2026-10-05-0900.yaml", "unshacklarr-2026-10-04-0900.yaml"]
    for bad in ("../config.yaml", "unshacklarr-2026-01-01-0000.yaml", ""):
        try:
            web.saved_backup(bad)
            raise AssertionError(bad)
        except web.web.HTTPNotFound:
            pass

    # a rebuild: no password yet, the backup in the data folder
    web.write_config({"auth": {}})
    monkeypatch.setattr(web, "sonarr_status", lambda url, key: "4.0")
    monkeypatch.setattr(web, "check_unshackle", lambda settings: (_ for _ in ()).throw(web.UnshackleError("not back yet")))
    h = {"X-Unshackle": "1"}

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            listed = (await client.post("/api/setup/backups", json={"setup_code": "nope"}, headers=h)).status
            found = await (await client.post("/api/setup/backups", json={"setup_code": "code"}, headers=h)).json()
            sneaky = (await client.post("/api/setup/restore", json={"setup_code": "code", "password": "password2", "name": "../config.yaml"}, headers=h)).status
            short = (await client.post("/api/setup/restore", json={"setup_code": "code", "password": "short", "name": names[0]}, headers=h)).status
            r = await client.post("/api/setup/restore", json={"setup_code": "code", "password": "password2", "name": names[0]}, headers=h)
            again = (await client.post("/api/setup/restore", json={"setup_code": "code", "password": "password3", "name": names[0]}, headers=h)).status
            return listed, found, sneaky, short, r.status, await r.json(), again

    listed, found, sneaky, short, status, body, again = asyncio.run(go())
    assert listed == 403 and [b["name"] for b in found["saved"]] == names  # only with the setup code
    assert sneaky == 404 and short == 400
    assert status == 200 and body["series"] == 1 and body["sonarr"] == {"ok": True}
    assert body["unshackle"] == {"ok": False, "error": "not back yet"}  # restored all the same
    assert again == 403  # set up now: the setup routes are closed
    config = web.read_config()
    assert list(config["series"]) == [111] and config["quality_ladders"] == ladders and web.password_ok("password2", config["auth"]["password"])


def test_episode_links_are_taken_back_to_their_series(tmp_path, monkeypatch):
    # From #12, by mj23au
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    cases = {
        "https://www.channel4.com/programmes/taskmaster/on-demand/71670-001": "https://www.channel4.com/programmes/taskmaster",
        "https://www.itv.com/watch/bay-of-fires/10a5270/10a5270a0001": "https://www.itv.com/watch/bay-of-fires/10a5270",
        "https://www.channel5.com/show/cause-of-death/season-1/episode-1": "https://www.channel5.com/show/cause-of-death",
        "https://www.paramountplus.com/shows/mobland/video/abc/x": "https://www.paramountplus.com/shows/mobland/",
        "https://www.sbs.com.au/ondemand/tv-series/blue-lights/season-1/x": "https://www.sbs.com.au/ondemand/tv-series/blue-lights",
        "https://u.co.uk/shows/blue-lights/watch-online/6388360695112": "https://u.co.uk/shows/blue-lights/watch-online",
    }
    for given, series in cases.items():
        assert web.series_title(given) == series, given

    class Page:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data
    tree = {"programme": {"pid": "p0f2cz7f", "parent": {"programme": {"pid": "p0f2cxs1", "parent": {"programme": {"pid": "p0f2cxpr"}}}}}}
    monkeypatch.setattr(web.requests, "get", lambda url, timeout: Page(tree))
    assert web.series_title("https://www.bbc.co.uk/iplayer/episode/p0f2cz7f/blue-lights") == "https://www.bbc.co.uk/iplayer/episodes/p0f2cxpr"

    def down(url, timeout):
        raise web.requests.ConnectionError("offline")
    monkeypatch.setattr(web.requests, "get", down)
    kept = web.series_title("https://www.bbc.co.uk/iplayer/episode/b0abc123")
    assert kept == "https://www.bbc.co.uk/iplayer/episode/b0abc123" and "b0abc123" not in web.bbc_programmes  # asked again next time
    assert web.episode_link(kept)  # still one episode: a series link of the same service is preferred
    assert web.episode_link("https://play.hbomax.com/video/watch/7656258d-aaaa/x") and web.episode_link("https://www.bbc.co.uk/iplayer/episode/b0abc123")
    assert not web.episode_link("https://play.hbomax.com/show/86bc816f-aaaa")
