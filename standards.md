# Standards implemented

This project implements the following [standards](https://gitea.crzynet.com/crzynet/homelab-configs/src/branch/main/standards)
from the crzynet `homelab-configs` repo. Each row pins the **version** that this
project has actually wired up.

| Standard | Version | Adopted | Notes |
|---|---|---|---|
| [release-prep-and-cut](https://gitea.crzynet.com/crzynet/homelab-configs/src/branch/main/standards/release-prep-and-cut/README.md) | 1.1.0 | 2026-11-17 | `VERSION` (bare semver) is the source of truth; the Dockerfile copies it and writes `version.txt` from it; `publish.yml` guards release tag == `VERSION`. Commands in `.claude/commands/`. No release cut yet: `0.1.0` waits for a live test against an STD with streams. |
| [code-checkin-and-pr](https://gitea.crzynet.com/crzynet/homelab-configs/src/branch/main/standards/code-checkin-and-pr/README.md) | 1.2.0 | 2026-11-17 | Checks: `ci.yml` (Lint, Test, Config validation, Compose validation), `publish.yml` job `build` (image build; PR → build only), `codeql.yml` (SAST, job `Analyze (python)`). **Migration check: N/A**, no database. SAST is a CodeQL **advanced-setup workflow**, not default setup (default setup must stay off). CI and CodeQL run on push to `dev`/`main` only, not `pull_request` (the PR's head commit is the `dev` push; avoids duplicate check runs, as in STD). Retention on GHCR via `retention.yml` (30 sha, 15 semver, protected tags kept). |
| [handoff-prompt-workflow](https://gitea.crzynet.com/crzynet/homelab-configs/src/branch/main/standards/handoff-prompt-workflow/README.md) | 2.0.0 | 2026-11-17 | `prompts/TEMPLATE.md` copied verbatim; snippet pasted verbatim into `CLAUDE.md`; decision log at `docs/decisions.md`. |
