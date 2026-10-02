"""Files written whole or not at all: next to their target, then swapped in, so a reader or a crash
never meets half a file; and with the permissions the caller asks for, not the umask's."""

import json
import os
import re
import stat
import tempfile
from pathlib import Path

PRIVATE = 0o600  # its owner only: what anything holding a secret gets


def shared_or_private(folder: Path) -> int:
    """For a secret in a folder shared by group (setgid and group-writable, as a Samba share of
    Unshackle's config is): its group too, never other users; elsewhere its owner only."""
    return 0o660 if folder.stat().st_mode & 0o2020 == 0o2020 else PRIVATE


CREDENTIALS = re.compile(r"//[^@/\s]+@")  # http://user:password@host


def no_credentials(text) -> str:
    """A message, any user name and password in its URLs masked: errors are shown, logged and sent."""
    return CREDENTIALS.sub("//***@", str(text))


def read_json(path: Path, empty):
    """A JSON file's content, or `empty` when it is missing or unreadable (never half-written: see write_atomic)."""
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return empty


def write_atomic(path: Path, content: bytes | str, mode: int | None = None) -> None:
    """Write content to path. mode: these permissions; None: the file's own if it exists, else PRIVATE."""
    path = Path(path)
    if mode is None:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except FileNotFoundError:
            mode = PRIVATE
    data = content.encode("utf8") if isinstance(content, str) else content
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # its own name: no clash
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # on disk before the rename: a power cut never leaves an empty file
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)  # nothing half-written left behind
        raise
