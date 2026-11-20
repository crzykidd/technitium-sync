#!/usr/bin/env bash
# Live test harness (phase 1): a throwaway Technitium + STD :dev + technitium-sync, no published ports.
#   ./run.sh up       start Technitium + STD, seed both, write tokens to the gitignored .env
#   ./run.sh sync     start (or recreate) the sidecar with the current .env
#   ./run.sh ctl ...  scenario helper (see ctl.py)
#   ./run.sh down     tear everything down (containers, volumes, network, built images)
set -euo pipefail
cd "$(dirname "$0")"
export COMPOSE_PROJECT_NAME=tsync-livetest
dc() { timeout 300 docker compose "$@"; }
tools() { dc --profile tools run --rm -T tools "$@" 2> >(grep -v ' Container \| Network ' >&2); }

case "${1:-}" in
  up)
    [ -f .env ] || printf 'TECH_ADMIN_PASSWORD=%s\n' "$(head -c12 /dev/urandom | base64 | tr -dc A-Za-z0-9)" > .env
    dc --profile tools build tools
    dc up -d technitium std
    sleep 10
    tools /live/seed_technitium.py | grep '^TECHNITIUM_TOKEN=' >> .env
    tools /live/bootstrap_std.py >> .env
    echo "seeded; tokens are in tests/live/.env (gitignored)" ;;
  sync) dc --profile sync up -d --build --force-recreate technitium-sync ;;
  ctl) shift; tools /live/ctl.py "$@" ;;
  down) dc --profile sync --profile tools down -v --rmi local --remove-orphans ;;
  *) sed -n 2,7p "$0"; exit 2 ;;
esac
