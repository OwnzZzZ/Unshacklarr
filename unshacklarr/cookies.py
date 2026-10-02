"""Unshackle's cookie files, from the web page: what there is, when it expires, and new ones.

Unshackle looks for <cookies>/<SERVICE>.txt, then <cookies>/<SERVICE>/<profile>.txt, then
<cookies>/<SERVICE>/default.txt, in the Netscape format browsers' extensions export. A JSON
export (Cookie-Editor and the like) is turned into that format on the way in.
"""

import json
import re
import time
from pathlib import Path

from unshacklarr.files import shared_or_private, write_atomic

NAME = re.compile(r"[A-Za-z0-9][\w.-]{0,39}")  # a service tag or a profile: never a path
MAX_SIZE = 1_000_000
# Anti-bot and analytics cookies: they last minutes or hours whatever the login does, so they
# say nothing about whether the file still logs in (Akamai, Cloudflare, DataDome, Google, Meta).
NOISE = re.compile(r"^(ak_bmsc|bm_\w+|_abck|__cf_bm|cf_clearance|__cfruid|datadome|_ga\w*|_gid|_gat\w*|_gcl_\w+|_fbp|_fbc)$", re.IGNORECASE)


class CookieError(ValueError):
    """What is wrong with a cookie file, in words for the page."""


def check_name(name: str, what: str) -> str:
    if not NAME.fullmatch(name or "") or ".." in name:
        raise CookieError(f"Not a valid {what}: {name!r}")
    return name


def parse(text: str) -> list[dict]:
    """The cookies of a Netscape file: domain, name, expiry (0 for a session cookie)."""
    cookies = []
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        try:
            expires = int(float(fields[4] or 0))
        except ValueError:
            continue
        cookies.append({"domain": fields[0].lstrip("."), "name": fields[5], "expires": expires})
    return cookies


def from_json(text: str) -> str:
    """A browser extension's JSON export, as a Netscape file."""
    try:
        data = json.loads(text)
    except ValueError:
        raise CookieError("Neither a Netscape cookie file nor a JSON export") from None
    if isinstance(data, dict):
        data = data.get("cookies") or []
    lines = ["# Netscape HTTP Cookie File"]
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict) or not c.get("name") or not c.get("domain"):
            continue
        domain = str(c["domain"])
        try:
            expires = int(float(c.get("expirationDate") or c.get("expires") or 0))
        except (TypeError, ValueError, OverflowError):
            raise CookieError(f"Cookie {c['name']}: its expiry date is not a number") from None
        if any(ch in str(c.get(k) or "") for k in ("domain", "path", "name", "value") for ch in "\t\n\r"):
            raise CookieError(f"Cookie {c['name']}: a tab or a line break in it would break the cookie file")
        lines.append("\t".join([
            ("#HttpOnly_" if c.get("httpOnly") else "") + domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            str(c.get("path") or "/"),
            "TRUE" if c.get("secure") else "FALSE",
            str(max(expires, 0)),
            str(c["name"]),
            str(c.get("value") or ""),
        ]))
    return "\n".join(lines) + "\n"


def normalise(text: str) -> str:
    """What gets written: a Netscape file with at least one cookie, whatever was pasted."""
    if len(text) > MAX_SIZE:
        raise CookieError("That file is too large to be cookies")
    text = text.replace("\r\n", "\n").strip()
    if text.startswith(("[", "{")):
        text = from_json(text)
    if not parse(text):
        raise CookieError("No cookie found: export them in the Netscape (cookies.txt) or JSON format")
    if not text.startswith("# Netscape HTTP Cookie File"):
        text = "# Netscape HTTP Cookie File\n" + text
    return text.rstrip("\n") + "\n"


def summary(path: Path, now: float | None = None) -> dict:
    """A file as the page shows it: how many cookies, for which sites, until when."""
    now = now or time.time()
    cookies = parse(path.read_text(errors="replace"))
    kept = [c for c in cookies if not NOISE.match(c["name"])]
    lasting = [c["expires"] for c in kept if c["expires"] > 0]
    session = sum(1 for c in kept if not c["expires"])
    last = max(lasting) if lasting else 0
    return {
        "count": len(cookies),
        "updated": path.stat().st_mtime,
        "expires": last or None,  # when the last dated one runs out
        "session": session,  # no date: they last as long as the service's own session, unknown here
        # expired only when nothing could still log in: every dated cookie is past, and no session one
        "expired": bool(lasting) and last < now and not session,
    }


def list_all(folder: Path) -> dict[str, list[dict]]:
    """Every service's cookie files: {service: [{profile, …summary}]}."""
    found: dict[str, list[dict]] = {}
    if not folder.is_dir():
        return found
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.suffix == ".txt":
            found.setdefault(entry.stem, []).append({"profile": "", **summary(entry)})
        elif entry.is_dir():
            for f in sorted(entry.glob("*.txt")):
                found.setdefault(entry.name, []).append({"profile": f.stem, **summary(f)})
    return found


def path_for(folder: Path, service: str, profile: str) -> Path:
    check_name(service, "service")
    return folder / f"{service}.txt" if not profile else folder / service / f"{check_name(profile, 'profile')}.txt"


def save(folder: Path, service: str, profile: str, text: str) -> dict:
    path = path_for(folder, service, profile)
    content = normalise(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, content, shared_or_private(path.parent))  # session secrets: never other users
    return {"profile": profile, **summary(path)}


def delete(folder: Path, service: str, profile: str) -> None:
    path = path_for(folder, service, profile)
    if not path.exists():
        raise CookieError("No such cookie file")
    path.unlink()
