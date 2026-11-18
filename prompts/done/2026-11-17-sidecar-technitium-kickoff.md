---
name: 2026-11-17-sidecar-technitium-kickoff
status: completed         # pending | completed | failed
created: 2026-11-17
model: opus              # mixed: repo bootstrap + design check + coding
completed: 2026-11-17
result: technitium-sync 0.1.0 scaffolded (package, tests, CI/publish/retention/CodeQL, Docker, docs, standards); ruff, pytest (98), docker build, compose all pass; not committed
---

# Task: Build `technitium-sync`, a Technitium DHCP → STD inventory sidecar

`technitium-sync` is an **independent project**: a small service that runs between
Technitium DHCP and Service Tracker Dashboard (STD). It reads Technitium's **DHCP static
reservations** and pushes them to STD's stream ingest API as a full **inventory snapshot**.
It is shipped as a Docker image on GHCR. It's also meant to be a readable reference that
other people copy to write their own sidecars (Pi-hole, Kea, …), so keep it small,
readable and well documented.

STD is an **external API** this project consumes. It is not part of this project's
development. **Never edit, move or commit anything in the STD repo.**

## Before you start

- Repo: this directory, `~/projects/technitium-sync`, remote
  `github.com/crzykidd/technitium-sync` (public). `main` and `dev` already exist on the
  remote (bootstrap commit: LICENSE + stub README). **Work on `dev`.**
- The name is `technitium-sync` **everywhere**: the `producer` field, the
  `User-Agent: technitium-sync/<VERSION>`, the image `ghcr.io/crzykidd/technitium-sync`,
  and the docs. (STD's docs call the sidecar `std-source-technitium`; that's their
  working name, and we don't follow it.)
- Read (read-only) the STD API spec in `../service-tracker-dashboard/docs/PRD.md`:
  - **§18.5**: the ingest contract (auth, payload, limits, responses). **This is the
    contract you code against;**
  - §18.1: API revision, `X-STD-API-Revision` / `X-STD-Version` headers,
    `GET /api/v1/meta`, producer expectations;
  - §18.6: what STD does with a snapshot, for log wording and README context.
  - §18.5 says 400/401/403/409/413 use "the management API error shape". Look up
    that shape in STD's code (`routes_mgmt_api.py` / `mgmt_schemas.py` or similar) so
    the client can log the field errors.
  - STD's `CLAUDE.md` is for house style only (Python 3.11, ruff, pydantic, Keep a
    Changelog). This repo should feel like a smaller sibling.
- Standards: read and adopt these three from `~/projects/homelab-configs/standards/`,
  at these versions:
  - `handoff-prompt-workflow` **2.0.0**: copy `TEMPLATE.md` to `prompts/TEMPLATE.md`
    and paste its `CLAUDE-snippet.md` verbatim into `CLAUDE.md`;
  - `code-checkin-and-pr` **1.2.0**: paste its snippet verbatim, and add the CI
    checks, the publish matrix and retention;
  - `release-prep-and-cut` **1.1.0**: copy and customise `release-prep.md` and
    `release-cut.md` into `.claude/commands/`, and paste its snippet verbatim.

  Look at STD's `standards.md`, `.github/workflows/` and `.claude/commands/` as a
  worked example of the same adoption (read-only).
- Technitium API. Verified against the official `APIDOCS.md`
  (https://github.com/TechnitiumSoftware/DnsServer/blob/master/APIDOCS.md, "DHCP API
  Calls"). Re-check it if anything looks off:
  - auth: header `Authorization: Bearer <token>`. The `token` query parameter is
    legacy only, so don't use it;
  - every response is `{"status": "ok"|"error"|"invalid-token"|..., "response": {...},
    "errorMessage": ...}`, and **errors come back as HTTP 200**. Always check
    `status`;
  - `GET /api/dhcp/scopes/list` → `response.scopes[]` with `name`, `enabled`,
    `startingAddress`, …;
  - `GET /api/dhcp/scopes/get?name=<scope>` → `response.reservedLeases[]` with
    `hostName` (may be `null`), `hardwareAddress` (e.g. `00-00-00-00-00-00`),
    `address`, `comments`. These are the static reservations. **Never call
    `/api/dhcp/leases/list`** (dynamic leases).

## Working tree check

Before making any edits, run `git status --porcelain` and cross-reference the files
this plan needs to modify. If any of those files have uncommitted changes, list them
and ask the user before touching them. Surface unrelated dirty files once as
awareness; don't block. This file (the handoff prompt itself) is exempt. It's
expected to be modified by "When done" below.

## What to do

1. **Scaffold:**
   - `README.md` (replacing the stub), `CHANGELOG.md` (Keep a Changelog,
     `[Unreleased]`), bare-semver `VERSION` (`0.1.0`), keep the existing `LICENSE`
     (MIT), `CLAUDE.md` (project overview + the three snippets verbatim + a pointer to
     `standards.md`), and `standards.md` pinning the three versions;
   - `prompts/TEMPLATE.md`, `docs/decisions.md` (newest at top);
   - `.github/workflows/`:
     - `ci.yml`: lint (ruff), test (pytest), structured-config validation (YAML/JSON
       parse), compose validation (`docker compose -f docker-compose.example.yml
       config --quiet`). There's no database, so the migration check is N/A; record
       that in `standards.md`;
     - `publish.yml` → `ghcr.io/crzykidd/technitium-sync`, using the
       code-checkin-and-pr matrix (dev → `:dev`+`:sha-<short>`; main → `:latest`+
       `:sha-<short>`; PR → build only; release → `:latest`, `:<semver>`, `:<major>`),
       plus the release-tag == `VERSION` guard;
     - `retention.yml`: 30 sha builds, 15 semver, protected tags never deleted;
     - SAST: CodeQL. Note in `standards.md` whether it's a workflow or default
       setup (default setup has to be enabled in repo settings, so tell the user);
   - `Dockerfile` (python:3.11-slim, non-root user, `HEALTHCHECK`, writes the version
     from `VERSION`), `.dockerignore`, `docker-compose.example.yml`;
   - `ruff.toml`, `requirements.in` → pinned `requirements.txt`, `requirements-dev.txt`,
     `.gitignore`.
   - Layout suggestion: a `technitium_sync/` package (`config.py`, `technitium.py`,
     `mapping.py`, `std_client.py`, `main.py`) run as `python -m technitium_sync`.
2. **Config** (environment variables, pydantic-settings, validated at startup; on error,
   print a clear message naming the bad variable and exit non-zero):
   - `TECHNITIUM_URL`, `TECHNITIUM_TOKEN`;
   - `TECHNITIUM_SCOPES` (optional comma list; empty = all **enabled** scopes. A scope
     named explicitly is read even if it's disabled);
   - `STD_URL`, `STD_INGEST_KEY`;
   - `STREAM_ID` (default `technitium-dhcp`; must match `^[a-z0-9][a-z0-9_-]{0,62}$`),
     `STREAM_DISPLAY_NAME` (default `DHCP`, 1–32 chars);
   - `INTERVAL_SECONDS` (default 300, minimum 60), `LOG_LEVEL` (default INFO);
   - optional `TECHNITIUM_VERIFY_TLS` / `STD_VERIFY_TLS` (default true);
   - `HEARTBEAT_FILE` (default `/tmp/technitium-sync.heartbeat`);
   - secrets are never logged (`SecretStr`).
3. **Technitium reader:** list the scopes and, for each selected scope, read its static
   reservations only. A configured scope that doesn't exist is an error for that run.
   Map each reservation to an item:
   - `key` and `mac`: `hardwareAddress` normalised to lower-case colon form;
   - `name`: `hostName`. When it's missing or blank, skip the reservation with a
     warning and count it;
   - `ip`: `address`;
   - `network`: the scope name;
   - `attributes`: `scope`, plus `comment` (from `comments`) when it's non-empty.

   Respect the §18.5 limits:
   - truncate attribute string values over 256 chars;
   - skip an item, with a warning, when its name (>128) or network (>64) is too long;
   - the same MAC in two scopes becomes one item: keep the first, log the duplicate.
     STD rejects duplicate keys;
   - more than 5,000 items: log an ERROR and skip the run. Never send a partial
     snapshot.
4. **STD client** (httpx):
   - send `User-Agent: technitium-sync/<VERSION>` and `Authorization: Bearer <key>`;
   - at startup, call `GET /api/v1/meta` and log `std_version` and `api_revision`. If
     `streams` isn't in `features`, log "STD too old for streams (needs API revision ≥
     2 / STD 0.10.0)" and retry on the normal interval rather than exiting;
   - on every response, read `X-STD-API-Revision`. Log at INFO when it differs from
     the last value seen;
   - at startup, call `GET /api/v1/streams/<id>/status` and log it. A 404 means "not
     created yet; the first snapshot will create it as pending";
   - `PUT /api/v1/streams/<STREAM_ID>/snapshot` with `display_name`, `producer`
     (`technitium-sync`), `producer_version`, `key_type: "mac"` and `items`;
   - handle each §18.5 status:
     - 200: log the counts at INFO;
     - 202 pending/disabled: log at WARNING once per state change: "stream is waiting
       for approval in STD Settings → Streams";
     - 202 ignored: log at WARNING once;
     - 400: log the field errors at ERROR, then wait the normal interval (it's our
       bug, so don't retry fast);
     - 401/403: log "check STD_INGEST_KEY / key scope" at ERROR;
     - 409/413: log at ERROR;
     - 5xx and network errors: retry with capped exponential backoff, and keep
       running.
5. **Loop:**
   - one snapshot at startup, then every `INTERVAL_SECONDS`;
   - a Technitium failure skips that run. **Never send an empty snapshot because a
     read failed**, because STD would mark every device missing. Only send `[]` when
     Technitium really returned no reservations, and log that at WARNING;
   - shut down cleanly on SIGTERM/SIGINT (interruptible sleep);
   - touch the heartbeat file after each successful post. Docker's `HEALTHCHECK`
     fails when it's older than about 3 × the interval (a small `healthcheck`
     subcommand or script).
6. **Tests** (pytest + respx):
   - MAC normalisation for every form §18.5 accepts (`aa:bb:..`, `AA-BB-..`,
     `aabb.ccdd.eeff`, `aabbccddeeff`), plus rejects;
   - the reservation → item mapping: no hostname, long values, a duplicate MAC across
     scopes, an empty comment, the 5,000 limit;
   - config validation: missing values, a bad stream id, an interval below 60, scope
     list parsing;
   - the Technitium reader: `status: error` / `invalid-token` returned as HTTP 200,
     disabled scopes, a configured scope that doesn't exist;
   - the STD client against respx: 200, 202 pending, 202 ignored, 400, 401, 409, 413,
     5xx with backoff, the revision-change log, `meta` without `streams`, status 404;
   - "Technitium failed → no snapshot sent", and "really empty → `[]` sent";
   - fixtures of real-shaped, anonymised Technitium responses in `tests/fixtures/`.
7. **Docs:**
   - README: what it does and where it sits (Technitium → technitium-sync → STD), the
     env table, the compose example, how to create the `ingest` key in STD (Settings →
     API, scope "Ingest"), the pending → Enable step in STD Settings → Streams, how to
     create a read-only Technitium API token (DhcpServer: View), what the logs mean,
     and a short "write your own sidecar" section pointing at STD PRD §18.5;
   - CHANGELOG `[Unreleased]` → Added;
   - `docs/decisions.md`: the non-obvious calls (skipping disabled scopes, the
     duplicate-MAC policy, skip vs truncate, no partial snapshots, the heartbeat
     healthcheck). Add a **"STD API notes"** section for anything about the §18.5
     contract that's awkward from the producer side. Don't work around it; describe
     it so the user can raise it with STD. Known so far: §18.5 doesn't define the
     error shape inline, and "wrong scope" vs "another key's stream" are both a plain
     403.
8. **Verify locally:** `ruff check .`, `pytest`, `docker build .`,
   `docker compose -f docker-compose.example.yml config --quiet`. Report any failures
   honestly.

Out of scope here: the live test against a real Technitium and an STD with the streams
API. That's a follow-up prompt once such an STD exists.

## Conventions to honor

- The three crzynet standards as adopted: Conventional Commits prefixes, **no
  `Co-authored-by:`**, never push to `main`, docs in the same commit as code, a bare
  `VERSION` file.
- Don't cut a release. `0.1.0` waits for a live test against an STD with streams.

## When done

1. Update this file's frontmatter: set `status` (completed/failed), `completed` (the
   date), and `result` (one line).
2. `git mv` this file into `prompts/done/` (on success) or `prompts/failed/` (on
   failure). Create the subdir if it doesn't exist yet. (The file may still be
   untracked. If so, `git add` this one path first, then `git mv`.)
3. Record any non-obvious decisions in `docs/decisions.md`.
4. Hand off ONE commit covering this prompt file, the files this session created or
   modified, and the prompt move. Present the file list and a one-line message (e.g.
   `feat: initial technitium-sync sidecar (Technitium DHCP reservations → STD stream
   snapshot)`).
   - **If you are a spawned agent:** do **not** commit. Prepare the working tree, then
     report the file list, the proposed message, the verification results, the STD API
     notes and anything the user must do by hand (CodeQL, branch protection) back to
     the orchestrating session.
   - **If you are running directly (manual fallback):** ask the user
     `commit these as "<message>"? (y/n)` yourself. On `y`, stage those specific paths
     and commit on `dev`.

   Either way: never `git add -A`, never push, never auto-commit.
