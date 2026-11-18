# technitium-sync — Claude Code Instructions

A small, independent sidecar: it reads **Technitium DHCP static reservations** and
pushes them to **Service Tracker Dashboard (STD)** as a full inventory snapshot through
STD's stream ingest API (`PUT /api/v1/streams/<id>/snapshot`, STD PRD §18.5). Shipped
as `ghcr.io/crzykidd/technitium-sync`. It's also meant to be a readable reference for
people writing their own sidecars, so keep it small, readable and documented.

## Project rules

- **STD is an external API.** This project consumes it and is not part of STD's
  development. Never edit, move or commit anything in the STD repo. Code against
  STD PRD §18.5 (ingest contract) and §18.1 (API revision, `meta`). When the contract
  is awkward from the producer side, don't work around it: record it under "STD API
  notes" in `docs/decisions.md` so it can be raised with STD.
- **The name is `technitium-sync` everywhere**: `producer`, `User-Agent:
  technitium-sync/<VERSION>`, the image and the docs (not STD's working name
  `std-source-technitium`).
- **Never send a snapshot built from a failed read.** A partial or empty snapshot
  makes STD mark devices missing. Only send `[]` when Technitium really returned no
  reservations.
- Never call Technitium's `/api/dhcp/leases/list` (dynamic leases). Reservations only.
- House style: Python 3.11, ruff (`ruff.toml`), pydantic / pydantic-settings, httpx,
  pytest + respx. `VERSION` holds the bare semver. `CHANGELOG.md` follows Keep a
  Changelog; every operator-visible change gets an `[Unreleased]` entry in the same
  commit. Non-obvious calls go in `docs/decisions.md`, newest at top.
- Layout: `technitium_sync/` (`config.py`, `technitium.py`, `mapping.py`,
  `std_client.py`, `main.py`), run as `python -m technitium_sync`. Tests in `tests/`,
  fixtures in `tests/fixtures/`.
- Local checks (the same as CI): `ruff check .`, `pytest -q`, `docker build .`,
  `docker compose -f docker-compose.example.yml config --quiet`.
- Dependencies: edit `requirements.in`, then
  `uv pip compile --python-version 3.11 requirements.in -o requirements.txt`.

## Standards

This project adopts crzynet engineering standards from `homelab-configs`.
**Read [`standards.md`](standards.md) at session start** whenever the work
could touch anything a standard governs (branching, commits, CI, releases,
handoff prompts). It lists each adopted standard with its pinned version.

<!--
Source: standards/handoff-prompt-workflow @ v2.0.0 (crzynet/homelab-configs).
Paste the section below verbatim into the adopting project's CLAUDE.md.
The full standard (the plan→decide→execute→document principle, model selection,
TEMPLATE, adoption checklist) lives at:
https://gitea.crzynet.com/crzynet/homelab-configs/src/branch/main/standards/handoff-prompt-workflow/README.md
-->

## Handoff prompts (operational rules)

This project adopts the `handoff-prompt-workflow` standard. The full why-and-how lives at
the source above; the rules below are the per-session do/don'ts an agent must honor by
default:

- **Edit-size threshold — decide by how much you'll change:**
  - A genuinely small change — roughly **one or two files and a few lines** (a typo, one
    config value, a one-line fix) — do it **in-session**, no prompt.
  - **Anything bigger requires a handoff prompt** — more than ~2 files, a multi-step
    change, a new feature, or any edit large enough that a fresh context would run it
    more cleanly. **When in doubt, write the prompt.**
- **A handoff prompt is a file in `prompts/`** — one per task, from `prompts/TEMPLATE.md`,
  with frontmatter (`name`, `status`, `created`, `model`, `completed`, `result`). Set
  `model:` from the task type: **Opus** for research/planning, **Sonnet** for coding;
  mixed defaults to Opus.
- **Execute the prompt by spawning a subagent — don't hand the user a command.** Spawn an
  agent on the prompt's `model:`, let it run the prompt end-to-end, and **report the
  outcome back**. The agent gets a fresh context; you stay in the loop.
  - **Manual fallback only on explicit request.** If the user says e.g. "use manual
    prompts for this," give them
    `claude --model <model> "Read prompts/<file>.md and execute it as your task."`
    instead of spawning.
- **Check the working tree before editing.** Run `git status --porcelain`, cross-reference
  the files the plan touches; if any have uncommitted changes, list them and ask before
  touching. Surface unrelated dirty files once; they don't block.
- **The prompt self-updates and moves when done.** The executing agent sets its
  frontmatter (`status`/`completed`/`result`) and `git mv`s the file into `prompts/done/`
  (success) or `prompts/failed/` (failure).
- **One commit at the end; the prompt bundles in.** The prompt file is **not** committed
  up front — it lands in the single end commit alongside the work and the prompt move.
  Propose ONE commit (files list + one-line message), ask `y/n`, stage only those specific
  paths. **Never `git add -A`, never auto-commit, never push.** A spawned agent prepares
  the tree and reports the proposed commit back; the orchestrating session surfaces the
  `y/n`.
- **Record non-obvious decisions** (approach changes, rejected alternatives, workarounds)
  in `docs/decisions.md`, newest at top.

If you're unsure whether an action would violate one of the above, stop and ask before
acting.

<!--
Source: standards/code-checkin-and-pr @ v1.2.0 (crzynet/homelab-configs).
Paste the section below verbatim into the adopting project's CLAUDE.md.
The full standard (publishing matrix, retention, CI check definitions) lives at:
https://gitea.crzynet.com/crzynet/homelab-configs/src/branch/main/standards/code-checkin-and-pr/README.md
-->

## Code check-in (operational rules)

This project adopts the `code-checkin-and-pr` standard. The full why-and-how lives at
the source above; the rules below are the per-session do/don'ts a coding agent must
honor by default:

- **Never push directly to `main`.** `main` is protected. All changes land via a pull
  request from `dev` → `main`, and only when every required check is green.
- **Day-to-day work happens on `dev`** (or a short-lived branch off `dev`). Push to
  `dev` freely.
- **Commit message prefixes are required** — Conventional-Commits style:
  - `feat:` — new user-facing feature
  - `fix:` — bug fix
  - `chore:` — config, tooling, dependencies, maintenance
  - `docs:` — documentation-only changes
- **Do not add `Co-authored-by:` trailers** unless the user explicitly asks.
- **Doc updates ship in the same commit as the code they describe** — never as a
  follow-up commit.
- **Never bypass hooks** (no `--no-verify`, `--no-gpg-sign`, etc.) unless the user
  explicitly asks. If a hook fails, fix the underlying issue.
- **Stable releases are tagged from `main` only.** Don't tag from `dev`.

If you're unsure whether an action would violate one of the above, stop and ask before
acting.

<!--
Source: standards/release-prep-and-cut @ v1.1.0 (crzynet/homelab-configs).
Paste the section below verbatim into the adopting project's CLAUDE.md.
The full standard (two-phase prep/cut workflow, archive trigger, validation
steps, adoption checklist) lives at:
https://gitea.crzynet.com/crzynet/homelab-configs/src/branch/main/standards/release-prep-and-cut/README.md
-->

## Release process (operational rules)

This project adopts the `release-prep-and-cut` standard. The full why-and-how
lives at the source above; the rules below are the per-session do/don'ts a
coding agent must honor by default:

- **The version is stored BARE in the source-of-truth file** — no `v` prefix
  anywhere in code. The `v` prefix is added in exactly one place: the git tag
  and matching GitHub release name. Don't add it to README badges, CHANGELOG
  headers, in-code image tags, or anywhere else.
- **`CHANGELOG.md` is the single source of truth for release notes.** The PR
  description (set by `/release-prep`) and the GitHub release body (set by
  `/release-cut`) reuse the **same section verbatim**. Never author release
  notes twice.
- **One commit per release prep.** Version bump + changelog roll + every doc
  sync ship in a single `chore(release): prepare v<version>` commit. No
  `Co-authored-by:` trailers.
- **Never re-tag.** If `v<version>` already exists as a local tag, a remote
  tag, or a GitHub release, STOP. Never delete-and-recreate; never `--force`.
  Pick the next version instead.
- **`/release-cut` only after the PR has merged and CI is green.** The
  publish-to-`main` workflow must have already pushed `:latest` images to the
  registry before `/release-cut` runs. If you cannot confirm both — STOP and
  tell the user to wait.
- **The release tag is the only thing the cut command writes to `main`.** Both
  the prep commit and any follow-up docs commit land on `dev` and reach `main`
  only via PR. Never push directly to `main` as part of a release.

If you're unsure whether an action would violate one of the above, stop and
ask before acting.
