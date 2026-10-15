# Unshacklarr API

Other programs (a script, Home Assistant, iOS Shortcuts) can drive Unshacklarr through the routes
under `/api/v1`. Their answers keep the shape described here: new fields may appear, none is
renamed or removed without a new version (`/api/v2`).

The page itself uses other `/api/...` routes. Those change with the page: don't build on them.

## The key

Create it in **Settings › Account › API key**. It is shown once: copy it then. Unshacklarr keeps
only its hash, so a lost key is replaced, never read back. Replacing it stops the old one at once;
changing the password doesn't.

Send it in the `X-Api-Key` header with every request:

```bash
curl -H "X-Api-Key: <your key>" https://unshacklarr.example/api/v1/status
```

The key opens `/api/v1` only. The password, sessions, cookies, CDM devices, settings and
`unshackle.yaml` stay behind the login.

## Errors

An error has an HTTP status and a plain-text message saying why:

| Status | Meaning |
|---|---|
| `400` | The request is wrong (the message says what to send) |
| `401` | No key, or a wrong one |
| `404` | Sonarr has no such series or episode |
| `409` | Not possible now (a sync already runs, the download is over) |
| `429` | Too many at once: downloads asked, or wrong passwords |
| `502` | Sonarr or Unshackle didn't answer |

## Routes

### `GET /api/v1/status`

Whether everything works, and what is going on.

```json
{
  "version": "1.5.0",
  "sonarr": {"ok": true, "error": null},
  "unshackle": {"ok": false, "error": "Unshackle is unreachable: …"},
  "sync": {"running": false, "last": "2026-10-01T18:00:04+00:00"},
  "downloads": {"running": 1, "queued": 2},
  "unread": 3
}
```

`ok` is `null` until the first check, a few seconds after start. `unread` counts the notifications
not seen yet.

### `POST /api/v1/sync`

Starts a sync: every missing episode of the series set up in Unshacklarr. Answers
`{"running": true}`, or `409` when one already runs.

### `POST /api/v1/downloads`

Downloads episodes, as the page's Download button does. Name them by the series' TVDB id:

```json
{"tvdbId": 361753, "episodes": ["S02E05", "S02E06"]}
```

or by Sonarr's episode ids: `{"episodeIds": [1234, 1235]}`, 100 at most. A file Sonarr already has is
replaced only by a better one, as the series' quality profile ranks them; `"replace": true` replaces it
anyway, as the page's Replace anyway. Answers `{"queued": 2}`, or `429` while too many downloads asked are still
going.

Two optional fields: `"sonarr": "sonarr-2"` downloads for another Sonarr set up in Settings › Sonarr (its own
episode ids and TVDB lookup, its ladder, imported into it), and `"retry": true` marks the download as a retry, as the
page's Retry does. `409` when an earlier try of one of the episodes may still be downloading on Unshackle.

`"proxy"` picks the proxy for this download only, a proxy provider and a region as Unshackle's `--proxy` takes them:

```json
{"tvdbId": 361753, "episodes": ["S02E05"], "proxy": "nordvpn:fr"}
```

It is used in place of the series' proxy (and of its No proxy), for these episodes, queued ones included; the
series' setup does not change. `400` when it is not a provider and a region (`nordvpn:fr`, `protonvpn:us:ny`).

`"vcodec"` (`"H.264"` or `"H.265"`, `"avc"` and `"hevc"` too) and `"quality"` (a height, `480` to `4320`) pick the video
for this download only, one or both:

```json
{"tvdbId": 361753, "episodes": ["S02E05"], "vcodec": "H.264", "quality": 1080}
```

They take the place of the series' quality ladder (of the series, its service or Settings) for these episodes, queued
ones included: a ladder of one step, that codec at that height in any range, is checked against the episode's tracks as
a ladder is, its best track there downloaded (the best range and bitrate). The ladder's audio and subtitle order still
applies, and a series without a ladder is checked the same way. When the service has no
such track, nothing else is downloaded: the download fails and says what the service has. A codec alone takes its
best height, a height alone any codec. Through a `--remote` server, where the tracks can't be listed, Unshackle gets
them as they are. The series' setup does not change, and Activity shows what was asked. `400` for another codec, or
a height that is not a whole number from 480 to 4320.

### `GET /api/v1/downloads?limit=50`

Downloads, the queued and running ones first, then the history from newest to oldest. `limit`: 1
to 500, 50 by default.

```json
[
  {
    "id": "20261001-180412-123456-361753-S02E05",
    "series": "Silo",
    "tvdbId": 361753,
    "episode": "S02E05",
    "episodeId": 1234,
    "service": "ATVP",
    "state": "running",
    "step": "downloading",
    "question": null,
    "started": "2026-10-01T18:04:12.123456+00:00",
    "ended": null,
    "error": null
  }
]
```

| Field | |
|---|---|
| `state` | `queued`, `running`, `downloaded`, `failed`, `kept` (the file Sonarr has is not worse), `import` (downloaded, Sonarr did not import it: to import by hand), `unavailable` (not on the service yet), `stopped`, `cancelled`, `interrupted` (Unshacklarr restarted during it) |
| `step` | While running: `queued`, `downloading`, `joining` (parts), `renaming`, `importing`, `done` |
| `question` | While running: what the service asks (a code sent by e-mail, a PIN), to answer below |
| `error` | Why it failed, was kept, needs an import or is unavailable |

A queued download's `id` is `waiting-<episodeId>` until it starts.

### `POST /api/v1/downloads/{id}/stop`

Stops a running download, or takes a queued one out of the queue. Answers `{"stopping": true}`.

### `POST /api/v1/downloads/{id}/answer`

Answers the `question` a running download asks: `{"response": "123456"}`. Answers
`{"sent": true}`, or `409` when it asks nothing now.

### `GET /api/v1/notifications?limit=50`

The notifications, newest first (the page's bell). Reading them doesn't mark them read.

```json
{
  "unread": 1,
  "items": [
    {"id": "3f2a…", "at": "2026-10-01T18:20:00+00:00", "level": "success",
     "title": "Downloaded: Silo S02E05", "message": "Imported by Sonarr.", "unread": true}
  ]
}
```

`level`: `success`, `warning` or `error`.
