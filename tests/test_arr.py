import asyncio
import importlib
import re


def test_sonarr_finds_an_episode_sends_it_and_imports_it_from_the_folder(tmp_path, monkeypatch):
    """The indexer offers the episode, the client takes its magnet, starts it, says when it is done."""
    from aiohttp.test_utils import TestClient, TestServer
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    arr = web.arr
    config = web.read_config()
    config["auth"] = {"password": web.hash_password("password"), "secret": "s"}
    config["series"] = {100: {"service": "ATV", "title": "umc.cmc.silo"}}
    web.write_config(config)
    series = {"id": 7, "title": "Silo", "tvdbId": 100, "runtime": 50}
    episodes = [{"id": 55, "seasonNumber": 1, "episodeNumber": 2, "airDateUtc": "2025-01-01T00:00:00Z", "hasFile": False},
                {"id": 56, "seasonNumber": 1, "episodeNumber": 3, "airDateUtc": "2999-01-01T00:00:00Z", "hasFile": False}]
    monkeypatch.setattr(arr.sync, "sonarr_get", lambda path, **params: [series] if path == "series" else [dict(e) for e in episodes])
    started = []
    monkeypatch.setattr(arr, "start", lambda ids, kind, numbering=None: started.append((ids, kind, numbering)))
    monkeypatch.setattr(arr, "available", lambda tvdb, show, series, ids: {i: "S01E02" for i in ids})  # the service has what is out
    key = arr.key()

    async def go():
        async with TestClient(TestServer(web.app)) as client:
            qb = "/qbittorrent/api/v2/"
            assert (await client.get("/torznab/api", params={"t": "caps", "apikey": "wrong"})).status == 401
            assert "tv-search" in await (await client.get("/torznab/api", params={"t": "caps", "apikey": key})).text()
            season = await (await client.get("/torznab/api", params={"t": "tvsearch", "apikey": key, "tvdbid": "100", "season": "1"})).text()
            assert "Silo.S01E02.1080p.ATV.WEB-DL-Unshacklarr" in season and "S01E03" not in season  # not aired yet: not offered
            magnet = re.search(r'magneturl" value="([^"]+)"', season).group(1).replace("&amp;", "&")

            assert await (await client.post(qb + "auth/login", data={"username": "x", "password": "wrong"})).text() == "Fails."
            assert (await client.get(qb + "torrents/info")).status == 403
            assert await (await client.post(qb + "auth/login", data={"username": "x", "password": key})).text() == "Ok."
            assert await (await client.post(qb + "torrents/add", data={"urls": magnet, "category": "tv-sonarr"})).text() == "Ok."
            waiting = (await (await client.get(qb + "torrents/info", params={"category": "tv-sonarr"})).json())[0]

            out = arr.sync.DOWNLOADS / "unshackle-100-S01E02"
            out.mkdir(parents=True)
            (out / "Silo.S01E02.mkv").write_bytes(b"x" * 10)
            done = (await (await client.get(qb + "torrents/info")).json())[0]
            await client.post(qb + "torrents/delete", data={"hashes": done["hash"], "deleteFiles": "true"})
            left = await (await client.get(qb + "torrents/info")).json()
            return waiting, done, left

    waiting, done, left = asyncio.run(go())
    assert started == [([55], "sonarr", None)]  # the series' own numbering
    assert waiting["state"] == "stalledDL" and waiting["progress"] == 0  # not on the service yet: Sonarr waits, no failure
    assert done["state"] == "pausedUP" and done["progress"] == 1 and done["size"] == 10 and done["content_path"].endswith("unshackle-100-S01E02")
    assert left == [] and not (arr.sync.DOWNLOADS / "unshackle-100-S01E02").exists()


def test_a_magnet_names_its_episode_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    from unshacklarr import arr
    ep = {"series": {"tvdbId": 100, "title": "Faites entrer l'accusé", "runtime": 50}, "seasonNumber": 25, "episodeNumber": 3,
          "airDateUtc": "2025-01-01T00:00:00Z"}
    r = arr.release(ep, {"service": "RMCP", "title": "x", "options": {"-q": "2160"}}, {})
    assert r["title"] == "Faites.entrer.laccusé.S25E03.2160p.RMCP.WEB-DL-Unshacklarr"
    assert arr.from_magnet(r["magnet"]) == {"hash": r["hash"], "name": r["title"], "tvdb": 100, "season": 25, "episode": 3}
    assert arr.from_magnet(r["magnet"].replace("&e=3", "&e=4")) is None  # its hash no longer fits: not ours
    assert arr.from_magnet("magnet:?xt=urn:btih:" + "a" * 40) is None


def test_a_sonarr_schema_gets_its_fields_and_keeps_the_rest():
    from unshacklarr import arr
    schema = {"implementation": "Torznab", "fields": [{"name": "baseUrl", "value": ""}, {"name": "apiKey"}, {"name": "minimumSeeders", "value": 1}]}
    body = arr.filled(schema, {"baseUrl": "http://u:8788/torznab", "apiKey": "k"}, enableRss=True)
    assert body["name"] == "Unshacklarr" and body["enableRss"] is True
    assert body["fields"] == [{"name": "baseUrl", "value": "http://u:8788/torznab"}, {"name": "apiKey", "value": "k"}, {"name": "minimumSeeders", "value": 1}]


def test_the_first_address_sonarr_reaches_wins(monkeypatch):
    from unshacklarr import arr
    monkeypatch.setattr(arr.sync, "SONARR", "http://sonarr:8989")
    monkeypatch.setattr(arr.socket, "gethostname", lambda: "3f2a9c")
    monkeypatch.setattr(arr.socket, "gethostbyname", lambda name: (_ for _ in ()).throw(OSError("no such host here")))
    assert arr.candidates(8788, "https://dl.example.com/") == ["http://unshacklarr:8788", "http://3f2a9c:8788", "https://dl.example.com"]
    tried = []

    def add_at(address):
        tried.append(address)
        if "unshacklarr" in address:
            raise ValueError("Sonarr said: Unable to connect")

    monkeypatch.setattr(arr, "add_at", add_at)
    assert arr.add_to_sonarr(["http://unshacklarr:8788", "http://3f2a9c:8788", "https://dl.example.com"]) == "http://3f2a9c:8788"
    assert tried == ["http://unshacklarr:8788", "http://3f2a9c:8788"]  # stops at the first that works


def test_an_episode_out_by_its_date_but_not_on_the_service_is_not_offered(tmp_path, monkeypatch):
    """Sonarr takes an indexer's result for a file to have now: the service is asked first."""
    from datetime import datetime, timezone
    monkeypatch.setenv("UNSHACKLARR_DATA", str(tmp_path))
    import unshacklarr.sync
    import unshacklarr.web
    importlib.reload(unshacklarr.sync)
    web = importlib.reload(unshacklarr.web)
    arr = web.arr
    config = web.read_config()
    config["series"] = {100: {"service": "ATV", "title": "umc.cmc.silo"}}
    web.write_config(config)
    series = {"id": 7, "title": "Silo", "tvdbId": 100}
    episodes = [{"id": 55, "seasonNumber": 1, "episodeNumber": 2, "airDateUtc": "2025-01-01T00:00:00Z"},
                {"id": 56, "seasonNumber": 1, "episodeNumber": 3, "airDateUtc": "2025-01-08T00:00:00Z"}]
    monkeypatch.setattr(arr.sync, "sonarr_get", lambda path, **params: [series] if path == "series" else [dict(e) for e in episodes])
    probed = []
    monkeypatch.setattr(web, "probe_series", lambda show, series_id, title: probed.append(series_id) or {
        "available": {55: {"service": "S02E01", "match": "title"}}, "titles": [], "local_titles": {}})
    now = datetime.now(timezone.utc)
    found = arr.search({"tvdbid": "100", "season": "1"}, now)
    assert [r["title"] for r in found] == ["Silo.S01E02.1080p.ATV.WEB-DL-Unshacklarr"] and probed == [7]  # S01E03 aired, not on ATV yet
    # found by its title as ATV's S02E01: the download asks for that one
    wanted = arr.from_magnet(found[0]["magnet"])
    assert wanted["theirs"] == "S02E01"
    assert arr.numbering_for({**wanted, "episode_id": 55})["episode_map"] == {"S01E02": "S02E01"}
    arr.search({"tvdbid": "100", "season": "1", "ep": "2"}, now)
    assert probed == [7]  # found a moment ago: the service is not asked again
    monkeypatch.setattr(web, "probe_series", lambda *a: (_ for _ in ()).throw(RuntimeError("Unshackle is down")))
    web.SERVICE_LISTS.unlink()
    assert arr.search({"tvdbid": "100"}, now) == []  # nothing checked, nothing promised
