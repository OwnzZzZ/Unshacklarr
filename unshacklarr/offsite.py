"""Backups sent off the server: encrypted with a passphrase, then put on a WebDAV folder or an S3-compatible bucket.

A backup holds API keys and notification addresses, so it never leaves the server in clear. The encrypted file is
text (a header line, then base64), restored like any other backup with its passphrase.
"""
import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

HEADER = "UNSHACKLARR ENCRYPTED BACKUP 1"
TIMEOUT = 60


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf8"))


def encrypt(text: str, passphrase: str) -> str:
    salt, nonce = os.urandom(16), os.urandom(12)
    sealed = AESGCM(_key(passphrase, salt)).encrypt(nonce, text.encode("utf8"), HEADER.encode())
    return f"{HEADER}\n{base64.b64encode(salt + nonce + sealed).decode()}\n"


def is_encrypted(text: str) -> bool:
    return text.lstrip().startswith(HEADER)


def decrypt(text: str, passphrase: str) -> str:
    """The backup inside; ValueError when the passphrase is wrong or the file damaged."""
    try:
        raw = base64.b64decode(text.strip()[len(HEADER):].strip(), validate=True)
        return AESGCM(_key(passphrase, raw[:16])).decrypt(raw[16:28], raw[28:], HEADER.encode()).decode("utf8")
    except (InvalidTag, ValueError, IndexError):
        raise ValueError("Wrong passphrase, or the file is damaged") from None


def webdav_put(url: str, user: str, password: str, name: str, body: bytes) -> None:
    r = requests.put(f"{url.rstrip('/')}/{quote(name)}", data=body, auth=(user, password) if user else None, timeout=TIMEOUT)
    if r.status_code in (404, 409):
        raise requests.HTTPError("The folder does not exist on the WebDAV server: create it first", response=r)
    if r.status_code in (401, 403):
        raise requests.HTTPError("The WebDAV server refused the user name or password", response=r)
    r.raise_for_status()


def _sign(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def s3_put(endpoint: str, bucket: str, region: str, access_key: str, secret_key: str, name: str, body: bytes,
           now: datetime | None = None) -> None:
    """PUT one object, signed with AWS Signature V4, path-style (every S3-compatible service takes it).
    bucket may hold a folder: "backups/unshacklarr"."""
    # ponytail: one PUT, no multipart upload: a backup is a few kilobytes
    now = now or datetime.now(timezone.utc)
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    region = region or "us-east-1"
    host = urlsplit(endpoint).netloc
    path = quote(f"/{bucket.strip('/')}/{name}", safe="/-_.~")
    payload = hashlib.sha256(body).hexdigest()
    canonical = f"PUT\n{path}\n\nhost:{host}\nx-amz-content-sha256:{payload}\nx-amz-date:{stamp}\n\nhost;x-amz-content-sha256;x-amz-date\n{payload}"
    scope = f"{day}/{region}/s3/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    key = _sign(_sign(_sign(_sign(f"AWS4{secret_key}".encode(), day), region), "s3"), "aws4_request")
    auth = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
            f"Signature={hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()}")
    r = requests.put(f"{endpoint.rstrip('/')}{path}", data=body, timeout=TIMEOUT,
                     headers={"x-amz-content-sha256": payload, "x-amz-date": stamp, "Authorization": auth})
    if r.status_code in (401, 403):
        raise requests.HTTPError("The S3 service refused the access key or secret key, or the bucket is not theirs", response=r)
    if r.status_code == 404:
        raise requests.HTTPError("The S3 bucket does not exist", response=r)
    r.raise_for_status()


def send(settings: dict, name: str, text: str) -> str:
    """The backup, encrypted, to where the settings say; the name it was sent under."""
    if not settings.get("backup_passphrase"):
        raise ValueError("Set a passphrase: a backup never leaves the server unencrypted")
    body = encrypt(text, settings["backup_passphrase"]).encode()
    name = f"{name}.enc"
    kind = settings.get("backup_remote")
    if kind == "webdav":
        webdav_put(settings["backup_remote_url"], settings.get("backup_remote_user", ""), settings.get("backup_remote_secret", ""), name, body)
    elif kind == "s3":
        s3_put(settings["backup_remote_url"], settings["backup_remote_bucket"], settings.get("backup_remote_region", ""),
               settings.get("backup_remote_user", ""), settings.get("backup_remote_secret", ""), name, body)
    else:
        raise ValueError("No place to send backups to")
    return name
