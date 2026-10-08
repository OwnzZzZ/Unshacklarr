"""The demo's data: the real server's answers, recorded once against a made-up Sonarr and Unshackle.

The series and services are real names, but every episode, device, cookie and file here is made up. demo.js plays the
answers back in the browser and moves their dates to the day the demo is opened.

    uv run python demo/record.py demo/site/data.json
"""
import asyncio
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA = Path(tempfile.mkdtemp(prefix="unshacklarr-demo-"))
os.environ["UNSHACKLARR_DATA"] = str(DATA)
for name in [k for k in os.environ if k in ("SONARR_URL", "SONARR_API_KEY", "UNSHACKLE_URL", "UNSHACKLE_API_KEY", "TZ")]:
    del os.environ[name]

import requests  # noqa: E402

import unshacklarr.sync  # noqa: E402
import unshacklarr.web  # noqa: E402

sync = importlib.reload(unshacklarr.sync)
web = importlib.reload(unshacklarr.web)

NOW = datetime.now(timezone.utc).replace(microsecond=0)
PASSWORD = "demo-password"
TZ = "UTC"


def offline(*_, **__):  # the demo never reaches anything real
    raise requests.ConnectionError("offline: this is the demo")


requests.Session.request = offline


# ---- A made-up Unshackle: six services, their options, two devices ----

SERVICES = [
    ("ATVP", "Apple TV+", "tv.apple.com", ["cookies"]),
    ("DSNP", "Disney+", "disneyplus.com", ["credentials"]),
    ("HULU", "Hulu", "hulu.com", ["cookies"]),
    ("AMZN", "Prime Video", "primevideo.com", ["cookies"]),
    ("TF1", "TF1+", "tf1.fr", ["cookies", "credentials"]),
    ("RMCBFM", "RMC BFM Play", "rmcbfmplay.com", ["cookies"]),
]
# every service its device, as a real setup has it: Prime Video on PlayReady, the others on Widevine
CDM_MAP = {"default": "samsung_sm-a536b_l3", "AMZN": "xiaomi_mibox_s_sl2000",
           **{tag: "samsung_sm-a536b_l3" for tag in ("ATVP", "DSNP", "HULU", "TF1", "RMCBFM")}}
SERVE_SERVICES = [{
    "tag": tag, "url": f"https://{site}", "help": f"Service code for {name} (https://{site})", "auth_methods": auth,
    "cli_params": [{"kind": "option", "name": "movie", "opts": ["-m", "--movie"], "is_flag": True, "help": "Title is a movie."}]
    + ([{"kind": "option", "name": "region", "opts": ["--region"], "type": "choice", "choices": ["us", "uk", "de", "fr"],
         "help": "The catalogue to use."}] if tag == "AMZN" else []),
} for tag, name, site, auth in SERVICES]


def serve(method, path, **kwargs):
    if path == "/api/services":
        return {"services": SERVE_SERVICES}
    if path == "/api/remote/services":
        return {"servers": []}
    if path == "/api/health":
        return {"version": "5.5.0", "update_check": {"update_available": False}}
    if path == "/api/env/check":
        return {"checks": [{"name": n, "installed": True, "version": v, "required": r} for n, v, r in
                           (("ffmpeg", "7.1", True), ("mkvmerge", "v94.0", True), ("shaka-packager", "3.4.2", True),
                            ("mp4decrypt", "1.6.0", False), ("aria2c", "1.37.0", False))]}
    if path == "/api/download/jobs":
        return {"jobs": [{"job_id": "demo-job-1", "service": "DSNP", "current_title": "The Bear",
                          "status": "downloading", "progress": 42}]}
    if path == "/api/config":
        return {"config": {"dl": {"sub_format": "srt"}, "cdm": CDM_MAP}}
    if path == "/api/cdm/devices":
        return {"devices": [
            {"kind": "widevine", "name": "samsung_sm-a536b_l3", "system_id": 22590, "level": "L3", "type": "Android", "company": "samsung",
             "model": "SM-A536B", "product": "a53xnsxx", "architecture": "arm64-v8a", "cdm_version": "17.0.0", "patch_level": "2024-05-01", "vmp": False},
            {"kind": "playready", "name": "xiaomi_mibox_s_sl2000", "level": "SL2000", "company": "Xiaomi MiBox S", "version": 3,
             "reprovisionable": True, "certificates": 3},
        ]}
    if path == "/api/cdm/devices/test":
        return {"ok": True, "keys": 1}
    if path == "/api/list-titles":
        show = next(s for s in SERIES if s["service"] == kwargs["json"]["service"] and s["url"] == kwargs["json"]["title_id"])
        return {"titles": [{"type": "episode", "season": e["seasonNumber"], "number": e["episodeNumber"], "name": e["title"],
                            "series_title": show["title"]} for e in EPISODES[show["id"]] if e["airDateUtc"] <= NOW.isoformat()]}
    raise sync.UnshackleError(f"unshackle serve: no {path} in the demo")


# ---- A made-up Sonarr: seven series, their episodes around today ----

COLOURS = ["#0e7490", "#7c3aed", "#b45309", "#15803d", "#be123c", "#1d4ed8", "#4d7c0f", "#9d174d", "#334155", "#a16207", "#6d28d9"]


def poster(title: str, colour: str) -> str:
    import textwrap
    from urllib.parse import quote
    from xml.sax.saxutils import escape
    lines = textwrap.wrap(title, 12)
    top = 50 - (len(lines) - 1) * 6
    text = "".join(f'<text x="50%" y="{top + i * 12}%" text-anchor="middle" font-family="sans-serif" font-size="34" '
                   f'font-weight="700" fill="#fff">{escape(w)}</text>' for i, w in enumerate(lines))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="340" height="500"><defs><linearGradient id="g" x2="0" y2="1">'
           f'<stop offset="0" stop-color="{colour}"/><stop offset="1" stop-color="#0b1120"/></linearGradient></defs>'
           f'<rect width="100%" height="100%" fill="url(#g)"/>{text}'
           f'<text x="50%" y="9%" text-anchor="middle" font-family="sans-serif" font-size="18" letter-spacing="4" fill="#ffffff99">DEMO</text></svg>')
    return "data:image/svg+xml," + quote(svg)


# title, service, url, seasons [(episodes, days between)], first air (days from now), hour UTC, status
SERIES = []
EPISODES = {}
PLAN = [
    ("Severance", "ATVP", "https://tv.apple.com/us/show/severance", [9, 10], -60, 1, "continuing"),
    ("The Bear", "DSNP", "https://www.disneyplus.com/series/the-bear", [10], -40, 2, "continuing"),
    ("The Simpsons", "DSNP", "https://www.disneyplus.com/series/the-simpsons", [22, 12], -200, 0, "continuing"),
    ("Koh-Lanta", "TF1", "https://www.tf1.fr/tf1/koh-lanta", [14], -45, 19, "continuing"),
    ("Only Murders in the Building", "HULU", "https://www.hulu.com/series/only-murders-in-the-building", [10, 10], -400, 2, "ended"),
    ("Wheeler Dealers", "RMCBFM", "https://www.rmcbfmplay.com/emissions/wheeler-dealers", [10], -30, 18, "continuing"),
    ("Abbott Elementary", None, None, [10], -20, 1, "continuing"),
    ("Grey's Anatomy", "HULU", "https://www.hulu.com/series/greys-anatomy", [20], -90, 1, "continuing"),
    ("Reacher", "AMZN", "https://www.primevideo.com/detail/reacher", [8], -150, 8, "continuing"),
    ("The Last of Us", None, None, [9, 7], -500, 1, "continuing"),
    ("Hacks", None, None, [10], -300, 2, "ended"),
]
for n, (title, service, url, seasons, first, hour, status) in enumerate(PLAN):
    sid, tvdb = n + 1, 900001 + n
    serie = {"id": sid, "tvdbId": tvdb, "tmdbId": 990001 + n, "title": title, "titleSlug": title.lower().replace(" ", "-"),
             "year": (NOW + timedelta(days=first)).year, "monitored": True, "status": status, "path": f"/tv/{title}",
             "images": [{"coverType": "poster", "remoteUrl": poster(title, COLOURS[n])}],
             "service": service, "url": url}
    eps, eid = [], sid * 1000
    day = NOW.replace(hour=hour, minute=0, second=0) + timedelta(days=first)
    for season, count in enumerate(seasons, 1):
        if season > 1:
            day += timedelta(days=21)
        for number in range(1, count + 1):
            eid += 1
            aired = day <= NOW
            # a few aired ones missing: what the Schedule and the sync go after
            missing = aired and (NOW - day < timedelta(days=4) or title == "Wheeler Dealers" and NOW - day < timedelta(days=12) or title == "The Bear" and NOW - day < timedelta(days=7))
            eps.append({"id": eid, "seriesId": sid, "seasonNumber": season, "episodeNumber": number,
                        "title": f"Épisode {number}" if title == "Koh-Lanta" else f"Episode {number}",
                        "airDateUtc": day.isoformat().replace("+00:00", "Z"), "airDate": day.date().isoformat(),
                        "hasFile": aired and not missing, "monitored": True, "episodeFileId": eid if aired and not missing else 0})
            day += timedelta(days=1 if title == "Wheeler Dealers" and number % 3 else 7)
    EPISODES[sid] = eps
    total, files = sum(e["airDateUtc"] <= NOW.isoformat() for e in eps), sum(e["hasFile"] for e in eps)  # Sonarr counts aired ones
    serie["statistics"] = {"episodeCount": total, "episodeFileCount": files}
    SERIES.append(serie)


def with_series(ep):
    s = next(s for s in SERIES if s["id"] == ep["seriesId"])
    return {**ep, "series": {k: v for k, v in s.items() if k not in ("service", "url")}}


def sonarr_get(path, **params):
    if path == "series":
        found = [{k: v for k, v in s.items() if k not in ("service", "url")} for s in SERIES]
        return [s for s in found if s["tvdbId"] == int(params["tvdbId"])] if "tvdbId" in params else found
    if path.startswith("series/"):
        return next(s for s in SERIES if s["id"] == int(path.split("/")[1]))
    if path == "episode" and "seriesId" in params:
        return EPISODES[int(params["seriesId"])]
    if path == "episode":
        ids = set(params["episodeIds"])
        return [with_series(e) for eps in EPISODES.values() for e in eps if e["id"] in ids]
    if path == "episodefile":
        return [{"id": e["id"], "size": 1_450_000_000 + e["id"] * 997, "releaseGroup": "UNSHACKLE",
                 "quality": {"quality": {"name": "WEBDL-1080p"}},
                 "mediaInfo": {"resolution": "1920x1080", "videoCodec": "h264", "audioCodec": "EAC3", "audioChannels": 5.1,
                               "audioLanguages": "eng", "subtitles": "eng/fre/spa", "runTime": "44:12"}}
                for e in EPISODES[int(params["seriesId"])] if e["hasFile"]]
    if path == "calendar":
        start, end = sync.parse_time(params["start"]), sync.parse_time(params["end"])
        return [with_series(e) for eps in EPISODES.values() for e in eps if start <= sync.parse_time(e["airDateUtc"]) < end]
    raise requests.ConnectionError(f"no {path} in the demo's Sonarr")


# ---- Unshackle's folder: unshackle.yaml, two devices, cookie files ----

UNSHACKLE_DIR, COOKIES, DOWNLOADS = DATA / "unshackle", DATA / "cookies", DATA / "downloads"
(UNSHACKLE_DIR / "WVDs").mkdir(parents=True)
(UNSHACKLE_DIR / "PRDs").mkdir()
(UNSHACKLE_DIR / "WVDs" / "samsung_sm-a536b_l3.wvd").write_bytes(b"WVD\x02\x02\x03" + bytes(2000))
(UNSHACKLE_DIR / "PRDs" / "xiaomi_mibox_s_sl2000.prd").write_bytes(bytes(1500))
CDM_LINES = "".join(f"  {k}: {v}\n" for k, v in CDM_MAP.items())
(UNSHACKLE_DIR / "unshackle.yaml").write_text(f"""# The demo's unshackle.yaml: nothing here is real
directories:
  downloads: /downloads
cdm:
{CDM_LINES}
dl:
  sub_format: srt
serve:
  api_secret: "•••"
""")


def cookie_file(name: str, days: float) -> None:
    expires = int((NOW + timedelta(days=days)).timestamp())
    COOKIES.mkdir(exist_ok=True)
    (COOKIES / f"{name}.txt").write_text("# Netscape HTTP Cookie File\n" + "".join(
        f".{site}\tTRUE\t/\tTRUE\t{expires}\t{c}\tdemo-{c}\n" for c in ("session", "token")
        for tag, _, site, _ in SERVICES if tag == name))


cookie_file("ATVP", 120)
cookie_file("AMZN", 60)
cookie_file("HULU", 5)  # about to expire: Settings and the bell say so
cookie_file("RMCBFM", -2)  # expired: Wheeler Dealers fails

# A download that waits in the downloads folder for Sonarr
waiting = DOWNLOADS / "unshackle-900006-S01E03"
waiting.mkdir(parents=True)
(waiting / "Wheeler.Dealers.S01E03.1080p.WEB-DL.mkv").write_bytes(bytes(4096))


# ---- Unshacklarr's own config and history ----

def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


config = {
    "settings": {"sonarr_url": "http://sonarr:8989", "sonarr_api_key": "demo" * 8, "sonarr_downloads": "/downloads",
                 "unshackle_mode": "remote", "unshackle_url": "http://unshackle:8786", "unshackle_api_key": "demo-not-a-real-key-" * 2,
                 "downloads": str(DOWNLOADS), "cookies_dir": str(COOKIES), "unshackle_config_dir": str(UNSHACKLE_DIR),
                 "country": "US", "timezone": TZ, "sync_every_hours": 2},
    "auth": {"password": web.hash_password(PASSWORD), "secret": "demo", "changed": ago(days=20)},
    "defaults": {"--quality": "1080", "--sub-format": "srt"},
    "service_defaults": {"AMZN": {"service_options": {"--region": "us"}}},
    "series": {},
    "notifications": {"targets": [
        {"url": "discord://000000000000000000/demo-token", "levels": ["success", "warning", "error"]},
        {"url": "ntfys://ntfy.sh/unshacklarr-demo", "levels": ["warning", "error"]}],
        "quiet": {"from": "23:00", "to": "07:30"}},
}
for s in SERIES:
    if s["service"]:
        config["series"][s["tvdbId"]] = {"service": s["service"], "title": s["url"], "since": ago(days=90),
                                         "file_name": s["title"]}
config["series"][900004].update(parts=2, release_day=0, release_time="19:30")  # Koh-Lanta: two parts a night
config["series"][900003].update(season_offset=1, season_offset_from=2)  # The Simpsons: Disney+'s S03 is Sonarr's S02
web.write_config(config)

RUN_LOG = """\x1b[90m── {when} · {kind} ──\x1b[0m\r
\x1b[36m▸\x1b[0m {series} {sxxeyy} on {service}\r
  unshackle serve took it as job demo-{id}\r
\x1b[36mqueued\x1b[0m {series}\r
\x1b[36mdownloading\x1b[0m {series} {sxxeyy}\r
\x1b[90m  INFO : Getting tracks for {sxxeyy}\x1b[0m\r
\x1b[90m  INFO : Video: 1920x1080 H.264 · 5.6 Mb/s\x1b[0m\r
\x1b[90m  INFO : Audio: English E-AC-3 5.1 · Subtitles: English, French, Spanish\x1b[0m\r
{end}"""
OK_END = """  \x1b[32m████████████████████\x1b[0m 100.0%  5/5 tracks  12.4 MB/s\r
\x1b[36mcompleted\x1b[0m\r
\x1b[36m▸\x1b[0m Renamed to {file}\r
\x1b[32m✓ Imported by Sonarr\x1b[0m\r
"""
FAIL_END = """\x1b[31m  ERROR : RMC BFM Play: your session has expired, log in again (cookies or credentials)\x1b[0m\r
\x1b[36mfailed\x1b[0m\r
\x1b[31m✗ Download failed: RMC BFM Play: your session has expired\x1b[0m\r
"""
TRACKS = [("Video 1080p H.264", 412), ("Audio English 5.1", 38), ("Subtitles English", 2), ("Subtitles French", 2), ("Subtitles Spanish", 2)]

sync.RUNS_DIR.mkdir(parents=True, exist_ok=True)


def run(series: dict, ep: dict, minutes_ago: float, outcome: str, kind: str = "auto", batch: str | None = None,
        took: float = 4.0, running: bool = False) -> dict:
    started = NOW - timedelta(minutes=minutes_ago)
    sxxeyy = f"S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}"
    rid = f"{started:%Y%m%d-%H%M%S-%f}-{series['tvdbId']}-{sxxeyy}"
    file = f"{series['title'].replace(' ', '.')}.{sxxeyy}.1080p.WEB-DL.mkv"
    card = {"id": rid, "series": series["title"], "tvdbId": series["tvdbId"], "sxxeyy": sxxeyy, "service": series["service"],
            "kind": kind, "started": started.isoformat(), "ended": None if running else (started + timedelta(minutes=took)).isoformat(),
            "outcome": None if running else outcome, "episodeId": ep["id"], "serviceEpisode": sxxeyy,
            "step": "downloading" if running else "done", "live": {}, "parts": None, "attempts": 1, "checks": 1,
            "tracks": [{"label": t, "progress": 100.0, "took": s} for t, s in TRACKS] if outcome == "downloaded" else [],
            "cause": "RMC BFM Play: your session has expired, log in again (cookies or credentials)" if outcome == "failed" else "",
            "detail": "", "proxy": None, "cdm": "samsung_sm-a536b_l3", "size": 1_480_000_000 if outcome == "downloaded" else None,
            "setup": {"via": "unshackle", "proxy": None,
                      "command": f"unshackle dl -w {sxxeyy} --quality 1080 {series['service']} {series['url']}"},
            **({"batch": batch} if batch else {})}
    (sync.RUNS_DIR / f"{rid}.json").write_text(json.dumps(card))
    end = "" if running else (OK_END if outcome == "downloaded" else FAIL_END).format(file=file)
    (sync.RUNS_DIR / f"{rid}.log").write_text(RUN_LOG.format(when=started.astimezone().strftime("%a %d %b %H:%M:%S"), kind=kind,
                                                             series=series["title"], sxxeyy=sxxeyy, service=series["service"],
                                                             id=rid[-12:], end=end))
    return card


by_title = {s["title"]: s for s in SERIES}
done = lambda title: [e for e in EPISODES[by_title[title]["id"]] if e["hasFile"]]
# A history: weeks of downloads, a few failures, a job of three picked together
minutes = 60 * 24 * 50
for title in ("Only Murders in the Building", "The Simpsons", "Severance", "The Bear", "Koh-Lanta"):
    for ep in done(title)[-8:]:
        minutes -= 60 * 24 * 1.3
        run(by_title[title], ep, max(minutes, 600), "downloaded", took=3 + ep["id"] % 5)
batch = f"{NOW - timedelta(hours=3):%Y%m%d-%H%M%S}-a1b2c3"
for i, ep in enumerate(done("The Simpsons")[-3:]):
    run(by_title["The Simpsons"], ep, 180 - i * 6, "downloaded", kind="manual", batch=batch, took=5)
wild = [e for e in EPISODES[by_title["Wheeler Dealers"]["id"]] if not e["hasFile"] and e["airDateUtc"] <= NOW.isoformat()]
for i, ep in enumerate(wild[:3]):
    run(by_title["Wheeler Dealers"], ep, 400 - i * 120, "failed", took=1)
airing = [e for e in EPISODES[by_title["The Bear"]["id"]] if not e["hasFile"] and e["airDateUtc"] <= NOW.isoformat()]
live = run(by_title["The Bear"], airing[0], 2, "running", kind="manual", running=True)

sync.inbox_add("success", "Downloaded: The Simpsons S02E11", "1.4 GB in 5 min, imported by Sonarr.", batch=batch)
sync.inbox_add("error", "Not downloaded: Wheeler Dealers S01E07", "RMC BFM Play: your session has expired, log in again (cookies or credentials)")
sync.inbox_add("warning", "HULU cookies expire in 5 days", "Replace them before, in Settings, Cookies.")
sync.inbox_add("success", "Downloaded: Koh-Lanta S01E09", "Both parts joined into one file, imported by Sonarr.")
(DATA / "notifications_sent.json").write_text(json.dumps([
    {"at": ago(hours=h), "level": lv, "title": t, "to": [{"app": "Discord", "ok": True}, {"app": "ntfy", "ok": lv != "success"}]}
    for h, lv, t in ((1, "success", "Downloaded: The Simpsons S02E11"), (5, "error", "Not downloaded: Wheeler Dealers S01E07"),
                     (26, "warning", "HULU cookies expire in 5 days"))]))


# ---- Record ----

async def record() -> dict:
    from aiohttp.test_utils import TestClient, TestServer
    out = {"recorded": NOW.isoformat(), "password": PASSWORD, "routes": {}, "logs": {}, "live": live and live["id"]}
    async with TestClient(TestServer(web.app)) as client:
        h = {"X-Unshackle": "1"}
        assert (await client.post("/api/login", json={"password": PASSWORD}, headers=h)).status == 200

        async def get(path, key=None, method="GET", body=None):
            r = await client.request(method, path, json=body, headers=h)
            if r.status != 200:
                print(f"  {method} {path}: {r.status} {(await r.text())[:200]}", file=sys.stderr)
                return
            out["routes"][key or path] = await r.json()

        for s in SERIES:
            if s["service"]:
                await get("/api/probe", f"POST /api/probe {s['tvdbId']}", "POST",
                          {"show": config["series"][s["tvdbId"]], "seriesId": s["id"], "tvdbId": s["tvdbId"], "title": s["title"]})
        for path in ("/api/session", "/api/state", "/api/schedule", "/api/runs", "/api/busy", "/api/inbox", "/api/stats",
                     "/api/status", "/api/log", "/api/cdm", "/api/cookies", "/api/notifications/sent", "/api/leftovers",
                     "/api/push/key", "/api/changelog"):
            await get(path)
        await get("/api/status?full=1")
        for days in ("", "?days=7", "?days=30", "?days=90"):  # Activity, Catch up
            await get(f"/api/missing{days}")
        await get("/api/upgrades")  # Activity, Upgrades (nothing checked yet: a check asks the service)
        start = (NOW.astimezone(sync.LOCAL) - timedelta(days=31)).date().isoformat()
        await get(f"/api/calendar?start={start}&days=62", "/api/calendar")
        await get("/api/unshackle/config-file/open", method="POST", body={"password": PASSWORD})
        for s in SERIES:
            await get(f"/api/series/{s['id']}/episodes")
            await get(f"/api/probe/{s['tvdbId']}")
            await get(f"/api/series/{s['tvdbId']}/release")
        for tag, *_ in SERVICES:
            await get(f"/api/services/{tag}")
    for f in sorted(sync.RUNS_DIR.glob("*.log")):
        out["logs"][f.stem] = f.read_text()
    return out


sync.sonarr_get = sonarr_get
def sonarr_series():
    return sorted([{"id": s["id"], "tvdbId": s["tvdbId"], "tmdbId": s["tmdbId"], "titleSlug": s["titleSlug"], "title": s["title"],
                    "year": s["year"], "monitored": True, "status": s["status"],
                    "missing": s["statistics"]["episodeCount"] - s["statistics"]["episodeFileCount"],
                    "poster": s["images"][0]["remoteUrl"]} for s in SERIES], key=lambda s: s["title"].lower())


web.sonarr_series = sonarr_series
web.sonarr_get_version = lambda: "4.0.15.2941"
web.tvdb_titles = lambda tvdb_id: {}
sync.UNSHACKLE.call = serve
if live:
    sync.EpisodeRun.active.add(live["id"])
web.check_health()

if __name__ == "__main__":
    data = asyncio.run(record())
    text = json.dumps(data, ensure_ascii=False).replace(str(DATA / "unshackle"), "/config/unshackle").replace(str(DATA), "/data")
    Path(sys.argv[1]).write_text(text)
    print(f"{len(data['routes'])} answers, {len(data['logs'])} logs → {sys.argv[1]}")
