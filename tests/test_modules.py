"""Each support module on its own: cookie files, atomic writes, CDM devices and unshackle.yaml."""

import json
import os
import stat
import threading
from pathlib import Path

import pytest
import responses

from unshacklarr.backend import Unshackle, UnshackleError
from unshacklarr.cdm import CdmError, NO_CDM, add, choose, delete, folders, read, remote_delete, remote_save, state
from unshacklarr.cookies import CookieError, list_all, normalise, parse, save
from unshacklarr.files import PRIVATE, shared_or_private, write_atomic


def test_cookie_files_are_read_saved_and_kept_in_their_folder(tmp_path):
    # formats, expiry, no path outside the folder
    netscape = "# Netscape HTTP Cookie File\n.molotov.tv\tTRUE\t/\tTRUE\t4102444800\ttoken\tabc\n#HttpOnly_.molotov.tv\tTRUE\t/\tTRUE\t0\tsid\tx\n"
    assert [c["name"] for c in parse(netscape)] == ["token", "sid"]
    js = json.dumps([{"domain": ".crave.ca", "name": "a", "value": "1", "expirationDate": 1, "httpOnly": True, "secure": True}])
    assert parse(normalise(js))[0] == {"domain": "crave.ca", "name": "a", "expires": 1}
    d = tmp_path
    folder = Path(d)
    save(folder, "MLT", "default", netscape)
    assert (folder / "MLT" / "default.txt").stat().st_mode & 0o077 == 0  # not for other users, nor its group here
    assert list_all(folder)["MLT"][0]["count"] == 2 and not list_all(folder)["MLT"][0]["expired"]
    rtl = "\n".join(["# Netscape HTTP Cookie File", ".rtlplay.be\tTRUE\t/\tTRUE\t0\t.AspNetCore.Cookies\tx",
                     ".rtlplay.be\tTRUE\t/\tTRUE\t1000\tak_bmsc\ty", ".dpgmedia.net\tTRUE\t/\tTRUE\t1000\tbm_sv\tz"])
    s = save(folder, "RTLP", "default", rtl)
    assert (s["expired"], s["session"], s["expires"]) == (False, 1, None)
    old = ".x.tv\tTRUE\t/\tTRUE\t1000\ttoken\tv"
    assert save(folder, "OLD", "default", old)["expired"]  # a dated login, past, no session: expired
    for bad in (("../x", "default"), ("MLT", "../../etc"), ("MLT", "a/b")):
        try:
            save(folder, *bad, netscape)
            raise AssertionError(bad)
        except CookieError:
            pass
    try:
        save(folder, "MLT", "default", "hello")
        raise AssertionError("not cookies")
    except CookieError:
        pass


def test_atomic_writes_are_whole_and_keep_permissions(tmp_path):
    # whole files, their permissions, writers at once
    d = tmp_path
    folder = Path(d)
    f = folder / "a.json"
    write_atomic(f, "{}")
    assert f.read_text() == "{}" and stat.S_IMODE(f.stat().st_mode) == PRIVATE  # new: its owner only
    os.chmod(f, 0o644)
    write_atomic(f, b"[1]")
    assert f.read_bytes() == b"[1]" and stat.S_IMODE(f.stat().st_mode) == 0o644  # an existing file keeps its own
    write_atomic(f, "x", 0o600)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600  # asked for: given
    threads = [threading.Thread(target=write_atomic, args=(f, chr(65 + i) * 1000)) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(f.read_text())) == 1  # writers at once: one whole file, never a mix
    assert not list(folder.glob(".*.tmp"))  # and no leftover
    assert shared_or_private(folder) == PRIVATE


def test_cdm_devices_and_unshackle_yaml_are_edited_safely(tmp_path):
    # devices, unshackle.yaml rewritten with its comments, no path
    d = tmp_path
    base = Path(d)
    conf = base / "unshackle.yaml"
    conf.write_text("# my settings\ndirectories:\n  wvds: /config/unshackle/WVDs  # theirs\n  prds: /config/unshackle/PRDs\n"
                    "cdm:\n  default: tv_sl3000   # the usual one\n  NF: 'old_l1'\n  AMZN:\n    '>=1080': tv_sl3000\n    default: old_l1\n"
                    "remote_cdm:\n  - name: FAR\n    \"Device Type\": PLAYREADY\n    secret: s\n")
    os.chmod(conf, 0o664)
    wvds, prds = folders(conf)
    assert (wvds, prds) == (base / "WVDs", base / "PRDs")
    add(wvds, prds, "old_l1.wvd", b"WVD\x02\x02\x01\x00" + b"k" * 40)
    add(wvds, prds, "tv_sl3000.prd", b"PRD\x03" + b"k" * 40)
    (prds / "folder_sl2000").mkdir()
    s = state(conf, wvds, prds)
    assert [(x["name"], x["kind"], x["level"], x["type"]) for x in s["devices"]] == [
        ("old_l1", "Widevine", "L1", "Android"), ("folder_sl2000", "PlayReady", "SL2000", ""), ("tv_sl3000", "PlayReady", "SL3000", "")]
    assert next(x for x in s["devices"] if x["name"] == "old_l1")["used_by"] == ["AMZN", "NF"]
    assert s["map"]["AMZN"] == {"rule": ["old_l1", "tv_sl3000"]}
    assert s["remote"][0] == {"name": "FAR", "kind": "PlayReady", "type": "playready", "label": "PlayReady, remote", "editable": True,
                              "has_secret": True, "device_type": "PLAYREADY", "used_by": []}  # never its secret
    choose(conf, wvds, prds, "nf", "FAR")  # the existing key, whatever its case
    choose(conf, wvds, prds, "DSNP", "tv_sl3000")
    text = conf.read_text()
    assert "# my settings" in text and "# the usual one" in text and "NF: 'FAR'" in text and "DSNP: tv_sl3000" in text
    assert "remote_cdm:\n  - name: FAR\n" in text and text.count("\n") == 15  # one line added, nothing else moved
    choose(conf, wvds, prds, "DSNP", None)
    choose(conf, wvds, prds, "default", "old_l1")
    assert "DSNP" not in conf.read_text() and "  default: old_l1   # the usual one\n" in conf.read_text()
    assert stat.S_IMODE(conf.stat().st_mode) == 0o664  # its permissions kept
    for bad in (("AMZN", "old_l1"), ("default", None), ("DSNP", "nope"), ("../x", "old_l1")):
        try:
            choose(conf, wvds, prds, *bad)
            raise AssertionError(bad)
        except CdmError:
            pass
    for bad in (("x.txt", b"WVD"), ("x.wvd", b"PRD"), ("../x.wvd", b"WVD"), ("old_l1.wvd", b"WVD")):
        try:
            add(wvds, prds, *bad)
            raise AssertionError(bad)
        except CdmError:
            pass
    try:
        delete(conf, wvds, prds, "tv_sl3000", "PlayReady")  # DSNP uses it
        raise AssertionError("in use")
    except CdmError:
        pass
    delete(conf, wvds, prds, "folder_sl2000", "PlayReady")
    # no CDM for a service; never for the default
    choose(conf, wvds, prds, "YT", NO_CDM)
    assert "  YT: none\n" in conf.read_text()
    try:
        choose(conf, wvds, prds, "default", NO_CDM)
        raise AssertionError("default none")
    except CdmError:
        pass
    # remote CDMs: added, changed (its secret kept), refused when unsafe, deleted when unused
    before = conf.read_text().split("remote_cdm:")[0]
    remote_save(conf, {"name": "wv_api", "type": "widevine", "host": "https://cdm.example/", "secret": "k1", "device_name": "l1", "security_level": "1"}, None)
    wv = next(e for e in read(conf)["remote_cdm"] if e["name"] == "wv_api")
    assert wv == {"name": "wv_api", "device_type": "ANDROID", "system_id": 26830, "security_level": 1, "host": "https://cdm.example/", "secret": "k1", "device_name": "l1"}
    remote_save(conf, {"name": "wv_api", "type": "widevine", "host": "https://cdm.example/", "secret": "", "device_name": "l2"}, "wv_api")
    wv = next(e for e in read(conf)["remote_cdm"] if e["name"] == "wv_api")
    assert (wv["device_name"], wv["secret"]) == ("l2", "k1")  # an empty secret keeps the saved one, for the same server
    try:
        remote_save(conf, {"name": "wv_api", "type": "widevine", "host": "https://attacker.example/", "secret": "", "device_name": "l2"}, "wv_api")
        raise AssertionError("the saved secret followed a new address")
    except CdmError:
        pass
    remote_save(conf, {"name": "wv_api", "type": "widevine", "host": "https://cdm2.example/", "secret": "k2", "device_name": "l2"}, "wv_api")
    wv = next(e for e in read(conf)["remote_cdm"] if e["name"] == "wv_api")
    assert (wv["host"], wv["secret"]) == ("https://cdm2.example/", "k2")
    assert conf.read_text().split("remote_cdm:")[0] == before  # everything before it as it was
    for bad in ({"name": "x", "type": "playready", "host": "https://h/", "secret": "s"}, {"name": "old_l1", "type": "decrypt_labs"},
                {"name": "y", "type": "custom_api"}, {"name": "z", "type": "widevine", "host": "ftp://h", "secret": "s"}):
        try:
            remote_save(conf, bad, None)
            raise AssertionError(bad)
        except CdmError:
            pass
    try:
        remote_delete(conf, "FAR")  # NF uses it
        raise AssertionError("in use")
    except CdmError:
        pass
    remote_delete(conf, "wv_api")
    assert [e["name"] for e in read(conf)["remote_cdm"]] == ["FAR"]
    assert not (prds / "folder_sl2000").exists()
    assert (wvds / "old_l1.wvd").stat().st_mode & 0o077 == 0  # not for other users, nor its group here


def test_odd_cookie_exports_are_refused_in_words(tmp_path):
    for cookie in ({"expirationDate": "abc"}, {"expirationDate": "inf"}, {"value": "a\tb"}):
        try:
            normalise(json.dumps([{"domain": ".x.tv", "name": "a", "value": "1", **cookie}]))
            raise AssertionError(cookie)
        except CookieError:
            pass


def test_a_device_named_in_capitals_can_be_deleted(tmp_path):
    conf = tmp_path / "unshackle.yaml"
    conf.write_text("cdm:\n  default: other\n")
    wvds, prds = folders(conf)
    wvds.mkdir()
    (wvds / "Dev.WVD").write_bytes(b"WVD\x02\x02\x01" + b"k" * 40)
    delete(conf, wvds, prds, "Dev", "Widevine")
    assert not list(wvds.iterdir())


def test_an_address_that_is_not_unshackle_serve_is_said_so(monkeypatch, tmp_path):
    import responses
    from unshacklarr.backend import Unshackle, UnshackleError
    u = Unshackle(tmp_path)
    u.configure({"unshackle_mode": "remote", "unshackle_url": "http://proxy.example", "unshackle_api_key": "k"})
    with responses.RequestsMock() as r:
        r.get("http://proxy.example/api/health", body="<html>Log in</html>", status=200)
        try:
            u.health()
            raise AssertionError("HTML taken for serve")
        except UnshackleError as e:
            assert "no JSON" in str(e)


def test_a_linked_unshackle_yaml_or_a_folder_outside_it_is_refused(tmp_path):
    secret = tmp_path / "config.yaml"
    secret.write_text("auth: {secret: s3cr3t}\n")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "unshackle.yaml").symlink_to(secret)  # planted by another user of the shared folder
    for call in (lambda: read(shared / "unshackle.yaml"), lambda: choose(shared / "unshackle.yaml", *folders(tmp_path / "x.yaml"), "NF", None)):
        try:
            call()
            raise AssertionError("read through a link")
        except CdmError:
            pass
    conf = tmp_path / "unshackle.yaml"
    conf.write_text("directories:\n  prds: ..\n")
    try:
        folders(conf)
        raise AssertionError("'..' taken as a folder")
    except CdmError:
        pass


def test_a_local_serve_gets_its_socket_and_key_without_the_command_line(tmp_path):
    import sys
    from unshacklarr.backend import Local
    pkg = tmp_path / "unshackle" / "core"
    pkg.mkdir(parents=True)
    (tmp_path / "unshackle" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "config.py").write_text("class C:\n    serve = {}\nconfig = C()\n")
    # a stand-in for unshackle serve: what it was given, where serve itself would bind
    (pkg / "__main__.py").write_text(
        "import os, sys, json\nfrom aiohttp import web\nfrom unshackle.core.config import config\n"
        "def main(argv):\n"
        "    seen = {'argv': sys.argv, 'key': config.serve['api_secret'], 'env': [k for k in os.environ if k.startswith('UNSHACKLARR')]}\n"
        "    app = web.Application()\n"
        "    app.router.add_get('/api/health', lambda r: web.json_response(seen))\n"
        "    web.run_app(app, host='127.0.0.1', port=1, print=None)\n")  # port 1: only the handed socket can serve
    command = tmp_path / ".venv" / "bin" / "unshackle"  # a git clone's: the launcher runs from the clone
    command.parent.mkdir(parents=True)
    command.write_text(f"#!{sys.executable}\n")
    command.chmod(0o755)
    local = Local(tmp_path / "serve.log")
    try:
        local.start(str(command))
        import requests
        seen = requests.get(f"{local.url}/api/health", timeout=5).json()
        assert seen["key"] == local.key and seen["env"] == []  # the key arrived, and left the environment
        assert str(local.url.rsplit(":", 1)[1]) not in " ".join(seen["argv"])  # nor the port on the command line
    finally:
        local.stop()


# From #5, by Artic0din: serve's load_errors (Unshackle after 5.4.0)
@responses.activate
def test_a_service_that_failed_to_load_is_refused_before_the_job_others_go_on(tmp_path):
    backend = Unshackle(tmp_path)
    backend.configure({"unshackle_url": "http://backend:8786", "unshackle_api_key": "test-api-secret"})
    responses.get("http://backend:8786/api/health", json={"status": "ok"})
    error = "AMZN: failed to import - ModuleNotFoundError: No module named 'tldextract'"
    responses.get("http://backend:8786/api/services", json={"services": [{"tag": "BINGE"}], "load_errors": [error]})
    responses.post("http://backend:8786/api/download", json={"job_id": "queued"})

    assert backend.health()["status"] == "ok"
    with pytest.raises(UnshackleError, match="AMZN.*tldextract.*is missing"):
        backend.download({"service": "AMZN"})
    assert not any(call.request.method == "POST" for call in responses.calls)
    assert backend.download({"service": "BINGE"}) == "queued"
    assert backend.download({"service": "AMZN", "remote": True}) == "queued"

    responses.replace(responses.GET, "http://backend:8786/api/services",
                      json={"services": [{"tag": "AMZN"}], "load_errors": []})
    assert backend.download({"service": "AMZN"}) == "queued"


@responses.activate
def test_a_load_error_s_own_words_stay_out_of_the_message(tmp_path):
    backend = Unshackle(tmp_path)
    backend.configure({"unshackle_url": "http://backend:8786", "unshackle_api_key": "test-api-secret"})
    responses.get("http://backend:8786/api/services", json={"services": [],
                  "load_errors": ["AMZN: failed to import - RuntimeError: password=private-value"]})
    with pytest.raises(UnshackleError) as caught:
        backend.download({"service": "AMZN"})
    assert "private-value" not in str(caught.value)
    assert "startup log says why" in str(caught.value)
