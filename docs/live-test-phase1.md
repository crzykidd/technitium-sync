# Live test, phase 1: throwaway Technitium + STD

Run 2026-11-17 on a dev box. technitium-sync 0.1.0 (dev, built from this repo) against a
throwaway Technitium and a throwaway STD, both in Docker, with made-up data. Nothing
touched a real network, Technitium or STD. (Phase 2, real Technitium data against a dev
STD, is a separate run.)

| Component | Version |
|---|---|
| STD | `ghcr.io/crzykidd/service-tracker-dashboard:dev`, digest `sha256:a5cefd56c43692a1d0e8ee2b205353aa652033fa33bc3d891bd27d5d6300d913`, `std_version` 0.9.0 (dev build), `api_revision` 2, features `register, management, notifications, streams` |
| Technitium | `technitium/dns-server:latest`, digest `sha256:eb48ac5c135f4ab936b536f74f853d53e15ab39ef37fdbc9598a089b90d9f245`, server version 15.6.2 |
| technitium-sync | 0.1.0, built from this repo (`build: ../..`) |

**Gate.** STD's `meta` advertises `streams` before the routes exist (STD API note 4), so it
wasn't trusted: against the `:dev` image, `PUT /api/v1/streams/probe/snapshot` without auth
gave **401**, `GET .../status` gave **401**, and an unknown route gave **404**. The streams
routes exist.

## How the stack is built

Everything is under [`tests/live/`](../tests/live/), project name `tsync-livetest`:

- `docker-compose.yml`: an isolated bridge network, **no published ports** (no 53, 67/68,
  5380 or 8815 on the host). Technitium (no DHCP interface binding to anything real; the
  scopes only exist and are read through the API), STD `:dev` with an empty named volume at
  `/config` (it needs one, or it can't open its SQLite DB), the sidecar (profile `sync`,
  `INTERVAL_SECONDS=60`, `LOG_LEVEL=DEBUG`) and a `tools` helper (profile `tools`: the sidecar
  image with `python` as the entrypoint, used to reach the services from inside the network).
- `run.sh`: `up` (start Technitium + STD, seed both, write the tokens to the gitignored
  `tests/live/.env`), `sync` (recreate the sidecar), `ctl ...` (scenario helper), `down`.
- `seed_technitium.py`: admin login with the generated password (`DNS_SERVER_ADMIN_PASSWORD`),
  then a `tsync-dhcp-view` group + `technitium-sync` user with **DhcpServer: View** only
  (`admin/groups/create`, `admin/users/create`, `admin/users/set`, `admin/permissions/set`),
  scopes `LAN` (enabled; 6 reservations: one with no hostname, one with a 300-char comment,
  one with an empty comment), `IoT` (enabled; 3, one with a MAC also reserved in `LAN`) and
  `Lab` (**disabled**; 2), then `admin/sessions/createToken` for the View-only user. All
  fake: `02:00:00:..` MACs, `10.99.x.x`, `lt-*` names.
- `bootstrap_std.py`: logs in as STD's default admin (`admin` / `changeme123`) and posts the
  Settings → API → Create key form (session + CSRF) three times: an **ingest** key for the
  sidecar, a second **ingest** key (to provoke the 403) and a **write** key (to preview,
  enable and disable the stream over the management API).
- `ctl.py`: Technitium mutations (add, change IP, delete a reservation) and STD
  status/preview/enable/disable/entries.

Passwords and tokens are generated or created at run time and live only in `tests/live/.env`
(gitignored); the sidecar never logs them.

### Re-run it

```sh
cd tests/live
./run.sh up                      # Technitium + STD, seeded; tokens in .env
./run.sh sync                    # start the sidecar
docker compose logs -f technitium-sync
./run.sh ctl s-preview           # see ctl.py for the other commands
./run.sh down                    # containers, volumes, network and built images
```

The commands need Docker and about a minute for the first pull. Pick the test STD with the
digest above, or any `:dev` that has the streams routes.

## Results

All 13 scenarios pass. Every sidecar run was a real 60 s loop; evidence below is from the logs
and STD's `GET /api/v1/streams/technitium-dhcp`.

| # | Scenario | Result | Notes |
|---|---|---|---|
| 1 | First run, pending | PASS | Stream created `pending`, `last_item_count` 7 (LAN 6 - no hostname - duplicate MAC = 4, IoT 3). The 202 warning is logged once; later runs only log at DEBUG ("still pending"). Heartbeat file written. |
| 2 | Preview, then enable | PASS | Preview: 7 `created`, no `Lab`, no no-hostname item, one item for the shared MAC (the IoT one: scopes are read in Technitium's list order, `IoT` before `LAN`). After Enable the comment is exactly 256 chars. The next run logged `Snapshot applied: 7 item(s); … unchanged=7`. |
| 3 | Change in Technitium | PASS | Added a reservation and changed another's IP: `created=1, updated=1, unchanged=6`; STD entry IP and new entry match. An empty comment sends no `attributes.comment`. |
| 4 | Delete a reservation | PASS | `missing=1`; the entry stays in STD with `inventory.missing = true`, not deleted. |
| 5 | `TECHNITIUM_SCOPES=Lab` | PASS | Startup shows `scopes: Lab`; the disabled scope was read (2 items, created) and every other entry was marked missing (expected: the snapshot is the full set). Reverting restored the normal snapshot. |
| 6 | Technitium down | PASS | `Technitium read failed, skipping this run (nothing sent)` each interval; STD's `last_snapshot_at` didn't move, nothing went missing, the container kept running. After starting Technitium the next run applied. |
| 7 | Bad Technitium token | PASS | `Technitium rejected TECHNITIUM_TOKEN (invalid-token): …` (HTTP 200 from Technitium); nothing sent; the loop continued (second error a minute later). |
| 8 | STD down | PASS | `Snapshot not sent, STD unreachable` with `Retrying in 5s / 10s / 20s / 40s`, process stayed up. After STD started, the next post was `Snapshot applied`. |
| 9 | Bad / wrong STD key | PASS | Invalid key: 401, `check STD_INGEST_KEY / key scope`. The second ingest key on the same stream: 403, `This stream belongs to a different API key.` |
| 10 | Disable the stream | PASS, message fixed | One 202 `disabled` warning, then silent. The old message said "waiting for approval", misleading for a stream an operator turned off (STD API note 7). Fixed in the sidecar, see below, and re-verified. |
| 11 | Healthcheck | PASS | `healthy` after a good run; with Technitium down it turned `unhealthy` at about 3 minutes after the last accepted snapshot (3 × 60 s; the check runs every 60 s, so it flips within 1 to 2 minutes after the limit), and `healthy` again after recovery. |
| 12 | SIGTERM | PASS | `docker stop` took 0.2 s, exit code 0, logs `Received SIGTERM, shutting down` then `Stopped`. |
| 13 | Headers | PASS | Startup logs `Connected to STD 0.9.0, API revision 2`. STD's access log shows `User-Agent: technitium-sync/0.1.0` on `meta`, `status` and `snapshot`. STD's own once-per-User-Agent log is for `/api/v1/register` only, so it isn't emitted for streams (STD API note 9). |

Also checked: the View-only token (DhcpServer: View, via a group) lists scopes and reads
reservations; it is refused (`Access was denied.`) for `addReservedLease` and `admin/users/list`.
That proves the README's read-only token steps. A user in a group works the same as a
per-user permission.

## Findings

- **Sidecar fix:** the `disabled` stream message no longer says "waiting for approval"
  (scenario 10). Test and README updated.
- **STD API notes** added to [`decisions.md`](decisions.md): note 7 confirmed live, new 9
  (User-Agent logged only on register) and 10 (the 403 `error` text is the only way to tell a
  wrong-scope key from another key's stream, though `details` does carry `stream_id`).
- **Harness caveats, not sidecar bugs:** the Technitium container has no volume. Stopping it
  (scenario 6) lost DHCP edits made after the seed (it came back with the earlier
  reservations), so the sidecar then correctly reported the reservations that were gone as
  `missing`. Use a volume at `/etc/dns` if you need Technitium state to survive a stop.
  Technitium also creates a disabled `Default` scope on first start (ignored by the sidecar
  as expected), and `dhcp/scopes/set` leaves a new scope enabled, so the seed disables `Lab`
  explicitly.

## Teardown

`./run.sh down` (= `docker compose -p tsync-livetest down -v --rmi local --remove-orphans`).
Afterwards `docker ps -a`, `docker volume ls` and `docker network ls` show nothing from
`tsync-livetest`.

Ready for phase 2 / 0.1.0 prep.
