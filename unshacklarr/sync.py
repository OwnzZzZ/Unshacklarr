"""Download Sonarr's missing episodes with Unshackle, then have Sonarr import them.

config.yaml, written by the web page or by hand, maps a series' TVDB id (shown in
Sonarr) to what Unshackle needs. Options are keyed by their long flag, as on Unshackle's
command line; a flag set to true is on, false or empty is left out:

    defaults:                    # Unshackle options for every series
      --quality: "1080"
    service_defaults:            # then per service, before the series' own
      ATV: {options: {--profile: alt}, service_options: {}, backend: vpn, ladder: 1080p}  # its serve, its ladder
    series:
      123456:
        service: M6
        title: https://www.m6.fr/...
        options: {--a-lang: fr}  # override the defaults for this series
        service_options: {}      # options of the service itself
        season_map: {34: 29}     # Sonarr season -> the service's numbering of it
        episode_offset: -1       # added to Sonarr's episode number on the service
        season_offset: 3         # added to Sonarr's season number on the service,
        season_offset_from: 8    # from this season on (default 1); season_map wins over it
        episode_map:             # exceptions, ahead of both: a part can be picked with .N
          S34E06: S29E05.2
        file_name: Koh-Lanta     # show name in file names and MKV titles; defaults to Sonarr's
        since: 2026-09-24T10:00:00+00:00  # set by the web page when the series gets a service
        release_time: "23:30"    # the service publishes then (local time): the web page tries
        broadcast:               # its own airing dates, in place of Sonarr's (a channel ahead of TVDB):
          from: S01E01           #   the first episode it dates, then every one after it
          start: 2026-09-30      #   that episode's evening,
          time: "20:39"          #   at that time (local),
          every: weekly          #   then weekly on `days` (0 Monday … 6 Sunday), or daily,
          days: [1]
          per_evening: 1         #   so many episodes an evening,
          evenings: {2026-10-07: 2, 2026-10-21: 0}  # but so many on these (0: no broadcast)
        release_day: 0           # from then on, every 30 s for 10 min; 1 = the day after airing,
                                 # -3 = three days before it airs (a platform ahead of the channel)
        join_parts: true         # join an episode's parts into one file (default)
        parts: 2                 # each episode comes in 2 parts: wait for all of them (default: any)
        episode_name: joined     # drop it from file names: keep, always, or joined (default)
        ladder: 1080p            # its quality ladder (off: none); else its service's, else the settings'
        download_only: true      # never imported: it waits for an import by hand; else the settings'
    quality_ladders:             # steps tried in order against the episode's tracks (BUILTIN_LADDERS until set)
      - {name: 1080p, steps: [{codec: AVC, range: SDR, min: 1080, max: 1080}]}
    notifications:
      urls: [discord://…, tgram://…]  # Apprise URLs: https://github.com/caronc/apprise#supported-notifications
      success: true              # episode downloaded and handed to Sonarr
      warning: true              # still unavailable a while after airing (once per episode)
      error: true                # Unshackle or Sonarr failed

The automatic sync only goes after new episodes: aired since the series was handed to
Unshackle (`since`), within AUTO_DAYS of airing. Older ones are downloaded by hand,
from the series' episode list in the web page.

Each episode downloads into its own folder of the downloads folder, named after the
TVDB id and SxxEyy. That folder only survives when a file landed in it, so it doubles as
the "already downloaded" marker: an episode Sonarr has not imported yet is not
downloaded twice, and one the service does not offer yet is retried next run.

Before Sonarr imports it, an episode the service split into parts (Unshackle names
them SxxEyy.Part.N) is joined back into one file, and the file and its MKV title get
Sonarr's show name and numbering. The import names the series and episode by id, so
it works whatever the service calls the show.
"""

import contextlib
import copy
import json
import queue
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import apprise
import requests
import yaml

from unshacklarr import i18n, options
from unshacklarr.backend import TERMINAL, Unshackle, UnshackleError
from unshacklarr.files import no_credentials, read_json, write_atomic
from unshacklarr.push import Push


def default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "Unshacklarr"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Unshacklarr"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "unshacklarr"


# Everything Unshacklarr keeps: config.yaml, the download history, caches.
DATA = Path(os.environ.get("UNSHACKLARR_DATA") or default_data_dir())
CONFIG_FILE = DATA / "config.yaml"
WARNED_FILE = DATA / "warned.json"
SEEN_FILE = DATA / "availability.json"  # per episode: last try it was not out yet, first try it was
RUNS_DIR = DATA / "runs"
INBOX_FILE = DATA / "inbox.json"  # every notification, for the page's bell: {"read_at": iso, "items": [...]}
INBOX_KEEP, INBOX_DAYS = 200, 60
inbox_lock = threading.Lock()
SENT_FILE = DATA / "notifications_sent.json"  # the last messages sent out, and whether each address took them
HELD_FILE = DATA / "notifications_held.json"  # what came in the quiet hours, sent together once they end
SENT_KEEP = 20
sent_lock = threading.Lock()

# Everything a user sets, from the web page's Settings (the `settings:` section of
# config.yaml). The environment fills in only what that section leaves empty: a Docker
# setup can start from it.
SETTINGS_DEFAULTS = {
    "unshackle_mode": "",        # local: start unshackle serve here; remote: use one at unshackle_url
    "unshackle_command": "",     # local: the Unshackle command, when it is not on the PATH
    "unshackle_url": "",
    "unshackle_api_key": "",
    "downloads": "",             # the downloads folder, as Unshacklarr sees it
    "unshackle_downloads": "",   # the same folder, as unshackle serve sees it (empty: the same path)
    "cookies_dir": "",           # Unshackle's Cookies folder, as Unshacklarr sees it (local: asked to unshackle)
    "unshackle_config_dir": "",  # the folder of unshackle.yaml, WVDs and PRDs, as Unshacklarr sees it (local: asked)
    "sonarr_url": "",
    "sonarr_public_url": "",     # Sonarr in the browser, for links (empty: sonarr_url, when a browser can reach it)
    "sonarr_api_key": "",
    "sonarr_downloads": "",      # the same folder, as Sonarr sees it (empty: the same path)
    "country": "US",             # where you watch: Schedule shows its services
    "timezone": "UTC",           # release times and the sync clock
    "language": "en",            # what notifications are written in (the page has its own, per browser)
    "tmdb_api_key": "",
    "sync_every_hours": 2,
    "auto_days": 14,             # the automatic sync gives up on an episode this long after airing
    "late_warning_hours": 24,    # a warning when an episode is still missing this long after airing; 0: never
    "burst_every_seconds": 30,   # at a series' release time, a try every…
    "burst_minutes": 10,         # … for this long
    "leftovers_days": 14,        # what waits in the downloads folder goes after this long (0: never)
    "history_keep": 300,
    "history_days": 60,
    "backup_every_days": 0,      # the settings saved in the data folder's backups/ this often, in days (0: never)
    "backup_keep": 14,           # the newest so many automatic backups kept, older ones deleted
    "backup_remote": "",         # each automatic backup also sent, encrypted, to: "webdav" or "s3" ("": nowhere)
    "backup_remote_url": "",     # the WebDAV folder, or the S3 endpoint (https://s3.eu-west-1.amazonaws.com)
    "backup_remote_user": "",    # the WebDAV user name, or the S3 access key
    "backup_remote_secret": "",  # the WebDAV password, or the S3 secret key
    "backup_remote_bucket": "",  # S3: the bucket, a folder in it allowed ("backups/unshacklarr")
    "backup_remote_region": "",  # S3: its region (empty: us-east-1; Cloudflare R2: auto)
    "backup_passphrase": "",     # what encrypts the backups sent away, and decrypts them for a restore
    "proxy_auth_header": "",     # a reverse proxy's login: this header names the user (Remote-User), believed only
    "proxy_auth_from": "",       # from these addresses (172.18.0.0/16, 10.0.0.5): the proxy's own; both, or none
    "min_free_gb": 0,            # nothing is downloaded while the downloads folder has less room (GB); 0: never checked
    "audio_accept": "",          # a download must have one of these audio languages ("fr, en"); empty: the first asked for
    "audio_prefer": "",          # the audio language to upgrade to: an episode without it is got again once it comes
    "upgrade_days": 30,          # for so many days after its download, checked once a day
    "upgrade_mode": "redownload",  # once the preferred audio comes: download the episode again, or add_track (its audio only, added)
    "library_sonarr_root": "",   # add_track reads the library's file: its folder as Sonarr sees it (/tv)…
    "library_local_root": "",    # …and as Unshacklarr sees it (/mnt/tv); both empty: the same path
    "release_learn": False,      # set a series' release time from when its episodes come out, once it is clear
    "download_from": "",         # the automatic sync downloads only from…
    "download_to": "",           # …to (local time, "01:00" to "07:00"); empty: any time
    "download_window_bursts": False,  # release-time downloads follow the download window too
    "spoiler_free": False,       # the page blurs episode titles until clicked (a series can say otherwise)
    "subs_accept": "",           # and one of these subtitle languages, forced ones aside ("fr"); empty: not checked
    "debug": False,              # Activity's output says more: Unshackle's debug log, every call to Sonarr
    "download_only": False,      # downloaded and tidied, never handed to Sonarr: imported by hand (a series can say otherwise)
    "backends": [],              # other unshackle serve: [{name, url, api_key, downloads (as that serve sees the folder)}]
    "quality_ladder": "",        # the quality ladder every series downloads by (a service or a series can pick another)
}
SETTINGS_FROM_ENV = {
    "unshackle_url": "UNSHACKLE_URL", "unshackle_api_key": "UNSHACKLE_API_KEY", "downloads": "DOWNLOADS",
    "unshackle_downloads": "UNSHACKLE_DOWNLOADS", "cookies_dir": "COOKIES_DIR", "unshackle_config_dir": "UNSHACKLE_CONFIG_DIR", "sonarr_url": "SONARR_URL", "sonarr_api_key": "SONARR_API_KEY",
    "sonarr_downloads": "SONARR_DOWNLOADS", "timezone": "TZ",
}

UNSHACKLE = Unshackle(DATA)
PUSH = Push(DATA)  # the web app's own notifications, on the devices that allowed them


_READ = {"stamp": None, "config": {}}  # the file parsed once per change: a read per series was 50 ms each


def read_file() -> dict:
    try:
        st = CONFIG_FILE.stat()
    except FileNotFoundError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    if _READ["stamp"] != stamp:
        text = CONFIG_FILE.read_text(encoding="utf8")
        _READ.update(stamp=stamp, config=yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {})
    return copy.deepcopy(_READ["config"])  # the callers' own to change


def load_settings(config: dict | None = None) -> dict:
    config = read_file() if config is None else config
    from_env = {k: os.environ[v] for k, v in SETTINGS_FROM_ENV.items() if os.environ.get(v)}
    own = {k: v for k, v in (config.get("settings") or {}).items() if v not in (None, "")}
    return {**SETTINGS_DEFAULTS, **from_env, **own}


def apply_settings(settings: dict) -> None:
    """Set the module's working values; the web page calls it again after a save."""
    global SETTINGS, SONARR, HEADERS, DOWNLOADS, LOCAL, LATE_AFTER, AUTO_DAYS, RUNS_KEEP, RUNS_MAX_DAYS, DEBUG
    SETTINGS = settings
    DEBUG = settings.get("debug") in (True, "true", "on", 1)
    SONARR = str(settings["sonarr_url"]).rstrip("/")
    HEADERS = {"X-Api-Key": str(settings["sonarr_api_key"])}
    DOWNLOADS = Path(str(settings["downloads"]) or DATA / "downloads")
    try:
        LOCAL = ZoneInfo(str(settings["timezone"]))
    except (KeyError, ValueError):
        LOCAL = ZoneInfo("UTC")
    LATE_AFTER = timedelta(hours=float(settings["late_warning_hours"]))
    AUTO_DAYS = float(settings["auto_days"])
    RUNS_KEEP, RUNS_MAX_DAYS = int(settings["history_keep"]), int(settings["history_days"])
    UNSHACKLE.configure(settings)
    BACKENDS.clear()
    for b in settings.get("backends") or []:
        other = Unshackle(DATA)
        other.name = b["name"]
        # its downloads folder empty: as the main serve sees it (both mount it alike), not as Unshacklarr does
        other.configure({"unshackle_mode": "remote", "unshackle_url": b.get("url"), "unshackle_api_key": b.get("api_key"),
                         "unshackle_downloads": b.get("downloads") or settings.get("unshackle_downloads")})
        BACKENDS[b["name"]] = other


BACKENDS: dict[str, Unshackle] = {}  # the other unshackle serve, by name


def backend_named(name: str | None) -> Unshackle:
    return BACKENDS.get(name or "", UNSHACKLE)


def has_service(backend: Unshackle, tag: str) -> bool | None:
    """Whether that serve lists the service; None when it can't be asked (down, a wrong key)."""
    try:
        return any(s["tag"] == tag for s in backend.services())
    except UnshackleError:
        return None


def backend_for(tag: str, config: dict | None = None) -> Unshackle:
    """The unshackle serve a service downloads with: the one Per service picks for it, else the main one, else
    the first other one that has it."""
    config = read_file() if config is None else config
    chosen = ((config.get("service_defaults") or {}).get(tag) or {}).get("backend")
    if chosen in BACKENDS:
        return BACKENDS[chosen]
    if BACKENDS and has_service(UNSHACKLE, tag) is False:  # down, it stays where it is set up: it fails there, saying why
        return next((b for b in BACKENDS.values() if has_service(b, tag)), UNSHACKLE)
    return UNSHACKLE


def all_services() -> list[dict]:
    """Every service a series can pick: the main unshackle serve's, then those only another one has (with its name)."""
    services = list(UNSHACKLE.services())
    have = {s["tag"] for s in services}
    for name, other in BACKENDS.items():
        try:
            extra = other.services()
        except UnshackleError:
            continue
        services += [{**s, "backend": name} for s in extra if s["tag"] not in have]
        have |= {s["tag"] for s in extra}
    return services


apply_settings(load_settings())


def seen_by(setting: str, folder: Path, settings: dict | None = None) -> str:
    """A folder of the downloads folder, as Unshackle (its settings: another serve's) or Sonarr sees it."""
    base = str((SETTINGS if settings is None else settings).get(setting) or "").rstrip("/\\")
    return f"{base}/{folder.name}" if base else str(folder)


LEVELS = {"success": apprise.NotifyType.SUCCESS, "warning": apprise.NotifyType.WARNING, "error": apprise.NotifyType.FAILURE}
# Discord gets a card of its own (colour, poster, fields), not Apprise's plain text under Apprise's logo
DISCORD_WEBHOOK = re.compile(r"^(?:https://(?:[\w-]+\.)?discord(?:app)?\.com/api/webhooks/|discord://)(\d+)/([\w-]+)")
COLOURS = {"success": 0x3FB97A, "warning": 0xE6B94A, "error": 0xE2574C}


def discord_payload(level: str, title: str, message: str, details: dict | None = None) -> dict:
    """A notification as a Discord embed: what happened above, the series and episode as its title, the
    message, then the service, size and time side by side, the series' poster by it."""
    kind, sep, what = re.split(r"(: |：)", title, maxsplit=1) if re.search(r": |：", title) else (title, "", "")  # "：" in Chinese and Japanese
    embed = {"author": {"name": kind if sep else "Unshacklarr"}, "title": (what if sep else title)[:256],
             "description": message[:4000], "color": COLOURS[level], "footer": {"text": "Unshacklarr"},
             "timestamp": datetime.now(timezone.utc).isoformat()}
    details = details or {}
    fields = [(name, details.get(key)) for name, key in (("Service", "service"), ("Size", "size"), ("Took", "took"))]
    if any(v for _, v in fields):
        embed["fields"] = [{"name": i18n.tr(name, SETTINGS.get("language", "en")), "value": str(v), "inline": True} for name, v in fields if v]
    if details.get("poster"):
        embed["thumbnail"] = {"url": details["poster"]}
    return {"username": "Unshacklarr", "embeds": [embed]}


def human_time(seconds: float) -> str:
    seconds = round(seconds)
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min {seconds % 60:02}" if seconds < 3600 else f"{seconds // 3600} h {seconds % 3600 // 60:02}"


def episode_details(ep: dict, show: dict, run: "EpisodeRun | None" = None) -> dict:
    """What a notification card shows beside its text: the service, and for a download its size and time."""
    card = run.card if run else {}
    took = (parse_time(card["ended"]) - parse_time(card["started"])).total_seconds() if card.get("ended") and card.get("started") else None
    poster = next((i.get("remoteUrl") for i in (ep.get("series") or {}).get("images") or [] if i.get("coverType") == "poster"), None)
    return {"service": show.get("service"), "size": card.get("size") and f"{card['size'] / 2**30:.1f} GB",
            "took": took and human_time(took), "poster": poster}


def notification_urls(settings: dict) -> list[str]:
    """Apprise URLs; a Discord webhook saved before them counts as one (Apprise reads it as is)."""
    urls = [u for u in settings.get("urls") or [] if u]
    return urls or ([settings["discord_webhook"]] if settings.get("discord_webhook") else [])


def read_inbox() -> dict:
    return read_json(INBOX_FILE, {"read_at": None, "items": []})


def change_inbox(change) -> dict:
    """Read, change and write the inbox under its lock; what was written."""
    with inbox_lock:
        inbox = read_inbox()
        change(inbox)
        too_old = (datetime.now(timezone.utc) - timedelta(days=INBOX_DAYS)).isoformat()
        inbox["items"] = [i for i in inbox["items"] if i["at"] >= too_old][:INBOX_KEEP]
        write_atomic(INBOX_FILE, json.dumps(inbox))
        return inbox


def inbox_add(level: str, title: str, message: str, action: dict | None = None, batch: str | None = None) -> None:
    item = {"id": uuid.uuid4().hex[:12], "at": datetime.now(timezone.utc).isoformat(), "level": level, "title": title, "message": message[:4000]}
    if action:
        item["action"] = action
    if batch:
        item["batch"] = batch  # from a job: the bell groups its lines by job
    change_inbox(lambda inbox: inbox["items"].insert(0, item))


def inbox_resolve(key: str) -> None:
    """The action with this code (or id) is done: no longer waiting for the person."""
    def done(inbox):
        for i in inbox["items"]:
            action = i.get("action") or {}
            if key and key in (action.get("code"), action.get("id")):
                i["action"]["done"] = True
    change_inbox(done)


ALL_LEVELS = ("success", "warning", "error")
APPS = [(r"discord(app)?\.com/api/webhooks|^discord:", "Discord"), (r"^tgram:", "Telegram"), (r"^ntfys?:", "ntfy"), (r"^pover:", "Pushover"),
        (r"^slack:|hooks\.slack\.com", "Slack"), (r"^mailtos?:", "E-mail"), (r"^gotifys?:", "Gotify"), (r"^matrixs?:", "Matrix"),
        (r"^signals?:", "Signal"), (r"^whatsapp:", "WhatsApp")]


def app_of(url: str) -> str:
    """An address by the app's name only: the address itself holds its secret."""
    return next((name for pattern, name in APPS if re.search(pattern, url)), url.split(":")[0])


def notification_targets(settings: dict) -> list[dict]:
    """Where messages go: [{url, levels}]; addresses saved before levels per address take them all."""
    if settings.get("targets"):
        return [t for t in settings["targets"] if t.get("url")]
    return [{"url": u, "levels": list(ALL_LEVELS)} for u in notification_urls(settings)]


def in_quiet_hours(settings: dict, now: datetime | None = None) -> bool:
    """Within the quiet hours ("23:00" to "08:00", over midnight or not), in the chosen time zone."""
    quiet = settings.get("quiet") or {}
    if not quiet.get("from") or not quiet.get("to") or quiet["from"] == quiet["to"]:
        return False
    now = (now or datetime.now(timezone.utc)).astimezone(LOCAL).strftime("%H:%M")
    start, end = quiet["from"], quiet["to"]
    return start <= now < end if start < end else now >= start or now < end


def send_to(url: str, level: str, title: str, message: str, details: dict | None = None) -> str | None:
    """One message to one address; None when it went through, else why not, in a line."""
    try:
        if hook := DISCORD_WEBHOOK.match(url):
            requests.post(f"https://discord.com/api/webhooks/{hook[1]}/{hook[2]}", json=discord_payload(level, title, message, details),
                          timeout=15).raise_for_status()
            return None
        sender = apprise.Apprise()
        if not sender.add(url):
            return "Not an address Apprise knows"
        with apprise.LogCapture(level=apprise.logging.WARNING) as said:
            if sender.notify(title=title, body=message[:4000], notify_type=LEVELS[level]):
                return None
            lines = [line.strip() for line in said.getvalue().splitlines() if line.strip()]
        return (lines[-1] if lines else "The service refused it")[:200]
    except requests.RequestException as e:
        # the error names the URL, or only its path (/api/webhooks/<id>/<token>): its secret either way
        return re.sub(r"/api/webhooks/\S+", "/api/webhooks/…", re.sub(r"https?://\S+", "the webhook", str(e)))[:200]
    except Exception as e:  # never let a notification stop a download
        return str(e)[:200]


def note_sent(level: str, title: str, to: list[dict]) -> None:
    with sent_lock:
        log = [{"at": datetime.now(timezone.utc).isoformat(), "level": level, "title": title, "to": to}, *read_json(SENT_FILE, [])][:SENT_KEEP]
        try:
            write_atomic(SENT_FILE, json.dumps(log))
        except OSError as e:
            print(f"Sent notifications not written: {e}", file=sys.stderr)


def deliver(settings: dict, level: str, title: str, message: str, details: dict | None = None) -> bool:
    """Out to this device and to each address that takes this level; what each said is kept."""
    to = []
    try:
        if PUSH.send(title, message):
            to.append({"app": "This device", "ok": True})
    except Exception as e:
        print(f"Push notification not sent: {e}", file=sys.stderr)
        to.append({"app": "This device", "ok": False, "error": str(e)[:200]})
    for target in notification_targets(settings):
        if level not in (target.get("levels") or ALL_LEVELS):
            continue
        error = send_to(target["url"], level, title, message, details)
        if error:
            print(f"A notification could not be sent ({app_of(target['url'])}): {title}: {error}", file=sys.stderr)
        to.append({"app": app_of(target["url"]), "ok": not error, **({"error": error} if error else {})})
    if to:
        note_sent(level, title, to)
    return any(t["ok"] for t in to if t["app"] != "This device")


def notify_series(show: dict | None, settings: dict, level: str, title: str, message: str, *args, **kwargs) -> bool:
    """An episode's notification, as its series asks (notify: all, failures or none): the bell keeps them all, only
    what the series lets through goes out."""
    mode = (show or {}).get("notify") or "all"
    if mode == "none" or (mode == "failures" and level != "error"):
        try:
            inbox_add(level, title, message, kwargs.get("action"), kwargs.get("batch"))
        except OSError as e:
            print(f"Inbox not written: {e}", file=sys.stderr)
        return False
    return notify(settings, level, title, message, *args, **kwargs)


def notify(settings: dict, level: str, title: str, message: str, action: dict | None = None, batch: str | None = None,
           details: dict | None = None) -> bool:
    try:
        inbox_add(level, title, message, action, batch)  # the page's bell keeps them all, whatever goes out
    except OSError as e:
        print(f"Inbox not written: {e}", file=sys.stderr)
    if not settings.get(level, True):
        return False
    lang = SETTINGS.get("language", "en")  # the bell above keeps English: the page translates it itself
    title, message = i18n.tr_lines(title, lang), i18n.tr_lines(message, lang)
    if in_quiet_hours(settings):  # kept for the end of the quiet hours; a question waiting for you goes at once
        if not action:
            with sent_lock:
                write_atomic(HELD_FILE, json.dumps([*read_json(HELD_FILE, []), {"level": level, "title": title}]))
            return False
    return deliver(settings, level, title, message, details)


def send_held(settings: dict) -> bool:
    """The quiet hours are over: what came meanwhile, in one message."""
    if in_quiet_hours(settings):
        return False
    with sent_lock:
        held = read_json(HELD_FILE, [])
        if not held:
            return False
        HELD_FILE.unlink(missing_ok=True)
    worst = "error" if any(h["level"] == "error" for h in held) else "warning" if any(h["level"] == "warning" for h in held) else "success"
    lines = [f"{'✓' if h['level'] == 'success' else '!' if h['level'] == 'warning' else '✗'} {h['title']}" for h in held[:30]]
    more = f"\n…and {len(held) - 30} more" if len(held) > 30 else ""
    title = f"During the quiet hours: {len(held)} message{'s' if len(held) > 1 else ''}"
    return deliver(settings, worst, i18n.tr_lines(title, SETTINGS.get("language", "en")), "\n".join(lines) + more)


def missing_episodes(now: datetime | None = None) -> list[dict]:
    """Monitored episodes without a file that aired within the automatic sync's reach (AUTO_DAYS): Sonarr's
    calendar for those days, not its whole wanted list, which runs to the library's every gap."""
    now = now or datetime.now(timezone.utc)
    episodes = sonarr_get("calendar", start=(now - timedelta(days=AUTO_DAYS + 1)).isoformat(), end=now.isoformat(),
                          includeSeries="true", unmonitored="false")
    return [ep for ep in episodes if not ep.get("hasFile") and ep.get("monitored", True) and ep["series"].get("monitored", True)]


def chosen_episodes(ids: list[int]):
    """Episodes picked by hand in the web page, shaped like /wanted/missing records."""
    episodes = sonarr_get("episode", episodeIds=ids, includeSeries="true")
    return sorted(episodes, key=lambda e: (e["seriesId"], e["seasonNumber"], e["episodeNumber"]))


current = threading.local()  # the download attempt this thread works on: what trace() writes to
# Episodes picked together wait for the one before them: episode id -> a card for Activity, meanwhile
waiting: dict[int, dict] = {}
unqueued: set[int] = set()  # episodes taken out of a job's queue from Activity: skipped when their turn comes


# A job's episode list, while it runs: an episode queued again is added to it, until the last is taken
running_jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
paused_jobs: set[str] = set()  # jobs paused from Activity: the download going on ends, the next waits


def pause_job(batch: str, pause: bool) -> None:
    """Pause a job (its next episode waits) or let it go on; its queued episodes say so in Activity."""
    with jobs_lock:
        (paused_jobs.add if pause else paused_jobs.discard)(batch)
        for card in list(waiting.values()):
            if card.get("batch") == batch:
                card["paused"] = pause


def job_episodes(episodes: list, batch: str | None):
    """The episodes one after the other, ones added meanwhile included; closed under the lock at the end,
    so an episode queued again after that starts on its own instead of being lost."""
    i = 0
    while True:
        while batch in paused_jobs:  # paused: the one before is done, this one waits
            time.sleep(1)
        with jobs_lock:
            if i >= len(episodes):
                if batch in running_jobs:
                    running_jobs[batch]["closed"] = True
                return
            ep = episodes[i]
        i += 1
        yield ep


def queued_card(ep: dict, show: dict, kind: str, batch: str | None) -> dict:
    """An episode waiting its turn, as Activity shows it until its download starts."""
    return {"id": f"waiting-{ep['id']}", "series": ep["series"]["title"], "tvdbId": ep["series"]["tvdbId"],
            "sxxeyy": f"S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}", "service": show["service"], "kind": kind,
            "started": datetime.now(timezone.utc).isoformat(), "ended": None, "outcome": "running", "episodeId": ep["id"],
            "step": "queued", "live": {}, "tracks": [], "waiting": True, **({"batch": batch} if batch else {})}


def add_to_job(batch: str, ep: dict, show: dict) -> bool:
    """An episode into a running job's queue (queued again, retried): in its place if it was cancelled
    before its turn, else at the end, one download at a time. False when the job took its last episode:
    then it starts on its own."""
    with jobs_lock:
        job = running_jobs.get(batch)
        if not job or job["closed"]:
            return False
        if ep["id"] in unqueued:
            unqueued.discard(ep["id"])  # its turn has not come: it keeps its place
        else:
            job["episodes"].append(ep)
        waiting[ep["id"]] = job["stubs"][ep["id"]] = {**queued_card(ep, show, "manual", batch), "paused": batch in paused_jobs}
        return True


def cancel_queued(card: dict) -> None:
    """A queued episode that won't start: kept in its job and the history as "cancelled", not dropped."""
    now = datetime.now(timezone.utc)
    run_id = f"{now:%Y%m%d-%H%M%S-%f}-{card['tvdbId']}-{card['sxxeyy']}"
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    done = {k: v for k, v in card.items() if k != "waiting"}
    write_atomic(RUNS_DIR / f"{run_id}.json", json.dumps({**done, "id": run_id, "outcome": "cancelled", "ended": now.isoformat()}))


def trace(text: str, debug: bool = False) -> None:
    """A line in the current attempt's output (in Activity), in grey; debug ones only in debug mode."""
    run = getattr(current, "run", None)
    if run and (DEBUG or not debug):
        run.raw(f"\r\x1b[2K\x1b[90m{'debug · ' if debug else '· '}{text}\x1b[0m\r\n".replace("\n", "\r\n").replace("\r\r", "\r").encode())


AUTOMATIC = ("auto", "burst")  # tries nobody asked for by hand


class EpisodeRun:
    """One download attempt's log and card, for the web page's terminal and its history.

    <id>.log holds what the terminal shows; <id>.json the series, episode, times and
    outcome (downloaded, failed, kept, unavailable). An episode's checks that found nothing
    (a release burst makes one every 30 s) fold into its next attempt: one line in the
    history, their logs one after the other in its terminal.
    """

    active: set[str] = set()  # ids of the runs going on now; any other without an end was cut off

    def __init__(self, ep: dict, show: dict, kind: str, service_sxxeyy: str = "", batch: str | None = None):
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        sxxeyy = f"S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}"
        self.id = f"{now:%Y%m%d-%H%M%S-%f}-{ep['series']['tvdbId']}-{sxxeyy}"
        self.card = {
            "id": self.id, "series": ep["series"]["title"], "tvdbId": ep["series"]["tvdbId"], "sxxeyy": sxxeyy,
            "service": show["service"], "kind": kind, "started": now.isoformat(), "ended": None, "outcome": None,
            "episodeId": ep.get("id"), "serviceEpisode": service_sxxeyy or sxxeyy,
            # For Activity's detail: where it stands (queued, downloading, finishing, importing),
            # the job's live figures, every track seen so far, and why it ended as it did.
            "step": "queued", "live": {}, "tracks": [], "parts": None, "cause": "", "detail": "",
            **({"batch": batch} if batch else {}),  # picked with others: one job in Activity
        }
        if kind == "retry" or batch:  # in a job: one card per episode, whatever its tries
            self.take_over_failures(batch)
        elif kind in AUTOMATIC:  # a sync or a release burst trying again: one card for its failures in a row
            self.take_over_failures(automatic=True)
        self.log = (RUNS_DIR / f"{self.id}.log").open("ab")
        self.raw(f"\x1b[90m── {now.astimezone(LOCAL):%a %d %b %H:%M:%S} · {kind} ──\x1b[0m\r\n".encode())
        self.save()
        EpisodeRun.active.add(self.id)
        current.run = self

    def say(self, text: str) -> None:
        print(text, flush=True)
        self.raw(f"{text}\r\n".encode())

    def raw(self, chunk: bytes) -> None:
        self.log.write(chunk)
        self.log.flush()

    def step(self, name: str) -> None:
        self.card["step"] = name
        self.save()

    def finish(self, outcome: str, cause: str = "", detail: str = "") -> None:
        if getattr(current, "run", None) is self:
            current.run = None
        self.card.update(cause=cause, detail=detail)
        EpisodeRun.active.discard(self.id)
        self.log.close()
        self.card.update(ended=datetime.now(timezone.utc).isoformat(), outcome=outcome)
        self.fold_checks()
        self.save()
        prune_runs()

    def take_over_failures(self, batch: str | None = None, automatic: bool = False) -> None:
        """A retry takes the place of this episode's failed, stopped, cancelled or cut-off attempts (in a job,
        that job's): one line in the history, their logs before its own, and how many attempts it makes in all.
        An automatic try (the sync, a release burst) takes only the automatic tries that failed before it: what
        was stopped or picked by hand keeps its own card. The failure last told carries over: told once."""
        attempts = 1
        for path in sorted(RUNS_DIR.glob(f"*-{self.card['tvdbId']}-{self.card['sxxeyy']}.json")):
            try:
                c = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            cut_off = not c.get("ended") and c["id"] not in EpisodeRun.active
            if c["id"] == self.id or not (cut_off or c.get("outcome") in ("failed", "stopped", "cancelled")):
                continue
            if batch and c.get("batch") != batch:
                continue  # another job's: its own card there
            if automatic and (c.get("kind") not in AUTOMATIC or c.get("outcome") != "failed"):
                continue
            if c.get("told"):
                self.card["told"] = c["told"]
            log = RUNS_DIR / f"{c['id']}.log"
            if log.exists():
                with (RUNS_DIR / f"{self.id}.log").open("ab") as mine:
                    mine.write(log.read_bytes())
            attempts += c.get("attempts", 1)
            for old in RUNS_DIR.glob(f"{c['id']}.*"):
                old.unlink(missing_ok=True)
        self.card["attempts"] = attempts

    def fold_checks(self) -> None:
        """This episode's earlier "not out yet" cards, taken into this one: their count, and their logs before its own."""
        earlier = []
        for path in sorted(RUNS_DIR.glob(f"*-{self.card['tvdbId']}-{self.card['sxxeyy']}.json")):
            try:
                c = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if c["id"] != self.id and c.get("outcome") == "unavailable" and c.get("ended"):
                earlier.append(c)
        if not earlier:
            self.card.setdefault("checks", 1)
            return
        log = RUNS_DIR / f"{self.id}.log"
        mine = log.read_bytes() if log.exists() else b""
        before = b"".join((RUNS_DIR / f"{c['id']}.log").read_bytes() for c in earlier if (RUNS_DIR / f"{c['id']}.log").exists())
        write_atomic(log, before + mine)
        self.card["checks"] = sum(c.get("checks", 1) for c in earlier) + 1
        self.card["first_check"] = earlier[0].get("first_check") or earlier[0]["started"]
        for c in earlier:
            for path in RUNS_DIR.glob(f"{c['id']}.*"):
                path.unlink(missing_ok=True)

    def save(self) -> None:
        write_atomic(RUNS_DIR / f"{self.id}.json", json.dumps(self.card))


def tell_failure(run: EpisodeRun, cause: str, send) -> None:
    """A failure notified once: the same cause again (the next sync, the next try of a burst) says nothing new."""
    if run.card.get("told") != cause:
        send()
        run.card["told"] = cause


def prune_runs() -> None:
    cards = sorted(RUNS_DIR.glob("*.json"), reverse=True)  # ids start with the time: newest first
    too_old = (datetime.now(timezone.utc) - timedelta(days=RUNS_MAX_DAYS)).strftime("%Y%m%d")
    for card in cards[RUNS_KEEP:] + [c for c in cards[:RUNS_KEEP] if c.name[:8] < too_old]:
        for path in RUNS_DIR.glob(f"{card.stem}.*"):
            path.unlink(missing_ok=True)


class JobFailed(Exception):
    """unshackle ran the download and it failed (or was stopped). The message is all it
    said, for the terminal; `cause` the one line that matters, for notifications."""

    def __init__(self, message: str, status: str = "failed", cause: str = ""):
        super().__init__(message)
        self.status = status
        self.cause = cause or message


def job_cause(job: dict) -> str:
    """Why a job failed, in a line: Unshackle's own error (what it printed on stderr),
    else serve's message without its "Worker exited with code 1:" wrapping."""
    stderr = (job.get("worker_stderr") or "")[-20_000:]  # its end: a service may put a whole page in it
    if (m := re.search(r"^Stderr:[ \t]*(.+?)\s*(?:^Worker failed|\Z)", stderr, re.M | re.S)) and m.group(1).strip():
        return m.group(1).strip()
    message = job.get("error_message") or job.get("status") or "failed"
    return re.sub(r"^(?:Worker exited with code \d+:\s*)+", "", message).strip()


def ask_for_action(run: EpisodeRun, need: dict | None) -> None:
    """What the service needs from the person to go on (a TV login: a link and a code), on the
    run's card for Activity and sent once per code; gone from the card once done."""
    had = run.card.get("action")
    if need and need.get("code") and need.get("code") != (had or {}).get("code"):
        run.card["action"] = {k: need.get(k) for k in ("service", "message", "url", "code", "expires_at")}
        left = max(1, round((float(need.get("expires_at") or time.time() + 600) - time.time()) / 60))
        what = f"open {need.get('url')} and enter {need.get('code')}"
        run.say(f"\r\n\x1b[33m{need.get('service') or run.card['service']}: {need.get('message') or 'action needed'}: {what} (within {left} min)\x1b[0m")
        notify((read_file().get("notifications") or {}), "warning", f"{need.get('service') or run.card['service']} needs you: {need.get('code')}",
               f"{need.get('message') or 'Action needed'}: {what} within {left} min, for {run.card['series']} {run.card['sxxeyy']}.",
               action=run.card["action"])
    elif not need and had:
        del run.card["action"]
        inbox_resolve(had.get("code"))
        run.say(f"\r\n\x1b[32m{had.get('service') or run.card['service']}: done, going on\x1b[0m")


def ask_for_input(run: EpisodeRun, prompt: str | None) -> None:
    """A question the service asks while downloading (an OTP code sent by e-mail, a profile PIN):
    on the run's card and in the bell, with a field to answer; sent once per question."""
    had = run.card.get("prompt")
    if prompt and prompt != (had or {}).get("text"):
        service = run.card["service"]
        run.card["prompt"] = {"id": uuid.uuid4().hex[:12], "text": prompt, "service": service, "run": run.id}
        run.say(f"\r\n\x1b[33m{service} asks: {prompt}\x1b[0m")
        notify((read_file().get("notifications") or {}), "warning", f"{service} asks you something",
               f"{prompt}\nAnswer in Unshacklarr, for {run.card['series']} {run.card['sxxeyy']}.",
               action={"kind": "input", **run.card["prompt"]})
    elif not prompt and had:
        del run.card["prompt"]
        inbox_resolve(had["id"])
        run.say(f"\r\n\x1b[32m{had['service']}: answered, going on\x1b[0m")


def run_job(payload: dict, run: EpisodeRun | None = None, poll: float = 0.5) -> list[str]:
    """Hand the download to unshackle serve (the run's own, when another one has its service) and follow it to
    the end, drawing its progress in the run's log (the web page's terminal). Returns the files it made."""
    backend = backend_named(run.card.get("backend") if run else None)
    job_id = backend.download(payload)
    trace(f"unshackle serve{f' {backend.name}' if backend.name else ''} took it as job {job_id}")
    if run:  # what Activity's Stop button cancels
        run.card["job_id"] = job_id
        run.save()
    last_status, line, logged = None, "", 0
    seen: dict[str, float] = {}  # serve lists only the tracks downloading now: keep the others
    timed: dict[str, list] = {}  # each track's [first seen, done]: how long it took
    labels: dict[str, str] = {}  # a track's key (its part and its place, or its label from an older serve): its label
    titles: list[str] = []
    misses = 0
    while True:
        try:
            job = backend.job(job_id)
            misses = 0
        except UnshackleError as e:
            # One status that didn't come is not a failed download: the job still runs on serve, and a
            # retry now would start it a second time into the same folder.
            misses += 1
            if misses >= POLL_MISSES or not TRANSIENT.search(str(e)):
                try:
                    backend.cancel(job_id)  # gone for good: nothing of it may go on behind a retry
                except UnshackleError:
                    pass
                raise
            time.sleep(max(poll, 5))
            continue
        status = job.get("status")
        if run:
            title = job.get("current_title") or ""
            if title and title not in titles:
                titles.append(title)
            active = {f"{title}|{t.get('index', t.get('label'))}": t for t in job.get("track_progress") or []}
            labels.update({k: str(t.get("label")) for k, t in active.items()})  # two tracks named alike stay two
            now = {k: float(t.get("progress") or 0) for k, t in active.items()}
            for key in seen:  # started before, not downloading now: done
                if key not in now and seen[key] < 100:
                    seen[key] = 100.0
            seen.update({k: max(v, seen.get(k, 0)) for k, v in now.items()})
            if status in TERMINAL and status == "completed":
                seen.update({k: 100.0 for k in seen})
            for key, value in seen.items():
                clock = timed.setdefault(key, [time.monotonic(), None])
                if value >= 100 and clock[1] is None:
                    clock[1] = time.monotonic()
            part = lambda key: f"Part {titles.index(key.split('|')[0]) + 1} · " if len(titles) > 1 and key.split("|")[0] in titles else ""
            run.card["tracks"] = [{"label": part(k) + labels[k], "progress": round(v, 1),
                                   **({"took": round(timed[k][1] - timed[k][0])} if timed[k][1] is not None else {})} for k, v in seen.items()]
            run.card["live"] = {k: job.get(k) for k in ("status", "progress", "speed", "completed_tracks", "total_tracks", "phase")}
            if job.get("cdm") or job.get("proxy"):  # what serve went through, once it says (no proxy is an answer too)
                run.card.update(proxy=job.get("proxy"), cdm=job.get("cdm"))
            if status == "downloading" and run.card["step"] == "queued":
                run.card["step"] = "downloading"
            ask_for_action(run, job.get("action_needed") if status not in TERMINAL else None)
            ask_for_input(run, job.get("input_prompt") if status not in TERMINAL else None)
            run.save()
        if run and (count := int(job.get("log_count") or 0)) > logged:  # Unshackle's own log, as it goes
            lines = (job.get("log") or [])[-(count - logged):]
            if count - logged > len(lines):
                run.raw(f"\r\x1b[2K\x1b[90m… {count - logged - len(lines)} lines of Unshackle's log skipped\x1b[0m\r\n".encode())
            for text in lines:
                colour = "31" if " ERROR " in text or " CRITICAL " in text else "33" if " WARNING " in text else "90"
                run.raw(f"\r\x1b[2K\x1b[{colour}m  {text}\x1b[0m\r\n".encode())
            logged, line = count, ""  # the progress line is drawn again under them
        if DEBUG and run and status != last_status:
            trace("job: " + json.dumps({k: v for k, v in job.items() if k not in ("log", "track_progress", "parameters")})[:1500], debug=True)
        if status != last_status and run:
            run.raw(f"\r\x1b[2K\x1b[36m{status}\x1b[0m {job.get('current_title') or job.get('title') or ''}\r\n".encode())
            last_status = status
        if status in TERMINAL:
            break
        if run and status == "downloading":
            progress = float(job.get("progress") or 0)
            tracks = f"{job.get('completed_tracks', 0)}/{job.get('total_tracks', 0)} tracks"
            new = f"  \x1b[32m{'█' * int(progress / 5):<20}\x1b[0m {progress:5.1f}%  {tracks}  {job.get('speed') or ''}  {job.get('phase') or ''}"
            if new != line:
                run.raw(f"\r\x1b[2K{new[:118]}".encode())  # within the terminal's 110 columns: redrawn, not wrapped
                line = new
        time.sleep(poll)
    if status != "completed":
        detail = job.get("error_details") or ""
        stderr = "\n".join((job.get("worker_stderr") or "").strip().splitlines()[-15:])
        raise JobFailed("\n".join(filter(None, [job.get("error_message") or status, detail, stderr])), status, job_cause(job))
    files = job.get("output_files") or []
    trace(f"Unshackle made {len(files)} file{'s' if len(files) != 1 else ''}: " + ", ".join(Path(f).name for f in files))
    return files


# Worth another try in a moment: the network or the service stumbled, nothing is wrong with the request.
TRANSIENT = re.compile(r"timed? ?out|timeout|connection (reset|refused|aborted|error)|remote end closed|temporarily|"
                       r"max retries|\b50[234]\b|service unavailable|bad gateway|name resolution|network is unreachable", re.IGNORECASE)
RETRY_AFTER = (30, 120)  # seconds before each retry
POLL_MISSES = 12  # job statuses missed in a row (5 s apart at least) before the job counts as lost
# The service turned Unshackle away: cookies or credentials out of date, most of the time.
LOGIN = re.compile(r"\b40[13]\b|unauthori[sz]ed|forbidden|not logged|log ?in|sign ?in|cookie|expired|session|"
                   r"invalid token|access token|credential|authenticat", re.IGNORECASE)


def run_job_retrying(payload: dict, run: EpisodeRun | None = None, sleep=time.sleep) -> list[str]:
    """run_job, tried again after 30 s then 2 min when it failed on the network or a service
    hiccup (a timeout, a 502...). Other failures (not found, login, a language missing) end it."""
    for wait in (*RETRY_AFTER, None):
        try:
            return run_job(payload, run)
        except (JobFailed, UnshackleError) as e:
            cause = getattr(e, "cause", str(e))
            if wait is None or getattr(e, "status", "failed") == "cancelled" or not TRANSIENT.search(cause):
                raise
            if run:
                run.say(f"\r\n\x1b[33mTemporary failure ({cause[:120]}): trying again in {wait} s\x1b[0m")
                run.card["attempts"] = run.card.get("attempts", 1) + 1
            sleep(wait)


PART = re.compile(r"\.Part\.(\d+)", re.IGNORECASE)
VIDEO = (".mkv", ".mp4")


def videos_in(folder: Path) -> list[Path]:
    return [f for f in folder.rglob("*") if f.suffix.lower() in VIDEO]
# In a file name, Unshackle writes a part .Part.N: a bare number after the episode is its resolution
# (S17E03.1080p) or the start of its title (S02E01.1000.Days), never a part.
SXXEYY = re.compile(r"S\d{2,}E\d{2,}(?:\.Part\.\d+)?", re.IGNORECASE)
# In an MKV title (Show S29E05.1 All Stars), it writes a part .N, and spaces around the rest.
TITLE_SXXEYY = re.compile(r"S\d{2,}E\d{2,}(?:\.\d+)?", re.IGNORECASE)


def parse_time(value: str) -> datetime:
    at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)  # a date written by hand, without a zone: UTC


def wanted_automatically(show: dict, ep: dict, now: datetime) -> bool:
    """New episodes only: aired since the series got its service, not too long ago, and
    past the series' release time when it has one (the service publishes it then, not at airing)."""
    since, aired = show.get("since"), ep.get("airDateUtc")
    if not since or not aired:
        return False  # no start date: nothing is safe to take without being asked
    slot = release_slot(show, ep)
    if slot and now < slot:
        return False
    aired = parse_time(aired)
    if not slot and aired > now:  # not aired yet, and no release before: Sonarr's wanted list alone kept these out
        return False
    return aired >= parse_time(str(since)) and now - aired <= timedelta(days=AUTO_DAYS)


BROADCAST_DAYS_MAX = 3 * 366  # how far a series' own schedule is followed: its episodes then go undated


def broadcast_dates(show: dict, episodes: list[dict]) -> dict[int, datetime]:
    """When each episode airs by the series' own schedule (Cat's Eyes: from S01E01 on Tuesday 30 September at
    20:39, weekly, one an evening but two on 7 October): episode id -> local time. The episodes before its
    first one keep Sonarr's dates; the schedule goes on for every one after, whatever Sonarr says."""
    plan = show.get("broadcast") or {}
    if not plan.get("start") or not plan.get("time"):
        return {}
    first = re.fullmatch(r"S(\d+)E(\d+)", str(plan.get("from") or "S01E01").upper())
    start = tuple(map(int, first.groups())) if first else (1, 1)
    dated = sorted((e for e in episodes if e.get("seasonNumber", 0) > 0 and (e["seasonNumber"], e["episodeNumber"]) >= start),
                   key=lambda e: (e["seasonNumber"], e["episodeNumber"]))
    hour, minute = map(int, str(plan["time"]).split(":"))
    evenings, days = {str(k): v for k, v in (plan.get("evenings") or {}).items()}, set(plan.get("days") or [])  # a date typed unquoted: a date key
    first_day = day = datetime.fromisoformat(str(plan["start"])).date()
    out = {}
    for _ in range(BROADCAST_DAYS_MAX):
        if not dated:
            break
        regular = plan.get("every") == "daily" or day.weekday() in days or day == first_day  # its first evening, whatever the day
        count = int(evenings.get(day.isoformat(), plan.get("per_evening") or 1 if regular else 0))
        at = datetime.combine(day, datetime.min.time(), LOCAL).replace(hour=hour, minute=minute)
        for ep in dated[:count]:
            out[ep["id"]] = at
        dated = dated[count:]
        day += timedelta(days=1)
    return out


def broadcast_plans(config: dict) -> list[tuple[dict, list[dict], dict[int, datetime]]]:
    """Each series with its own schedule: Sonarr's series, its episodes, and the dates the schedule gives them."""
    shows = {tvdb: s for tvdb, s in (config.get("series") or {}).items() if s.get("service") and s.get("broadcast")}
    if not shows:
        return []
    plans = []
    for tvdb, serie in sonarr_series(set(shows)).items():
        if tvdb in shows:
            episodes = sonarr_get("episode", seriesId=serie["id"])
            plans.append((serie, episodes, broadcast_dates(shows[tvdb], episodes)))
    return plans


_series_by_tvdb: tuple[float, dict] = (0.0, {})


def sonarr_series(wanted: set) -> dict[int, dict]:
    """Sonarr's series by TVDB id, kept an hour: the whole library is one big answer, asked again sooner
    only for a series it did not have (added since), at most once a minute."""
    global _series_by_tvdb
    fetched, known = _series_by_tvdb
    age = time.monotonic() - fetched
    if not fetched or age > 3600 or (age > 60 and not wanted <= set(known)):
        known = {s["tvdbId"]: s for s in sonarr_get("series") if s.get("tvdbId")}
        _series_by_tvdb = (time.monotonic(), known)
    return known


def broadcast_dated(plans: list) -> set[int]:
    """The episodes a series' own schedule dates: Sonarr's dates for them are set aside. The ones
    before its first episode keep Sonarr's."""
    return {episode_id for _, _, dates in plans for episode_id in dates}


def broadcast_episodes(config: dict, start: datetime, end: datetime, plans: list | None = None) -> list[dict]:
    """The episodes a series' own schedule has air between start and end (whatever Sonarr's dates), with
    that date, shaped like Sonarr's calendar records: what the calendar and the sync ask Sonarr for otherwise."""
    out = []
    for serie, episodes, dates in broadcast_plans(config) if plans is None else plans:
        for ep in episodes:
            at = dates.get(ep["id"])
            if at and start <= at < end:
                out.append({**ep, "series": serie, "airDateUtc": at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "airDate": at.date().isoformat()})
    return out


EARLY_DAYS_MAX = 14  # a service may put an episode out up to two weeks before the channel Sonarr follows airs it


def release_day(show: dict) -> int:
    """The day the service publishes, from the day it airs: 0 that day, 1 the day after, -N N days before."""
    return max(-EARLY_DAYS_MAX, min(1, int(show.get("release_day") or 0)))


def release_slot(show: dict, ep: dict) -> datetime | None:
    """When the service publishes this episode: the series' release time on the day it airs, in
    local time (a 00:01 release of a Monday 21:00 episode is Monday 00:01), the day after with
    `release_day` 1, or N days before with -N (a platform ahead of the channel Sonarr follows).
    Days before without a release time count from midnight: the regular syncs take it from then."""
    day = release_day(show)
    if not ep.get("airDateUtc") or not (show.get("release_time") or day < 0):
        return None
    hour, minute = map(int, str(show.get("release_time") or "00:00").split(":"))
    aired = parse_time(ep["airDateUtc"]).astimezone(LOCAL).date() + timedelta(days=day)
    return datetime.combine(aired, datetime.min.time(), LOCAL).replace(hour=hour, minute=minute)


def early_episodes(config: dict, now: datetime) -> list[dict]:
    """Episodes a service puts out before they air (release_day -N), once their release time has
    come: Sonarr's wanted list only holds aired ones. Shaped like its records."""
    ahead = max((-release_day(s) for s in (config.get("series") or {}).values() if s.get("service")), default=0)
    if ahead <= 0:
        return []
    episodes = sonarr_get("calendar", start=(now - timedelta(days=1)).isoformat(),
                          end=(now + timedelta(days=ahead + 1)).isoformat(), includeSeries="true")
    early = []
    for ep in episodes:
        show = (config.get("series") or {}).get(ep["series"]["tvdbId"]) or {}
        slot = show.get("service") and release_day(show) < 0 and release_slot(show, ep)
        if slot and slot <= now < parse_time(ep["airDateUtc"]) and not ep.get("hasFile") and ep.get("monitored", True):
            early.append(ep)
    return early


busy_episodes: set[str] = set()
busy_lock = threading.Lock()


@contextlib.contextmanager
def episode_lock(out: Path):
    """One download per episode at a time, whoever starts it: the sync, a burst, a click."""
    with busy_lock:
        mine = out.name not in busy_episodes
        busy_episodes.add(out.name)
    try:
        yield mine
    finally:
        if mine:
            with busy_lock:
                busy_episodes.discard(out.name)


# The series listed on its service, as the page's own lookup reads it (web.py sets it): {"available": Sonarr episode
# id -> {"service": its number there, "match": "title" or "absolute" when not by its number}, "listed": the numbers
# it lists}. Asked before a download and kept a while: the bursts at a release time try every 30 s, and the other
# episodes of a job ask the same.
find_by_title = None
TITLE_KEEP = 15 * 60  # ponytail: one listing per series every 15 min; a fresher one only from the page
title_matches: dict[int, tuple[float, dict | None]] = {}


def service_listing(show: dict, ep: dict, run) -> dict | None:
    """The series' listing on its service, kept TITLE_KEEP; None when it can't be had (no hook, an error)."""
    # kept for the series as it is set up now: a numbering, a URL or a service changed since is listed afresh
    tvdb = (ep["series"]["tvdbId"], show.get("service"), str(show.get("title")), json.dumps({k: show.get(k) for k in NUMBERING}, sort_keys=True, default=str))
    when, found = title_matches.get(tvdb, (None, None))
    if when is None or time.monotonic() - when > TITLE_KEEP:
        if find_by_title is None:
            return None
        try:
            found = find_by_title(show, ep)
        except Exception as e:  # a listing that fails changes nothing: the episode is asked for by its number
            run.say(f"Could not list {show['service']}'s episodes: {e}")
            found = None
        title_matches[tvdb] = (time.monotonic(), found)
    return found


def by_title(show: dict, ep: dict, asked: str, run) -> dict | None:
    """Where the service has an episode under another number than asked, found by its title or its absolute
    number: {"service": its number there, "match": "title" or "absolute"}."""
    other = ((service_listing(show, ep, run) or {}).get("available") or {}).get(ep["id"])
    return other if other and other.get("match") in ("title", "absolute") and other["service"] != asked else None


def wrong_number(show: dict, ep: dict, asked: str, run) -> dict | None:
    """Before the download: the number asked is another episode's, by that one's title (a same-named series
    elsewhere, seasons numbered apart). Then {"service": where this one's own title puts it} or {} when
    nowhere. None when nothing says so: a title that merely differs (a translation TMDB lacks on release day)
    never stops a download."""
    found = service_listing(show, ep, run)
    if not found or asked not in found.get("listed", ()):
        return None  # not listed (not out yet, a stale list): asked as before
    hit = (found.get("available") or {}).get(ep["id"])
    if hit and hit.get("match") in ("title", "absolute") and hit["service"] != asked:
        return {"service": hit["service"], "match": hit["match"]}  # its own title puts it elsewhere
    if not hit and asked.split(".")[0] in found.get("titled", ()):
        return {}  # its number is another episode's, by that one's title
    return None


FALLBACK_NUMBERING = ("season_map", "season_offset", "season_offset_from", "episode_offset", "episode_map")


def fallback_show(show: dict) -> dict | None:
    """The series as its fallback service has it ({service, title, service_options} and its own numbering), the
    rest (file names, parts, languages, ladder) its own; None when it has none."""
    alt = show.get("fallback") or {}
    if not alt.get("service") or not alt.get("title"):
        return None
    own = {k: v for k, v in show.items() if k not in ("service", "title", "service_options", "fallback", *FALLBACK_NUMBERING)}
    return {**own, "service": alt["service"], "title": alt["title"], "service_options": alt.get("service_options") or {},
            **{k: alt[k] for k in FALLBACK_NUMBERING if alt.get(k)}}


def stacked(show: dict, config: dict) -> tuple[dict, dict]:
    """The options for this series: the defaults, then the service's, then the series' own;
    each level overrides the one before for the same flag."""
    service = (config.get("service_defaults") or {}).get(show["service"]) or {}
    dl = {**(config.get("defaults") or {}), **(service.get("options") or {}), **(show.get("options") or {})}
    own = {**(service.get("service_options") or {}), **(show.get("service_options") or {})}
    return dl, own


def to_args(opts: dict) -> list[str]:
    args = []
    for flag, value in opts.items():
        if value is True:
            args.append(flag)
        elif value not in (None, False, ""):
            args += [flag, options.HIDDEN.sub("//***@", str(value))]  # shown and copied: no proxy password
    return args


def unshackle_command(show: dict, config: dict, service_sxxeyy: str, out: Path) -> list[str]:
    """The `unshackle dl` command line the download amounts to, as the Schedule shows it."""
    dl, own = stacked(show, config)
    where = seen_by("unshackle_downloads", out, backend_for(show["service"], config).settings)
    return ["unshackle", "dl", "-o", where, "-w", service_sxxeyy,
            *to_args(dl), show["service"], *to_args(own), str(show["title"])]


def service_entry(tag: str, config: dict | None = None) -> dict:
    return next((s for s in backend_for(tag, config).services() if s["tag"] == tag), {})


def no_cdm(tag: str, config: dict | None = None) -> str:
    """Why a service cannot be downloaded for want of a CDM: unshackle.yaml gives it no device and has no
    default one, so Unshackle would fail on its first license. Empty when a device decrypts it, when it is
    set to none (no DRM), or when it can't be told (Unshackle unreachable, no cdm: section read)."""
    try:
        cdm = backend_for(tag, config).cdm_config()
    except UnshackleError:
        return ""
    if not cdm or any(str(k).lower() in ("default", tag.lower()) and v for k, v in cdm.items()):
        return ""
    return (f"No CDM for {tag}: unshackle.yaml sets no device for it and no default device. "
            "Pick one in Settings, CDM, or No CDM if it has no DRM")


def download_request(show: dict, config: dict, service_sxxeyy: str, out: Path) -> dict:
    """The same download, as unshackle serve's /api/download takes it."""
    if why := no_cdm(show["service"], config):
        raise ValueError(why)
    dl, own = stacked(show, config)
    backend = backend_for(show["service"], config)
    return {
        # unshackle.yaml's dl: section first, as on the command line: serve does not apply it
        **options.from_dl_config(backend.dl_config(), show["service"]),
        **options.to_params(own, options.service_specs(service_entry(show["service"], config))),
        **options.to_params(dl, options.dl_specs()),
        "service": show["service"],
        "title_id": str(show["title"]),
        "wanted": [service_sxxeyy],
        "output_dir": seen_by("unshackle_downloads", out, backend.settings),
        **({"debug": True} if DEBUG else {}),  # the job's log at DEBUG
    }


def setup_of(request: dict, backend: Unshackle | None = None) -> dict:
    """What Activity shows of a download before serve says more: where it runs, the proxy asked, the same as a command."""
    backend = backend or UNSHACKLE
    if request.get("remote"):
        via = f"remote server {request['server']}" if request.get("server") else "remote server"
    elif backend.mode == "remote":
        host = urlparse(str(backend.settings.get("unshackle_url") or "")).hostname or "elsewhere"
        via = f"{backend.name} ({host})" if backend.name else host
    else:
        via = "this computer"
    proxy = "none" if request.get("no_proxy") else options.HIDDEN.sub("//***@", str(request.get("proxy") or "")) or None
    return {"via": via, "proxy": proxy, "command": options.command_line(request)}


def episode_folder(ep: dict) -> Path:
    """Where the episode downloads; its presence means it waits for Sonarr's import."""
    return DOWNLOADS / f"unshackle-{ep['series']['tvdbId']}-S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}"


def service_episode(show: dict, season: int, number: int) -> str | None:
    """How the service numbers Sonarr's episode, None when the offset leaves no episode."""
    mapped = (show.get("episode_map") or {}).get(f"S{season:02}E{number:02}")
    if mapped:
        return mapped
    number += int(show.get("episode_offset") or 0)
    season_map = show.get("season_map") or {}
    if season in season_map:
        season = season_map[season]
    elif season >= int(show.get("season_offset_from") or 1):  # Futurama on Disney+: Sonarr's S08 on is S11 on
        season += int(show.get("season_offset") or 0)
    return f"S{season:02}E{number:02}" if number >= 1 else None  # E00 would be a special, or nothing


def parts_in(out: Path) -> int:
    """How many parts of the episode landed: its Part.N files, or 1 for a whole file."""
    return len({int(m.group(1)) for f in out.rglob("*.mkv") if (m := PART.search(f.name))}) or 1


def finalize(out: Path, sxxeyy: str, name: str, join_parts: bool = True, episode_name: str = "joined",
             on_step=lambda step, parts: None) -> int:
    """Join split parts into one file, then name it and title it after Sonarr's series.

    `name` replaces whatever the service calls the show ("Koh-Lanta : All stars") and
    `sxxeyy` its numbering, both in the file name and in the MKV's title. `episode_name`
    says when to drop the episode's name: "keep", "always", or once parts were "joined"
    (each part has its own name then, and none names the whole episode).
    """
    # The downloads folder is shared (Sonarr, a NAS): a link planted there must not make mkvmerge or a
    # rename write through it, outside the folder
    if out.is_symlink() or any(f.is_symlink() for f in out.rglob("*")):
        raise RuntimeError(f"{out.name} contains a symbolic link: nothing was joined or renamed. Check the folder")
    parts = sorted((int(m.group(1)), f) for f in out.rglob("*.mkv") if (m := PART.search(f.name)))
    if len(parts) > 1 and not join_parts:
        raise RuntimeError(
            f"The service split this episode into {len(parts)} parts and joining is off for this series. "
            "Map each part to its episode (e.g. S01E02=S01E01.2) or turn joining on."
        )
    trace(f"{len(parts)} parts to join" if len(parts) > 1 else "one file, nothing to join")
    if len(parts) > 1:
        on_step("joining", len(parts))
        first = parts[0][1]
        joined = first.with_name(PART.sub("", first.name, count=1))
        if joined.exists() or joined.is_symlink():
            raise RuntimeError(f"{joined.name} already exists: the parts are kept and nothing is joined")
        cmd = ["mkvmerge", "-q", "-o", str(joined), str(first)]
        for _, f in parts[1:]:
            cmd += ["+", str(f)]  # "+" appends: part 2 plays after part 1
        run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        # 1 means warnings only, but not that one: "can probably not be appended correctly" is a file that may
        # stop playing where the parts meet. The parts are kept for a look, nothing is imported.
        if run.returncode > 1 or (run.returncode == 1 and "append" in run.stdout.lower()):
            joined.unlink(missing_ok=True)
            raise RuntimeError(f"mkvmerge could not join the {len(parts)} parts: {run.stdout.strip()[-500:]}")
        for _, f in parts:
            f.unlink()

    def dots(text: str) -> str:
        return re.sub(r"[^\w-]+", ".", text).strip(".")

    on_step("renaming", max(len(parts), 1))

    for f in out.rglob("*.mkv"):
        match = SXXEYY.search(f.name)
        if not match:
            continue
        info = subprocess.run(["mkvmerge", "-J", str(f)], stdout=subprocess.PIPE, text=True)
        title = json.loads(info.stdout or "{}").get("container", {}).get("properties", {}).get("title", "")
        episode = title[m.end():].strip() if (m := TITLE_SXXEYY.search(title)) else ""
        rest = f.name[match.end():]
        if episode_name == "always" or (episode_name == "joined" and len(parts) > 1):
            if episode and rest.lower().startswith(f".{dots(episode)}.".lower()):
                rest = rest[len(dots(episode)) + 1 :]
            episode = ""
        trace(f"renamed {f.name} to {dots(name)}.{sxxeyy}{rest}")
        f = f.rename(f.with_name(f"{dots(name)}.{sxxeyy}{rest}"))
        subprocess.run(
            ["mkvpropedit", "-q", str(f), "--edit", "info", "--set", f"title={name} {sxxeyy} {episode}".strip()],
            check=True,
        )
    return max(len(parts), 1)


# Two- and three-letter codes of the same language, as mkvmerge may write them (fre/fra/fr).
# mkvmerge's older three-letter language codes, as the two-letter ones --a-lang and the page use
LANGS = {"fre": "fr", "fra": "fr", "eng": "en", "ger": "de", "deu": "de", "spa": "es", "ita": "it", "jpn": "ja",
         "por": "pt", "dut": "nl", "nld": "nl", "kor": "ko", "chi": "zh", "zho": "zh", "rus": "ru", "swe": "sv"}


def wanted_audio(show: dict, config: dict) -> str | None:
    """The language asked for first (--a-lang, else --lang), if a real one: fr in "fr,orig,en"."""
    dl, _ = stacked(show, config)
    for flag in ("--a-lang", "--lang"):
        for code in str(dl.get(flag) or "").split(","):
            code = code.strip().lower()
            if code and code not in ("orig", "all", "best") and not code.startswith("-"):
                return code
    return None


def free_space_problem() -> str:
    """Why nothing may be downloaded now: the downloads folder has less room than the settings keep free. Empty
    when there is room, when it is not checked (0), or when the folder can't be read (its own error tells)."""
    keep = float(SETTINGS.get("min_free_gb") or 0)
    try:
        free = shutil.disk_usage(DOWNLOADS).free / 1e9
    except OSError:
        return ""
    if not keep or free >= keep:
        return ""
    return f"Only {free:.1f} GB free in the downloads folder, less than the {keep:g} GB to keep free: nothing is downloaded until there is room"


def fallback_profiles(show: dict, config: dict) -> list[str]:
    """The profiles to log in with when the series' own is refused (its fallback_profiles, in order), never its own."""
    dl, _ = stacked(show, config)
    own = str(dl.get("--profile") or "default")
    return [p for p in re.split(r"[,\s]+", str(show.get("fallback_profiles") or "")) if p and p != own]


def accepted_audio(show: dict, config: dict) -> list[str]:
    """The audio languages a download must have one of (fr, en): the series' own, else the settings'; none
    set, the language asked for first, as before."""
    raw = show.get("audio_accept") or (config.get("settings") or {}).get("audio_accept") or ""
    accepted = [c.strip().lower() for c in re.split(r"[,\s]+", str(raw)) if c.strip()]
    return accepted or [want for want in [wanted_audio(show, config)] if want]


def preferred_audio(show: dict, config: dict) -> str:
    """The audio language an episode is got again in once it comes (fr): the series' own, else the settings'."""
    return str(show.get("audio_prefer") or (config.get("settings") or {}).get("audio_prefer") or "").strip().lower()


def lacks_preferred(out: Path, show: dict, config: dict) -> str:
    """The preferred audio language when the download lacks it (read before Sonarr moves the file), else ""."""
    prefer = preferred_audio(show, config)
    if not prefer:
        return ""
    langs = set().union(*(audio_languages(f) for f in out.rglob("*.mkv")))
    return prefer if langs and not speaks(langs, [prefer]) else ""


UPGRADES_FILE = DATA / "upgrades.json"  # episode id -> the preferred language it waits for, since when
upgrades_lock = threading.Lock()


def library_file(sonarr_path: str) -> Path:
    """A library file, as Sonarr names it, where Unshacklarr reads it (the two roots of Settings)."""
    root, local = str(SETTINGS.get("library_sonarr_root") or "").rstrip("/\\"), str(SETTINGS.get("library_local_root") or "").rstrip("/\\")
    if root and local and (sonarr_path == root or sonarr_path.startswith(root + "/") or sonarr_path.startswith(root + "\\")):
        return Path(local + sonarr_path[len(root):].replace("\\", "/"))
    return Path(sonarr_path)


def add_track(episode_id: int, want: str) -> bool:
    """The preferred audio added to the episode's file, not the whole episode again (upgrade_mode add_track): its
    audio only is downloaded, joined to the library's file with mkvmerge, and Sonarr imports the joined file in its
    place. False when that can't be done (the file out of reach, no audio came, lengths that differ): the caller
    then downloads the episode again as before."""
    config = read_file()
    ep = chosen_episodes([episode_id])[0]
    show = (config.get("series") or {}).get(ep["series"]["tvdbId"]) or {}
    if not ep.get("episodeFileId") or not show.get("service"):
        return False
    have = library_file(sonarr_get(f"episodefile/{ep['episodeFileId']}")["path"])
    if not have.is_file():
        print(f"Add the audio: {have} is out of reach here, the episode is downloaded again", flush=True)
        return False
    out = episode_folder(ep)
    sxxeyy = f"S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}"
    service_sxxeyy = service_episode(show, ep["seasonNumber"], ep["episodeNumber"]) or sxxeyy
    label = f"{ep['series']['title']} {sxxeyy}"
    with episode_lock(out) as mine:
        if not mine or out.exists():
            return False
        run = EpisodeRun(ep, show, "upgrade", service_sxxeyy)
        try:
            run.say(f"{label}: adding the {want} audio from {show['service']} to the file it has")
            request = download_request(show, config, service_sxxeyy, out)
            for k in ("video_only", "subs_only", "no_audio", "lang", "a_lang", "require_audio", "s_lang", "require_subs"):
                request.pop(k, None)
            request.update(audio_only=True, a_lang=[want])
            run_job_retrying(request, run)
            got = videos_in(out) + [f for f in out.rglob("*.mka")]
            if not got:
                raise RuntimeError(f"No {want} audio came from {show['service']}")
            lengths = [duration_of(have), duration_of(got[0])]
            if all(lengths) and abs(lengths[0] - lengths[1]) > 2:
                raise RuntimeError(f"The {want} audio lasts {lengths[1]:.0f} s, the file {lengths[0]:.0f} s: not joined")
            joined = out / have.name
            run.step("joining")
            cmd = ["mkvmerge", "-q", "-o", str(joined), str(have), "--no-video", "--no-subtitles", "--no-chapters", "--no-attachments", str(got[0])]
            done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            if done.returncode > 1:
                raise RuntimeError(f"mkvmerge could not add the audio: {done.stdout.strip()[-300:]}")
            for f in got:
                f.unlink(missing_ok=True)
            run.card["size"] = joined.stat().st_size
            run.step("importing")
            import_episode(ep, out, replace=True)
            note_upgrade(ep, show, service_sxxeyy, "")
            run.say(f"{label}: {want} audio added, the file imported again")
            run.step("done")
            run.finish("downloaded", "", f"{want} audio added to the file")
            notify(config.get("notifications") or {}, "success", f"Audio added: {label}", f"The {want} audio track was added to the file.")
            return True
        except (RuntimeError, JobFailed, UnshackleError, ValueError, subprocess.CalledProcessError, requests.RequestException) as e:
            run.say(f"{label}: {e}")
            run.finish("failed", str(e), f"The files are in {seen_by('sonarr_downloads', out)}")
            return True  # tried: the next day's check tries again, not a second download now
        finally:
            if not videos_in(out):
                shutil.rmtree(out, ignore_errors=True)


def duration_of(path: Path) -> float:
    """A file's length in seconds, as mkvmerge reads it (0 when it can't tell)."""
    info = subprocess.run(["mkvmerge", "-J", str(path)], stdout=subprocess.PIPE, text=True)
    return (json.loads(info.stdout or "{}").get("container", {}).get("properties", {}).get("duration") or 0) / 1e9


def note_upgrade(ep: dict, show: dict, service_sxxeyy: str, lacking: str) -> None:
    """An episode imported without its preferred language is watched for it; one with it is no longer."""
    with upgrades_lock:
        watched = read_json(UPGRADES_FILE, {})
        key = str(ep["id"])
        if not lacking:
            if watched.pop(key, None) is None:
                return
        else:
            since = (watched.get(key) or {}).get("since") or datetime.now(timezone.utc).isoformat()  # a try again keeps its start
            watched[key] = {"tvdb": ep["series"]["tvdbId"], "label": f"{ep['series']['title']} S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}",
                            "service": show["service"], "serviceEpisode": service_sxxeyy, "want": lacking, "since": since,
                            "checked": datetime.now(timezone.utc).isoformat()}
        write_atomic(UPGRADES_FILE, json.dumps(watched))


def accepted_subs(show: dict, config: dict) -> list[str]:
    """The subtitle languages a download must have one of (fr): the series' own, else the settings'; none: not checked."""
    raw = show.get("subs_accept") or (config.get("settings") or {}).get("subs_accept") or ""
    return [c.strip().lower() for c in re.split(r"[,\s]+", str(raw)) if c.strip()]


def speaks(tags, accepted: list[str]) -> bool:
    """Whether a track language among `tags` is one accepted: fr takes fr-FR and fr-CA; fr-CA takes fr-CA, and a
    plain fr too (a file's tag says no region: it may be it), never fr-FR."""
    have = {LANGS.get(t.lower(), t.lower()) for t in tags if t}
    return any(a == h or (a.split("-")[0] == h.split("-")[0] and ("-" not in a or "-" not in h)) for a in accepted for h in have)


NOT_LISTED = re.compile(r"No (available )?episodes found", re.IGNORECASE)  # list-tracks: the episode is not on the service


def list_tracks(show: dict, config: dict, request: dict, ladder: dict | None = None) -> list[dict]:
    """The episode's tracks on the service, nothing downloaded: one entry per part ([] when the service has no such
    episode). For a quality ladder, the ranges and codecs of its steps are asked for: a service lists only those."""
    _, own = stacked(show, config)
    keep = {"service", "title_id", "profile", "proxy", "no_proxy", "cdm_type",
            *options.to_params(own, options.service_specs(service_entry(show["service"], config)))}
    asked = {k: v for k, v in request.items() if k in keep}
    if ladder:
        ranges = {step.get("range") or "" for step in ladder["steps"]}
        codecs = {step.get("codec") or "" for step in ladder["steps"]}
        asked["range_"] = sorted(RANGES if "" in ranges else ranges)
        if "" not in codecs:
            asked["vcodec"] = sorted(codecs)
    try:  # list-tracks as the official serve has it: the same episode, profile, proxy and service options
        found = backend_for(show["service"], config).call("POST", "/api/list-tracks", json={**asked, "wanted": request["wanted"]})
    except UnshackleError as e:
        if NOT_LISTED.search(str(e)):
            return []
        raise
    return found.get("episodes") if "episodes" in found else [found]


def tracks_on_service(show: dict, config: dict, request: dict) -> dict[str, set[str]] | None:
    """The audio and subtitle languages the service offers for the episode, nothing downloaded (forced subtitles,
    a few lines only, left out); None when it can't tell (a remote service, an error, no answer): the download then
    goes on, checked once it is in."""
    if request.get("remote"):
        return None
    try:
        episodes = list_tracks(show, config, request)
    except UnshackleError as e:
        trace(f"tracks before the download: not told ({e})")
        return None
    if not episodes:
        return None
    langs = lambda kind: {str(t.get("language")) for ep in episodes for t in ep.get(kind) or []  # noqa: E731
                          if t.get("language") and not t.get("forced")}
    return {"audio": langs("audio"), "subtitles": langs("subtitles")}


# Quality ladders: steps tried in order against the episode's video tracks; the first one a track fits is downloaded,
# nothing outside them. A step: a codec and a range (empty: any), a height from min to max (0: no limit).
CODECS = ("AVC", "HEVC", "AV1", "VP9", "VP8", "VC1")  # Unshackle's names: H.264, H.265…
RANGES = ("SDR", "HDR10", "HDR10P", "DV", "HLG")
BUILTIN_LADDERS = [
    {"name": "1080p", "steps": [{"codec": "AVC", "range": "SDR", "min": 1080, "max": 1080}, {"codec": "HEVC", "range": "SDR", "min": 1080, "max": 1080},
                                {"codec": "AVC", "range": "SDR", "min": 720, "max": 720}, {"codec": "HEVC", "range": "SDR", "min": 720, "max": 720}]},
    {"name": "4K, then 1080p", "steps": [{"codec": "", "range": "DV", "min": 2160, "max": 0}, {"codec": "", "range": "HDR10", "min": 2160, "max": 0},
                                         {"codec": "", "range": "SDR", "min": 2160, "max": 0}, {"codec": "", "range": "SDR", "min": 1080, "max": 1080}]},
    {"name": "Archival", "steps": [{"codec": "", "range": "", "min": 0, "max": 0}]},
]


def ladders(config: dict) -> list[dict]:
    return config["quality_ladders"] if "quality_ladders" in config else BUILTIN_LADDERS


def ladder_of(show: dict, config: dict) -> dict | None:
    """The series' ladder, else its service's, else the settings' one; "off" at a level: none."""
    name = (show.get("ladder") or ((config.get("service_defaults") or {}).get(show["service"]) or {}).get("ladder")
            or (config.get("settings") or {}).get("quality_ladder") or "")
    return next((lad for lad in ladders(config) if lad["name"] == name), None) if name != "off" else None


HEIGHTS = (4320, 2160, 1440, 1080, 720, 576, 540, 480, 360, 240)  # the classes a picture is called by


def eq_height(track: dict) -> int:
    """A track's class, as a ladder reads it: its height, or its width's at 16:9, each taken to the class within 2%
    of it (a 1920x800 film and a 2:1 1920x960 series are 1080p, Apple's 1918x802 too, a 3840x1920 one 2160p)."""
    def classed(value: float) -> int:
        return next((h for h in HEIGHTS if abs(value - h) <= h * 0.02), int(value))
    return max(classed(int(track.get("height") or 0)), classed(int(track.get("width") or 0) * 9 / 16))


def fits(step: dict, track: dict) -> bool:
    height = eq_height(track)
    return ((not step.get("codec") or track.get("codec") == step["codec"]) and (not step.get("range") or track.get("range") == step["range"])
            and height >= int(step.get("min") or 0) and (not step.get("max") or height <= int(step["max"])))


def climb(ladder: dict, episodes: list[dict]) -> tuple[int, dict] | None:
    """The first step every part of the episode has a track for, and its best track there (height, then bitrate)."""
    for number, step in enumerate(ladder["steps"], 1):
        found = [[t for t in ep.get("video") or [] if fits(step, t)] for ep in episodes]
        if found and all(found):
            return number, max(found[0], key=lambda t: (eq_height(t), int(t.get("bitrate") or 0)))
    return None


def step_of(ladder: dict, track: dict) -> int:
    """Where a track stands on a ladder: the index of the first step it fits, len(steps) when none."""
    return next((i for i, step in enumerate(ladder["steps"]) if fits(step, track)), len(ladder["steps"]))


def step_label(step: dict) -> str:
    height = f"{step.get('min') or 0}-{step['max']}p" if step.get("max") and step.get("max") != step.get("min") else \
        f"{step['max']}p" if step.get("max") else f"{step['min']}p and up" if step.get("min") else "any height"
    return " ".join(filter(None, [height, step.get("codec") or "any codec", step.get("range") or "any range"]))


def track_label(t: dict) -> str:
    return f"{eq_height(t) or '?'}p {t.get('codec') or '?'} {t.get('range') or '?'}"


def apply_ladder(show: dict, config: dict, request: dict) -> None:
    """The series' quality ladder, resolved against the episode's real tracks: the first step that has one sets
    the quality, codec and range asked for. None does: the episode fails, nothing outside the ladder downloaded."""
    ladder = ladder_of(show, config)
    if not ladder:
        return
    if request.get("remote"):
        raise ValueError(f"The quality ladder {ladder['name']} needs the episode's track list, which a --remote download does not give")
    episodes = list_tracks(show, config, request, ladder)
    if not episodes:  # ponytail: not on the service (yet): the download finds nothing either, and says so as usual
        return
    found = climb(ladder, episodes)
    if not found:
        have = sorted({track_label(t) for ep in episodes for t in ep.get("video") or []})
        raise ValueError(f"None of the quality ladder {ladder['name']}'s steps is on {show['service']} (it has {', '.join(have) or 'no video'})")
    number, track = found
    # its own height: Unshackle's --quality takes a track by it exactly (its 16:9 class may be no exact match there)
    request.update(quality=[int(track.get("height") or 0) or eq_height(track)], vcodec=[track["codec"]], **({"range": [track["range"]]} if track.get("range") else {}))
    trace(f"quality ladder {ladder['name']}: step {number} ({step_label(ladder['steps'][number - 1])}), {track_label(track)}")
    # its language order picks among the episode's tracks, unless the series or its service ask for languages themselves
    for key, kind, asked in (("a_lang", "audio", ("lang", "a_lang", "require_audio")), ("s_lang", "subtitles", ("s_lang", "require_subs"))):
        if ladder.get(kind) and not any(request.get(k) for k in asked) and (lang := first_language(ladder[kind], episodes[0].get(kind) or [])):
            request[key] = [lang]
            trace(f"quality ladder {ladder['name']}: {kind} in {lang}")


def first_language(order: list[str], tracks: list[dict]) -> str | None:
    """The first language of the order the episode has a track in (forced subtitles aside): exactly, else by its
    base language (en takes en-GB)."""
    have = [str(t["language"]) for t in tracks if t.get("language") and not t.get("forced")]
    base = lambda code: code.lower().split("-")[0]  # noqa: E731
    for same in (lambda a, b: a.lower() == b.lower(), lambda a, b: base(a) == base(b)):
        for wanted in order:
            if hit := next((h for h in have if same(h, wanted)), None):
                return hit
    return None


def audio_languages(path: Path, kind: str = "audio") -> set[str]:
    """The languages of a file's audio (or subtitles: forced ones left out), as mkvmerge reads them."""
    info = subprocess.run(["mkvmerge", "-J", str(path)], stdout=subprocess.PIPE, text=True)
    tracks = json.loads(info.stdout or "{}").get("tracks") or []
    found = set()
    for t in tracks:
        if t.get("type") == kind and not (t.get("properties") or {}).get("forced_track"):
            props = t.get("properties") or {}
            for code in (props.get("language_ietf"), props.get("language")):
                if code:
                    found.add(code.split("-")[0].lower())
    return found


def check_audio(out: Path, accepted: list[str]) -> None:
    """Refuse a download with none of the accepted audio languages: a VO-only file in the library is
    worse than no file (best_available lets unshackle take what there is)."""
    if not accepted:
        trace("audio: no language asked for, not checked")
        return
    for f in out.rglob("*.mkv"):
        langs = audio_languages(f)
        trace(f"audio of {f.name}: {', '.join(sorted(langs)) or 'no language tag'} (accepted: {', '.join(accepted)})")
        if langs and not speaks(langs, accepted):  # a file without language tags says nothing either way
            raise RuntimeError(f"No {' or '.join(accepted)} audio in the download (it has {', '.join(sorted(langs))}): not imported")


def check_subs(out: Path, accepted: list[str]) -> None:
    """Refuse a download with none of the accepted subtitle languages (forced ones do not count)."""
    if not accepted:
        return
    for f in out.rglob("*.mkv"):
        langs = audio_languages(f, "subtitles")
        trace(f"subtitles of {f.name}: {', '.join(sorted(langs)) or 'none'} (accepted: {', '.join(accepted)})")
        if not speaks(langs, accepted):
            raise RuntimeError(f"No {' or '.join(accepted)} subtitles in the download (it has {', '.join(sorted(langs)) or 'none'}): not imported")


class Missing(Exception):
    """The service has the episode, in none of the audio (or subtitle) languages accepted."""

    def __init__(self, kind: str, accepted: list[str], found: set[str]):
        super().__init__(kind)
        self.kind, self.accepted, self.found = kind, accepted, found


class Kept(Exception):
    """Sonarr keeps the file it has: the download is no better."""


def sonarr_get(path: str, **params):
    r = requests.get(f"{SONARR}/api/v3/{path}", headers=HEADERS, params=params, timeout=120)
    trace(f"Sonarr GET {path} {params or ''} → {r.status_code}, {len(r.content)} bytes", debug=True)
    r.raise_for_status()
    return r.json()


def quality_ranks(profile: dict) -> dict[int, int]:
    """Quality id -> rank in the series' profile, lowest first; a group shares one rank."""
    ranks = {}
    for rank, item in enumerate(profile["items"]):
        for quality in [item.get("quality")] + [sub.get("quality") for sub in item.get("items") or []]:
            if quality:
                ranks[quality["id"]] = rank
    return ranks


def better_than_library(ep: dict, candidate: dict) -> tuple[bool, str]:
    """Whether the download beats the episode's file, as the series' quality profile ranks them."""
    existing = sonarr_get(f"episodefile/{ep['episodeFileId']}")
    ranks = quality_ranks(sonarr_get(f"qualityprofile/{ep['series']['qualityProfileId']}"))
    new_q, old_q = candidate["quality"]["quality"], existing["quality"]["quality"]
    new_key = (ranks.get(new_q["id"], -1), candidate.get("customFormatScore") or 0)
    old_key = (ranks.get(old_q["id"], -1), existing.get("customFormatScore") or 0)
    why = f"the library has {old_q['name']} (score {old_key[1]}), the download is {new_q['name']} (score {new_key[1]})"
    return new_key > old_key, why


def import_episode(ep: dict, out: Path, replace: bool = False) -> None:
    """Import by series and episode id: Sonarr need not recognise the file name.

    An episode that already has a file is only replaced by a better one, unless
    `replace` says so. Sonarr's manual import would replace it whatever its quality.
    """
    folder = seen_by("sonarr_downloads", out)
    # No seriesId here: with one, Sonarr lists the series' library folder instead of this one.
    inside = folder.replace("\\", "/") + "/"
    candidates = [c for c in sonarr_get("manualimport", folder=folder, filterExistingFiles="false")
                  if c["path"].replace("\\", "/").startswith(inside)]
    if not candidates:
        raise RuntimeError(f"Sonarr sees no video file in {folder}")
    if len(candidates) > 1:  # one episode is one file: the quality check could not vouch for the others
        raise RuntimeError(f"Sonarr sees {len(candidates)} video files in {folder}, for one episode: nothing imported")
    for c in candidates:
        rejected = "; ".join(r.get("reason", "") for r in c.get("rejections") or [])
        trace(f"Sonarr sees {Path(c['path']).name}: {c['quality']['quality']['name']}" + (f" (rejections: {rejected})" if rejected else ""))
    # As Sonarr has it now, not as when the list was made: it may have got a file during the download
    now = sonarr_get(f"episode/{ep['id']}")
    ep.update(hasFile=now.get("hasFile"), episodeFileId=now.get("episodeFileId"))
    if ep.get("hasFile") and ep.get("episodeFileId") and not replace:
        better, why = better_than_library(ep, candidates[0])
        trace(f"{'better' if better else 'not better'}: {why}")
        if not better:
            raise Kept(why)
    files = [
        {
            "path": c["path"],
            "seriesId": ep["seriesId"],
            "episodeIds": [ep["id"]],
            "quality": c["quality"],
            "languages": c["languages"],
            "releaseGroup": c.get("releaseGroup"),
        }
        for c in candidates
    ]
    old_file = ep.get("episodeFileId") if ep.get("hasFile") else None
    for attempt in range(1, IMPORT_ASKS + 1):
        try:
            r = requests.post(
                f"{SONARR}/api/v3/command",
                headers=HEADERS,
                json={"name": "ManualImport", "files": files, "importMode": "move"},
                timeout=120,  # as long as its reads: a Sonarr busy moving another big file answers late
            )
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            # It may have taken it and imported all the same: the episode's file says, before asking again
            time.sleep(IMPORT_RETRY_WAIT)
            now = sonarr_get(f"episode/{ep['id']}")
            if now.get("hasFile") and now.get("episodeFileId") != old_file:
                trace(f"Sonarr did not answer the import ({type(e).__name__}), but the episode has its new file: imported")
                return
            if attempt == IMPORT_ASKS:
                raise RuntimeError(f"Sonarr did not answer the import {IMPORT_ASKS} times ({no_credentials(str(e))})") from e
            trace(f"Sonarr did not answer the import ({type(e).__name__}): asked again ({attempt + 1} of {IMPORT_ASKS})")
    r.raise_for_status()
    trace(f"Sonarr's import asked (command {r.json().get('id')}), moving {len(files)} file{'s' if len(files) != 1 else ''}{', replacing its file' if replace else ''}")
    wait_for_import(ep, r.json().get("id"), old_file)


IMPORT_WAIT, IMPORT_POLL = 300, 3  # seconds
IMPORT_ASKS, IMPORT_RETRY_WAIT = 3, 60  # an import Sonarr does not answer: asked so many times, a minute apart


def wait_for_import(ep: dict, command_id: int | None, old_file: int | None) -> None:
    """Follow Sonarr's import to its end and check the episode got the new file: asking is not
    importing (a full disk, a permission, a path Sonarr cannot reach all fail quietly)."""
    if not command_id:
        raise RuntimeError("Sonarr gave no command to follow: the import is unconfirmed")
    deadline = time.monotonic() + IMPORT_WAIT
    while True:
        command = sonarr_get(f"command/{command_id}")
        if command.get("status") in ("completed", "failed", "aborted", "cancelled", "orphaned"):
            break
        if time.monotonic() > deadline:
            raise RuntimeError(f"Sonarr's import is still running after {IMPORT_WAIT // 60} min: check Activity in Sonarr")
        time.sleep(IMPORT_POLL)
    if command.get("status") != "completed":
        why = command.get("exception") or command.get("message") or command.get("status")
        raise RuntimeError(f"Sonarr's import failed: {why}")
    now = sonarr_get(f"episode/{ep['id']}")
    trace(f"Sonarr's import done: {command.get('message') or 'completed'}; episode file {now.get('episodeFileId')}")
    if not now.get("hasFile") or now.get("episodeFileId") == old_file:
        raise RuntimeError(f"Sonarr finished the import but the episode has no new file: {command.get('message') or 'see Activity in Sonarr'}")


seen_lock = threading.Lock()


def in_download_window(now: datetime | None = None) -> bool:
    """Within the hours the automatic sync downloads in (download_from to download_to, local time, over midnight or not);
    no window set: always."""
    start, end = str(SETTINGS.get("download_from") or ""), str(SETTINGS.get("download_to") or "")
    if not start or not end or start == end:
        return True
    at = (now or datetime.now(timezone.utc)).astimezone(LOCAL).strftime("%H:%M")
    return start <= at < end if start < end else at >= start or at < end


LEARN_AFTER = 3  # episodes seen coming out at the same time before a release time is set from them
on_release_learned = None  # web.py sets it: writes the series' release time in config.yaml


def learn_release(ep: dict, show: dict, settings: dict) -> None:
    """A series with no release time gets one once its episodes are seen coming out at the same time (release_learn):
    the page's suggestion, applied and told. One set by hand is never changed."""
    if not SETTINGS.get("release_learn") or show.get("release_time") or on_release_learned is None:
        return
    tvdb = ep["series"]["tvdbId"]
    found = suggest_release([e for e in read_json(SEEN_FILE, {}).values() if e.get("tvdbId") == tvdb])
    if not found or found["episodes"] < LEARN_AFTER:
        return
    on_release_learned(tvdb, found["time"], found["day"])
    when = {0: "the day it airs", 1: "the day after"}.get(found["day"], f"{-found['day']} days before")
    notify_series(show, settings, "success", f"Release time set: {ep['series']['title']}",
           f"{found['episodes']} episodes came out on {show['service']} at {found['time']}, {when}. New episodes are now downloaded at that time. "
           "You can change it on the series page.")


def note_availability(ep: dict, out: Path, found: bool, when: datetime) -> None:
    """Remember when an episode was not out yet, and when it first was: over a few episodes,
    that brackets the time the service publishes the series."""
    if not ep.get("airDateUtc"):
        return
    with seen_lock:
        seen = read_json(SEEN_FILE, {})
        entry = seen.setdefault(out.name, {"tvdbId": ep["series"]["tvdbId"], "aired": ep["airDateUtc"]})
        if found:
            entry.setdefault("available", when.isoformat())
        elif "available" not in entry:
            entry["not_yet"] = when.isoformat()
        write_atomic(SEEN_FILE, json.dumps(seen))


def suggest_release(entries: list[dict]) -> dict | None:
    """The release time the observations point to (local time, on 5 min) and its day after
    airing, from the episodes seen out: after the latest "not yet", by the first "out"."""
    found = [e for e in entries if e.get("available")]
    if not found:
        return None
    minutes = lambda t: t.hour * 60 + t.minute
    local = lambda iso: parse_time(iso).astimezone(LOCAL)
    days = [(local(e["available"]).date() - local(e["aired"]).date()).days for e in found]
    day = max(set(days), key=days.count)
    same = [e for e, d in zip(found, days) if d == day]
    upper = min(minutes(local(e["available"])) for e in same)
    lows = [minutes(local(e["not_yet"])) for e in same
            if e.get("not_yet") and (local(e["not_yet"]).date() - local(e["aired"]).date()).days == day]
    lower = max(lows) if lows else None
    if lower is not None and lower < upper:
        at = min(-(-(lower + 1) // 5) * 5, upper)  # just after it was last missing
    else:
        at = upper // 5 * 5
    hhmm = lambda m: f"{m // 60 % 24:02}:{m % 60:02}"
    return {"time": hhmm(at), "day": max(min(day, 1), -EARLY_DAYS_MAX), "episodes": len(same),
            "between": [hhmm(lower) if lower is not None else None, hhmm(upper)]}


FOLDER = re.compile(r"unshackle-(\d+)-S(\d+)E(\d+)")


def leftovers() -> list[dict]:
    """What waits in the downloads folder: Unshacklarr's own episode folders still holding a video."""
    found = []
    for d in sorted(DOWNLOADS.glob("unshackle-*")) if DOWNLOADS.is_dir() else []:
        m = FOLDER.fullmatch(d.name)
        files = videos_in(d) if m and d.is_dir() else []
        if not files or d.name in busy_episodes:
            continue
        found.append({"folder": d.name, "tvdbId": int(m.group(1)), "sxxeyy": f"S{int(m.group(2)):02}E{int(m.group(3)):02}",
                      "files": [f.name for f in files], "size": sum(f.stat().st_size for f in files),
                      "since": max(f.stat().st_mtime for f in files)})
    return found


def leftover_path(folder: str) -> Path:
    if not FOLDER.fullmatch(folder or ""):
        raise RuntimeError("Not one of Unshacklarr's folders")
    return DOWNLOADS / folder


def import_leftover(folder: str) -> None:
    """Hand a waiting folder to Sonarr for good, replacing its file whatever the quality."""
    out = leftover_path(folder)
    m = FOLDER.fullmatch(folder)
    tvdb, season, number = int(m.group(1)), int(m.group(2)), int(m.group(3))
    series = sonarr_get("series", tvdbId=tvdb)
    if not series:
        raise RuntimeError(f"Sonarr has no series with TVDB id {tvdb}")
    ep = next((e for e in sonarr_get("episode", seriesId=series[0]["id"]) if e["seasonNumber"] == season and e["episodeNumber"] == number), None)
    if not ep:
        raise RuntimeError(f"Sonarr has no S{season:02}E{number:02} for this series")
    ep["series"] = series[0]
    import_episode(ep, out, replace=True)
    for card in sorted(RUNS_DIR.glob(f"*-{tvdb}-S{season:02}E{number:02}.json"), reverse=True):
        c = json.loads(card.read_text())
        if c.get("outcome") == "kept":  # its history line: imported after all
            by_hand = str(c.get("cause") or "").startswith("Download only")
            c.update(outcome="downloaded", detail="Imported by hand" if by_hand else "Imported anyway: it replaced the library's file")
            write_atomic(card, json.dumps(c))
        break
    if not videos_in(out):
        shutil.rmtree(out, ignore_errors=True)


def download_only(show: dict, config: dict) -> bool:
    """Downloaded but never imported: the series says so (true or false), else the settings."""
    own = show.get("download_only")
    return bool(own if own is not None else (config.get("settings") or {}).get("download_only"))


def waits_for_hand(tvdb: int, config: dict) -> bool:
    """A series downloaded only: what waits of it is for an import by hand, never deleted on its own."""
    show = (config.get("series") or {}).get(tvdb)
    return bool(show) and download_only(show, config)


def clean_leftovers(now: float | None = None) -> list[str]:
    """Delete what has waited longer than leftovers_days: a kept file nobody came back for. A download only
    series' episodes wait for an import by hand: kept."""
    days = float(SETTINGS.get("leftovers_days") or 0)
    if days <= 0:
        return []
    now, config = now or time.time(), read_file()
    gone = [l["folder"] for l in leftovers() if now - l["since"] > days * 86400 and not waits_for_hand(l["tvdbId"], config)]
    for folder in gone:
        shutil.rmtree(DOWNLOADS / folder, ignore_errors=True)
    return gone


sweep_lock = threading.Lock()


def main(episode_ids: list[int] | None = None, replace: bool = False, kind: str = "manual", numbering: dict | None = None,
         batch: str | None = None) -> int:
    config = read_file()
    settings = config.get("notifications") or {}
    try:
        if episode_ids:  # picked by hand or a release burst: the per-episode lock is enough
            return sync(config, settings, chosen_episodes(episode_ids), manual=True, replace=replace, kind=kind, numbering=numbering, batch=batch)
        if not sweep_lock.acquire(blocking=False):  # one automatic sweep at a time
            print("A sync is already running, nothing to do.")
            return 0
        try:
            if not in_download_window():
                print("Outside the download window (Settings, Automation): the sync waits for it.", flush=True)
                return 0
            now, plans = datetime.now(timezone.utc), broadcast_plans(config)
            dated = broadcast_dated(plans)
            own = lambda ep: ep["id"] in dated  # its own schedule dates it
            wanted = [ep for ep in missing_episodes() if not own(ep)]
            seen = {ep["id"] for ep in wanted}
            wanted += [ep for ep in early_episodes(config, now) if ep["id"] not in seen and not own(ep)]
            wanted += [ep for ep in broadcast_episodes(config, now - timedelta(days=AUTO_DAYS + 1), now + timedelta(days=EARLY_DAYS_MAX + 1), plans)
                       if not ep.get("hasFile") and ep.get("monitored", True) and ep["series"].get("monitored", True)]
            return sync(config, settings, wanted, kind="auto")
        finally:
            sweep_lock.release()
    except Exception as e:
        notify(settings, "error", "Unshacklarr sync stopped", no_credentials(f"{type(e).__name__}: {e}"))
        raise


# A series' numbering and file names: what a download's own numbering replaces, whole
NUMBERING = ("season_map", "season_offset", "season_offset_from", "episode_offset", "episode_map", "file_name", "join_parts", "parts", "episode_name")


def sync(config: dict, settings: dict, episodes, manual: bool = False, replace: bool = False, kind: str = "auto",
         numbering: dict | None = None, batch: str | None = None) -> int:
    series = config.get("series") or {}
    if numbering is not None:  # this download's own, in place of the series': the file keeps its own
        series = {k: {**{o: v for o, v in show.items() if o not in NUMBERING}, **numbering} for k, show in series.items()}
        config = {**config, "series": series}
    episodes = list(episodes)
    # Episodes picked together are one job in Activity: their cards, and one output for them all
    # (a job's episode queued again once the job was over goes on in that same job)
    batch = batch or (f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}" if kind != "auto" and len(episodes) > 1 else None)
    job = {"episodes": episodes, "stubs": {}, "closed": False}
    if kind != "auto":  # picked by hand: the ones after the first show as queued in Activity
        for ep in episodes:
            show = series.get(ep["series"]["tvdbId"]) or {}
            if show.get("service") and ep["id"] not in waiting:
                with jobs_lock:
                    waiting[ep["id"]] = job["stubs"][ep["id"]] = queued_card(ep, show, kind, batch)
    if batch:
        with jobs_lock:
            running_jobs[batch] = job  # a job's episode started again once it closed takes its place here
    try:
        return run_episodes(config, settings, episodes, manual, replace, kind, batch, numbering)
    finally:
        with jobs_lock:
            for episode_id in job["stubs"]:
                waiting.pop(episode_id, None)
                unqueued.discard(episode_id)
        with jobs_lock:
            if running_jobs.get(batch) is job:
                running_jobs.pop(batch)
                paused_jobs.discard(batch)


def run_episodes(config: dict, settings: dict, episodes: list, manual: bool, replace: bool, kind: str, batch: str | None = None,
                 numbering: dict | None = None) -> int:
    series = config.get("series") or {}
    failures, started = 0, time.monotonic()

    def finish(run: EpisodeRun, ep: dict, show: dict, out: Path, label: str, sxxeyy: str) -> None:
        """What follows a download: parts joined, the file named, its audio checked, Sonarr's import."""
        nonlocal failures
        if kind in ("auto", "burst"):  # a click says nothing of when it came out
            note_availability(ep, out, True, parse_time(run.card["started"]))
            learn_release(ep, show, settings)
        try:
            def on_step(step: str, parts: int) -> None:
                run.card["parts"] = parts
                run.step(step)

            finalize(
                out,
                sxxeyy,
                show.get("file_name") or ep["series"]["title"],
                show.get("join_parts", True),
                show.get("episode_name") or "joined",
                on_step,
            )
            check_audio(out, accepted_audio(show, config))
            check_subs(out, accepted_subs(show, config))
            lacking = lacks_preferred(out, show, config)  # read now: Sonarr moves the file away
            run.card["size"] = sum(f.stat().st_size for f in videos_in(out))  # for the job's sum
            if download_only(show, config):
                where = seen_by("sonarr_downloads", out)
                run.say(f"{label}: downloaded but not imported (download only). It waits in {where}")
                run.step("done")
                run.finish("kept", "Download only: not imported", f"The download waits in {where}")
                notify_series(show, settings, "success", f"Downloaded, not imported: {label}",
                       f"Download only: the file waits in {where}. Import or delete it in Activity, Waiting in downloads.",
                       batch=batch, details=episode_details(ep, show, run))
                return
            run.step("importing")
            import_episode(ep, out, replace)
        except Kept as e:
            # Deliberate: the library keeps its better (or equal) file; ours waits in the downloads folder.
            run.say(f"{label}: not imported, the library keeps its file: {e}")
            run.finish("kept", f"Not better: {e}", f"The download waits in {seen_by('sonarr_downloads', out)}")
            notify_series(show, settings, "warning", f"Kept the existing file: {label}",
                   f"Not better: {e}.\nThe new download is in {seen_by('sonarr_downloads', out)}; delete it or import it by hand.", batch=batch)
            return
        except (RuntimeError, subprocess.CalledProcessError, requests.RequestException) as e:
            # The files stay in the downloads folder: nothing is imported, nothing is downloaded again.
            with counted:
                failures += 1
            run.say(f"{label}: {e}")
            run.finish("failed", str(e), f"The files are in {seen_by('sonarr_downloads', out)}")
            notify_series(show, settings, "error", f"Not imported: {label}", f"{e}\nThe files are in {seen_by('sonarr_downloads', out)}.", batch=batch)
            return
        if not videos_in(out):
            shutil.rmtree(out, ignore_errors=True)  # Sonarr moved the file: nothing left to keep
        note_upgrade(ep, show, run.card.get("serviceEpisode") or sxxeyy, lacking)
        if lacking:
            run.say(f"{label}: no {lacking} audio yet. The service is checked once a day, and the episode is downloaded again when it comes")
        run.say(f"{label}: downloaded and imported by Sonarr")
        run.step("done")
        imported_at[ep["id"]] = time.monotonic()
        run.finish("downloaded", "", "Imported by Sonarr")
        notify_series(show, settings, "success", f"Downloaded: {label}", "Imported by Sonarr.", batch=batch, details=episode_details(ep, show, run))

    def finishing_loop() -> None:
        """One episode at a time, in the order they were downloaded, while the next ones download."""
        nonlocal failures
        while (job := finishing.get()) is not None:
            run, lock = job[0], job[-1]
            current.run = run
            try:
                finish(*job[:-1])
            except Exception as e:  # never leave the next ones unfinished
                with counted:
                    failures += 1
                if not run.card.get("ended"):
                    run.say(f"{job[4]}: {type(e).__name__}: {e}")
                    run.finish("failed", f"{type(e).__name__}: {e}")
                    notify(settings, "error", f"Not imported: {job[4]}", f"{type(e).__name__}: {e}\nThe files are in {seen_by('sonarr_downloads', job[3])}.", batch=batch)
            finally:
                lock.__exit__(None, None, None)

    finishing: queue.Queue = queue.Queue()
    counted = threading.Lock()  # failures, counted by both
    finisher = threading.Thread(target=finishing_loop, daemon=True)
    finisher.start()

    try:
        for ep in job_episodes(episodes, batch):
            with jobs_lock:  # its turn; add_to_job must not slip in between
                if (waiting.get(ep["id"]) or {}).get("batch") == batch:  # its own stub, never another job's
                    waiting.pop(ep["id"], None)
                taken_out = ep["id"] in unqueued
                unqueued.discard(ep["id"])
            if taken_out:
                continue  # taken out of the queue
            show = series.get(ep["series"]["tvdbId"])
            if not show or not show.get("service") or not show.get("title"):
                continue
            if not manual and not wanted_automatically(show, ep, datetime.now(timezone.utc)):
                continue

            sxxeyy = f"S{ep['seasonNumber']:02}E{ep['episodeNumber']:02}"
            service_sxxeyy = service_episode(show, ep["seasonNumber"], ep["episodeNumber"])
            label = f"{ep['series']['title']} {sxxeyy}"
            if not service_sxxeyy:
                continue  # the episode offset puts it before the service's first episode
            out = episode_folder(ep)
            if out.exists():
                print(f"{label}: already in the downloads folder ({out.name}), waiting for Sonarr to import it")
                with episode_lock(out) as mine:
                    if manual and mine:  # asked for by hand: say why nothing happens, not a silent skip
                        run = EpisodeRun(ep, show, kind, service_sxxeyy, batch)
                        run.say(f"{label}: a download already waits in {seen_by('sonarr_downloads', out)}")
                        run.finish("failed", "A download of this episode already waits in the downloads folder",
                                   "Import it or delete it in Activity, Waiting in downloads, then try again")
                continue

            lock = episode_lock(out)  # held until the episode is finished, aside or not
            if not lock.__enter__():
                lock.__exit__(None, None, None)
                print(f"{label}: already being downloaded by another run")
                continue
            handed, run = False, None
            try:
                if imported_at.get(ep["id"], 0) > started and not replace:  # a release burst got it since the list was made
                    print(f"{label}: imported meanwhile, nothing to download")
                    continue
                if full := free_space_problem():  # the health alert tells it once; a click says why nothing happens
                    print(f"{label}: {full}", flush=True)
                    if manual:
                        run = EpisodeRun(ep, show, kind, service_sxxeyy, batch)
                        run.finish("failed", full, "Make room in the downloads folder, or lower the room kept free in Settings")
                    continue
                run = EpisodeRun(ep, show, kind, service_sxxeyy, batch)
                if numbering is not None:  # this download's own numbering: a retry asks the service the same again
                    run.card["numbering"] = numbering
                    run.save()
                on_service = f" as {service_sxxeyy}" if service_sxxeyy != sxxeyy else ""
                run.say(f"{label}: downloading from {show['service']}{on_service}")
                wanted_langs = {"audio": accepted_audio(show, config) if show.get("audio_accept") or (config.get("settings") or {}).get("audio_accept") else [],
                                "subtitles": accepted_subs(show, config)}

                def no_language(e: "Missing") -> None:
                    """Out, but in none of the languages accepted: not downloaded, tried again like an episode not out yet."""
                    langs, found = " or ".join(e.accepted), ", ".join(sorted(e.found)) or "none"
                    if e.kind == "audio":  # whole sentences, each its own: they are translated as they are
                        why, title = f"No {langs} audio on {show['service']} yet (it has {found})", f"No {langs} audio yet: {label}"
                    else:
                        why, title = f"No {langs} subtitles on {show['service']} yet (it has {found})", f"No {langs} subtitles yet: {label}"
                    run.say(f"{label}: {why}, not downloaded")
                    run.finish("unavailable", why)
                    if first_warning(f"{out.name}:{e.kind}"):
                        notify_series(show, settings, "warning", title,
                               f"{why}. The episode is tried again at each sync, and downloaded when one is there.", batch=batch, details=episode_details(ep, show))

                def ask(wanted: str, profile: str = "", use: dict | None = None) -> None:
                    use = use or show  # the series as it downloads: its own service, or its fallback
                    request = download_request(use, config, wanted, out)
                    if profile:  # a fallback profile, its login refused with the series' own
                        request["profile"] = profile
                    if any(wanted_langs.values()) and (tracks := tracks_on_service(use, config, request)) is not None:
                        for kind, accepted in wanted_langs.items():
                            if accepted and not speaks(tracks[kind], accepted):
                                raise Missing(kind, accepted, tracks[kind])
                    apply_ladder(use, config, request)
                    asked = {k: v for k, v in request.items() if k not in ("service", "title_id", "wanted", "output_dir", "debug")}
                    trace(f"asking for {request['service']} {request['title_id']} {wanted}, into {request['output_dir']}")
                    trace("options: " + (", ".join(f"{k}={options.HIDDEN.sub('//***@', str(v))}" for k, v in asked.items()) or "none"))  # no proxy password in the log
                    backend = backend_for(use["service"], config)
                    run.card["setup"] = setup_of(request, backend)
                    if backend.name:  # the serve run_job hands it to, and Activity's Stop cancels it on
                        run.card["backend"] = backend.name
                    run.save()
                    run_job_retrying(request, run)

                missed = None  # its own service has it in none of the languages accepted: the fallback's turn
                try:
                  try:
                    # Its number on the service may hold another episode (a same-named series elsewhere, seasons
                    # numbered apart): its title says so before anything is downloaded
                    if numbering is None and int(show.get("parts") or 0) <= 1 and sxxeyy not in (show.get("episode_map") or {}) \
                            and (wrong := wrong_number(show, ep, service_sxxeyy, run)) is not None:
                        if not wrong:
                            why = f"{service_sxxeyy} on {show['service']} is another episode, by its title"
                            run.say(f"{label}: {why}, not downloaded")
                            tell_failure(run, why, lambda: notify_series(show, settings, "error", f"Failed: {label}", f"{why}.\nCheck its Numbering, then try again.",
                                                                  batch=batch))
                            with counted:
                                failures += 1
                            run.finish("failed", why, "Check this series' Numbering (its episode table), then try again")
                            continue
                        how = "its absolute number" if wrong.get("match") == "absolute" else "its title"
                        run.say(f"{label}: {service_sxxeyy} on {show['service']} is a different episode; found by {how} as {wrong['service']} instead")
                        own = {k: show[k] for k in NUMBERING if k in show}  # a retry asks the same again
                        run.card.update(serviceEpisode=wrong["service"],
                                        numbering={**own, "episode_map": {**(own.get("episode_map") or {}), sxxeyy: wrong["service"]}})
                        service_sxxeyy = wrong["service"]
                    ask(service_sxxeyy)
                    error = cause = None
                    # Nothing under its number: the service may have it under another, found by its title
                    if not videos_in(out) and int(show.get("parts") or 0) <= 1 and (found := by_title(show, ep, service_sxxeyy, run)):
                        other = found["service"]
                        how = "its absolute number" if found.get("match") == "absolute" else "its title"
                        run.say(f"{label}: not on {show['service']} as {service_sxxeyy}, found by {how} as {other}")
                        service_sxxeyy = other
                        own = {k: show[k] for k in NUMBERING if k in show}  # a retry asks the same again
                        run.card.update(serviceEpisode=other, numbering={**own, "episode_map": {**(own.get("episode_map") or {}), sxxeyy: other}})
                        ask(other)
                  except Missing as e:
                    if not fallback_show(show):
                        raise
                    missed = e
                  # Nothing on its own service (or not in a language accepted): its fallback service, when it has one,
                  # with its own URL and numbering
                  if not videos_in(out) and (alt := fallback_show(show)) and (alt_sxxeyy := service_episode(alt, ep["seasonNumber"], ep["episodeNumber"])):
                    if missed:
                        run.say(f"{label}: no accepted language on {show['service']}, trying {alt['service']} as {alt_sxxeyy}")
                    else:
                        run.say(f"{label}: not on {show['service']}, trying {alt['service']} as {alt_sxxeyy}")
                    run.card.update(service=alt["service"], serviceEpisode=alt_sxxeyy, fallback=show["service"])
                    service_sxxeyy = alt_sxxeyy
                    ask(alt_sxxeyy, use=alt)
                    missed = None
                  elif missed:
                    raise missed
                except Missing as e:
                    no_language(e)
                    continue
                except JobFailed as e:
                    if e.status == "cancelled":  # stopped from Activity: not a failure, nothing to import
                        shutil.rmtree(out, ignore_errors=True)
                        run.say(f"\r\n{label}: stopped")
                        run.finish("stopped")
                        continue
                    error, cause = str(e), e.cause
                except (UnshackleError, ValueError) as e:
                    error = cause = str(e)

                # Its login refused and nothing came: the series' fallback profiles, in their order, until one logs in
                stopped = waits = False
                for profile in fallback_profiles(show, config) if error and LOGIN.search(cause or "") and not videos_in(out) else []:
                    run.say(f"{label}: {show['service']} refused the login, trying the profile {profile}")
                    try:
                        ask(service_sxxeyy, profile)
                        error = cause = None
                        run.card["profile"] = profile
                        run.say(f"{label}: logged in with the profile {profile}")
                        break
                    except Missing as e:  # logged in: the episode has none of the languages accepted yet
                        no_language(e)
                        waits = True
                        break
                    except JobFailed as e:
                        if e.status == "cancelled":
                            stopped = True
                            break
                        error, cause = str(e), e.cause
                    except (UnshackleError, ValueError) as e:
                        error = cause = str(e)
                    if not LOGIN.search(cause or ""):
                        break  # another failure than a login: another profile would not help
                if waits:
                    continue
                if stopped:
                    shutil.rmtree(out, ignore_errors=True)
                    run.say(f"\r\n{label}: stopped")
                    run.finish("stopped")
                    continue

                # A series whose episodes come in parts: one part out of two is not the episode.
                # Nothing is imported, and it is tried again like an episode not out yet.
                missing = ""
                expected = int(show.get("parts") or 0)
                if expected > 1 and "." not in service_sxxeyy and videos_in(out):
                    if (found := parts_in(out)) < expected:
                        missing = f" ({found} of {expected} parts)"
                        run.say(f"{label}: {found} of {expected} parts on {show['service']}, waiting for the rest")
                        shutil.rmtree(out, ignore_errors=True)

                if error and any(PART.search(f.name) for f in videos_in(out)):
                    # some parts came, the rest failed: never imported as the whole episode, kept aside
                    with counted:
                        failures += 1
                    where = seen_by("sonarr_downloads", out)
                    run.say(f"\r\n\x1b[31m{label}: Unshackle FAILED after {parts_in(out)} part(s)\x1b[0m\n{error}".replace("\n", "\r\n"))
                    tell_failure(run, cause, lambda: notify_series(show, settings, "error", f"Failed: {label}", f"{cause[:1500]}\nThe parts that came are in {where}.", batch=batch, details=episode_details(ep, show)))
                    run.finish("failed", cause, f"The parts that came are in {where}")
                    continue
                if not videos_in(out):
                    shutil.rmtree(out, ignore_errors=True)
                    if error:
                        with counted:
                            failures += 1
                        run.say(f"\r\n\x1b[31m{label}: Unshackle FAILED\x1b[0m\n{error}".replace("\n", "\r\n"))
                        if LOGIN.search(cause):
                            hint = f"{show['service']} refused Unshackle: its cookies may have expired. Update them in Settings, Cookies."
                            tell_failure(run, cause, lambda: notify_series(show, settings, "error", f"Login failed on {show['service']}: {label}", f"{hint}\n{cause[:1200]}", batch=batch))
                            run.finish("failed", cause, hint)
                        else:
                            tell_failure(run, cause, lambda: notify_series(show, settings, "error", f"Failed: {label}", f"{cause[:1500]}\nThe history in Activity has Unshackle's full output.", batch=batch, details=episode_details(ep, show)))
                            run.finish("failed", cause)
                    else:
                        run.say(f"{label}: not on {show['service']} yet{missing}")
                        run.finish("unavailable", f"Not on {show['service']} yet{missing}")
                        note_availability(ep, out, False, datetime.now(timezone.utc))
                        aired = ep.get("airDateUtc")
                        if LATE_AFTER and aired and datetime.now(timezone.utc) - parse_time(aired) > LATE_AFTER and first_warning(out.name):
                            notify_series(show, settings, "warning", f"Still unavailable: {label}", f"Aired {aired[:10]}, still not on {show['service']}{missing}.", batch=batch, details=episode_details(ep, show))
                    continue

                # Downloaded: the rest (parts, name, audio, Sonarr) goes on aside, the next download starts now
                finishing.put((run, ep, show, out, label, sxxeyy, lock))
                handed = True
                current.run = None
            except Exception as e:  # never a run left "running", nor the rest of the job dropped
                with counted:
                    failures += 1
                print(f"{label}: {type(e).__name__}: {e}", flush=True)
                if run and not run.card.get("ended"):
                    run.finish("failed", f"{type(e).__name__}: {e}")
                notify_series(show, settings, "error", f"Failed: {label}", f"{type(e).__name__}: {e}", batch=batch)
            finally:
                if not handed:
                    lock.__exit__(None, None, None)

    finally:  # what was downloaded is finished, whatever stopped the loop
        finishing.put(None)
        finisher.join()
    return 1 if failures else 0


imported_at: dict[int, float] = {}  # episode id -> when this process had Sonarr import it
warned_lock = threading.Lock()


def first_warning(key: str) -> bool:
    """True the first time an episode is late, and noted at once: runs side by side, or one cut off, never warn twice."""
    with warned_lock:
        warned = set(read_json(WARNED_FILE, []))
        if key in warned:
            return False
        write_atomic(WARNED_FILE, json.dumps(sorted(warned | {key})))
        return True
