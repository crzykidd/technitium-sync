"""Client for STD's stream ingest API (PRD §18.1 and §18.5).

STD is an external API here: this module only sends what §18.5 documents and
interprets the documented responses. Errors (400/401/403/409/413) use STD's
management API error shape, `{"error": str, "details": ...}`, where `details`
for a 400 is a list of `{"loc": [...], "msg": str, "type": str}`.
"""

import enum
import logging

import httpx

from technitium_sync.version import USER_AGENT

log = logging.getLogger(__name__)

REVISION_HEADER = "X-STD-API-Revision"
VERSION_HEADER = "X-STD-Version"
STREAMS_FEATURE = "streams"


class Outcome(enum.Enum):
    """What the loop should do after a request to STD."""

    OK = "ok"  # accepted (200, or 202 stored/ignored): wait the normal interval
    REJECTED = "rejected"  # our request or key is wrong (4xx): wait the normal interval
    RETRY = "retry"  # 5xx or network error: retry soon, with backoff


def describe_error(response: httpx.Response) -> str:
    """One line from STD's error shape, field errors included; falls back to the raw body."""
    try:
        body = response.json()
    except ValueError:
        text = response.text.strip().replace("\n", " ")
        return text[:300] or f"HTTP {response.status_code}"
    if not isinstance(body, dict):
        return str(body)[:300]
    message = str(body.get("error") or f"HTTP {response.status_code}")
    details = body.get("details")
    if isinstance(details, list) and details:
        parts = []
        for d in details:
            if isinstance(d, dict):
                loc = d.get("loc") or d.get("field") or []
                where = ".".join(str(p) for p in loc) if isinstance(loc, list) else str(loc)
                what = d.get("msg") or d.get("message") or ""
                parts.append(f"{where}: {what}" if where else str(what))
            else:
                parts.append(str(d))
        message += " — " + "; ".join(parts)
    elif details:
        message += f" — {details}"
    return message


class StdClient:
    def __init__(self, base_url: str, ingest_key: str, verify_tls: bool = True, timeout: float = 30.0):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {ingest_key}", "User-Agent": USER_AGENT},
            verify=verify_tls,
            timeout=timeout,
        )
        self.api_revision: str | None = None
        self._last_stream_status: str | None = None

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send a request and track `X-STD-API-Revision`. Network errors propagate."""
        response = self._http.request(method, path, **kwargs)
        revision = response.headers.get(REVISION_HEADER)
        if revision is not None and revision != self.api_revision:
            if self.api_revision is None:
                log.info("STD API revision %s (STD %s)", revision, response.headers.get(VERSION_HEADER, "?"))
            else:
                log.info("STD API revision changed: %s -> %s (STD %s)", self.api_revision, revision,
                         response.headers.get(VERSION_HEADER, "?"))
            self.api_revision = revision
        return response

    # --- startup checks ---------------------------------------------------------

    def check_meta(self) -> Outcome:
        """`GET /api/v1/meta`: log STD's version; OK only when STD advertises `streams`."""
        try:
            response = self._request("GET", "/api/v1/meta")
        except httpx.HTTPError as exc:
            log.error("Cannot reach STD at %s: %s", self._http.base_url, exc)
            return Outcome.RETRY
        if response.status_code >= 500:
            log.error("STD meta returned HTTP %d: %s", response.status_code, describe_error(response))
            return Outcome.RETRY
        meta = None
        if response.status_code == 200:
            try:
                meta = response.json()
            except ValueError:
                meta = None
        if not isinstance(meta, dict):
            # STD before 0.10.0 has no /api/v1/meta at all (404).
            log.error("STD too old for streams (needs API revision ≥ 2 / STD 0.10.0): "
                      "GET /api/v1/meta returned HTTP %d", response.status_code)
            return Outcome.REJECTED
        log.info("Connected to STD %s, API revision %s", meta.get("std_version", "?"),
                 meta.get("api_revision", "?"))
        if STREAMS_FEATURE not in (meta.get("features") or []):
            log.error("STD too old for streams (needs API revision ≥ 2 / STD 0.10.0); "
                      "features: %s", ", ".join(map(str, meta.get("features") or [])) or "none")
            return Outcome.REJECTED
        return Outcome.OK

    def log_stream_status(self, stream_id: str) -> None:
        """`GET /api/v1/streams/<id>/status`, logged once at startup. Never fails the run."""
        try:
            response = self._request("GET", f"/api/v1/streams/{stream_id}/status")
        except httpx.HTTPError as exc:
            log.warning("Could not read stream status: %s", exc)
            return
        if response.status_code == 404:
            log.info("Stream %r not created yet; the first snapshot will create it as pending",
                     stream_id)
            return
        if response.status_code in (401, 403):
            log.error("Stream status refused (HTTP %d): check STD_INGEST_KEY / key scope — %s",
                      response.status_code, describe_error(response))
            return
        if response.status_code != 200:
            log.warning("Stream status returned HTTP %d: %s", response.status_code,
                        describe_error(response))
            return
        try:
            status = response.json()
        except ValueError:
            log.warning("Stream status returned a non-JSON body")
            return
        log.info("Stream %r: status=%s, display_name=%r, last_snapshot_at=%s, last_item_count=%s",
                 stream_id, status.get("stream_status"), status.get("display_name"),
                 status.get("last_snapshot_at"), status.get("last_item_count"))

    # --- snapshot -----------------------------------------------------------------

    def put_snapshot(self, stream_id: str, body: bytes) -> Outcome:
        """`PUT /api/v1/streams/<id>/snapshot` with an already-encoded JSON body."""
        try:
            response = self._request("PUT", f"/api/v1/streams/{stream_id}/snapshot", content=body,
                                     headers={"Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            log.error("Snapshot not sent, STD unreachable: %s", exc)
            return Outcome.RETRY

        code = response.status_code
        if code >= 500:
            log.error("STD returned HTTP %d for the snapshot: %s", code, describe_error(response))
            return Outcome.RETRY
        if code in (200, 202):
            return self._accepted(stream_id, response)
        if code == 400:
            log.error("STD rejected the snapshot as invalid (HTTP 400; a technitium-sync bug, "
                      "please report it): %s", describe_error(response))
        elif code in (401, 403):
            log.error("STD refused the snapshot (HTTP %d): check STD_INGEST_KEY / key scope "
                      "(a stream belongs to the ingest key that created it) — %s",
                      code, describe_error(response))
        elif code == 409:
            log.error("STD refused the snapshot (HTTP 409, key_type conflict with the existing "
                      "stream): %s", describe_error(response))
        elif code == 413:
            log.error("STD refused the snapshot (HTTP 413, body too large): %s",
                      describe_error(response))
        else:
            log.error("Unexpected HTTP %d for the snapshot: %s", code, describe_error(response))
        return Outcome.REJECTED

    def _accepted(self, stream_id: str, response: httpx.Response) -> Outcome:
        try:
            body = response.json()
        except ValueError:
            body = {}
        status = body.get("stream_status")
        changed = status != self._last_stream_status
        self._last_stream_status = status

        if response.status_code == 200 and body.get("applied"):
            counts = body.get("counts") or {}
            log.info("Snapshot applied: %s item(s); %s", body.get("item_count"),
                     ", ".join(f"{k}={v}" for k, v in counts.items()) or "no counts")
        elif status == "ignored":
            if changed:
                log.warning("Stream %r is set to ignored in STD: snapshots are discarded "
                            "(STD Settings → Streams)", stream_id)
        elif status == "pending":
            if changed:
                log.warning("Stream %r is pending: stream is waiting for approval in STD "
                            "Settings → Streams (%s item(s) stored, not applied)",
                            stream_id, body.get("item_count"))
            else:
                log.debug("Stream %r still pending; snapshot stored", stream_id)
        elif status == "disabled":
            # Someone turned it off on purpose; don't tell them to "approve" it.
            if changed:
                log.warning("Stream %r is disabled in STD: snapshots are stored but not applied "
                            "until it is enabled again (STD Settings → Streams; %s item(s) stored)",
                            stream_id, body.get("item_count"))
            else:
                log.debug("Stream %r still disabled; snapshot stored", stream_id)
        else:
            log.warning("STD accepted the snapshot (HTTP %d) with unexpected status %r",
                        response.status_code, status)
        return Outcome.OK
