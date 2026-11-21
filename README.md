# technitium-sync

![version](https://img.shields.io/badge/version-0.1.0-blue)
![license](https://img.shields.io/badge/license-MIT-green)

A small sidecar that reads the **DHCP static reservations** from
[Technitium DNS Server](https://technitium.com/dns/) and pushes them to
[Service Tracker Dashboard (STD)](https://github.com/crzykidd/service-tracker-dashboard)
as an **inventory stream**, so every reserved device shows up in STD without typing it in.

```
┌──────────────┐  GET /api/dhcp/scopes/…   ┌──────────────────┐  PUT /api/v1/streams/<id>/snapshot  ┌─────┐
│  Technitium  │ ◀──────────────────────── │ technitium-sync  │ ──────────────────────────────────▶ │ STD │
│  DHCP server │   (read-only API token)   │  every 5 minutes │        (ingest API key)             │     │
└──────────────┘                           └──────────────────┘                                     └─────┘
```

Every run sends a **full snapshot**: all reservations of the selected scopes. STD creates,
updates or marks missing its entries to match (STD PRD §18.6). Nothing is deleted on
the STD side; a device that disappears from Technitium is shown as missing.

It's also meant as a readable reference for writing your own sidecar (Pi-hole, Kea, …);
see [Write your own sidecar](#write-your-own-sidecar).

> **Requires** STD 0.10.0 or later (API revision 2, the `streams` feature).

## What's New

### v0.1.0 (2026-11-19)

First release. technitium-sync reads the static reservations of your Technitium DHCP
scopes and sends them to STD as a full inventory snapshot every 5 minutes, keyed by MAC.
A failed or partial Technitium read never sends a snapshot, so STD doesn't mark devices
missing during an outage. It retries STD with backoff, logs stream state changes once,
validates its settings at startup, and ships as a non-root Docker image with a
healthcheck. See [CHANGELOG.md](CHANGELOG.md) for the full list.

## What gets sent

One item per reservation:

| STD item field | From Technitium |
|---|---|
| `key`, `mac` | `hardwareAddress`, normalised to `aa:bb:cc:dd:ee:ff` |
| `name` | `hostName` |
| `ip` | `address` |
| `network` | the scope name |
| `attributes.scope` | the scope name |
| `attributes.comment` | `comments`, when not empty (cut to 256 chars) |

Rules:

- Only **static reservations** are read, never dynamic leases.
- With `TECHNITIUM_SCOPES` empty, every **enabled** scope is read. A scope you name in
  `TECHNITIUM_SCOPES` is read even when it's disabled; a name Technitium doesn't know is
  an error and the run is skipped.
- A reservation **without a hostname** is skipped (with a warning): STD needs a name.
- A hostname over 128 chars or a scope name over 64 chars is skipped (STD's limits).
- The **same MAC in two scopes** is sent once, from the first scope read; the duplicate
  is logged.
- If anything goes wrong reading Technitium, **nothing is sent** that run. A partial or
  empty snapshot would make STD mark devices missing. An empty snapshot (`[]`) is sent
  only when Technitium really has no reservations, with a warning.
- Over 5,000 items (STD's limit) the run is skipped with an error, never trimmed.

## Quick start

1. **Create a read-only Technitium API token** (see below).
2. **Create an `ingest` API key in STD** (see below).
3. Copy [`docker-compose.example.yml`](docker-compose.example.yml), set the URLs and the
   two secrets, and start it:

   ```yaml
   services:
     technitium-sync:
       image: ghcr.io/crzykidd/technitium-sync:latest
       container_name: technitium-sync
       restart: unless-stopped
       environment:
         TECHNITIUM_URL: http://technitium:5380
         TECHNITIUM_TOKEN: ${TECHNITIUM_TOKEN:-change-me}
         TECHNITIUM_SCOPES: ""
         STD_URL: http://service-tracker-dashboard:8815
         STD_INGEST_KEY: ${STD_INGEST_KEY:-change-me}
         STREAM_ID: technitium-dhcp
         STREAM_DISPLAY_NAME: DHCP
         INTERVAL_SECONDS: "300"
         LOG_LEVEL: INFO
   ```

   ```sh
   docker compose up -d && docker compose logs -f technitium-sync
   ```

4. **Enable the stream in STD.** The first snapshot creates the stream as **pending**:
   STD stores it but imports nothing until you approve it. Open **STD Settings →
   Streams**, check the preview, and click **Enable**. The stored snapshot is applied
   straight away, and every later snapshot is applied as it arrives.

## Configuration

All settings are environment variables, checked at startup. A bad or missing value
stops the container with a message naming the variable.

| Variable | Default | Description |
|---|---|---|
| `TECHNITIUM_URL` | *required* | Technitium web/API URL, e.g. `http://technitium:5380` |
| `TECHNITIUM_TOKEN` | *required* | Technitium API token with `DhcpServer: View` |
| `TECHNITIUM_SCOPES` | empty | Comma list of scope names. Empty = every enabled scope |
| `TECHNITIUM_VERIFY_TLS` | `true` | Set `false` for a self-signed Technitium certificate |
| `STD_URL` | *required* | STD base URL, e.g. `http://service-tracker-dashboard:8815` |
| `STD_INGEST_KEY` | *required* | STD API key with scope **Ingest** (`std_<prefix>_<secret>`) |
| `STD_VERIFY_TLS` | `true` | Set `false` for a self-signed STD certificate |
| `STREAM_ID` | `technitium-dhcp` | The stream's id in STD, `^[a-z0-9][a-z0-9_-]{0,62}$` |
| `STREAM_DISPLAY_NAME` | `DHCP` | 1–32 chars. Used when STD creates the stream; you can rename it in STD later |
| `INTERVAL_SECONDS` | `300` | Seconds between snapshots, minimum `60` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `HEALTH_PORT` | `8080` | Port of the health endpoint (all interfaces) |
| `HEALTH_MAX_AGE` | 3 × `INTERVAL_SECONDS` | Seconds `/healthz` tolerates since the last good push; at least `INTERVAL_SECONDS` |
| `HEARTBEAT_FILE` | `/tmp/technitium-sync.heartbeat` | Touched after each accepted snapshot. Kept for compatibility; the healthcheck no longer reads it |

The tokens are never logged.

## Create a read-only Technitium API token

Give technitium-sync its own user that can only **view** DHCP:

1. In the Technitium web console, **Administration → Users**: add a user, e.g.
   `technitium-sync`, with a long random password.
2. **Administration → Permissions**: on the **DhcpServer** section, add that user with
   **View** only (no Modify or Delete). It needs nothing else.
3. **Administration → Sessions → Create Token**: pick the `technitium-sync` user, name the
   token (e.g. `technitium-sync`), and copy it into `TECHNITIUM_TOKEN`.

The token goes in an `Authorization: Bearer` header; technitium-sync never puts it in a
URL. It only calls `GET /api/dhcp/scopes/list` and `GET /api/dhcp/scopes/get`.

## Create the STD ingest key

In STD, **Settings → API → Create key**, choose scope **Ingest (sidecars)**, and copy the
key (`std_<prefix>_<secret>`, shown once) into `STD_INGEST_KEY`.

An ingest key can only post snapshots and read stream status. The stream belongs to the
key that created it: a different ingest key gets 403 for that stream. Rotating the key
keeps the binding.

## Health endpoint

technitium-sync listens on `HEALTH_PORT` (default `8080`, all interfaces) and reports
whether pushes to STD actually work. A cycle counts as good only when Technitium was
read **and** STD accepted the snapshot (200, or 202 while the stream is pending,
disabled or ignored: the sidecar is doing its job, the stream state is STD's concern).

`GET /healthz` returns one `text/plain` line:

| Status | Body | When |
|---|---|---|
| 200 | `OK` | The last cycle succeeded and was within `HEALTH_MAX_AGE` |
| 503 | `starting` | No cycle has finished yet |
| 503 | `technitium: 401 unauthorized`, `technitium: invalid-token`, `technitium: unreachable` | The last Technitium read failed |
| 503 | `std: 422 …`, `std: 401 check STD_INGEST_KEY`, `std: unreachable`, `std: streams not supported (needs STD 0.10.0)` | STD refused the snapshot or can't be reached |
| 503 | `snapshot too large (5001 items > 5000)` | The snapshot was skipped for its size |
| 503 | `last push 22m ago` | The last cycle succeeded but is older than `HEALTH_MAX_AGE` (e.g. a stuck loop) |

The body never contains tokens or keys. Other paths return 404, other methods 405.

`GET /status` returns the same verdict as JSON:

```json
{"version": "0.1.0", "stream_id": "technitium-dhcp",
 "last_attempt_at": "2026-11-20T10:05:00Z", "last_success_at": "2026-11-20T10:05:00Z",
 "last_error": null, "last_item_count": 42, "health_max_age_seconds": 900, "healthy": true}
```

**In STD**, add an http check for the service pointing at
`http://technitium-sync:8080/healthz` with mode `body`: STD counts it up only when the
body is exactly `OK` and shows any other body as the failure message. Publish the port
(`ports: - "8080:8080"`) only if STD reaches the sidecar through the host.

The image's `HEALTHCHECK` runs `python -m technitium_sync healthcheck`, which calls
`/healthz` on `127.0.0.1` and exits 0 only on `200 OK` (printing the body otherwise),
with 3 retries. `HEARTBEAT_FILE` is still written but no longer read by it.

## Reading the logs

| Log | Meaning |
|---|---|
| `Connected to STD 0.10.0, API revision 2` | Startup handshake with `GET /api/v1/meta` |
| `STD API revision changed: 2 -> 3` | STD was upgraded; logged whenever `X-STD-API-Revision` changes |
| `STD too old for streams (needs API revision ≥ 2 / STD 0.10.0)` | Upgrade STD. technitium-sync keeps checking every interval |
| `Stream 'technitium-dhcp' not created yet; the first snapshot will create it as pending` | First run against this STD |
| `stream is waiting for approval in STD Settings → Streams` | The stream is pending: enable it in STD. Logged once per state change |
| `Stream … is disabled in STD: snapshots are stored but not applied` | Someone disabled the stream in STD; enable it again to resume. Logged once per state change |
| `Stream … is set to ignored in STD` | Someone chose Ignore in STD; snapshots are discarded |
| `Snapshot applied: 42 item(s); created=…, updated=…, missing=…` | Normal operation |
| `Skipping reservation … no hostname` | Give the reservation a hostname in Technitium |
| `Duplicate MAC … keeping the first` | The same device is reserved in two scopes; only the first is sent |
| `Technitium read failed, skipping this run (nothing sent)` | Technitium down, bad token, or a missing scope. STD keeps its last data |
| `check STD_INGEST_KEY / key scope` | 401/403: wrong key, a key without the Ingest scope, or another key's stream |
| `STD rejected the snapshot as invalid (HTTP 400 …)` | A bug in technitium-sync: please open an issue with the logged field errors |
| `Retrying in 10s` | STD unreachable or 5xx; backing off 5 s, 10 s, 20 s … up to the interval |

Set `LOG_LEVEL=DEBUG` to see the per-scope counts.

## Write your own sidecar

technitium-sync is deliberately small so you can copy it. The contract is
**STD PRD §18.5** (ingest) and **§18.1** (API revision) in the
[STD repo](https://github.com/crzykidd/service-tracker-dashboard/blob/main/docs/PRD.md).
In short:

- `PUT /api/v1/streams/<stream_id>/snapshot` with `Authorization: Bearer <ingest key>`
  and `{"display_name", "producer", "producer_version", "key_type", "items": [...]}`.
  Every snapshot is the full set; STD works out what changed.
- Keys must be unique within a snapshot; 5,000 items and 2 MiB at most.
- Never send a snapshot you didn't fully read: whatever is absent is marked missing.
- Send `User-Agent: <producer>/<version>`, call `GET /api/v1/meta` at startup, log the
  `X-STD-API-Revision` header when it changes, retry 5xx and network errors with
  backoff, and never exit on a failed post.

The code maps onto those steps: `technitium_sync/technitium.py` (read the source),
`mapping.py` (build items within the limits), `std_client.py` (talk to STD), and
`main.py` (the loop). Replace `technitium.py` and `mapping.py` for your source.

## Development

```sh
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pytest -q
docker build -t technitium-sync:dev .
```

Runtime dependencies live in `requirements.in`; regenerate the pinned
`requirements.txt` with `uv pip compile --python-version 3.11 requirements.in -o requirements.txt`.
Design decisions are in [`docs/decisions.md`](docs/decisions.md).

An end-to-end test against a throwaway Technitium and STD (Docker, no published ports) lives
in [`tests/live/`](tests/live/); [`docs/live-test-phase1.md`](docs/live-test-phase1.md) has the
results and how to re-run it (`tests/live/run.sh up`, `sync`, `down`).

## License

[MIT](LICENSE)
