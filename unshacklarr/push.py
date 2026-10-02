"""Web Push: the installed web app's own notifications, on phones and computers.

Each device that allows them gives a subscription (its push service's endpoint and keys);
Unshacklarr signs its messages with a VAPID key made on first use and kept in its data folder.
"""

import base64
import json
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01
from pywebpush import WebPushException, webpush

from unshacklarr.files import PRIVATE, read_json, write_atomic

# The VAPID subject: Apple refuses an address it cannot take for real (".invalid": 403 BadJwtToken)
CONTACT = "mailto:" + os.environ.get("PUSH_CONTACT", "unshacklarr@example.com")
_lock = threading.Lock()


class Push:
    def __init__(self, folder: Path):
        self.key_file = folder / "vapid.pem"
        self.subs_file = folder / "push.json"
        self.last_error = ""  # what a push service said when it refused, for the page and the log

    def vapid(self) -> Vapid01:
        with _lock:
            if not self.key_file.exists():
                self.key_file.parent.mkdir(parents=True, exist_ok=True)
                key = Vapid01()
                key.generate_keys()
                write_atomic(self.key_file, key.private_pem(), PRIVATE)  # it signs as this server: its owner only, from the start
            return Vapid01.from_file(str(self.key_file))

    def public_key(self) -> str:
        """The key a browser subscribes with (applicationServerKey), base64url."""
        raw = self.vapid().public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def subscriptions(self) -> list[dict]:
        return read_json(self.subs_file, [])

    def _save(self, subs: list[dict]) -> None:
        write_atomic(self.subs_file, json.dumps(subs), PRIVATE)  # each device's push keys

    def subscribe(self, sub: dict, label: str = "") -> None:
        if not str(sub.get("endpoint", "")).startswith("https://") or not (sub.get("keys") or {}).get("p256dh"):
            raise ValueError("Not a push subscription")
        with _lock:
            subs = [s for s in self.subscriptions() if s["endpoint"] != sub["endpoint"]]
            subs.append({"endpoint": sub["endpoint"], "keys": sub["keys"], "label": label[:80]})
            self._save(subs)

    def unsubscribe_all(self) -> None:
        """Every device forgotten: a new password or "log out other devices" must not leave one listening."""
        with _lock:
            self._save([])

    def unsubscribe(self, endpoint: str) -> None:
        with _lock:
            self._save([s for s in self.subscriptions() if s["endpoint"] != endpoint])

    def send(self, title: str, body: str, only: str | None = None) -> int:
        """To every device (or the one given); a device its push service no longer knows is dropped."""
        subs = [s for s in self.subscriptions() if not only or s["endpoint"] == only]
        if not subs:
            return 0
        key, sent, gone = str(self.key_file), 0, []
        self.last_error = ""
        self.vapid()  # made on first use
        for s in subs:
            try:
                webpush(subscription_info={"endpoint": s["endpoint"], "keys": s["keys"]},
                        data=json.dumps({"title": title, "body": body[:400]}),
                        vapid_private_key=key, vapid_claims={"sub": CONTACT}, ttl=86400, timeout=10)
                sent += 1
            except WebPushException as e:
                if e.response is not None and e.response.status_code in (404, 410):
                    gone.append(s["endpoint"])  # unsubscribed, or the app uninstalled
                    continue
                said = f"{e.response.status_code} {e.response.text.strip()[:160]}" if e.response is not None else str(e)[:160]
                self.last_error = f"{s['endpoint'].split('/')[2]} refused it: {said}"
                print(f"Push: {self.last_error}", flush=True)
            except Exception as e:  # a push service down must not stop the rest
                self.last_error = f"{s['endpoint'].split('/')[2]} unreachable: {e}"
                print(f"Push: {self.last_error}", flush=True)
        for endpoint in gone:
            self.unsubscribe(endpoint)
        return sent
