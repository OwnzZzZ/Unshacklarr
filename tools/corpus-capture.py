"""Capture series for tests/corpus, read-only: what probe_series gets for each one (the service's listing, Sonarr's
episodes, TMDB's and TheTVDB's titles) and what it answers now. Run it inside a running Unshacklarr, with the TVDB ids:
    docker exec -i unshacklarr python - 73871 447183 < tools/corpus-capture.py > capture.json
then check each answer by hand before turning it into a case (tests/test_corpus.py)."""
import json, sys
from unshacklarr import web, sync
PICK = [int(a) for a in sys.argv[1:]]
EP_KEYS = ("id", "seasonNumber", "episodeNumber", "title", "airDate", "airDateUtc", "hasFile", "absoluteEpisodeNumber")
cfg = web.read_config()["series"]; lib = sync.sonarr_series(set())
out = {}
for tvdb in PICK:
    show = {k: v for k, v in (cfg.get(tvdb) or {}).items() if k not in ("options", "service_options", "since")}
    serie = lib.get(tvdb)
    if not show.get("service") or not serie:
        print("skip", tvdb, file=sys.stderr); continue
    rec = {}
    real = (web.list_titles, sync.sonarr_get, web.tmdb_episodes, web.tvdb_titles)
    web.list_titles = lambda s, _f=real[0]: rec.setdefault("service", _f(s))
    def sg(path, _f=real[1], **p):
        r = _f(path, **p)
        if path == "episode":
            rec["episodes"] = [{k: e.get(k) for k in EP_KEYS} for e in r]; return rec["episodes"]
        if path.startswith("series/"):
            rec["series"] = {k: r.get(k) for k in ("tvdbId", "tmdbId", "title")}; return rec["series"]
        return r
    sync.sonarr_get = sg
    web.tmdb_episodes = lambda i, _f=real[2]: rec.setdefault("tmdb", _f(i))
    web.tvdb_titles = lambda i, _f=real[3]: rec.setdefault("tvdb", _f(i))
    try:
        found = web.probe_series(show, serie["id"], serie["title"])
    except Exception as e:
        print("fail", tvdb, type(e).__name__, str(e)[:120], file=sys.stderr); continue
    finally:
        web.list_titles, sync.sonarr_get, web.tmdb_episodes, web.tvdb_titles = real
    out[str(tvdb)] = {"title": serie["title"], "show": show, "tmdb_key": bool(web.tmdb_key()), "country": sync.SETTINGS.get("country"),
                      "inputs": rec, "found": {"available": {str(k): v for k, v in found["available"].items()},
                                               "season_map": found.get("season_map"), "titlesAgree": found.get("titlesAgree")}}
    print("ok", tvdb, serie["title"], len(rec.get("service") or []), "service eps,", len(found["available"]), "available", file=sys.stderr)
json.dump(out, sys.stdout, ensure_ascii=False)
