import asyncio
import importlib
import json
import shutil
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import responses
from aiohttp import web as aioweb
from aiohttp.test_utils import TestClient, TestServer

SONARR = "http://sonarr:8989"
SINCE = "2026-01-01T00:00:00+00:00"


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


WEBHOOK = "https://discord.com/api/webhooks/1/token"  # Apprise reads a Discord webhook as is
SERVICES = [{"tag": "RTLP", "cli_params": [{"name": "movie", "kind": "option", "opts": ["--movie"], "is_flag": True}]},
            {"tag": "MLT", "cli_params": []}, {"tag": "CRAVE", "cli_params": []}, {"tag": "ATV", "cli_params": []}]


def load(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        "defaults: {--quality: '1080', --noatmos: true}\n"
        "series:\n"
        f"  111: {{service: RTLP, title: https://rtl/show, since: '{SINCE}', options: {{--noatmos: false}}, service_options: {{--movie: true}}}}\n"
        f"notifications: {{urls: ['{WEBHOOK}']}}\n"
    )
    monkeypatch.setenv("TZ", "Europe/Paris")
    monkeypatch.setenv("SONARR_URL", SONARR)
    monkeypatch.setenv("SONARR_API_KEY", "k")
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    monkeypatch.setenv("DOWNLOADS", str(tmp_path))  # the same folder for unshackle and Sonarr in tests
    import unshacklarr.sync
    module = importlib.reload(unshacklarr.sync)
    monkeypatch.setattr(module.UNSHACKLE, "services", lambda: SERVICES)
    monkeypatch.setattr(module.UNSHACKLE, "dl_config", lambda: {})
    monkeypatch.setattr(module.UNSHACKLE, "cdm_config", lambda: {})
    return module


def episode(tvdb, season, number, title="Show", aired=None):
    aired = aired or days_ago(2)  # recent enough for the automatic sync, late enough for a warning
    return {
        "id": season * 100 + number,
        "seriesId": 7,
        "seasonNumber": season,
        "episodeNumber": number,
        "airDateUtc": aired,
        "series": {"tvdbId": tvdb, "title": title, "qualityProfileId": 1},
    }


def fake_tools(calls, on_dl, mkv_titles):
    """Stand-in for unshackle serve (its /api/download payloads land in calls too), mkvmerge and mkvpropedit."""

    def run(cmd, **_):
        calls.append(cmd)
        if isinstance(cmd, dict):
            on_dl(Path(cmd["output_dir"]), cmd["wanted"][0])
            return []
        if cmd[:2] == ["mkvmerge", "-J"]:
            title = mkv_titles.get(Path(cmd[2]).name, "")
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"container": {"properties": {"title": title}}}))
        elif cmd[0] == "mkvmerge":
            Path(cmd[cmd.index("-o") + 1]).touch()
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    return run


def use_tools(monkeypatch, sync, run):
    monkeypatch.setattr(sync.subprocess, "run", run)
    monkeypatch.setattr(sync, "run_job", lambda payload, run_=None: run(payload))


def wanted(calls):
    return [c["wanted"][0] for c in calls if isinstance(c, dict)]


def sonarr_sees_folder(quality=3):
    """/manualimport lists the files Sonarr finds in the folder, as the real one does."""

    def callback(request):
        assert "seriesId" not in request.params  # with it, Sonarr would list the library instead
        folder = Path(request.params["folder"])
        q = quality[0] if isinstance(quality, list) else quality  # a list lets a test change it midway
        files = [
            {"path": str(f), "quality": {"quality": {"id": q, "name": f"Q{q}"}}, "languages": [{"id": 2}]}
            for f in folder.rglob("*.mkv")
        ]
        return 200, {}, json.dumps(files)

    responses.add_callback(responses.GET, f"{SONARR}/api/v3/manualimport", callback=callback)


def as_text(body) -> str:
    """A Discord card as text: "Kind: series episode", then its message and fields."""
    card = json.loads(body.decode() if isinstance(body, bytes) else body)["embeds"][0]
    return "\n".join([f"{card['author']['name']}: {card['title']}", card.get("description", ""),
                      *(f"{f['name']}: {f['value']}" for f in card.get("fields", []))])


def discord_messages():
    """What reached the Discord webhook, as text."""
    return [as_text(c.request.body) for c in responses.calls if c.request.url.split("?")[0] == WEBHOOK]


def sonarr_imports(status="completed", has_file=True, before=None):
    """Sonarr takes the ManualImport as command 1, runs it, and the episode then has file 900; before it,
    the episode is as `before` says (no file by default)."""
    responses.post(f"{SONARR}/api/v3/command", json={"id": 1})
    responses.get(f"{SONARR}/api/v3/command/1", json={"id": 1, "status": status, "message": "Import failed: disk full" if status != "completed" else "Completed"})

    def episode_now(request):
        imported = any(c.request.method == "POST" and c.request.url == f"{SONARR}/api/v3/command" for c in responses.calls)
        state = {"hasFile": has_file, "episodeFileId": 900 if has_file else 0} if imported else (before or {"hasFile": False, "episodeFileId": 0})
        return 200, {}, json.dumps(state)
    responses.add_callback(responses.GET, re.compile(re.escape(SONARR) + r"/api/v3/episode/\d+$"), callback=episode_now)


def commands():
    return [json.loads(c.request.body) for c in responses.calls if c.request.url == f"{SONARR}/api/v3/command"]


@responses.activate
def test_downloads_mapped_episodes_and_imports_them_by_id(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    records = [episode(111, 2, 5), episode(111, 2, 6), episode(999, 1, 1)]  # 999 is not mapped
    responses.get(f"{SONARR}/api/v3/calendar", json=records)
    sonarr_imports()
    responses.post(re.compile(re.escape(WEBHOOK)))
    sonarr_sees_folder()

    def dl(out, wanted):
        out.mkdir()
        if wanted == "S02E05":  # only E05 is on the service yet
            (out / "Service.Name.S02E05.Pilot.mkv").touch()

    calls = []
    use_tools(monkeypatch, sync, fake_tools(calls, dl, {}))

    assert sync.main() == 0
    assert wanted(calls) == ["S02E05", "S02E06"]
    # defaults, overridden per series, and the service's own option, typed as the API wants them
    assert calls[0] == {"movie": True, "quality": [1080], "service": "RTLP", "title_id": "https://rtl/show",
                        "wanted": ["S02E05"], "output_dir": str(tmp_path / "unshackle-111-S02E05")}
    assert not (tmp_path / "unshackle-111-S02E06").exists()  # retried next run

    folder = tmp_path / "unshackle-111-S02E05"
    assert [f.name for f in folder.iterdir()] == ["Show.S02E05.Pilot.mkv"]  # named after Sonarr's series
    [imported] = commands()
    assert imported["name"] == "ManualImport"
    assert imported["files"] == [{
        "path": str(folder / "Show.S02E05.Pilot.mkv"), "seriesId": 7, "episodeIds": [205],
        "quality": {"quality": {"id": 3, "name": "Q3"}}, "languages": [{"id": 2}], "releaseGroup": None,
    }]

    discord = discord_messages()
    assert len(discord) == 2
    # S02E05's import goes on while S02E06 is looked for: either may be told first
    assert any("Downloaded: Show S02E05" in m for m in discord)
    assert any("Still unavailable: Show S02E06" in m for m in discord)  # aired two days ago

    calls.clear()
    responses.calls.reset()
    assert sync.main() == 0
    assert wanted(calls) == ["S02E06"]  # E05 waits for Sonarr
    assert not discord_messages()  # E06 already warned about


@responses.activate
def test_maps_the_season_joins_parts_and_renames_after_sonarr(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    (tmp_path / "config.yaml").write_text(f"series:\n  222: {{service: MLT, title: https://mlt/koh, since: '{SINCE}', season_map: {{34: 29}}}}\n")
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(222, 34, 5, title="Koh-Lanta")])
    sonarr_imports()
    sonarr_sees_folder()

    def dl(out, wanted):  # Molotov ships S29E05 in two parts, under another show name
        (out / "Koh").mkdir(parents=True)
        (out / "Koh" / "Koh-Lanta.All.stars.S29E05.Part.2.Emission.FRENCH-GROUP.mkv").touch()
        (out / "Koh" / "Koh-Lanta.All.stars.S29E05.Part.1.All.Stars.FRENCH-GROUP.mkv").touch()

    calls = []
    titles = {"Koh-Lanta.All.stars.S29E05.All.Stars.FRENCH-GROUP.mkv": "Koh-Lanta : All stars S29E05.1 All Stars"}
    use_tools(monkeypatch, sync, fake_tools(calls, dl, titles))

    assert sync.main() == 0
    assert calls[0]["wanted"] == ["S29E05"]
    folder = tmp_path / "unshackle-222-S34E05" / "Koh"
    assert calls[1] == [
        "mkvmerge", "-q", "-o", str(folder / "Koh-Lanta.All.stars.S29E05.All.Stars.FRENCH-GROUP.mkv"),
        str(folder / "Koh-Lanta.All.stars.S29E05.Part.1.All.Stars.FRENCH-GROUP.mkv"),
        "+", str(folder / "Koh-Lanta.All.stars.S29E05.Part.2.Emission.FRENCH-GROUP.mkv"),
    ]
    final = folder / "Koh-Lanta.S34E05.FRENCH-GROUP.mkv"  # the parts' names are dropped
    assert list(folder.iterdir()) == [final]
    assert calls[-1] == ["mkvpropedit", "-q", str(final), "--edit", "info", "--set", "title=Koh-Lanta S34E05"]
    assert commands()[0]["files"][0]["episodeIds"] == [3405]


def test_service_episode_numbering(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    show = {"season_map": {34: 29}, "episode_offset": -1, "episode_map": {"S34E06": "S29E05.2"}}
    assert sync.service_episode(show, 34, 6) == "S29E05.2"  # the table wins, part included
    assert sync.service_episode(show, 34, 3) == "S29E02"  # season map and offset combine
    assert sync.service_episode(show, 1, 0) is None  # nothing before the first episode
    assert sync.service_episode({}, 2, 5) == "S02E05"


def split_episode(folder, names):
    folder.mkdir(parents=True)
    for part, name in enumerate(names, 1):
        (folder / f"Show.S01E05.Part.{part}.{name}.FRENCH-GROUP.mkv").touch()


def test_joining_off_keeps_the_parts_and_says_why(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    split_episode(tmp_path / "ep", ["One", "Two"])
    use_tools(monkeypatch, sync, fake_tools([], None, {}))
    try:
        sync.finalize(tmp_path / "ep", "S01E05", "Show", join_parts=False)
        raise AssertionError("expected a RuntimeError")
    except RuntimeError as e:
        assert "joining is off" in str(e)
    assert len(list((tmp_path / "ep").iterdir())) == 2  # nothing joined, nothing lost


def test_episode_name_modes(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    titles = {
        "Show.S01E05.One.FRENCH-GROUP.mkv": "Show S01E05.1 One",
        "Show.S01E05.Pilot.FRENCH-GROUP.mkv": "Show S01E05 Pilot",
    }
    use_tools(monkeypatch, sync, fake_tools([], None, titles))

    split_episode(tmp_path / "keep", ["One", "Two"])
    sync.finalize(tmp_path / "keep", "S01E05", "Show", episode_name="keep")
    assert [f.name for f in (tmp_path / "keep").iterdir()] == ["Show.S01E05.One.FRENCH-GROUP.mkv"]

    (tmp_path / "always").mkdir()
    (tmp_path / "always" / "Show.S01E05.Pilot.FRENCH-GROUP.mkv").touch()
    sync.finalize(tmp_path / "always", "S01E05", "Show", episode_name="always")
    assert [f.name for f in (tmp_path / "always").iterdir()] == ["Show.S01E05.FRENCH-GROUP.mkv"]

    (tmp_path / "joined").mkdir()
    (tmp_path / "joined" / "Show.S01E05.Pilot.FRENCH-GROUP.mkv").touch()
    sync.finalize(tmp_path / "joined", "S01E05", "Show")  # a single file keeps its name by default
    assert [f.name for f in (tmp_path / "joined").iterdir()] == ["Show.S01E05.Pilot.FRENCH-GROUP.mkv"]


@responses.activate
def test_automatic_sync_takes_new_episodes_only(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    records = [
        episode(111, 1, 1, aired="2020-05-01T20:00:00Z"),  # before the series got its service
        episode(111, 2, 1, aired=days_ago(20)),  # after it, but given up on
        episode(111, 2, 9),  # new: the only one to try
    ]
    responses.get(f"{SONARR}/api/v3/calendar", json=records)
    responses.get(f"{SONARR}/api/v3/episode", json=[records[0]])
    calls = []
    use_tools(monkeypatch, sync, fake_tools(calls, lambda out, wanted: out.mkdir(), {}))

    assert sync.main() == 0
    assert wanted(calls) == ["S02E09"]

    calls.clear()
    assert sync.main([101]) == 0  # picked by hand: taken, however old
    assert wanted(calls) == ["S01E01"]


def test_release_slot(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    slot = lambda time, aired, day=0: sync.release_slot({"release_time": time, "release_day": day}, {"airDateUtc": aired}).isoformat()
    # Koh-Lanta airs 21:15 in Paris, on Molotov at 23:30 the same evening
    assert slot("23:30", "2026-09-29T19:15:00Z") == "2026-09-29T23:30:00+02:00"
    # Scrubs airs 02:00 in Paris (US evening before): 09:00 that morning, not the day before
    assert slot("09:00", "2026-10-01T00:00:00Z") == "2026-10-01T09:00:00+02:00"
    # Brothers: TVDB says 06:00, Apple publishes at 03:00: the same morning
    assert slot("03:00", "2026-09-30T04:00:00Z") == "2026-09-30T03:00:00+02:00"
    assert slot("03:00", "2026-09-30T04:00:00Z", day=1) == "2026-10-01T03:00:00+02:00"
    # Has Fallen airs Monday 21:00 in Paris: 00:01 on its day is Monday 00:01, the day after Tuesday 00:01
    assert slot("00:01", "2026-09-28T19:00:00Z") == "2026-09-28T00:01:00+02:00"
    assert slot("00:01", "2026-09-28T19:00:00Z", day=1) == "2026-09-29T00:01:00+02:00"
    # Cyberpunk airs 22:00, on Netflix at 09:01 the day after
    assert slot("09:01", "2026-10-20T20:00:00Z", day=1) == "2026-10-21T09:01:00+02:00"
    assert sync.release_slot({}, {"airDateUtc": "2026-09-30T04:00:00Z"}) is None
    # A platform ahead of the channel: three days before it airs, at 03:00; without a time, from midnight
    assert slot("03:00", "2026-10-03T19:00:00Z", day=-3) == "2026-09-30T03:00:00+02:00"
    assert sync.release_slot({"release_day": -3}, {"airDateUtc": "2026-10-03T19:00:00Z"}).isoformat() == "2026-09-30T00:00:00+02:00"
    assert sync.release_slot({"release_day": 1}, {"airDateUtc": "2026-10-03T19:00:00Z"}) is None  # the day after needs a time
    assert sync.release_day({"release_day": -40}) == -sync.EARLY_DAYS_MAX


def test_an_episode_out_before_it_airs_joins_the_automatic_sync(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    ep = lambda i, tvdb, aired, **kw: {"id": i, "airDateUtc": aired, "hasFile": False, "monitored": True, "series": {"tvdbId": tvdb}, **kw}
    calendar = [ep(1, 7, "2026-10-02T19:00:00Z"),  # its service has it three days ahead: out since the 29th
                ep(2, 7, "2026-10-05T19:00:00Z"),  # out on the 2nd: not yet
                ep(3, 7, "2026-10-01T19:00:00Z", hasFile=True),  # already on disk
                ep(4, 8, "2026-10-01T19:00:00Z")]  # a series released the day it airs: Sonarr's wanted list has it then
    asked = {}
    monkeypatch.setattr(sync, "sonarr_get", lambda path, **q: asked.update(q) or calendar)
    config = {"series": {7: {"service": "DSNP", "release_day": -3}, 8: {"service": "DSNP"}}}
    assert [e["id"] for e in sync.early_episodes(config, now)] == [1]
    assert asked["end"].startswith("2026-10-04")  # three days ahead, and one more
    assert sync.early_episodes({"series": {8: {"service": "DSNP"}}}, now) == []  # none ahead: Sonarr isn't even asked


def test_one_download_per_episode_at_a_time(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    out = tmp_path / "unshackle-1-S01E01"
    with sync.episode_lock(out) as first:
        with sync.episode_lock(out) as second:
            assert (first, second) == (True, False)
    with sync.episode_lock(out) as again:
        assert again  # released once the first run is done


@responses.activate
def test_a_library_file_is_only_replaced_by_a_better_one(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    ep = {**episode(111, 2, 5), "hasFile": True, "episodeFileId": 77}
    responses.get(f"{SONARR}/api/v3/episode", json=[ep])
    responses.get(f"{SONARR}/api/v3/episodefile/77", json={"quality": {"quality": {"id": 3, "name": "WEBDL-1080p"}}})
    # Profile, lowest first: HDTV-720p, then a WEB 1080p group, then WEBDL-2160p
    responses.get(f"{SONARR}/api/v3/qualityprofile/1", json={"items": [
        {"quality": {"id": 4}}, {"items": [{"quality": {"id": 3}}, {"quality": {"id": 15}}]}, {"quality": {"id": 18}},
    ]})
    sonarr_imports(before={"hasFile": True, "episodeFileId": 77})
    responses.post(re.compile(re.escape(WEBHOOK)))

    def dl(out, wanted):
        out.mkdir()
        (out / "Show.S02E05.mkv").touch()

    use_tools(monkeypatch, sync, fake_tools([], dl, {}))
    folder = tmp_path / "unshackle-111-S02E05"

    quality = [15]
    sonarr_sees_folder(quality)  # WEBRip-1080p: same group as the library's file
    assert sync.main([205]) == 0  # not a failure: the library keeps its file
    assert not commands()
    [warning] = discord_messages()
    assert "Kept the existing file: Show S02E05" in warning
    assert (folder / "Show.S02E05.mkv").exists()  # waits in the downloads folder

    folder.rename(tmp_path / "gone")
    assert sync.main([205], replace=True) == 0  # asked for: replaced anyway
    assert [c["name"] for c in commands()] == ["ManualImport"]

    (tmp_path / "unshackle-111-S02E05").rename(tmp_path / "gone2")
    responses.calls.reset()
    quality[0] = 18  # 2160p beats the library's 1080p
    assert sync.main([205]) == 0
    assert [c["name"] for c in commands()] == ["ManualImport"]


def test_options_stack_defaults_then_service_then_series(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    config = {
        "defaults": {"--quality": "1080", "--proxy": "fr"},
        "service_defaults": {"CRAVE": {"options": {"--proxy": "ca"}, "service_options": {"--movie": True}}},
    }
    show = {"service": "CRAVE", "title": "https://crave.ca/x", "options": {"--quality": "2160"}}
    cmd = sync.unshackle_command(show, config, "S01E02", tmp_path / "out")
    assert cmd[cmd.index("S01E02") + 1 :] == ["--quality", "2160", "--proxy", "ca", "CRAVE", "--movie", "https://crave.ca/x"]
    payload = sync.download_request(show, config, "S01E02", tmp_path / "out")
    assert (payload["quality"], payload["proxy"], payload["movie"]) == ([2160], "ca", True)
    other = sync.download_request({**show, "service": "ATV"}, config, "S01E02", tmp_path / "out")
    assert other["proxy"] == "fr" and "movie" not in other  # another service only gets the defaults


def test_each_side_sees_the_downloads_folder_at_its_own_path(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    sync.apply_settings({**sync.load_settings(), "unshackle_downloads": "/downloads", "sonarr_downloads": "D:\\Sonarr\\in"})
    out = sync.episode_folder(episode(111, 1, 2))
    assert out == tmp_path / "unshackle-111-S01E02"
    assert sync.seen_by("unshackle_downloads", out) == "/downloads/unshackle-111-S01E02"
    assert sync.seen_by("sonarr_downloads", out) == "D:\\Sonarr\\in/unshackle-111-S01E02"


def test_a_job_is_followed_to_its_end(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    states = iter([{"status": "queued"}, {"status": "downloading", "progress": 50.0, "completed_tracks": 1, "total_tracks": 2},
                   {"status": "completed", "output_files": ["/downloads/x.mkv"]}])
    monkeypatch.setattr(sync.UNSHACKLE, "download", lambda payload: "job1")
    monkeypatch.setattr(sync.UNSHACKLE, "job", lambda job_id: next(states))
    assert sync.run_job({}, poll=0) == ["/downloads/x.mkv"]

    monkeypatch.setattr(sync.UNSHACKLE, "job", lambda job_id: {"status": "failed", "error_message": "No such title"})
    try:
        sync.run_job({}, poll=0)
        raise AssertionError("expected JobFailed")
    except sync.JobFailed as e:
        assert "No such title" in str(e)


@responses.activate
def test_history_has_the_download_and_the_episode_not_out_yet(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    records = [episode(111, 2, 5), episode(111, 2, 6)]
    responses.get(f"{SONARR}/api/v3/calendar", json=records)
    sonarr_imports()
    responses.post(re.compile(re.escape(WEBHOOK)))
    sonarr_sees_folder()

    def dl(out, wanted):
        out.mkdir()
        if wanted == "S02E05":
            (out / "Show.S02E05.mkv").touch()

    use_tools(monkeypatch, sync, fake_tools([], dl, {}))
    assert sync.main() == 0
    cards = sorted((json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json")), key=lambda c: c["sxxeyy"])
    assert [(c["sxxeyy"], c["kind"], c["outcome"]) for c in cards] == [("S02E05", "auto", "downloaded"), ("S02E06", "auto", "unavailable")]
    assert b"downloaded and imported by Sonarr" in (tmp_path / "runs" / f"{cards[0]['id']}.log").read_bytes()
    assert not sync.EpisodeRun.active  # none left running


def test_settings_come_from_the_config_then_the_environment(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)  # SONARR_URL and TZ come from the environment here
    assert (sync.SONARR, str(sync.LOCAL)) == (SONARR, "Europe/Paris")
    settings = sync.load_settings({"settings": {"sonarr_url": "http://other:8989/", "timezone": "", "auto_days": 3}})
    sync.apply_settings(settings)
    assert sync.SONARR == "http://other:8989"  # the config wins over the environment
    assert str(sync.LOCAL) == "Europe/Paris"  # an empty setting leaves the environment's
    assert sync.AUTO_DAYS == 3 and settings["sync_every_hours"] == 2  # and a missing one, the default


def test_unshackle_yaml_dl_section_comes_first_as_on_the_command_line(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    monkeypatch.setattr(sync.UNSHACKLE, "dl_config", lambda: {
        "best": True, "best_available": True, "sub_format": "srt", "workers": 16, "lang": None,
        "s_lang": ["fr", "en"], "quality": 720, "CRAVE": {"workers": 4}, "EXAMPLE": {"bitrate": "CBR"},
    })
    show = {"service": "CRAVE", "title": "https://crave.ca/x", "options": {"--quality": "1080"}}
    payload = sync.download_request(show, {}, "S01E02", tmp_path / "out")
    assert payload["best_available"] is True and payload["sub_format"] == "srt"
    assert payload["s_lang"] == ["fr", "en"]
    assert payload["workers"] == 4  # the service's own dl: values win over the general ones
    assert payload["quality"] == [1080]  # and Unshacklarr's options over both
    assert "best" not in payload and "lang" not in payload and "bitrate" not in payload


def test_a_failure_is_notified_by_its_cause_and_paths_as_sonarr_sees_them(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    job = {  # as serve reported a missing subtitle language
        "status": "failed",
        "error_message": "Worker exited with code 1: Download failed with exit code 1",
        "worker_stderr": "Download exited with code 1\nStdout: \n  ── Series: Koh-Lanta ──\n\nStderr: en not found in tracks\n\n"
                         "Worker failed with error: Download failed with exit code 1",
    }
    assert sync.job_cause(job) == "en not found in tracks"
    assert sync.job_cause({"status": "failed", "error_message": "Worker exited with code 1: series/1 failed: 404"}) == "series/1 failed: 404"

    sync.apply_settings({**sync.load_settings(), "sonarr_downloads": "/Syno/0.Temp"})
    records = [episode(111, 2, 5)]
    responses_add = responses.RequestsMock()
    with responses_add as r:
        r.get(f"{SONARR}/api/v3/calendar", json=records)
        r.post(re.compile(re.escape(WEBHOOK)))

        def fail(payload, run_=None):
            raise sync.JobFailed("all of it", "failed", sync.job_cause(job))

        monkeypatch.setattr(sync, "run_job", fail)
        assert sync.main() == 1
        [message] = [as_text(c.request.body) for c in r.calls if c.request.url.split("?")[0] == WEBHOOK]
    assert "en not found in tracks" in message and "Service: RTLP" in message and "Stdout" not in message
    assert sync.seen_by("sonarr_downloads", sync.episode_folder(records[0])) == "/Syno/0.Temp/unshackle-111-S02E05"


def test_a_run_keeps_every_track_it_saw_and_its_parts(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    ep, show = episode(111, 2, 5), {"service": "RTLP"}
    run = sync.EpisodeRun(ep, show, "manual", "S02E05")
    t = lambda label, p: {"label": label, "progress": p}
    states = iter([
        {"status": "queued"},
        {"status": "downloading", "current_title": "Show S02E05.1", "progress": 20, "track_progress": [t("video 1080p", 30), t("audio fr 2.0", 50)]},
        {"status": "downloading", "current_title": "Show S02E05.1", "progress": 40, "track_progress": [t("video 1080p", 60)]},
        {"status": "downloading", "current_title": "Show S02E05.2", "progress": 70, "track_progress": [t("video 1080p", 10)]},
        {"status": "completed", "output_files": []},
    ])
    monkeypatch.setattr(sync.UNSHACKLE, "download", lambda payload: "job1")
    monkeypatch.setattr(sync.UNSHACKLE, "job", lambda job_id: next(states))
    sync.run_job({}, run, poll=0)
    card = json.loads((tmp_path / "runs" / f"{run.id}.json").read_text())
    assert card["step"] == "downloading" and card["job_id"] == "job1" and card["episodeId"] == 205
    assert [{k: t[k] for k in ("label", "progress")} for t in card["tracks"]] == [
        {"label": "Part 1 · video 1080p", "progress": 100.0},  # gone from serve's list: done
        {"label": "Part 1 · audio fr 2.0", "progress": 100.0},
        {"label": "Part 2 · video 1080p", "progress": 100.0},  # the job completed: every track is done
    ]
    assert all(isinstance(t.get("took"), int) for t in card["tracks"])  # each done track: how long it took
    run.finish("failed", "en not found in tracks")
    card = json.loads((tmp_path / "runs" / f"{run.id}.json").read_text())
    assert (card["outcome"], card["cause"]) == ("failed", "en not found in tracks")


def test_two_tracks_named_alike_stay_two(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    run = sync.EpisodeRun(episode(111, 2, 5), {"service": "RTLP"}, "manual", "S02E05")
    t = lambda i, p: {"index": i, "label": "subtitle fr", "progress": p}
    states = iter([{"status": "downloading", "progress": 20, "track_progress": [t(2, 30), t(3, 50)]},
                   {"status": "completed", "output_files": []}])
    monkeypatch.setattr(sync.UNSHACKLE, "download", lambda payload: "job1")
    monkeypatch.setattr(sync.UNSHACKLE, "job", lambda job_id: next(states))
    sync.run_job({}, run, poll=0)
    card = json.loads((tmp_path / "runs" / f"{run.id}.json").read_text())
    assert [t["label"] for t in card["tracks"]] == ["subtitle fr", "subtitle fr"]


def test_the_output_has_unshackles_log_and_debug_only_in_debug_mode(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    run = sync.EpisodeRun(episode(111, 2, 5), {"service": "RMCP"}, "manual", "S02E05")
    states = iter([
        {"status": "downloading", "log_count": 2, "log": ["02:00:01 INFO RMCP: Getting tracks", "02:00:02 WARNING dl: slow segment"]},
        {"status": "downloading", "log_count": 5, "log": ["b", "c", "02:00:09 ERROR dl: gave up"]},  # 3 new, all sent
        {"status": "completed", "output_files": ["/downloads/x/Show.S02E05.mkv"], "log_count": 5, "log": []},
    ])
    monkeypatch.setattr(sync.UNSHACKLE, "download", lambda payload: "job1")
    monkeypatch.setattr(sync.UNSHACKLE, "job", lambda job_id: next(states))
    sync.trace("a debug line", debug=True)
    sync.run_job({}, run, poll=0)
    log = (tmp_path / "runs" / f"{run.id}.log").read_text()
    assert log.index("Getting tracks") < log.index("slow segment") < log.index("gave up")
    assert "\x1b[33m  02:00:02 WARNING" in log and "\x1b[31m  02:00:09 ERROR" in log
    assert "job job1" in log and "made 1 file: Show.S02E05.mkv" in log and "a debug line" not in log
    monkeypatch.setattr(sync, "DEBUG", True)
    sync.trace("a debug line", debug=True)
    run.finish("downloaded")
    sync.trace("after the end")  # no attempt going on: nowhere to write
    log = (tmp_path / "runs" / f"{run.id}.log").read_text()
    assert "debug · a debug line" in log and "after the end" not in log


def test_episodes_picked_together_show_as_queued_until_their_turn(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    seen = []
    def download(payload, run=None):
        seen.append((run.card["sxxeyy"], sorted(c["sxxeyy"] for c in sync.waiting.values())))
        raise sync.UnshackleError("no such title")
    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    sync.sync(sync.read_file(), {}, [episode(111, 2, 5), episode(111, 2, 6)], manual=True, kind="manual")
    assert seen == [("S02E05", ["S02E06"]), ("S02E06", [])]  # the second waits, visibly, then its turn comes
    assert sync.waiting == {}


def test_a_download_with_its_own_numbering_leaves_the_series_as_it_is(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    asked = []
    def download(payload, run=None):
        asked.append(payload["wanted"])
        raise sync.UnshackleError("no such title")
    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    config = sync.read_file()
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual", numbering={"episode_map": {"S02E05": "S09E01.2"}})
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert asked == [["S09E01.2"], ["S02E05"]]  # this download's numbering, then the series' own again
    assert "episode_map" not in sync.read_file()["series"][111]
    cards = [json.loads(f.read_text()) for f in sorted(sync.RUNS_DIR.glob("*.json"))]  # oldest first
    assert [c.get("numbering") for c in cards] == [{"episode_map": {"S02E05": "S09E01.2"}}, None]  # kept for a retry


def test_the_next_download_starts_while_the_one_before_is_imported(tmp_path, monkeypatch):
    import threading, time
    import time
    from pathlib import Path
    sync = load(tmp_path, monkeypatch)
    events, importing, go_on = [], threading.Event(), threading.Event()

    def download(payload, run=None):
        events.append(("download", payload["wanted"][0]))
        out = Path(payload["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / f"Show.{payload['wanted'][0]}.mkv").write_bytes(b"x")

    def import_episode(ep, out, replace=False):
        events.append(("import", ep["episodeNumber"]))
        if ep["episodeNumber"] == 5:
            importing.set()
            assert go_on.wait(5)  # a slow import in Sonarr
        for f in out.rglob("*.mkv"):
            f.unlink()

    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "download_request", lambda show, config, sx, out: {"service": "RTLP", "title_id": "t", "wanted": [sx], "output_dir": str(out)})
    monkeypatch.setattr(sync, "finalize", lambda *a, **k: 1)
    monkeypatch.setattr(sync, "check_audio", lambda *a: None)
    monkeypatch.setattr(sync, "import_episode", import_episode)
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    batch = threading.Thread(target=sync.sync, args=(sync.read_file(), {}, [episode(111, 2, 5), episode(111, 2, 6)]),
                             kwargs={"manual": True, "kind": "manual"})
    batch.start()
    assert importing.wait(5)
    deadline = time.time() + 5
    while ("download", "S02E06") not in events and time.time() < deadline:
        time.sleep(0.01)
    assert ("download", "S02E06") in events  # while S02E05 is still in Sonarr's import
    go_on.set()
    batch.join(5)
    assert events.index(("import", 5)) < events.index(("import", 6))  # finished in the order they came
    cards = [json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json")]
    assert sorted(c["outcome"] for c in cards) == ["downloaded", "downloaded"] and len({c["batch"] for c in cards}) == 1


def test_a_job_can_lose_a_queued_episode_or_stop(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    started = []

    def download(payload, run=None):
        started.append(payload["wanted"][0])
        if payload["wanted"][0] == "S02E05":
            sync.waiting.pop(206, None)
            sync.unqueued.add(206)  # S02E06 taken out of the queue from Activity
        if payload["wanted"][0] == "S02E07":  # then the whole job stopped: its queued ones cancelled
            for i, card in list(sync.waiting.items()):
                if card.get("batch") == run.card["batch"]:
                    sync.waiting.pop(i)
                    sync.unqueued.add(i)
        raise sync.UnshackleError("no such title")

    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "download_request", lambda show, config, sx, out: {"service": "RTLP", "title_id": "t", "wanted": [sx], "output_dir": str(out)})
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    sync.sync(sync.read_file(), {}, [episode(111, 2, n) for n in (5, 6, 7, 8)], manual=True, kind="manual")
    assert started == ["S02E05", "S02E07"]  # S02E06 skipped, S02E08 never started
    assert not sync.unqueued and not sync.waiting
    sync.cancel_queued({"id": "waiting-208", "series": "Show", "tvdbId": 111, "sxxeyy": "S02E08", "batch": "b", "waiting": True, "outcome": "running"})
    kept = [json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*S02E08.json")]
    assert [(c["outcome"], c["batch"], "waiting" in c, bool(c["ended"])) for c in kept] == [("cancelled", "b", False, True)]  # still in its job


def test_a_cancelled_episode_queued_again_keeps_its_place_or_goes_last(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    started = []

    def download(payload, run=None):
        sx, batch = payload["wanted"][0], run.card["batch"]
        started.append(sx)
        if sx == "S02E05":  # S02E06 and S02E07 cancelled, then S02E06 queued again before its turn
            for i in (206, 207):
                sync.waiting.pop(i)
                sync.unqueued.add(i)
            assert sync.add_to_job(batch, episode(111, 2, 6), {"service": "RTLP"})
        if sx == "S02E08":  # S02E07, skipped by now, queued again: it goes last
            assert sync.add_to_job(batch, episode(111, 2, 7), {"service": "RTLP"}) and 207 in sync.waiting
        raise sync.UnshackleError("no such title")

    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "download_request", lambda show, config, sx, out: {"service": "RTLP", "title_id": "t", "wanted": [sx], "output_dir": str(out)})
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    sync.sync(sync.read_file(), {}, [episode(111, 2, n) for n in (5, 6, 7, 8)], manual=True, kind="manual")
    assert started == ["S02E05", "S02E06", "S02E08", "S02E07"]
    assert not sync.running_jobs and not sync.waiting and not sync.unqueued
    assert not sync.add_to_job("gone", episode(111, 2, 6), {"service": "RTLP"})  # a job over: it starts on its own instead


def test_in_a_job_an_episode_has_one_card_whatever_its_tries(tmp_path, monkeypatch):
    from unshacklarr import sync
    monkeypatch.setattr(sync, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(sync, "prune_runs", lambda: None)
    ep = {"seasonNumber": 25, "episodeNumber": 1, "series": {"tvdbId": 84095, "title": "FELA"}, "id": 9}
    other = sync.EpisodeRun(ep, {"service": "RMCP"}, "manual", "S25E01", "job-a")
    other.finish("stopped")
    first = sync.EpisodeRun(ep, {"service": "RMCP"}, "manual", "S25E01", "job-b")
    first.finish("failed", "timeout")
    again = sync.EpisodeRun(ep, {"service": "RMCP"}, "manual", "S25E01", "job-b")  # retried in its job
    again.finish("downloaded")
    cards = sorted((c["batch"], c["outcome"], c.get("attempts", 1)) for c in (json.loads(p.read_text()) for p in tmp_path.glob("*.json")))
    assert cards == [("job-a", "stopped", 1), ("job-b", "downloaded", 2)]  # job-a keeps its own card


def test_a_paused_job_ends_its_download_then_waits(tmp_path, monkeypatch):
    import threading, time
    import time
    sync = load(tmp_path, monkeypatch)
    started = []

    def download(payload, run=None):
        started.append(payload["wanted"][0])
        if len(started) == 1:
            sync.pause_job(run.card["batch"], True)
        raise sync.UnshackleError("no such title")

    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "download_request", lambda show, config, sx, out: {"service": "RTLP", "title_id": "t", "wanted": [sx], "output_dir": str(out)})
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    job = threading.Thread(target=sync.sync, args=(sync.read_file(), {}, [episode(111, 2, 5), episode(111, 2, 6)]), kwargs={"manual": True, "kind": "manual"})
    job.start()
    time.sleep(1.5)
    assert started == ["S02E05"] and sync.waiting[206]["paused"]  # paused: S02E06 waits
    sync.pause_job(next(iter(sync.running_jobs)), False)
    job.join(5)
    assert started == ["S02E05", "S02E06"] and not sync.paused_jobs


def test_a_discord_card_has_colour_fields_and_poster():
    from unshacklarr import sync
    card = sync.discord_payload("success", "Downloaded: Show S02E05", "Imported by Sonarr.",
                                {"service": "RMCP", "size": "1.4 GB", "took": "38 s", "poster": "https://img/p.jpg"})["embeds"][0]
    assert (card["author"]["name"], card["title"], card["color"]) == ("Downloaded", "Show S02E05", 0x3FB97A)
    assert [(f["name"], f["value"]) for f in card["fields"]] == [("Service", "RMCP"), ("Size", "1.4 GB"), ("Took", "38 s")]
    assert card["thumbnail"]["url"] == "https://img/p.jpg"
    assert "fields" not in sync.discord_payload("error", "Unshacklarr sync stopped", "boom")["embeds"][0]
    assert sync.DISCORD_WEBHOOK.match("discord://1234/abc-DEF") and not sync.DISCORD_WEBHOOK.match("tgram://bot/chat")


def test_finalize_reports_joining_then_renaming(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    use_tools(monkeypatch, sync, fake_tools([], None, {}))
    steps = []
    split_episode(tmp_path / "two", ["One", "Two"])
    assert sync.finalize(tmp_path / "two", "S01E05", "Show", on_step=lambda st, n: steps.append((st, n))) == 2
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "Show.S01E05.Pilot.mkv").touch()
    assert sync.finalize(tmp_path / "one", "S01E05", "Show", on_step=lambda st, n: steps.append((st, n))) == 1
    assert steps == [("joining", 2), ("renaming", 2), ("renaming", 1)]  # one file: nothing to join


@responses.activate
def test_an_import_sonarr_did_not_do_is_a_failure(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(111, 2, 5)])
    responses.post(re.compile(re.escape(WEBHOOK)))
    sonarr_sees_folder()
    sonarr_imports(status="failed")

    def dl(out, wanted):
        out.mkdir()
        (out / "Show.S02E05.mkv").touch()

    use_tools(monkeypatch, sync, fake_tools([], dl, {}))
    assert sync.main() == 1
    [message] = discord_messages()
    assert "Not imported: Show S02E05" in message and "disk full" in message
    assert (tmp_path / "unshackle-111-S02E05" / "Show.S02E05.mkv").exists()  # kept for a second try


@responses.activate
def test_an_imported_episode_leaves_no_folder(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(111, 2, 5)])
    responses.post(re.compile(re.escape(WEBHOOK)))
    sonarr_sees_folder()
    sonarr_imports()

    def dl(out, wanted):
        (out / "sub").mkdir(parents=True)
        (out / "sub" / "Show.S02E05.mkv").touch()

    def sonarr_moves(ep, out, replace=False):  # what the real import does: the file leaves the folder
        for f in out.rglob("*.mkv"):
            f.unlink()

    use_tools(monkeypatch, sync, fake_tools([], dl, {}))
    monkeypatch.setattr(sync, "import_episode", sonarr_moves)
    assert sync.main() == 0
    assert not (tmp_path / "unshackle-111-S02E05").exists()


def test_a_network_hiccup_is_tried_again_a_real_failure_is_not(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    tries, waits = [], []

    def job(payload, run=None):
        tries.append(1)
        if len(tries) < 3:
            raise sync.JobFailed("x", "failed", "HTTPSConnectionPool: Read timed out.")
        return ["/downloads/x.mkv"]

    monkeypatch.setattr(sync, "run_job", job)
    assert sync.run_job_retrying({}, sleep=waits.append) == ["/downloads/x.mkv"]
    assert (len(tries), waits) == (3, [30, 120])

    tries.clear(); waits.clear()
    monkeypatch.setattr(sync, "run_job", lambda payload, run=None: tries.append(1) or (_ for _ in ()).throw(
        sync.JobFailed("x", "failed", "series/1 failed: 404 not found")))
    try:
        sync.run_job_retrying({}, sleep=waits.append)
        raise AssertionError("expected JobFailed")
    except sync.JobFailed:
        pass
    assert (len(tries), waits) == (1, [])  # a 404 stays a 404


@responses.activate
def test_a_login_failure_points_to_the_cookies(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(111, 2, 5)])
    responses.post(re.compile(re.escape(WEBHOOK)))

    def refused(payload, run=None):
        raise sync.JobFailed("x", "failed", "series/1 failed: 401 Unauthorized")

    monkeypatch.setattr(sync, "run_job", refused)
    assert sync.main() == 1
    [message] = discord_messages()
    assert "Login failed on RTLP: Show S02E05" in message and "Settings, Cookies" in message


def test_a_download_without_the_first_language_asked_for_is_not_imported(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    config = {"defaults": {"--a-lang": "fr,orig,en"}}
    assert sync.wanted_audio({"service": "X"}, config) == "fr"
    assert sync.wanted_audio({"service": "X", "options": {"--a-lang": "orig,en"}}, config) == "en"
    assert sync.wanted_audio({"service": "X"}, {}) is None
    (tmp_path / "ep").mkdir()
    (tmp_path / "ep" / "Show.S01E01.mkv").touch()

    def mkvmerge(langs):
        def run(cmd, **_):
            tracks = [{"type": "video"}] + [{"type": "audio", "properties": {"language": l}} for l in langs]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"tracks": tracks}))
        return run

    monkeypatch.setattr(sync.subprocess, "run", mkvmerge(["fre", "eng"]))
    sync.check_audio(tmp_path / "ep", ["fr"])  # fre is French: fine
    monkeypatch.setattr(sync.subprocess, "run", mkvmerge(["eng"]))
    try:
        sync.check_audio(tmp_path / "ep", ["fr"])
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "No fr audio" in str(e) and "eng" in str(e)
    monkeypatch.setattr(sync.subprocess, "run", mkvmerge([]))
    sync.check_audio(tmp_path / "ep", ["fr"])  # no tags at all: nothing to go by, let Sonarr judge
    monkeypatch.setattr(sync.subprocess, "run", mkvmerge(["fre"]))
    sync.check_audio(tmp_path / "ep", [sync.wanted_audio({"service": "X", "options": {"--a-lang": "fr-CA"}}, {})])  # a region asked: the file's tag has none
    monkeypatch.setattr(sync.subprocess, "run", mkvmerge(["kor"]))
    sync.check_audio(tmp_path / "ep", ["ko"])  # the older three-letter tag of any language the page knows, not only French


def test_the_release_time_is_learnt_from_what_was_seen(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)  # Europe/Paris
    assert sync.suggest_release([]) is None
    seen = [  # aired 21:15 local; not out at 23:20, out at 23:31 / 23:34 / 23:33
        {"aired": "2026-09-01T19:15:00Z", "not_yet": "2026-09-01T21:20:00Z", "available": "2026-09-01T21:31:00Z"},
        {"aired": "2026-09-08T19:15:00Z", "not_yet": "2026-09-08T21:21:00Z", "available": "2026-09-08T21:34:00Z"},
        {"aired": "2026-09-15T19:15:00Z", "available": "2026-09-15T21:33:00Z"},
    ]
    assert sync.suggest_release(seen) == {"time": "23:25", "day": 0, "episodes": 3, "between": ["23:21", "23:31"]}
    next_day = [{"aired": "2026-09-01T02:00:00Z", "available": "2026-09-02T01:07:00Z"}]  # out at 03:07 the day after
    assert sync.suggest_release(next_day) == {"time": "03:05", "day": 1, "episodes": 1, "between": [None, "03:07"]}

    ep = episode(111, 2, 5, aired="2026-09-01T19:15:00Z")
    out = tmp_path / "unshackle-111-S02E05"
    sync.note_availability(ep, out, False, sync.parse_time("2026-09-01T21:20:00Z"))
    sync.note_availability(ep, out, True, sync.parse_time("2026-09-01T21:31:00Z"))
    sync.note_availability(ep, out, False, sync.parse_time("2026-09-02T08:00:00Z"))  # later tries change nothing
    entry = json.loads((tmp_path / "availability.json").read_text())["unshackle-111-S02E05"]
    assert (entry["not_yet"], entry["available"]) == ("2026-09-01T21:20:00+00:00", "2026-09-01T21:31:00+00:00")


def test_leftovers_are_listed_and_the_old_ones_cleaned(tmp_path, monkeypatch):
    import os
    sync = load(tmp_path, monkeypatch)
    for name, age_days in (("unshackle-111-S02E05", 20), ("unshackle-111-S02E06", 1)):
        (tmp_path / name).mkdir()
        f = tmp_path / name / "Show.mkv"
        f.write_bytes(b"x" * 10)
        os.utime(f, (time_now := __import__("time").time(), time_now - age_days * 86400))
    (tmp_path / "unshackle-111-S02E07").mkdir()  # empty: nothing waits
    (tmp_path / "someone-else").mkdir()
    assert [l["folder"] for l in sync.leftovers()] == ["unshackle-111-S02E05", "unshackle-111-S02E06"]
    assert sync.leftovers()[0]["sxxeyy"] == "S02E05" and sync.leftovers()[0]["size"] == 10
    assert sync.clean_leftovers() == ["unshackle-111-S02E05"]  # 14 days by default
    assert not (tmp_path / "unshackle-111-S02E05").exists() and (tmp_path / "unshackle-111-S02E06").exists()
    for bad in ("../x", "unshackle-1-S01E01/../../x", ""):
        try:
            sync.leftover_path(bad)
            raise AssertionError(bad)
        except RuntimeError:
            pass


def test_the_sync_waits_for_the_release_time():
    from datetime import datetime, timezone
    from unshacklarr import sync
    show = {"since": "2026-09-01T00:00:00+00:00", "release_time": "09:00", "release_day": 0}
    ep = {"airDateUtc": "2026-09-25T02:00:00Z"}  # 04:00 in Paris; the service publishes at 09:00
    slot = sync.release_slot(show, ep)
    assert not sync.wanted_automatically(show, ep, slot.astimezone(timezone.utc).replace(hour=slot.astimezone(timezone.utc).hour - 1))
    assert sync.wanted_automatically(show, ep, slot.astimezone(timezone.utc))
    assert sync.wanted_automatically({"since": show["since"]}, ep, datetime(2026, 9, 25, 3, tzinfo=timezone.utc))  # no release time: from airing


def test_checks_fold_into_the_next_attempt(tmp_path, monkeypatch):
    from unshacklarr import sync
    monkeypatch.setattr(sync, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(sync, "prune_runs", lambda: None)
    ep = {"seasonNumber": 13, "episodeNumber": 1, "series": {"tvdbId": 250487, "title": "AHS"}, "id": 7}
    for i in range(3):
        run = sync.EpisodeRun(ep, {"service": "DSNP"}, "burst")
        run.say(f"check {i}")
        run.finish("unavailable", "Not on DSNP yet")
    cards = sorted(tmp_path.glob("*.json"))
    assert len(cards) == 1  # one line in the history
    card = sync.json.loads(cards[0].read_text())
    assert card["checks"] == 3 and card["outcome"] == "unavailable" and card["first_check"] < card["started"]
    log = cards[0].with_suffix(".log").read_text()
    assert log.index("check 0") < log.index("check 1") < log.index("check 2")  # every check, oldest first
    run = sync.EpisodeRun(ep, {"service": "DSNP"}, "auto")
    run.finish("downloaded")
    card = sync.json.loads(sorted(tmp_path.glob("*.json"))[0].read_text())
    assert len(list(tmp_path.glob("*.json"))) == 1 and card["outcome"] == "downloaded" and card["checks"] == 4


def test_a_retry_takes_the_place_of_the_failed_attempts(tmp_path, monkeypatch):
    from unshacklarr import sync
    monkeypatch.setattr(sync, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(sync, "prune_runs", lambda: None)
    ep = {"seasonNumber": 25, "episodeNumber": 13, "series": {"tvdbId": 79315, "title": "FELA"}, "id": 9}
    for outcome in ("failed", "stopped"):
        run = sync.EpisodeRun(ep, {"service": "MLT"}, "manual")
        run.say(outcome)
        run.finish(outcome, "no title found")
    run = sync.EpisodeRun(ep, {"service": "MLT"}, "retry")
    run.say("third")
    run.finish("downloaded")
    cards = list(tmp_path.glob("*.json"))
    assert len(cards) == 1  # one line in the history, updated
    card = sync.json.loads(cards[0].read_text())
    assert card["outcome"] == "downloaded" and card["attempts"] == 3
    log = cards[0].with_suffix(".log").read_text()
    assert log.index("failed") < log.index("stopped") < log.index("third")
    other = sync.EpisodeRun(ep, {"service": "MLT"}, "manual")  # anything but a retry: a line of its own
    other.finish("failed")
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_a_click_on_an_episode_waiting_in_downloads_says_why_nothing_happens(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    ep = episode(111, 2, 5)
    (tmp_path / "unshackle-111-S02E05").mkdir()  # a kept download, not imported
    config = sync.read_file()
    assert sync.sync(config, {}, [ep]) == 0 and not (tmp_path / "runs").exists()  # the automatic sync: quiet
    sync.sync(config, {}, [ep], manual=True, kind="manual")
    cards = [json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json")]
    assert [(c["outcome"], c["cause"]) for c in cards] == [("failed", "A download of this episode already waits in the downloads folder")]


def test_a_login_code_is_sent_once_and_leaves_the_card_once_done(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(sync, "notify", lambda settings, level, title, message, action=None: sent.append((level, title, message)))
    run = sync.EpisodeRun(episode(111, 2, 5), {"service": "DSNP"}, "auto")
    need = {"service": "DSNP", "message": "Link this device to your Disney+ account", "url": "https://www.disneyplus.com/start",
            "code": "ABCD1234", "expires_at": sync.time.time() + 600}
    sync.ask_for_action(run, need)
    sync.ask_for_action(run, need)  # the next polls: the same code, not sent again
    assert run.card["action"]["code"] == "ABCD1234" and len(sent) == 1
    assert sent[0][1] == "DSNP needs you: ABCD1234" and "https://www.disneyplus.com/start" in sent[0][2] and "within 10 min" in sent[0][2]
    sync.ask_for_action(run, {**need, "code": "EFGH5678"})  # expired, a new one: sent
    assert len(sent) == 2
    sync.ask_for_action(run, None)
    assert "action" not in run.card
    run.finish("failed")


def test_the_inbox_keeps_every_notification_and_its_action(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    monkeypatch.setattr(sync.PUSH, "send", lambda *a, **k: 0)
    sync.notify({"warning": False}, "warning", "Late: Show S01E01", "Not on DSNP yet.")  # off for Discord, kept here
    sync.notify({}, "warning", "DSNP needs you: 1234", "Open https://example.com and enter 1234", action={"code": "1234", "url": "https://example.com"})
    box = sync.read_inbox()
    assert [i["title"] for i in box["items"]] == ["DSNP needs you: 1234", "Late: Show S01E01"]  # newest first
    sync.inbox_resolve("1234")
    assert sync.read_inbox()["items"][0]["action"]["done"] is True
    sync.notify({}, "error", "Failed", "x", action={"code": "9", "url": "https://example.com"})
    sync.change_inbox(lambda b: b.update(items=[i for i in b["items"] if i.get("action") and not i["action"].get("done")]))
    assert [i["title"] for i in sync.read_inbox()["items"]] == ["Failed"]  # clear keeps what still waits for you


def test_a_question_is_asked_once_kept_in_the_bell_and_done_once_answered(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    monkeypatch.setattr(sync.PUSH, "send", lambda *a, **k: 0)
    monkeypatch.setattr(sync, "notification_urls", lambda settings: [])  # the bell only: no network in a test
    run = sync.EpisodeRun(episode(111, 2, 5), {"service": "DSNP"}, "auto")
    question = "Disney+ sent a code to the account's e-mail: enter it"
    sync.ask_for_input(run, question)
    sync.ask_for_input(run, question)  # the next polls: the same question, not sent again
    items = sync.read_inbox()["items"]
    assert len(items) == 1 and items[0]["action"]["kind"] == "input" and items[0]["action"]["run"] == run.id
    assert run.card["prompt"]["text"] == question and not items[0]["action"].get("done")
    sync.ask_for_input(run, None)  # answered: the job no longer asks
    assert "prompt" not in run.card and sync.read_inbox()["items"][0]["action"]["done"] is True
    run.finish("failed")


@responses.activate
def test_an_episode_in_two_parts_waits_for_both(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    (tmp_path / "config.yaml").write_text(
        f"series:\n  222: {{service: MLT, title: https://mlt/koh, since: '{SINCE}', season_map: {{34: 29}}, parts: 2}}\n")
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(222, 34, 5, title="Koh-Lanta", aired=days_ago(0.01))])
    out_parts = [["Koh-Lanta.S29E05.mkv"]]  # at the release time, Molotov has only the first part, unnamed

    def dl(out, wanted):
        out.mkdir(parents=True, exist_ok=True)
        for name in out_parts[0]:
            (out / name).touch()

    calls = []
    use_tools(monkeypatch, sync, fake_tools(calls, dl, {}))
    assert sync.main() == 0
    assert not (tmp_path / "unshackle-222-S34E05").exists()  # nothing kept, nothing imported
    assert not commands()
    card = json.loads(next((tmp_path / "runs").glob("*.json")).read_text())
    assert card["outcome"] == "unavailable" and "(1 of 2 parts)" in card["cause"]

    # The second part is out: both are joined and imported as one episode.
    out_parts[0] = ["Koh-Lanta.S29E05.Part.1.mkv", "Koh-Lanta.S29E05.Part.2.mkv"]
    sonarr_imports()
    sonarr_sees_folder()
    assert sync.main() == 0
    assert any(c[:3] == ["mkvmerge", "-q", "-o"] for c in calls if isinstance(c, list))
    assert commands()[0]["files"][0]["episodeIds"] == [3405]


def test_parts_in_counts_part_files_or_one_whole_file(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "X.S01E01.mkv").touch()
    assert sync.parts_in(tmp_path / "a") == 1
    (tmp_path / "b").mkdir()
    for n in (1, 2):
        (tmp_path / "b" / f"X.S01E01.Part.{n}.mkv").touch()
    assert sync.parts_in(tmp_path / "b") == 2


def test_a_download_shows_its_command_where_it_runs_and_its_proxy(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    request = {"quality": [1080], "no_atmos": True, "proxy": "http://user:pw@proxy.example:8080", "service": "RTLP",
               "movie": True, "title_id": "https://www.rtl.fr/some show", "wanted": ["S02E08"], "output_dir": "/dl", "debug": True}
    setup = sync.setup_of(request)
    assert setup["command"] == ("unshackle dl --quality 1080 --noatmos --proxy 'http://***@proxy.example:8080'"
                                " RTLP --movie 'https://www.rtl.fr/some show' -w S02E08")
    assert setup["proxy"] == "http://***@proxy.example:8080" and setup["via"] == "this computer"
    assert sync.setup_of({**request, "remote": True, "server": "eu", "no_proxy": True})["via"] == "remote server eu"
    assert sync.setup_of({**request, "no_proxy": True})["proxy"] == "none"



def test_a_series_own_schedule_dates_its_episodes(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    eps = [{"id": 100 + n, "seasonNumber": 1, "episodeNumber": n} for n in range(1, 7)] + [{"id": 1, "seasonNumber": 0, "episodeNumber": 1}]
    # Cat's Eyes: from S01E02 on Wednesday 30 September at 20:39, weekly on Wednesdays, two on 7 October, a break on the 14th
    plan = {"from": "S01E02", "start": "2026-09-30", "time": "20:39", "every": "weekly", "days": [2], "per_evening": 1,
            "evenings": {"2026-10-07": 2, "2026-10-14": 0}}
    dates = {i: at.isoformat() for i, at in sync.broadcast_dates({"broadcast": plan}, eps).items()}
    assert dates == {102: "2026-09-30T20:39:00+02:00", 103: "2026-10-07T20:39:00+02:00", 104: "2026-10-07T20:39:00+02:00",
                     105: "2026-10-21T20:39:00+02:00", 106: "2026-10-28T20:39:00+01:00"}  # E01 and the special keep Sonarr's
    daily = sync.broadcast_dates({"broadcast": {**plan, "every": "daily", "evenings": {}}}, eps)
    assert daily[106].date().isoformat() == "2026-10-04"
    assert sync.broadcast_dates({}, eps) == {}


def test_an_episode_not_aired_yet_waits_without_a_release_time(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    show = {"service": "X", "since": "2026-01-01T00:00:00+00:00"}
    assert not sync.wanted_automatically(show, {"airDateUtc": "2026-09-30T18:39:00Z"}, now)  # tonight: not yet
    assert sync.wanted_automatically(show, {"airDateUtc": "2026-09-29T18:39:00Z"}, now)
    assert sync.wanted_automatically({**show, "release_day": -1}, {"airDateUtc": "2026-09-30T18:39:00Z"}, now)  # out the day before


def test_a_schedule_starts_on_its_first_evening_whatever_the_day(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    eps = [{"id": n, "seasonNumber": 1, "episodeNumber": n} for n in (1, 2)]
    plan = {"from": "S01E01", "start": "2026-09-30", "time": "20:39", "every": "weekly", "days": [0]}  # a Wednesday, then Mondays
    assert [at.date().isoformat() for at in sync.broadcast_dates({"broadcast": plan}, eps).values()] == ["2026-09-30", "2026-10-05"]


def test_each_address_gets_its_levels_and_quiet_hours_hold_messages_for_one_summary(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    monkeypatch.setattr(sync.PUSH, "send", lambda *a, **k: 0)
    got = []
    monkeypatch.setattr(sync, "send_to", lambda url, level, title, message, details=None: got.append((url, level, title)) or (None if "ok" in url else "401 Unauthorized"))
    settings = {"targets": [{"url": "ntfy://ok", "levels": ["success", "warning", "error"]}, {"url": "tgram://bad/1", "levels": ["error"]}]}
    sync.notify(settings, "success", "Downloaded: Show S01E01", "x")
    assert [u for u, *_ in got] == ["ntfy://ok"]  # Telegram only takes failures
    sync.notify(settings, "error", "Failed: Show S01E02", "x")
    assert sync.read_json(sync.SENT_FILE, [])[0]["to"] == [{"app": "ntfy", "ok": True}, {"app": "Telegram", "ok": False, "error": "401 Unauthorized"}]
    got.clear()
    quiet = {**settings, "quiet": {"from": "00:00", "to": "23:59"}}
    monkeypatch.setattr(sync, "in_quiet_hours", lambda s, now=None: bool(s.get("quiet")))
    sync.notify(quiet, "success", "Downloaded: Show S01E03", "x")
    sync.notify(quiet, "error", "Failed: Show S01E04", "x")
    assert got == [] and sync.send_held(quiet) is False  # held while quiet
    assert sync.send_held(settings) is True  # over: one message, as bad as the worst of them, to who takes that
    assert [(u, lv, t) for u, lv, t in got] == [("ntfy://ok", "error", "During the quiet hours: 2 messages"), ("tgram://bad/1", "error", "During the quiet hours: 2 messages")]
    assert sync.send_held(settings) is False


def test_quiet_hours_over_midnight(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    at = lambda hm: datetime.fromisoformat(f"2026-09-30T{hm}:00").replace(tzinfo=sync.LOCAL)
    quiet = {"quiet": {"from": "23:00", "to": "08:00"}}
    assert sync.in_quiet_hours(quiet, at("23:30")) and sync.in_quiet_hours(quiet, at("07:59"))
    assert not sync.in_quiet_hours(quiet, at("08:00")) and not sync.in_quiet_hours({}, at("02:00"))


def test_a_missed_job_status_waits_instead_of_downloading_twice(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    started, answers = [], iter([sync.UnshackleError("unshackle serve is unreachable: Read timed out"),
                                 {"status": "completed", "output_files": []}])

    def job(_):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(sync.UNSHACKLE, "download", lambda payload: started.append(payload) or "j1")
    monkeypatch.setattr(sync.UNSHACKLE, "job", job)
    monkeypatch.setattr(sync.time, "sleep", lambda _: None)
    assert sync.run_job_retrying({"service": "X"}) == [] and len(started) == 1  # the same job, followed to its end


def test_shown_commands_hide_a_proxy_password_and_hand_written_dates_count_as_utc(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    assert sync.to_args({"--proxy": "http://user:secret@proxy:8080"}) == ["--proxy", "http://***@proxy:8080"]
    assert sync.parse_time("2026-09-24").tzinfo is not None  # `since: 2026-09-24` typed in config.yaml


@responses.activate
def test_a_job_that_fails_after_its_first_part_imports_nothing(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(111, 2, 5)])
    responses.post(re.compile(re.escape(WEBHOOK)))

    def dl(out, wanted):
        out.mkdir(parents=True, exist_ok=True)
        (out / "Show.S02E05.Part.1.mkv").touch()
        raise sync.JobFailed("part 2: no licence", "failed", "No licence for part 2")

    use_tools(monkeypatch, sync, fake_tools([], dl, {}))
    assert sync.main() == 1
    assert not commands()  # part 1 alone is not the episode
    assert (tmp_path / "unshackle-111-S02E05" / "Show.S02E05.Part.1.mkv").exists()  # kept, to look at
    card = json.loads(next((tmp_path / "runs").glob("*.json")).read_text())
    assert card["outcome"] == "failed" and card["cause"] == "No licence for part 2"


def test_an_offset_before_the_first_episode_asks_for_none_and_a_schedule_dates_only_its_own(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    assert sync.service_episode({"episode_offset": -1}, 1, 1) is None and sync.service_episode({"episode_offset": -1}, 1, 2) == "S01E01"
    eps = [{"id": i, "seasonNumber": 1 + (i > 2), "episodeNumber": i} for i in (1, 2, 3, 4)]
    plan = {"broadcast": {"start": "2026-10-06", "time": "21:00", "from": "S02E03", "every": "daily"}}
    dated = sync.broadcast_dated([({}, eps, sync.broadcast_dates(plan, eps))])
    assert dated == {3, 4}  # S01 keeps Sonarr's dates: the sync still finds its missing episodes
    plan["broadcast"]["evenings"] = {datetime(2026, 10, 6).date(): 2}  # typed unquoted in config.yaml: a date key
    assert len(set(sync.broadcast_dates(plan, eps).values())) == 1


def test_a_link_planted_in_the_downloads_folder_is_never_written_through(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    out = tmp_path / "unshackle-111-S02E05"
    out.mkdir()
    for n in (1, 2):
        (out / f"Show.S02E05.Part.{n}.mkv").write_bytes(b"x")
    (out / "Show.S02E05.mkv").symlink_to(victim)  # where mkvmerge -o would write the joined file
    monkeypatch.setattr(sync.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("mkvmerge ran")))
    try:
        sync.finalize(out, "S02E05", "Show")
        raise AssertionError("finalize went on")
    except RuntimeError as e:
        assert "symbolic link" in str(e)
    assert victim.read_text() == "precious" and (out / "Show.S02E05.Part.1.mkv").exists()


@responses.activate
def test_a_better_file_sonarr_got_during_the_download_is_kept(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(111, 2, 5)])
    responses.get(f"{SONARR}/api/v3/episodefile/77", json={"quality": {"quality": {"id": 18, "name": "WEBDL-2160p"}}})
    responses.get(f"{SONARR}/api/v3/qualityprofile/1", json={"items": [{"quality": {"id": 3}}, {"quality": {"id": 18}}]})
    sonarr_imports(before={"hasFile": True, "episodeFileId": 77})  # missing when listed, a 2160p file by the import
    responses.post(re.compile(re.escape(WEBHOOK)))
    sonarr_sees_folder([3])  # ours: WEBDL-1080p

    def dl(out, wanted):
        out.mkdir()
        (out / "Show.S02E05.mkv").touch()

    use_tools(monkeypatch, sync, fake_tools([], dl, {}))
    assert sync.main() == 0
    assert not commands()  # Sonarr's 2160p stays


@responses.activate
def test_a_network_error_never_shows_a_discord_webhook_token(tmp_path, monkeypatch):
    import requests as rq
    sync = load(tmp_path, monkeypatch)
    hook = "https://discord.com/api/webhooks/123/SECRETTOKEN"
    responses.post(hook, body=rq.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='discord.com', port=443): Max retries exceeded with url: /api/webhooks/123/SECRETTOKEN?wait=true"))
    error = sync.send_to(hook, "error", "t", "m")
    assert error and "SECRETTOKEN" not in error
    assert sync.no_credentials("unreachable at http://admin:hunter2@sonarr:8989") == "unreachable at http://***@sonarr:8989"


def test_a_service_with_no_cdm_at_all_is_refused_but_no_drm_and_the_default_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    sync = importlib.reload(unshacklarr.sync)
    for cdm, refused in (({"default": "phone_l3"}, False), ({"NF": "phone_l3"}, False), ({"DSNP": "none", "NF": "x"}, False),
                         ({"NF": "phone_l3"}, False), ({"CBC": "phone_l3"}, True), ({}, False)):
        monkeypatch.setattr(sync.UNSHACKLE, "cdm_config", lambda cdm=cdm: cdm)
        tag = "DSNP" if "DSNP" in cdm else "NF"
        assert bool(sync.no_cdm(tag)) is refused, cdm
    monkeypatch.setattr(sync.UNSHACKLE, "cdm_config", lambda: {"CBC": "phone_l3"})
    try:
        sync.download_request({"service": "NF", "title": "x"}, {}, "S01E01", tmp_path)
        raise AssertionError("a download with no CDM at all went through")
    except ValueError as e:
        assert "No CDM for NF" in str(e)


def test_an_episode_its_number_misses_is_found_by_its_title(tmp_path, monkeypatch):
    from pathlib import Path
    sync = load(tmp_path, monkeypatch)
    asked, listed, imported = [], [], []

    def download(payload, run=None):
        asked.append(payload["wanted"][0])
        if payload["wanted"][0] == "S01E07":  # the service numbers the whole series as one season
            out = Path(payload["output_dir"])
            out.mkdir(parents=True, exist_ok=True)
            (out / "Show.S01E07.mkv").write_bytes(b"x")

    def find(show, ep):
        listed.append(ep["id"])
        return {"available": {201: {"service": "S01E07", "match": "title"}}, "listed": {"S01E07"}}  # S02E01 by its title; S02E02 not out yet

    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "download_request", lambda show, config, sx, out: {"service": "RTLP", "title_id": "t", "wanted": [sx], "output_dir": str(out)})
    monkeypatch.setattr(sync, "find_by_title", find)
    monkeypatch.setattr(sync, "finalize", lambda *a, **k: 1)
    monkeypatch.setattr(sync, "check_audio", lambda *a: None)
    monkeypatch.setattr(sync, "import_episode", lambda ep, out, replace=False: imported.append(ep["id"]))
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    sync.sync(sync.read_file(), {}, [episode(111, 2, 1), episode(111, 2, 2)], manual=True, kind="manual")
    assert asked == ["S02E01", "S01E07", "S02E02"]
    assert listed == [201]  # the series listed once: S02E02 is looked up in that same list
    assert imported == [201]
    cards = {c["sxxeyy"]: c for c in (json.loads(f.read_text()) for f in sync.RUNS_DIR.glob("*.json"))}
    assert cards["S02E01"]["serviceEpisode"] == "S01E07"
    assert cards["S02E01"]["numbering"]["episode_map"] == {"S02E01": "S01E07"}  # a retry asks for S01E07 again
    assert cards["S02E02"]["outcome"] == "unavailable" and "numbering" not in cards["S02E02"]


def test_a_bursts_failures_make_one_card_told_once(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    told, causes = [], iter(["key not allowed", "key not allowed", "key not allowed", "no space left"])

    def download(payload, run=None):
        cause = next(causes)
        raise sync.JobFailed(cause, "failed", cause)

    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "download_request", lambda show, config, sx, out: {"service": "RTLP", "title_id": "t", "wanted": [sx], "output_dir": str(out)})
    monkeypatch.setattr(sync, "notify", lambda settings, level, title, message, **k: told.append(message.split("\n")[0]))
    monkeypatch.setattr(sync, "by_title", lambda *a: None)
    for kind in ("burst", "burst", "auto", "burst"):  # the release burst, the next sync, a burst again
        sync.sync(sync.read_file(), {}, [episode(111, 2, 5)], kind=kind)
    cards = [json.loads(f.read_text()) for f in sync.RUNS_DIR.glob("*.json")]
    assert len(cards) == 1 and cards[0]["attempts"] == 4  # one line in Activity, its tries counted
    assert told == ["key not allowed", "no space left"]  # each cause told once


def test_an_episode_in_none_of_the_accepted_languages_waits_undownloaded(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    assert sync.speaks({"en-US"}, ["fr", "en"]) and sync.speaks({"fre"}, ["fr"]) and not sync.speaks({"fr-FR"}, ["fr-CA"])
    assert sync.speaks({"fr"}, ["fr-ca"])  # --a-lang fr-CA: the file's plain French tag may be it, as before
    config = sync.read_file()
    assert sync.accepted_audio(config["series"][111], {**config, "settings": {"audio_accept": "fr, en"}}) == ["fr", "en"]
    config["series"][111]["audio_accept"] = "fr,en"
    tracks = {"S02E05": [{"language": "en"}], "S02E06": [{"language": "de"}, {"language": "de-AT"}]}  # what serve lists
    asked = []
    monkeypatch.setattr(sync.UNSHACKLE, "call", lambda method, path, json=None, **_: asked.append(json) or {"episodes": [{"audio": tracks[json["wanted"][0]]}]})
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    downloads, warnings = [], []
    monkeypatch.setattr(sync, "run_job_retrying", lambda payload, run=None: downloads.append(payload["wanted"][0]))
    monkeypatch.setattr(sync, "notify", lambda settings, level, title, *a, **k: warnings.append((level, title)))
    for _ in range(2):  # tried again at the next sync: still none, told once
        sync.sync(config, {}, [episode(111, 2, 6)], manual=True, kind="manual")
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert downloads == ["S02E05"]  # English is one of them: downloaded; German only: never
    assert warnings[:1] == [("warning", "No fr or en audio yet: Show S02E06")] and len([w for w in warnings if "audio" in w[1]]) == 1
    cards = [json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json")]
    assert {c["outcome"] for c in cards if c["sxxeyy"] == "S02E06"} == {"unavailable"}
    assert "de, de-AT" in next(c["cause"] for c in cards if c["sxxeyy"] == "S02E06")
    assert all(set(a) <= {"service", "title_id", "wanted", "profile", "proxy", "no_proxy", "cdm_type", "movie"} for a in asked)  # only what the official serve takes


def test_nothing_is_downloaded_without_room_and_a_click_says_why(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    usage = lambda free_gb: (lambda path: shutil._ntuple_diskusage(100e9, 100e9 - free_gb * 1e9, free_gb * 1e9))
    monkeypatch.setattr(sync.shutil, "disk_usage", usage(2))
    assert sync.free_space_problem() == ""  # off unless set: an update changes nothing
    sync.apply_settings({**sync.SETTINGS, "min_free_gb": 5})
    assert "Only 2.0 GB free" in sync.free_space_problem() and "5 GB" in sync.free_space_problem()
    downloads = []
    monkeypatch.setattr(sync, "run_job_retrying", lambda payload, run=None: downloads.append(payload["wanted"][0]))
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    sync.sync(sync.read_file(), {}, [episode(111, 2, 5)])  # the automatic sync: quiet, the health alert tells it
    assert downloads == [] and not (tmp_path / "runs").exists()
    sync.sync(sync.read_file(), {}, [episode(111, 2, 5)], manual=True, kind="manual")
    card = json.loads(next((tmp_path / "runs").glob("*.json")).read_text())
    assert downloads == [] and card["outcome"] == "failed" and "Only 2.0 GB free" in card["cause"]
    sync.apply_settings({**sync.SETTINGS, "min_free_gb": 0})  # 0: never checked
    assert sync.free_space_problem() == ""
    monkeypatch.setattr(sync.shutil, "disk_usage", usage(50))
    sync.apply_settings({**sync.SETTINGS, "min_free_gb": 5})
    assert sync.free_space_problem() == ""


@responses.activate
def test_the_missing_episodes_come_from_the_calendar_and_the_library_is_asked_once(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    have = {**episode(111, 2, 4), "hasFile": True}
    off = {**episode(111, 2, 6), "monitored": False}
    shelved = {**episode(111, 2, 7), "series": {**episode(111, 2, 7)["series"], "monitored": False}}
    responses.get(f"{SONARR}/api/v3/calendar", json=[have, episode(111, 2, 5), off, shelved])
    assert [e["id"] for e in sync.missing_episodes()] == [205]  # a file, an unmonitored episode or series: not wanted
    asked = responses.calls[-1].request.params
    assert asked["unmonitored"] == "false" and asked["end"] > asked["start"]
    library = responses.get(f"{SONARR}/api/v3/series", json=[{"id": 7, "tvdbId": 111}])
    assert sync.sonarr_series({111})[111]["id"] == 7 and sync.sonarr_series({111})[111]["id"] == 7
    assert library.call_count == 1  # kept: the whole library is one big answer
    clock = [sync.time.monotonic()]
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock[0])
    sync._series_by_tvdb[""] = (clock[0], {111: {"id": 7}})  # the main Sonarr's entry
    sync.sonarr_series({222})  # a series added since, but asked less than a minute ago: not again yet
    assert library.call_count == 1
    clock[0] += 61
    sync.sonarr_series({222})
    assert library.call_count == 2


def test_a_refused_login_is_tried_again_with_the_fallback_profiles(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    config = sync.read_file()
    config["series"][111]["fallback_profiles"] = "default, alt, backup"  # its own (default) is never a fallback
    assert sync.fallback_profiles(config["series"][111], config) == ["alt", "backup"]
    tried = []

    def download(payload, run=None):
        profile = payload.get("profile") or "default"
        tried.append(profile)
        if profile == "backup":
            out = Path(payload["output_dir"])
            out.mkdir(parents=True, exist_ok=True)
            (out / "Show.S02E05.mkv").touch()
            return
        raise sync.UnshackleError("unshackle serve: 401 Unauthorized: your session has expired")
    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    for name in ("finalize", "check_audio", "import_episode"):
        monkeypatch.setattr(sync, name, lambda *a, **k: None)
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual")
    card = json.loads(next((tmp_path / "runs").glob("*.json")).read_text())
    assert tried == ["default", "alt", "backup"] and card["outcome"] == "downloaded" and card["profile"] == "backup"

    tried.clear()
    monkeypatch.setattr(sync, "run_job_retrying", lambda payload, run=None: tried.append(payload.get("profile") or "default") or (_ for _ in ()).throw(sync.UnshackleError("No such title")))
    sync.sync(config, {}, [episode(111, 2, 6)], manual=True, kind="manual")
    assert tried == ["default"]  # not a login: another profile would not help


def test_an_episode_without_full_subtitles_in_a_required_language_waits(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    config = sync.read_file()
    config["series"][111]["subs_accept"] = "fr"
    subtitles = {"S02E05": [{"language": "fr"}], "S02E06": [{"language": "fr", "forced": True}, {"language": "en"}]}
    monkeypatch.setattr(sync.UNSHACKLE, "call", lambda method, path, json=None, **_: {"episodes": [{"audio": [{"language": "en"}], "subtitles": subtitles[json["wanted"][0]]}]})
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    downloads, warnings = [], []
    monkeypatch.setattr(sync, "run_job_retrying", lambda payload, run=None: downloads.append(payload["wanted"][0]))
    monkeypatch.setattr(sync, "notify", lambda settings, level, title, *a, **k: warnings.append(title))
    sync.sync(config, {}, [episode(111, 2, 6)], manual=True, kind="manual")
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert downloads == ["S02E05"] and [w for w in warnings if "subtitles" in w] == ["No fr subtitles yet: Show S02E06"]  # forced French lines are not subtitles
    cards = {json.loads(p.read_text())["sxxeyy"]: json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json")}
    assert cards["S02E06"]["outcome"] == "unavailable" and "(it has en)" in cards["S02E06"]["cause"]

    (tmp_path / "ep").mkdir()
    (tmp_path / "ep" / "Show.S01E01.mkv").touch()
    tracks = [{"type": "subtitles", "properties": {"language": "fre", "forced_track": True}}, {"type": "subtitles", "properties": {"language": "eng"}}]
    monkeypatch.setattr(sync.subprocess, "run", lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"tracks": tracks})))
    try:
        sync.check_subs(tmp_path / "ep", ["fr"])
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "No fr subtitles" in str(e) and "it has en" in str(e)
    tracks[0]["properties"]["forced_track"] = False
    sync.check_subs(tmp_path / "ep", ["fr"])  # full French subtitles: fine


def test_a_file_without_the_preferred_audio_is_noticed_before_sonarr_moves_it(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    (tmp_path / "ep").mkdir()
    (tmp_path / "ep" / "Show.S01E01.mkv").touch()
    audio = ["eng"]
    monkeypatch.setattr(sync.subprocess, "run", lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
        {"tracks": [{"type": "audio", "properties": {"language": a}} for a in audio]})))
    show, config = {"service": "X", "audio_prefer": "fr"}, {}
    assert sync.lacks_preferred(tmp_path / "ep", show, config) == "fr"  # English only: watched for French
    audio[:] = ["fre", "eng"]
    assert sync.lacks_preferred(tmp_path / "ep", show, config) == ""
    audio[:] = []
    assert sync.lacks_preferred(tmp_path / "ep", show, config) == ""  # no tags: nothing to go by
    assert sync.lacks_preferred(tmp_path / "ep", {"service": "X"}, {}) == ""  # none preferred


def test_a_quality_ladder_takes_its_first_step_the_episode_has_and_nothing_outside_it(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    config = sync.read_file()
    config["series"][111]["ladder"] = "1080p"  # H.264 then H.265, 1080p then 720p, SDR only
    video = {"S02E05": [{"height": 2160, "codec": "HEVC", "range": "DV"}, {"height": 1080, "codec": "HEVC", "range": "SDR", "bitrate": 5000},
                        {"height": 720, "codec": "AVC", "range": "SDR"}],
             "S02E06": [{"height": 2160, "codec": "HEVC", "range": "DV"}, {"height": 480, "codec": "AVC", "range": "SDR"}]}
    asked = []
    monkeypatch.setattr(sync.UNSHACKLE, "call", lambda method, path, json=None, **_: asked.append(json) or {"episodes": [{"video": video[json["wanted"][0]]}]})
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    downloads = []
    monkeypatch.setattr(sync, "run_job_retrying", lambda payload, run=None: downloads.append(payload))
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert asked[0]["range_"] == ["SDR"] and asked[0]["vcodec"] == ["AVC", "HEVC"]  # a service lists only what is asked for
    assert [(d["quality"], d["vcodec"], d["range"]) for d in downloads] == [([1080], ["HEVC"], ["SDR"])]

    sync.sync(config, {}, [episode(111, 2, 6)], manual=True, kind="manual")
    assert len(downloads) == 1  # 2160p DV and 480p are outside the ladder: nothing downloaded
    cards = {json.loads(p.read_text())["sxxeyy"]: json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json")}
    assert cards["S02E06"]["outcome"] == "failed" and "None of the quality ladder 1080p's steps" in cards["S02E06"]["cause"]
    assert "480p AVC SDR" in cards["S02E06"]["cause"]

    config["settings"] = {"quality_ladder": "1080p"}
    config["series"][111]["ladder"] = "off"  # a series can turn its service's or the settings' ladder off
    assert sync.ladder_of(config["series"][111], config) is None
    del config["series"][111]["ladder"]
    assert sync.ladder_of(config["series"][111], config)["name"] == "1080p"


@responses.activate
def test_a_download_only_series_is_downloaded_never_imported_nor_cleaned(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    (tmp_path / "config.yaml").write_text((tmp_path / "config.yaml").read_text().replace("options: {--noatmos: false}", "download_only: true, options: {--noatmos: false}"))
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(111, 2, 5)])
    responses.post(re.compile(re.escape(WEBHOOK)))

    def dl(out, wanted):
        out.mkdir(parents=True)
        (out / "Show.S02E05.mkv").write_bytes(b"x")

    imported = []
    use_tools(monkeypatch, sync, fake_tools([], dl, {}))
    monkeypatch.setattr(sync, "import_episode", lambda *a, **k: imported.append(a))
    assert sync.main() == 0
    assert not imported and (tmp_path / "unshackle-111-S02E05" / "Show.S02E05.mkv").exists()
    card = next(json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json"))
    assert card["outcome"] == "kept" and card["cause"].startswith("Download only")
    assert "Downloaded, not imported: Show S02E05" in discord_messages()[0]
    assert sync.clean_leftovers(now=datetime.now().timestamp() + 30 * 86400) == []  # waits for an import by hand
    assert sync.download_only({"download_only": False}, {"settings": {"download_only": True}}) is False  # the series wins
    assert sync.download_only({}, {"settings": {"download_only": True}}) is True


def test_each_service_downloads_with_its_own_unshackle_server(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    sync.apply_settings({**sync.SETTINGS, "backends": [{"name": "vpn", "url": "http://vpn:8786", "api_key": "k2", "downloads": "/vpn/dl"}]})
    vpn = sync.BACKENDS["vpn"]
    monkeypatch.setattr(sync.UNSHACKLE, "services", lambda: SERVICES)
    monkeypatch.setattr(vpn, "services", lambda: [{"tag": "CRAVE", "cli_params": []}, {"tag": "NF", "cli_params": []}])
    monkeypatch.setattr(vpn, "dl_config", lambda: {})
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    config = {"service_defaults": {"CRAVE": {"backend": "vpn"}}}
    assert sync.backend_for("CRAVE", config) is vpn and sync.backend_for("RTLP", config) is sync.UNSHACKLE
    assert sync.backend_for("NF", config) is vpn  # only that server has it
    assert [s["tag"] for s in sync.all_services() if s.get("backend") == "vpn"] == ["NF"]
    out = tmp_path / "unshackle-111-S02E05"
    request = sync.download_request({"service": "CRAVE", "title": "x"}, config, "S02E05", out)
    assert request["output_dir"] == "/vpn/dl/unshackle-111-S02E05"  # the folder as that server sees it
    assert sync.setup_of(request, vpn)["via"] == "vpn (vpn)"

    started = []
    monkeypatch.setattr(vpn, "download", lambda payload: started.append(payload) or "j1")
    monkeypatch.setattr(vpn, "job", lambda job_id: {"status": "completed", "output_files": []})
    run = sync.EpisodeRun(episode(111, 2, 5), {"service": "CRAVE"}, "manual")
    run.card["backend"] = "vpn"
    assert sync.run_job(request, run) == [] and len(started) == 1


def test_the_rename_keeps_the_resolution_and_a_title_starting_with_a_number(tmp_path, monkeypatch):
    # From #9, by mj23au: S17E03.1080p was read as part 1080 of S17E03, and renamed S17E03p
    sync = load(tmp_path, monkeypatch)
    found = lambda name: sync.SXXEYY.search(name).group()  # noqa: E731
    assert found("Show.S17E03.1080p.ALL4.WEB-DL.mkv") == "S17E03"
    assert found("Show.S01E01.576i.mkv") == "S01E01"
    assert found("Show.S02E01.1000.Days.1080p.mkv") == "S02E01"  # a title, not a part
    assert found("Show.S29E05.Part.2.1080p.mkv") == "S29E05.Part.2"  # a part is still one
    out = tmp_path / "ep"
    out.mkdir()
    (out / "Bake.Off.S17E03.1080p.ALL4.WEB-DL.mkv").touch()
    use_tools(monkeypatch, sync, fake_tools([], lambda *a: None, {}))
    sync.finalize(out, "S17E03", "Bake Off")
    assert [f.name for f in out.iterdir()] == ["Bake.Off.S17E03.1080p.ALL4.WEB-DL.mkv"]


def test_a_ladder_takes_widescreen_tracks_by_their_16_9_height_and_its_language_order(tmp_path, monkeypatch):
    # From #11, by mj23au: a cropped 1920x800 film is 1080p to Unshackle's own --quality
    sync = load(tmp_path, monkeypatch)
    config = sync.read_file()
    config["series"][111]["ladder"] = "1080p"
    config["quality_ladders"] = [{**sync.BUILTIN_LADDERS[0], "audio": ["en-AU", "en"], "subtitles": ["fr"]}]
    tracks = {"video": [{"height": 800, "width": 1920, "codec": "AVC", "range": "SDR"}],
              "audio": [{"language": "en-GB"}, {"language": "de"}], "subtitles": [{"language": "fr", "forced": True}, {"language": "en"}]}
    monkeypatch.setattr(sync.UNSHACKLE, "call", lambda method, path, json=None, **_: {"episodes": [tracks]})
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    downloads = []
    monkeypatch.setattr(sync, "run_job_retrying", lambda payload, run=None: downloads.append(payload))
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert downloads[0]["quality"] == [800]  # its own height: Unshackle takes the track by it exactly
    assert downloads[0]["a_lang"] == ["en-GB"]  # no en-AU: en takes en-GB
    assert "s_lang" not in downloads[0]  # forced French lines are not subtitles
    config["series"][111]["options"] = {"--lang": "fr,en"}  # the series asks for languages itself: the ladder's order stays out
    sync.sync(config, {}, [episode(111, 2, 6)], manual=True, kind="manual")
    assert "a_lang" not in downloads[1]
    assert sync.eq_height({"width": 3840, "height": 1920}) == 2160 and sync.eq_height({"height": 720}) == 720
    assert sync.eq_height({"width": 1918, "height": 802}) == 1080  # Apple's: 1078 at 16:9, within 2% of 1080
    assert sync.eq_height({"width": 1280, "height": 534}) == 720 and sync.eq_height({"height": 900}) == 900
    assert sync.first_language(["pt-BR", "pt"], [{"language": "pt-PT"}, {"language": "pt-BR"}]) == "pt-BR"


def test_a_server_that_is_down_keeps_its_services_and_another_takes_the_main_folder(tmp_path, monkeypatch):
    # From #13, by mj23au
    sync = load(tmp_path, monkeypatch)
    sync.apply_settings({**sync.SETTINGS, "unshackle_downloads": "/srv/dl", "backends": [{"name": "vpn", "url": "http://vpn:8786", "api_key": "k"}]})
    vpn = sync.BACKENDS["vpn"]
    assert vpn.settings["unshackle_downloads"] == "/srv/dl"  # empty: as the main serve sees it
    monkeypatch.setattr(vpn, "services", lambda: [{"tag": "RTLP", "cli_params": []}])

    def down():
        raise sync.UnshackleError("unshackle serve is unreachable")
    monkeypatch.setattr(sync.UNSHACKLE, "services", down)
    assert sync.backend_for("RTLP", {}) is sync.UNSHACKLE  # down is not "doesn't have it": no other cookies, proxy or CDM
    monkeypatch.setattr(sync.UNSHACKLE, "services", lambda: [{"tag": "MLT"}])
    assert sync.backend_for("RTLP", {}) is vpn


def test_a_number_its_title_gives_to_another_episode_is_not_downloaded(tmp_path, monkeypatch):
    # From #12, by mj23au: a same-named series elsewhere, seasons numbered apart
    sync = load(tmp_path, monkeypatch)
    listing = {"available": {}, "listed": {"S02E05", "S03E01"}, "titled": {"S02E05"}}  # another episode's title gives S02E05
    monkeypatch.setattr(sync, "find_by_title", lambda show, ep: listing)
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    asked = []
    monkeypatch.setattr(sync, "run_job_retrying", lambda payload, run=None: asked.append(payload["wanted"][0]))
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    sync.sync(sync.read_file(), {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert asked == []  # listed under its number, but its title says another episode
    card = next(json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json"))
    assert card["outcome"] == "failed" and "is another episode, by its title" in card["cause"]

    sync.title_matches.clear()
    listing["available"] = {205: {"service": "S03E01", "match": "title"}}  # its title puts it there
    sync.sync(sync.read_file(), {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert asked == ["S03E01"]

    sync.title_matches.clear()
    listing["available"] = {205: {"service": "S02E05"}}  # its number is right
    sync.sync(sync.read_file(), {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert asked == ["S03E01", "S02E05"]

    sync.title_matches.clear()
    listing.update(available={}, titled=set())  # its title merely differs (no translation yet): downloaded by its number
    sync.sync(sync.read_file(), {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert asked == ["S03E01", "S02E05", "S02E05"]

@responses.activate
def test_another_sonarr_gets_its_own_copy_with_its_own_ladder(tmp_path, monkeypatch):
    # From #10, by mj23au: sonarr for 1080p, sonarr-4k for 4K, the same series set up once
    sync = load(tmp_path, monkeypatch)
    sync.apply_settings({**sync.SETTINGS, "sonarrs": [{"name": "sonarr-4k", "url": "http://sonarr-4k:8989", "api_key": "k4",
                                                        "downloads": "/4k-sees", "quality_ladder": "4K, then 1080p"}]})
    responses.get(f"{SONARR}/api/v3/calendar", json=[episode(111, 2, 5)])
    responses.get("http://sonarr-4k:8989/api/v3/calendar", json=[{**episode(111, 2, 5), "id": 9905}, episode(999, 1, 1)])  # 999: not set up here
    responses.post(re.compile(re.escape(WEBHOOK)))
    asked, imported = [], []

    def dl(out, wanted):
        out.mkdir(parents=True)
        (out / "Show.S02E05.mkv").write_bytes(b"x")

    def fake_import(ep, out, replace=False):
        imported.append((sync.sonarr_url(), sync.seen_by("sonarr_downloads", out), ep["id"]))
        for f in out.rglob("*.mkv"):
            f.unlink()

    listed = []

    def listing(show, ep):  # each Sonarr's episode ids are its own: one listing each
        listed.append(ep["id"])
        return {"available": {ep["id"]: {"service": "S02E05"}}, "listed": {"S02E05"}, "titled": {"S02E05"}}

    use_tools(monkeypatch, sync, fake_tools([], dl, {}))
    monkeypatch.setattr(sync, "find_by_title", listing)
    monkeypatch.setattr(sync, "import_episode", fake_import)
    monkeypatch.setattr(sync, "apply_ladder", lambda show, config, request: asked.append((sync.instance() or {}).get("name"), ) or
                        asked.append(sync.ladder_of(show, config) and sync.ladder_of(show, config)["name"]))
    assert sync.main() == 0
    assert imported == [(SONARR, str(tmp_path / "unshackle-111-S02E05"), 205),
                        ("http://sonarr-4k:8989", "/4k-sees/unshackle-sonarr-4k-111-S02E05", 9905)]  # each into its own Sonarr
    assert asked == [None, None, "sonarr-4k", "4K, then 1080p"]  # its ladder over the series' (none here)
    assert listed == [205, 9905]  # the main Sonarr's list never answers for the other's ids
    cards = sorted((json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json")), key=lambda c: c["id"])
    assert [(c.get("instance"), c["series"], c["outcome"]) for c in cards] == [(None, "Show", "downloaded"), ("sonarr-4k", "Show · sonarr-4k", "downloaded")]
    m = sync.FOLDER.fullmatch("unshackle-sonarr-4k-111-S02E05")
    assert m.groups() == ("sonarr-4k", "111", "02", "05") and sync.FOLDER.fullmatch("unshackle-111-S02E05").group(1) is None


# From #10, by mj23au: another Sonarr picked by hand, its own series list, Catch up
FOURK = {"name": "sonarr-4k", "url": "http://sonarr4k:8989", "api_key": "k4", "downloads": "", "quality_ladder": "4K only",
         "download_only": None}


def with_4k(sync):
    sync.apply_settings({**sync.SETTINGS, "sonarrs": [FOURK]})
    return sync.SONARRS["sonarr-4k"]


@responses.activate
def test_another_sonarr_needs_a_ladder_chosen_and_waits_until_it_is(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    import unshacklarr.web
    web = importlib.reload(unshacklarr.web)
    try:  # a new one: chosen before it is saved
        web.check_sonarrs([{**FOURK, "quality_ladder": ""}], [], "http://sonarr:8989")
        raise AssertionError("a new instance without a ladder chosen was kept")
    except aioweb.HTTPBadRequest as e:
        assert "Choose a quality ladder" in e.text
    assert web.check_sonarrs([FOURK], [], "http://sonarr:8989")[0]["quality_ladder"] == "4K only"
    same = web.check_sonarrs([{**FOURK, "quality_ladder": "series"}], [], "http://sonarr:8989")[0]
    assert sync.instance_config(same, {"series": {1: {"ladder": "1080p"}}})["series"][1]["ladder"] == "1080p"  # each series' own
    # one saved before (a test build): kept as it is, so other settings still save, but paused and told once
    old = {**FOURK, "quality_ladder": ""}
    assert web.check_sonarrs([{**old, "api_key": ""}], [old], "http://sonarr:8989")[0]["quality_ladder"] == ""
    told = []
    monkeypatch.setattr(sync, "notify", lambda settings, level, title, *a, **k: told.append(title))
    monkeypatch.setattr(sync, "missing_episodes", lambda: (_ for _ in ()).throw(AssertionError("asked Sonarr for a paused instance")))
    assert sync.sync_instance(old, sync.read_file(), {}) == 0 and sync.sync_instance(old, sync.read_file(), {}) == 0
    assert told == ["Sonarr sonarr-4k has no quality ladder chosen"]
    sync.apply_settings({**sync.SETTINGS, "sonarrs": [old]})
    try:
        sync.main([5], sonarr="sonarr-4k")
        raise AssertionError("a hand-picked download went to a paused instance")
    except RuntimeError as e:
        assert "Choose a quality ladder" in str(e)


def test_each_sonarr_keeps_its_own_series_list(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    inst = with_4k(sync)
    libraries = {"": [{"tvdbId": 1, "id": 10}], "sonarr-4k": [{"tvdbId": 1, "id": 77}]}
    monkeypatch.setattr(sync, "sonarr_get", lambda path, **_: libraries[(sync.instance() or {}).get("name", "")])
    assert sync.sonarr_series({1})[1]["id"] == 10
    with sync.on_instance(inst):
        assert sync.sonarr_series({1})[1]["id"] == 77  # not the main Sonarr's id, cached a moment ago
    assert sync.sonarr_series({1})[1]["id"] == 10


def test_episodes_picked_in_another_sonarr_download_for_it_with_its_ladder(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    with_4k(sync)
    seen = {}
    monkeypatch.setattr(sync, "chosen_episodes", lambda ids: seen.setdefault("asked_in", (sync.instance() or {}).get("name")) and [])
    monkeypatch.setattr(sync, "sync", lambda config, settings, episodes, **kw: seen.update(
        ran_in=(sync.instance() or {}).get("name"), ladder=config["series"][111].get("ladder"), manual=kw.get("manual")) or 0)
    sync.main([5], sonarr="sonarr-4k")
    assert seen == {"asked_in": "sonarr-4k", "ran_in": "sonarr-4k", "ladder": "4K only", "manual": True}
    seen.clear()
    sync.main([5])
    assert seen["ran_in"] is None and seen["ladder"] is None  # the main Sonarr, the series' own ladder


def test_episodes_catch_up_and_download_name_another_sonarr(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    import unshacklarr.web
    web = importlib.reload(unshacklarr.web)
    with_4k(web.sonarr_sync)
    asked = []
    monkeypatch.setattr(web.sonarr_sync, "sonarr_get", lambda path, **_: asked.append((path, (web.sonarr_sync.instance() or {}).get("name"))) or [])
    monkeypatch.setattr(web, "missing_of_managed", lambda days: [{"from": (web.sonarr_sync.instance() or {}).get("name")}])
    monkeypatch.setattr(web, "cdm_refusal", lambda ids: asked.append(("cdm", (web.sonarr_sync.instance() or {}).get("name"))) or "")
    started = []
    monkeypatch.setattr(web, "run_sync", lambda ids, **kw: started.append(kw.get("sonarr")))
    monkeypatch.setattr(web, "room_for_one_more", lambda: None)
    web.app._middlewares = type(web.app._middlewares)([web.same_origin_only])  # logged in, for this test
    h = {"X-Unshackle": "1"}

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            eps = (await client.get("/api/series/77/episodes?sonarr=sonarr-4k", headers=h)).status
            bad = (await client.get("/api/series/77/episodes?sonarr=nope", headers=h)).status
            caught = await (await client.get("/api/missing?sonarr=sonarr-4k", headers=h)).json()
            down = (await client.post("/api/download", json={"episodeIds": [5], "sonarr": "sonarr-4k"}, headers=h)).status
            return eps, bad, caught, down

    eps, bad, caught, down = asyncio.run(go())
    assert eps == 200 and bad == 400
    assert ("episode", "sonarr-4k") in asked and ("episodefile", "sonarr-4k") in asked and ("cdm", "sonarr-4k") in asked
    assert caught["items"] == [{"from": "sonarr-4k"}]
    assert down == 200 and started == ["sonarr-4k"]


def test_the_series_page_and_upgrades_know_the_other_sonarr(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    import unshacklarr.web
    web = importlib.reload(unshacklarr.web)
    web.write_config({**web.read_config(), "series": {1: {"service": "X", "title": "t"}, 2: {"service": "X", "title": "u"}}})
    with_4k(web.sonarr_sync)  # after: writing the config applies its settings
    libraries = {"": {1: {"id": 10}, 2: {"id": 20}}, "sonarr-4k": {1: {"id": 77, "statistics": {"episodeCount": 6, "episodeFileCount": 1}}}}
    monkeypatch.setattr(web.sonarr_sync, "sonarr_series", lambda wanted: libraries[(web.sonarr_sync.instance() or {}).get("name", "")])
    assert web.instances_of_series() == {1: [{"name": "sonarr-4k", "id": 77, "ladder": "4K only", "download_only": None, "missing": 5}]}
    libraries["sonarr-4k"][3] = {"id": 78, "titleSlug": "tehran"}  # in sonarr-4k, not set up here yet
    assert web.sonarrs_of_series() == {1: [{"name": "sonarr-4k", "url": web.sonarr_sync.SONARRS["sonarr-4k"]["url"], "slug": "", "missing": 5}],
                                       3: [{"name": "sonarr-4k", "url": web.sonarr_sync.SONARRS["sonarr-4k"]["url"], "slug": "tehran", "missing": 0}]}
    web.health["sonarrs"] = {"sonarr-4k": {"ok": False}}
    assert web.instances_of_series() == {} and web.sonarrs_of_series() == {}  # down: the page opens without waiting for it
    assert web.upgrades_file("") == web.UPGRADES_FILE and web.upgrades_file("sonarr-4k").name == "upgrades_found-sonarr-4k.json"
    seen = []
    monkeypatch.setattr(web, "upgrade_candidates", lambda config: seen.append(((web.sonarr_sync.instance() or {}).get("name"),
                                                                              config["series"][1].get("ladder"))) or [])
    web.scan_upgrades("sonarr-4k")
    assert seen == [("sonarr-4k", "4K only")]  # its files, on its own ladder
    assert web.read_json(web.upgrades_file("sonarr-4k"), {})["items"] == [] and not web.UPGRADES_FILE.exists()


def test_a_hybrid_dolby_vision_file_stands_where_its_best_layer_does(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    import unshacklarr.web
    web = importlib.reload(unshacklarr.web)
    four_k = {"name": "4K only", "steps": [{"codec": "HEVC", "range": "HDR10P", "min": 2160, "max": 0},
                                           {"codec": "HEVC", "range": "HDR10", "min": 2160, "max": 0},
                                           {"codec": "HEVC", "range": "DV", "min": 2160, "max": 0}]}
    track = lambda dynamic: web.ladder_track({"resolution": "3840x1920", "videoCodec": "h265", "videoDynamicRangeType": dynamic})  # noqa: E731
    assert track("DV HDR10Plus")["layers"] == ["DV", "HDR10P"] and track("DV HDR10")["layers"] == ["DV", "HDR10"]
    assert sync.step_of(four_k, track("DV HDR10Plus")) == 0  # its HDR10+ layer: step 1, nothing to upgrade
    assert sync.step_of(four_k, track("DV HDR10")) == 1  # its HDR10 layer: step 2, before plain DV
    assert sync.step_of(four_k, track("DV")) == 2 and sync.step_of(four_k, track("HDR10Plus")) == 0
    assert track("")["layers"] == ["SDR"]
    assert sync.track_label(track("DV HDR10Plus")) == "2160p HEVC DV + HDR10P"  # labelled by every layer, not DV alone
    assert sync.track_label(track("DV")) == "2160p HEVC DV"


def test_the_release_group_set_only_in_unshackle_yaml_is_read_from_the_file(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    folder = tmp_path / "unshackle-config"
    folder.mkdir()
    (folder / "unshackle.yaml").write_text("tag: TiNA\ndl:\n  sub_format: srt\n")
    monkeypatch.setitem(sync.SETTINGS, "unshackle_config_dir", str(folder))
    monkeypatch.setattr(sync.UNSHACKLE, "dl_config", lambda: {"sub_format": "srt"})  # serve's /api/config: no tag
    monkeypatch.setattr(sync, "backend_for", lambda tag, config=None: sync.UNSHACKLE)
    assert sync.release_group_of({"service": "NF"}, {"settings": {}, "defaults": {}, "service_defaults": {}}) == ("TiNA", "unshackle.yaml (tag:)")


def test_a_series_switches_another_sonarr_off_or_adds_options_for_it(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    import unshacklarr.web
    web = importlib.reload(unshacklarr.web)
    inst = {"name": "sonarr-4k", "quality_ladder": "4K only", "download_only": None}
    config = {"series": {1: {"service": "X", "options": {"--tag": "a"}},
                         2: {"service": "X", "options": {"--tag": "a"}, "sonarrs": {"sonarr-4k": {"options": {"--atmos": True}}}},
                         3: {"service": "X", "sonarrs": {"sonarr-4k": {"off": True}}}}}
    got = sync.instance_config(inst, config)["series"]
    assert got[1]["ladder"] == "4K only" and "download_only" not in got[1]  # that Sonarr's own, for every series
    assert got[2]["ladder"] == "4K only" and got[2]["options"] == {"--tag": "a", "--atmos": True}  # that Sonarr's ladder, its own options
    assert config["series"][2]["options"] == {"--tag": "a"}  # the main Sonarr's copy keeps the series' own
    assert [sync.off_in(inst, s) for s in config["series"].values()] == [False, False, True]
    assert sync.off_in(None, {"main_off": True}) and not sync.off_in(None, config["series"][3])  # the main Sonarr's own switch

    downloads = []
    monkeypatch.setattr(sync, "run_episodes", lambda config, settings, episodes, *a, **k: downloads.append([e["id"] for e in episodes]) or 0)
    eps = [{"id": n, "series": {"tvdbId": n, "title": "S"}, "seasonNumber": 1, "episodeNumber": n} for n in (1, 2)]
    off = {"series": {1: {"service": "X", "main_off": True}, 2: {"service": "X"}}}
    sync.sync(off, {}, eps, kind="auto")
    sync.sync(off, {}, eps, kind="burst")
    assert downloads == [[2], [2]]  # switched off for the main Sonarr: none of its new episodes (picked ones aren't filtered)

    body = {"settings": {"sonarrs": [{"name": "sonarr-4k"}]}}
    specs = [{"flag": "--atmos", "is_flag": True, "choices": []}]
    assert web.check_series_sonarrs({"sonarr-4k": {"off": True, "ladder": "1080p", "options": {}}}, body, specs, "1") == {"sonarr-4k": {"off": True}}  # a ladder is that Sonarr's
    assert web.check_series_sonarrs({"sonarr-4k": {}}, body, specs, "1") == {}  # nothing of its own: left out
    try:
        web.check_series_sonarrs({"nope": {"off": True}}, body, specs, "1")
        raise AssertionError("a Sonarr not in Settings was taken")
    except web.web.HTTPBadRequest:
        pass

def test_the_download_window_keeps_the_automatic_sync_to_its_hours(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    at = lambda hhmm: datetime(2026, 10, 7, *map(int, hhmm.split(":")), tzinfo=sync.LOCAL)  # noqa: E731
    assert sync.in_download_window(at("15:00"))  # none set: always
    sync.SETTINGS.update(download_from="01:00", download_to="07:00")
    assert sync.in_download_window(at("03:00")) and not sync.in_download_window(at("15:00"))
    sync.SETTINGS.update(download_from="23:00", download_to="06:00")  # over midnight
    assert sync.in_download_window(at("23:30")) and sync.in_download_window(at("05:59")) and not sync.in_download_window(at("06:00"))
    monkeypatch.setattr(sync, "missing_episodes", lambda: (_ for _ in ()).throw(AssertionError("asked Sonarr outside the window")))
    monkeypatch.setattr(sync, "in_download_window", lambda now=None: False)
    assert sync.main() == 0


def test_a_series_falls_back_on_another_service_when_its_own_has_nothing(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    config = sync.read_file()
    config["series"][111].update(season_map={2: 9}, fallback={"service": "MLT", "title": "https://mlt/show", "episode_offset": 1})
    alt = sync.fallback_show(config["series"][111])
    assert alt["service"] == "MLT" and "season_map" not in alt and alt["episode_offset"] == 1  # its own numbering, not the main one's
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    asked = []

    def download(payload, run=None):
        asked.append((payload["service"], payload["wanted"][0]))
        if payload["service"] == "MLT":  # only the fallback has it
            out = Path(payload["output_dir"])
            out.mkdir(parents=True, exist_ok=True)
            (out / "Show.S02E06.mkv").write_bytes(b"x")

    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync, "finalize", lambda *a, **k: 1)
    monkeypatch.setattr(sync, "check_audio", lambda *a: None)
    imported = []
    monkeypatch.setattr(sync, "import_episode", lambda ep, out, replace=False: imported.append(ep["id"]))
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert asked == [("RTLP", "S09E05"), ("MLT", "S02E06")] and imported == [205]
    card = next(json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json"))
    assert card["service"] == "MLT" and card["fallback"] == "RTLP" and card["outcome"] == "downloaded"


def test_a_release_time_is_learnt_once_clear_and_never_over_one_set(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    seen = {f"unshackle-111-S02E0{n}": {"tvdbId": 111, "aired": f"2026-09-0{n}T19:00:00Z", "not_yet": f"2026-09-0{n}T20:55:00Z",
                                         "available": f"2026-09-0{n}T21:05:00Z"} for n in (1, 2, 3)}
    sync.write_atomic(sync.SEEN_FILE, json.dumps(seen))
    learnt, told = [], []
    monkeypatch.setattr(sync, "on_release_learned", lambda tvdb, at, day: learnt.append((tvdb, at, day)))
    monkeypatch.setattr(sync, "notify", lambda settings, level, title, *a, **k: told.append(title))
    ep = episode(111, 2, 4)
    sync.learn_release(ep, {"service": "RTLP"}, {})
    assert learnt == [] and told == []  # off by default
    sync.SETTINGS["release_learn"] = True
    sync.learn_release(ep, {"service": "RTLP", "release_time": "20:00"}, {})
    assert learnt == []  # one set by hand stays
    sync.learn_release(ep, {"service": "RTLP"}, {})
    assert learnt == [(111, sync.suggest_release(list(seen.values()))["time"], 0)] and told == ["Release time set: Show"]


def test_the_preferred_audio_is_added_to_the_file_it_has(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    library = tmp_path / "lib" / "Show" / "Season 02"
    library.mkdir(parents=True)
    (library / "Show - S02E05.mkv").write_bytes(b"video")
    sync.SETTINGS.update(library_sonarr_root="/tv", library_local_root=str(tmp_path / "lib"))
    assert sync.library_file("/tv/Show/Season 02/Show - S02E05.mkv") == library / "Show - S02E05.mkv"
    assert sync.library_file("/elsewhere/x.mkv") == Path("/elsewhere/x.mkv")
    ep = {**episode(111, 2, 5), "episodeFileId": 9}
    monkeypatch.setattr(sync, "chosen_episodes", lambda ids: [ep])
    monkeypatch.setattr(sync, "sonarr_get", lambda path, **p: {"path": "/tv/Show/Season 02/Show - S02E05.mkv"})
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    asked = []

    def download(payload, run=None):
        asked.append(payload)
        (Path(payload["output_dir"]) / "audio.mka").parent.mkdir(parents=True, exist_ok=True)
        (Path(payload["output_dir"]) / "audio.mka").write_bytes(b"fr")

    calls, imported = [], []

    def tools(cmd, **_):
        calls.append(cmd)
        if cmd[:2] == ["mkvmerge", "-J"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"container": {"properties": {"duration": 2_400_000_000_000}}}))
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"joined")
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(sync, "run_job_retrying", download)
    monkeypatch.setattr(sync.subprocess, "run", tools)
    monkeypatch.setattr(sync, "import_episode", lambda e, out, replace=False: imported.append((sorted(f.name for f in out.iterdir()), replace)))
    monkeypatch.setattr(sync, "notify", lambda *a, **k: None)
    assert sync.add_track(205, "fr") is True
    assert asked[0]["audio_only"] is True and asked[0]["a_lang"] == ["fr"] and "lang" not in asked[0]
    merge = calls[-1]
    assert merge[:4] == ["mkvmerge", "-q", "-o", str(tmp_path / "unshackle-111-S02E05" / "Show - S02E05.mkv")]
    assert str(library / "Show - S02E05.mkv") in merge and merge[-1].endswith("audio.mka")
    assert imported == [(["Show - S02E05.mkv"], True)]  # the joined file only, in place of the library's
    card = next(json.loads(p.read_text()) for p in (tmp_path / "runs").glob("*.json"))
    assert card["kind"] == "upgrade" and card["outcome"] == "downloaded"

    monkeypatch.setattr(sync, "sonarr_get", lambda path, **p: {"path": "/tv/Show/Gone.mkv"})
    assert sync.add_track(205, "fr") is False  # out of reach here: downloaded again as before


def test_a_series_can_send_only_its_failures_or_nothing(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    sent, boxed = [], []
    monkeypatch.setattr(sync, "notify", lambda settings, level, title, message, *a, **k: sent.append(level) or True)
    monkeypatch.setattr(sync, "inbox_add", lambda level, title, message, action=None, batch=None: boxed.append(level))
    for mode, level in (("failures", "success"), ("failures", "error"), ("none", "error"), (None, "success")):
        sync.notify_series({"notify": mode} if mode else {}, {}, level, "t", "m")
    assert sent == ["error", "success"] and boxed == ["success", "error"]  # the bell keeps what is not sent


def test_an_import_sonarr_does_not_answer_is_asked_again(tmp_path, monkeypatch):
    # Sonarr busy moving another big file: the import's answer times out. Asked again, a minute apart (here none);
    # an import it did all the same is not asked twice; one it never answers fails after IMPORT_ASKS
    sync = load(tmp_path, monkeypatch)
    monkeypatch.setattr(sync, "IMPORT_RETRY_WAIT", 0)
    out = tmp_path / "unshackle-111-S02E05"
    out.mkdir()
    folder = sync.seen_by("sonarr_downloads", out)
    candidate = {"path": f"{folder}/Show.S02E05.mkv", "quality": {"quality": {"name": "WEBDL-1080p"}}, "languages": []}
    state = {"hasFile": False, "episodeFileId": 0}
    monkeypatch.setattr(sync, "sonarr_get", lambda path, **p: [candidate] if path == "manualimport" else dict(state))
    monkeypatch.setattr(sync, "wait_for_import", lambda ep, command, old: None)
    posts = []

    class Answer:
        def raise_for_status(self): pass
        def json(self): return {"id": 7}

    def post(url, answers, **kw):
        posts.append(kw["timeout"])
        answer = answers.pop(0)
        if state.pop("on_post", None):  # Sonarr takes it, then answers too late
            state.update(hasFile=True, episodeFileId=9)
        if isinstance(answer, Exception):
            raise answer
        return answer
    ep = {"id": 5, "seriesId": 1}
    answers = [sync.requests.Timeout("read timed out"), Answer()]
    monkeypatch.setattr(sync.requests, "post", lambda url, **kw: post(url, answers, **kw))
    sync.import_episode(dict(ep), out)
    assert posts == [120, 120]  # asked again once, each time as patiently as its reads

    posts.clear()
    answers[:] = [sync.requests.Timeout("read timed out")]
    state["on_post"] = True  # it imported it all the same
    sync.import_episode(dict(ep), out)
    assert posts == [120]

    posts.clear()
    state.update(hasFile=False, episodeFileId=0)
    answers[:] = [sync.requests.ConnectionError("refused")] * 3
    try:
        sync.import_episode(dict(ep), out)
        raise AssertionError("an import Sonarr never answered passed")
    except RuntimeError as e:
        assert "did not answer the import 3 times" in str(e)
    assert len(posts) == 3


def test_subtitles_asked_for_that_the_episode_lacks_do_not_stop_its_download(tmp_path, monkeypatch):
    # By mj23au (#16): s_lang en (here from unshackle.yaml's dl:) on an episode with no English subtitles: Unshackle
    # would exit ("en not found in tracks"); the download goes on without them. With them there, nothing changes.
    sync = load(tmp_path, monkeypatch)
    config = sync.read_file()
    config["series"][111]["ladder"] = "1080p"
    subs = {"S02E05": [], "S02E06": [{"language": "en-GB"}], "S02E07": [{"language": "fr"}]}
    video = [{"height": 1080, "codec": "AVC", "range": "SDR"}]
    monkeypatch.setattr(sync.UNSHACKLE, "call", lambda method, path, json=None, **_:
                        {"episodes": [{"video": video, "subtitles": subs[json["wanted"][0]]}]} if path == "/api/list-tracks" else {})
    monkeypatch.setattr(sync.UNSHACKLE, "dl_config", lambda: {"s_lang": ["en"]})
    monkeypatch.setattr(sync, "no_cdm", lambda tag, config=None: "")
    downloads = []
    monkeypatch.setattr(sync, "run_job_retrying", lambda payload, run=None: downloads.append(payload))
    for n in (5, 6, 7):
        sync.sync(config, {}, [episode(111, 2, n)], manual=True, kind="manual")
    assert [(d.get("s_lang"), d.get("no_subs")) for d in downloads] == [(None, True), (["en"], None), (None, True)]

    config["series"][111]["options"] = {"--require-subs": "en"}  # required: it stops as before, nothing relaxed
    sync.sync(config, {}, [episode(111, 2, 5)], manual=True, kind="manual")
    assert downloads[-1].get("no_subs") is None


def test_one_import_at_a_time_per_sonarr(tmp_path, monkeypatch):
    # Three downloads ending together: their imports go one after the other, never several big files at once
    import threading, time
    sync = load(tmp_path, monkeypatch)
    out = tmp_path / "unshackle-111-S02E05"
    out.mkdir()
    folder = sync.seen_by("sonarr_downloads", out)
    candidate = {"path": f"{folder}/Show.S02E05.mkv", "quality": {"quality": {"name": "WEBDL-1080p"}}, "languages": []}
    monkeypatch.setattr(sync, "sonarr_get", lambda path, **p: [candidate] if path == "manualimport" else {"hasFile": False})
    now, most = [0], [0]

    def wait(ep, command, old):
        now[0] += 1
        most[0] = max(most[0], now[0])
        time.sleep(.05)
        now[0] -= 1

    class Answer:
        def raise_for_status(self): pass
        def json(self): return {"id": 7}
    monkeypatch.setattr(sync, "wait_for_import", wait)
    monkeypatch.setattr(sync.requests, "post", lambda url, **kw: Answer())
    threads = [threading.Thread(target=sync.import_episode, args=({"id": i, "seriesId": 1}, out)) for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert most[0] == 1

def test_only_the_missing_subtitle_languages_are_dropped(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    assert sync.subs_dropped({"s_lang": ["fr", "en"]}, {"fr"}) == {"s_lang": ["en"]}  # partly there: the rest kept
    assert sync.subs_dropped({"s_lang": ["fr", "-es"]}, {"fr"}) == {"no_subs": True}
    assert sync.subs_dropped({"s_lang": ["fr"], "require_subs": ["fr"]}, {"fr"}) is None  # required: it waits
    assert sync.subs_dropped({"s_lang": ["en"]}, {"fr"}) is None  # nothing to drop
    # none left, forced subtitles wanted: --no-subs would drop them, --best-available keeps them
    assert sync.subs_dropped({"s_lang": ["fr"], "forced_s_lang": ["fr"]}, {"fr"}) == {"s_lang": ["fr"], "forced_s_lang": ["fr"], "best_available": True}


def test_a_download_stopped_for_missing_subtitles_goes_again_without_them(tmp_path, monkeypatch):
    # Any series, with no ladder (or --remote): Unshackle's own error says which, the download goes again once, at once
    sync = load(tmp_path, monkeypatch)
    asked = []

    def job(payload, run=None):
        asked.append(payload)
        if len(asked) == 1:
            raise sync.JobFailed("Tracks listed\nfr not found in subtitle tracks", "failed")
        return ["ok"]
    monkeypatch.setattr(sync, "run_job", job)
    assert sync.run_job_retrying({"s_lang": ["fr", "en"]}, sleep=lambda s: None) == ["ok"]
    assert asked[1] == {"s_lang": ["en"]}


def test_the_official_unshackle_wording_of_missing_subtitles_is_read(tmp_path, monkeypatch):
    sync = load(tmp_path, monkeypatch)
    assert sync.SUBS_MISSING.search("fr, de not found in tracks").group(1) == "fr, de"  # Unshackle 5.x
    assert sync.SUBS_MISSING.search("fr not found in subtitle tracks").group(1) == "fr"
    assert not sync.SUBS_MISSING.search("fr not found in audio tracks")
    assert not sync.SUBS_MISSING.search("fr not found in video tracks")


def test_downloads_at_once_per_server_and_a_slow_serve_waited_for(tmp_path, monkeypatch):
    # Downloads at once 1: a second download on the same server waits for the first; another server doesn't (#10)
    import threading, time
    sync = load(tmp_path, monkeypatch)
    monkeypatch.setitem(sync.SETTINGS, "downloads_at_once", 1)
    now, most = {"": 0, "b": 0}, {"": 0, "b": 0}

    def busy(server):
        with sync.download_slot(server):
            now[server] += 1
            most[server] = max(most[server], now[server])
            time.sleep(.05)
            now[server] -= 1
    threads = [threading.Thread(target=busy, args=(s,)) for s in ("", "", "", "b", "b")]
    for t in threads: t.start()
    for t in threads: t.join()
    assert most == {"": 1, "b": 1}
    monkeypatch.setitem(sync.SETTINGS, "downloads_at_once", 0)  # 0: no limit here
    threads = [threading.Thread(target=busy, args=("",)) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert most[""] == 3

    # serve too slow to answer: waited for 1, 3 then 10 min; another hiccup 30 s then 2 min
    slept = []
    for cause, waits in (("unshackle serve is unreachable at http://x: Read timed out. (read timeout=60)", [60, 180, 600]),
                         ("502 Bad Gateway", [30, 120])):
        slept.clear()
        monkeypatch.setattr(sync, "run_job", lambda payload, run=None: (_ for _ in ()).throw(sync.UnshackleError(cause)))
        try:
            sync.run_job_retrying({}, sleep=slept.append)
            raise AssertionError("it never failed")
        except sync.UnshackleError:
            pass
        assert slept == waits
