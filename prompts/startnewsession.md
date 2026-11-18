# Start a new session: live test phase 2, then prepare 0.1.0

Paste this into a fresh Claude Code session in this repo, or say "read
prompts/startnewsession.md and start".

## What this project is

`technitium-sync` is an **independent** sidecar. It reads Technitium DHCP
**static reservations** and pushes them as a full snapshot to Service Tracker
Dashboard's (STD) stream ingest API (`PUT /api/v1/streams/<id>/snapshot`, STD
PRD §18.5). STD is an external API to us. **Never edit, move or commit anything
in the STD repo** (`../service-tracker-dashboard` is read-only reference). If
the API is awkward from our side, record it under "STD API notes" in
`docs/decisions.md` and tell the operator, who raises it with STD.

Standards (see `CLAUDE.md` / `standards.md`): handoff-prompt-workflow 2.0.0,
code-checkin-and-pr 1.2.0, release-prep-and-cut 1.1.0. Work on `dev`; `main`
is protected (PR only, 6 required checks); propose commits `y/n`, never
`git add -A`, no `Co-authored-by:`, and push only with the operator's OK.

## Where things stand (2026-11-18)

- **Version:** `0.1.0` in `VERSION`, unreleased. No tags. Everything is under
  CHANGELOG `[Unreleased]`.
- **Branches:** `main` holds only the bootstrap commit (`9cdd091`). `dev` on
  GitHub is at `f8715be` (the initial sidecar); CI, CodeQL and the image
  build were green there, and `ghcr.io/crzykidd/technitium-sync:dev` is public.
- **Local, not pushed:** `3c69045` (fix: distinct log for a disabled stream,
  plus the live test phase 1 harness in `tests/live/` and results in
  `docs/live-test-phase1.md`). **Ask the operator before pushing it.**
- **Live test phase 1: done.** 13/13 scenarios passed against a throwaway
  Technitium 15.6.2 and STD `:dev` (sha256:a5cefd56…, reports 0.9.0 / API
  revision 2, has the streams routes). Re-run with `tests/live/run.sh` (see the
  README "Development" section).
- **Handoff prompts:** kickoff and phase 1 are in `prompts/done/`. The queue is
  empty.
- **GitHub/GHCR:** branch protection on `main` is set (admins enforced). The
  GHCR package is public and linked to the repo; the operator granted the repo
  Admin in "Manage Actions access" (not verifiable by API). `retention.yml`
  only starts running once it's on `main`.
- **homelab-configs registry:** done (`projects/technitium-sync/README.md` and
  the row in `projects/README.md`).

## This session's job

1. **Push `3c69045` to `dev`** if the operator agrees, and check CI is green.
2. **Live test phase 2** runs on **another machine** that can reach the
   operator's real Technitium and a **dev copy of STD** the operator deploys
   (not production). Ask the operator for:
   - the Technitium URL, and a read-only token (DhcpServer: View, a dedicated
     user; README "Create a read-only Technitium API token"). Keep it in a
     gitignored `.env` and never paste or log it;
   - the dev STD URL and an `ingest` key created there;
   - which scopes (or all enabled ones).

   Write it as a handoff prompt (`prompts/` from `TEMPLATE.md`), modelled on
   `prompts/done/2026-11-17-live-test-phase1-local.md`, but against real data:
   - item counts, skipped items (no hostname, too long) and duplicate MACs on
     the real reservations;
   - snapshot size against the 2 MiB limit;
   - pending → preview → enable in the dev STD, and the next run's counts;
   - anything surprising in real hostnames, MAC formats or comments.

   Record the results in `docs/live-test-phase2.md`.
3. **STD API notes:** `docs/decisions.md` has 10. Ask the operator which ones
   STD has changed. If STD changes the contract (e.g. adds reason codes or a
   distinct disabled message), adapt the sidecar after STD ships it, never
   before.
4. **Release 0.1.0** once phase 2 passes and STD 0.10.0 (the streams API) is
   released: `/release-prep 0.1.0` → the operator merges the PR → `/release-cut
   0.1.0`. Afterwards check that `retention.yml` runs on `main` without a 403
   (that proves the GHCR Admin access).

## Host notes (the dev box, crzydev)

- Docker's default log driver was a dead Loki (`docker03:3100`), which hung
  containers. On 2026-11-18 `/etc/docker/daemon.json` was switched to
  `json-file` (10 MB × 3; backup `daemon.json.bak-20261117-210559`), the Loki
  plugin was removed, and every old dev container was deleted (the operator
  said it's all test data; each project recreates its own).
- About 140 anonymous Docker volumes from those containers are still there.
  `docker volume prune` would clear them; the operator hasn't decided.
- This host's DHCP lease hands out only the `home.arpa` search domain, so
  short names like `docker03` don't resolve (FQDNs like
  `docker03.crzynet.com` do).
- Publishing host ports (`-p`) misbehaved while Loki was broken. The phase 1
  harness uses no host ports, so this wasn't retested afterwards.
