# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-11-20

### Added

- **Snapshot interval reporting.** Every snapshot now carries `snapshot_interval_seconds`
  (`INTERVAL_SECONDS`, capped at STD's maximum of 86400) so STD can tell when this
  producer has gone quiet. It is sent only when STD reports API revision 3 or later
  (STD 0.10.1); STD 0.10.0 keeps working unchanged and receives the same snapshot as
  before, with a one-time warning that staleness uses STD's default. After a rejected
  snapshot (400, 409 or 413) `GET /api/v1/meta` is re-checked before the next push, in
  case STD was downgraded, and the 400 log line now mentions a possible STD revision
  mismatch instead of calling every 400 a bug.
- **Health endpoint.** `GET /healthz` answers `200 OK` only when the most recent
  sync cycle succeeded (Technitium read and STD accepted the snapshot) and the last
  success is within `HEALTH_MAX_AGE`. Otherwise it answers `503` with a one-line reason
  such as `technitium: invalid-token`, `std: 401 check STD_INGEST_KEY`,
  `last push 22m ago` or `starting`. Built for STD's http check in `body` mode, so a
  bad token or an STD error no longer shows green. `GET /status` returns the same
  verdict and the last attempt, success, error and item count as JSON.
- `HEALTH_PORT` (default `8080`) and `HEALTH_MAX_AGE` (seconds, default 3 ×
  `INTERVAL_SECONDS`, never below `INTERVAL_SECONDS`).

### Changed

- The Docker healthcheck now calls `/healthz` instead of reading the heartbeat file, so
  there is one definition of healthy, and the image declares `EXPOSE 8080` and retries
  3 times. `HEARTBEAT_FILE` is still written and still accepted, but nothing reads it.

## [0.1.0] — 2026-11-19

### Added

- **Technitium DHCP → STD sync.** Reads the static reservations of every enabled
  Technitium DHCP scope (or the scopes in `TECHNITIUM_SCOPES`, disabled ones included)
  and sends them to STD as a full inventory snapshot
  (`PUT /api/v1/streams/<STREAM_ID>/snapshot`) at startup and every
  `INTERVAL_SECONDS` (default 300). Each reservation becomes one item keyed by its
  MAC, with the hostname, IP, scope as network, and the scope and comment as
  attributes.
- **Safe snapshots.** A failed Technitium read skips the run and sends nothing, so STD
  never marks devices missing because of an outage. Reservations without a hostname,
  with a bad MAC, or with a name or scope over STD's limits are skipped with a warning;
  the same MAC in two scopes is sent once (the first). More than 5,000 items, or a body
  over 2 MiB, skips the run instead of sending a partial snapshot.
- **STD handshake and logging.** Logs STD's version and API revision from
  `GET /api/v1/meta` at startup and whenever `X-STD-API-Revision` changes, waits (and
  retries) when STD doesn't support streams yet, logs the stream status, the applied
  counts, and the pending/disabled/ignored states once per change. Sends
  `User-Agent: technitium-sync/<version>`.
- **Resilience.** 5xx and network errors retry with capped exponential backoff
  (5 s doubling up to the interval); 4xx errors are logged with STD's field errors and
  retried on the normal interval. Clean shutdown on SIGTERM/SIGINT.
- **Configuration** by environment variables, validated at startup with a clear message
  naming each bad variable. Secrets are never logged.
- **Docker image** `ghcr.io/crzykidd/technitium-sync` (Python 3.11 slim, non-root) with
  a `HEALTHCHECK` that fails when the last successful snapshot is older than 3 ×
  `INTERVAL_SECONDS`, and an example `docker-compose.example.yml`.
