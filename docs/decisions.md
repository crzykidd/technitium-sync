# Decisions

Non-obvious decisions (approach changes, rejected alternatives, workarounds), newest
at top. One entry per decision. The **STD API notes** section at the bottom collects
the awkward parts of STD's ingest contract from the producer side, to raise with STD.

---

## 2026-11-17 — Live test phase 1 (harness and one fix)

- **No published ports; a `tools` container reaches the services.** Publishing host
  ports was unreliable on the dev box, and it avoids any chance of a throwaway DHCP/DNS
  server listening on the LAN. The seed/ctl scripts run in the sidecar image (it already has
  httpx) on the compose network, via `run.sh`.
- **View-only token through a group.** The Technitium user gets DhcpServer View through a
  dedicated group (`admin/permissions/set` with `groupPermissions`), which the live test showed
  works and is refused for modify and admin calls.
- **Disabled stream gets its own message.** "Waiting for approval" was wrong for a stream
  an operator disabled on purpose, so `disabled` now logs "disabled in STD: snapshots are
  stored but not applied until it is enabled again" (still once per state change).

---

## 2026-11-17 — Initial design of technitium-sync 0.1.0

- **Disabled scopes are skipped unless named.** With `TECHNITIUM_SCOPES` empty only
  enabled scopes are read: a disabled scope is usually a retired or test network, and
  its devices shouldn't appear in STD. Naming a scope is an explicit choice, so a named
  scope is read even when disabled. A named scope Technitium doesn't have fails the
  whole run (a typo would otherwise silently mark that scope's devices missing).
- **Duplicate MAC across scopes: keep the first, log the rest.** STD rejects a snapshot
  with duplicate keys (400), so one has to go. Scopes are read in Technitium's list
  order (or the configured order), so "first" is stable between runs and the item
  doesn't flap between networks. Merging (e.g. a multi-valued `ip`) was rejected: STD's
  item has one `ip` and one `network`.
- **Skip vs truncate.** Identity fields are skipped when too long (`name` > 128,
  `network` > 64): a truncated name could collide with another and would rename the
  entry in STD. Free-text attribute values (`comment`) are truncated to 256 chars,
  since they're shown, never interpreted. A reservation without a hostname, or with a
  hardware address that isn't a MAC, is skipped. An invalid `address` drops just the
  `ip` field rather than the item.
- **No partial snapshots, ever.** Any Technitium error (network, HTTP, `status` not
  `ok`, a missing scope, one scope failing among many) skips the run and sends nothing,
  because a snapshot is the full set and anything absent is marked missing. Over 5,000
  items, or a body over 2 MiB, also skips the run rather than trimming. An empty list is
  sent only when Technitium successfully returned zero reservations (logged at WARNING).
- **Meta gate before reading Technitium.** Until `GET /api/v1/meta` lists `streams`,
  the run stops there and retries on the normal interval (5xx/network: backoff). Once
  it has, it isn't re-checked; the revision header is watched on every response instead.
- **Backoff covers the whole run.** On 5xx/network errors the next run (Technitium read
  included, so the data is fresh) comes after 5, 10, 20 … seconds, capped at
  `INTERVAL_SECONDS`, and resets after any non-retryable outcome. 4xx is our bug or the
  operator's config, so it waits the normal interval.
- **Heartbeat-file healthcheck.** The container is healthy while the last *accepted*
  snapshot (200 or 202) is younger than 3 × `INTERVAL_SECONDS`. A 202 pending counts as
  healthy: the sidecar is doing its job, the operator just hasn't enabled the stream.
  The `healthcheck` subcommand reads only `HEARTBEAT_FILE` and `INTERVAL_SECONDS`, so
  it doesn't depend on config validation. An HTTP health endpoint was rejected: it would
  add a listening port to a sidecar that otherwise only makes outbound calls.
- **CI/CodeQL push-only, CodeQL as a workflow.** Same reasoning as STD (duplicate
  same-named checks from push + pull_request). CodeQL is an advanced-setup workflow so
  it's versioned in the repo; GitHub's default setup must therefore stay disabled.
- **Pinned with `uv pip compile`** (same as STD) instead of pip-tools; the output format
  is equivalent.
- **Container user is `tsync` (uid 10001).** Debian's base image already has a `sync`
  user (uid 4), so `useradd sync` fails.

---

## STD API notes

Observations about STD's ingest contract (PRD §18.1 / §18.5, read 2026-11-17, and
STD's `routes_mgmt_api.py`) that are awkward from the producer side. Not worked
around here; listed so they can be raised with STD.

1. **The error shape isn't defined in §18.5.** It says "the management API error
   shape". From STD's code that is `{"error": str, "details": ...}`; for a pydantic 400,
   `details` is a list of `{"loc": [...], "msg": str, "type": str}`; for other errors
   `details` is usually `null` or a string. A producer author has to read STD's source
   to find this. Suggest inlining it in §18.5.
2. **"Wrong scope" and "another key's stream" are both a plain 403.** A producer can't
   tell "this key isn't an ingest key" (fix the key) from "this stream belongs to a
   different ingest key" (pick another `STREAM_ID`, or use the original key) without
   parsing the free-text `error`. Suggest distinct `error` codes or a machine-readable
   `details.reason`.
3. **404 on `/status` is ambiguous on a pre-streams STD.** STD answers unknown routes
   with the same `{"error": "Not found"}` shape, so "stream not created yet" and "this
   STD has no streams API" look identical. technitium-sync relies on `meta.features`
   first, which avoids it, but a producer that skips `meta` can't tell.
4. **`streams` is advertised before the endpoints exist.** STD's `api_version.py` lists
   `streams` in `FEATURES` from the `inv-01` change, while the routes land in `inv-04`
   of the same release. A `dev` build between the two passes the `meta` gate and then
   gets 404/405 on the snapshot PUT. Harmless for released versions, confusing when
   testing against `:dev`.
5. **The 5,000-item and 2 MiB limits don't agree.** At STD's per-field maximums
   (name 128, network 64, 32 attributes of 256 chars) far fewer than 5,000 items fit in
   2 MiB, so "≤ 5000 items" doesn't guarantee acceptance. technitium-sync checks the
   encoded size itself and skips the run. Suggest §18.5 state that the byte limit wins,
   or raise it.
6. **No stable reason on 409.** 409 is documented only for a `key_type` change, but it
   reuses the generic error shape; if more 409 causes are added, producers can't tell
   them apart. Same suggestion as (2).
7. **202 pending vs disabled share one message.** Both mean "stored, not applied", but
   "disabled" means an operator turned it off on purpose; the suggested action
   ("enable it in Settings → Streams") isn't always what the operator wants. Cosmetic.
8. **The ingest key must also read `/status`.** §18.5 says "same auth" for the status
   route, which implies the ingest scope covers it; worth stating explicitly, since an
   ingest key "fails read and write checks everywhere else".
9. **STD only logs a new `User-Agent` on `/api/v1/register`.** Seen live: the streams
   routes (`meta`, `status`, `snapshot`) show the `technitium-sync/<version>` User-Agent only
   in the gunicorn access log; there is no application-level "new producer seen" line for
   them, though STD's README says each distinct User-Agent is logged. Suggest the same once-per-restart log on the
   ingest routes, so the producer and version are visible without the access log.
10. **The two 403 causes differ only in free text.** Verified live: a second `ingest` key
    on an existing stream gets `403 {"error": "This stream belongs to a different API key.",
    "details": {"stream_id": "..."}}`. `details` carries no reason code, so a producer still
    can't branch on it (see 2). Note 7 was also seen live: a stream an operator disabled
    answers `202` with `stream_status: "disabled"`, sharing the pending path.
