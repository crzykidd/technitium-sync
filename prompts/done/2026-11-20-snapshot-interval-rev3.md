---
name: 2026-11-20-snapshot-interval-rev3
status: completed
created: 2026-11-20
model: sonnet
completed: 2026-11-20
result: snapshot_interval_seconds sent only at API revision >= 3, meta re-checked after 400/409/413, tests and docs updated in 0.2.0; checks green, uncommitted
---

# Task: send `snapshot_interval_seconds` to STD when it reports API revision ≥ 3

STD added an optional snapshot field, `snapshot_interval_seconds` (60–86400), in API
revision 3. It tells STD how often to expect a snapshot from this producer. technitium-sync
should send it, but **only** when `GET /api/v1/meta` reports `api_revision >= 3`. STD
0.10.0 (revision 2) rejects the unknown field with a 400, so against an older STD the
snapshot must stay byte-for-byte what it is now.

This change is **rolled into the unreleased 0.2.0**. PR #2 (`dev` → `main`, "Release
v0.2.0") is open and the operator is holding it unmerged for this.

## Precondition

STD **0.10.1** (API revision 3) is being released now. The operator said to go ahead
without waiting for it. The contract is in STD's PRD **§18.5 / §18.9** on `origin/main`
of the STD repo. That repo is **read-only**: use
`git -C ../service-tracker-dashboard fetch` and
`git -C ../service-tracker-dashboard show origin/main:docs/PRD.md`. Never edit, check out
into or commit anything there.

Confirm that PR #2 is still open and unmerged (`gh pr view 2`) and that there's no
`v0.2.0` tag or release yet. If 0.2.0 has already shipped, STOP and report.

### What STD 0.10.1 changes (from the STD developer; check it against the PRD)

- An optional `snapshot_interval_seconds` (int, 60–86400) in the snapshot PUT body: how
  often this producer posts.
  - STD marks a stream stale when it's enabled and has had no snapshot for more than
    2 × the interval. The default is 15 min when nothing is set, and an interval the
    operator sets in STD wins.
  - **Omitting the field clears the value STD has stored**, so once STD is revision 3+,
    send it on every snapshot.
- STD 0.10.0 (revision 2) and older reject unknown keys with a 400 (`extra="forbid"`).
- STD now records rejected snapshots (400/409/413) on the stream (`last_rejected_at`,
  `last_rejection`), and the operator can see them.

## Before you start

- Read `CLAUDE.md`, `standards.md`, `docs/decisions.md` (especially "STD API notes") and
  all of `technitium_sync/`, especially `std_client.py`: `meta()`, the revision-header
  watch at about line 79, `self.api_revision`, and how the snapshot body is built. Read
  the existing tests.
- Read the STD **release's** PRD §18.1 (API revision) and §18.5 (ingest contract) for
  the exact field: name, type, range, whether it's top-level in the PUT body, and what
  STD does with it. If the released contract differs from this prompt (for example, a
  different range or name), follow the PRD and record the difference.
- The "never send a snapshot built from a failed read" rule and everything else in the
  payload stay as they are. Never call `/api/dhcp/leases/list`.

## Working tree check

Before making any edits, run `git status --porcelain` and cross-reference the files
this plan needs to modify. If any of those files have uncommitted changes, list them
and ask the user before touching them. Surface unrelated dirty files once as
awareness; don't block. This file (the handoff prompt itself) is exempt — it's
expected to be modified by "When done" below.

## What to do

1. **Gate on the revision.** Store the revision as an int, parsed from `meta`'s
   `api_revision` and kept current by the `X-STD-API-Revision` header the client
   already watches. Add `snapshot_interval_seconds` to the PUT body only when that
   revision is ≥ 3. If it's unknown or unparsable, don't send it; that's the safe side.
   - If STD is downgraded and answers 400 naming the field, the next run must already
     leave it out. The header watch should handle that; add a test.
   - Don't re-call `meta` every run, and don't add a timer to re-check it every N hours.
     The header on every response already tracks the revision.
   - **After a 400, 409 or 413, re-check `GET /api/v1/meta` before the next push**,
     because STD may have been downgraded. If that re-check fails, handle it like the
     existing startup check: nothing is pushed, and the existing retry and backoff
     apply. Keep the rule that nothing is pushed until `meta` has succeeded. The STD
     developer's "if meta fails, omit the field" holds automatically, because we never
     push without `meta`.
   - Log STD's version and revision once at startup, as now. When the revision is
     below 3, log one WARNING: `STD too old for interval reporting (needs API revision ≥ 3
     / STD 0.10.1); staleness uses STD's default`. Log it once per revision change, not
     every run.
   - **Schedule:** keep pushing a full snapshot every interval whatever the stream status.
     A 202 (`pending`, `disabled`, `ignored`) is accepted: no backoff, no stopping. The
     sidecar already does this; add tests that pin it.
   - **Rejections:** keep logging 400, 409 and 413 at ERROR with STD's error details, and
     keep the normal schedule. Change the 400 message so it no longer calls every 400
     "a technitium-sync bug". Mention a possible STD revision mismatch: the field was
     sent to an STD that doesn't accept it, and `meta` will be re-checked. Say in the
     message that the same text appears in STD under the stream's last rejection.
   - Don't send any other new key. `attributes` stays as it is.
2. **The value** is `INTERVAL_SECONDS`, clamped to STD's range. Our minimum is already
   60. Above 86400, either clamp and log once at WARNING, or reject at config time.
   Pick one, keep it small, and record the choice. No new env var unless the PRD makes
   one necessary.
3. **Tests** (pytest + respx):
   - revision 2 → the field is absent (assert the exact JSON keys);
   - revision 3 → present with the interval;
   - revision 4 → present;
   - revision missing or garbage → absent;
   - the header changes 2 → 3 mid-run → sent from the next snapshot on;
   - 3 → 2 → dropped;
   - clamping or validation at the bounds;
   - a failed `meta` → no push, so no field;
   - a 202 with `pending`, `disabled` or `ignored` keeps the normal schedule (no backoff);
   - a 400 (and 409, 413) → `meta` is re-checked before the next push, and a
     downgrade to revision 2 drops the field;
   - the below-3 warning is logged once.
4. **Docs, in the unreleased 0.2.0** (not `[Unreleased]`):
   - `CHANGELOG.md`: add an **Added** entry under the existing `## [0.2.0] — <date>`
     section. Say that it's sent only to STD API revision 3+ (name the STD version that
     ships it), and that STD 0.10.0 keeps working unchanged. Update the header date to
     today.
   - `README.md`: extend the `### v0.2.0` What's New entry by one sentence and update its
     date to match. In "Write your own sidecar" / "What gets sent", mention the field and
     the revision gate. State that the requirement is still STD 0.10.0+ (the field is
     optional), unless the PRD says otherwise.
     - Add a **compatibility table**: STD 0.10.0 = revision 2, the interval isn't sent;
       STD 0.10.1+ = revision 3, the interval is sent.
     - Explain STD's staleness (no snapshot for more than 2 × the interval) next to
       our `HEALTH_MAX_AGE` (3 × the interval, our own `/healthz`), so readers don't
       mix them up.
     - Add the new log lines to the "Reading the logs" table.
   - `docs/decisions.md`: a new entry, newest at top, for the gating approach and the
     clamp choice. Add an item to **STD API notes**: an optional field that an older
     revision rejects with a 400 forces producers to gate on the revision. Ignoring
     unknown fields would be friendlier. Number it after the existing notes.
5. **Local checks** (the same as CI): `ruff check .`, `pytest -q`,
   `python -c "import yaml, glob, json; ..."` (as in `.claude/commands/release-prep.md`
   step 6), `docker compose -f docker-compose.example.yml config --quiet`,
   `docker build .`. Then grep `README.md`, `CHANGELOG.md` and `docs/` for a stale
   0.2.0 date.

## Conventions to honor

- Python 3.11, ruff, small readable code with docstrings: this is a reference sidecar.
- Leave `VERSION` at `0.2.0` and don't add a version bump.
- Commit prefix `feat:`, no `Co-authored-by:`, on `dev`. Never push to `main`, and never
  tag.

## When done

1. Update this file's frontmatter: set `status` (completed/failed), `completed` (the
   date), and `result` (one line).
2. `git mv` this file into `prompts/done/` (on success) or `prompts/failed/` (on
   failure). Create the subdir if it doesn't exist yet. (The file isn't tracked, so use
   plain `mv`; it gets added in the one commit.)
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
5. **For the orchestrator (after the commit is pushed to `dev`):** PR #2 picks up the
   commit automatically, but its description is the old `[0.2.0]` section. Regenerate
   it from the updated CHANGELOG section with `gh pr edit 2 --body-file <file>`, so the
   PR body and the later `/release-cut` notes stay the same text. Then wait for PR #2's
   checks to pass, and tell the operator it's ready to merge.
