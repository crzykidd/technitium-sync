"""Push health: in-memory state plus the small HTTP server that reports it.

`GET /healthz` answers one plain-text line built for STD's http check mode `body`
(up only when the body is exactly `OK`). `GET /status` returns the same verdict
as JSON. Both read a `HealthState` that the sync loop updates after every cycle.
"""

import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from technitium_sync.version import __version__

log = logging.getLogger(__name__)

MAX_REASON_CHARS = 200


def clean_reason(text: str, secrets: Iterable[str] = ()) -> str:
    """One short line that is safe to publish: no newlines, secrets blanked, capped."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_REASON_CHARS:
        text = text[: MAX_REASON_CHARS - 1] + "…"
    return text


def _age(seconds: float) -> str:
    return f"{int(seconds)}s" if seconds < 60 else f"{int(seconds // 60)}m"


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class HealthState:
    """Thread-safe record of the last sync cycle. The loop writes, the HTTP threads read.

    A cycle succeeds only when Technitium was read **and** STD accepted the snapshot
    (200, or 202 pending/disabled/ignored). `clock` is injectable for tests.
    """

    def __init__(self, max_age: float = 900, stream_id: str = "", secrets: Iterable[str] = (),
                 clock: Callable[[], float] = time.time):
        self.max_age = max_age
        self.stream_id = stream_id
        self._secrets = tuple(secrets)
        self._clock = clock
        self._lock = threading.Lock()
        self.last_attempt_at: float | None = None
        self.last_success_at: float | None = None
        self.last_error: str | None = None
        self.last_item_count: int | None = None

    def record_success(self, item_count: int) -> None:
        with self._lock:
            self.last_attempt_at = self.last_success_at = self._clock()
            self.last_error = None
            self.last_item_count = item_count

    def record_failure(self, reason: str) -> None:
        with self._lock:
            self.last_attempt_at = self._clock()
            self.last_error = clean_reason(reason, self._secrets) or "unknown error"

    def verdict(self) -> tuple[bool, str]:
        """(healthy, body): body is exactly `OK` when healthy, else the reason."""
        with self._lock:
            if self.last_attempt_at is None:
                return False, "starting"
            if self.last_error:
                return False, self.last_error
            age = self._clock() - (self.last_success_at or 0)
            if age > self.max_age:
                return False, f"last push {_age(age)} ago"
            return True, "OK"

    def status(self) -> dict:
        healthy, _ = self.verdict()
        with self._lock:
            return {
                "version": __version__,
                "stream_id": self.stream_id,
                "last_attempt_at": _iso(self.last_attempt_at),
                "last_success_at": _iso(self.last_success_at),
                "last_error": self.last_error,
                "last_item_count": self.last_item_count,
                "health_max_age_seconds": self.max_age,
                "healthy": healthy,
            }


def _handler_for(state: HealthState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "technitium-sync"

        def _send(self, code: int, body: str, content_type: str, head: bool = False) -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def _route(self, head: bool) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                healthy, body = state.verdict()
                self._send(200 if healthy else 503, body, "text/plain; charset=utf-8", head)
            elif path == "/status":
                self._send(200, json.dumps(state.status()), "application/json", head)
            else:
                self._send(404, "not found", "text/plain; charset=utf-8", head)

        def do_GET(self):
            self._route(head=False)

        def do_HEAD(self):
            self._route(head=True)

        def _not_allowed(self):
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed

        def log_message(self, format, *args):
            # STD polls every minute: request lines are DEBUG only.
            log.debug("%s %s", self.address_string(), format % args)

    return Handler


def start_health_server(state: HealthState, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    """Serve /healthz and /status on a daemon thread. Port 0 picks a free one (tests)."""
    server = ThreadingHTTPServer((host, port), _handler_for(state))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="health-http", daemon=True).start()
    return server
