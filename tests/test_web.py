import importlib
import json


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
    page = (Path(__file__).parents[1] / "unshacklarr" / "static" / "index.html").read_text()
    script = tmp_path / "page.js"
    script.write_text(page[page.rindex("<script>") + 8 : page.rindex("</script>")])
    check = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
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
