"""The sync loop: read Technitium, build a full snapshot, PUT it to STD, sleep, repeat."""

import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

from technitium_sync.config import ConfigError, Settings, load_settings
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

    def __init__(self, settings: Settings, technitium: TechnitiumClient, std: StdClient):
        self.settings = settings
        self.technitium = technitium
        self.std = std
        self.backoff = Backoff(cap=settings.interval_seconds)
        self._std_ready = False

    def _retry(self) -> int:
        delay = self.backoff.next()
        log.info("Retrying in %ds", delay)
        return delay

    def _wait_interval(self) -> int:
        self.backoff.reset()
        return self.settings.interval_seconds

    def run_once(self) -> int:
        s = self.settings

        # Until STD has confirmed it speaks streams, check meta first: there's
        # no point reading Technitium for a server that can't take the result.
        if not self._std_ready:
            outcome = self.std.check_meta()
            if outcome is Outcome.RETRY:
                return self._retry()
            if outcome is Outcome.REJECTED:
                return self._wait_interval()
            self._std_ready = True
            self.std.log_stream_status(s.stream_id)

        try:
            reservations = read_reservations(self.technitium, s.technitium_scopes)
        except TechnitiumError as exc:
            # Never send a snapshot built from a failed read: STD would mark every
            # device missing.
            log.error("Technitium read failed, skipping this run (nothing sent): %s", exc)
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
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            log.error("Snapshot body is %d bytes, over STD's 2 MiB limit; skipping this run",
                      len(body))
            return self._wait_interval()

        log.debug("Sending snapshot: %d item(s), %d bytes", len(result.items), len(body))
        outcome = self.std.put_snapshot(s.stream_id, body)
        if outcome is Outcome.RETRY:
            return self._retry()
        if outcome is Outcome.OK:
            touch_heartbeat(s.heartbeat_file)
        return self._wait_interval()


def touch_heartbeat(path: str) -> None:
    try:
        Path(path).write_text(str(int(time.time())), encoding="utf-8")
    except OSError as exc:
        log.warning("Cannot write heartbeat file %s: %s", path, exc)


def healthcheck(env: dict | None = None, now: float | None = None) -> int:
    """Exit 0 when the heartbeat is fresher than 3 × INTERVAL_SECONDS, else 1.

    Reads only HEARTBEAT_FILE and INTERVAL_SECONDS, so it runs without the
    secrets being valid. Used by the Docker HEALTHCHECK.
    """
    env = os.environ if env is None else env
    path = env.get("HEARTBEAT_FILE", "/tmp/technitium-sync.heartbeat")
    try:
        interval = max(int(env.get("INTERVAL_SECONDS", "300")), 60)
    except ValueError:
        interval = 300
    try:
        age = (time.time() if now is None else now) - os.path.getmtime(path)
    except OSError:
        print(f"unhealthy: no heartbeat at {path} yet")
        return 1
    if age > 3 * interval:
        print(f"unhealthy: last successful snapshot {int(age)}s ago (limit {3 * interval}s)")
        return 1
    print(f"healthy: last successful snapshot {int(age)}s ago")
    return 0


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
    syncer = Syncer(settings, technitium, std)
    try:
        while not stop.is_set():
            try:
                delay = syncer.run_once()
            except Exception:  # keep running: one bad run must not kill the sidecar
                log.exception("Unexpected error in sync run")
                delay = settings.interval_seconds
            if stop.wait(delay):
                break
    finally:
        technitium.close()
        std.close()
    log.info("Stopped")
    return 0
