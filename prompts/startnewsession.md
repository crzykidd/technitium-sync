# Start a new session: technitium-sync after 0.2.0

Paste this into a fresh Claude Code session in this repo, or say "read
prompts/startnewsession.md and start". It's a briefing, not a task: read it, check
the state below, then ask the operator what's next.

## What this project is

`technitium-sync` is an **independent** sidecar. It reads Technitium DHCP **static
reservations** (never dynamic leases) and sends them every `INTERVAL_SECONDS`
(default 300) as a full snapshot to Service Tracker Dashboard's (STD) stream ingest
API: `PUT /api/v1/streams/<id>/snapshot`, STD PRD §18.5. It's also meant to be a
readable reference for people writing their own sidecars, so keep it small.

- **STD is an external API.** `../service-tracker-dashboard` is a read-only reference.
  Never edit, check out into or commit anything there. When the contract is awkward
  from our side, add an item under "STD API notes" in `docs/decisions.md` (11 so far)
  and tell the operator, who raises it with STD. Adapt to an STD contract change only
  after STD ships it, or when the operator says to go ahead.
- **Never send a snapshot built from a failed read.** A partial or empty snapshot makes
  STD mark devices missing.
- **Code:** `technitium_sync/` (`config.py`, `technitium.py`, `mapping.py`,
  `std_client.py`, `health.py`, `main.py`). Tests are in `tests/` (153, pytest + respx),
  and the live Docker harness is in `tests/live/`.

## Rules (see `CLAUDE.md` and `standards.md`)

Adopted standards: handoff-prompt-workflow 2.0.0, code-checkin-and-pr 1.2.0 and
release-prep-and-cut 1.1.0.

- Work on the `dev` branch. `main` is protected: changes reach it only through a PR,
  and every required check must pass.
- More than about 2 files → write a handoff prompt in `prompts/` (from `TEMPLATE.md`) and
  run it with a spawned agent: Sonnet for coding, Opus for research or planning.
- Propose ONE commit and ask the operator `y/n`. Stage specific paths; never `git add -A`.
  Prefixes `feat:`/`fix:`/`chore:`/`docs:`. **No `Co-authored-by:` trailers** (the
  project rule wins over the attribution reminder). Push only with the operator's OK.
- Every operator-visible change gets an entry under CHANGELOG `[Unreleased]` in the same
  commit. Non-obvious decisions go in `docs/decisions.md`, newest at top.
- Releases: `/release-prep <x.y.z>` → the operator merges the PR → `/release-cut
  <x.y.z>`. Never re-tag.
- **Never change or suggest a deployment's Docker image tag** (`:dev` vs `:latest` /
  `:<semver>`). The operator runs release images and switches to `:dev` themselves.
- The operator wants short, direct answers and action, not repeated questions about
  things already decided.

## Where things stand (2026-11-20)

- **Released:** `v0.1.0` (2026-11-19) and `v0.2.0` (2026-11-20, tag on `main`
  `91e9bd6`). The GHCR images `ghcr.io/crzykidd/technitium-sync:latest`, `:0.2.0` and
  `:0` are all the same build. `VERSION` is `0.2.0`, and CHANGELOG `[Unreleased]` is
  empty.
- **Branches:** `dev` is at `b0a4eb9`. `main` has the same content plus the PR merge
  commits. The queue in `prompts/` is empty; finished prompts are in `prompts/done/`.
- **CI:** CI, CodeQL, the image build and `retention.yml` all pass on `main`. Retention
  ran without a 403, so the GHCR Admin access is confirmed.
- **STD:** the latest release is **0.10.1, API revision 3**. 0.10.0 is revision 2.
- **What 0.2.0 added:**
  - `GET /healthz`: plain text, `200 OK` or `503 <one-line reason>`, for STD's http
    check in `body` mode.
  - `GET /status`: JSON with the same details, for humans.
  - `HEALTH_PORT` (8080) and `HEALTH_MAX_AGE` (3 × interval).
  - The Docker healthcheck now calls `/healthz`. `HEARTBEAT_FILE` is still written but
    nothing reads it.
  - `snapshot_interval_seconds` is sent only when STD reports revision 3+.
  - `meta` is re-checked after a 400, 409 or 413.
- **Deployment:** live on em-docker01 as stream `technitium-dhcp`. The operator
  validated against their production STD; the dev-STD "phase 2" live test was dropped.
- **Up to the operator:** update em-docker01 to 0.2.0, make port 8080 reachable from
  STD, and add STD's http check on `/healthz` (mode `body`).

## Things to know before the next release

- **CHANGELOG archive:** for 0.2.0 the operator chose not to archive. `0.1.0` is still
  in full in `CHANGELOG.md`. The next minor `/release-prep` will want to archive the
  closed series (0.1.x, 0.2.x) to `docs/CHANGELOG-<minor>.x.md`. Ask the operator
  before doing it.
- **First-release exception:** 0.1.0 skipped `/release-prep`'s "not newer" check once,
  because `VERSION` already said 0.1.0 (see `docs/decisions.md`). Later releases go
  through the command unchanged.
- **Known gap:** a 401 or 403 from STD doesn't trigger the `meta` re-check; only 400,
  409 and 413 do.

## Host notes (the dev box, crzydev)

- Docker's log driver is `json-file`. The old Loki driver hung containers and was
  removed on 2026-11-18. About 140 anonymous volumes are left over; the operator
  hasn't decided whether to prune them.
- Only the `home.arpa` search domain is set, so use FQDNs (e.g.
  `docker03.crzynet.com`).
- Local checks are the same as CI: `.venv/bin/ruff check .`, `.venv/bin/pytest -q`,
  `docker build .`, `docker compose -f docker-compose.example.yml config --quiet`.
