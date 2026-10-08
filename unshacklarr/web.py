"""Unshacklarr's web page: which Sonarr series Unshackle downloads, and with which options.

It edits config.yaml, runs the syncs in the background and follows the downloads that
unshackle serve makes. A password protects it: set with the first-run setup, then asked
for by a login page.
"""

import asyncio
import base64
import copy
import difflib
import hashlib
import hmac
import html
import ipaddress
import json
import ntpath
import os
import posixpath
import re
import secrets
import shutil
import sys
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import apprise
import requests
import yaml
from aiohttp import web

from unshacklarr import __version__, cdm, cookies, i18n, offsite, options
from unshacklarr.files import PRIVATE, no_credentials, read_json, write_atomic
from unshacklarr import sync as sonarr_sync  # the schedule shows the very downloads the sync will ask for
from unshacklarr.backend import UnshackleError

HERE = Path(__file__).parent
SERIES_FILE = sonarr_sync.CONFIG_FILE
UNSHACKLE = sonarr_sync.UNSHACKLE

# Streaming sites, and the codes a service for each goes by in one Unshackle or another (HBO Max is MAX here,
# HMAX there), best first: for a site no installed service names in its help, the first of them installed.
SITES = {
    "hbomax.com": ("MAX", "HMAX"), "max.com": ("MAX", "HMAX"),
    "tv.apple.com": ("ATV", "ATVP"),
    "primevideo.com": ("AMZN",), "amazon.fr": ("AMZN",), "amazon.com": ("AMZN",),
    "disneyplus.com": ("DSNP",), "canalplus.com": ("CanalPlus",), "crave.ca": ("CRAVE",),
    "m6.fr": ("M6",), "m6plus.fr": ("M6",), "channel4.com": ("ALL4", "C4"),
}
# A network's own catch-up service, by its name's first words (Sonarr's network): what a series TMDB links to
# nowhere is offered, best code first. Extend per country.
NETWORKS = {"bbc": ("iP",), "cbbc": ("iP",), "channel 4": ("ALL4", "C4"), "e4": ("ALL4", "C4"), "more4": ("ALL4", "C4"),
            "itv": ("ITVX", "ITV"), "channel 5": ("MY5",), "u&": ("UKTV",), "abc (au)": ("iView", "AUBC"), "sbs": ("SBS",),
            "seven network": ("7plus", "SEVEN"), "network 10": ("TEN",), "tvnz": ("TVNZ",), "m6": ("M6",), "w9": ("M6",), "6ter": ("M6",)}


def service_for_network(network: str, installed: set[str]) -> str | None:
    """The installed service a network's catch-up is on (BBC One: iP), by the network's first words."""
    name = str(network or "").lower().strip()
    for key, tags in NETWORKS.items():
        if name == key or name.startswith(key + " "):
            return next((t for t in tags if t in installed), None)
    return None

sync_threads: list[threading.Thread] = []
last_sync: datetime | None = None


def service_tags() -> list[str]:
    return sorted(s["tag"] for s in sonarr_sync.all_services())


def service_name(service: dict) -> str:
    """What people call a service ("RMC+" for RMCP), from its help ("Service code for RMC+ (https://…)"),
    else its site ("netflix.com"), else its tag."""
    first = (service.get("help") or "").strip().split("\n")[0]
    m = re.match(r"Service (?:code )?(?:for|pour) (.+?)(?:\s*[(\[—]|\.?$)", first)
    name = re.sub(r"(?:'s)? streaming service$|,? streaming service$", "", m.group(1), flags=re.IGNORECASE).strip() if m else ""
    if not name or name.startswith("http"):
        name = (urlparse(str(service.get("url") or "").split(",")[0].strip()).hostname or "").removeprefix("www.")
    return name or service["tag"]


def service_domains() -> dict[str, str]:
    """Map each site to the installed service for it: what the services name in their help (e.g. "https://crave.ca")
    first, then the SITES the help leaves out, under the code this Unshackle has for them. A site with no installed
    service for it maps to nothing: a suggestion never names a service Unshackle does not have."""
    services = sonarr_sync.all_services()
    installed = {s["tag"] for s in services}
    domains = {}
    for service in sorted(services, key=lambda s: s["tag"]):
        for url in re.findall(r"https?://[^\s,]+", " ".join(filter(None, [service.get("url"), service.get("help")]))):
            try:
                host = (urlparse(url.rstrip(").;:")).hostname or "").removeprefix("www.")  # "(https://max.com)." in a help
            except ValueError:  # a service's help may hold a malformed URL ("https://[…]")
                continue
            if host and service["tag"] != "EXAMPLE":
                domains.setdefault(host, service["tag"])
    for site, tags in SITES.items():
        if site not in domains and (tag := next((t for t in tags if t in installed), None)):
            domains[site] = tag
    return domains


def on_site(url: str, *sites: str) -> bool:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return any(host == site or host.endswith("." + site) for site in sites)


def service_for(url: str, domains: dict[str, str]) -> str | None:
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return next((tag for domain, tag in domains.items() if host == domain or host.endswith("." + domain)), None)


def series_title(url: str) -> str:
    """What the service accepts as its title argument, when the watch link is not it. By the link's site, whatever
    the code of the service for it (Apple TV+ is ATV here, ATVP there)."""
    query = parse_qs(urlparse(url).query)
    if on_site(url, "tv.apple.com") and query.get("showId"):  # the link opens an episode; the show is in showId
        return query["showId"][0]
    if on_site(url, "tv.apple.com") and (show := re.search(r"/show/[^/]+/(umc\.cmc\.[a-z0-9]+)", url)):
        return show.group(1)  # the same show in every country's store
    if on_site(url, "hbomax.com", "max.com") and (show := re.search(r"/(show|movie)/([0-9a-f-]{36})", url)):
        # …/ch/en/show/<id>/s1/e1-…: HBO Max wants <type>/<id> right after the domain
        return f"https://play.hbomax.com/{show.group(1)}/{show.group(2)}"
    if on_site(url, "canalplus.com"):  # tracking and episode parameters; the /h/<id> path is what counts
        return url.split("?")[0]
    if on_site(url, "disneyplus.com"):  # drop the locale (/fr-fr/, /en-ca/…): one entity, one suggestion
        return re.sub(r"(disneyplus\.com)/[a-z]{2}-[a-z]{2}/", r"\1/", url)
    if on_site(url, "primevideo.com", "amazon.com", "amazon.fr") and query.get("gti"):  # app.primevideo.com: not parsed
        return query["gti"][0]
    # These sites' links open one episode; the series page is in the path (an iPlayer episode's programme comes from
    # BBC's own programme page). From #12, by mj23au.
    path = urlparse(url).path
    if on_site(url, "bbc.co.uk") and (pid := re.search(r"/iplayer/episode/([a-z0-9]+)", path)):
        programme = bbc_programme(pid.group(1))  # the episode's own when BBC's page can't tell: the link stays an episode's
        return f"https://www.bbc.co.uk/iplayer/episodes/{programme}" if programme != pid.group(1) else url
    if on_site(url, "channel4.com") and (show := re.match(r"/programmes/([a-z0-9-]+)", path)):
        return f"https://www.channel4.com/programmes/{show.group(1)}"
    if on_site(url, "itv.com") and (show := re.match(r"/watch/([a-z0-9-]+)/([a-z0-9]+)", path)):
        return f"https://www.itv.com/watch/{show.group(1)}/{show.group(2)}"
    if on_site(url, "channel5.com") and (show := re.match(r"/(?:show/)?([a-z0-9-]+)", path)):
        return f"https://www.channel5.com/show/{show.group(1)}"
    if on_site(url, "paramountplus.com") and (show := re.match(r"(?:/[a-z]{2})?/shows/(?!video/)([A-Za-z0-9_-]+)", path)):
        return f"https://www.paramountplus.com/shows/{show.group(1)}/"
    if on_site(url, "sbs.com.au") and (show := re.match(r"/ondemand/tv-series/([a-z0-9-]+)", path)):
        return f"https://www.sbs.com.au/ondemand/tv-series/{show.group(1)}"
    if on_site(url, "u.co.uk") and (show := re.match(r"/shows/([a-z0-9-]+)", path)):
        return f"https://u.co.uk/shows/{show.group(1)}/watch-online"
    return url


bbc_programmes: dict[str, str] = {}  # an iPlayer episode's pid -> its brand's (or its series')


def bbc_programme(pid: str) -> str:
    """The programme an iPlayer episode belongs to, from bbc.co.uk/programmes/<pid>.json: its brand, else its series;
    the episode itself when the page can't tell (then flagged as an episode link: see episode_link)."""
    if pid not in bbc_programmes:
        try:
            node = requests.get(f"https://www.bbc.co.uk/programmes/{pid}.json", timeout=10).json()["programme"]
            top = pid
            while node:
                top = node.get("pid") or top
                node = (node.get("parent") or {}).get("programme")
            if not re.fullmatch(r"[a-z0-9]+", top):
                return pid
            bbc_programmes[pid] = top
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return pid  # not kept: asked again next time
    return bbc_programmes[pid]


def episode_link(url: str) -> bool:
    """A link that still opens one episode once normalised: HBO Max's /video/watch/<id> (its show can't be told
    without logging in), an iPlayer episode whose programme page couldn't be read."""
    path = urlparse(url).path
    return (on_site(url, "hbomax.com", "max.com") and path.startswith("/video/watch/")) or (
        on_site(url, "bbc.co.uk") and path.startswith("/iplayer/episode/"))


def unwrap(url: str) -> str:
    """The service's own URL behind an affiliate redirect.

    Disney+ links go through disneyplus.bn5x.net/c/…?u=<the real URL>, a domain no
    service claims; without this, Disney+ never showed up in the suggestions.
    """
    for _ in range(3):
        query = parse_qs(urlparse(url).query)
        inner = next((query[k][0] for k in ("u", "url", "murl") if query.get(k) and query[k][0].startswith("http")), None)
        if not inner:
            break
        url = inner
    return url


TMDB_PAGES = threading.Lock()  # TMDB's site answers 429 to a burst: its pages are fetched one at a time
TMDB_GAP = 0.4  # seconds between two of them
# Its own name: TMDB's site answers 403 to a client calling itself a browser ("Mozilla/5.0") that does not
# talk like one, and lets an honest one through
TMDB_HEADERS = {"User-Agent": f"Unshacklarr/{__version__} (+https://github.com/OwnzZzZ/Unshacklarr)"}
tmdb_last = 0.0


def tmdb_page(url: str, params: dict) -> requests.Response:
    """A page of TMDB's site, politely: one at a time, a short gap between them, and on a
    429 a wait (its Retry-After, else a growing one) before asking again."""
    global tmdb_last
    for attempt in range(4):
        with TMDB_PAGES:
            time.sleep(max(0.0, tmdb_last + TMDB_GAP - time.monotonic()))
            r = requests.get(url, params=params, headers=TMDB_HEADERS, timeout=20)
            tmdb_last = time.monotonic()
            if r.status_code != 429 or attempt == 3:
                r.raise_for_status()
                return r
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(float(wait), 30) if wait.replace(".", "", 1).isdigit() else 2 * (attempt + 1))


CRAVE_LOCK = threading.Lock()
crave_ids: dict[str, str] = {}  # series slug: id, from Crave's sitemap
crave_read = 0.0


def crave_sitemap() -> dict[str, str]:
    """Crave's series, slug to id, from its sitemap: readable from anywhere, unlike its API."""
    global crave_ids, crave_read
    with CRAVE_LOCK:
        if crave_ids and time.monotonic() - crave_read < 86400:
            return crave_ids
        try:
            index = requests.get("https://www.crave.ca/sitemap.xml", timeout=20).text
            found: dict[str, str] = {}
            for part in re.findall(r"<loc>(https://www\.crave\.ca/crave-media-sitemap-\d+\.xml)</loc>", index):
                page = requests.get(part, timeout=20)
                page.raise_for_status()
                for slug, id_ in re.findall(r"/en/series/([a-z0-9-]+)-(\d+)</loc>", page.text):
                    found.setdefault(slug, id_)  # ponytail: two series with one slug take the first; none seen so far
        except requests.RequestException:
            return crave_ids  # the last good list, never an empty one
        if found:
            crave_ids, crave_read = found, time.monotonic()
        return crave_ids


def crave_series(url: str) -> str | None:
    """The series page of a Crave episode link: …/play/<series>/<episode>-<id> names the series."""
    slug = re.search(r"/play/([a-z0-9-]+)/", url)
    series_id = slug and crave_sitemap().get(slug.group(1))
    lang = "fr" if "/fr/" in url else "en"
    return f"https://www.crave.ca/{lang}/series/{slug.group(1)}-{series_id}" if series_id else None


def tmdb_links(tmdb_id: int, country: str, domains: dict[str, str]) -> list[dict]:
    """Service URLs from TMDB's "where to watch" page: its JustWatch links carry them in `r`."""
    r = tmdb_page(f"https://www.themoviedb.org/tv/{tmdb_id}/watch", {"locale": country})
    links = []
    for href in re.findall(r'href="(https://click\.justwatch\.com/[^"]+)"', r.text):
        url = unwrap(parse_qs(urlparse(href.replace("&amp;", "&")).query).get("r", [""])[0])
        service = service_for(url, domains)
        if service:
            # its site kept: the code for it may change (another Unshackle, a service renamed), the site does not
            link = {"service": service, "url": series_title(url), "country": country, "site": urlparse(url).hostname}
            if on_site(url, "crave.ca") and "/play/" in url:
                # Crave's link opens one episode and holds no series id (…/series/<slug>-<id>)
                if series := crave_series(url):
                    link["url"] = series
                else:  # the page asks for the series URL
                    link["needs_series_url"] = True
            elif re.search(r"-episode-\d+|episodeId=", link["url"]) or episode_link(link["url"]):  # still an episode once normalised
                link["episode"] = True  # dropped below when the same service also links the series
            links.append(link)
    return links


YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml's, when PyYAML has it


config_read: tuple[tuple, dict] = ((), {})


def read_config() -> dict:
    """The whole config, secrets included: only for the server's own use. Parsed again only when the
    file changed (every request reads it), a copy each time: callers change what they get."""
    global config_read
    try:
        st = SERIES_FILE.stat()
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
    except FileNotFoundError:
        stamp = ()
    if not stamp or stamp != config_read[0]:
        parsed = yaml.load(SERIES_FILE.read_text(encoding="utf8"), Loader=YAML_LOADER) if stamp else None
        config_read = (stamp, parsed or {})
    config = copy.deepcopy(config_read[1])
    return {
        "settings": config.get("settings") or {},
        "auth": config.get("auth") or {},
        "defaults": config.get("defaults") or {},
        "service_defaults": config.get("service_defaults") or {},
        "series": config.get("series") or {},
        "notifications": config.get("notifications") or {},
        "tmdb_countries": config.get("tmdb_countries") or [sonarr_sync.load_settings(config)["country"]],
        "hidden_series": config.get("hidden_series") or [],
        "quality_ladders": sonarr_sync.ladders(config),  # the built-in ones until some are saved
    }


SECRET_SETTINGS = ("sonarr_api_key", "tmdb_api_key", "unshackle_api_key", "backup_remote_secret", "backup_passphrase")
MASK = "•••"  # what stands for a secret the browser never gets
OPTION_LEVELS = ("options", "service_options")


def masked_url(url: str) -> str:
    """A notification address as the browser gets it: its app still shows (scheme, host, first plain path
    words), its tokens never do. Its #ref finds the real one again on save and test."""
    ref = hashlib.sha256(url.encode()).hexdigest()[:10]
    scheme, sep, rest = url.partition("://")
    keep = ""
    if sep and scheme.lower() in ("http", "https"):
        host, _, path = rest.partition("/")
        words = [w for w in path.split("/")[:2] if re.fullmatch(r"[a-z]{2,15}", w)]
        keep = host.rpartition("@")[2] + "/" + "".join(w + "/" for w in words)
    return f"{scheme}://{keep}{MASK}#{ref}" if sep else f"{MASK}#{ref}"


def real_url(url: str, saved: list[str]) -> str:
    """The address a masked one stands for, among those saved; a new one as typed."""
    if MASK not in url:
        return url
    found = next((u for u in saved if masked_url(u) == url), None)
    if found is None:
        raise web.HTTPBadRequest(text="A notification address changed meanwhile: reload the page")
    return found


def masked_options(opts: dict) -> dict:
    return {k: options.HIDDEN.sub(f"//{MASK}@", v) if isinstance(v, str) else v for k, v in (opts or {}).items()}


def real_options(opts: dict, saved: dict) -> dict:
    """Options from the browser, a proxy's masked credentials put back from the saved value."""
    out = {}
    for k, v in (opts or {}).items():
        if isinstance(v, str) and f"//{MASK}@" in v:
            old = (saved or {}).get(k)
            if not isinstance(old, str) or masked_options({k: old})[k] != v:
                raise web.HTTPBadRequest(text=f"Type the proxy's credentials of {k} again")
            v = old
        out[k] = v
    return out


def real_config(body: dict, saved: dict) -> None:
    """What the browser sends back, its masked secrets replaced by the saved ones."""
    body["defaults"] = real_options(body.get("defaults"), saved["defaults"])
    for service, levels in (body.get("service_defaults") or {}).items():
        for level in OPTION_LEVELS:
            levels[level] = real_options(levels.get(level), (saved["service_defaults"].get(service) or {}).get(level))
    for key, show in (body.get("series") or {}).items():
        before = saved["series"].get(int(key)) if str(key).isdigit() else None
        for level in OPTION_LEVELS:
            show[level] = real_options(show.get(level), (before or {}).get(level))
        if isinstance(show.get("fallback"), dict):
            show["fallback"]["service_options"] = real_options(show["fallback"].get("service_options"), ((before or {}).get("fallback") or {}).get("service_options"))
    notifications = body.get("notifications") or {}
    urls = [t["url"] for t in sonarr_sync.notification_targets(saved["notifications"])]
    for target in notifications.get("targets") or []:
        target["url"] = real_url(str(target.get("url") or ""), urls)


def public_config(config: dict) -> dict:
    """What the browser gets: no password hash, and API keys only as "set" or not."""
    settings = sonarr_sync.load_settings(config)
    shown = {k: v for k, v in settings.items() if k not in SECRET_SETTINGS}
    shown.update({f"{k}_set": bool(settings.get(k)) for k in SECRET_SETTINGS})
    shown["unshackle_mode"] = UNSHACKLE.mode
    shown["backends"] = [{**{k: v for k, v in b.items() if k != "api_key"}, "api_key_set": bool(b.get("api_key"))} for b in settings.get("backends") or []]
    shown["sonarrs"] = [{**{k: v for k, v in i.items() if k != "api_key"}, "api_key_set": bool(i.get("api_key"))} for i in settings.get("sonarrs") or []]
    notifications = {**config["notifications"], "targets": [{**t, "url": masked_url(t["url"])} for t in sonarr_sync.notification_targets(config["notifications"])]}
    notifications.pop("discord_webhook", None)
    notifications.pop("urls", None)
    # a proxy's credentials in options stay on the server too
    service_defaults = {svc: {**lv, **{level: masked_options(lv.get(level)) for level in OPTION_LEVELS}} for svc, lv in config["service_defaults"].items()}
    series = {key: {**show, **{level: masked_options(show.get(level)) for level in OPTION_LEVELS if level in show},
                    **({"fallback": {**show["fallback"], "service_options": masked_options(show["fallback"].get("service_options"))}} if show.get("fallback") else {})}
              for key, show in config["series"].items()}
    return {**{k: v for k, v in config.items() if k not in ("auth", "settings")}, "settings": shown, "notifications": notifications,
            "defaults": masked_options(config["defaults"]), "service_defaults": service_defaults, "series": series}


def tmdb_error(e: requests.RequestException) -> str:
    """A TMDB failure in words, never its URL: TMDB takes the API key in the query, and an error
    message quoting the URL showed it in the page and the logs."""
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status == 404:
        return "TMDB has no series with this id: Sonarr's TMDB id may be wrong"
    if status == 401:
        return "TMDB refused the API key: check it in Settings, Sonarr"
    if status:
        return f"TMDB answered {status}"
    return f"TMDB is unreachable ({type(e).__name__})"


def tmdb_key() -> str:
    return sonarr_sync.SETTINGS.get("tmdb_api_key") or ""


def write_config(config: dict) -> None:
    global config_read
    config_read = ((), {})  # read afresh next time, whatever the clock says
    # the session secret, the password hash and the API keys: its owner only, whatever it was before
    write_atomic(SERIES_FILE, yaml.safe_dump(config, allow_unicode=True, sort_keys=False), PRIVATE)
    sonarr_sync.apply_settings(sonarr_sync.load_settings(config))


def sonarr_series() -> list[dict]:
    r = requests.get(f"{sonarr_sync.SONARR}/api/v3/series", headers=sonarr_sync.HEADERS, timeout=30)
    r.raise_for_status()
    series = []
    for s in r.json():
        stats = s.get("statistics") or {}
        poster = next((i.get("remoteUrl") for i in s.get("images", []) if i.get("coverType") == "poster"), None)
        series.append({
            "id": s["id"],
            "tvdbId": s["tvdbId"],
            "tmdbId": s.get("tmdbId"),
            "titleSlug": s.get("titleSlug"),  # its page in Sonarr: /series/<slug>
            "title": s["title"],
            "year": s.get("year"),
            "monitored": s.get("monitored"),
            "status": s.get("status"),  # continuing, ended, upcoming: Suggest services looks at the living ones
            "network": s.get("network"),  # its channel: its catch-up service when TMDB links to none
            "missing": max(0, stats.get("episodeCount", 0) - stats.get("episodeFileCount", 0)),
            "poster": poster,
        })
    return sorted(series, key=lambda s: s["title"].lower())


page_cache: tuple[tuple, str] = ((), "")


def page_html() -> str:
    """index.html with each of its style and scripts at an address of its own content (app.css?v=…): an app on a
    phone that kept the old ones (iOS does, whatever no-cache says) gets the new ones with the new page."""
    global page_cache
    files = [HERE / "static" / "index.html", *(HERE / "static" / p.lstrip("/") for p in sorted(PAGE_FILES))]
    stamp = tuple((f.stat().st_mtime_ns, f.stat().st_size) for f in files)
    if stamp != page_cache[0]:
        html = files[0].read_text(encoding="utf8")
        for path in PAGE_FILES:
            version = hashlib.sha256((HERE / "static" / path.lstrip("/")).read_bytes()).hexdigest()[:10]
            html = html.replace(f'"{path}"', f'"{path}?v={version}"')
        page_cache = (stamp, html)
    return page_cache[1]


async def index(_):
    # Always revalidated: a cached page from before a deploy misreads the new API.
    return web.Response(text=await asyncio.to_thread(page_html), content_type="text/html", headers={"Cache-Control": "no-cache"})


async def static_file(request):
    # Public files: the home-screen icons and the manifest (fetched before anyone logs in), the editor's code.
    return web.FileResponse(HERE / "static" / request.path.lstrip("/"),
                            headers={"Cache-Control": "max-age=86400", "Content-Type": STATIC[request.path]})


async def page_file(request):
    # The page's own style and scripts: public as the page is. Asked for with their content's ?v=, they never change
    # under that address: kept for good, no revalidation per load. Without it, revalidated like the page.
    cache = "max-age=31536000, immutable" if request.query.get("v") else "no-cache"
    return web.FileResponse(HERE / "static" / request.path.lstrip("/"),
                            headers={"Cache-Control": cache, "Content-Type": PAGE_FILES[request.path]})


FAILING_AFTER = 3  # failed downloads in a row


def series_health(cards: list[dict]) -> dict[int, dict]:
    """Series whose last FAILING_AFTER finished tries all failed: a dead URL, a broken service,
    cookies gone. Tries that found nothing yet are not counted (they are not kept)."""
    health: dict[int, dict] = {}
    by_series: dict[int, list[dict]] = {}
    for c in cards:  # newest first
        if c.get("ended") and c.get("outcome") in ("downloaded", "failed", "kept") and not c.get("instance"):  # the main Sonarr's
            by_series.setdefault(c["tvdbId"], []).append(c)
    for tvdb, runs in by_series.items():
        streak = 0
        for c in runs:
            if c["outcome"] != "failed":
                break
            streak += 1
        if streak >= FAILING_AFTER:
            health[tvdb] = {"failing": streak, "cause": runs[0].get("cause") or "", "since": runs[streak - 1]["started"]}
    return health


async def state(_):
    def unshackle_side():
        try:  # the page still opens without Unshackle, to fix its settings
            names = {s["tag"]: service_name(s) for s in sonarr_sync.all_services()}
            # a service with none of its own uses the default CDM; a link pasted on a series picks its service
            return service_tags(), None, names, UNSHACKLE.cdm_config(), service_domains()
        except UnshackleError as e:
            return [], str(e), {}, {}, {}
    sonarr_side = asyncio.ensure_future(asyncio.to_thread(sonarr_series))  # both at once: the slower one sets the pace
    services, unshackle_error, names, cdm, domains = await asyncio.to_thread(unshackle_side)
    try:
        series = await sonarr_side
    except requests.RequestException as e:
        raise web.HTTPBadGateway(text=f"Sonarr is unreachable: {no_credentials(e)}")
    cards = await asyncio.to_thread(run_cards) if sonarr_sync.RUNS_DIR.exists() else []
    return web.json_response({
        "series": series,
        "health": {str(k): v for k, v in series_health(cards).items()},
        "config": public_config(read_config()),
        "services": services,
        "service_names": names,
        "unshackle_error": unshackle_error,
        "version": __version__,
        "update": update_info(),
        "dl_options": options.dl_specs(),
        "cdm": {str(k): v for k, v in cdm.items()},
        "service_domains": domains,
        "builtin_ladders": sonarr_sync.BUILTIN_LADDERS,
        "news": read_json(NEWS_FILE, {}),
        "backups": backups_info(),
        "network_services": {str(s["tvdbId"]): tag for s in series if (tag := service_for_network(s.get("network"), set(services)))},
        "instances": {str(k): v for k, v in (await asyncio.to_thread(instances_of_series)).items()},
        "sonarrs_of": {str(k): v for k, v in (await asyncio.to_thread(sonarrs_of_series)).items()},
    })


def with_remote(tag: str, specs: list[dict]) -> list[dict]:
    """A service's options, plus those its servers in remote_services declare for it (NF's
    --server-identity, say), for a download with --remote; marked with the server's name."""
    have = {s["flag"] for s in specs}
    specs = list(specs)
    for server in UNSHACKLE.remote_services():
        entry = next((s for s in server.get("services") or [] if s.get("tag") == tag), None)
        for spec in options.service_specs(entry) if entry else []:
            if spec["flag"] not in have:
                have.add(spec["flag"])
                specs.append({**spec, "remote": server["name"], "help": f"On {server['name']} (--remote). {spec['help']}".strip()})
    return specs


def specs_of(tag: str) -> list[dict]:
    entry = next((s for s in sonarr_sync.backend_for(tag).services() if s["tag"] == tag), None)
    specs = with_remote(tag, options.service_specs(entry) if entry else [])
    if entry is None and not specs:
        raise web.HTTPNotFound(text="Unknown service")
    return specs


async def service_options(request):
    try:
        return web.json_response(await asyncio.to_thread(specs_of, request.match_info["tag"]))
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))


def check_series_sonarrs(value, body: dict, dl_specs: list[dict], where: str) -> dict:
    """A series' own settings for each other Sonarr: {name: {"off", "options"}}; its ladder and After the download
    are that Sonarr's, for every series. Only the Sonarrs in Settings; empty ones are left out."""
    if not value:
        return {}
    if not isinstance(value, dict):
        raise web.HTTPBadRequest(text=f"The Sonarr settings of {where} must be an object")
    names = {str(i.get("name") or "") for i in (body.get("settings") or {}).get("sonarrs") or []} | set(sonarr_sync.SONARRS)
    out = {}
    for name, per in value.items():
        if name not in names or not isinstance(per, dict):
            raise web.HTTPBadRequest(text=f"{where}: no Sonarr named {name} in Settings, Sonarr")
        own = {}
        if per.get("off") is True:  # its new episodes are not downloaded for that Sonarr
            own["off"] = True
        if opts := check_options(per.get("options") or {}, dl_specs, f"{where} in {name}"):
            own["options"] = opts
        if own:
            out[name] = own
    return out


def check_options(opts: dict, specs: list[dict] | None, where: str) -> dict:
    """Only options Unshackle defines, with values of their type. None: the service's
    options are not known (Unshackle unreachable), so only their shape is checked."""
    by_flag = {s["flag"]: s for s in specs} if specs is not None else None
    for flag, value in opts.items():
        if by_flag is not None and flag not in by_flag:
            raise web.HTTPBadRequest(text=f"Unknown option {flag} in {where}")
        if not re.fullmatch(r"--[a-z0-9][a-z0-9-]*", flag) or not isinstance(value, (bool, str, int, float)):
            raise web.HTTPBadRequest(text=f"Invalid option {flag} in {where}")
    try:
        options.to_params(opts, specs or [])
    except ValueError as e:
        raise web.HTTPBadRequest(text=f"{e} in {where}")
    return opts


def known_services() -> dict[str, list[dict]] | None:
    try:
        config = sonarr_sync.read_file()
        return {s["tag"]: with_remote(s["tag"], options.service_specs(sonarr_sync.service_entry(s["tag"], config)))
                for s in sonarr_sync.all_services()}
    except UnshackleError:
        return None


def check_service(service: str, known: dict | None, where: str) -> list[dict] | None:
    if known is None:
        if not re.fullmatch(r"[\w-]+", service):
            raise web.HTTPBadRequest(text=f"Unknown service {service!r} for {where}")
        return None
    if service not in known:
        raise web.HTTPBadRequest(text=f"Unknown service {service!r} for {where}")
    return known[service]


async def series_search(request):
    """A series looked up by name on its service, with its profile and proxy, for its URL; nothing downloaded."""
    body = await json_object(request)
    show, query = body.get("show") or {}, str(body.get("query") or "").strip()[:200]
    if not show.get("service") or not query:
        raise web.HTTPBadRequest(text="Pick a service and type a name first")
    dl, _ = sonarr_sync.stacked(show, read_config())
    params = {k: v for k, v in options.to_params(dl, options.dl_specs()).items() if k in ("profile", "proxy", "no_proxy")}
    try:
        backend = await asyncio.to_thread(sonarr_sync.backend_for, show["service"])
        found = await asyncio.to_thread(backend.call, "POST", "/api/search", json={**params, "service": show["service"], "query": query})
    except UnshackleError as e:
        if re.search(r"not (supported|implemented)|NotImplemented", str(e), re.IGNORECASE):
            raise web.HTTPBadRequest(text=f"{show['service']} can't be searched: paste the series' URL instead") from None
        raise web.HTTPBadGateway(text=str(e))
    return web.json_response({"results": [
        {"id": str(r.get("id") or ""), "title": str(r.get("title") or ""), "label": str(r.get("label") or ""),
         "description": str(r.get("description") or "")[:240], "url": str(r.get("url") or "")}
        for r in (found.get("results") or [])[:30] if r.get("id") or r.get("url")]})


async def service_profiles(request):
    """The profiles a service can log in with: unshackle.yaml's credentials (as serve lists them) and its cookie files."""
    service = request.match_info["service"]
    found: set[str] = set()
    try:
        backend = await asyncio.to_thread(sonarr_sync.backend_for, service)
        found |= set((await asyncio.to_thread(backend.call, "GET", "/api/profiles")).get("profiles", {}).get(service) or [])
    except UnshackleError:
        pass
    try:
        files = await asyncio.to_thread(lambda: cookies.list_all(cookies_folder()).get(service) or [])
        found |= {f["profile"] for f in files if f["profile"]}
    except (UnshackleError, cdm.CdmError, OSError):
        pass
    return web.json_response({"profiles": sorted(found)})


async def save_config(request):
    body = await json_object(request)
    previous = read_config()
    real_config(body, previous)  # the secrets the browser only saw masked
    dl_specs = options.dl_specs()
    known = await asyncio.to_thread(known_services)
    ladders = check_ladders(body.get("quality_ladders"))
    series = {}
    for key, show in (body.get("series") or {}).items():
        service, title = show.get("service") or "", str(show.get("title") or "")
        service_specs = check_service(service, known, str(key))
        if title.startswith("-"):
            raise web.HTTPBadRequest(text=f"The URL of {key} cannot start with '-'")
        series[int(key)] = {
            "service": service,
            "title": title,
            "options": check_options(show.get("options") or {}, dl_specs, str(key)),
            "service_options": check_options(show.get("service_options") or {}, service_specs, str(key)),
        }
        series[int(key)].update(check_numbering(show, str(key)))
        # The automatic sync only takes episodes aired from here on: see sonarr_sync.wanted_automatically.
        series[int(key)]["since"] = str(show.get("since") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
        try:  # the day the service publishes, from the day it airs: 1 after, -N days before (up to two weeks)
            day = int(show.get("release_day") or 0)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text=f"The release day of {key} must be a whole number") from None
        if not -sonarr_sync.EARLY_DAYS_MAX <= day <= 1:
            raise web.HTTPBadRequest(text=f"The release day of {key} must be from {sonarr_sync.EARLY_DAYS_MAX} days before airing to the day after")
        if release_time := str(show.get("release_time") or "").strip():
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", release_time):
                raise web.HTTPBadRequest(text=f"The release time of {key} must look like 23:30")
            series[int(key)]["release_time"] = release_time
        if day and (release_time or day < 0):  # the day after means nothing without a time; days before do
            series[int(key)]["release_day"] = day
        if show.get("broadcast"):
            series[int(key)]["broadcast"] = check_broadcast(show["broadcast"], str(key))
        if accept := check_audio_accept(show.get("audio_accept"), str(key)):
            series[int(key)]["audio_accept"] = accept
        if subs := check_audio_accept(show.get("subs_accept"), str(key), "subtitles"):
            series[int(key)]["subs_accept"] = subs
        if prefer := check_prefer(show.get("audio_prefer"), str(key)):
            series[int(key)]["audio_prefer"] = prefer
        profiles = [p for p in re.split(r"[,\s]+", str(show.get("fallback_profiles") or "")) if p]
        if bad := [p for p in profiles if not cookies.NAME.fullmatch(p)]:
            raise web.HTTPBadRequest(text=f"The fallback profiles of {key}: {bad[0]!r} is not a profile name")
        if profiles:
            series[int(key)]["fallback_profiles"] = ", ".join(dict.fromkeys(profiles))
        if (alt := show.get("fallback") or {}) and (alt.get("service") or alt.get("title")):
            series[int(key)]["fallback"] = check_fallback(alt, known, dl_specs, str(key))
        if show.get("notify") in ("failures", "none"):  # absent: every notification, as the settings say
            series[int(key)]["notify"] = show["notify"]
        if isinstance(show.get("spoiler_free"), bool):  # absent: the settings say
            series[int(key)]["spoiler_free"] = show["spoiler_free"]
        if isinstance(show.get("download_only"), bool):  # absent: the settings say
            series[int(key)]["download_only"] = show["download_only"]
        if ladder := check_ladder_name(show.get("ladder"), ladders, str(key)):
            series[int(key)]["ladder"] = ladder
        if show.get("skip_upgrades") is True:  # left out of Activity, Upgrades
            series[int(key)]["skip_upgrades"] = True
        if per := check_series_sonarrs(show.get("sonarrs"), body, dl_specs, str(key)):
            series[int(key)]["sonarrs"] = per
        if show.get("main_off") is True:  # not downloaded for the main Sonarr, only for the others
            series[int(key)]["main_off"] = True
    notifications = body.get("notifications") or {}
    targets = check_targets(notifications, [t["url"] for t in sonarr_sync.notification_targets(previous["notifications"])])
    countries = [c for c in body.get("tmdb_countries") or [] if re.fullmatch(r"[A-Z]{2}", c)]
    settings = check_settings(body.get("settings") or {}, previous["settings"])
    service_defaults = {}
    for service, levels in (body.get("service_defaults") or {}).items():
        service_specs = check_service(service, known, "the service defaults")
        opts = check_options(levels.get("options") or {}, dl_specs, service)
        own = check_options(levels.get("service_options") or {}, service_specs, service)
        picks = {}
        if backend := str(levels.get("backend") or ""):
            if backend not in {b["name"] for b in settings.get("backends") or []}:
                raise web.HTTPBadRequest(text=f"{service} downloads with the Unshackle server {backend}, which is not in Settings, Unshackle")
            picks["backend"] = backend
        if ladder := check_ladder_name(levels.get("ladder"), ladders, service):
            picks["ladder"] = ladder
        if opts or own or picks:  # a service with nothing set is not worth a line
            service_defaults[service] = {"options": opts, "service_options": own, **picks}
    settings["quality_ladder"] = check_ladder_name(settings.get("quality_ladder"), ladders, "the settings")
    for other in settings["sonarrs"]:  # "" stays as saved (not chosen yet: paused, and said so); "series": each series' own
        if other["quality_ladder"] not in ("", sonarr_sync.SAME_AS_SERIES):
            other["quality_ladder"] = check_ladder_name(other["quality_ladder"], ladders, f"Sonarr {other['name']}")
    config = {
        "settings": settings,
        "auth": previous["auth"],  # never from the browser
        "defaults": check_options(body.get("defaults") or {}, dl_specs, "defaults"),
        "service_defaults": service_defaults,
        "series": series,
        "notifications": {
            "targets": targets,
            **{level: bool(notifications.get(level, True)) for level in ("success", "warning", "error")},
            "quiet": check_quiet(notifications.get("quiet")),
            "events": {k: bool((notifications.get("events") or {}).get(k, True)) for k in EVENTS},
        },
        "tmdb_countries": countries or [sonarr_sync.load_settings(previous)["country"]],
        "hidden_series": sorted({int(i) for i in body.get("hidden_series") or []}),
        "quality_ladders": ladders,
    }
    write_config(config)
    return web.json_response(public_config(read_config()))


def check_fallback(alt: dict, known: dict | None, dl_specs: list[dict], key: str) -> dict:
    """A series' fallback service: its service and URL, its own options and numbering (only the ones set)."""
    service, title = str(alt.get("service") or ""), str(alt.get("title") or "").strip()
    if not service or not title:
        raise web.HTTPBadRequest(text=f"The fallback service of {key} needs both a service and a series URL")
    specs = check_service(service, known, f"the fallback of {key}")
    if title.startswith("-"):
        raise web.HTTPBadRequest(text=f"The fallback URL of {key} cannot start with '-'")
    numbering = {k: v for k, v in check_numbering(alt, f"the fallback of {key}").items() if k in sonarr_sync.FALLBACK_NUMBERING}
    own = check_options(alt.get("service_options") or {}, specs, f"the fallback of {key}")
    return {"service": service, "title": title, **({"service_options": own} if own else {}), **numbering}


def check_ladders(given) -> list[dict]:
    """Quality ladders: each a name and its steps, in order: a codec and a range (empty: any), a height from min to max (0: no limit)."""
    out = []
    for ladder in given if isinstance(given, list) else sonarr_sync.BUILTIN_LADDERS:
        name = str(ladder.get("name") or "").strip()
        if not name or len(name) > 40 or name == "off":
            raise web.HTTPBadRequest(text="Give each quality ladder a name")
        if name in {o["name"] for o in out}:
            raise web.HTTPBadRequest(text=f"Two quality ladders are named {name}")
        steps = []
        for step in ladder.get("steps") or []:
            codec, dynamic = str(step.get("codec") or ""), str(step.get("range") or "")
            if (codec and codec not in sonarr_sync.CODECS) or (dynamic and dynamic not in sonarr_sync.RANGES):
                raise web.HTTPBadRequest(text=f"The quality ladder {name}: unknown codec or range")
            try:
                low, high = int(step.get("min") or 0), int(step.get("max") or 0)
            except (TypeError, ValueError):
                raise web.HTTPBadRequest(text=f"The quality ladder {name}: heights are whole numbers, like 1080") from None
            if not (0 <= low <= 10000 and 0 <= high <= 10000) or (high and low > high):
                raise web.HTTPBadRequest(text=f"The quality ladder {name}: a step's lowest height is above its highest")
            steps.append({"codec": codec, "range": dynamic, "min": low, "max": high})
        if not steps:
            raise web.HTTPBadRequest(text=f"The quality ladder {name} has no step")
        orders = {}
        for kind in ("audio", "subtitles"):  # its language order: which of the episode's tracks to take, first found
            given = ladder.get(kind) or []
            if not isinstance(given, (str, list)):
                raise web.HTTPBadRequest(text=f"The quality ladder {name}: its {kind} order is a list of language codes")
            codes = [str(c).strip() for c in (given.split(",") if isinstance(given, str) else given) if str(c).strip()]
            if bad := [c for c in codes if not LANGUAGE_TAG.fullmatch(c) or c.lower() in ("orig", "all", "best")]:
                raise web.HTTPBadRequest(text=f"The quality ladder {name}: {bad[0]!r} is not a language code like fr or en-AU")
            if codes:
                orders[kind] = list(dict.fromkeys(codes))
        out.append({"name": name, "steps": steps, **orders})
    return out


def check_ladder_name(name, ladders: list[dict], where: str) -> str:
    """A ladder picked by name: one of them, off (none), or empty (the level before decides)."""
    name = str(name or "")
    if name and name != "off" and name not in {lad["name"] for lad in ladders}:
        raise web.HTTPBadRequest(text=f"{where} uses the quality ladder {name}, which is not in Settings, Quality")
    return name


def check_backends(given, saved) -> list[dict]:
    """Other unshackle serve: a name, its address, its API key (empty keeps the one saved for that name and
    address), and the downloads folder as it sees it."""
    saved = {b.get("name"): b for b in saved or []}
    out = []
    for b in given or []:
        name, url = str(b.get("name") or "").strip(), str(b.get("url") or "").strip().rstrip("/")
        if not re.fullmatch(r"[\w][\w .-]{0,39}", name):
            raise web.HTTPBadRequest(text="Give each other Unshackle server a name, with letters and digits")
        if name in {o["name"] for o in out}:
            raise web.HTTPBadRequest(text=f"Two Unshackle servers are named {name}")
        if not URL.fullmatch(url) or "@" in urlparse(url).netloc:
            raise web.HTTPBadRequest(text=f"The address of {name} must start with http:// or https://, without a user name")
        old = saved.get(name) or next((o for o in saved.values() if o.get("url") == url), {})  # renamed: its key stays
        key = str(b.get("api_key") or "").strip()
        if not key and old.get("api_key"):
            if old.get("url") != url:  # the saved key belongs to the old address: never handed to a new one unasked
                raise web.HTTPBadRequest(text=f"The new address of {name} needs its API key too")
            key = old["api_key"]
        folder = str(b.get("downloads") or "").strip().rstrip("/\\")
        if folder and (not (posixpath.isabs(folder) or ntpath.isabs(folder)) or ".." in re.split(r"[\\/]", folder)):
            raise web.HTTPBadRequest(text=f"{folder} must be a full path, from / (or a drive letter), without ..")
        out.append({"name": name, "url": url, "api_key": key, "downloads": folder})
    return out


def check_sonarrs(given, saved, main_url: str) -> list[dict]:
    """Other Sonarr instances: a name (it starts the folders of their downloads), the address, its API key (empty keeps
    the one saved for that name and address), the downloads folder as it sees it, and its own ladder and import."""
    saved = {i.get("name"): i for i in saved or []}
    out = []
    for i in given or []:
        name, url = str(i.get("name") or "").strip(), str(i.get("url") or "").strip().rstrip("/")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,23}", name):
            raise web.HTTPBadRequest(text="Name each other Sonarr with a letter, then letters, digits or dashes, like sonarr-4k")
        if name in {o["name"] for o in out}:
            raise web.HTTPBadRequest(text=f"Two Sonarr instances are named {name}")
        if not URL.fullmatch(url) or "@" in urlparse(url).netloc:
            raise web.HTTPBadRequest(text=f"The address of {name} must start with http:// or https://, without a user name")
        if url in (main_url.rstrip("/"), str(sonarr_sync.load_settings().get("sonarr_url") or "").rstrip("/")) \
                or url in {o["url"] for o in out}:
            raise web.HTTPBadRequest(text=f"{name} has the address of another Sonarr here: each instance has its own")
        old = saved.get(name) or next((o for o in saved.values() if o.get("url") == url), {})
        key = str(i.get("api_key") or "").strip()
        if not key:
            if old.get("api_key") and old.get("url") != url:
                raise web.HTTPBadRequest(text=f"The new address of {name} needs its API key too")
            key = old.get("api_key") or ""
        if not key:
            raise web.HTTPBadRequest(text=f"Enter the API key of {name}")
        folder = str(i.get("downloads") or "").strip().rstrip("/\\")
        if folder and (not (posixpath.isabs(folder) or ntpath.isabs(folder)) or ".." in re.split(r"[\\/]", folder)):
            raise web.HTTPBadRequest(text=f"{folder} must be a full path, from / (or a drive letter), without ..")
        if not str(i.get("quality_ladder") or "") and name not in saved:  # a new one: chosen, never a silent default
            raise web.HTTPBadRequest(text=f"Choose a quality ladder for {name}: a ladder, Off, or Same as each series")
        only = i.get("download_only")
        out.append({"name": name, "url": url, "api_key": key, "downloads": folder, "quality_ladder": str(i.get("quality_ladder") or ""),
                    "download_only": only if isinstance(only, bool) else None})
    return out


NUMBER_SETTINGS = {  # name: (smallest, largest)
    "sync_every_hours": (1, 24), "auto_days": (1, 90), "late_warning_hours": (0, 720),
    "burst_every_seconds": (10, 600), "burst_minutes": (1, 120), "history_keep": (10, 5000), "history_days": (1, 3650),
    "leftovers_days": (0, 365), "min_free_gb": (0, 100000), "upgrade_days": (1, 365),
    "backup_every_days": (0, 30), "backup_keep": (1, 365),
    "upgrade_recheck_days": (0, 3650), "upgrade_max_age_years": (0, 100),
}


URL = re.compile(r"https?://[^\s/]+(/[^\s]*)?")


def check_urls(urls, saved=()) -> list[str]:
    """Apprise URLs (discord://…, tgram://…, a Discord webhook as is): each new one must be one it knows.
    The message names its kind only: the rest is the address's secret."""
    urls = [str(u).strip() for u in urls or [] if str(u).strip()]
    for url in urls:
        if url not in saved and not apprise.Apprise().add(url):
            raise web.HTTPBadRequest(text=f"Not a notification URL Apprise knows: {url.partition('://')[0] + '://'}…")  # its scheme only
    return urls


def check_targets(notifications: dict, saved=()) -> list[dict]:
    """The addresses and the messages each takes; a list of URLs alone (an older page) takes them all."""
    given = notifications.get("targets")
    if given is None:
        given = [{"url": u} for u in notifications.get("urls") or []]
    urls = check_urls([t.get("url") for t in given], saved)
    levels = [[lv for lv in (t.get("levels") or sonarr_sync.ALL_LEVELS) if lv in sonarr_sync.ALL_LEVELS] for t in given if str(t.get("url") or "").strip()]
    return [{"url": u, "levels": lv} for u, lv in zip(urls, levels)]


def check_quiet(quiet) -> dict | None:
    if not quiet or not quiet.get("from") or not quiet.get("to"):
        return None
    for at in (quiet["from"], quiet["to"]):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(at)):
            raise web.HTTPBadRequest(text="Quiet hours look like 23:00")
    return {"from": quiet["from"], "to": quiet["to"]}


EVENTS = ("down", "cookies", "cdm", "precheck", "disk")  # what else is watched and told: Unshackle or Sonarr down, cookies, CDM devices, a check before a release


def check_broadcast(plan: dict, key: str) -> dict:
    """A series' own airing schedule, as sync.broadcast_dates reads it; a clear error for anything off."""
    bad = lambda what: web.HTTPBadRequest(text=f"The broadcast schedule of {key}: {what}")
    first = str(plan.get("from") or "S01E01").upper()
    if not re.fullmatch(r"S\d+E\d+", first):
        raise bad("its first episode must look like S01E01")
    try:
        start = date.fromisoformat(str(plan.get("start")))
    except ValueError:
        raise bad("pick the day of its first episode") from None
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(plan.get("time") or "")):
        raise bad("its time must look like 20:39")
    every = plan.get("every") if plan.get("every") in ("weekly", "daily") else "weekly"
    days = sorted({int(d) for d in plan.get("days") or [] if str(d).isdigit() and 0 <= int(d) <= 6})
    if every == "weekly" and not days:
        days = [start.weekday()]  # weekly on no day: the day it starts
    per_evening = int(plan.get("per_evening") or 1)
    evenings = {}
    for day, count in (plan.get("evenings") or {}).items():
        try:
            evenings[date.fromisoformat(str(day)).isoformat()] = int(count)
        except (TypeError, ValueError):
            raise bad(f"{day} is not a day with a number of episodes") from None
    if not 1 <= per_evening <= 9 or any(not 0 <= c <= 9 for c in evenings.values()):
        raise bad("an evening has 0 to 9 episodes")
    return {"from": first, "start": start.isoformat(), "time": str(plan["time"]), "every": every,
            **({"days": days} if every == "weekly" else {}), "per_evening": per_evening, **({"evenings": evenings} if evenings else {})}


def check_numbering(show: dict, key: str) -> dict:
    """A series' numbering and file names (season and episode maps, offset, parts, names), validated:
    only the ones set, as config.yaml keeps them."""
    numbering = {}
    try:
        season_map = {int(k): int(v) for k, v in (show.get("season_map") or {}).items()}
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=f"Season numbers of {key} must be whole numbers")
    if season_map:
        numbering["season_map"] = season_map
    if file_name := str(show.get("file_name") or "").strip():
        numbering["file_name"] = file_name
    episode_map = {}
    for sonarr_ep, service_ep in (show.get("episode_map") or {}).items():
        a = re.fullmatch(r"S(\d+)E(\d+)", str(sonarr_ep).strip().upper())
        b = re.fullmatch(r"S(\d+)E(\d+)(\.\d+)?", str(service_ep).strip().upper())
        if not a or not b:
            raise web.HTTPBadRequest(text=f"Episode table of {key}: write S34E06=S29E05 or S34E06=S29E05.2")
        # the sync looks episodes up as S01E06: S1E6 must find it
        episode_map[f"S{int(a[1]):02}E{int(a[2]):02}"] = f"S{int(b[1]):02}E{int(b[2]):02}{b[3] or ''}"
    if episode_map:
        numbering["episode_map"] = episode_map
    try:
        if offset := int(show.get("episode_offset") or 0):
            numbering["episode_offset"] = offset
        if offset := int(show.get("season_offset") or 0):
            numbering["season_offset"] = offset
            if (start := int(show.get("season_offset_from") or 1)) > 1:
                numbering["season_offset_from"] = start
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=f"The offsets of {key} must be whole numbers")
    if show.get("join_parts") is False:
        numbering["join_parts"] = False
    if parts := show.get("parts"):
        if str(parts) not in ("2", "3", "4"):
            raise web.HTTPBadRequest(text=f"The parts per episode of {key} must be 2, 3 or 4")
        numbering["parts"] = int(parts)
    if (name_mode := show.get("episode_name") or "joined") != "joined":
        if name_mode not in ("keep", "always"):
            raise web.HTTPBadRequest(text=f"Unknown episode name mode {name_mode!r}")
        numbering["episode_name"] = name_mode
    return numbering


LANGUAGE_TAG = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})*", re.IGNORECASE)


def check_audio_accept(value, where: str, what: str = "audio") -> str:
    """The audio (or subtitle, or upgrade) languages of a setting, as "fr, en": language tags only, never orig or all."""
    codes = [c for c in re.split(r"[,\s]+", str(value or "").strip()) if c]
    for code in codes:
        if not LANGUAGE_TAG.fullmatch(code) or code.lower() in ("orig", "all", "best"):
            if what == "subtitles":  # whole sentences, each its own: they are translated as they are
                raise web.HTTPBadRequest(text=f"Required subtitle languages of {where}: {code!r} is not a language code like fr or en-US")
            if what == "upgrade":
                raise web.HTTPBadRequest(text=f"The audio upgrade language of {where}: {code!r} is not a language code like fr or en-US")
            raise web.HTTPBadRequest(text=f"Accepted audio languages of {where}: {code!r} is not a language code like fr or en-US")
    return ", ".join(c.lower() for c in codes)


def check_prefer(value, where: str) -> str:
    """The one audio language an episode is got again in once it comes."""
    code = check_audio_accept(value, where, "upgrade")
    if "," in code:
        raise web.HTTPBadRequest(text=f"The audio upgrade language of {where} must be one language, like fr")
    return code


def check_proxy_auth(header, sources) -> tuple[str, str]:
    """A reverse proxy's login: the header that names the user, and the addresses the proxy connects from.
    Both or neither: a header believed from anywhere would let anyone in."""
    header, sources = str(header or "").strip(), str(sources or "").strip()
    if not header and not sources:
        return "", ""
    if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", header):
        raise web.HTTPBadRequest(text="The reverse proxy's header is a name like Remote-User")
    networks = []
    for source in [s for s in re.split(r"[,\s]+", sources) if s]:
        try:
            networks.append(str(ipaddress.ip_network(source, strict=False)))
        except ValueError:
            raise web.HTTPBadRequest(text=f"{source!r} is not an address or a range like 172.18.0.0/16") from None
    if not networks:
        raise web.HTTPBadRequest(text="Enter the addresses the reverse proxy connects from. Without them, anyone could send the header")
    return header, ", ".join(networks)


OFFSITE_SETTINGS = ("backup_remote", "backup_remote_url", "backup_remote_user", "backup_remote_secret", "backup_remote_bucket",
                    "backup_remote_region", "backup_passphrase")


def check_offsite(body: dict, previous: dict) -> dict:
    """Where backups are sent: whole (a place, its URL, a passphrase), the saved secrets kept when left empty but
    never following a new URL."""
    settings = {key: str(body.get(key) or "").strip() or previous.get(key, "") for key in ("backup_remote_secret", "backup_passphrase")}
    kind = str(body.get("backup_remote") or "")
    if kind not in ("", "webdav", "s3"):
        raise web.HTTPBadRequest(text="Backups can be sent to WebDAV or S3 only")
    settings["backup_remote"] = kind
    for name in ("backup_remote_url", "backup_remote_user", "backup_remote_bucket", "backup_remote_region"):
        settings[name] = str(body.get(name) or "").strip()
    settings["backup_remote_url"] = settings["backup_remote_url"].rstrip("/")
    if not kind:
        return {**settings, "backup_remote_secret": "", "backup_passphrase": ""}  # off: nothing kept
    url, what = settings["backup_remote_url"], "WebDAV folder" if kind == "webdav" else "S3 endpoint"
    if not URL.fullmatch(url):
        raise web.HTTPBadRequest(text=f"The {what} URL must start with http:// or https://")
    if previous.get("backup_remote_secret") and url != previous.get("backup_remote_url") and not str(body.get("backup_remote_secret") or "").strip():
        raise web.HTTPBadRequest(text=f"A new {what} URL needs its password or secret key too")
    if kind == "s3" and not (settings["backup_remote_bucket"] and settings["backup_remote_user"] and settings["backup_remote_secret"]):
        raise web.HTTPBadRequest(text="Sending backups to S3 needs a bucket, an access key and a secret key")
    if not settings["backup_passphrase"]:
        raise web.HTTPBadRequest(text="Set a passphrase: a backup never leaves the server unencrypted")
    if len(settings["backup_passphrase"]) < 12:
        raise web.HTTPBadRequest(text="The passphrase must be at least 12 characters: a few words are easy to remember")
    if kind == "webdav" and settings["backup_remote_secret"] and not url.startswith("https://"):
        raise web.HTTPBadRequest(text="Use an https:// address: over http://, the WebDAV password would cross the network unencrypted")
    return settings


def check_settings(body: dict, previous: dict) -> dict:
    """Validated settings; an API key left empty keeps the one already saved."""
    settings = {}
    for name, what in (("sonarr_url", "Sonarr"), ("sonarr_public_url", "Sonarr in the browser"), ("unshackle_url", "unshackle serve")):
        url = str(body.get(name) or "").strip().rstrip("/")
        if url and not URL.fullmatch(url):
            raise web.HTTPBadRequest(text=f"The {what} URL must start with http:// or https://")
        if "@" in urlparse(url).netloc:  # it would show in every error message: a proxy's own login stays in the proxy
            raise web.HTTPBadRequest(text=f"The {what} URL cannot hold a user name or password")
        settings[name] = url
    for key in SECRET_SETTINGS:
        settings[key] = str(body.get(key) or "").strip() or previous.get(key, "")
    effective = sonarr_sync.load_settings({"settings": previous})  # with the keys and URLs from the environment
    for name, key, what in (("sonarr_url", "sonarr_api_key", "Sonarr"), ("unshackle_url", "unshackle_api_key", "unshackle serve")):
        url = settings[name]
        if url and effective.get(key) and url != str(effective.get(name) or "").rstrip("/") and not str(body.get(key) or "").strip():
            # The saved key belongs to the old address (or to none, once cleared): never hand it to a new one unasked.
            raise web.HTTPBadRequest(text=f"A new {what} URL needs its API key too")
    for key in OFFSITE_SETTINGS:  # where backups go is changed with the password only (backup_offsite)
        settings[key] = previous.get(key, sonarr_sync.SETTINGS_DEFAULTS[key])
    mode = str(body.get("unshackle_mode") or "")
    if mode not in ("", "local", "remote"):
        raise web.HTTPBadRequest(text="unshackle runs either here (local) or elsewhere (remote)")
    settings["unshackle_mode"] = mode
    for name in ("unshackle_command", "downloads", "unshackle_downloads", "sonarr_downloads", "cookies_dir", "unshackle_config_dir"):
        settings[name] = str(body.get(name) or "").strip().rstrip("/\\")
        folder = settings[name]
        if name != "unshackle_command" and folder and (not (posixpath.isabs(folder) or ntpath.isabs(folder))
                                                       or ".." in re.split(r"[\\/]", folder)):
            raise web.HTTPBadRequest(text=f"{folder} must be a full path, from / (or a drive letter), without ..")
    country = str(body.get("country") or "US").upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise web.HTTPBadRequest(text="The country must be a two-letter code, like US")
    settings["country"] = country
    zone = str(body.get("timezone") or "UTC")
    try:
        sonarr_sync.ZoneInfo(zone)
    except (KeyError, ValueError):
        raise web.HTTPBadRequest(text=f"Unknown time zone {zone!r}")
    settings["timezone"] = zone
    language = str(body.get("language") or previous.get("language") or "en")
    settings["language"] = language if language in i18n.languages() else "en"
    for key, (low, high) in NUMBER_SETTINGS.items():
        try:
            value = float(body.get(key, sonarr_sync.SETTINGS_DEFAULTS[key]))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text=f"{key} must be a number")
        if not low <= value <= high:
            raise web.HTTPBadRequest(text=f"{key} must be between {low} and {high}")
        settings[key] = int(value) if value.is_integer() else value
    settings["audio_accept"] = check_audio_accept(body.get("audio_accept"), "the settings")
    settings["subs_accept"] = check_audio_accept(body.get("subs_accept"), "the settings", "subtitles")
    settings["audio_prefer"] = check_prefer(body.get("audio_prefer"), "the settings")
    settings["proxy_auth_header"], settings["proxy_auth_from"] = check_proxy_auth(body.get("proxy_auth_header"), body.get("proxy_auth_from"))
    settings["debug"] = body.get("debug") is True
    mode = str(body.get("upgrade_mode") or "redownload")
    if mode not in ("redownload", "add_track"):
        raise web.HTTPBadRequest(text="Choose whether to download the episode again or to add the audio track to the existing file")
    settings["upgrade_mode"] = mode
    for name in ("library_sonarr_root", "library_local_root"):
        settings[name] = folder = str(body.get(name) or "").strip().rstrip("/\\")
        if folder and (not (posixpath.isabs(folder) or ntpath.isabs(folder)) or ".." in re.split(r"[\\/]", folder)):
            raise web.HTTPBadRequest(text=f"{folder} must be a full path, from / (or a drive letter), without ..")
    if bool(settings["library_sonarr_root"]) != bool(settings["library_local_root"]):
        raise web.HTTPBadRequest(text="Set the library folder both as Sonarr sees it and as Unshacklarr sees it, or neither")
    settings["release_learn"] = body.get("release_learn") is True
    for name in ("download_from", "download_to"):
        at = str(body.get(name) or "").strip()
        if at and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at):
            raise web.HTTPBadRequest(text="Download window times look like 01:00")
        settings[name] = at
    if bool(settings["download_from"]) != bool(settings["download_to"]):
        raise web.HTTPBadRequest(text="Set both a start and an end time for the download window, or neither")
    settings["download_window_bursts"] = body.get("download_window_bursts") is True
    settings["spoiler_free"] = body.get("spoiler_free") is True
    settings["download_only"] = body.get("download_only") is True
    settings["quality_ladder"] = str(body.get("quality_ladder") or "")  # checked against the ladders by save_config
    settings["upgrade_other_groups"] = body.get("upgrade_other_groups") is True
    settings["backends"] = check_backends(body.get("backends"), previous.get("backends"))
    settings["sonarrs"] = check_sonarrs(body.get("sonarrs"), previous.get("sonarrs"), settings["sonarr_url"])
    return settings


async def hide_series(request):
    """Hide a series from the Schedule, or show it again: saved at once, no Save needed."""
    body = await json_object(request)
    try:
        tvdb_id = int(body.get("tvdbId"))
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="tvdbId must be a number")
    config = read_config()
    hidden = set(config["hidden_series"])
    hidden.add(tvdb_id) if body.get("hidden") else hidden.discard(tvdb_id)
    config["hidden_series"] = sorted(hidden)
    write_config(config)
    return web.json_response(config["hidden_series"])


async def test_notification(request):
    """A test message to each address given, one by one: what each one said."""
    saved = [t["url"] for t in sonarr_sync.notification_targets(read_config()["notifications"])]
    urls = check_urls([real_url(str(u), saved) for u in (await json_object(request)).get("urls") or []])
    if not urls:
        raise web.HTTPBadRequest(text="Add a notification address first")
    lang = sonarr_sync.SETTINGS.get("language", "en")
    title, message = (i18n.tr_lines(t, lang) for t in ("Test: notifications work", "A downloaded episode will look like this."))

    def one(url):
        return {"app": sonarr_sync.app_of(url), "error": sonarr_sync.send_to(url, "success", title, message,
                                                                             {"service": "RMCP", "size": "1.4 GB", "took": "38 s"})}
    results = await asyncio.gather(*(asyncio.to_thread(one, u) for u in urls))
    await asyncio.to_thread(sonarr_sync.note_sent, "success", title, [{"app": r["app"], "ok": not r["error"], **({"error": r["error"]} if r["error"] else {})} for r in results])
    return web.json_response({"results": [{"ok": not r["error"], "error": r["error"]} for r in results]})


async def sent_notifications(_):
    return web.json_response({"sent": read_json(sonarr_sync.SENT_FILE, []), "held": len(read_json(sonarr_sync.HELD_FILE, []))})


TMDB_CACHE = sonarr_sync.DATA / "tmdb_cache.json"
TMDB_CACHE_DAYS = 7


async def suggest(request):
    """TMDB's where-to-watch links, cached a week per series: opening a series costs no request."""
    tmdb_id = request.match_info["tmdb_id"]
    countries = read_config()["tmdb_countries"]
    cache = read_json(TMDB_CACHE, {})
    hit = cache.get(tmdb_id)
    fresh = hit and hit["countries"] == countries and (
        datetime.now(timezone.utc) - datetime.fromisoformat(hit["checked"]) < timedelta(days=TMDB_CACHE_DAYS)
    )
    if not fresh or request.query.get("refresh"):
        try:
            domains = await asyncio.to_thread(service_domains)
            found = await asyncio.gather(*(asyncio.to_thread(tmdb_links, int(tmdb_id), c, domains) for c in countries))
        except UnshackleError as e:
            if not hit:
                raise web.HTTPBadGateway(text=str(e))
            found = None
        except requests.RequestException as e:
            if not hit:
                raise web.HTTPBadGateway(text=tmdb_error(e))
            found = None  # keep the old links rather than show nothing
        if found is not None:
            unique: dict[tuple, dict] = {}
            for link in (link for links in found for link in links):
                seen = unique.setdefault((link["service"], link["url"]), {**link, "country": ""})
                if link["country"] not in seen["country"].split(", "):
                    seen["country"] = ", ".join(filter(None, [seen["country"], link["country"]]))
            with_series = {l["service"] for l in unique.values() if not l.get("episode")}
            links = [l for l in unique.values() if not (l.get("episode") and l["service"] in with_series)]
            links = await asyncio.to_thread(searched_in_place, links, int(tmdb_id))
            hit = {"countries": countries, "checked": datetime.now(timezone.utc).isoformat(), "links": links}
            cache[tmdb_id] = hit
            write_atomic(TMDB_CACHE, json.dumps(cache))
    return web.json_response({"links": await asyncio.to_thread(installed_links, hit["links"]), "checked": hit["checked"]})


def title_key(title: str) -> str:
    """A title compared without its year, case, accents or punctuation: "WAR (2026)" and "War" match."""
    title = re.sub(r"\s*\(\d{4}\)\s*$", "", unicodedata.normalize("NFKD", title or ""))
    return re.sub(r"[^a-z0-9]+", "", title.casefold())


def searched_in_place(links: list[dict], tmdb_id: int) -> list[dict]:
    """A service TMDB links only by an episode (HBO Max's /video/watch/<id>) is searched with
    Sonarr's title; the one result with that title takes the episode link's place. No match, several, or a service
    that can't be searched: the episode link stays."""
    alone = {l["service"] for l in links if l.get("episode")} - {l["service"] for l in links if not l.get("episode")}
    if not alone:
        return links
    try:
        series = next((s for s in sonarr_series() if s.get("tmdbId") == tmdb_id), None)
    except requests.RequestException:
        return links
    if not series:
        return links
    out = list(links)
    for service in sorted(alone):
        if url := search_series_url(service, series["title"]):
            episode = next(l for l in out if l["service"] == service and l.get("episode"))
            out = [l for l in out if not (l["service"] == service and l.get("episode"))]
            out.append({**{k: v for k, v in episode.items() if k != "episode"}, "url": url, "found_by": "search"})
    return out


def search_series_url(service: str, title: str) -> str | None:
    """The series' address on its service when its search has exactly one result of that title."""
    show = {"service": service}
    dl, _ = sonarr_sync.stacked(show, read_config())
    params = {k: v for k, v in options.to_params(dl, options.dl_specs()).items() if k in ("profile", "proxy", "no_proxy")}
    query = re.sub(r"\s*\(\d{4}\)\s*$", "", title)
    try:
        found = sonarr_sync.backend_for(service).call("POST", "/api/search", json={**params, "service": service, "query": query})
    except UnshackleError as e:
        print(f"Search {service} for {query!r}: {no_credentials(e)}", flush=True)
        return None
    same = [r for r in found.get("results") or [] if title_key(str(r.get("title") or "")) == title_key(title) and (r.get("url") or r.get("id"))]
    return str(same[0].get("url") or same[0].get("id")) if len(same) == 1 else None


def installed_links(links: list[dict]) -> list[dict]:
    """Suggestions under the code of the service installed now for their site (one kept under MAX shows as HMAX
    where the service is HMAX), none for a site no service takes any more. Unshackle out of reach: as kept."""
    try:
        domains = service_domains()
    except UnshackleError:
        return links
    out = []
    for link in links:
        site = link.get("site") or (urlparse(link["url"]).hostname if link["url"].startswith("http") else None)
        service = service_for(f"https://{site}/", domains) if site else link["service"]
        if service:
            out.append({**link, "service": service})
    return out


def next_sync(now: datetime) -> datetime:
    """The next automatic sync: on the hour, every sync_every_hours of the local day."""
    every = int(sonarr_sync.SETTINGS["sync_every_hours"])
    local = now.astimezone(sonarr_sync.LOCAL)
    hour = (local.hour // every + 1) * every
    return local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour - local.hour)


async def run_automatic_syncs():
    """The automatic sync, on the web container's own clock: no crontab to keep in step."""
    while True:
        due = next_sync(datetime.now(timezone.utc))
        while (left := (due - datetime.now(timezone.utc)).total_seconds()) > 0:
            await asyncio.sleep(min(left, 60))  # a new interval in Settings takes effect within a minute
            if next_sync(datetime.now(timezone.utc)) < due:
                due = next_sync(datetime.now(timezone.utc))
        try:
            if sonarr_sync.SONARR and read_config()["auth"]:  # nothing to sync before the setup is done
                run_sync()
                gone = await asyncio.to_thread(sonarr_sync.clean_leftovers)
                if gone:
                    print(f"Deleted from the downloads folder, waiting too long: {', '.join(gone)}", flush=True)
        except Exception as e:  # one bad round must not end the automatic syncs
            print(f"Automatic sync: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(1)


def first_try(show: dict, ep: dict, aired: datetime | None, now: datetime) -> str | None:
    slot = sonarr_sync.release_slot(show, ep)
    if slot:
        return slot.isoformat() if slot > now else None
    return next_sync(aired).isoformat() if aired and aired > now else None


burst_runs: dict[int, threading.Thread] = {}


def calendar_episodes(config: dict, start: datetime, end: datetime) -> list[dict]:
    """Sonarr's calendar from start to end, its series' own broadcast schedules put in; reaching as many days
    further as a series publishes before airing."""
    ahead = max([-sonarr_sync.release_day(s) for s in config["series"].values()] + [0])  # a release days before airing
    end = end + timedelta(days=ahead)
    r = requests.get(f"{sonarr_sync.SONARR}/api/v3/calendar", headers=sonarr_sync.HEADERS,
                     params={"start": start.isoformat(), "end": end.isoformat(), "includeSeries": "true"}, timeout=30)
    r.raise_for_status()
    plans = sonarr_sync.broadcast_plans(config)
    dated = sonarr_sync.broadcast_dated(plans)  # dated by its own schedule: Sonarr's date set aside
    return [ep for ep in r.json() if ep["id"] not in dated] + sonarr_sync.broadcast_episodes(config, start, end, plans)


def due_releases(now: datetime) -> list[int]:
    """Episodes whose release time has just come and which Sonarr still lacks."""
    config = read_config()
    timed = [s for s in config["series"].values() if s.get("release_time")]
    if not timed:
        return []  # no series has a release time: no need to ask Sonarr every 30 s
    due = []
    for ep in calendar_episodes(config, now - timedelta(days=2), now + timedelta(days=1)):
        show = config["series"].get(ep["series"]["tvdbId"])
        slot = show and show.get("service") and show.get("release_time") and sonarr_sync.release_slot(show, ep)  # no time: no burst
        burst_for = timedelta(minutes=float(sonarr_sync.SETTINGS["burst_minutes"]))
        if slot and slot <= now < slot + burst_for and not ep["hasFile"] and not sonarr_sync.episode_folder(ep).exists():
            due.append(ep["id"])
    return due


def burst_failed(episode_id: int) -> bool:
    """A try of this release burst failed for good (refused, an error: not "not out yet"). The burst is there to
    catch the episode the minute it is out; trying again every 30 s would only fail the same way. The sync
    tries it again later."""
    if not sonarr_sync.RUNS_DIR.exists():
        return False
    since = (datetime.now(timezone.utc) - timedelta(minutes=float(sonarr_sync.SETTINGS["burst_minutes"]))).isoformat()
    card = next((c for c in cards_on_disk() if c.get("episodeId") == episode_id), None)  # its latest try
    return bool(card and card.get("kind") == "burst" and card.get("outcome") == "failed" and (card.get("ended") or "") >= since)


PRECHECK_AHEAD = timedelta(hours=3)
PRECHECKED_FILE = sonarr_sync.DATA / "prechecked.json"  # episode id -> when it was checked ahead of its release


def precheck(now: datetime) -> list[str]:
    """A few hours before an episode's first try (its release time, else the sync after it airs), its series is
    listed on its service, nothing downloaded: a login refused, no CDM, a series the service no longer lists
    are told then, while there is time to fix them, once per episode. The titles of what was told."""
    config = read_config()
    if not (config["notifications"].get("events") or {}).get("precheck", True):
        return []
    series = config["series"]
    if not any(s.get("service") and s.get("title") for s in series.values()):
        return []
    done = {k: v for k, v in read_json(PRECHECKED_FILE, {}).items() if now - datetime.fromisoformat(v) < timedelta(days=14)}
    coming: dict[int, list[dict]] = {}
    for ep in calendar_episodes(config, now - timedelta(days=1), now + PRECHECK_AHEAD):
        show = series.get(ep["series"]["tvdbId"])
        if not show or not show.get("service") or not show.get("title") or ep.get("hasFile") or str(ep["id"]) in done:
            continue
        at = first_try(show, ep, sonarr_sync.parse_time(ep["airDateUtc"]) if ep.get("airDateUtc") else None, now)
        if at and datetime.fromisoformat(at) <= now + PRECHECK_AHEAD:
            coming.setdefault(ep["series"]["tvdbId"], []).append(ep | {"first_try": at})
    told = []
    for tvdb, eps in coming.items():
        show = series[tvdb]
        problem = sonarr_sync.no_cdm(show["service"])
        if not problem:
            try:
                if not any(t.get("type") == "episode" for t in list_titles(show)):
                    problem = f"{show['service']} lists no episodes for this series: is its URL still right?"
            except UnshackleError as e:
                problem = str(e)
        for ep in eps:
            done[str(ep["id"])] = now.isoformat()
        if problem:
            ep = min(eps, key=lambda e: e["first_try"])
            label = f"{ep['series']['title']} S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}"
            when = datetime.fromisoformat(ep["first_try"]).astimezone(sonarr_sync.LOCAL).strftime("%H:%M")
            title = f"Not ready for its release: {label}"
            sonarr_sync.notify(config["notifications"], "error", title, f"{problem[:1200]}\nIt is first tried at {when}: fix it before then.")
            told.append(title)
    write_atomic(PRECHECKED_FILE, json.dumps(done))
    return told


async def watch_prechecks():
    """Every 10 minutes, the series whose new episode comes out within 3 hours: see precheck."""
    while True:
        try:
            if read_config()["auth"] and sonarr_sync.SONARR:
                await asyncio.to_thread(precheck, datetime.now(timezone.utc))
        except Exception as e:  # Sonarr down for a moment: next round
            print(f"Check before release: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(600)


UPGRADE_EVERY = timedelta(hours=20)  # about once a day, whatever the hour the loop started


def check_upgrades(now: datetime) -> list[str]:
    """Each episode waiting for its preferred audio, once a day: listed on its service, nothing downloaded. There now,
    it is got again in place of Sonarr's file; past upgrade_days, the file it has stays, told once. The ones got again."""
    config = read_config()
    days = timedelta(days=float(sonarr_sync.SETTINGS.get("upgrade_days") or 30))
    started, dropped, checked = [], [], []
    for key, w in read_json(sonarr_sync.UPGRADES_FILE, {}).items():
        show = config["series"].get(int(w["tvdb"])) or {}
        if show.get("service") != w["service"] or not show.get("title"):
            dropped.append(key)  # another service, or none now: what was waited for there means nothing
            continue
        if now - datetime.fromisoformat(w["since"]) > days:
            dropped.append(key)
            sonarr_sync.notify(config["notifications"], "warning", f"Kept without {w['want']} audio: {w['label']}",
                               f"Still no {w['want']} audio on {w['service']} after {days.days} days: the episode keeps the audio it has.")
            continue
        if w.get("checked") and now - datetime.fromisoformat(w["checked"]) < UPGRADE_EVERY:
            continue
        checked.append(key)
        try:
            request = sonarr_sync.download_request(show, config, w["serviceEpisode"], sonarr_sync.DOWNLOADS / f"upgrade-{key}")
        except ValueError:  # no CDM for it now: tried again tomorrow
            continue
        tracks = sonarr_sync.tracks_on_service(show, config, request)
        if tracks and sonarr_sync.speaks(tracks["audio"], [w["want"]]):
            # its audio only, added to the file, when chosen and possible; else the whole episode again
            if sonarr_sync.SETTINGS.get("upgrade_mode") == "add_track":
                threading.Thread(target=add_or_download, args=(int(key), w["want"]), daemon=True).start()
            else:
                run_sync([int(key)], replace=True, kind="upgrade")  # its import takes it off the list
            started.append(w["label"])
    with sonarr_sync.upgrades_lock:  # read again: a download may have added or taken one meanwhile
        watched = read_json(sonarr_sync.UPGRADES_FILE, {})
        for key in dropped:
            watched.pop(key, None)
        for key in checked:
            if key in watched:
                watched[key]["checked"] = now.isoformat()
        write_atomic(sonarr_sync.UPGRADES_FILE, json.dumps(watched))
    return started


def add_or_download(episode_id: int, want: str) -> None:
    try:
        if sonarr_sync.add_track(episode_id, want):
            return
    except Exception as e:  # anything unforeseen: the whole episode again, as before
        print(f"Add the audio: {type(e).__name__}: {e}", flush=True)
    run_sync([episode_id], replace=True, kind="upgrade")


async def watch_upgrades():
    """Every hour, the episodes waiting for their preferred audio: see check_upgrades."""
    while True:
        try:
            if read_config()["auth"] and sonarr_sync.SONARR and sonarr_sync.UPGRADES_FILE.exists():
                await asyncio.to_thread(check_upgrades, datetime.now(timezone.utc))
        except Exception as e:
            print(f"Language upgrades: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(3600)


async def watch_releases():
    """At a series' release time, try its new episode every 30 s for 10 min."""
    while True:
        try:
            if not sonarr_sync.SONARR:
                await asyncio.sleep(30)
                continue  # set up first
            for episode_id in await asyncio.to_thread(due_releases, datetime.now(timezone.utc)):
                run = burst_runs.get(episode_id)
                if (run is None or not run.is_alive()) and not await asyncio.to_thread(burst_failed, episode_id):  # the previous try is over
                    if sonarr_sync.SETTINGS.get("download_window_bursts") and not sonarr_sync.in_download_window():
                        continue  # chosen in Automation: the release-time tries keep to the window too
                    burst_runs[episode_id] = run_sync([episode_id], kind="burst")
        except Exception as e:  # Sonarr down for a moment: try again next round
            print(f"Release watch: {e}", flush=True)
        await asyncio.sleep(float(sonarr_sync.SETTINGS["burst_every_seconds"]))


# ---- Health: Unshackle and Sonarr, checked in the background for the header's lights ----

HEALTH_EVERY = 120  # seconds
health: dict = {"unshackle": {"ok": None}, "sonarr": {"ok": None}}
failures_in_a_row = {"unshackle": 0, "sonarr": 0}


def unshackle_health(full: bool = False) -> dict:
    """Whether Unshackle answers, and what the page shows about it. `full` adds the tools
    and the queue (a tool check runs a dozen programs: only when Settings is open)."""
    local = UNSHACKLE.mode == "local"
    state = {"mode": UNSHACKLE.mode, "ok": False,
             "address": sonarr_sync.SETTINGS.get("unshackle_command") or "unshackle" if local else sonarr_sync.SETTINGS.get("unshackle_url")}
    try:
        h = UNSHACKLE.health()
        update = h.get("update_check") or {}
        state.update(ok=True, version=h.get("version"), latest=update.get("latest_version") if update.get("update_available") else None,
                     services=len(UNSHACKLE.services()))
        if local and UNSHACKLE.local.started:
            state["since"] = UNSHACKLE.local.started.isoformat()
        if full:
            jobs = UNSHACKLE.jobs()
            state["queue"] = {s: sum(j["status"] == s for j in jobs) for s in ("downloading", "queued")}
            state["jobs"] = [{"id": j["job_id"], "service": j.get("service"), "title": j.get("current_title") or j.get("title") or j.get("title_id"),
                              "status": j["status"], "progress": round(float(j.get("progress") or 0))}
                             for j in jobs if j["status"] in ("queued", "downloading")]
            state["tools"] = UNSHACKLE.tools() + [
                {"name": f"{name} (for Unshacklarr)", "installed": bool(shutil.which(name)), "required": True}
                for name in ("mkvmerge", "mkvpropedit")]
    except UnshackleError as e:
        state["error"] = str(e)
    return state


def sonarr_health() -> dict:
    if not sonarr_sync.SONARR:
        return {"ok": False, "error": "Sonarr is not set up"}
    try:
        return {"ok": True, "version": sonarr_get_version()}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": f"Sonarr is unreachable: {no_credentials(e)}"}


def sonarr_get_version() -> str:
    return sonarr_status(sonarr_sync.SONARR, sonarr_sync.HEADERS["X-Api-Key"])


def server_health(backend) -> dict:
    """Whether another unshackle serve answers: its version and how many services it has, or why not."""
    try:
        return {"ok": True, "version": backend.health().get("version"), "services": len(backend.services())}
    except UnshackleError as e:
        return {"ok": False, "error": str(e)}


def other_sonarr_health(inst: dict) -> dict:
    """Whether another Sonarr answers: its version, or why not."""
    try:
        return {"ok": True, "version": sonarr_status(inst["url"], inst["api_key"])}
    except (requests.RequestException, ValueError) as e:
        return {"ok": False, "error": f"Sonarr {inst['name']} is unreachable: {no_credentials(e)}"}


def check_health() -> None:
    """Refresh every state (Unshackle, Sonarr, the other Unshackle servers); notify once when one goes down (two
    checks in a row, not a blip) and once when it comes back."""
    settings = read_config()["notifications"]
    servers = [(f"server:{name}", server_health(b), f"Unshackle {name}") for name, b in list(sonarr_sync.BACKENDS.items())]
    health["servers"] = {name.split(":", 1)[1]: {**state, "checked": datetime.now(timezone.utc).isoformat()} for name, state, _ in servers}
    sonarrs = [(f"sonarr:{name}", other_sonarr_health(i), f"Sonarr {name}") for name, i in list(sonarr_sync.SONARRS.items())]
    health["sonarrs"] = {name.split(":", 1)[1]: {**state, "checked": datetime.now(timezone.utc).isoformat()} for name, state, _ in sonarrs}
    servers += sonarrs
    for name, state, label in [("unshackle", unshackle_health(), "Unshackle"), ("sonarr", sonarr_health(), "Sonarr"), *servers]:
        was_down = failures_in_a_row.get(name, 0) >= 2
        failures_in_a_row[name] = 0 if state["ok"] else failures_in_a_row.get(name, 0) + 1
        state["checked"] = datetime.now(timezone.utc).isoformat()
        if ":" not in name:
            health[name] = state
        if not (settings.get("events") or {}).get("down", True):
            continue
        if failures_in_a_row[name] == 2:
            sonarr_sync.notify(settings, "error", f"{label} is unreachable", f"{state.get('error')}\nDownloads wait until it is back.")
        elif was_down and state["ok"]:
            sonarr_sync.notify(settings, "success", f"{label} is back", "Downloads carry on.")


async def health_check(request):
    """For Docker's HEALTHCHECK, Uptime Kuma and the like, no login: Unshacklarr answers, and whether Sonarr and
    Unshackle did at the last check (every few minutes, not asked again here). 200 while Unshacklarr runs; with
    ?strict=1, 503 when Sonarr or Unshackle is down. Only yes or no: no address, no error text."""
    parts = {name: health[name].get("ok") for name in ("sonarr", "unshackle")}
    down = any(ok is False for ok in parts.values())
    return web.json_response({"ok": not down, "unshacklarr": True, **parts},
                             status=503 if down and request.query.get("strict") else 200)


async def watch_health():
    while True:
        try:
            if read_config()["auth"]:  # nothing to watch before the setup is done
                await asyncio.to_thread(check_health)
        except Exception as e:
            print(f"Health check: {e}", flush=True)
        await asyncio.sleep(HEALTH_EVERY)


# ---- Updates: a newer release of Unshacklarr, said in the page's header ----

# The site's own page, not GitHub's API: the API allows 60 anonymous calls an hour per address, which a
# home connection shares with every other program; this page redirects to the latest release's tag.
RELEASES = "https://github.com/OwnzZzZ/Unshacklarr/releases/latest"
UPDATE_FILE = sonarr_sync.DATA / "update.json"  # the last answer: {"checked", "version", "url"}
# one small request to github.com an hour: a release shows within the hour (GitHub may still give the one
# before it for a few minutes after a release, which the next check puts right)
UPDATE_EVERY, UPDATE_RETRY = 3600, 15 * 60
UPDATE_HEADERS = {"User-Agent": f"Unshacklarr/{__version__}"}


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3])


def check_update() -> None:
    """The latest release published on GitHub (no draft, no pre-release), kept on disk."""
    r = requests.get(RELEASES, timeout=15, headers=UPDATE_HEADERS, allow_redirects=False)
    tag = re.search(r"/releases/tag/v?([^/?#]+)$", r.headers.get("Location", "")) if r.status_code in (301, 302) else None
    if not tag:  # no release yet (it redirects to the list), or an answer that is not GitHub's
        raise ValueError(f"no release in GitHub's answer ({r.status_code})")
    write_atomic(UPDATE_FILE, json.dumps({"checked": datetime.now(timezone.utc).isoformat(), "version": tag[1], "url": r.headers["Location"]}))


def update_info() -> dict | None:
    """A release newer than this one, for the header; None when this is the latest or none is known."""
    last = read_json(UPDATE_FILE, {})
    newer = last.get("version") and version_key(last["version"]) > version_key(__version__)
    return {"version": last["version"], "url": last.get("url")} if newer else None


async def watch_updates():
    """At start, then every hour; 15 min later when GitHub did not answer (its last answer stands)."""
    while True:
        try:
            await asyncio.to_thread(check_update)
            wait = UPDATE_EVERY
        except (requests.RequestException, ValueError) as e:
            print(f"Update check: {no_credentials(e)}", flush=True)
            wait = UPDATE_RETRY
        await asyncio.sleep(wait)


ALERTS_FILE = sonarr_sync.DATA / "alerts.json"  # what was told already: {key: state}, told again only when it changes
CDM_TEST_EVERY = timedelta(days=1)


def check_alerts(now: datetime | None = None) -> None:
    """Cookies about to expire or expired, and CDM devices in use that fail their test (tested once a day):
    each told once, told again only if it changes."""
    now = now or datetime.now(timezone.utc)
    settings = read_config()["notifications"]
    events = settings.get("events") or {}
    told, problems = read_json(ALERTS_FILE, {}), {}
    unknown = []  # what could not be checked now: what was told of it stands, never told twice
    if events.get("disk", True) and (full := sonarr_sync.free_space_problem()):
        problems["disk"] = ("error", "The downloads folder is almost full", f"{full}. Make room, or lower the room kept free in Settings, Unshackle, Folders.")
    if events.get("cookies", True):
        try:
            state = cookie_state()
        except UnshackleError:
            state, unknown = None, unknown + ["cookies:"]
        for service in (state or {}).get("used", []):
            for f in state["files"].get(service, []):
                name = f"{service} {f['profile']}".strip()
                left = (f["expires"] - now.timestamp()) / 86400 if f.get("expires") else None
                if f.get("expired"):
                    problems[f"cookies:{name}"] = ("error", f"{name} cookies have expired", "Its downloads will fail: replace them in Settings, Cookies.")
                elif left is not None and 0 <= left < 7:
                    problems[f"cookies:{name}"] = ("warning", f"{name} cookies expire in {max(1, round(left))} days",
                                                    "Replace them before they expire, in Settings, Cookies.")
    if events.get("cdm", True):
        try:
            state = cdm_state()
        except UnshackleError:
            state, unknown = None, unknown + ["cdm:"]
        for d in (state or {}).get("devices", []):
            if not d.get("used_by") or d.get("folder") or (d.get("info") or {}).get("unreadable"):
                continue
            test = d.get("test")
            if not test or now - datetime.fromisoformat(test["at"]) > CDM_TEST_EVERY:
                try:
                    result = UNSHACKLE.call("POST", "/api/cdm/devices/test", json={"kind": d["kind"].lower(), "name": d["name"]})
                except UnshackleError:
                    unknown.append(f"cdm:{d['kind']}:{d['name']}")
                    continue  # Unshackle down: told apart
                test = {k: result.get(k) for k in ("ok", "revoked", "error", "keys")} | {"at": now.isoformat()}
                note_cdm_test(f"{d['kind']}:{d['name']}", test)
            if not test.get("ok"):
                word = "was revoked" if test.get("revoked") else "failed its test"
                problems[f"cdm:{d['kind']}:{d['name']}"] = ("error", f"CDM {d['name']} {word}",
                                                             f"{test.get('error') or 'No license'}. Used by {', '.join(d['used_by'])}: see Settings, CDM.")
    for key, (level, title, message) in problems.items():
        if told.get(key) != title:
            sonarr_sync.notify(settings, level, title, message)
    kept = {key: title for key, title in told.items() if key.startswith(tuple(unknown))} if unknown else {}
    write_atomic(ALERTS_FILE, json.dumps(kept | {key: p[1] for key, p in problems.items()}))


async def watch_alerts():
    """Every minute, what the quiet hours kept; every hour, cookies and CDM devices."""
    last = None
    while True:
        try:
            if read_config()["auth"] and sonarr_sync.SONARR:
                await asyncio.to_thread(sonarr_sync.send_held, read_config()["notifications"])
                if not last or datetime.now(timezone.utc) - last > timedelta(hours=1):
                    last = datetime.now(timezone.utc)
                    await asyncio.to_thread(check_alerts)
        except Exception as e:
            print(f"Alerts: {e}", flush=True)
        await asyncio.sleep(60)


async def status(request):
    """The header's lights; ?full=1 checks Unshackle afresh, tools and queue included."""
    if request.query.get("full"):
        health["unshackle"] = {**await asyncio.to_thread(unshackle_health, True), "checked": datetime.now(timezone.utc).isoformat()}
    return web.json_response(health)


async def restart_unshackle(_):
    try:
        await asyncio.to_thread(UNSHACKLE.restart)
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))
    health["unshackle"] = {**await asyncio.to_thread(unshackle_health, True), "checked": datetime.now(timezone.utc).isoformat()}
    return web.json_response(health["unshackle"])


# ---- Cookies: Unshackle's cookie files, seen and replaced from the page ----

def cookies_folder() -> Path:
    if sonarr_sync.SETTINGS.get("cookies_dir"):
        return Path(sonarr_sync.SETTINGS["cookies_dir"])
    if sonarr_sync.SETTINGS.get("unshackle_config_dir"):  # next to unshackle.yaml, as its directories: section names it
        return cdm.directory(Path(sonarr_sync.SETTINGS["unshackle_config_dir"]) / "unshackle.yaml", "cookies", "Cookies")
    if UNSHACKLE.mode == "local":
        return UNSHACKLE.local.cookies_dir(str(sonarr_sync.SETTINGS.get("unshackle_command") or ""))
    raise UnshackleError("Unshacklarr cannot see Unshackle's Cookies folder: mount it and set its path "
                         "in Settings, Unshackle (COOKIES_DIR with Docker)")


def cookie_state() -> dict:
    folder = cookies_folder()
    files = cookies.list_all(folder)
    used = sorted({c["service"] for c in read_config()["series"].values() if c.get("service")})
    try:
        wants = {s["tag"] for s in UNSHACKLE.services() if "cookies" in (s.get("auth_methods") or [])}
    except UnshackleError:
        wants = set()
    return {"folder": str(folder), "files": files, "used": used, "wants": sorted(wants)}


async def list_cookies(_):
    try:
        return web.json_response(await asyncio.to_thread(cookie_state))
    except (UnshackleError, cdm.CdmError) as e:
        return web.json_response({"folder": None, "error": str(e), "files": {}, "used": [], "wants": []})


async def save_cookies(request):
    body = await json_object(request)
    try:
        folder = await asyncio.to_thread(cookies_folder)
        saved = await asyncio.to_thread(cookies.save, folder, str(body.get("service") or ""), str(body.get("profile") or ""), str(body.get("text") or ""))
    except (cookies.CookieError, cdm.CdmError, UnshackleError) as e:
        raise web.HTTPBadRequest(text=str(e))
    except OSError as e:
        raise web.HTTPBadGateway(text=f"Could not write the cookie file: {e}")
    return web.json_response(saved)


async def delete_cookies(request):
    body = await json_object(request)
    try:
        folder = await asyncio.to_thread(cookies_folder)
        await asyncio.to_thread(cookies.delete, folder, str(body.get("service") or ""), str(body.get("profile") or ""))
    except (cookies.CookieError, cdm.CdmError, UnshackleError) as e:
        raise web.HTTPBadRequest(text=str(e))
    return web.json_response({"deleted": True})


# ---- CDMs: Unshackle's Widevine and PlayReady devices, and which one each service uses ----

def cdm_paths() -> tuple[Path, Path, Path]:
    """unshackle.yaml, and the WVDs and PRDs folders, as Unshacklarr sees them."""
    if sonarr_sync.SETTINGS.get("unshackle_config_dir"):
        conf = Path(sonarr_sync.SETTINGS["unshackle_config_dir"]) / "unshackle.yaml"
        return (conf, *cdm.folders(conf))
    if UNSHACKLE.mode == "local":
        found = UNSHACKLE.local.paths(str(sonarr_sync.SETTINGS.get("unshackle_command") or ""))
        if str(found["config"]) not in ("", "."):
            return found["config"], found["wvds"], found["prds"]
        raise UnshackleError("This Unshackle has no unshackle.yaml yet")
    raise UnshackleError("Unshacklarr cannot see Unshackle's config: mount its folder (unshackle.yaml, WVDs, PRDs) "
                         "and set its path in Settings, Unshackle (UNSHACKLE_CONFIG_DIR with Docker)")


CDM_TESTS = sonarr_sync.DATA / "cdm_tests.json"  # each device's last test: "Widevine:name": {at, ok, revoked, error, keys}
cdm_tests_lock = threading.Lock()


def cdm_tests() -> dict:
    return read_json(CDM_TESTS, {})


def note_cdm_test(key: str, result: dict | None) -> None:
    with cdm_tests_lock:
        tests = cdm_tests()
        if result is None:
            tests.pop(key, None)
        else:
            tests[key] = result
        write_atomic(CDM_TESTS, json.dumps(tests))


def cdm_details() -> tuple[dict, str | None]:
    """What each device's certificate says, asked to Unshackle (it has the DRM libraries)."""
    try:
        found = UNSHACKLE.call("GET", "/api/cdm/devices")["devices"]
    except UnshackleError as e:
        if "404" in str(e) or "not found" in str(e).lower():
            return {}, "This Unshackle cannot describe or test its devices: it needs the /api/cdm routes (a newer Unshackle)"
        return {}, str(e)
    return {(d["kind"], d["name"]): d for d in found}, None


def cdm_state() -> dict:
    conf, wvds, prds = cdm_paths()
    try:
        services = sorted(s["tag"] for s in UNSHACKLE.services())
    except UnshackleError:
        services = []
    state = cdm.state(conf, wvds, prds)
    details, details_error = cdm_details()
    tests = cdm_tests()
    for d in state["devices"]:
        d["info"] = details.get((d["kind"].lower(), d["name"]))
        d["test"] = tests.get(f"{d['kind']}:{d['name']}")
    return {"folder": str(conf.parent), **state, "services": services, "details_error": details_error}


async def cdm_test(request):
    """A test license from the DRM's own test server, remembered for the page."""
    body = await json_object(request)
    kind, name = str(body.get("kind") or ""), str(body.get("name") or "")
    if kind not in ("Widevine", "PlayReady"):
        raise web.HTTPBadRequest(text="The kind is Widevine or PlayReady")
    try:
        result = await asyncio.to_thread(UNSHACKLE.call, "POST", "/api/cdm/devices/test", json={"kind": kind.lower(), "name": name})
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))
    result = {k: result.get(k) for k in ("ok", "revoked", "error", "keys")} | {"at": datetime.now(timezone.utc).isoformat()}
    await asyncio.to_thread(note_cdm_test, f"{kind}:{name}", result)
    return web.json_response(result)


async def cdm_reprovision(request):
    body = await json_object(request)
    name = str(body.get("name") or "")
    try:
        done = await asyncio.to_thread(UNSHACKLE.call, "POST", "/api/cdm/devices/reprovision", json={"name": name})
    except UnshackleError as e:
        raise web.HTTPBadRequest(text=str(e))
    await asyncio.to_thread(note_cdm_test, f"PlayReady:{name}", None)  # a new certificate: its old test says nothing now
    return web.json_response({"backup": done.get("backup"), "state": await asyncio.to_thread(cdm_state)})


async def list_cdm(_):
    try:
        return web.json_response(await asyncio.to_thread(cdm_state))
    except UnshackleError as e:
        return web.json_response({"folder": None, "error": str(e)})


async def cdm_action(request):
    body = await json_object(request)
    action = request.match_info["action"]
    try:
        conf, wvds, prds = await asyncio.to_thread(cdm_paths)
        if action == "add":
            try:
                content = base64.b64decode(str(body.get("data") or ""), validate=True)
            except ValueError:
                raise cdm.CdmError("The file did not arrive whole: pick it again") from None
            await asyncio.to_thread(cdm.add, wvds, prds, str(body.get("filename") or ""), content)
        elif action == "delete":
            await asyncio.to_thread(cdm.delete, conf, wvds, prds, str(body.get("name") or ""), str(body.get("kind") or ""))
            await asyncio.to_thread(note_cdm_test, f"{body.get('kind')}:{body.get('name')}", None)
        elif action == "remote-save":
            await asyncio.to_thread(cdm.remote_save, conf, body.get("form") or {}, str(body["was"]) if body.get("was") else None)
        elif action == "remote-delete":
            await asyncio.to_thread(cdm.remote_delete, conf, str(body.get("name") or ""))
        else:
            device = body.get("device")
            await asyncio.to_thread(cdm.choose, conf, wvds, prds, str(body.get("service") or ""), str(device) if device else None)
    except (cdm.CdmError, UnshackleError) as e:
        raise web.HTTPBadRequest(text=str(e))
    except OSError as e:
        raise web.HTTPBadGateway(text=f"Could not write Unshackle's files: {e}")
    return web.json_response(await asyncio.to_thread(cdm_state))


# ---- Unshacklarr's own settings, backed up and restored from the page: the password again ----

BACKUP_KEYS = ("settings", "defaults", "service_defaults", "series", "notifications", "tmdb_countries", "hidden_series", "quality_ladders")
BEFORE_RESTORE = sonarr_sync.DATA / "config-before-restore.yaml"  # the settings a restore replaced, its owner only


def backup_problem(data) -> str | None:
    """Why this is no Unshacklarr backup, or None: its sections of the right kinds, each series a service and a URL."""
    if not isinstance(data, dict) or not set(data) & set(BACKUP_KEYS):
        return "Not an Unshacklarr backup: none of its sections (series, settings…) is in it"
    kinds = {"settings": dict, "defaults": dict, "service_defaults": dict, "series": dict, "notifications": dict,
             "tmdb_countries": list, "hidden_series": list, "quality_ladders": list}
    for key, kind in kinds.items():
        if data.get(key) is not None and not isinstance(data[key], kind):
            return f"Its {key} section is not in the format Unshacklarr writes"
    for key, show in (data.get("series") or {}).items():
        if not str(key).isdigit() or not isinstance(show, dict):
            return f"Its series entry {key!r} is not in the format Unshacklarr writes"
    return None


def backup_text(config: dict) -> str:
    """The settings as a backup holds them: every secret in them but the password and the session."""
    return yaml.safe_dump({k: config[k] for k in BACKUP_KEYS if k in config}, allow_unicode=True, sort_keys=False)


async def backup(request):
    """The settings as a file, every secret in them but the password and the session: asked for with the password."""
    await reauth(request, str((await json_object(request)).get("password") or ""))
    return web.json_response({"name": f"unshacklarr-{datetime.now(sonarr_sync.LOCAL):%Y-%m-%d}.yaml", "text": backup_text(read_config())})


def backup_data(text: str, passphrase: str = "") -> dict:
    """A backup's text, read and checked; an encrypted one opened with the passphrase given, else the saved one."""
    if offsite.is_encrypted(text):
        passphrase = passphrase or sonarr_sync.SETTINGS.get("backup_passphrase") or ""
        if not passphrase:
            raise web.HTTPBadRequest(text="This backup is encrypted: type its passphrase")
        try:
            text = offsite.decrypt(text, passphrase)
        except ValueError as e:
            raise web.HTTPBadRequest(text=f"{e}: type the passphrase it was sent with") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        raise web.HTTPBadRequest(text="Not an Unshacklarr backup: the file is not valid YAML") from None
    if problem := backup_problem(data):
        raise web.HTTPBadRequest(text=problem)
    return data


async def restore(request):
    """A backup in place of the settings, the password and the session kept; the settings before kept aside."""
    body = await json_object(request)
    await reauth(request, str(body.get("password") or ""))
    data = await asyncio.to_thread(backup_data, str(body.get("text") or ""), str(body.get("passphrase") or ""))
    current = read_config()
    write_atomic(BEFORE_RESTORE, backup_text(current), PRIVATE)
    write_config({**{k: data[k] for k in BACKUP_KEYS if data.get(k) is not None}, "auth": current["auth"]})
    return web.json_response({"series": len(data.get("series") or {})})


# Automatic backups (#15): the same file as Download a backup, written in the data folder every so many days, the
# newest kept. Listed in Account with a Download behind the password, and offered by the setup of a new install.
BACKUPS_DIR = sonarr_sync.DATA / "backups"
BACKUP_NAME = re.compile(r"unshacklarr-\d{4}-\d{2}-\d{2}-\d{4}\.yaml")


def saved_backups() -> list[Path]:
    """The automatic backups, newest first."""
    found = [p for p in BACKUPS_DIR.iterdir() if BACKUP_NAME.fullmatch(p.name)] if BACKUPS_DIR.is_dir() else []
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def make_backup(now: datetime | None = None) -> Path | None:
    """A backup once the newest is backup_every_days old (0: never), then only the newest backup_keep kept. No
    password: the server writes its own file, its owner only."""
    every = float(sonarr_sync.SETTINGS.get("backup_every_days") or 0)
    if not every or not read_config()["auth"].get("password"):  # nothing to keep before the setup
        return None
    now = now or datetime.now(sonarr_sync.LOCAL)
    saved = saved_backups()
    if saved and now.timestamp() - saved[0].stat().st_mtime < every * 86400 - 600:  # slack for the hourly check
        return None
    BACKUPS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = BACKUPS_DIR / f"unshacklarr-{now:%Y-%m-%d-%H%M}.yaml"
    write_atomic(path, backup_text(read_config()), PRIVATE)
    for old in saved_backups()[max(1, int(sonarr_sync.SETTINGS.get("backup_keep") or 14)):]:
        old.unlink(missing_ok=True)
    return path


def backups_info() -> dict:
    """What Account lists: names, times and sizes only (the files hold secrets: the password opens them)."""
    saved = [{"name": p.name, "at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(), "size": p.stat().st_size}
             for p in saved_backups()]
    return {"count": len(saved), "folder": str(BACKUPS_DIR), "latest": saved[0]["at"] if saved else None, "saved": saved,
            "offsite": read_json(OFFSITE_FILE, {})}


OFFSITE_FILE = sonarr_sync.DATA / "backup_offsite.json"  # the last backup sent away: {"at", "ok", "name" or "error"}


def send_offsite(name: str, text: str) -> dict:
    """A backup sent, encrypted, where Settings say; how it went kept for Account, and a failure notified."""
    settings = sonarr_sync.SETTINGS
    try:
        sent = {"ok": True, "name": offsite.send(settings, name, text)}
    except (requests.RequestException, ValueError, KeyError) as e:
        sent = {"ok": False, "error": no_credentials(e) if isinstance(e, requests.RequestException) else str(e)}
        sonarr_sync.notify(read_config()["notifications"], "error", "Remote backup failed", sent["error"])
    sent["at"] = datetime.now(timezone.utc).isoformat()
    write_atomic(OFFSITE_FILE, json.dumps(sent))
    return sent


async def backup_offsite(request):
    """Where backups are sent, saved with the password (they hold every key: a session alone must not send them to
    another place), then a backup sent there at once to test it."""
    body = await json_object(request)
    await reauth(request, str(body.get("password") or ""))
    config = read_config()
    config["settings"] = {**config["settings"], **check_offsite(body.get("settings") or {}, sonarr_sync.load_settings(config))}
    write_config(config)
    sent = {}
    if config["settings"]["backup_remote"]:
        name = f"unshacklarr-{datetime.now(sonarr_sync.LOCAL):%Y-%m-%d-%H%M}.yaml"
        sent = await asyncio.to_thread(send_offsite, name, backup_text(config))
    return web.json_response({"sent": sent, "settings": public_config(read_config())["settings"]})


def saved_backup(name: str) -> Path:
    """One of the automatic backups, by its name only: never a path."""
    path = BACKUPS_DIR / name
    if not BACKUP_NAME.fullmatch(name) or not path.is_file():
        raise web.HTTPNotFound(text=f"No backup named {name}")
    return path


async def backup_download(request):
    """A saved backup's file, as Download a backup gives it: asked for with the password."""
    body = await json_object(request)
    await reauth(request, str(body.get("password") or ""))
    path = saved_backup(str(body.get("name") or ""))
    return web.json_response({"name": path.name, "text": path.read_text(encoding="utf8")})


async def watch_backups():
    """Every hour: a backup when one is due."""
    while True:
        try:
            if path := await asyncio.to_thread(make_backup):
                print(f"Backup: {path.name}", flush=True)
                if sonarr_sync.SETTINGS.get("backup_remote"):
                    await asyncio.to_thread(send_offsite, path.name, path.read_text(encoding="utf8"))
        except OSError as e:
            print(f"Backup: {no_credentials(e)}", flush=True)
        await asyncio.sleep(3600)


# ---- unshackle.yaml, edited from the page: the password again, a check, the versions before ----

CONFIG_HISTORY = sonarr_sync.DATA / "unshackle-yaml-history"  # the file before each save, its owner only
CONFIG_KEEP = 10
VERSION_ID = re.compile(r"\d{8}-\d{6}-\d{6}")


async def reauth(request, password: str) -> None:
    """The password asked again before showing or changing a file full of secrets; login's limit applies."""
    if not await password_try(request, password):
        raise web.HTTPForbidden(text="Wrong password")  # not 401: the page reads that as a lost session


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()[:16]


def config_versions() -> list[dict]:
    return [{"id": p.stem, "at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(), "size": p.stat().st_size}
            for p in sorted(CONFIG_HISTORY.glob("*.yaml"), reverse=True)]


def yaml_problem(text: str) -> str | None:
    """Why this is no unshackle.yaml, or None."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f", line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        return f"Not valid YAML{where}: {getattr(e, 'problem', None) or e}"
    if data is not None and not isinstance(data, dict):
        return "unshackle.yaml must hold settings (key: value), not a list or a single value"
    return None


def save_unshackle_yaml(path: Path, old: str, new: str) -> dict:
    """Keep the file as it was, then write the new one (with the file's own permissions)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    write_atomic(CONFIG_HISTORY / f"{stamp}.yaml", old, PRIVATE)
    for extra in sorted(CONFIG_HISTORY.glob("*.yaml"), reverse=True)[CONFIG_KEEP:]:
        extra.unlink(missing_ok=True)
    write_atomic(path, new)
    before, after = yaml.safe_load(old) or {}, yaml.safe_load(new) or {}
    UNSHACKLE.configure(UNSHACKLE.settings)  # its services, servers and dl: section may have changed: asked afresh
    return {"saved": True, "base": fingerprint(new), "versions": config_versions(),
            "restart": before.get("serve") != after.get("serve")}


async def unshackle_yaml(request):
    action = request.match_info["action"]
    body = await json_object(request)
    await reauth(request, str(body.get("password") or ""))
    try:
        path = (await asyncio.to_thread(cdm_paths))[0]
    except UnshackleError as e:
        raise web.HTTPBadRequest(text=str(e))
    if not path.exists():
        raise web.HTTPNotFound(text=f"No unshackle.yaml in {path.parent}")
    try:
        cdm.regular(path)
    except cdm.CdmError as e:
        raise web.HTTPBadRequest(text=str(e))
    text = await asyncio.to_thread(path.read_text, encoding="utf8")
    if action == "open":
        return web.json_response({"text": text, "base": fingerprint(text), "path": str(path), "versions": config_versions()})
    if action == "version":
        version = str(body.get("id") or "")
        if not VERSION_ID.fullmatch(version) or not (CONFIG_HISTORY / f"{version}.yaml").exists():
            raise web.HTTPNotFound(text="No such version")
        return web.json_response({"text": (CONFIG_HISTORY / f"{version}.yaml").read_text(encoding="utf8")})
    new = str(body.get("text") or "")
    if len(new) > 1_000_000:
        raise web.HTTPBadRequest(text="That is too large for unshackle.yaml")
    if body.get("base") != fingerprint(text):
        raise web.HTTPConflict(text="unshackle.yaml changed since you opened it (Settings, CDM writes to it too): open it again")
    if problem := yaml_problem(new):
        raise web.HTTPBadRequest(text=problem)
    if new == text:
        return web.json_response({"saved": False, "base": fingerprint(text), "versions": config_versions(), "restart": False})
    return web.json_response(await asyncio.to_thread(save_unshackle_yaml, path, text, new))


MAINTENANCE = {"clear-cache", "clear-temp", "refresh-services"}  # serve's own /api/maintenance actions


async def unshackle_maintenance(request):
    action = request.match_info["action"]
    if action not in MAINTENANCE:
        raise web.HTTPNotFound(text="Unknown maintenance action")
    try:
        result = await asyncio.to_thread(UNSHACKLE.call, "POST", f"/api/maintenance/{action}")
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))
    if action == "refresh-services":
        UNSHACKLE.configure(sonarr_sync.SETTINGS)  # forget the services it listed before
    return web.json_response(result)


async def cancel_job(request):
    job_id = request.match_info["job_id"]
    if not re.fullmatch(r"[0-9a-f-]{8,64}", job_id):
        raise web.HTTPBadRequest(text="Unknown job")
    try:
        await asyncio.to_thread(UNSHACKLE.cancel, job_id)
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))
    return web.json_response({"cancelling": True})


async def unshackle_log(_):
    if UNSHACKLE.mode == "remote":
        raise web.HTTPBadRequest(text="A remote unshackle serve keeps its log where it runs")
    return web.json_response({"log": UNSHACKLE.local.tail(400, "\n")})


async def stop_run(request):
    run_id = request.match_info["run_id"]
    if not RUN_ID.fullmatch(run_id):
        raise web.HTTPBadRequest(text="Unknown download")
    try:
        card = json.loads((sonarr_sync.RUNS_DIR / f"{run_id}.json").read_text())
    except (OSError, ValueError):
        card = {}
    job_id = card.get("job_id")
    if not job_id or run_id not in sonarr_sync.EpisodeRun.active:
        raise web.HTTPConflict(text="This download is not running")
    try:
        await asyncio.to_thread(sonarr_sync.backend_named(card.get("backend")).cancel, job_id)
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))
    return web.json_response({"stopping": True})


async def stop_job(request):
    """Stop a job: the one downloading is cancelled and its queued episodes are, as each one's cross does;
    one being joined, named or imported finishes (half an import in Sonarr is worse than one more episode).
    Nothing marks the job itself: an episode can be queued again in it."""
    batch = request.match_info["batch"]
    if not BATCH_ID.fullmatch(batch):
        raise web.HTTPBadRequest(text="Unknown job")
    sonarr_sync.pause_job(batch, False)  # a paused job stops too: its loop wakes to skip what was cancelled
    for episode_id, card in list(sonarr_sync.waiting.items()):
        if card.get("batch") == batch and sonarr_sync.waiting.pop(episode_id, None):
            sonarr_sync.unqueued.add(episode_id)
            await asyncio.to_thread(sonarr_sync.cancel_queued, card)
    cancelled = 0
    for c in await asyncio.to_thread(run_cards):
        if c.get("batch") == batch and c["outcome"] == "running" and c.get("job_id") and c.get("step") in ("queued", "downloading"):
            try:
                await asyncio.to_thread(sonarr_sync.backend_named(c.get("backend")).cancel, c["job_id"])
            except UnshackleError as e:
                raise web.HTTPBadGateway(text=str(e))
            cancelled += 1
    return web.json_response({"cancelled": cancelled})


async def unqueue(request):
    """Take an episode out of its job's queue: it is skipped when its turn comes."""
    episode_id = int(request.match_info["episode_id"])
    card = sonarr_sync.waiting.pop(episode_id, None)
    if card is None:
        raise web.HTTPConflict(text="This episode has already started")
    sonarr_sync.unqueued.add(episode_id)
    await asyncio.to_thread(sonarr_sync.cancel_queued, card)
    return web.json_response({"unqueued": episode_id})


async def pause_job(request):
    """Pause a job (the download going on ends, the next one waits) or let it go on."""
    batch = request.match_info["batch"]
    if not BATCH_ID.fullmatch(batch):
        raise web.HTTPBadRequest(text="Unknown job")
    if batch not in sonarr_sync.running_jobs:
        raise web.HTTPConflict(text="This job is over")
    sonarr_sync.pause_job(batch, request.match_info["action"] == "pause")
    return web.json_response({"paused": batch in sonarr_sync.paused_jobs})


async def join_job(batch: str, episode_id: int) -> bool:
    """The episode at the end of its running job's queue (or in its place, if cancelled before its turn)."""
    if batch not in sonarr_sync.running_jobs:
        return False
    try:
        ep = (await asyncio.to_thread(sonarr_sync.chosen_episodes, [episode_id]))[0]
    except (requests.RequestException, IndexError):
        return False
    show = read_config()["series"].get(ep["series"]["tvdbId"]) or {}
    return bool(show.get("service")) and sonarr_sync.add_to_job(batch, ep, show)


async def requeue(request):
    """A cancelled episode queued again: back in its job's queue while the job takes episodes, else it starts
    on its own in that job."""
    episode_id = int(request.match_info["episode_id"])
    body = await json_object(request)
    batch, run_id = str(body.get("batch") or ""), str(body.get("run") or "")
    if not BATCH_ID.fullmatch(batch) or not RUN_ID.fullmatch(run_id):
        raise web.HTTPBadRequest(text="Unknown job")
    card = sonarr_sync.RUNS_DIR / f"{run_id}.json"
    try:
        cancelled = json.loads(card.read_text())
    except (OSError, ValueError):
        raise web.HTTPConflict(text="This episode is not cancelled any more")
    if cancelled.get("outcome") != "cancelled" or cancelled.get("episodeId") != episode_id or cancelled.get("batch") != batch:
        raise web.HTTPConflict(text="This episode is not cancelled any more")
    card.unlink(missing_ok=True)  # queued again: its "cancelled" card goes
    if not await join_job(batch, episode_id):  # its job took its last episode: it starts on its own, in that job
        run_sync([episode_id], kind="manual", batch=batch)
    return web.json_response({"queued": episode_id})


def forget_runs(keep) -> int:
    """Delete finished attempts from the history (card and log), all but those `keep` says; never a running one."""
    gone = 0
    for card in sonarr_sync.RUNS_DIR.glob("*.json"):
        try:
            c = json.loads(card.read_text())
        except (OSError, ValueError):
            continue
        if card.stem in sonarr_sync.EpisodeRun.active or not c.get("ended") or keep(c):
            continue
        for path in sonarr_sync.RUNS_DIR.glob(f"{card.stem}.*"):
            path.unlink(missing_ok=True)
        gone += 1
    return gone


async def answer_run(request):
    """The person's answer to the question a running download asks (its input_prompt), passed to Unshackle."""
    run_id = request.match_info["run_id"]
    if not RUN_ID.fullmatch(run_id):
        raise web.HTTPBadRequest(text="Unknown download")
    answer = str((await json_object(request)).get("response") or "").strip()
    if not answer or len(answer) > 200:
        raise web.HTTPBadRequest(text="Type the answer first")
    try:
        card = json.loads((sonarr_sync.RUNS_DIR / f"{run_id}.json").read_text())
    except (OSError, ValueError):
        raise web.HTTPNotFound(text="No such download") from None
    if run_id not in sonarr_sync.EpisodeRun.active or not card.get("prompt") or not card.get("job_id"):
        raise web.HTTPConflict(text="This download asks nothing now")
    try:
        await asyncio.to_thread(sonarr_sync.backend_named(card.get("backend")).call, "POST", f"/api/download/jobs/{card['job_id']}/input",
                                json={"response": answer})
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))
    return web.json_response({"sent": True})


async def delete_run(request):
    run_id = request.match_info["run_id"]
    if not RUN_ID.fullmatch(run_id):
        raise web.HTTPBadRequest(text="Unknown download")
    if run_id in sonarr_sync.EpisodeRun.active:
        raise web.HTTPConflict(text="This download is running: stop it first")
    if not await asyncio.to_thread(forget_runs, lambda c: c["id"] != run_id):
        raise web.HTTPNotFound(text="No such download in the history")
    return web.json_response({"deleted": 1})


async def clear_runs(request):
    what = (await json_object(request)).get("what")
    if what not in ("failed", "all"):
        raise web.HTTPBadRequest(text="Clear either the failed downloads or all of them")
    keep = (lambda c: c.get("outcome") != "failed") if what == "failed" else (lambda c: False)
    return web.json_response({"deleted": await asyncio.to_thread(forget_runs, keep)})


def latest_runs() -> dict[tuple[int, str], str]:
    """Each episode's latest download attempt, (tvdbId, "S01E02") -> its id: the Schedule's way to it in Activity."""
    latest: dict[tuple[int, str], str] = {}
    for card in run_cards() if sonarr_sync.RUNS_DIR.exists() else []:  # newest first
        if not card.get("waiting"):  # a stub waiting its turn has no detail yet
            latest.setdefault((card["tvdbId"], card["sxxeyy"]), card["id"])
    return latest


def describe_episode(ep: dict, config: dict, now: datetime, all_series: bool = False, runs: dict | None = None) -> dict | None:
    """An episode as the Schedule shows it; for Unshackle's series, with the command it will run."""
    show = config["series"].get(ep["series"]["tvdbId"])
    base = {
        "episodeId": ep.get("id"),  # for the Schedule's Download now
        "tvdbId": ep["series"]["tvdbId"],
        "series": ep["series"]["title"],
        "sxxeyy": f"S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}",
        "title": ep.get("title"),
        "airDateUtc": ep.get("airDateUtc"),
        "hasFile": ep.get("hasFile"),
    }
    if not show or not show.get("service") or not show.get("title"):
        # Sonarr's calendar: shown too, so the series can be added to Unshackle from there.
        return {**base, "managed": False} if all_series else None
    service_sxxeyy = sonarr_sync.service_episode(show, ep["seasonNumber"], ep["episodeNumber"])
    folder = sonarr_sync.episode_folder(ep)
    aired = sonarr_sync.parse_time(ep["airDateUtc"]) if ep.get("airDateUtc") else None
    return {
        **base,
        "managed": True,
        "service": show["service"],
        "serviceEpisode": service_sxxeyy,
        "waitingImport": folder.exists(),
        "firstTry": first_try(show, ep, aired, now),
        "release": (slot := sonarr_sync.release_slot(show, ep)) and slot.isoformat(),  # past or not: the Schedule's order
        "command": sonarr_sync.unshackle_command(show, config, service_sxxeyy, folder) if service_sxxeyy else None,
        "run": (runs or {}).get((base["tvdbId"], base["sxxeyy"])),  # its latest attempt, to see in Activity
    }


async def schedule(_):
    """When the next sync runs, and the late episodes it will retry."""
    config = read_config()
    now = datetime.now(timezone.utc)
    def wanted():
        plans = sonarr_sync.broadcast_plans(config)
        dated = sonarr_sync.broadcast_dated(plans)  # dated by its own schedule: Sonarr's date set aside
        return [ep for ep in sonarr_sync.missing_episodes() if ep["id"] not in dated] + [
            ep for ep in sonarr_sync.broadcast_episodes(config, now - timedelta(days=sonarr_sync.AUTO_DAYS + 1), now, plans)
            if not ep.get("hasFile") and ep.get("monitored", True) and ep["series"].get("monitored", True)]
    try:
        missing = await asyncio.to_thread(wanted)
    except requests.RequestException as e:
        raise web.HTTPBadGateway(text=f"Sonarr is unreachable: {no_credentials(e)}")
    runs = await asyncio.to_thread(latest_runs)
    return web.json_response({
        "nextSync": next_sync(now).isoformat(),
        "missing": await asyncio.to_thread(lambda: [  # each looks for its folder on the downloads mount
            d for ep in missing
            if sonarr_sync.wanted_automatically(config["series"].get(ep["series"]["tvdbId"]) or {}, ep, now)
            and (d := describe_episode(ep, config, now, runs=runs))
        ]),
    })


async def calendar(request):
    """Sonarr's calendar from a day (local time) for some days: the Schedule pages through it."""
    try:
        first = datetime.fromisoformat(request.query.get("start", "")).date()
        days = min(max(int(request.query.get("days", "14")), 1), 62)
    except ValueError:
        raise web.HTTPBadRequest(text="start must be a YYYY-MM-DD date and days a number")
    start = datetime.combine(first, datetime.min.time(), sonarr_sync.LOCAL)
    config = read_config()
    now = datetime.now(timezone.utc)
    try:
        plans = await asyncio.to_thread(sonarr_sync.broadcast_plans, config)
        dated = sonarr_sync.broadcast_dated(plans)  # dated by its own schedule: Sonarr's date set aside
        episodes = [ep for ep in await asyncio.to_thread(
            sonarr_sync.sonarr_get, "calendar",
            start=start.isoformat(), end=(start + timedelta(days=days)).isoformat(), includeSeries="true",
        ) if ep["id"] not in dated]
        episodes += sonarr_sync.broadcast_episodes(config, start, start + timedelta(days=days), plans)
    except requests.RequestException as e:
        raise web.HTTPBadGateway(text=f"Sonarr is unreachable: {no_credentials(e)}")
    runs = await asyncio.to_thread(latest_runs)
    return web.json_response(await asyncio.to_thread(lambda: [  # each looks for its folder on the downloads mount
        d for ep in sorted(episodes, key=lambda e: e.get("airDateUtc") or "")
        if (d := describe_episode(ep, config, now, all_series=True, runs=runs))
    ]))


NETWORKS_CACHE = sonarr_sync.DATA / "tmdb_networks.json"
NETWORKS_DAYS = 7  # streaming catalogues move faster than networks


def logo_url(path: str | None) -> str | None:
    return f"https://image.tmdb.org/t/p/w92{path}" if path else None


def tmdb_network(tmdb_id: int) -> dict:
    """The series' original network on TMDB (FX, Apple TV+…) and, per country, the
    subscription services that carry it: one request with append_to_response."""
    r = requests.get(
        f"https://api.themoviedb.org/3/tv/{tmdb_id}",
        params={"api_key": tmdb_key(), "append_to_response": "watch/providers"},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    first = (data.get("networks") or [{}])[0]
    by_country = (data.get("watch/providers") or {}).get("results") or {}
    return {
        "name": first.get("name"),
        "logo": logo_url(first.get("logo_path")),
        "network_country": first.get("origin_country") or "",  # M6: FR, a channel watched there anyway
        "providers": {
            country: [{"name": p["provider_name"], "logo": logo_url(p.get("logo_path"))}
                      for p in (offers.get("flatrate") or []) + (offers.get("free") or []) + (offers.get("ads") or [])]
            for country, offers in by_country.items()
        },
    }


async def networks(request):
    """Network and local services of the series asked for, cached a week: TMDB is asked once per series."""
    try:
        ids = sorted({int(i) for i in request.query.get("tmdb", "").split(",") if i})[:200]
    except ValueError:
        raise web.HTTPBadRequest(text="tmdb must be comma-separated numbers")
    country = sonarr_sync.SETTINGS["country"]
    cache = read_json(NETWORKS_CACHE, {})
    fresh_after = (datetime.now(timezone.utc) - timedelta(days=NETWORKS_DAYS)).isoformat()
    todo = [i for i in ids if (cache.get(str(i)) or {}).get("checked", "") < fresh_after
            or "providers" not in cache[str(i)] or "network_country" not in cache[str(i)]]  # kept before it was asked
    if todo and tmdb_key():
        limit = asyncio.Semaphore(8)  # polite to TMDB when a new calendar brings many series

        async def one(tmdb_id):
            async with limit:
                try:
                    found = await asyncio.to_thread(tmdb_network, tmdb_id)
                except requests.RequestException:
                    return  # try again next time
                cache[str(tmdb_id)] = {**found, "checked": datetime.now(timezone.utc).isoformat()}

        await asyncio.gather(*(one(i) for i in todo))
        write_atomic(NETWORKS_CACHE, json.dumps(cache))
    return web.json_response({
        i: {"name": cache[str(i)].get("name"), "logo": cache[str(i)].get("logo"), "country": country,
            "network_country": cache[str(i)].get("network_country") or "",
            "providers": (cache[str(i)].get("providers") or {}).get(country, [])}
        for i in ids if str(i) in cache and "providers" in cache[str(i)]
    })


def running() -> bool:
    sync_threads[:] = [t for t in sync_threads if t.is_alive()]
    return bool(sync_threads)


ASKED_AT_ONCE = 20  # downloads asked by hand or by the API and not over yet: each is a thread


def room_for_one_more() -> None:
    """Refuse a download asked on top of too many still going: a thread each, a client in a loop would pile them up."""
    if running() and len(sync_threads) >= ASKED_AT_ONCE:
        raise web.HTTPTooManyRequests(text="Too many downloads requested at once: wait for some to finish")


def run_sync(episode_ids: list[int] | None = None, replace: bool = False, kind: str = "manual", numbering: dict | None = None,
             batch: str | None = None, sonarr: str = "") -> threading.Thread:
    """A sync in the background: every missing episode, or the ones given. Each episode has
    its own lock, so these runs never download the same one twice."""
    def work():
        global last_sync
        try:
            sonarr_sync.main(episode_ids, replace=replace, kind=kind, numbering=numbering, batch=batch, sonarr=sonarr)
        except Exception as e:  # notified already; the page shows the history
            print(f"Sync stopped: {type(e).__name__}: {e}", flush=True)
        finally:
            last_sync = datetime.now(timezone.utc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    sync_threads.append(thread)
    return thread


async def start_sync(_):
    if running():
        raise web.HTTPConflict(text="A sync is already running")
    run_sync()
    return web.json_response({"running": True})


CODECS = {"h264": "H.264", "x264": "H.264", "avc": "H.264", "h265": "H.265", "x265": "H.265", "hevc": "H.265",
          "av1": "AV1", "vp9": "VP9", "mpeg2": "MPEG-2", "eac3": "E-AC-3", "ac3": "AC-3", "dts": "DTS", "truehd": "TrueHD"}
def seconds(run_time: str) -> int:
    """Sonarr's run time, "2:03:22" or "43:10", in seconds."""
    total = 0
    for part in (run_time or "").split(":"):
        total = total * 60 + int(part) if part.isdigit() else total
    return total


STANDARD_HEIGHTS = (4320, 2160, 1440, 1080, 720, 576, 480, 360)


def resolution_height(width: int, height: int) -> int | None:
    """The height a picture counts as: a wide one (Apple TV+'s 1920x960, a film's 1920x800) is 1080p all the
    same, as its width says; within 5% of a standard height, that height (1916x1036: 1080)."""
    effective = max(height, round(width * 9 / 16))
    return next((s for s in STANDARD_HEIGHTS if effective >= s * 0.95), effective) or None


def file_summary(f: dict) -> dict:
    """What the page shows under an episode on disk: from Sonarr's media info. Its bitrates are
    often 0 (a tag the MKV lacks), so the file's average bitrate stands in for the video's."""
    mi = f.get("mediaInfo") or {}
    langs = lambda text: [sonarr_sync.LANGS.get(x.strip().lower(), x.strip().lower()) for x in (text or "").split("/") if x.strip() and x.strip().lower() != "und"]
    width, _, height = str(mi.get("resolution") or "").partition("x")
    length = seconds(mi.get("runTime"))
    return {
        "resolution": mi.get("resolution") or "",
        "height": resolution_height(int(width) if width.isdigit() else 0, int(height) if height.isdigit() else 0),  # its class: 1920x960 is 1080
        "video": CODECS.get(str(mi.get("videoCodec") or "").lower(), mi.get("videoCodec") or ""),
        "videoBitrate": mi.get("videoBitrate") or None,
        "averageBitrate": round(f["size"] * 8 / length) if f.get("size") and length else None,
        "audio": CODECS.get(str(mi.get("audioCodec") or "").lower(), mi.get("audioCodec") or ""),
        "channels": mi.get("audioChannels") or None,
        "audioLanguages": list(dict.fromkeys(langs(mi.get("audioLanguages")))),
        "subtitles": list(dict.fromkeys(langs(mi.get("subtitles")))),
        "size": f.get("size"),
        "runTime": mi.get("runTime") or "",
        "quality": ((f.get("quality") or {}).get("quality") or {}).get("name", ""),
        "releaseGroup": f.get("releaseGroup") or "",
        "track": ladder_track(mi),  # as a quality ladder reads a track: where the file stands on it
    }


LADDER_CODECS = {"H.264": "AVC", "H.265": "HEVC", "AV1": "AV1", "VP9": "VP9", "VP8": "VP8", "VC-1": "VC1"}


def layers(dynamic: str) -> list[str]:
    """every range a file carries: "DV HDR10PLUS" is a DV layer on an HDR10+ base, so both."""
    d = dynamic.upper()
    out = ["DV"] if "DV" in d else []
    out += ["HDR10P"] if "PLUS" in d else ["HDR10"] if "HDR10" in d or "PQ" in d else []
    out += ["HLG"] if "HLG" in d else []
    return out or ["SDR"]


def ladder_track(mi: dict) -> dict:
    """A file's video, from Sonarr's media info, as a quality ladder reads a track: its height (16:9), codec and range."""
    width, _, height = str(mi.get("resolution") or "").partition("x")
    dynamic = str(mi.get("videoDynamicRangeType") or "").upper()  # "", HDR10, HDR10PLUS, DV, DV HDR10, HLG, PQ…
    return {"height": int(height) if height.isdigit() else 0, "width": int(width) if width.isdigit() else 0,
            "codec": LADDER_CODECS.get(CODECS.get(str(mi.get("videoCodec") or "").lower(), ""), ""),
            "range": "DV" if "DV" in dynamic else "HDR10P" if "PLUS" in dynamic else "HLG" if "HLG" in dynamic
            else "HDR10" if "HDR" in dynamic or "PQ" in dynamic else "SDR",
            "layers": layers(dynamic)}  # a hybrid file sits on the earliest step any of its layers fits


def instances_of_series() -> dict[int, list[dict]]:
    """Each series set up here, in the other Sonarr instances: {tvdb: [{"name", "id" (its series id there), "ladder",
    "download_only"}]}. An instance the health check finds down is skipped: the page opens without waiting for it."""
    managed = {int(k) for k, v in (read_config()["series"] or {}).items() if v.get("service")}
    out: dict[int, list[dict]] = {}
    for inst in list(sonarr_sync.SONARRS.values()):
        if health.get("sonarrs", {}).get(inst["name"], {}).get("ok") is False:
            continue
        try:
            found = on_sonarr(inst, sonarr_sync.sonarr_series, managed)
        except requests.RequestException:
            continue
        for tvdb in managed & set(found):
            stats = found[tvdb].get("statistics") or {}  # as the main Sonarr's "missing" on the Series page
            out.setdefault(tvdb, []).append({"name": inst["name"], "id": found[tvdb]["id"],
                                             "ladder": inst["quality_ladder"], "download_only": inst["download_only"],
                                             "missing": max(0, stats.get("episodeCount", 0) - stats.get("episodeFileCount", 0))})
    return out


def sonarrs_of_series() -> dict[int, list[dict]]:
    """Every series of the other Sonarr instances, set up here or not: {tvdb: [{"name", "url", "slug", "missing"}]}, so a series'
    page says where it is before it is set up. An instance the health check finds down is skipped."""
    out: dict[int, list[dict]] = {}
    for inst in list(sonarr_sync.SONARRS.values()):
        if health.get("sonarrs", {}).get(inst["name"], {}).get("ok") is False:
            continue
        try:
            found = on_sonarr(inst, sonarr_sync.sonarr_series, set())
        except requests.RequestException:
            continue
        for tvdb, serie in found.items():
            stats = serie.get("statistics") or {}
            out.setdefault(tvdb, []).append({"name": inst["name"], "url": inst["url"], "slug": serie.get("titleSlug") or "",
                                             "missing": max(0, stats.get("episodeCount", 0) - stats.get("episodeFileCount", 0))})
    return out


def sonarr_named(name: str) -> dict | None:
    """The other Sonarr a request names (?sonarr= or "sonarr"); None: the main one."""
    if not name:
        return None
    if name not in sonarr_sync.SONARRS:
        raise web.HTTPBadRequest(text=f"No Sonarr named {name} in Settings, Sonarr")
    return sonarr_sync.SONARRS[name]


def on_sonarr(inst: dict | None, fn, *args, **kwargs):
    """fn in a worker thread, talking to that Sonarr: the instance is the thread's own."""
    with sonarr_sync.on_instance(inst):
        return fn(*args, **kwargs)


async def episodes(request):
    series_id = request.match_info["series_id"]  # that Sonarr's own series id (?sonarr=)
    inst = sonarr_named(request.query.get("sonarr", ""))
    try:
        eps, files = await asyncio.gather(
            asyncio.to_thread(on_sonarr, inst, sonarr_sync.sonarr_get, "episode", seriesId=series_id),
            asyncio.to_thread(on_sonarr, inst, sonarr_sync.sonarr_get, "episodefile", seriesId=series_id),
        )
    except requests.RequestException as e:
        raise web.HTTPBadGateway(text=f"Sonarr is unreachable: {no_credentials(e)}")
    by_id = {f["id"]: file_summary(f) for f in files}
    return web.json_response([
        {**{k: e.get(k) for k in ("id", "seasonNumber", "episodeNumber", "title", "airDateUtc", "hasFile", "monitored")},
         "file": by_id.get(e.get("episodeFileId")) if e.get("hasFile") else None}
        for e in sorted(eps, key=lambda e: (e["seasonNumber"], e["episodeNumber"]))
    ])


# ---- Test a series: what the service lists, set against Sonarr, before a release night ----

def suggest_season_map(sonarr_eps: list[dict], service_eps: list[dict]) -> dict[int, int]:
    """Sonarr season -> the service's, where they differ. By air dates when the service gives
    them (the season sharing the most dates, within 2 days); else the latest seasons in order."""
    from datetime import date
    def days(eps, key):
        by = {}
        for e in eps:
            try:
                by.setdefault(e["season"], set()).add(date.fromisoformat(str(e[key])[:10]).toordinal())
            except (KeyError, TypeError, ValueError):
                continue
        return by
    ours, theirs = days(sonarr_eps, "air_date"), days(service_eps, "air_date")
    service_seasons = sorted({e["season"] for e in service_eps if e.get("season") is not None})
    found: dict[int, int] = {}
    if theirs:
        for season, dates in ours.items():
            scores = {t: sum(any(abs(d - x) <= 2 for x in xs) for d in dates) for t, xs in theirs.items()}
            best = max(scores, key=scores.get, default=None)
            if best is not None and scores[best] >= min(2, len(dates)) and best != season:
                found[season] = best
    if not found and service_seasons:
        mine = sorted({e["season"] for e in sonarr_eps if e["season"] > 0 and e.get("air_date")})
        if mine and mine[-1] not in service_seasons:  # numbered apart: pair the latest ones, newest first
            for a, b in zip(reversed(mine), reversed(service_seasons)):
                if a != b:
                    found[a] = b
    return found


# ponytail: the main language where you watch; a country missing here reads its services' titles in English
COUNTRY_LANGUAGE = {"FR": "fr", "BE": "fr", "CH": "fr", "LU": "fr", "CA": "fr", "DE": "de", "AT": "de", "ES": "es",
                    "MX": "es", "AR": "es", "IT": "it", "NL": "nl", "PT": "pt", "BR": "pt", "PL": "pl", "SE": "sv",
                    "DK": "da", "NO": "no", "FI": "fi", "JP": "ja", "KR": "ko"}


def tmdb_episodes(tmdb_id: int) -> list[dict]:
    """The series' episodes on TMDB, each named in every language that may meet: the series' own, the one where
    you watch (a service names episodes in it: Disney+ France gives Futurama French titles) and English
    (Sonarr's). [{"names": [...], "air_date", "season", "number"}]: TMDB's episode ties its names together, its date
    (and, for two aired the same day, its number) to Sonarr's."""
    get = lambda **params: requests.get(f"https://api.themoviedb.org/3/tv/{tmdb_id}", params={"api_key": tmdb_key(), **params}, timeout=20)
    r = get()
    r.raise_for_status()
    show = r.json()
    seasons = [x["season_number"] for x in show.get("seasons") or []]
    languages = dict.fromkeys([show.get("original_language") or "en", COUNTRY_LANGUAGE.get(sonarr_sync.SETTINGS.get("country", ""), "en"), "en"])
    episodes: dict[tuple, dict] = {}
    for language in languages:
        for i in range(0, len(seasons), 20):  # TMDB appends 20 seasons at most to one request
            r = get(language=language, append_to_response=",".join(f"season/{n}" for n in seasons[i:i + 20]))
            r.raise_for_status()
            for n in seasons[i:i + 20]:
                for e in (r.json().get(f"season/{n}") or {}).get("episodes") or []:
                    ep = episodes.setdefault((n, e.get("episode_number")), {"names": [], "air_date": e.get("air_date"),
                                                                            "season": n, "number": e.get("episode_number")})
                    if e.get("name") and e["name"] not in ep["names"]:
                        ep["names"].append(e["name"])
    return list(episodes.values())


def plain_title(text: str) -> str:
    """A title to compare: no accents, case, punctuation or "(1/2)"."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"\(\s*\d+\s*/\s*\d+\s*\)", "", text)).strip()


GENERIC_TITLE = re.compile(r"(tba|tbd|episode|episodio|folge|aflevering)( \d+)?")


def usable_title(text: str | None) -> str:
    """A title worth comparing, plain; "" for none, a placeholder ("TBA", "Episode 3") or one written mostly
    in another alphabet (TMDB's Japanese title of an anime, before its translations). An episode number
    ahead of the name goes (Molotov's "E6 Franck Dubosc")."""
    letters = [c for c in text or "" if c.isalpha()]
    latin = sum(unicodedata.normalize("NFKD", c).encode("ascii", "ignore").isalpha() for c in letters)
    if letters and latin * 2 < len(letters):  # another alphabet: "修行DEディナー" is not a title "de"
        return ""
    plain = plain_title(text or "")
    if GENERIC_TITLE.fullmatch(plain):
        return ""
    return re.sub(r"^e\d+ (?=\w)", "", plain)


TVDB_TITLES = sonarr_sync.DATA / "tvdb_titles.json"
TVDB_TITLES_DAYS = 7
TVDB_LANGUAGES = {"fr": "fra", "de": "deu", "es": "spa", "it": "ita", "nl": "nld", "pt": "por", "pl": "pol", "sv": "swe",
                  "da": "dan", "no": "nor", "fi": "fin", "ja": "jpn", "ko": "kor"}
EPISODE_ROW = re.compile(r'episode-label">\s*(S\d+E\d+)\s*</span>\s*<a href="[^"]+">\s*(.*?)\s*</a>', re.S)


def tvdb_titles(tvdb_id: int) -> dict[str, str]:
    """The episode titles TheTVDB's website gives in the language where you watch ("S04E04": "Orelsan"), kept a
    week; no API key. Sonarr's come in English only, "TBA" for a series TVDB names in French alone. A page
    TVDB changes finds nothing: never worse than without."""
    language = TVDB_LANGUAGES.get(COUNTRY_LANGUAGE.get(sonarr_sync.SETTINGS.get("country", ""), ""))
    if not language:
        return {}
    cache = read_json(TVDB_TITLES, {})
    key, hit = f"{tvdb_id}:{language}", None
    hit = cache.get(key)
    if hit and datetime.now(timezone.utc) - datetime.fromisoformat(hit["checked"]) < timedelta(days=TVDB_TITLES_DAYS):
        return hit["titles"]
    get = lambda url, **params: requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0 Unshacklarr"})
    series = get(f"https://thetvdb.com/dereferrer/series/{int(tvdb_id)}")  # to its page, /series/<slug>
    series.raise_for_status()
    page = get(series.url.rstrip("/") + "/allseasons/official", lang=language)
    page.raise_for_status()
    titles = {code: html.unescape(re.sub(r"\s+", " ", name)).strip() for code, name in EPISODE_ROW.findall(page.text)}
    cache[key] = {"checked": datetime.now(timezone.utc).isoformat(), "titles": titles}
    write_atomic(TVDB_TITLES, json.dumps(cache))
    return titles


def same_title(a: str, b: str) -> bool:
    """Two plain titles naming the same episode: a longer one when close (accents, a typo) or one holds the
    other; a short one (a country, under 8 letters) when the same, or spelt alike from the same start (Serbie,
    Serbia) but never merely close (Mali, Malibu; Chili, Chine)."""
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    if min(len(a), len(b)) < 8:
        return a == b or (a[:4] == b[:4] and ratio >= 0.82)
    return a in b or b in a or ratio >= 0.85


def titles_of(sonarr_eps: list[dict], tmdb_eps: list[dict], own: bool = True) -> dict[int, set]:
    """Every title a Sonarr episode goes by, plain: Sonarr's (unless own is false), and TMDB's in each language
    for the episode TMDB airs that day; two aired the same day (Futurama's S11E01 and E02) are told apart by
    TMDB's number."""
    by_date: dict[str, list[dict]] = {}
    for e in sonarr_eps:
        if e.get("airDate"):
            by_date.setdefault(e["airDate"], []).append(e)
    titles = {e["id"]: {usable_title(e.get("title"))} if own else set() for e in sonarr_eps}
    for t in tmdb_eps:
        if t.get("episodeId") in titles:  # a title for that very episode (TVDB's website, in your language)
            titles[t["episodeId"]] |= {usable_title(n) for n in t.get("names") or []}
            continue
        same_day = by_date.get(t.get("air_date") or "") or []
        if len(same_day) > 1:
            same_day = [e for e in same_day if (e.get("seasonNumber"), e.get("episodeNumber")) == (t.get("season"), t.get("number"))]
        if len(same_day) == 1:
            titles[same_day[0]["id"]] |= {usable_title(n) for n in t.get("names") or [t.get("name")]}
    return {id_: {t for t in names if t} for id_, names in titles.items()}


def match_by_title(service_eps: list[dict], sonarr_eps: list[dict], tmdb_eps: list[dict]) -> dict[str, dict]:
    """The service's episodes found in Sonarr by their title: service episode ("S26E11", or "S26E12" for all
    its parts) -> {"episodeId", "name"}. The title is looked for in Sonarr's own titles (TVDB's) and in TMDB's,
    whose air date gives the episode in Sonarr. A title that fits two episodes, or that two of the service's
    episodes share, finds nothing."""
    named = [(title, id_) for id_, titles in titles_of(sonarr_eps, tmdb_eps).items() for title in titles]
    key = lambda t: f"S{t['season']:02}E{t['number']:02}"
    keys_by_name: dict[str, set] = {}
    for t in service_eps:
        keys_by_name.setdefault(usable_title(t.get("name")), set()).add(key(t))
    found: dict[str, list] = {}
    for t in service_eps:
        name = usable_title(t.get("name"))
        if not name or len(keys_by_name[name]) > 1:
            continue
        ids = {id_ for other, id_ in named if same_title(name, other)}
        if len(ids) == 1:
            found.setdefault(key(t), []).append((t.get("part"), ids.pop(), t.get("name")))
    matches = {}
    for k, hits in found.items():
        if len({h[1] for h in hits}) == 1:  # every part of it: the same Sonarr episode, taken whole
            matches[k] = {"episodeId": hits[0][1], "name": re.sub(r"\s*\(\s*\d+\s*/\s*\d+\s*\)$", "", (hits[0][2] or "")[:300].rstrip())}
    claims: dict[int, int] = {}
    for m in matches.values():
        claims[m["episodeId"]] = claims.get(m["episodeId"], 0) + 1
    return {k: m for k, m in matches.items() if claims[m["episodeId"]] == 1}  # one Sonarr episode, two of theirs: neither


def list_titles(show: dict) -> list[dict]:
    """The series' titles on its service, with its profile, proxy and service options: nothing downloaded."""
    dl, own = sonarr_sync.stacked(show, read_config())
    params = {k: v for k, v in options.to_params(dl, options.dl_specs()).items() if k in ("profile", "proxy", "no_proxy")}
    params.update(options.to_params(own, options.service_specs(sonarr_sync.service_entry(show["service"]))))
    return sonarr_sync.backend_for(show["service"]).call("POST", "/api/list-titles", json={**params, "service": show["service"], "title_id": str(show["title"])})["titles"]


SEASON_MAP_FROM = 3  # episodes listed, at least, for a season map to be suggested


def probe_series(show: dict, series_id: int, title: str = "") -> dict:
    """List the series on its service (nothing downloaded) and check the next episodes against Sonarr."""
    titles = list_titles(show)
    service_eps = [t for t in titles if t.get("type") == "episode"]
    by_key = {}
    for t in service_eps:
        key = f"S{t['season']:02}E{t['number']:02}" + (f".{t['part']}" if t.get("part") else "")
        by_key[key] = t
        by_key.setdefault(f"S{t['season']:02}E{t['number']:02}", t)  # a split episode answers to its plain number too
    every = sonarr_sync.sonarr_get("episode", seriesId=series_id) if series_id else []
    sonarr = [e for e in every if e["seasonNumber"] > 0]
    sonarr_eps = [{"season": e["seasonNumber"], "air_date": (e.get("airDateUtc") or "")[:10] or None} for e in sonarr]
    now = datetime.now(timezone.utc).isoformat()
    aired = sorted((e for e in sonarr if e.get("airDateUtc") and e["airDateUtc"] <= now), key=lambda e: e["airDateUtc"])
    coming = sorted((e for e in sonarr if e.get("airDateUtc") and e["airDateUtc"] > now), key=lambda e: e["airDateUtc"])
    name = show.get("file_name") or title
    dots = lambda text: re.sub(r"[^\w-]+", ".", text).strip(".")
    checks = []
    for e in aired[-3:] + coming[:1]:
        sxxeyy = f"S{e['seasonNumber']:02}E{e['episodeNumber']:02}"
        theirs = sonarr_sync.service_episode(show, e["seasonNumber"], e["episodeNumber"])
        hit = by_key.get(theirs or "")
        checks.append({
            "sxxeyy": sxxeyy, "service": theirs, "aired": e["airDateUtc"] <= now, "air": e["airDateUtc"], "hasFile": e.get("hasFile"),
            "found": bool(hit), "name": (hit or {}).get("name"), "file": f"{dots(name)}.{sxxeyy}….mkv" if name else None,
        })
    seasons = {}
    for t in service_eps:
        seasons[t["season"]] = seasons.get(t["season"], 0) + 1
    tmdb_eps = []  # with a TMDB key, its titles too; Sonarr's own always count
    info = sonarr_sync.sonarr_get(f"series/{series_id}") if series_id else {}
    if tmdb_key() and (tmdb_id := info.get("tmdbId")):
        try:
            tmdb_eps = tmdb_episodes(tmdb_id)
        except requests.RequestException as e:
            print(f"TMDB: no titles for {title}: {tmdb_error(e)}", flush=True)
    local = {}  # TVDB's titles in your language, by Sonarr's episode: its own numbering, so no date is needed
    if info.get("tvdbId"):
        try:
            names = tvdb_titles(info["tvdbId"])
            local = {e["id"]: names[k] for e in every if (k := f"S{e['seasonNumber']:02}E{e['episodeNumber']:02}") in names}
        except requests.RequestException as e:
            print(f"TVDB: no titles for {title}: {type(e).__name__}", flush=True)
    tmdb_eps = tmdb_eps + [{"episodeId": i, "names": [n]} for i, n in local.items()]
    by_title = match_by_title(service_eps, every, tmdb_eps)  # the service's episodes found by their title, whatever their number
    titled = {m["episodeId"]: key for key, m in by_title.items()}
    # The service's titles meet ours often enough (a language in common): one that meets none of an episode's
    # titles then says another episode, whatever its number (RMC+'s S01E03 "France" is not TVDB's "Mali").
    names = [n for t in service_eps if (n := usable_title(t.get("name")))]
    enough = lambda hits: hits >= 3 and hits >= len(names) / 4
    comparable = enough(len(by_title))
    # Sonarr's own titles weigh only in the service's language (French, J'irai dormir), not in English against
    # a French Disney+ (Futurama): there, an episode with no French title from TMDB has nothing to contradict.
    ours = {usable_title(e.get("title")) for e in every} - {""}
    known = titles_of(every, tmdb_eps, own=enough(sum(any(same_title(n, o) for o in ours) for n in names)))
    mapped = show.get("episode_map") or {}
    available = {}  # every Sonarr episode the service has, for the Episodes tab: by number and title, by title, by number
    for e in every:
        sxxeyy = f"S{e['seasonNumber']:02}E{e['episodeNumber']:02}"
        theirs = sonarr_sync.service_episode(show, e["seasonNumber"], e["episodeNumber"])
        hit = by_key.get(theirs or "")
        if e["id"] in titled:
            key = titled[e["id"]]
            available[e["id"]] = {"service": key, "name": by_title[key]["name"], "match": "title"}
        elif hit and (theirs or "").split(".")[0] not in by_title:  # a number the title gives to another episode is not this one
            theirs_name = usable_title(hit.get("name"))
            clash = comparable and theirs_name and known[e["id"]] and not any(same_title(theirs_name, t) for t in known[e["id"]])
            if sxxeyy in mapped or not clash:
                available[e["id"]] = {"service": theirs, "name": hit.get("name")}
    # Numbered from the series' first episode (Netflix's anime: Sonarr's S02E01, the 13th, is its S02E13; a single
    # season on 6play): by Sonarr's absolute number, in the episode's season, else in the first. Only a number no
    # other episode has there, and whose title doesn't say another episode.
    taken = {a["service"] for a in available.values()}
    for e in every:
        absolute = e.get("absoluteEpisodeNumber")
        if e["id"] in available or not absolute or e["seasonNumber"] < 1 or f"S{e['seasonNumber']:02}E{e['episodeNumber']:02}" in mapped:
            continue
        for key in dict.fromkeys((f"S{e['seasonNumber']:02}E{absolute:02}", f"S01E{absolute:02}")):
            hit = by_key.get(key)
            theirs_name = usable_title((hit or {}).get("name"))
            clash = comparable and theirs_name and known[e["id"]] and not any(same_title(theirs_name, t) for t in known[e["id"]])
            if hit and key not in taken and key not in by_title and not clash:
                available[e["id"]] = {"service": key, "name": hit.get("name"), "match": "absolute"}
                taken.add(key)
                break
    return {
        "available": available,
        "titled": sorted(by_title),  # the service's numbers some Sonarr episode's title points to
        "local_titles": local,  # TVDB's titles in your language, for the page to show over Sonarr's "TBA"
        # everything the service lists, as it numbers it: what a download's own numbering can ask for
        "titles": [{"key": f"S{t['season']:02}E{t['number']:02}", "part": t.get("part"), "name": t.get("name")} for t in service_eps],
        "series": service_eps[0].get("series_title") if service_eps else None,
        "count": len(service_eps),
        "seasons": [{"season": k, "episodes": v} for k, v in sorted(seasons.items())],
        "checks": checks,
        # never from a listing of one or two episodes: a link to one episode (iPlayer's /episode/) would send a whole
        # Sonarr season to that episode's season
        "season_map": {str(k): v for k, v in suggest_season_map(sonarr_eps, service_eps).items()
                       if (show.get("season_map") or {}).get(k) != v} if len(service_eps) >= SEASON_MAP_FROM else {},
    }


SERVICE_LISTS = sonarr_sync.DATA / "service_lists.json"  # the last "What's on <service>?" of each series
SERVICE_LIST_HOURS = 12


def keep_service_list(tvdb: int, show: dict, found: dict) -> None:
    lists = read_json(SERVICE_LISTS, {})
    lists[str(tvdb)] = {"checked": datetime.now(timezone.utc).isoformat(), "service": show["service"], "title": str(show["title"]),
                        "available": found["available"], "titles": found["titles"], "local_titles": found.get("local_titles") or {}}
    write_atomic(SERVICE_LISTS, json.dumps(lists))


def title_matches(show: dict, ep: dict) -> dict:
    """For the sync, before a download: where the service has each Sonarr episode (by number, title or absolute
    number: Sonarr id -> {"service", "match"}) and every number it lists, the list kept for the page too."""
    found = probe_series(show, ep["seriesId"], ep["series"]["title"])
    if not sonarr_sync.instance():  # the page's list holds the main Sonarr's episode ids
        keep_service_list(ep["series"]["tvdbId"], show, found)
    listed = {t["key"] + (f".{t['part']}" if t.get("part") else "") for t in found["titles"]} | {t["key"] for t in found["titles"]}
    return {"available": {int(i): m for i, m in found["available"].items()}, "listed": listed,
            "titled": set(found.get("titled") or ())}  # the numbers another episode's title gives


sonarr_sync.find_by_title = title_matches


def release_learned(tvdb: int, at: str, day: int) -> None:
    """A release time the sync learnt (release_learn): in the series' settings, as if typed on its page."""
    config = read_config()
    show = config["series"].get(tvdb)
    if not show or show.get("release_time"):
        return  # gone, or set meanwhile: never over one set by hand
    show["release_time"] = at
    if day:
        show["release_day"] = day
    write_config(config)


sonarr_sync.on_release_learned = release_learned


async def service_list(request):
    """The series' last check of what its service has, if recent and for the service and URL it has now."""
    tvdb = request.match_info["tvdb"]
    lists = read_json(SERVICE_LISTS, {})
    hit, show = lists.get(tvdb), read_config()["series"].get(int(tvdb)) or {}
    fresh = hit and hit["service"] == show.get("service") and hit["title"] == str(show.get("title")) and (
        datetime.now(timezone.utc) - datetime.fromisoformat(hit["checked"]) < timedelta(hours=SERVICE_LIST_HOURS))
    return web.json_response(hit if fresh else None)


async def probe(request):
    body = await json_object(request)
    show = body.get("show") or {}
    if not show.get("service") or not show.get("title"):
        raise web.HTTPBadRequest(text="Pick a service and its URL first")
    show = {**show, "season_map": {int(k): int(v) for k, v in (show.get("season_map") or {}).items()}}
    try:
        found = await asyncio.to_thread(probe_series, show, int(body.get("seriesId") or 0), str(body.get("title") or ""))
        if body.get("tvdbId"):
            await asyncio.to_thread(keep_service_list, int(body["tvdbId"]), show, found)
        return web.json_response({**found, "checked": datetime.now(timezone.utc).isoformat()})
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))
    except requests.RequestException as e:
        raise web.HTTPBadGateway(text=f"Sonarr is unreachable: {no_credentials(e)}")


async def release_seen(request):
    """When this series' episodes were seen coming out on the service, and the time that suggests."""
    tvdb = int(request.match_info["tvdb"])
    seen = read_json(sonarr_sync.SEEN_FILE, {})
    entries = [e for e in seen.values() if e.get("tvdbId") == tvdb]
    return web.json_response({"suggestion": sonarr_sync.suggest_release(entries), "seen": len(entries)})


async def list_leftovers(_):
    """What waits in the downloads folder, with why: the last run of that episode says it."""
    items = await asyncio.to_thread(sonarr_sync.leftovers)
    config = read_config()
    cards = await asyncio.to_thread(run_cards) if sonarr_sync.RUNS_DIR.exists() else []
    for item in items:
        last = next((c for c in cards if c.get("tvdbId") == item["tvdbId"] and c.get("sxxeyy") == item["sxxeyy"] and c.get("ended")
                     and c.get("instance", "") == item["instance"]), None)
        item.update(series=(last or {}).get("series"), outcome=(last or {}).get("outcome"), cause=(last or {}).get("cause"),
                    sonarr_path=sonarr_sync.seen_by("sonarr_downloads", sonarr_sync.DOWNLOADS / item["folder"],
                                                     {"sonarr_downloads": sonarr_sync.SONARRS[item["instance"]]["downloads"]} if item["instance"] in sonarr_sync.SONARRS else None),
                    by_hand=sonarr_sync.waits_for_hand(item["tvdbId"], config, item["instance"]))
    return web.json_response({"items": items, "days": sonarr_sync.SETTINGS.get("leftovers_days")})


async def leftover_action(request):
    body = await json_object(request)
    folder = str(body.get("folder") or "")
    try:
        path = sonarr_sync.leftover_path(folder)
        if request.match_info["action"] == "delete":
            await asyncio.to_thread(sonarr_sync.shutil.rmtree, path, True)
        else:
            await asyncio.to_thread(sonarr_sync.import_leftover, folder)
    except (RuntimeError, requests.RequestException) as e:
        raise web.HTTPBadRequest(text=str(e))
    return web.json_response({"done": True})


# ---- Web Push: this device's notifications ----

async def push_key(_):
    return web.json_response({"key": await asyncio.to_thread(sonarr_sync.PUSH.public_key),
                              "devices": len(sonarr_sync.PUSH.subscriptions())})


async def push_subscribe(request):
    body = await json_object(request)
    try:
        await asyncio.to_thread(sonarr_sync.PUSH.subscribe, body.get("subscription") or {}, str(body.get("label") or ""))
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e))
    return web.json_response({"devices": len(sonarr_sync.PUSH.subscriptions())})


async def push_unsubscribe(request):
    await asyncio.to_thread(sonarr_sync.PUSH.unsubscribe, str((await json_object(request)).get("endpoint") or ""))
    return web.json_response({"devices": len(sonarr_sync.PUSH.subscriptions())})


# ---- The bell: every notification, the actions waiting for the person first ----

async def inbox(_):
    box = await asyncio.to_thread(sonarr_sync.read_inbox)
    read_at = box.get("read_at") or ""
    return web.json_response({"items": box["items"], "read_at": read_at, "unread": sum(1 for i in box["items"] if i["at"] > read_at)})


async def inbox_action(request):
    if request.match_info["action"] == "read":
        change = lambda box: box.update(read_at=datetime.now(timezone.utc).isoformat())
    else:  # clear
        change = lambda box: box.update(items=[i for i in box["items"] if (i.get("action") and not i["action"].get("done"))])
    await asyncio.to_thread(sonarr_sync.change_inbox, change)
    return await inbox(request)


async def push_test(request):
    endpoint = str((await json_object(request)).get("endpoint") or "")
    sent = await asyncio.to_thread(sonarr_sync.PUSH.send, "Unshacklarr test", "Notifications will show up here.", endpoint or None)
    if not sent:
        raise web.HTTPBadGateway(text=f"The message did not go through ({sonarr_sync.PUSH.last_error or 'this device is no longer subscribed'}): turn notifications off and on again")
    return web.json_response({"sent": sent})


def retried_numbering(ids: list[int], batch: str) -> dict | None:
    """The numbering a download was given for itself (from the page: by title, or set for it), which a retry
    of it repeats; None when its last attempt used the series' own. The episodes of one retry come from one
    job, given one numbering: the first found stands for them all."""
    if not sonarr_sync.RUNS_DIR.exists():
        return None
    seen = set()
    for card in cards_on_disk():  # newest first: each episode's last attempt decides
        episode = card.get("episodeId")
        if episode not in ids or episode in seen or (batch and card.get("batch") != batch):
            continue
        seen.add(episode)
        if card.get("numbering") is not None:
            return card["numbering"]
    return None


def cdm_refusal(ids: list[int]) -> str:
    """Why these episodes can't be downloaded: a service among theirs has no CDM at all. Sonarr is asked
    which series they belong to only when a series' service lacks one."""
    config = read_config()
    shows = {tvdb: s for tvdb, s in config["series"].items() if s.get("service")}
    lacking = {svc: why for svc in {s["service"] for s in shows.values()} if (why := sonarr_sync.no_cdm(svc, config))}
    if not lacking:
        return ""
    try:
        episodes = sonarr_sync.chosen_episodes(ids)
    except requests.RequestException as e:
        raise web.HTTPBadGateway(text=f"Sonarr is unreachable: {no_credentials(e)}")
    return next((lacking[show["service"]] for ep in episodes
                 if (show := shows.get(ep["series"]["tvdbId"])) and show["service"] in lacking), "")


async def download(request):
    body = await json_object(request)
    ids = body.get("episodeIds") or []
    if not ids or not all(isinstance(i, int) and i > 0 for i in ids):
        raise web.HTTPBadRequest(text="Pick at least one episode")
    replace = body.get("replace") is True
    # For this download only: numbering and file names that stand in for the series' own, not saved
    numbering = check_numbering(body["numbering"], "this download") if isinstance(body.get("numbering"), dict) else None
    print(f"Download asked for {len(ids)} episode{'s' if len(ids) > 1 else ''}{', replacing files on disk' if replace else ''}"
          f"{', with its own numbering' if numbering is not None else ''}", flush=True)
    batch = str(body.get("batch") or "")  # a retry from a job: it stays in that job
    if batch and not BATCH_ID.fullmatch(batch):
        raise web.HTTPBadRequest(text="Unknown job")
    inst = sonarr_named(str(body.get("sonarr") or ""))  # episodes picked in another Sonarr: its ids, its ladder
    if why := await asyncio.to_thread(on_sonarr, inst, cdm_refusal, ids):
        raise web.HTTPBadRequest(text=why)
    if numbering is None and body.get("retry") is True:  # the same numbering as the attempt it retries
        numbering = await asyncio.to_thread(retried_numbering, ids, batch)
    if batch and len(ids) == 1 and await join_job(batch, ids[0]):
        return web.json_response({"running": True})  # the job still runs: at the end of its queue, one download at a time
    room_for_one_more()
    run_sync(ids, replace=replace, kind="retry" if body.get("retry") is True else "manual", numbering=numbering, batch=batch or None,
             sonarr=inst["name"] if inst else "")
    return web.json_response({"running": True})


def missing_of_managed(days: int | None, now: datetime | None = None) -> list[dict]:
    """The episodes Unshacklarr could catch up on: monitored, aired, without a file, of the series it downloads (not
    Sonarr's whole wanted list: other series there are for other download clients), none already waiting in the
    downloads folder; aired within `days` when given. Oldest first."""
    config, now = sonarr_sync.read_file(), now or datetime.now(timezone.utc)
    managed = {int(k) for k, v in (config.get("series") or {}).items() if v.get("service") and v.get("title")}
    out = []
    for tvdb, series in sonarr_sync.sonarr_series(managed).items():
        if tvdb not in managed or not series.get("monitored", True):
            continue
        for ep in sonarr_sync.sonarr_get("episode", seriesId=series["id"]):
            aired = ep.get("airDateUtc") and sonarr_sync.parse_time(ep["airDateUtc"])
            if (not ep.get("monitored", True) or ep.get("hasFile") or ep.get("seasonNumber", 0) < 1 or not aired or aired > now
                    or (days and now - aired > timedelta(days=days))
                    or sonarr_sync.episode_folder({**ep, "series": series}).exists()):
                continue
            out.append({"episodeId": ep["id"], "tvdbId": tvdb, "series": series["title"], "aired": ep["airDateUtc"],
                        "sxxeyy": f"S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}", "title": ep.get("title")})
    return sorted(out, key=lambda e: (e["aired"], e["series"]))


# Upgrades: the files of the series with a quality ladder that are not on its first step, checked against the
# service's tracks, a series' episodes UPGRADE_BATCH at a time. A list-tracks logs in to the service: a pause between two, and only
# when asked (Activity, Upgrades).
UPGRADES_FILE = sonarr_sync.DATA / "upgrades_found.json"  # the last check's finds: {"checked", "items"}; another Sonarr's apart


def upgrades_file(name: str = "") -> Path:
    return UPGRADES_FILE.with_name(f"upgrades_found-{name}.json") if name else UPGRADES_FILE
UPGRADE_PAUSE = 2.0  # seconds between two episodes asked of a service
upgrade_scan = {"running": False, "done": 0, "total": 0, "series": "", "errors": 0, "stop": False, "sonarr": ""}


def upgrade_candidates(config: dict) -> list[tuple]:
    """Every file of a series with a ladder that is not on its first step, or, with upgrade_other_groups, from another
    release group than the series' downloads carry: (series, show, ladder, episode, track, step). track["ours"] is that
    group, track["group"] the file's other one."""
    other_groups = bool(sonarr_sync.SETTINGS.get("upgrade_other_groups"))
    shows = {int(k): v for k, v in (config.get("series") or {}).items() if v.get("service") and v.get("title")}
    work = []
    for tvdb, serie in sonarr_sync.sonarr_series(set(shows)).items():
        show = shows.get(tvdb)
        ladder = show and not show.get("skip_upgrades") and sonarr_sync.ladder_of(show, config)
        if not ladder:
            continue
        ours = sonarr_sync.release_group_of(show, config)[0] if other_groups else ""
        files = {f["id"]: f for f in sonarr_sync.sonarr_get("episodefile", seriesId=serie["id"])}
        for e in sonarr_sync.sonarr_get("episode", seriesId=serie["id"]):
            if not e.get("hasFile") or e.get("seasonNumber", 0) < 1 or e.get("episodeFileId") not in files:
                continue
            years = float(sonarr_sync.SETTINGS.get("upgrade_max_age_years") or 0)
            if years and e.get("airDateUtc") and \
                    datetime.now(timezone.utc) - sonarr_sync.parse_time(e["airDateUtc"]) > timedelta(days=365.25 * years):
                continue
            file = files[e["episodeFileId"]]
            track = ladder_track(file.get("mediaInfo") or {})
            if ours:
                track["ours"] = ours
                if str(file.get("releaseGroup") or "").lower() != ours.lower():
                    track["group"] = file.get("releaseGroup") or "no group"  # not ours: the same step from the service replaces it
            if (step := sonarr_sync.step_of(ladder, track)) > 0 or track.get("group"):
                work.append((serie, show, ladder, e, track, step))
    return work


def scan_upgrades(name: str = "") -> None:
    """Ask each candidate's service which step it has; keep those it has on an earlier step than the file. For
    another Sonarr (name), its files, placed on its own ladder."""
    inst = sonarr_sync.SONARRS.get(name) if name else None
    with sonarr_sync.on_instance(inst):
        scan_upgrades_in(inst, name)


UPGRADE_BATCH = 10  # episodes of one series asked in one list-tracks: one login for them


def upgrades_seen_file(name: str = "") -> Path:
    """Each episode's last answer, kept upgrade_recheck_days: {episode id: {"checked", "file", "item"}}. Another Sonarr's apart."""
    return sonarr_sync.DATA / (f"upgrades_seen-{name}.json" if name else "upgrades_seen.json")


def file_label(track: dict) -> str:
    return sonarr_sync.track_label(track) + (f" from {track['group']}" if track.get("group") else "")


def upgrade_item(serie: dict, ladder: dict, e: dict, track: dict, step: int, best) -> dict | None:
    """The episode as Upgrades lists it, when the service has it on an earlier step than its file (on the same step
    too, for a file from another release group than ours)."""
    if not best or best[0] - 1 > step or best[0] - 1 == step and not track.get("group"):
        return None
    number, have = best
    label = file_label(track)
    return {"episodeId": e["id"], "tvdbId": serie["tvdbId"], "series": serie["title"], "ladder": ladder["name"],
            "sxxeyy": f"S{e['seasonNumber']:02}E{e['episodeNumber']:02}", "title": e.get("title"),
            "file": label, "group": track.get("group"), "fileStep": step + 1 if step < len(ladder["steps"]) else None,
            "better": sonarr_sync.track_label(have), "betterStep": number}


def scan_upgrades_in(inst: dict | None, name: str) -> None:
    """an answer younger than upgrade_recheck_days is reused for the same file; the rest is asked
    per series, UPGRADE_BATCH episodes in one list-tracks (each answer names its season and number)."""
    config, found = sonarr_sync.read_file(), []
    config = sonarr_sync.instance_config(inst, config) if inst else config
    upgrade_scan.update(running=True, done=0, total=0, series="", errors=0, stop=False, sonarr=name)
    now = datetime.now(timezone.utc)
    keep_days = float(sonarr_sync.SETTINGS.get("upgrade_recheck_days", 30) or 0)
    seen = read_json(upgrades_seen_file(name), {})
    try:
        work = upgrade_candidates(config)
        upgrade_scan["total"] = len(work)
        ask: dict[int, list] = {}
        for w in work:
            e = w[3]
            hit = seen.get(str(e["id"]))
            if keep_days and hit and hit.get("file") == e.get("episodeFileId") and hit.get("groups", "") == w[4].get("ours", "") \
                    and now - datetime.fromisoformat(hit["checked"]) < timedelta(days=keep_days):
                upgrade_scan["done"] += 1  # answered lately, for this very file: not asked again
                if hit.get("item"):
                    found.append({**hit["item"], "file": file_label(w[4])})  # labelled as the file is labelled now
            else:
                ask.setdefault(w[0]["id"], []).append(w)
        for chunks in ask.values():
            show = chunks[0][1]
            size = 1 if int(show.get("parts") or 0) > 1 else UPGRADE_BATCH  # parts: one episode at a time, as before
            for start in range(0, len(chunks), size):
                if upgrade_scan["stop"]:
                    break
                chunk = chunks[start:start + size]
                serie, show, ladder = chunk[0][:3]
                upgrade_scan["series"] = serie["title"]
                wanted = {}
                for w in chunk:
                    if key := sonarr_sync.service_episode(show, w[3]["seasonNumber"], w[3]["episodeNumber"]):
                        wanted[key] = w
                try:
                    if not wanted:
                        continue
                    first = next(iter(wanted.values()))[3]
                    request = sonarr_sync.download_request(show, config, next(iter(wanted)), sonarr_sync.episode_folder({**first, "series": serie}))
                    if request.get("remote"):
                        continue  # a --remote download lists no tracks
                    request["wanted"] = list(wanted)
                    listed = sonarr_sync.list_tracks(show, config, request, ladder) or []
                except (UnshackleError, ValueError) as err:
                    upgrade_scan["errors"] += 1
                    print(f"Upgrades: {serie['title']} {', '.join(wanted)}: {err}", flush=True)
                    continue
                finally:
                    upgrade_scan["done"] += len(chunk)
                    time.sleep(UPGRADE_PAUSE)
                by_key: dict[str, list] = {}
                for ep in listed:
                    t = ep.get("title") or {}
                    if t.get("season") is not None and t.get("number") is not None:
                        by_key.setdefault(f"S{int(t['season']):02}E{int(t['number']):02}", []).append(ep)
                if len(wanted) == 1 and listed and not by_key:  # one episode asked, its answer without a title
                    by_key[next(iter(wanted))] = listed
                for key, (serie_, show_, ladder_, e, track, step) in wanted.items():
                    parts = by_key.get(key.split(".")[0])
                    if parts is None:
                        continue  # left out of the answer (not on the service now): asked again next time
                    item = upgrade_item(serie, ladder, e, track, step, sonarr_sync.climb(ladder, parts))
                    seen[str(e["id"])] = {"checked": now.isoformat(), "file": e.get("episodeFileId"), "item": item, "groups": track.get("ours", "")}
                    if item:
                        found.append(item)
        write_atomic(upgrades_seen_file(name), json.dumps(seen))
        write_atomic(upgrades_file(name), json.dumps({"checked": datetime.now(timezone.utc).isoformat(), "items": found,
                                                "stopped": upgrade_scan["stop"]}))
    except requests.RequestException as err:
        upgrade_scan["errors"] += 1
        print(f"Upgrades: Sonarr is unreachable: {no_credentials(err)}", flush=True)
    finally:
        upgrade_scan.update(running=False, series="")


async def upgrade_groups(_):
    """Settings, Upgrades: the release group each set-up series' downloads carry, grouped, and where it is set."""
    config = read_config()

    def found():
        out, none = {}, []
        for key, show in (config.get("series") or {}).items():
            if not show.get("service"):
                continue
            tag, where = sonarr_sync.release_group_of(show, config)
            if tag:
                out.setdefault((tag, where), []).append(int(key))
            else:
                none.append(int(key))
        return [{"group": t, "where": w, "series": ids} for (t, w), ids in sorted(out.items(), key=lambda x: -len(x[1]))], none
    groups, none = await asyncio.to_thread(found)
    return web.json_response({"groups": groups, "none": none})


async def upgrades(request):
    """Activity, Upgrades: the last check's finds, and the check going on; another Sonarr's with ?sonarr=."""
    name = (sonarr_named(request.query.get("sonarr", "")) or {}).get("name", "")
    last = await asyncio.to_thread(read_json, upgrades_file(name), {})
    cards = await asyncio.to_thread(run_cards) if sonarr_sync.RUNS_DIR.exists() else []
    # replaced since the check: gone from the list; being replaced now: said so
    mine = [c for c in cards if c.get("instance", "") == name]  # that Sonarr's copies only
    done = {(c.get("tvdbId"), c.get("sxxeyy")) for c in mine if c.get("outcome") == "downloaded" and (c.get("ended") or "") > (last.get("checked") or "")}
    busy = {(c.get("tvdbId"), c.get("sxxeyy")) for c in mine if c.get("outcome") == "running"}
    items = [{**i, "running": (i["tvdbId"], i["sxxeyy"]) in busy} for i in last.get("items") or [] if (i["tvdbId"], i["sxxeyy"]) not in done]
    return web.json_response({"scan": {k: v for k, v in upgrade_scan.items() if k != "stop"},
                              "checked": last.get("checked"), "items": items, "stopped": bool(last.get("stopped"))})


async def upgrades_action(request):
    """Start a check (in the background: it takes a pause per episode), or stop the one going on."""
    if request.match_info["action"] == "stop":
        upgrade_scan["stop"] = True
    elif not upgrade_scan["running"]:
        inst = sonarr_named(str((await json_object(request)).get("sonarr") or "")) if request.can_read_body else None
        if sonarr_sync.unset_ladder(inst):
            raise web.HTTPBadRequest(text=f"Choose a quality ladder for Sonarr {inst['name']} in Settings, Sonarr first")
        upgrade_scan["running"] = True  # at once: a second click before the thread starts is not a second check
        threading.Thread(target=scan_upgrades, args=((inst or {}).get("name", ""),), daemon=True).start()
    return web.json_response({"running": upgrade_scan["running"]})


async def missing(request):
    """Activity's catch-up: what missing_of_managed finds, aired within ?days= when given, in ?sonarr= when given."""
    days = request.query.get("days", "")
    if days and not days.isdigit():
        raise web.HTTPBadRequest(text="days is a number of days")
    inst = sonarr_named(request.query.get("sonarr", ""))
    try:
        items = await asyncio.to_thread(on_sonarr, inst, missing_of_managed, int(days) if days else None)
    except requests.RequestException as e:
        raise web.HTTPBadGateway(text=f"Sonarr is unreachable: {no_credentials(e)}")
    return web.json_response({"items": items})


RUN_ID = re.compile(r"\d{8}-\d{6}-\d{6}-\d+-S\d+E\d+")
# What a diagnostic never shows: a URL's query (tokens, license contexts), long keys and hashes, e-mail addresses
SECRET_LIKE = [(re.compile(r"(https?://[^\s\"'?]+)\?[^\s\"']+"), r"\1?…"), (re.compile(r"[A-Za-z0-9_+/=-]{32,}"), "…"),
               (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "…@…")]


def scrubbed(text: str) -> str:
    text = no_credentials(text)
    for pattern, by in SECRET_LIKE:
        text = pattern.sub(by, text)
    return text


def diagnostic(run_id: str) -> str:
    """One attempt, as an issue needs it: versions, the series' settings and the settings (secrets masked, as the
    page gets them), what was up, then the attempt's card and the end of its log, scrubbed of tokens and keys."""
    card = json.loads((sonarr_sync.RUNS_DIR / f"{run_id}.json").read_text())
    log = sonarr_sync.RUNS_DIR / f"{run_id}.log"
    tail = log.read_text(encoding="utf8", errors="replace").splitlines()[-300:] if log.exists() else []
    config = public_config(read_config())
    show = config["series"].get(card.get("tvdbId")) or config["series"].get(str(card.get("tvdbId"))) or {}
    settings = {k: v for k, v in config["settings"].items() if not k.endswith("_url") and k not in ("proxy_auth_from",)}
    parts = [
        f"# Unshacklarr diagnostic: {card.get('series')} {card.get('sxxeyy')}",
        f"Unshacklarr {__version__} · Unshackle {health['unshackle'].get('version') or '?'} · Python {sys.version.split()[0]}",
        f"Sonarr up: {health['sonarr'].get('ok')} · Unshackle up: {health['unshackle'].get('ok')}",
        "## The series' settings", "```json", json.dumps(show, indent=1, ensure_ascii=False), "```",
        "## Settings", "```json", json.dumps(settings, indent=1, ensure_ascii=False), "```",
        "## The attempt", "```json", json.dumps({k: v for k, v in card.items() if k != "live"}, indent=1, ensure_ascii=False), "```",
        f"## Its log (last {len(tail)} lines)", "```", *tail, "```",
    ]
    return scrubbed("\n".join(parts)) + "\n"


async def run_diagnostic(request):
    run_id = request.match_info["run_id"]
    if not RUN_ID.fullmatch(run_id) or not (sonarr_sync.RUNS_DIR / f"{run_id}.json").exists():
        raise web.HTTPNotFound(text="No such attempt")
    text = await asyncio.to_thread(diagnostic, run_id)
    return web.Response(text=text, content_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="unshacklarr-diagnostic-{run_id}.md"'})


cards_read: tuple[int, list[dict]] = (0, [])


def cards_on_disk() -> list[dict]:
    """Every card in the history, newest first. Read again only once the folder changed (each card is
    written whole and renamed in, which changes it), and always within 2 s of a change: a coarse clock
    may not tell two saves apart. The page and the job terminal ask for them every few seconds."""
    global cards_read
    stamp = sonarr_sync.RUNS_DIR.stat().st_mtime_ns
    if stamp != cards_read[0] or time.time_ns() - stamp < 2_000_000_000:
        cards = []
        for path in sorted(sonarr_sync.RUNS_DIR.glob("*.json"), reverse=True):
            try:
                cards.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue  # gone meanwhile
        cards_read = (stamp, cards)
    return cards_read[1]


def run_cards() -> list[dict]:
    """Download attempts, newest first; one without an end that no run follows was cut off."""
    cards = [card if card.get("ended") else {**card, "outcome": "running" if card["id"] in sonarr_sync.EpisodeRun.active else "interrupted"}
             for card in cards_on_disk()]
    return list(sonarr_sync.waiting.values()) + cards  # picked with others, waiting their turn


def stats_of(cards: list[dict], now: datetime) -> dict:
    """What the history adds up to: the last 30 days, 8 weeks of downloads, and each service."""
    def when(c):
        return datetime.fromisoformat(c["started"])
    ended = [c for c in cards if c.get("ended") and c.get("outcome") in ("downloaded", "failed", "kept")]
    recent = [c for c in ended if now - when(c) <= timedelta(days=30)]
    took = lambda c: (datetime.fromisoformat(c["ended"]) - when(c)).total_seconds()
    week0 = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weeks = []
    for i in range(7, -1, -1):
        start = week0 - timedelta(weeks=i)
        these = [c for c in ended if start <= when(c) < start + timedelta(weeks=1)]
        weeks.append({"start": start.date().isoformat(), **{o: sum(c["outcome"] == o for c in these) for o in ("downloaded", "failed", "kept")}})
    services = {}
    for c in ended:
        sv = services.setdefault(c["service"], {"service": c["service"], "downloaded": 0, "failed": 0, "kept": 0, "last_success": None, "took": []})
        sv[c["outcome"]] += 1
        if c["outcome"] == "downloaded":
            sv["took"].append(took(c))
            sv["last_success"] = max(sv["last_success"] or "", c["ended"])
    for sv in services.values():
        tries = sv["downloaded"] + sv["failed"]
        sv["success"] = round(100 * sv["downloaded"] / tries) if tries else None
        sv["average"] = round(sum(sv["took"]) / len(sv["took"])) if sv["took"] else None
        del sv["took"]
    done = [c for c in recent if c["outcome"] == "downloaded"]
    tries = sum(c["outcome"] in ("downloaded", "failed") for c in recent)
    return {
        "month": {"downloaded": len(done), "failed": sum(c["outcome"] == "failed" for c in recent),
                  "kept": sum(c["outcome"] == "kept" for c in recent),
                  "success": round(100 * len(done) / tries) if tries else None,
                  "average": round(sum(map(took, done)) / len(done)) if done else None},
        "weeks": weeks,
        "services": sorted(services.values(), key=lambda v: -(v["downloaded"] + v["failed"] + v["kept"])),
        "since": min((c["started"] for c in ended), default=None),
    }


async def stats(_):
    cards = await asyncio.to_thread(run_cards) if sonarr_sync.RUNS_DIR.exists() else []
    return web.json_response(stats_of(cards, datetime.now(timezone.utc)))


async def busy(_):
    """How many downloads run and wait now: the menu's Activity says so, whatever page is open."""
    return web.json_response({"running": len(sonarr_sync.EpisodeRun.active), "queued": len(sonarr_sync.waiting)})


async def runs(request):
    """The history's cards; ?live=1: only those going on (a series' page asks every few seconds)."""
    cards = await asyncio.to_thread(run_cards) if sonarr_sync.RUNS_DIR.exists() else []
    if request.query.get("live"):
        cards = [c for c in cards if c.get("outcome") == "running"]
    return web.json_response(cards)


async def runs_live(request):
    """The downloads going on now, pushed as they change (server-sent events): Activity's bars move in real time."""
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})  # a reverse proxy must not hold it back
    await response.prepare(request)
    last, quiet = None, 0.0
    try:
        while True:
            live = await asyncio.to_thread(live_cards) if sonarr_sync.EpisodeRun.active else []  # idle: no thread
            data = json.dumps(live)
            if data != last:
                await response.write(f"data: {data}\n\n".encode())
                last, quiet = data, 0.0
            elif quiet >= 15:
                await response.write(b": still here\n\n")  # keeps proxies from closing an idle stream
                quiet = 0.0
            await asyncio.sleep(0.5)
            quiet += 0.5
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    return response


def live_cards() -> list[dict]:
    cards = []
    for run_id in sorted(sonarr_sync.EpisodeRun.active, reverse=True):
        try:
            cards.append({**json.loads((sonarr_sync.RUNS_DIR / f"{run_id}.json").read_text()), "outcome": "running"})
        except (OSError, ValueError):
            continue  # being written: the next tick
    return cards


async def send_log(ws, run_id: str) -> None:
    """One attempt's log down the socket, raw ANSI and all: live while it runs, whole once done."""
    log, card = sonarr_sync.RUNS_DIR / f"{run_id}.log", sonarr_sync.RUNS_DIR / f"{run_id}.json"
    pos = max(0, log.stat().st_size - 3_000_000) if log.exists() else 0  # a long progress log: its end
    while not ws.closed:
        size = log.stat().st_size if log.exists() else 0
        if size > pos:
            with log.open("rb") as f:
                f.seek(pos)
                data = f.read(min(size - pos, 1_000_000))
            pos += len(data)
            await ws.send_bytes(data)
            continue
        try:
            ended = json.loads(card.read_text()).get("ended")
        except (OSError, ValueError):
            ended = None
        if ended or not log.exists() or run_id not in sonarr_sync.EpisodeRun.active:
            return  # all of it sent (an attempt cut off by a restart never ends: its log is all there is)
        await asyncio.sleep(0.25)


BATCH_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")


async def terminal(request):
    """Stream an attempt's log (?run=), or a job's: the logs of the episodes picked together (?batch=),
    one after the other as they come, until the last is done."""
    origin = urlparse(request.headers.get("Origin", "")).netloc
    if origin != request.host:  # other sites open in the browser must not read it
        raise web.HTTPForbidden(text="Cross-origin terminal refused")
    run_id, batch = request.query.get("run", ""), request.query.get("batch", "")
    if not (RUN_ID.fullmatch(run_id) or BATCH_ID.fullmatch(batch)):
        raise web.HTTPBadRequest(text="Unknown download")
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    try:
        if run_id:
            await send_log(ws, run_id)
        sent: set[str] = set()
        while batch and not ws.closed:
            cards = sorted((c for c in await asyncio.to_thread(run_cards) if c.get("batch") == batch and not c.get("waiting") and c["outcome"] != "cancelled"), key=lambda c: c["id"])
            todo = [c for c in cards if c["id"] not in sent]
            if todo:
                c = todo[0]
                await ws.send_bytes(f"\r\n\x1b[1;36m━━━ {c['series']} {c['sxxeyy']} ━━━\x1b[0m\r\n".encode())
                await send_log(ws, c["id"])
                sent.add(c["id"])
            elif any(w.get("batch") == batch for w in list(sonarr_sync.waiting.values())):
                await asyncio.sleep(0.5)  # the next one has not started yet
            else:
                break
        await ws.close()
    except ConnectionResetError:
        # A viewer can disconnect after ws.closed was checked; its job still runs.
        pass
    return ws


async def sync_log(_):
    return web.json_response({"running": running(), "updated": last_sync.isoformat() if last_sync else None})


# ---- Password, session and first-run setup ----

SESSION_COOKIE = "unshackle_session"
SESSION_DAYS = 30
STATIC = {"/apple-touch-icon.png": "image/png", "/icon-192.png": "image/png", "/icon-512.png": "image/png",
          "/manifest.webmanifest": "application/manifest+json", "/sw.js": "text/javascript",  # what a phone fetches to install the app, logged in or not
          "/codemirror.js": "text/javascript", "/unshackle-keys.json": "application/json",  # the unshackle.yaml editor: public code and docs
          "/xterm.js": "text/javascript", "/xterm-fit.js": "text/javascript", "/xterm.css": "text/css"}  # Activity's terminal, served here: no CDN
STATIC |= {f"/i18n/{f.name}": "application/json" for f in (HERE / "static" / "i18n").glob("*.json")}  # the page's languages, the login's too
PAGE_FILES = {"/app.css": "text/css", **{f"/js/{f.name}": "text/javascript" for f in (HERE / "static" / "js").glob("*.js")}}
OPEN_PATHS = {"/", "/health", "/api/session", "/api/login", "/api/logout", *STATIC, *PAGE_FILES}
SETUP_PATHS = {"/api/setup", "/api/setup/found", "/api/setup/backups", "/api/setup/restore", "/api/sonarr/test", "/api/unshackle/test"}  # open only until a password exists
failed_logins: dict[str, list[float]] = {}


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32).hex()
    return f"scrypt${salt}${digest}"


def password_ok(password: str, stored: str) -> bool:
    try:
        salt = stored.split("$")[1]
    except IndexError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def session_value(auth: dict) -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    return f"{expires}.{hmac.new(auth['secret'].encode(), str(expires).encode(), 'sha256').hexdigest()}"


def proxy_user(request) -> str:
    """The user a reverse proxy vouches for: its header, only on a connection that comes from the proxy's own
    address (the peer itself, never X-Real-IP or X-Forwarded-For: anyone writes those). Empty otherwise."""
    header, sources = sonarr_sync.SETTINGS.get("proxy_auth_header"), sonarr_sync.SETTINGS.get("proxy_auth_from")
    if not header or not sources:
        return ""
    try:
        peer = ipaddress.ip_address(request.remote or "")
        networks = [ipaddress.ip_network(s.strip(), strict=False) for s in str(sources).split(",") if s.strip()]
    except ValueError:
        return ""
    if not any(peer in n for n in networks if n.version == peer.version):
        return ""
    return request.headers.get(header, "").strip()[:200]


def logged_in(request, auth: dict) -> bool:
    expires, _, signature = request.cookies.get(SESSION_COOKIE, "").partition(".")
    if not expires.isdigit() or int(expires) < time.time() or not auth.get("secret"):
        return False
    expected = hmac.new(auth["secret"].encode(), expires.encode(), "sha256").hexdigest()
    return hmac.compare_digest(signature, expected) and signature not in read_json(LOGGED_OUT_FILE, {})


LOGGED_OUT_FILE = sonarr_sync.DATA / "logged_out.json"  # sessions closed before their end: {signature: expires}


def forget_session(value: str) -> None:
    """Log out for good: the cookie, copied before, is refused until it would have expired."""
    expires, _, signature = value.partition(".")
    if not expires.isdigit() or not signature:
        return
    closed = {sig: until for sig, until in read_json(LOGGED_OUT_FILE, {}).items() if until > time.time()}  # the expired ones go
    write_atomic(LOGGED_OUT_FILE, json.dumps({**closed, signature: int(expires)}), PRIVATE)


def with_session(request, response, auth: dict):
    https = request.secure or request.headers.get("X-Forwarded-Proto") == "https"
    response.set_cookie(SESSION_COOKIE, session_value(auth), max_age=SESSION_DAYS * 86400,
                        httponly=True, samesite="Strict", secure=https, path="/")
    return response


LOGIN_TRIES = 5  # per address, in 5 minutes
LOGIN_TRIES_ALL = 30  # every address together: rotating X-Real-IP from the LAN gains nothing past it


def recent_failures(ip: str, now: float) -> list[float]:
    """This address's wrong passwords of the last 5 minutes, or a 429 when there were too many, from it or from all."""
    for key in [k for k, times in failed_logins.items() if not any(now - t < 300 for t in times)]:
        failed_logins.pop(key, None)  # forgotten after 5 minutes: the table never grows
    recent = [t for t in failed_logins.get(ip, []) if now - t < 300]
    if len(recent) >= LOGIN_TRIES or sum(now - t < 300 for v in failed_logins.values() for t in v) >= LOGIN_TRIES_ALL:
        raise web.HTTPTooManyRequests(text="Too many wrong passwords: wait 5 minutes")
    return recent


password_slots = asyncio.Semaphore(2)  # scrypt takes 16 MiB and a thread: a burst of tries waits its turn on the loop


async def password_try(request, password: str) -> bool:
    """One password check under login's limits. The try is counted before scrypt runs, so tries sent all at
    once count all the same, and a right password forgives this address's tries."""
    ip, now = client_ip(request), time.time()
    recent_failures(ip, now)  # 429 when there were too many
    failed_logins.setdefault(ip, []).append(now)  # no await since the check: nothing slips in between
    stored = read_config()["auth"].get("password")
    async with password_slots:
        ok = bool(stored) and await asyncio.to_thread(password_ok, password, stored)
    if ok:
        failed_logins.pop(ip, None)
    return ok


def client_ip(request) -> str:
    """Who is trying to log in. X-Real-IP is believed only from a private address, i.e. the
    reverse proxy, which sets it itself; X-Forwarded-For never: its first entry is the client's
    to write, which would let anyone dodge the attempt limit."""
    remote = request.remote or "?"
    try:
        behind_proxy = ipaddress.ip_address(remote).is_private
    except ValueError:
        behind_proxy = False
    return (request.headers.get("X-Real-IP") if behind_proxy else None) or remote


# ---- The API for other programs: /api/v1, with an API key; its answers keep their shape (docs/API.md) ----

API_KEY_HEADER = "X-Api-Key"


def api_key_ok(request, auth: dict) -> bool:
    """The key opens /api/v1 only: the password, cookies, CDM and unshackle.yaml stay behind the login.
    Only its hash is kept, so the config file never holds it."""
    given = request.headers.get(API_KEY_HEADER, "")
    return bool(given and auth.get("api_key") and request.path.startswith("/api/v1/")
                and hmac.compare_digest(hashlib.sha256(given.encode()).hexdigest(), auth["api_key"]))


async def json_object(request) -> dict:
    """The request's JSON body, an object, or a 400 saying so."""
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Send a JSON object")
    return body


async def api_key_action(request):
    """A new key (shown this once, the old one stops working) or none at all. A new one needs the password
    again: a session left open somewhere must not be enough to mint lasting access."""
    if request.match_info["action"] == "new":
        await reauth(request, str((await json_object(request)).get("password") or ""))
    config = read_config()
    for k in ("api_key", "api_key_created"):
        config["auth"].pop(k, None)
    key = None
    if request.match_info["action"] == "new":
        key = secrets.token_urlsafe(32)
        config["auth"].update(api_key=hashlib.sha256(key.encode()).hexdigest(), api_key_created=datetime.now(timezone.utc).isoformat())
    write_config(config)
    return web.json_response({"key": key, "created": config["auth"].get("api_key_created")})


def v1_download(card: dict) -> dict:
    state = "queued" if card.get("waiting") else card.get("outcome") or "running"
    return {"id": card["id"], "series": card["series"], "tvdbId": card["tvdbId"], "episode": card["sxxeyy"],
            "episodeId": card.get("episodeId"), "service": card.get("service"), "state": state,
            "step": card.get("step") if state == "running" else None,
            "question": (card.get("prompt") or {}).get("text") if state == "running" else None,
            "started": card.get("started"), "ended": card.get("ended"), "error": card.get("cause") or None}


async def v1_status(_):
    box = await asyncio.to_thread(sonarr_sync.read_inbox)
    read_at = box.get("read_at") or ""
    return web.json_response({
        "version": __version__,
        **{name: {"ok": health[name].get("ok"), "error": health[name].get("error")} for name in ("sonarr", "unshackle")},
        "sync": {"running": running(), "last": last_sync.isoformat() if last_sync else None},
        "downloads": {"running": len(sonarr_sync.EpisodeRun.active), "queued": len(sonarr_sync.waiting)},
        "unread": sum(1 for i in box["items"] if i["at"] > read_at),
    })


def limit_of(request, default: int = 50) -> int:
    value = request.query.get("limit", str(default))
    if not (value.isascii() and value.isdigit()) or not 1 <= int(value) <= 500:
        raise web.HTTPBadRequest(text="limit must be from 1 to 500")
    return int(value)


async def v1_downloads(request):
    limit = limit_of(request)
    cards = await asyncio.to_thread(run_cards) if sonarr_sync.RUNS_DIR.exists() else list(sonarr_sync.waiting.values())
    return web.json_response([v1_download(c) for c in cards[:limit]])


def episode_ids_of(tvdb: int, episodes: list[str]) -> list[int]:
    """Sonarr's ids for a series' episodes named S01E02."""
    series = sonarr_sync.sonarr_get("series", tvdbId=tvdb)
    if not series:
        raise web.HTTPNotFound(text=f"Sonarr has no series with TVDB id {tvdb}")
    known = {f"S{e['seasonNumber']:02}E{e['episodeNumber']:02}": e["id"] for e in sonarr_sync.sonarr_get("episode", seriesId=series[0]["id"])}
    missing = [x for x in episodes if x not in known]
    if missing:
        raise web.HTTPNotFound(text=f"Sonarr has no {', '.join(missing)} for this series")
    return [known[x] for x in episodes]


async def v1_start_download(request):
    """Episodes by Sonarr's ids, or by the series' TVDB id and S01E02: downloaded as the page's Download does,
    a file Sonarr has replaced only by a better one."""
    body = await json_object(request)
    ids, tvdb = body.get("episodeIds"), body.get("tvdbId")
    names = [str(x).upper() for x in body.get("episodes") or []] if isinstance(body.get("episodes"), list) else []
    if type(tvdb) is int and names and all(re.fullmatch(r"S\d+E\d+", x) for x in names):
        if not (read_config()["series"].get(tvdb) or {}).get("service"):
            raise web.HTTPNotFound(text=f"Unshacklarr downloads no series with TVDB id {tvdb}: set it up on its page first")
        names = [f"S{int(m[1]):02}E{int(m[2]):02}" for m in (re.fullmatch(r"S(\d+)E(\d+)", x) for x in names)]
        try:
            ids = await asyncio.to_thread(episode_ids_of, tvdb, names)
        except requests.RequestException as e:
            raise web.HTTPBadGateway(text=f"Sonarr is unreachable: {no_credentials(e)}")
    if not ids or not isinstance(ids, list) or not all(type(i) is int and i > 0 for i in ids):
        raise web.HTTPBadRequest(text='Give "episodeIds", or "tvdbId" and "episodes" such as ["S01E02"]')
    if len(ids) > 100:
        raise web.HTTPBadRequest(text="At most 100 episodes at a time")
    if why := await asyncio.to_thread(cdm_refusal, ids):
        raise web.HTTPBadRequest(text=why)
    room_for_one_more()
    # never "replace": a key kept in another program must not be able to swap the library's files
    run_sync(ids)
    return web.json_response({"queued": len(ids)})


async def v1_stop(request):
    """Stop a download: one running is cancelled, one still queued leaves the queue."""
    if m := re.fullmatch(r"waiting-(\d+)", request.match_info["run_id"]):
        card = sonarr_sync.waiting.pop(int(m[1]), None)
        if card is None:
            raise web.HTTPConflict(text="This download has already started")
        sonarr_sync.unqueued.add(int(m[1]))
        await asyncio.to_thread(sonarr_sync.cancel_queued, card)
        return web.json_response({"stopping": True})
    return await stop_run(request)


async def v1_notifications(request):
    limit = limit_of(request)
    box = await asyncio.to_thread(sonarr_sync.read_inbox)
    read_at = box.get("read_at") or ""
    return web.json_response({"unread": sum(1 for i in box["items"] if i["at"] > read_at), "items": [
        {k: i.get(k) for k in ("id", "at", "level", "title", "message")} | {"unread": i["at"] > read_at} for i in box["items"][:limit]]})


@web.middleware
async def password_required(request, handler):
    auth = read_config()["auth"]
    if request.path in OPEN_PATHS or (request.path in SETUP_PATHS and not auth.get("password")):
        return await handler(request)
    if not auth.get("password"):
        raise web.HTTPForbidden(text="Set Unshackle up first")
    if not (logged_in(request, auth) or proxy_user(request) or api_key_ok(request, auth)):
        raise web.HTTPUnauthorized(text="Log in first")
    return await handler(request)


async def session(request):
    """What the page must show first: the setup, the login, or the app."""
    config = read_config()
    auth, settings = config["auth"], sonarr_sync.load_settings(config)
    by_proxy = proxy_user(request) if auth.get("password") else ""  # the setup comes first, whatever the proxy says
    body = {"configured": bool(auth.get("password")), "logged_in": logged_in(request, auth) or bool(by_proxy)}
    if by_proxy:
        body["proxy_user"] = by_proxy
    if body["logged_in"]:  # for Settings, Account: until when, and since when the password is the same
        body["expires"] = int(request.cookies[SESSION_COOKIE].partition(".")[0]) if logged_in(request, auth) else None
        body["password_changed"] = auth.get("changed")
        body["api_key"] = {"set": bool(auth.get("api_key")), "created": auth.get("api_key_created")}
    if not body["configured"]:  # what the setup starts from; addresses and paths only with the setup code
        body["setup"] = {"country": settings["country"], "timezone": settings["timezone"], "unshackle_mode": UNSHACKLE.mode}
    return web.json_response(body)


async def setup_found(request):
    """What the environment already says for the setup (a key only as "found"), once the setup code is
    given: before, anyone who reaches a fresh install would learn its addresses and folders."""
    body = await json_object(request)
    if read_config()["auth"].get("password"):
        raise web.HTTPForbidden(text="Unshacklarr is set up already")
    if not trusted(request, body):
        raise web.HTTPForbidden(text="Wrong setup code: it is in Unshacklarr's log")
    settings = sonarr_sync.load_settings(read_config())
    return web.json_response({
        **{k: settings[k] for k in ("sonarr_url", "sonarr_downloads", "unshackle_url", "unshackle_command", "downloads", "unshackle_downloads")},
        "sonarr_api_key_set": bool(settings["sonarr_api_key"]),
        "unshackle_api_key_set": bool(settings["unshackle_api_key"]),
    })


# What's new: the new options a person has seen (their "New" badges gone), for the account, on every device.
# A new install sees nothing as new: its options are all new to it, so none is pointed at.
NEWS_FILE = sonarr_sync.DATA / "news_seen.json"  # {"seen": [ids], "installed": the version set up with, "list_read": a version}
NEWS_ID = re.compile(r"[a-z0-9-]{1,40}")


def news_installed() -> None:
    write_atomic(NEWS_FILE, json.dumps({**read_json(NEWS_FILE, {}), "installed": __version__}))


async def news_seen(request):
    """Mark news items seen ({"ids": [...]}), or the What's new list read ({"list": true})."""
    body = await json_object(request)
    ids = [i for i in body.get("ids") or [] if isinstance(i, str) and NEWS_ID.fullmatch(i)][:50]
    news = read_json(NEWS_FILE, {})
    news["seen"] = sorted(set(news.get("seen") or []) | set(ids))
    if body.get("list") is True:
        news["list_read"] = __version__
    write_atomic(NEWS_FILE, json.dumps(news))
    return web.json_response(news)


# The release notes, from the CHANGELOG shipped in the package (the repository's root when run from a checkout)
CHANGELOG_FILES = (Path(__file__).with_name("CHANGELOG.md"), Path(__file__).parent.parent / "CHANGELOG.md")
CHANGE_KINDS = {"added": "new", "changed": "improved", "fixed": "fix", "security": "security"}


def changelog() -> list[dict]:
    """[{"version", "date", "changes": [{"kind": new|improved|fix|security|note, "text": markdown}]}], newest first."""
    path = next((p for p in CHANGELOG_FILES if p.is_file()), None)
    releases, kind, cur = [], "note", None
    for line in path.read_text(encoding="utf-8").splitlines() if path else []:
        if m := re.match(r"## \[([^\]]+)\](?: - (\S+))?", line):
            releases.append({"version": m[1], "date": m[2], "changes": []})
            kind, cur = "note", None
        elif not releases or line.startswith("[") and "]: " in line:
            continue
        elif line.startswith("### "):
            kind, cur = CHANGE_KINDS.get(line[4:].strip().lower(), "note"), None
        elif not line.strip() or re.fullmatch(r"\*\*[^*]+\*\*", line.strip()):  # a gap, or a group's name
            cur = None
        elif line.startswith("- "):
            cur = {"kind": kind, "text": line[2:].strip()}
            releases[-1]["changes"].append(cur)
        elif cur:  # the next line of a change
            cur["text"] += " " + line.strip()
        else:  # a paragraph of its own: a note
            cur = {"kind": "note", "text": line.strip()}
            releases[-1]["changes"].append(cur)
    return [r for r in releases if r["changes"]]


async def release_notes(request):
    return web.json_response({"installed": __version__, "releases": changelog()})


async def setup_backups(request):
    """The setup of a new install: the backups found in its data folder, once the setup code is given."""
    body = await json_object(request)
    if read_config()["auth"].get("password"):
        raise web.HTTPForbidden(text="Unshacklarr is set up already")
    if not trusted(request, body):
        raise web.HTTPForbidden(text="Wrong setup code: it is in Unshacklarr's log")
    return web.json_response({"saved": backups_info()["saved"]})


async def setup_restore(request):
    """The setup of a new install from a backup (one found in backups/, or a file): the setup code, a new password
    (a backup never holds one), then the backup's settings. Sonarr and Unshackle are tested with the addresses it
    brings, and said how it went, but the restore is done either way: in a rebuild, one may not be back yet."""
    config = read_config()
    if config["auth"].get("password"):
        raise web.HTTPForbidden(text="Already set up")
    body = await json_object(request)
    if not hmac.compare_digest(str(body.get("setup_code") or "").strip(), SETUP_CODE):
        raise web.HTTPForbidden(text="Wrong setup code: Unshacklarr prints it when it starts (docker logs unshacklarr)")
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise web.HTTPBadRequest(text="Password must be at least 8 characters")
    text = saved_backup(str(body["name"])).read_text(encoding="utf8") if body.get("name") else str(body.get("text") or "")
    data = await asyncio.to_thread(backup_data, text, str(body.get("passphrase") or ""))
    auth = {"password": hash_password(password), "secret": secrets.token_hex(32), "changed": datetime.now(timezone.utc).isoformat()}
    write_config({**{k: data[k] for k in BACKUP_KEYS if data.get(k) is not None}, "auth": auth})
    news_installed()
    settings = sonarr_sync.load_settings(read_config())
    checks = {}
    try:
        await asyncio.to_thread(sonarr_status, settings["sonarr_url"], settings["sonarr_api_key"])
        checks["sonarr"] = {"ok": True}
    except (requests.RequestException, ValueError) as e:
        checks["sonarr"] = {"ok": False, "error": f"Sonarr is unreachable: {no_credentials(e)}"}
    try:
        await asyncio.to_thread(check_unshackle, settings)
        checks["unshackle"] = {"ok": True}
    except UnshackleError as e:
        checks["unshackle"] = {"ok": False, "error": str(e)}
    return with_session(request, web.json_response({"logged_in": True, "series": len(data.get("series") or {}), **checks}), auth)


async def login(request):
    password = str((await json_object(request)).get("password") or "")
    if not await password_try(request, password):
        raise web.HTTPUnauthorized(text="Wrong password")
    return with_session(request, web.json_response({"logged_in": True}), read_config()["auth"])


async def logout(request):
    if logged_in(request, read_config()["auth"]):
        await asyncio.to_thread(forget_session, request.cookies[SESSION_COOKIE])
    response = web.json_response({"logged_in": False})
    response.del_cookie(SESSION_COOKIE, path="/")
    return response


def sonarr_status(url: str, key: str) -> str:
    r = requests.get(f"{url.rstrip('/')}/api/v3/system/status", headers={"X-Api-Key": key}, timeout=10)
    if r.status_code == 401:
        raise ValueError("Sonarr refused the API key")
    r.raise_for_status()
    return r.json().get("version", "?")


def stored_key_for(url: str, what: str = "sonarr") -> str:
    """The saved (or environment) key of Sonarr or unshackle serve, but only for the address it
    belongs to: a key is never sent to a URL someone typed, or the first request to a test
    could carry it off to any server."""
    settings = sonarr_sync.load_settings()
    saved = str(settings[f"{what}_url"]).strip().rstrip("/")
    if saved and url.strip().rstrip("/") == saved:
        return settings[f"{what}_api_key"]
    others = settings["backends"] if what == "unshackle" else settings.get("sonarrs") or []
    other = next((b for b in others if b["url"] == url.strip().rstrip("/")), {})
    return other.get("api_key") or ""


def trusted(request, body: dict) -> bool:
    return logged_in(request, read_config()["auth"]) or hmac.compare_digest(str(body.get("setup_code") or "").strip(), SETUP_CODE)


async def test_sonarr(request):
    body = await json_object(request)
    if not trusted(request, body):  # before the setup, no stranger makes this server fetch URLs
        raise web.HTTPForbidden(text="Enter the setup code first")
    url = str(body.get("sonarr_url") or "").strip()
    if not URL.fullmatch(url.rstrip("/")):
        raise web.HTTPBadRequest(text="The address must start with http:// or https://")
    key = str(body.get("sonarr_api_key") or "").strip() or stored_key_for(url)
    if not key:
        raise web.HTTPBadRequest(text="Enter the API key to test this Sonarr")
    try:
        version = await asyncio.to_thread(sonarr_status, url, key)
    except (requests.RequestException, ValueError) as e:
        raise web.HTTPBadGateway(text=f"Could not reach Sonarr: {e}")
    return web.json_response({"version": version})


def check_unshackle(settings: dict) -> int:
    """Reach Unshackle with these settings (starting a local serve if need be): its service
    count, its EXAMPLE left out."""
    from unshacklarr.backend import Unshackle
    probe = UNSHACKLE if settings.get("unshackle_mode", "local") != "remote" else Unshackle(sonarr_sync.DATA)
    if probe is UNSHACKLE and sonarr_sync.EpisodeRun.active and \
            settings.get("unshackle_command", "") != str(sonarr_sync.SETTINGS.get("unshackle_command") or ""):
        raise UnshackleError("Downloads are using the current command: test another one when they are done")
    probe.configure({**sonarr_sync.SETTINGS, **settings})
    try:
        return sum(s["tag"] != "EXAMPLE" for s in probe.services())
    finally:
        if probe is UNSHACKLE:
            UNSHACKLE.configure(sonarr_sync.SETTINGS)  # the saved settings again, until a save


async def test_unshackle(request):
    body = await json_object(request)
    if not trusted(request, body):  # a local test starts a program: never for a stranger before the setup
        raise web.HTTPForbidden(text="Enter the setup code first")
    settings = {k: str(body.get(k) or "").strip() for k in ("unshackle_mode", "unshackle_command", "unshackle_url", "unshackle_api_key")}
    if settings["unshackle_mode"] == "remote":
        if not URL.fullmatch(settings["unshackle_url"]):
            raise web.HTTPBadRequest(text="Enter the address of unshackle serve, like http://unshackle:8786")
        if not settings["unshackle_api_key"] and trusted(request, body):
            settings["unshackle_api_key"] = stored_key_for(settings["unshackle_url"], "unshackle")
    try:
        count = await asyncio.to_thread(check_unshackle, settings)
    except UnshackleError as e:
        raise web.HTTPBadGateway(text=str(e))
    if not count:
        raise web.HTTPBadGateway(text="unshackle answers, but has no services: add yours to its services folder")
    return web.json_response({"services": count})


# First-run setup needs this code, so whoever finds the page before its owner cannot claim
# it. From SETUP_TOKEN, else made at start and printed to the container's log.
SETUP_CODE = os.environ.get("SETUP_TOKEN") or secrets.token_urlsafe(9)


def announce_setup_code() -> None:
    if not read_config()["auth"].get("password") and not os.environ.get("SETUP_TOKEN"):
        print(f"Unshacklarr is not set up yet. Setup code: {SETUP_CODE}", flush=True)


async def setup(request):
    """First run: the password, Unshackle and Sonarr, in one go."""
    config = read_config()
    if config["auth"].get("password"):
        raise web.HTTPForbidden(text="Already set up")
    body = await json_object(request)
    if not hmac.compare_digest(str(body.get("setup_code") or "").strip(), SETUP_CODE):
        raise web.HTTPForbidden(text="Wrong setup code: Unshacklarr prints it when it starts (docker logs unshacklarr)")
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise web.HTTPBadRequest(text="Password must be at least 8 characters")
    typed = {k: str(body.get(k) or "").strip() for k in ("sonarr_api_key", "unshackle_api_key")}
    settings = check_settings({**sonarr_sync.load_settings(config), **body, **typed}, {})
    settings["sonarr_api_key"] = typed["sonarr_api_key"] or stored_key_for(settings["sonarr_url"])
    settings["unshackle_api_key"] = typed["unshackle_api_key"] or stored_key_for(settings["unshackle_url"], "unshackle")
    if not settings["sonarr_api_key"]:
        raise web.HTTPBadRequest(text="Enter Sonarr's API key")
    try:
        await asyncio.to_thread(check_unshackle, settings)
    except UnshackleError as e:
        raise web.HTTPBadRequest(text=f"Could not reach Unshackle with these settings: {e}")
    try:
        await asyncio.to_thread(sonarr_status, settings["sonarr_url"], settings["sonarr_api_key"])
    except (requests.RequestException, ValueError) as e:
        raise web.HTTPBadRequest(text=f"Could not reach Sonarr with these settings: {e}")
    if not settings["sonarr_downloads"]:
        raise web.HTTPBadRequest(text="Enter the downloads folder as Sonarr sees it")
    config["settings"] = settings
    config["auth"] = {"password": hash_password(password), "secret": secrets.token_hex(32), "changed": datetime.now(timezone.utc).isoformat()}
    write_config(config)
    news_installed()  # a new install: nothing is "new" to it
    return with_session(request, web.json_response({"logged_in": True}), config["auth"])


async def set_language(request):
    """The page's language, chosen in a browser, for the notifications too."""
    lang = str((await json_object(request)).get("language") or "")
    if lang not in i18n.languages():
        raise web.HTTPBadRequest(text=f"No translation for {lang!r}")
    config = read_config()
    config.setdefault("settings", {})["language"] = lang
    write_config(config)
    return web.json_response({"ok": True})


async def change_password(request):
    body = await json_object(request)
    try:
        await reauth(request, str(body.get("current") or ""))  # login's limit on tries
    except web.HTTPForbidden:
        raise web.HTTPForbidden(text="The current password is wrong") from None  # not 401: the page reads that as a lost session
    config = read_config()
    new = str(body.get("new") or "")
    if len(new) < 8:
        raise web.HTTPBadRequest(text="Password must be at least 8 characters")
    # A new secret too: every other open session is logged out. The API key stays.
    config["auth"].update(password=hash_password(new), secret=secrets.token_hex(32), changed=datetime.now(timezone.utc).isoformat())
    write_config(config)
    await asyncio.to_thread(sonarr_sync.PUSH.unsubscribe_all)  # a device added by someone else stops listening too
    return with_session(request, web.json_response({"ok": True}), config["auth"])


async def logout_others(request):
    """A new session secret, the password kept: every device but this one logs in again."""
    config = read_config()
    config["auth"] = {**config["auth"], "secret": secrets.token_hex(32)}
    write_config(config)
    await asyncio.to_thread(sonarr_sync.PUSH.unsubscribe_all)  # their push notifications too: turn them on again on yours
    return with_session(request, web.json_response({"ok": True}), config["auth"])


COMPRESSED = re.compile(r"text/|application/(json|javascript|manifest\+json)|image/svg")


@web.middleware
async def compress(request, handler):
    """Text answers gzipped (or deflated) when the browser takes it: the page's files and the JSON shrink 4 to 20
    times. Streams (the live feed, the terminal) are left as they are."""
    response = await handler(request)
    if (isinstance(response, (web.Response, web.FileResponse)) and not response.prepared
            and COMPRESSED.match(response.content_type or "") and "Content-Encoding" not in response.headers):
        response.enable_compression()
    return response


@web.middleware
async def same_origin_only(request, handler):
    # A custom header cannot be sent cross-site without a CORS preflight, which this
    # server never grants: other pages open in the browser cannot change anything. X-Api-Key is one too.
    if request.method not in ("GET", "HEAD") and request.headers.get("X-Unshackle") != "1" and API_KEY_HEADER not in request.headers:
        raise web.HTTPForbidden(text="Missing X-Unshackle header")
    return await handler(request)


async def stop_unshackle(app):
    yield
    await asyncio.to_thread(UNSHACKLE.stop)  # a local serve goes with us


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",  # never inside another site's frame: no clickjacking
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


async def security_headers(request, response):
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)


def background(loop):
    """A loop that runs for as long as the server does, as aiohttp's cleanup context."""
    async def context(app):
        task = asyncio.create_task(loop())
        yield
        task.cancel()
    return context


app = web.Application(middlewares=[compress, same_origin_only, password_required])
app.on_response_prepare.append(security_headers)
app.cleanup_ctx.append(background(watch_releases))
app.cleanup_ctx.append(background(run_automatic_syncs))
app.cleanup_ctx.append(stop_unshackle)
app.cleanup_ctx.append(background(watch_health))
app.cleanup_ctx.append(background(watch_alerts))
app.cleanup_ctx.append(background(watch_prechecks))
app.cleanup_ctx.append(background(watch_upgrades))
app.cleanup_ctx.append(background(watch_updates))
app.cleanup_ctx.append(background(watch_backups))
app.add_routes([
    web.get("/", index),
    *[web.get(path, static_file) for path in STATIC],
    *[web.get(path, page_file) for path in PAGE_FILES],
    web.get("/api/state", state),
    web.get("/api/services/{tag}", service_options),
    web.get(r"/api/suggest/{tmdb_id:\d+}", suggest),
    web.put("/api/config", save_config),
    web.post("/api/hidden", hide_series),
    web.post("/api/notifications/test", test_notification),
    web.get("/api/notifications/sent", sent_notifications),
    web.post("/api/sync", start_sync),
    web.get(r"/api/series/{series_id:\d+}/episodes", episodes),
    web.post("/api/download", download),
    web.get("/api/missing", missing),
    web.get("/api/upgrades", upgrades),
    web.get("/api/upgrades/groups", upgrade_groups),
    web.post(r"/api/upgrades/{action:check|stop}", upgrades_action),
    web.post("/api/probe", probe),
    web.get(r"/api/probe/{tvdb:\d+}", service_list),
    web.get("/api/push/key", push_key),
    web.post("/api/push/subscribe", push_subscribe),
    web.post("/api/push/unsubscribe", push_unsubscribe),
    web.post("/api/push/test", push_test),
    web.get("/api/leftovers", list_leftovers),
    web.post(r"/api/leftovers/{action:import|delete}", leftover_action),
    web.get(r"/api/series/{tvdb:\d+}/release", release_seen),
    web.get("/api/log", sync_log),
    web.get("/api/terminal", terminal),
    web.get("/api/runs", runs),
    web.get("/api/busy", busy),
    web.get("/api/runs/live", runs_live),
    web.get("/api/stats", stats),
    web.get("/api/networks", networks),
    web.get("/api/schedule", schedule),
    web.get("/api/calendar", calendar),
    web.get("/health", health_check),
    web.get("/api/session", session),
    web.post("/api/login", login),
    web.post("/api/logout", logout),
    web.post("/api/setup", setup),
    web.post("/api/setup/found", setup_found),
    web.post("/api/sonarr/test", test_sonarr),
    web.post("/api/unshackle/test", test_unshackle),
    web.get("/api/status", status),
    web.post("/api/unshackle/restart", restart_unshackle),
    web.get("/api/unshackle/log", unshackle_log),
    web.post(r"/api/unshackle/maintenance/{action}", unshackle_maintenance),
    web.post(r"/api/unshackle/jobs/{job_id}/cancel", cancel_job),
    web.get("/api/cookies", list_cookies),
    web.get("/api/profiles/{service}", service_profiles),
    web.post("/api/series/search", series_search),
    web.post("/api/backup", backup),
    web.post("/api/restore", restore),
    web.post("/api/backups/download", backup_download),
    web.post("/api/backup/offsite", backup_offsite),
    web.post("/api/setup/backups", setup_backups),
    web.post("/api/setup/restore", setup_restore),
    web.post("/api/news/seen", news_seen),
    web.get("/api/changelog", release_notes),
    web.post("/api/cookies", save_cookies),
    web.post("/api/cookies/delete", delete_cookies),
    web.post(r"/api/unshackle/config-file/{action:open|version|save}", unshackle_yaml),
    web.get("/api/cdm", list_cdm),
    web.post(r"/api/cdm/{action:add|delete|choose|remote-save|remote-delete}", cdm_action),
    web.post("/api/cdm/test", cdm_test),
    web.get("/api/inbox", inbox),
    web.post(r"/api/inbox/{action:read|clear}", inbox_action),
    web.post("/api/cdm/reprovision", cdm_reprovision),
    web.post(r"/api/runs/{run_id}/stop", stop_run),
    web.post(r"/api/jobs/{batch}/stop", stop_job),
    web.post(r"/api/jobs/{batch}/{action:pause|resume}", pause_job),
    web.post(r"/api/queue/{episode_id:\d+}/remove", unqueue),
    web.post(r"/api/queue/{episode_id:\d+}/add", requeue),
    web.delete(r"/api/runs/{run_id}", delete_run),
    web.get(r"/api/runs/{run_id}/diagnostic", run_diagnostic),
    web.post(r"/api/runs/{run_id}/input", answer_run),
    web.post("/api/runs/clear", clear_runs),
    web.post("/api/password", change_password),
    web.post("/api/logout-others", logout_others),
    web.post("/api/language", set_language),
    web.post(r"/api/api-key/{action:new|delete}", api_key_action),
    web.get("/api/v1/status", v1_status),
    web.post("/api/v1/sync", start_sync),
    web.get("/api/v1/downloads", v1_downloads),
    web.post("/api/v1/downloads", v1_start_download),
    web.post("/api/v1/downloads/{run_id}/stop", v1_stop),
    web.post("/api/v1/downloads/{run_id}/answer", answer_run),
    web.get("/api/v1/notifications", v1_notifications),
])

def reset_password(password: str) -> None:
    """A new password, from the command line: the lost one cannot be read back, only replaced.
    Every open session is logged out; settings and series stay as they are."""
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    config = read_config()
    config["auth"].update(password=hash_password(password), secret=secrets.token_hex(32), changed=datetime.now(timezone.utc).isoformat())
    write_config(config)
    sonarr_sync.PUSH.unsubscribe_all()


def main() -> None:
    if sys.argv[1:] == ["reset-password"]:  # unshacklarr reset-password (docker exec -it unshacklarr …)
        import getpass
        new = getpass.getpass("New password: ")
        if new != getpass.getpass("Again: "):
            sys.exit("The two passwords differ; nothing changed.")
        try:
            reset_password(new)
        except ValueError as e:
            sys.exit(f"{e}; nothing changed.")
        print("Password changed. Log in again on every device.")
        return
    announce_setup_code()
    print(f"Unshacklarr keeps its settings in {sonarr_sync.DATA}", flush=True)
    web.run_app(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8788")), print=None)


if __name__ == "__main__":
    main()
