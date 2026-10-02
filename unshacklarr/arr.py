"""Unshacklarr as Sonarr sees it: an indexer and a download client, so Sonarr drives the downloads.

- The indexer speaks Torznab (/torznab/api): asked for an episode of a series set up here, it offers
  one release, "Title.S02E03.1080p.ATV.WEB-DL-Unshacklarr", whose magnet link names the episode.
- The download client speaks qBittorrent's Web API (/qbittorrent/api/v2/…): given that magnet, the
  episode joins Unshacklarr's own queue (Activity shows it), downloads with Unshackle, and is left in
  the downloads folder for Sonarr to import itself. An episode not on the service yet waits here,
  tried again every few minutes, so Sonarr never takes it for a failure and never blocks the release.

Both take the downloader key (Settings, Sonarr): the indexer as its API key, the client as its
password (any user name).
"""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import shutil
import socket
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from xml.sax.saxutils import escape

import requests
from aiohttp import web

from unshacklarr import sync
from unshacklarr.files import write_atomic

# Set by web.py: the config, a download started in the background (run_sync), and which of a series'
# episodes its service really has (its "What's on the service" check, cached)
read_config = write_config = start = available = None

CATEGORY = "tv-sonarr"
RETRY_MINUTES = 10  # an episode not on the service yet is tried again this often
MB_PER_MINUTE = 25  # the size a release says it has: Sonarr checks it against the quality's limits


def key() -> str:
    """The downloader key, made the first time it is asked for."""
    config = read_config()
    if not config["auth"].get("downloader_key"):
        config["auth"]["downloader_key"] = secrets.token_urlsafe(24)
        write_config(config)
    return config["auth"]["downloader_key"]


def new_key() -> str:
    config = read_config()
    config["auth"]["downloader_key"] = secrets.token_urlsafe(24)
    write_config(config)
    return config["auth"]["downloader_key"]


def session_id() -> str:
    return hmac.new(key().encode(), b"qbittorrent", hashlib.sha256).hexdigest()


# ---- Releases: what the indexer offers, and the magnet that brings it back to the client ----

def info_hash(tvdb: int, season: int, episode: int) -> str:
    return hashlib.sha1(f"unshacklarr:{tvdb}:{season}:{episode}".encode()).hexdigest()


def dotted(title: str) -> str:
    return re.sub(r"[^\w]+", ".", re.sub(r"['’]", "", title)).strip(".")


def quality(show: dict, config: dict) -> str:
    """The resolution the series downloads in, from its -q/--quality option; 1080p otherwise."""
    dl, _ = sync.stacked(show, config)
    wanted = str(dl.get("--quality") or dl.get("-q") or "")
    return f"{m.group(0)}p" if (m := re.search(r"\d{3,4}", wanted)) else "1080p"


def release(ep: dict, show: dict, config: dict, theirs: str | None = None) -> dict:
    """The release offered for an episode; `theirs`, the service's number for it when not the series' own
    numbering (found by its title): the download then asks for that one."""
    tvdb, s, e = ep["series"]["tvdbId"], ep["seasonNumber"], ep["episodeNumber"]
    title = f"{dotted(ep['series']['title'])}.S{s:02}E{e:02}.{quality(show, config)}.{show['service']}.WEB-DL-Unshacklarr"
    h = info_hash(tvdb, s, e)
    minutes = int(ep["series"].get("runtime") or 45) or 45
    other = theirs and theirs != sync.service_episode(show, s, e)
    return {"title": title, "hash": h, "magnet": f"magnet:?xt=urn:btih:{h}&dn={quote(title)}&tvdb={tvdb}&s={s}&e={e}" + (f"&svc={theirs}" if other else ""),
            "size": minutes * MB_PER_MINUTE * 1_000_000, "tvdb": tvdb, "season": s, "episode": e,
            "date": sync.parse_time(ep["airDateUtc"]) if ep.get("airDateUtc") else datetime.now(timezone.utc)}


def offered(ep: dict, show: dict | None, now: datetime) -> bool:
    """An episode the indexer offers: its series set up with a service, and out by now
    (its release time when it has one, else its air date)."""
    if not show or not show.get("service") or not show.get("title") or not ep.get("airDateUtc"):
        return False
    if sync.service_episode(show, ep["seasonNumber"], ep["episodeNumber"]) is None:
        return False
    return (sync.release_slot(show, ep) or sync.parse_time(ep["airDateUtc"])) <= now


def search(params: dict, now: datetime) -> list[dict]:
    """The releases for a Torznab query: an episode or a season of a series by its TVDB id, or, with
    no id (Sonarr's RSS sync), the missing episodes of the series set up here."""
    config = read_config()
    series = config["series"]
    tvdb = params.get("tvdbid")
    if tvdb:
        show = series.get(int(tvdb)) if str(tvdb).isdigit() else None
        if not show:
            return []
        found = sync.sonarr_get("series", tvdbId=int(tvdb))
        if not found:
            return []
        episodes = sync.sonarr_get("episode", seriesId=found[0]["id"])
        for ep in episodes:
            ep["series"] = found[0]
        season, number = params.get("season"), params.get("ep")
        episodes = [ep for ep in episodes if (not season or ep["seasonNumber"] == int(season)) and (not number or ep["episodeNumber"] == int(number))]
    elif params.get("q"):
        return []  # a free-text search: Sonarr asks by TVDB id
    else:
        since = now - timedelta(days=sync.AUTO_DAYS)
        episodes = [ep for ep in sync.missing_episodes() if ep.get("airDateUtc") and sync.parse_time(ep["airDateUtc"]) >= since]
    episodes = [ep for ep in episodes if offered(ep, series.get(ep["series"]["tvdbId"]), now)]
    # Out by its date is not enough: an indexer's result is a file to have now. The service is asked, per series.
    on_service = []
    for tvdb_id in dict.fromkeys(ep["series"]["tvdbId"] for ep in episodes):
        mine = [ep for ep in episodes if ep["series"]["tvdbId"] == tvdb_id]
        try:
            has = available(tvdb_id, series[tvdb_id], mine[0]["series"], [ep["id"] for ep in mine])
        except Exception as e:  # the service or Unshackle down: nothing to offer, not a promise
            print(f"Indexer: {mine[0]['series']['title']}: the service could not be checked ({type(e).__name__}: {e})", flush=True)
            continue
        on_service += [(ep, has[ep["id"]]) for ep in mine if ep["id"] in has]
    return [release(ep, series[ep["series"]["tvdbId"]], config, theirs) for ep, theirs in on_service][:100]


CAPS = """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server title="Unshacklarr"/>
  <limits max="100" default="100"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <tv-search available="yes" supportedParams="q,season,ep,tvdbid"/>
    <movie-search available="no" supportedParams="q"/>
  </searching>
  <categories>
    <category id="5000" name="TV"><subcat id="5040" name="TV/HD"/><subcat id="5045" name="TV/UHD"/></category>
  </categories>
</caps>"""


def feed(releases: list[dict]) -> str:
    def item(r: dict) -> str:
        magnet = escape(r["magnet"], {'"': "&quot;"})
        cat = "5045" if ".2160p." in r["title"] else "5040"
        attrs = {"category": cat, "seeders": 100, "peers": 100, "infohash": r["hash"], "magneturl": r["magnet"],
                 "tvdbid": r["tvdb"], "season": r["season"], "episode": r["episode"],
                 "downloadvolumefactor": 0, "uploadvolumefactor": 1}
        return (f"<item><title>{escape(r['title'])}</title><guid>{r['hash']}</guid><link>{magnet}</link>"
                f"<pubDate>{format_datetime(r['date'])}</pubDate><size>{r['size']}</size>"
                f'<enclosure url="{magnet}" length="{r["size"]}" type="application/x-bittorrent"/>'
                + "".join(f'<torznab:attr name="{k}" value="{escape(str(v), {chr(34): "&quot;"})}"/>' for k, v in attrs.items())
                + "</item>")

    return ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">'
            f"<channel><title>Unshacklarr</title>{''.join(item(r) for r in releases)}</channel></rss>")


# Sonarr's test of a new indexer wants at least one result: when nothing is missing, a release of no series
PLACEHOLDER = {"title": "Unshacklarr.Test.S01E01.1080p.WEB-DL-Unshacklarr", "hash": "0" * 40, "size": 1_000_000_000,
               "magnet": "magnet:?xt=urn:btih:" + "0" * 40, "tvdb": 0, "season": 1, "episode": 1,
               "date": datetime(2000, 1, 1, tzinfo=timezone.utc)}


async def torznab(request):
    params = dict(request.query)
    if not hmac.compare_digest(params.get("apikey", ""), key()):
        return web.Response(status=401, text='<?xml version="1.0"?><error code="100" description="Wrong API key"/>', content_type="application/xml")
    kind = params.get("t", "search")
    if kind == "caps":
        return web.Response(text=CAPS, content_type="application/xml")
    if kind not in ("search", "tvsearch"):
        return web.Response(text=feed([]), content_type="application/xml")
    try:
        found = await asyncio.to_thread(search, params, datetime.now(timezone.utc))
    except Exception as e:  # Sonarr down for a moment: an error it shows, not a crash
        return web.Response(status=500, text=f'<?xml version="1.0"?><error code="900" description="{escape(str(e))[:200]}"/>', content_type="application/xml")
    if not found and not params.get("tvdbid") and not params.get("q"):
        found = [PLACEHOLDER]
    return web.Response(text=feed(found), content_type="application/xml")


# ---- Added to Sonarr through its API: the download client, then the indexer that sends to it alone ----

NAME = "Unshacklarr"


def filled(schema: dict, values: dict, **top) -> dict:
    """A Sonarr schema (an indexer's, a client's) with its fields set: the others keep their defaults."""
    body = {**schema, **top, "name": NAME}
    body["fields"] = [{**f, "value": values[f["name"]]} if f["name"] in values else f for f in schema.get("fields", [])]
    return body


def sonarr_save(kind: str, body: dict) -> dict:
    """POST a new one, or PUT over the one already named Unshacklarr. Sonarr tests it first: its
    answer when the test fails (Unshacklarr unreachable at that address…) comes back as an error."""
    existing = next((x for x in sync.sonarr_get(kind) if x.get("name") == NAME), None)
    url = f"{sync.SONARR}/api/v3/{kind}" + (f"/{existing['id']}" if existing else "")
    r = (requests.put if existing else requests.post)(url, headers=sync.HEADERS, json={**body, **({"id": existing["id"]} if existing else {})}, timeout=60)
    if r.status_code >= 400:
        try:
            why = "; ".join(e.get("errorMessage", "") for e in r.json()) if isinstance(r.json(), list) else r.json().get("message", r.text)
        except ValueError:
            why = r.text
        raise ValueError(f"Sonarr said: {why.strip()[:300] or r.status_code}")
    return r.json()


def candidates(port: int, browser: str) -> list[str]:
    """Where Sonarr may reach Unshacklarr, the likeliest first: this container's names on a network shared with
    Sonarr (its service name, its host name), the address this machine uses to reach Sonarr, then the browser's."""
    found = []
    sonarr = urlparse(sync.SONARR)
    if sonarr.hostname and not re.fullmatch(r"[\d.]+|localhost|\[.*\]", sonarr.hostname):  # Sonarr by a name: a Docker network
        found += [f"http://unshacklarr:{port}", f"http://{socket.gethostname()}:{port}"]
    try:  # the address of the interface that leads to Sonarr (a UDP "connect" sends nothing)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((socket.gethostbyname(sonarr.hostname or "localhost"), sonarr.port or 80))
            found.append(f"http://{probe.getsockname()[0]}:{port}")
    except OSError:
        pass
    found.append(browser.rstrip("/"))
    return list(dict.fromkeys(a for a in found if a))


def add_to_sonarr(addresses: list[str]) -> str:
    """Unshacklarr in Sonarr, at the first address Sonarr's own test accepts; that address."""
    reasons = []
    for address in addresses:
        try:
            add_at(address)
            return address
        except ValueError as e:  # Sonarr could not reach it there: the next one
            reasons.append(f"{address}: {e}")
    raise ValueError("Sonarr reached Unshacklarr at none of these addresses. " + " · ".join(reasons))


def add_at(address: str) -> None:
    """Unshacklarr in Sonarr, as Sonarr reaches it at `address` (http://unshacklarr:8788 in a shared Docker network)."""
    u = urlparse(address.strip().rstrip("/"))
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("The address must look like http://unshacklarr:8788")
    base = f"{u.scheme}://{u.netloc}{u.path}"
    k = key()
    client_schema = next(s for s in sync.sonarr_get("downloadclient/schema") if s.get("implementation") == "QBittorrent")
    client = sonarr_save("downloadclient", filled(client_schema, {
        "host": u.hostname, "port": u.port or (443 if u.scheme == "https" else 80), "useSsl": u.scheme == "https",
        "urlBase": f"{u.path.rstrip('/')}/qbittorrent", "username": "unshacklarr", "password": k, "tvCategory": CATEGORY,
    }, enable=True, priority=1, removeCompletedDownloads=True, removeFailedDownloads=True))
    indexer_schema = next(s for s in sync.sonarr_get("indexer/schema") if s.get("implementation") == "Torznab")
    sonarr_save("indexer", filled(indexer_schema, {
        "baseUrl": f"{base}/torznab", "apiPath": "/api", "apiKey": k, "categories": [5040, 5045], "minimumSeeders": 1,
    }, enableRss=True, enableAutomaticSearch=True, enableInteractiveSearch=True, downloadClientId=client["id"]))


# ---- The download client: what Sonarr sent, and where each stands ----

def store_file() -> Path:
    return sync.DATA / "downloader.json"


def stored() -> dict:
    try:
        return json.loads(store_file().read_text())
    except (OSError, ValueError):
        return {}


def save(items: dict) -> None:
    write_atomic(store_file(), json.dumps(items))


def from_magnet(url: str) -> dict | None:
    """The episode a magnet from the indexer names, or None for any other."""
    q = parse_qs(urlparse(url).query)
    xt = (q.get("xt") or [""])[0]
    h = xt.rsplit(":", 1)[-1].lower()
    try:
        tvdb, s, e = int(q["tvdb"][0]), int(q["s"][0]), int(q["e"][0])
    except (KeyError, ValueError, IndexError):
        return None
    if h != info_hash(tvdb, s, e):
        return None
    theirs = (q.get("svc") or [""])[0].upper()
    return {"hash": h, "name": (q.get("dn") or [f"S{s:02}E{e:02}"])[0], "tvdb": tvdb, "season": s, "episode": e,
            **({"theirs": theirs} if re.fullmatch(r"S\d{2,}E\d{2,}(\.\d+)?", theirs) else {})}


def sonarr_episode(tvdb: int, season: int, number: int) -> dict | None:
    found = sync.sonarr_get("series", tvdbId=tvdb)
    if not found:
        return None
    for ep in sync.sonarr_get("episode", seriesId=found[0]["id"]):
        if ep["seasonNumber"] == season and ep["episodeNumber"] == number:
            return {**ep, "series": found[0]}
    return None


def folder(item: dict) -> Path:
    return sync.DOWNLOADS / f"unshackle-{item['tvdb']}-S{item['season']:02}E{item['episode']:02}"


def last_card(item: dict) -> dict | None:
    cards = sorted(sync.RUNS_DIR.glob(f"*-{item['tvdb']}-S{item['season']:02}E{item['episode']:02}.json"))
    for path in reversed(cards):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            continue
    return None


def videos(path: Path) -> list[Path]:
    return [f for f in path.rglob("*") if f.suffix.lower() in (".mkv", ".mp4")] if path.is_dir() else []


SPEED = re.compile(r"([\d.]+)\s*([KMG]?)i?B/s", re.IGNORECASE)


def standing(item: dict) -> dict:
    """Where an episode Sonarr sent stands, as qBittorrent says it: queued, downloading, done, or an error."""
    out, card = folder(item), last_card(item)
    running = card and not card.get("ended") and card["id"] in sync.EpisodeRun.active
    if item.get("episode_id") in sync.waiting:
        return {"state": "queuedDL", "progress": 0}
    if running:
        live = card.get("live") or {}
        progress = float(live.get("progress") or 0) / 100 * 0.95 if card.get("step") in ("queued", "downloading") else 0.97
        m = SPEED.search(str(live.get("speed") or ""))
        speed = int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9}[m.group(2).upper()]) if m else 0
        return {"state": "downloading", "progress": progress, "dlspeed": speed}
    if files := videos(out):
        return {"state": "pausedUP", "progress": 1, "size": sum(f.stat().st_size for f in files)}
    if item.get("error"):
        return {"state": "error", "progress": 0}
    if card and card.get("outcome") == "failed" and card["started"] >= item["added"]:
        return {"state": "error", "progress": 0}
    return {"state": "stalledDL", "progress": 0}  # not on the service yet: tried again in a moment


def torrent(h: str, item: dict) -> dict:
    now = int(datetime.now(timezone.utc).timestamp())
    st = standing(item)
    size = st.get("size") or item.get("size") or 1_000_000_000
    save_path = sync.seen_by("sonarr_downloads", sync.DOWNLOADS)
    done = st["state"] == "pausedUP"
    return {
        "hash": h, "name": item["name"], "size": size, "total_size": size, "progress": st["progress"],
        "dlspeed": st.get("dlspeed", 0), "upspeed": 0, "eta": 0 if done else 8640000, "state": st["state"],
        "category": item.get("category", CATEGORY), "tags": "", "save_path": save_path,
        "content_path": sync.seen_by("sonarr_downloads", folder(item)), "added_on": int(item.get("added_ts", now)),
        "completion_on": now if done else -1, "ratio": 0, "ratio_limit": -2, "seeding_time": 0, "seeding_time_limit": -2,
        "num_seeds": 1, "num_leechs": 0, "amount_left": 0 if done else size, "downloaded": size if done else 0,
        "priority": 0, "last_activity": now,
    }


def add(urls: list[str], category: str) -> bool:
    """Episodes Sonarr grabbed: kept, and started (an episode already here or on its way is not started twice)."""
    items, added = stored(), False
    for url in urls:
        wanted = from_magnet(url.strip())
        if not wanted:
            continue
        ep = sonarr_episode(wanted["tvdb"], wanted["season"], wanted["episode"])
        if not ep:
            continue
        items[wanted["hash"]] = {**{k: wanted[k] for k in ("name", "tvdb", "season", "episode", "theirs") if k in wanted}, "episode_id": ep["id"],
                                 "category": category or CATEGORY, "added": datetime.now(timezone.utc).isoformat(),
                                 "added_ts": int(datetime.now(timezone.utc).timestamp()),
                                 "size": int(ep["series"].get("runtime") or 45) * MB_PER_MINUTE * 1_000_000}
        added = True
        save(items)
        item = items[wanted["hash"]]
        if not videos(folder(item)) and standing(item)["state"] not in ("queuedDL", "downloading"):
            start([ep["id"]], kind="sonarr", numbering=numbering_for(item))
    return added


def numbering_for(item: dict) -> dict | None:
    """The series' numbering, with this episode asked under the service's own number when the indexer found
    it there by its title; None: the series' numbering as it is."""
    if not item.get("theirs"):
        return None
    show = read_config()["series"].get(item["tvdb"]) or {}
    numbering = {k: show[k] for k in sync.NUMBERING if k in show}
    numbering["episode_map"] = {**(show.get("episode_map") or {}), f"S{item['season']:02}E{item['episode']:02}": item["theirs"]}
    return numbering


def remove(hashes: list[str], files: bool) -> None:
    items = stored()
    for h in hashes:
        item = items.pop(h, None)
        if item and files and standing(item)["state"] not in ("queuedDL", "downloading"):
            shutil.rmtree(folder(item), ignore_errors=True)
    save(items)


def retry_due(now: datetime) -> list[dict]:
    """Episodes Sonarr sent that were not on the service yet: their next try, until the automatic
    sync would give up on them too."""
    due, items, changed = [], stored(), False
    for item in items.values():
        st = standing(item)["state"]
        if st != "stalledDL" or item.get("error"):
            continue
        card = last_card(item)
        if card and card.get("ended") and sync.parse_time(card["ended"]) > now - timedelta(minutes=RETRY_MINUTES):
            continue
        if sync.parse_time(item["added"]) < now - timedelta(days=sync.AUTO_DAYS):
            item["error"] = f"Not on the service after {sync.AUTO_DAYS} days"
            changed = True
            continue
        due.append({"ids": [item["episode_id"]], "numbering": numbering_for(item)})
    if changed:
        save(items)
    return due


# ---- qBittorrent's Web API, the part Sonarr uses ----

def logged_in(request) -> bool:
    return hmac.compare_digest(request.cookies.get("SID", ""), session_id())


async def qbittorrent(request):
    path = request.match_info["path"]
    form = await request.post() if request.method == "POST" else {}
    if path == "auth/login":
        if not hmac.compare_digest(str(form.get("password", "")), key()):
            return web.Response(text="Fails.")
        response = web.Response(text="Ok.")
        response.set_cookie("SID", session_id(), httponly=True)
        return response
    if not logged_in(request):
        return web.Response(status=403, text="Forbidden")
    q = {**request.query, **{k: v for k, v in form.items() if isinstance(v, str)}}
    hashes = [h.lower() for h in str(q.get("hashes") or q.get("hash") or "").split("|") if h]
    items = stored()
    if path == "app/version":
        return web.Response(text="v4.6.7")
    if path == "app/webapiVersion":
        return web.Response(text="2.9.3")
    if path == "app/buildInfo":
        return web.json_response({"qt": "6.5", "libtorrent": "2.0", "boost": "1.83", "openssl": "3", "bitness": 64})
    if path == "app/preferences":
        return web.json_response({"save_path": sync.seen_by("sonarr_downloads", sync.DOWNLOADS), "max_ratio_enabled": True,
                                  "max_ratio": 0, "max_ratio_act": 0, "max_seeding_time_enabled": False, "max_seeding_time": -1,
                                  "queueing_enabled": False, "dht": False, "web_ui_username": "unshacklarr"})
    if path == "torrents/categories":
        cats = {i.get("category", CATEGORY) for i in items.values()} | {CATEGORY}
        save_path = sync.seen_by("sonarr_downloads", sync.DOWNLOADS)
        return web.json_response({c: {"name": c, "savePath": save_path} for c in sorted(cats)})
    if path == "torrents/info":
        category = q.get("category")
        return web.json_response(await asyncio.to_thread(lambda: [torrent(h, i) for h, i in items.items()
                                 if (not category or i.get("category", CATEGORY) == category) and (not hashes or h in hashes)]))
    if path == "torrents/properties":
        item = items.get(hashes[0]) if hashes else None
        if not item:
            return web.Response(status=404, text="Not Found")
        t = torrent(hashes[0], item)
        return web.json_response({"save_path": t["save_path"], "seeding_time": 0, "share_ratio": 0, "total_size": t["size"],
                                  "addition_date": t["added_on"], "completion_date": t["completion_on"]})
    if path == "torrents/files":
        item = items.get(hashes[0]) if hashes else None
        if not item:
            return web.Response(status=404, text="Not Found")
        out = folder(item)
        return web.json_response([{"name": str(f.relative_to(out.parent)), "size": f.stat().st_size, "progress": 1, "priority": 1}
                                  for f in videos(out)])
    if path == "torrents/add":
        urls = [u for u in str(form.get("urls") or "").splitlines() if u.strip()]
        ok = await asyncio.to_thread(add, urls, str(form.get("category") or CATEGORY))
        return web.Response(text="Ok." if ok else "Fails.")
    if path == "torrents/delete":
        await asyncio.to_thread(remove, hashes, str(q.get("deleteFiles", "")).lower() == "true")
        return web.Response(text="")
    if path == "torrents/setCategory":
        for h in hashes:
            if h in items:
                items[h]["category"] = str(q.get("category") or CATEGORY)
        save(items)
        return web.Response(text="")
    # Asked of any client, meaningless here: pause, resume, priorities, share limits, a new category, a recheck…
    return web.Response(text="")
