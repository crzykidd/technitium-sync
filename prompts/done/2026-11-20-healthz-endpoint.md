---
name: 2026-11-20-healthz-endpoint
status: completed
created: 2026-11-20
model: sonnet
completed: 2026-11-20
result: Added /healthz and /status (health.py), HEALTH_PORT/HEALTH_MAX_AGE, healthcheck via /healthz, docs and tests; all local checks pass
---

# Task: add a `/healthz` endpoint that reflects whether pushes to STD succeed

Today STD monitors technitium-sync only with a container check, so a bad token, a
Technitium API error or an STD 4xx/5xx still shows green. Add an HTTP health endpoint
built for STD's http check mode `body`: STD counts the check **up only when the response
body is exactly `OK`**. Any other body is down and STD shows it as the failure message.

## Before you start

- Read `CLAUDE.md`, `standards.md`, `README.md` and `docs/decisions.md`. Then read all of
  `technitium_sync/` (about 800 lines) and the existing tests, especially the
  `healthcheck` subcommand and `touch_heartbeat` in `main.py` and `config.py`.
- `docs/decisions.md` (2026-11-17, "Heartbeat-file healthcheck") **rejected an HTTP
  health endpoint** because it adds a listening port. The operator now wants one. Add a
  new decision entry (newest at top) that supersedes that point and says why: STD needs
  to check push health over the network. Don't edit the old entry; just say in the new
  one that it supersedes it.
- STD is an external API. Don't touch the STD repo, and don't change the snapshot
  payload. Both are out of scope.
- **No new dependencies.** Use the stdlib (`http.server.ThreadingHTTPServer` on a daemon
  thread is fine). `httpx` is already installed if the Docker healthcheck needs to make
  an HTTP call.
- Never call `/api/dhcp/leases/list`, and keep the "never send a snapshot built from a
  failed read" rule exactly as it is.

## Working tree check

Before making any edits, run `git status --porcelain` and cross-reference the files
this plan needs to modify. If any of those files have uncommitted changes, list them
and ask the user before touching them. Surface unrelated dirty files once as
awareness; don't block. This file (the handoff prompt itself) is exempt — it's
expected to be modified by "When done" below.

## What to do

1. **Health state (in memory).** A small thread-safe state object (a lock is enough)
   that the loop updates after each cycle. Fields: `last_attempt_at`,
   `last_success_at`, `last_error` (a short string or None), `last_item_count` (items
   in the last snapshot sent). A "full cycle" succeeds only when the Technitium read
   succeeded **and** STD accepted the snapshot. Use the same acceptance rule as the
   heartbeat: 200, or 202 (pending, disabled or ignored). Record in decisions that a 202
   counts as healthy, because the sidecar is doing its job and the stream state is
   STD's concern. Every other outcome sets `last_error`: a Technitium failure, the
   too-many-items or too-big skip, the meta gate ("STD too old"), STD 4xx/5xx or
   unreachable. Success clears it.

2. **`GET /healthz`**, plain text (`text/plain; charset=utf-8`), one line:
   - **200 `OK`** when the most recent cycle succeeded and `last_success_at` is within
     `HEALTH_MAX_AGE`.
   - **503 `starting`** before any cycle has succeeded and none has failed yet. Never
     return `OK` before the first push succeeds.
   - **503 `<reason>`** when the most recent cycle failed. This holds even when the last
     success is still recent: the spec says the *last* cycle must have succeeded. Use
     short prefixed reasons, for example `technitium: 401 unauthorized`,
     `technitium: invalid-token`, `technitium: unreachable`, `std: 422 <detail>`,
     `std: 401 check STD_INGEST_KEY`, `std: unreachable`,
     `std: streams not supported (needs STD 0.10.0)`,
     `snapshot too large (5001 items > 5000)`.
   - **503 `last push 22m ago`** when the last cycle succeeded but is older than
     `HEALTH_MAX_AGE`, for example when the loop is stuck. Use minutes; use seconds
     under 1 minute.
   - Sanitise the body: one line (collapse newlines), cap it around 200 chars, and
     **never include tokens, keys or Authorization headers**. Build reasons from status
     codes and STD's error text, not from raw exception or request reprs. Add a test
     that a token value never appears in the body.
   - Any other path: 404. Methods other than GET (HEAD optional): 405.

3. **`GET /status`** (optional but wanted): JSON with `version`, `stream_id`,
   `last_attempt_at`, `last_success_at` (ISO 8601 UTC or null), `last_error`,
   `last_item_count`, `health_max_age_seconds`, and `healthy` (bool, the same verdict
   as `/healthz`). No secrets.

4. **Config** (`config.py`, validated like the rest):
   - `HEALTH_MAX_AGE`: seconds. Default 3 × `INTERVAL_SECONDS` (900 at the default
     300). It must be ≥ `INTERVAL_SECONDS`, and a bad value names the variable.
   - `HEALTH_PORT`: default `8080`. Listen on all interfaces so STD can reach it.
     Decide whether `HEALTH_BIND` is worth adding. Keep it small and record the
     choice.
   - Don't log request lines at INFO. STD polls every minute, so use DEBUG at most.

5. **Docker healthcheck.** The image already has Python and httpx, and an existing
   `HEALTHCHECK` (heartbeat file). Change the `healthcheck` subcommand to `GET
   http://127.0.0.1:$HEALTH_PORT/healthz` and exit 0 only on 200 `OK`, printing the
   body otherwise. That gives one definition of healthy. Keep writing `HEARTBEAT_FILE`
   and keep accepting the variable so existing deployments don't break, and document
   that the healthcheck no longer reads it. Since one failed cycle now flips health,
   consider `--retries` in the Dockerfile and record the choice. Add `EXPOSE 8080`.

6. **Tests** (pytest + respx, matching the existing style). Test the state/verdict logic
   directly with an injectable clock, plus at least one real HTTP round trip against the
   server on an ephemeral port. Required cases:
   - never succeeded → 503 `starting`;
   - fresh success → 200 `OK`;
   - stale success → 503 `last push Nm ago`;
   - Technitium failure after a success → 503 `technitium: …`;
   - STD failure after a success → 503 `std: …`;
   - plus: 202 pending counts as success; secrets never in the body; `/status` fields;
     `HEALTH_MAX_AGE` default and validation; the healthcheck subcommand's exit codes.

7. **Docs.**
   - `README.md`: add `HEALTH_MAX_AGE` and `HEALTH_PORT` (and `HEALTH_BIND` if added) to
     the configuration table. Replace the "Health check" section with a "Health
     endpoint" section: the `/healthz` contract (status codes, example bodies), the
     `/status` example, how to point STD's http check (mode `body`) at it, and the Docker
     healthcheck. Update the architecture blurb if it says the sidecar only makes
     outbound calls.
   - `docker-compose.example.yml` is the repo's env example (there is no `.env.example`;
     don't add one). Add `HEALTH_MAX_AGE` there, and a commented `ports: - "8080:8080"`
     with a note that it's only needed when STD reaches the sidecar through the host.
     `docker compose -f docker-compose.example.yml config --quiet` must still pass.
   - `CHANGELOG.md` `[Unreleased]`: **Added** (the `/healthz` and `/status` endpoints,
     `HEALTH_MAX_AGE`, `HEALTH_PORT`) and **Changed** (the Docker healthcheck now uses
     `/healthz`, and the heartbeat file is no longer read by it). User-facing prose,
     like the existing entries.
   - `docs/decisions.md`: the new entry (see "Before you start" and steps 1, 2, 4, 5).

8. **Run the local checks**, which are the same as CI: `ruff check .`, `pytest -q`,
   `docker build .`, `docker compose -f docker-compose.example.yml config --quiet`.
   Then smoke-test the built image: run it with dummy env and no reachable Technitium or
   STD, curl `/healthz` from inside the container (via `python -c`), and check you get
   503 with a `technitium:`/`std:` reason or `starting`, never `OK`. Remove the
   container afterwards.

## Conventions to honor

- Python 3.11, ruff (`ruff.toml`), pydantic-settings, httpx, pytest + respx. Small,
  readable code with docstrings: this repo is a reference sidecar. A new module (e.g.
  `technitium_sync/health.py`) is fine. Update the `CLAUDE.md` layout line if you add
  one.
- Don't bump `VERSION`. This ships in the next release via `/release-prep`.
- Commit prefix `feat:`. No `Co-authored-by:`. Work on `dev`.

## When done

1. Update this file's frontmatter: set `status` (completed/failed), `completed` (the
   date), and `result` (one line).
2. `git mv` this file into `prompts/done/` (on success) or `prompts/failed/` (on
   failure). Create the subdir if it doesn't exist yet. (The file isn't tracked yet, so
   `git add` it first or use plain `mv`; either way it ends up in the one commit.)
3. Record any non-obvious decisions in the project's `docs/decisions.md`.
4. Hand off ONE commit covering this prompt file, the files this session modified, and
   the prompt move (the prompt is **not** pre-committed — it bundles in here). Present
   the file list and a one-line message summarising the changes.
   - **If you are a spawned agent:** do **not** commit. Prepare the working tree, then
     report the file list + proposed message back to the orchestrating session, which
     surfaces the `y/n` to the user.
   - **If you are running directly (manual fallback):** ask the user
     `commit these as "<message>"? (y/n)` yourself. On `y`, stage those specific paths
     and commit on the current branch.
   Either way: never `git add -A`, never push, never auto-commit. By default the message
   is a plain one-liner and the branch is whatever the session is on. **Only if** the
   project also adopts `code-checkin-and-pr` do its branch rules (`dev`, never `main`)
   and commit-prefix conventions (`feat:` / `fix:` / `chore:` / `docs:`, no
   `Co-authored-by:`) apply on top.
5. In your report, include the **final endpoint contract**: paths, status codes, the
   content type, example bodies for each case, the `/status` JSON example, and the
   env vars. The orchestrator relays it so the STD check can be set up.
