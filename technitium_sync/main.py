"""The sync loop: read Technitium, build a full snapshot, PUT it to STD, sleep, repeat."""

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import httpx

from technitium_sync.config import MAX_REPORTED_INTERVAL, ConfigError, Settings, load_settings
from technitium_sync.health import HealthState, start_health_server
from technitium_sync.mapping import MAX_BODY_BYTES, MAX_ITEMS, build_items
from technitium_sync.std_client import Outcome, StdClient
from technitium_sync.technitium import TechnitiumClient, TechnitiumError, read_reservations
from technitium_sync.version import PRODUCER, __version__, build_info

log = logging.getLogger("technitium_sync")

BACKOFF_START = 5


class Backoff:
    """Capped exponential backoff for 5xx and network errors: 5, 10, 20, … up to `cap` seconds."""

    def __init__(self, cap: int, start: int = BACKOFF_START):
        self.cap, self.start = cap, start
        self._next = start

    def next(self) -> int:
        delay = min(self._next, self.cap)
        self._next = min(self._next * 2, self.cap)
        return delay

    def reset(self) -> None:
        self._next = self.start


class Syncer:
    """One run = one snapshot attempt. `run_once` returns the seconds to wait before the next."""

    def __init__(self, settings: Settings, technitium: TechnitiumClient, std: StdClient,
                 health: HealthState | None = None):
        self.settings = settings
        self.technitium = technitium
        self.std = std
        self.health = health or HealthState()
        self.backoff = Backoff(cap=settings.interval_seconds)
        self._std_ready = False
        if settings.interval_seconds > MAX_REPORTED_INTERVAL:
            log.warning("INTERVAL_SECONDS %d is over STD's maximum of %d: STD is told %d, so it may "
                        "mark the stream stale early", settings.interval_seconds,
                        MAX_REPORTED_INTERVAL, MAX_REPORTED_INTERVAL)

    def _retry(self) -> int:
        delay = self.backoff.next()
        log.info("Retrying in %ds", delay)
        return delay

    def _wait_interval(self) -> int:
        self.backoff.reset()
        return self.settings.interval_seconds

    def _fail(self, reason: str) -> None:
        self.health.record_failure(reason)

    def run_once(self) -> int:
        s = self.settings

        # Until STD has confirmed it speaks streams, check meta first: there's
        # no point reading Technitium for a server that can't take the result. Check it
        # again after a 400/409/413, because STD may have been swapped for another version.
        if not self._std_ready or self.std.meta_recheck_needed:
            first_check = not self._std_ready
            outcome = self.std.check_meta()
            if outcome is not Outcome.OK:
                self._fail(self.std.last_error or "std: unavailable")
            if outcome is Outcome.RETRY:
                return self._retry()
            if outcome is Outcome.REJECTED:
                return self._wait_interval()
            self._std_ready = True
            if first_check:
                self.std.log_stream_status(s.stream_id)

        try:
            reservations = read_reservations(self.technitium, s.technitium_scopes)
        except TechnitiumError as exc:
            # Never send a snapshot built from a failed read: STD would mark every
            # device missing.
            log.error("Technitium read failed, skipping this run (nothing sent): %s", exc)
            self._fail(f"technitium: {exc.reason}")
            return self._wait_interval()

        result = build_items(reservations)
        if result.skipped:
            log.warning("%d reservation(s) skipped (no hostname: %d, bad MAC: %d, too long: %d, "
                        "duplicate MAC: %d)", result.skipped, result.skipped_no_name,
                        result.skipped_bad_mac, result.skipped_too_long, result.duplicates)
        if len(result.items) > MAX_ITEMS:
            log.error("%d items is over STD's limit of %d per snapshot; skipping this run "
                      "(a partial snapshot would mark the rest missing). Narrow TECHNITIUM_SCOPES.",
                      len(result.items), MAX_ITEMS)
            self._fail(f"snapshot too large ({len(result.items)} items > {MAX_ITEMS})")
            return self._wait_interval()
        if not reservations:
            log.warning("Technitium returned no reservations in the selected scopes: sending an "
                        "empty snapshot (STD will mark every entry of this stream missing)")
        elif not result.items:
            log.warning("No usable reservations after skipping: sending an empty snapshot")

        payload = {
            "display_name": s.stream_display_name,
            "producer": PRODUCER,
            "producer_version": __version__,
            "key_type": "mac",
            "items": result.items,
        }
        if self.std.interval_supported:
            # Optional, revision 3+ only; STD clears the stored value when it is absent,
            # so it goes on every snapshot. An older STD would answer 400.
            payload["snapshot_interval_seconds"] = s.reported_interval_seconds
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            log.error("Snapshot body is %d bytes, over STD's 2 MiB limit; skipping this run",
                      len(body))
            self._fail(f"snapshot too large ({len(body)} bytes > {MAX_BODY_BYTES})")
            return self._wait_interval()

        log.debug("Sending snapshot: %d item(s), %d bytes", len(result.items), len(body))
        outcome = self.std.put_snapshot(s.stream_id, body)
        if outcome is Outcome.OK:
            touch_heartbeat(s.heartbeat_file)
            self.health.record_success(len(result.items))
        else:
            self._fail(self.std.last_error or "std: snapshot not accepted")
        if outcome is Outcome.RETRY:
            return self._retry()
        return self._wait_interval()


def touch_heartbeat(path: str) -> None:
    try:
        Path(path).write_text(str(int(time.time())), encoding="utf-8")
    except OSError as exc:
        log.warning("Cannot write heartbeat file %s: %s", path, exc)


def healthcheck(env: dict | None = None) -> int:
    """Exit 0 only when the sidecar's own /healthz answers 200 `OK`, else print why and exit 1.

    Reads only HEALTH_PORT, so it runs without the secrets being valid. Used by the
    Docker HEALTHCHECK, which makes `/healthz` the one definition of healthy.
    """
    env = os.environ if env is None else env
    try:
        port = int(env.get("HEALTH_PORT", "8080"))
    except ValueError:
        port = 8080
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=5)
    except httpx.HTTPError as exc:
        print(f"unhealthy: /healthz not reachable on port {port} ({type(exc).__name__})")
        return 1
    body = response.text.strip()
    if response.status_code == 200 and body == "OK":
        print("OK")
        return 0
    print(body or f"HTTP {response.status_code}")
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["healthcheck"]:
        return healthcheck()
    if argv:
        print("usage: python -m technitium_sync [healthcheck]", file=sys.stderr)
        return 2

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"technitium-sync: {exc}", file=sys.stderr)
        return 1

    logging.basicConfig(level=settings.log_level_number,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request at INFO; that's noise every five minutes.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(max(settings.log_level_number, logging.WARNING))

    build = build_info()
    log.info("%s %s (commit %s, channel %s) starting: Technitium %s → STD %s, stream %r, every %ds, scopes: %s",
             PRODUCER, __version__, build.get("commit", "local")[:12], build.get("channel", "local"),
             settings.technitium_url, settings.std_url, settings.stream_id,
             settings.interval_seconds, ", ".join(settings.technitium_scopes) or "all enabled")

    stop = threading.Event()

    def _on_signal(signum, _frame):
        log.info("Received %s, shutting down", signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    technitium = TechnitiumClient(settings.technitium_url,
                                  settings.technitium_token.get_secret_value(),
                                  verify_tls=settings.technitium_verify_tls)
    std = StdClient(settings.std_url, settings.std_ingest_key.get_secret_value(),
                    verify_tls=settings.std_verify_tls)
    health = HealthState(
        max_age=settings.health_max_age, stream_id=settings.stream_id,
        secrets=[settings.technitium_token.get_secret_value(),
                 settings.std_ingest_key.get_secret_value()])
    try:
        health_server = start_health_server(health, settings.health_port)
    except OSError as exc:
        print(f"technitium-sync: cannot listen on HEALTH_PORT {settings.health_port}: {exc}",
              file=sys.stderr)
        return 1
    log.info("Health endpoint on port %d (/healthz, /status), max push age %ds",
             settings.health_port, settings.health_max_age)
    syncer = Syncer(settings, technitium, std, health)
    try:
        while not stop.is_set():
            try:
                delay = syncer.run_once()
            except Exception:  # keep running: one bad run must not kill the sidecar
                log.exception("Unexpected error in sync run")
                health.record_failure("internal error (see logs)")
                delay = settings.interval_seconds
            if stop.wait(delay):
                break
    finally:
        health_server.shutdown()
        technitium.close()
        std.close()
    log.info("Stopped")
    return 0
