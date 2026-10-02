"""Unshackle's CDMs, from the web page: its Widevine (.wvd) and PlayReady (.prd) devices, and
which one each service uses.

The devices are files (a PlayReady device can also be a folder of certificates) in the WVDs
and PRDs folders; the choice per service is the cdm: section of unshackle.yaml, where only the
service's own line changes: comments, quotes and indents stay as they are. Unshackle reads both anew for every
download, so a change applies to the next one. A device is never sent to the browser: it
holds a private key.
"""

import re
import shutil
from pathlib import Path

import yaml

from unshacklarr.files import shared_or_private, write_atomic

NAME = re.compile(r"[A-Za-z0-9][\w.@ -]{0,150}")  # device names as seen: letters, digits, _ . - @ and spaces
SERVICE = re.compile(r"[A-Za-z0-9][\w-]{0,39}")
MAX_SIZE = 1_000_000
NO_CDM = "none"  # a service set to it uses no CDM at all (no DRM): Unshackle's cdm: section, as the fork reads it
# remote_cdm entries the page edits, by kind: {key: label}; custom_api and others stay in unshackle.yaml
REMOTE_KINDS = {
    "widevine": "Widevine, pywidevine serve", "playready": "PlayReady, remote", "decrypt_labs": "Decrypt Labs",
}
REMOTE_FIELDS = ("name", "type", "device_type", "system_id", "security_level", "host", "secret", "device_name")
KINDS = {".wvd": ("Widevine", b"WVD"), ".prd": ("PlayReady", b"PRD")}


class CdmError(ValueError):
    """What is wrong, in words for the page."""


def directory(yaml_file: Path, key: str, default: str) -> Path:
    """A folder of Unshackle's, found next to unshackle.yaml: its directories: section names it for
    Unshackle's own filesystem, so only its last part is kept."""
    named = (read(yaml_file).get("directories") or {}).get(key) or default
    name = Path(str(named)).name
    if name in ("", ".", ".."):  # "..", "/" or ".": not a folder next to unshackle.yaml
        raise CdmError(f"unshackle.yaml's directories: {key}: {named!r} is no folder next to it")
    return yaml_file.parent / name


def folders(yaml_file: Path) -> tuple[Path, Path]:
    """The WVDs and PRDs folders."""
    return directory(yaml_file, "wvds", "WVDs"), directory(yaml_file, "prds", "PRDs")


def read(yaml_file: Path) -> dict:
    return yaml.safe_load(regular(yaml_file).read_text(encoding="utf8")) or {} if yaml_file.exists() else {}


def regular(path: Path) -> Path:
    """The file itself, never a link: a link put in a shared folder would make the page read, then write
    back in that folder, what it points to (Unshacklarr's own config.yaml, say)."""
    if path.is_symlink():
        raise CdmError(f"{path.name} is a symbolic link: replace it with the file itself")
    return path


def describe(path: Path, kind: str) -> dict:
    """A device as the page shows it: its name (what unshackle.yaml calls it), kind and level."""
    level = kind_of = ""
    if path.is_file() and path.suffix.lower() == ".wvd":
        with path.open("rb") as f:
            head = f.read(6)  # "WVD", version, type, security level
        if head[:3] == b"WVD" and len(head) == 6:
            kind_of = {1: "Chrome", 2: "Android"}.get(head[4], "")
            level = f"L{head[5]}" if head[5] else ""
    if kind == "PlayReady":
        found = re.search(r"sl(\d{3,4})", path.name, re.IGNORECASE)  # the certificate is not read: its name says it
        level = f"SL{found.group(1)}" if found else ""
    return {"name": path.stem if path.is_file() else path.name, "kind": kind, "level": level, "type": kind_of,
            "folder": path.is_dir(), "size": path.stat().st_size if path.is_file() else None}


def devices(wvds: Path, prds: Path) -> list[dict]:
    found = []
    for folder, kind, suffix in ((wvds, "Widevine", ".wvd"), (prds, "PlayReady", ".prd")):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if (path.is_file() and path.suffix.lower() == suffix) or (path.is_dir() and kind == "PlayReady" and not path.name.startswith(".")):
                found.append(describe(path, kind))
    return found


def names_in(value) -> set[str]:
    """The devices a cdm: entry names: one, or several in a rule by profile, quality or DRM."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return set().union(*(names_in(v) for v in value.values())) if value else set()
    return set()


def state(yaml_file: Path, wvds: Path, prds: Path) -> dict:
    """Everything the page shows: devices with the services using them, remote CDMs, the choice per service."""
    config = read(yaml_file)
    cdm = config.get("cdm") or {}
    remote = [{"name": str(r["name"]), "kind": "PlayReady" if "PLAYREADY" in str(field(r, "device_type") or "").upper() else "Widevine",
               **remote_view(r)} for r in config.get("remote_cdm") or [] if isinstance(r, dict) and r.get("name")]
    found = devices(wvds, prds)
    for d in found + remote:
        d["used_by"] = sorted(str(s) for s, v in cdm.items() if d["name"] in names_in(v))
    return {
        "devices": found, "remote": remote,
        # a rule (by profile, quality or DRM) is kept as it is: edited in unshackle.yaml
        "map": {str(s): v if isinstance(v, str) else {"rule": sorted(names_in(v))} for s, v in cdm.items()},
    }


def choose(yaml_file: Path, wvds: Path, prds: Path, service: str, device: str | None) -> None:
    """Set (or, with None, remove) the device a service uses, in unshackle.yaml's cdm: section."""
    if not SERVICE.fullmatch(service or ""):
        raise CdmError(f"Not a service: {service!r}")
    if not yaml_file.exists():
        raise CdmError(f"No unshackle.yaml in {yaml_file.parent}")
    current = state(yaml_file, wvds, prds)  # once: it reads unshackle.yaml and lists both folders
    known = {d["name"] for d in current["devices"] + current["remote"]}
    if device is not None and device != NO_CDM and device not in known:
        raise CdmError(f"No device called {device!r}")
    if device == NO_CDM and service.lower() == "default":
        raise CdmError("The default CDM cannot be none: set none on the services without DRM")
    if device is None and service.lower() == "default":
        raise CdmError("The default CDM cannot be removed, only changed")
    text = yaml_file.read_text(encoding="utf8")
    cdm = read(yaml_file).get("cdm") or {}
    key = next((str(k) for k in cdm if str(k).lower() == service.lower()), None)  # Unshackle reads them case-insensitively
    if key is not None and not isinstance(cdm[key], str):
        raise CdmError(f"{service} has a rule by profile, quality or DRM: change it in unshackle.yaml")
    new = edit_line(text, key or service, device, key is not None)
    after = yaml.safe_load(new) or {}
    got = next((v for k, v in (after.get("cdm") or {}).items() if str(k).lower() == service.lower()), None)
    others = {k: v for k, v in after.items() if k != "cdm"} == {k: v for k, v in (yaml.safe_load(text) or {}).items() if k != "cdm"}
    if got != device or not others:  # checked by reading it back: never a file Unshackle would read otherwise
        raise CdmError("Could not change unshackle.yaml safely: change it by hand")
    write_atomic(yaml_file, new)  # unshackle.yaml keeps its own permissions


def edit_line(text: str, key: str, device: str | None, exists: bool) -> str:
    """The cdm: section with one line set, added or removed; every other line as it was."""
    lines = text.splitlines(keepends=True)
    top = next((i for i, l in enumerate(lines) if re.match(r"cdm:\s*(#.*)?$", l.rstrip("\r\n"))), None)
    value = device if device and re.fullmatch(r"[\w.@-]+", device) else f"'{device}'" if device else ""
    if top is None:
        if device is None:
            return text
        return text + ("" if text.endswith("\n") or not text else "\n") + f"cdm:\n  {key}: {value}\n"
    end = top + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t" or lines[end].lstrip().startswith("#")):
        end += 1
    entries = [i for i in range(top + 1, end) if lines[i].strip() and not lines[i].lstrip().startswith("#")]
    indent = re.match(r"\s*", lines[entries[0]]).group(0) if entries else "  "
    mine = re.compile(rf"{re.escape(indent)}(['\"]?){re.escape(key)}\1\s*:(?P<value>[^#\n]*?)(?P<rest>\s+#.*)?(?P<nl>\r?\n)?$")
    for i in entries:
        m = mine.match(lines[i])
        if not (exists and m and lines[i][len(indent)] not in " \t"):
            continue
        if device is None:
            return "".join(lines[:i] + lines[i + 1:])
        old = m.group("value").strip()
        quote = old[0] if old[:1] in ("'", '"') else ""  # the value keeps its quoting
        shown = f"{quote}{device}{quote}" if quote and "'" not in device else value
        start = lines[i][:m.start("value")]
        lines[i] = f"{start} {shown}{m.group('rest') or ''}{m.group('nl') or ''}"
        return "".join(lines)
    if device is None:
        return text
    at = (entries[-1] + 1) if entries else top + 1
    return "".join(lines[:at] + [f"{indent}{key}: {value}\n"] + lines[at:])


def squash(key) -> str:
    """A remote_cdm key as Unshackle compares them: device_type, "Device Type" and DeviceType are one."""
    return re.sub(r"[\s_]", "", str(key)).lower()


def field(entry: dict, name: str):
    """A remote_cdm field, whatever its spelling."""
    return next((v for k, v in entry.items() if squash(k) == squash(name)), None)


def remote_kind(entry: dict) -> str:
    """widevine, playready, decrypt_labs, or another type the page leaves to unshackle.yaml (custom_api)."""
    kind = str(field(entry, "type") or "").lower()
    if kind:
        return kind
    return "playready" if "PLAYREADY" in str(field(entry, "device_type") or "").upper() else "widevine"


def remote_view(entry: dict) -> dict:
    """A remote CDM as the page shows and edits it: never its secret, only whether it has one."""
    kind = remote_kind(entry)
    return {"type": kind, "label": REMOTE_KINDS.get(kind, kind), "editable": kind in REMOTE_KINDS, "has_secret": bool(field(entry, "secret")),
            **{k: field(entry, k) for k in ("host", "device_name", "device_type", "system_id", "security_level") if field(entry, k) not in (None, "")}}


def remote_save(yaml_file: Path, form: dict, was: str | None) -> None:
    """Add a remote CDM, or change one (was: its name before); its secret kept when the form leaves it empty
    and the address stays the same."""
    config = read(yaml_file)
    entries = [e for e in config.get("remote_cdm") or [] if isinstance(e, dict)]
    name = str(form.get("name") or "").strip()
    kind = str(form.get("type") or "")
    if not NAME.fullmatch(name):
        raise CdmError("A remote CDM's name: letters, digits, _ . - @")
    if kind not in REMOTE_KINDS:
        raise CdmError("A remote CDM is Widevine (pywidevine serve), PlayReady or Decrypt Labs here; others in unshackle.yaml")
    old = next((e for e in entries if str(e.get("name")) == was), None) if was else None
    if was and old is None:
        raise CdmError(f"No remote CDM called {was!r}")
    if old is not None and remote_kind(old) not in REMOTE_KINDS:
        raise CdmError(f"{was} is a {remote_kind(old)} remote CDM: change it in unshackle.yaml")
    taken = {str(e.get("name")) for e in entries if e is not old} | {d["name"] for d in devices(*folders(yaml_file))}
    if name in taken:
        raise CdmError(f"{name} is already a device's or a remote CDM's name")
    cdm = config.get("cdm") or {}
    if old is not None and name != was and any(was in names_in(v) for v in cdm.values()):
        raise CdmError(f"{was} is in use: give its services another device before renaming it")
    host = str(form.get("host") or "").strip()
    if kind != "decrypt_labs" and not re.fullmatch(r"https?://\S+", host):
        raise CdmError("Its address: http(s)://…")
    if kind == "playready" and not host.rstrip("/").endswith("/playready"):
        raise CdmError("A PlayReady remote CDM's address ends in /playready")
    secret = str(form.get("secret") or "")
    if not secret and old is not None:  # kept only for the same server: a saved key never follows a new address
        if host.rstrip("/") != str(field(old, "host") or "").rstrip("/") or kind != remote_kind(old):
            raise CdmError("Type its secret again: a saved secret never goes to a new address")
        secret = str(field(old, "secret") or "")
    if kind != "decrypt_labs" and not secret:
        raise CdmError("Its secret (API key)")
    number = lambda k, default=None: int(form[k]) if str(form.get(k) or "").strip().isdigit() else default
    entry = {"name": name}
    if kind == "decrypt_labs":
        entry |= {"type": "decrypt_labs", "device_name": str(form.get("device_name") or "L1").strip()}
        entry |= ({"host": host} if host else {}) | ({"secret": secret} if secret else {})
    else:
        entry |= {"device_type": "PLAYREADY" if kind == "playready" else str(form.get("device_type") or "ANDROID").upper(),
                  **({"system_id": number("system_id", 26830)} if kind == "widevine" else {}),
                  "security_level": number("security_level", 3000 if kind == "playready" else 3),
                  "host": host, "secret": secret, "device_name": str(form.get("device_name") or "").strip()}
    if old is not None:  # fields the form doesn't know (a proxy, a timeout) stay
        entry |= {k: v for k, v in old.items() if not any(squash(k) == squash(f) for f in REMOTE_FIELDS)}
    entries = [entry if e is old else e for e in entries] if old is not None else entries + [entry]
    write_remote(yaml_file, config, entries)


def remote_delete(yaml_file: Path, name: str) -> None:
    config = read(yaml_file)
    entries = [e for e in config.get("remote_cdm") or [] if isinstance(e, dict)]
    if not any(str(e.get("name")) == name for e in entries):
        raise CdmError(f"No remote CDM called {name!r}")
    used = sorted(str(s) for s, v in (config.get("cdm") or {}).items() if name in names_in(v))
    if used:
        raise CdmError(f"{name} is used by {', '.join(used)}: pick another device for them first")
    write_remote(yaml_file, config, [e for e in entries if str(e.get("name")) != name])


def write_remote(yaml_file: Path, config: dict, entries: list[dict]) -> None:
    """unshackle.yaml with its remote_cdm: block written anew and every other line as it was; checked by reading it back."""
    text = yaml_file.read_text(encoding="utf8")
    lines = text.splitlines(keepends=True)
    top = next((i for i, l in enumerate(lines) if re.match(r"remote_cdm:\s*(#.*)?$", l.rstrip("\r\n"))), None)
    block = yaml.safe_dump({"remote_cdm": entries}, sort_keys=False, allow_unicode=True, default_flow_style=False)
    if top is None:
        new = text + ("" if text.endswith("\n") or not text else "\n") + block
    else:
        end = top + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t-" or lines[end].lstrip().startswith("#")):
            end += 1
        new = "".join(lines[:top]) + block + "".join(lines[end:])
    after = yaml.safe_load(new) or {}
    if after.get("remote_cdm") != entries or {k: v for k, v in after.items() if k != "remote_cdm"} != {k: v for k, v in config.items() if k != "remote_cdm"}:
        raise CdmError("Could not change unshackle.yaml safely: change it by hand")
    write_atomic(yaml_file, new)


def add(wvds: Path, prds: Path, filename: str, content: bytes) -> dict:
    """A new device file, checked to be one: never over an existing one."""
    path = Path(filename or "")
    suffix = path.suffix.lower()
    if suffix not in KINDS:
        raise CdmError("A device is a .wvd (Widevine) or .prd (PlayReady) file")
    kind, magic = KINDS[suffix]
    if not NAME.fullmatch(path.stem) or "/" in filename or "\\" in filename:  # a name, never a path
        raise CdmError(f"Not a usable device name: {path.stem!r}")
    if len(content) > MAX_SIZE or not content.startswith(magic):
        raise CdmError(f"Not a {kind} device: a {suffix} file starts with {magic.decode()}")
    folder = wvds if kind == "Widevine" else prds
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.stem}{suffix}"
    if target.exists() or (folder / path.stem).exists():
        raise CdmError(f"{path.stem} is already there: delete it first to replace it")
    write_atomic(target, content, shared_or_private(folder))  # a private key
    return describe(target, kind)


def delete(yaml_file: Path, wvds: Path, prds: Path, name: str, kind: str) -> None:
    if not NAME.fullmatch(name or "") or kind not in ("Widevine", "PlayReady"):
        raise CdmError("No such device")
    device = next((d for d in devices(wvds, prds) if d["name"] == name and d["kind"] == kind), None)
    if not device:
        raise CdmError("No such device")
    used = next(d for d in state(yaml_file, wvds, prds)["devices"] if d["name"] == name and d["kind"] == kind)["used_by"]
    if used:
        raise CdmError(f"{name} is used by {', '.join(used)}: pick another device for them first")
    folder = wvds if kind == "Widevine" else prds
    suffix = ".wvd" if kind == "Widevine" else ".prd"
    path = folder / name if device["folder"] else next(p for p in folder.iterdir() if p.is_file() and p.stem == name and p.suffix.lower() == suffix)
    shutil.rmtree(path) if path.is_dir() else path.unlink()
