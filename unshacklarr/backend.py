"""unshackle, through its REST API (`unshackle serve`).

Remote: a serve that already runs (another container, another machine), reached with
its URL and API key. Local: Unshacklarr starts one itself from the Unshackle installed
on this computer, bound to 127.0.0.1 on a free port, with a key made up for the run
and set in memory, so the user's unshackle.yaml is never touched.
"""

import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from unshacklarr.files import no_credentials

# Runs with Unshackle's own Python: its config, cookies and CDMs, plus a key for this run.
# Port and key come through the environment, never the command line (which every user of the machine
# can read). Where it can, Unshacklarr binds the socket itself and hands it over: no moment when the port
# is free for another program to take and pass for serve.
LAUNCHER = """
import os
from unshackle.core.config import config
config.serve["api_secret"] = os.environ.pop("UNSHACKLARR_SERVE_KEY")
port, fd = os.environ.pop("UNSHACKLARR_SERVE_PORT"), os.environ.pop("UNSHACKLARR_SERVE_FD", "")
if fd:
    import socket
    from aiohttp import web
    sock, run_app = socket.socket(fileno=int(fd)), web.run_app
    web.run_app = lambda app, *args, host=None, port=None, **kw: run_app(app, *args, sock=sock, **kw)
from unshackle.core.__main__ import main
main(["serve", "--api-only", "-h", "127.0.0.1", "-p", port])
"""

TERMINAL = {"completed", "failed", "cancelled"}


class UnshackleError(Exception):
    """unshackle cannot be reached or refused a request; the message says why."""


def unshackle_python(command: str) -> tuple[str, Path]:
    """The Python that runs the `unshackle` command (its script's shebang, or the venv's own),
    and the folder to run it from: a git clone's, when the command is in its .venv, since a
    config there may name its folders relative to it (services: [unshackle/services])."""
    found = shutil.which(command or "unshackle")
    if not found:
        raise UnshackleError(
            f"The {command or 'unshackle'!r} command was not found. Install Unshackle, "
            "or give the full path to its command in Settings, Unshackle."
        )
    script = Path(found).resolve()
    venv = script.parent.parent
    folder = venv.parent if venv.name == ".venv" else Path.home()
    with script.open("rb") as f:
        first = f.readline(512)
    if first.startswith(b"#!") and b"python" in first:
        return first[2:].decode(errors="replace").strip().split()[0], folder
    for name in ("python.exe", "python3", "python"):  # a Windows .exe launcher, or an odd shebang
        if (script.parent / name).exists():
            return str(script.parent / name), folder
    raise UnshackleError(f"Could not tell which Python runs {script}")


EXAMPLE_KEYS = {"change-me-to-a-long-random-string", "the-UNSHACKLE_API_KEY-of-.env"}


# A service signing in by a code on another device (MAX's device linking) only writes it to its log
SIGN_IN = re.compile(r"Go to:\s*(https?://\S+).{0,600}?(?:Enter|Use)(?: the)? code:\s*([A-Za-z0-9-]{4,16})", re.S)
SIGNED_IN = re.compile(r"linked successfully|linking timed out|signed in|logged in", re.I)
SIGN_IN_WAIT = 600  # seconds a code stays worth showing (MAX waits 10 minutes)


class Local:
    """A serve of our own, started on demand and restarted if it dies."""

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self.process: subprocess.Popen | None = None
        self.started: datetime | None = None
        self.url = self.key = ""
        self.command = None
        self.lock = threading.Lock()
        self.codes_seen: dict[str, float] = {}  # a sign-in code and when it was first read

    def endpoint(self, command: str) -> tuple[str, str]:
        with self.lock:
            if self.process and self.process.poll() is None and command == self.command:
                return self.url, self.key
            self.stop()
            self.start(command)
            return self.url, self.key

    def start(self, command: str) -> None:
        python, folder = unshackle_python(command)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        handed = os.name != "nt"  # Windows cannot pass a socket this way: there, serve binds the port itself
        if handed:
            sock.listen(128)
            sock.set_inheritable(True)
        else:
            sock.close()
        self.key, self.url, self.command = secrets.token_urlsafe(24), f"http://127.0.0.1:{port}", command
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        if self.log_file.exists() and self.log_file.stat().st_size > 20_000_000:  # one older log kept, no more
            self.log_file.replace(self.log_file.with_suffix(".log.1"))
        log = self.log_file.open("ab")
        env = {**os.environ, "UNSHACKLARR_SERVE_KEY": self.key, "UNSHACKLARR_SERVE_PORT": str(port)}
        if handed:
            env["UNSHACKLARR_SERVE_FD"] = str(sock.fileno())
        try:
            self.process = subprocess.Popen(
                [python, "-c", LAUNCHER],
                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=folder, env=env,
                pass_fds=(sock.fileno(),) if handed else (),
            )
        finally:
            if handed:
                sock.close()  # serve has its own copy
        deadline = time.monotonic() + 90  # Unshackle loads every service at start
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise UnshackleError(f"unshackle serve stopped at start: {self.tail()}")
            try:
                requests.get(f"{self.url}/api/health", timeout=2).raise_for_status()
                self.started = datetime.now(timezone.utc)
                return
            except requests.RequestException:
                time.sleep(0.5)
        self.stop()
        raise UnshackleError(f"unshackle serve did not answer within 90 s: {self.tail()}")

    def sign_in(self) -> dict | None:
        """The code a service waits for you to enter to sign in, read from our serve's log: {"url", "code"} while it
        waits, None once signed in, timed out, or with no serve of our own. Only a serve we started has its log here."""
        if not (self.process and self.process.poll() is None):
            return None
        try:
            with self.log_file.open("rb") as f:
                f.seek(max(0, f.seek(0, os.SEEK_END) - 40_000))
                text = f.read().decode(errors="replace")
        except OSError:
            return None
        found = list(SIGN_IN.finditer(text))
        if not found or SIGNED_IN.search(text, found[-1].end()):
            return None
        url, code = found[-1].group(1), found[-1].group(2)
        first = self.codes_seen.setdefault(code, time.monotonic())
        return {"url": url, "code": code} if time.monotonic() - first < SIGN_IN_WAIT else None

    def tail(self, lines: int = 3, sep: str = " / ") -> str:
        try:
            with self.log_file.open("rb") as f:  # its end only: the log grows for as long as serve runs
                f.seek(max(0, f.seek(0, os.SEEK_END) - 400_000))
                text = f.read().decode(errors="replace")
        except OSError:
            return "no output"
        return sep.join(text.strip().splitlines()[-lines:]) or "no output"

    def paths(self, command: str) -> dict[str, Path]:
        """Where this unshackle keeps its config file, cookies and CDMs, as it says itself (asked once)."""
        if getattr(self, "_paths", (None, None))[0] != command:
            python, folder = unshackle_python(command)
            ask = ("from unshackle.core.config import config, config_path; d = config.directories\n"
                   "print(config_path or ''); print(d.cookies.resolve()); print(d.wvds.resolve()); print(d.prds.resolve())")
            out = subprocess.run([python, "-c", ask], cwd=folder, capture_output=True, text=True, timeout=60)
            lines = out.stdout.strip().splitlines()[-4:]
            if out.returncode or len(lines) != 4:
                raise UnshackleError(f"Could not ask unshackle where its files are: {out.stderr.strip()[-300:]}")
            self._paths = (command, dict(zip(("config", "cookies", "wvds", "prds"), map(Path, lines))))
        return self._paths[1]

    def cookies_dir(self, command: str) -> Path:
        return self.paths(command)["cookies"]

    def restart(self, command: str) -> None:
        with self.lock:
            self.stop()
            self.start(command)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()  # reaped: no zombie left behind
        self.process, self.started = None, None


class Unshackle:
    name = ""  # another serve's name in Settings; the main one has none

    def __init__(self, data_dir: Path):
        self.local = Local(data_dir / "unshackle-serve.log")
        self.settings: dict = {}
        self._services: tuple[float, list] = (0.0, [])
        self._remote: tuple[float, list] = (0.0, [])
        self._config: tuple[float, dict] = (0.0, {})

    def configure(self, settings: dict) -> None:
        self.settings = settings
        self._services = (0.0, [])
        self._remote = (0.0, [])
        self._config = (0.0, {})

    def sign_in(self) -> dict | None:
        """A sign-in code a service waits for, when serve runs here (local): see Local.sign_in."""
        return self.local.sign_in() if self.mode == "local" else None

    @property
    def mode(self) -> str:
        return self.settings.get("unshackle_mode") or ("remote" if self.settings.get("unshackle_url") else "local")

    def endpoint(self) -> tuple[str, str]:
        if self.mode == "remote":
            url = str(self.settings.get("unshackle_url") or "").rstrip("/")
            if not url:
                raise UnshackleError("Set the address of unshackle serve in Settings, Unshackle")
            key = str(self.settings.get("unshackle_api_key") or "")
            if key in EXAMPLE_KEYS:  # the value of .env.example: anyone who read it knows it
                raise UnshackleError("The API key of unshackle serve is still the example one: put a long random one in "
                                     "UNSHACKLE_API_KEY and in serve: api_secret (openssl rand -hex 32)")
            return url, key
        return self.local.endpoint(str(self.settings.get("unshackle_command") or ""))

    def call(self, method: str, path: str, **kwargs):
        url, key = self.endpoint()
        try:
            r = requests.request(method, f"{url}{path}", headers={"X-Secret-Key": key}, timeout=(5, 60), **kwargs)  # a server down: 5 s, not 60
        except requests.RequestException as e:
            raise UnshackleError(no_credentials(f"unshackle serve is unreachable at {url}: {e}")) from e
        if r.status_code == 401:
            raise UnshackleError("unshackle serve refused the API key")
        if not r.ok:
            try:
                message = r.json().get("message") or r.text
            except ValueError:
                message = r.text
            raise UnshackleError(f"unshackle serve: {message.strip()[:500]}")
        try:
            return r.json()
        except ValueError:  # a proxy's login page, or another program at this address
            raise UnshackleError(f"{url} did not answer like unshackle serve (no JSON): is the address right?") from None

    def services(self) -> list[dict]:
        """Tag, URL and help of each service, kept 10 minutes."""
        fetched, services = self._services
        if not services or time.monotonic() - fetched > 600:
            services = self.call("GET", "/api/services")["services"]
            self._services = (time.monotonic(), services)
        return services

    def remote_services(self) -> list[dict]:
        """The servers of Unshackle's remote_services and the services each offers (for --remote), kept
        10 minutes; none when this Unshackle cannot tell (no such route, or unreachable)."""
        fetched, servers = self._remote
        if time.monotonic() - fetched > 600:
            try:
                servers = self.call("GET", "/api/remote/services").get("servers") or []
            except UnshackleError:
                servers = []
            self._remote = (time.monotonic(), servers)
        return servers

    def health(self) -> dict:
        """Its version, and whether a newer one is out."""
        return self.call("GET", "/api/health")

    def tools(self) -> list[dict]:
        """The programs Unshackle needs (ffmpeg, mkvmerge, shaka-packager…): found or not, and their version."""
        return self.call("GET", "/api/env/check")["checks"]

    def jobs(self) -> list[dict]:
        return self.call("GET", "/api/download/jobs")["jobs"]

    def restart(self) -> None:
        if self.mode == "remote":
            raise UnshackleError("A remote unshackle serve is restarted where it runs")
        self.local.restart(str(self.settings.get("unshackle_command") or ""))

    def dl_config(self) -> dict:
        """The `dl:` section of its unshackle.yaml (secrets redacted), kept 10 minutes."""
        return self.config().get("dl") or {}

    def cdm_config(self) -> dict:
        """The `cdm:` section of its unshackle.yaml: the device each service decrypts with, and the default."""
        return self.config().get("cdm") or {}

    def config(self) -> dict:
        """Its unshackle.yaml (secrets redacted), kept 10 minutes."""
        fetched, config = self._config
        if not fetched or time.monotonic() - fetched > 600:
            config = self.call("GET", "/api/config")["config"] or {}
            self._config = (time.monotonic(), config)
        return config

    def download(self, payload: dict) -> str:
        if not payload.get("remote"):
            # A service that failed to import is in serve's load_errors (Unshackle after 5.4.0; none before): told
            # now, not once the job fails. Asked afresh each time, not the cached list: serve may have restarted.
            catalogue = self.call("GET", "/api/services")
            tag = str(payload.get("service") or "")
            for error in catalogue.get("load_errors") or []:
                if isinstance(error, str) and error.partition(":")[0].casefold() == tag.casefold():
                    # its own words stay out: the error may quote a secret; a missing module is safe to name
                    if module := re.search(r"No module named '([A-Za-z0-9_.]+)'", error):
                        raise UnshackleError(f"{tag} did not load in unshackle serve: the Python module {module[1]} is missing "
                                             "on the machine where it runs. Its startup log has more details")
                    raise UnshackleError(f"{tag} did not load in unshackle serve: its startup log says why")
        return self.call("POST", "/api/download", json=payload)["job_id"]

    def job(self, job_id: str) -> dict:
        return self.call("GET", f"/api/download/jobs/{job_id}")

    def cancel(self, job_id: str) -> None:
        self.call("DELETE", f"/api/download/jobs/{job_id}")

    def stop(self) -> None:
        self.local.stop()


if __name__ == "__main__":  # python -m unshacklarr.backend [command]: start a local serve and list its services
    u = Unshackle(Path(os.environ.get("TMPDIR", "/tmp")))
    u.configure({"unshackle_command": sys.argv[1] if len(sys.argv) > 1 else ""})
    try:
        print(sorted(s["tag"] for s in u.services()))
    finally:
        u.stop()
