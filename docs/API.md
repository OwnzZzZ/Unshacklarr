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
  "version": "1.3.2",
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
replaced only by a better one, as the series' quality profile ranks them: the API cannot force a
replacement (the page can). Answers `{"queued": 2}`, or `429` while too many downloads asked are still
going.

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
| `state` | `queued`, `running`, `downloaded`, `failed`, `kept` (the file Sonarr has is not worse), `unavailable` (not on the service yet), `stopped`, `cancelled`, `interrupted` (Unshacklarr restarted during it) |
| `step` | While running: `queued`, `downloading`, `joining` (parts), `renaming`, `importing`, `done` |
| `question` | While running: what the service asks (a code sent by e-mail, a PIN), to answer below |
| `error` | Why it failed, was kept or is unavailable |

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
