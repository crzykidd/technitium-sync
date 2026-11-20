---
name: 2026-11-17-live-test-phase1-local
status: completed        # pending | completed | failed
created: 2026-11-17
model: sonnet            # execution: stand up a test stack and run a scripted test plan
completed: 2026-11-17
result: 13/13 scenarios pass against STD :dev 0.9.0 (sha256:a5cefd56); 1 sidecar fix (disabled-stream message), 2 STD API notes added (9, 10) and note 7 confirmed
---

# Task: Live test phase 1, a throwaway local Technitium + STD

Run `technitium-sync` end to end against a **throwaway Technitium** and a **throwaway
STD**, both in Docker on this machine, with made-up data. This proves the sidecar
against the real STD streams API, not just the respx fakes. Nothing here touches the
operator's real Technitium, real network or production STD.

(Phase 2, real Technitium data against a dev STD, is a separate session the operator
runs on another machine. It's out of scope here.)

## Before you start

- **Gate: STD's published `:dev` image must have the streams API.** STD's `meta`
  already lists `streams` before the routes exist (see `docs/decisions.md` → STD API
  notes), so **don't trust `meta`**. Instead:
  1. `docker pull ghcr.io/crzykidd/service-tracker-dashboard:dev`, run it, and send
     `PUT /api/v1/streams/probe/snapshot` with no auth header.
  2. **401** means the route exists, so go ahead. **404** means it isn't there yet:
     stop, set nothing, leave this prompt pending, and report "STD :dev has no streams
     routes yet".
- STD is an external API: use **only its published image**. Never build from, edit or
  run git write commands in `../service-tracker-dashboard`. You may read its
  `docs/PRD.md` §18.5–§18.8 and README for how to bootstrap an admin and create an
  `ingest` key, and how Settings → Streams preview, enable and disable work (UI,
  management API or MCP, whichever is scriptable).
- Read this repo's `README.md`, `docs/decisions.md` and `technitium_sync/` so you know
  the env vars and the expected log lines.
- Technitium: the official `technitium/dns-server` image. Its API docs are
  https://github.com/TechnitiumSoftware/DnsServer/blob/master/APIDOCS.md: `user/login`
  or an initial admin password env, `admin/sessions/createToken` (or `user/createToken`),
  `dhcp/scopes/set`, `dhcp/scopes/addReservedLease`, `dhcp/scopes/enable` /
  `disable`, `admin/users/*` and `admin/groups/*` for a View-only user.

## Working tree check

Before making any edits, run `git status --porcelain` and cross-reference the files
this plan needs to modify. If any of those files have uncommitted changes, list them
and ask the user before touching them. Surface unrelated dirty files once as
awareness; don't block. This file (the handoff prompt itself) is exempt. It's
expected to be modified by "When done" below.

## What to do

1. **Test stack** (`docker compose -p tsync-livetest`, with the compose file and
   scripts under `tests/live/`, committed so the test can be repeated):
   - an isolated bridge network. **Publish no DNS (53) or DHCP (67/68) ports**, and no
     host networking. Expose Technitium's 5380 and STD's web port only on
     `127.0.0.1`, if at all;
   - Technitium with a known admin password (a local test value, not a real secret).
     Don't enable the DHCP server's listening on any real interface. The scopes only
     need to exist and be readable through the API;
   - STD `:dev` with an empty data volume;
   - technitium-sync built from this repo (`build: ../..`), with `INTERVAL_SECONDS=60`
     and `LOG_LEVEL=DEBUG`.
2. **Seed Technitium** with a script (`tests/live/seed_technitium.py` or `.sh`):
   - a dedicated `technitium-sync` user in a group with only **DhcpServer: View**, and
     an API token for it. This proves the README's read-only token instructions. If a
     View-only token can't read reservations, that's a finding;
   - scope `LAN` (enabled): about 5 normal reservations, one with **no hostname**, one
     with a 300-char comment, one with an empty comment;
   - scope `IoT` (enabled): about 3 reservations, one using a MAC that's also reserved
     in `LAN`;
   - scope `Lab` (**disabled**): 2 reservations;
   - all names, MACs and IPs are clearly fake (e.g. `02:00:00:..` locally-administered
     MACs, `10.99.x.x`). If Technitium itself rejects a case (e.g. a duplicate MAC
     across scopes), record that and adapt.
3. **Bootstrap STD:** an admin, then an API key with scope **Ingest** (the README's
   steps). Record exactly how you did it; the README should match.
4. **Scenarios.** For each one, capture the sidecar log lines and the STD state (stream
   status, counts, entries) as evidence:
   1. **First run → pending.** The stream `technitium-dhcp` appears in STD as
      **pending** with the expected item count. The sidecar logs the 202 warning
      **once**, not every run. The heartbeat file is updated.
   2. **Preview, then enable.** Preview matches what we expect: the no-hostname item
      is skipped, the duplicate MAC becomes one item, `Lab` is excluded and the long
      comment is truncated to 256. Enabling applies it, and the next run logs a 200
      with the counts (all `unchanged` after the first apply).
   3. **Change in Technitium.** Add a reservation and change another's IP. The next
      run shows `created: 1` and `updated: 1`, and STD's entries match.
   4. **Delete a reservation.** The next run shows `missing: 1`, and that entry is
      flagged `inventory_missing` in STD and not deleted.
   5. **`TECHNITIUM_SCOPES=Lab`.** Restart with it: a disabled scope that is named
      explicitly gets read. Revert afterwards.
   6. **Technitium down.** Stop the Technitium container. The sidecar logs the
      failure, **sends no snapshot** (STD's `last_snapshot_at` doesn't move, and
      nothing goes missing) and keeps running. Start Technitium again and it
      recovers on the next run.
   7. **Bad Technitium token.** The `invalid-token` status (returned as HTTP 200) is
      logged clearly, no snapshot is sent, and the loop continues.
   8. **STD down.** Stop STD: backoff retries happen and the sidecar doesn't exit.
      Start STD again: the next post succeeds.
   9. **Bad or wrong STD key.** An invalid key gives 401 and the "check
      STD_INGEST_KEY" log line. If STD makes it easy, also try a second `ingest` key
      on the same stream to get the 403 (bound to another key).
   10. **Disable the stream in STD.** The sidecar logs a 202 pending/disabled once.
       Note whether the message is misleading (known STD API note 7).
   11. **Healthcheck.** `docker inspect` shows the container `healthy` after a good
       run. With the sidecar stuck (Technitium down for longer than 3 × the
       interval) it turns `unhealthy`. At least explain the timing if it's too
       slow to wait out.
   12. **SIGTERM.** `docker stop` on the sidecar exits cleanly within a few seconds
       (exit code 0 or 143, with a shutdown log line). This was never confirmed in
       the container during the kickoff.
   13. **Headers.** The sidecar logged STD's `std_version` and `api_revision` at
       startup, and STD saw the `User-Agent: technitium-sync/<VERSION>` (STD logs
       each new User-Agent once).
5. **Fixes.** If the sidecar misbehaves, fix it in this repo with tests and re-run the
   scenario. If **STD** misbehaves or the contract is awkward, **don't fix or work
   around STD**: add it to `docs/decisions.md` → "STD API notes" with the scenario,
   the request, the response and the §18.5 rule involved.
6. **Write up** `docs/live-test-phase1.md`: the STD image digest and version, the
   Technitium version, how the stack was built, and a pass/fail table for each
   scenario with a one-line note. Link it from the README "Development" section,
   along with how to re-run it.
7. **Tear down:** `docker compose -p tsync-livetest down -v`. Leave no containers,
   volumes or networks behind, and check with `docker ps -a` and `docker volume ls`.
   If Docker hangs (as it did once in the kickoff), report it rather than forcing
   anything destructive on other containers.

## Conventions to honor

- The crzynet standards as adopted (see `CLAUDE.md` / `standards.md`): Conventional
  Commits, no `Co-authored-by:`, work on `dev`, never push to `main`, docs in the same
  commit as code.
- Test credentials are local throwaway values. Still, keep them out of committed files
  (generate them or read them from a gitignored `tests/live/.env`), and never log the
  tokens.
- CHANGELOG `[Unreleased]`: add entries only for real sidecar fixes or user-facing
  changes. The test harness itself goes under "Added" only if it's useful to
  operators; otherwise skip it.
- No release. If every scenario passes, report "ready for phase 2 / 0.1.0 prep".

## When done

1. Update this file's frontmatter: set `status` (completed/failed), `completed` (the
   date), and `result` (one line, e.g. "13/13 scenarios pass against STD :dev
   <version>; 2 STD API notes added").
2. `git mv` this file into `prompts/done/` (on success) or `prompts/failed/` (on
   failure). If it's still untracked, `git add` this one path first. If the gate
   failed (no streams routes), leave the file in `prompts/` as `pending` and only
   report.
3. Record any non-obvious decisions in `docs/decisions.md`.
4. Hand off ONE commit covering this prompt file, the files this session created or
   modified, and the prompt move. Present the file list and a one-line message (e.g.
   `chore: live test phase 1 harness and results against STD :dev`, or `fix: …` if
   sidecar fixes dominate).
   - **If you are a spawned agent:** do **not** commit. Prepare the working tree and
     report the file list, the proposed message, the scenario table, the new STD API
     notes and the teardown status back to the orchestrating session.
   - **If you are running directly (manual fallback):** ask the user
     `commit these as "<message>"? (y/n)` yourself. On `y`, stage those specific paths
     and commit on `dev`.

   Either way: never `git add -A`, never push, never auto-commit.
