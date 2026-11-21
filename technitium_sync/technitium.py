"""Read DHCP static reservations from the Technitium DNS Server HTTP API.

Only two calls are used, both needing the `DhcpServer: View` permission:

- `GET /api/dhcp/scopes/list` for the scope names and their `enabled` flag;
- `GET /api/dhcp/scopes/get?name=<scope>` for each scope's `reservedLeases`.

Dynamic leases (`/api/dhcp/leases/list`) are never read: a reservation is the
operator's statement that a device exists, a lease is just traffic.

Technitium reports errors as HTTP 200 with `"status": "error"` (or
`"invalid-token"`, `"2fa-required"`), so every response's `status` is checked.
"""

import logging
from dataclasses import dataclass

import httpx

from technitium_sync.version import USER_AGENT

log = logging.getLogger(__name__)


class TechnitiumError(Exception):
    """Any failure reading Technitium. The run that hit it is skipped.

    `reason` is a short, secret-free summary for /healthz; `str(exc)` is the log text.
    """

    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        self.reason = reason or "read failed"


@dataclass(frozen=True)
class Reservation:
    scope: str
    host_name: str | None
    hardware_address: str
    address: str | None
    comments: str | None


class TechnitiumClient:
    def __init__(self, base_url: str, token: str, verify_tls: bool = True, timeout: float = 15.0):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
            verify=verify_tls,
            timeout=timeout,
        )

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None) -> dict:
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise TechnitiumError(f"cannot reach Technitium ({path}): {exc}",
                                  "unreachable") from exc
        if response.status_code != 200:
            code = response.status_code
            raise TechnitiumError(f"Technitium {path} returned HTTP {code}",
                                  f"{code} {response.reason_phrase.lower()}".strip())
        try:
            body = response.json()
        except ValueError as exc:
            raise TechnitiumError(f"Technitium {path} returned a non-JSON body",
                                  "non-JSON response") from exc
        status = body.get("status") if isinstance(body, dict) else None
        if status == "ok":
            result = body.get("response")
            if not isinstance(result, dict):
                raise TechnitiumError(f"Technitium {path} returned no 'response' object",
                                      f"unexpected response ({path})")
            return result
        if status == "invalid-token":
            raise TechnitiumError(
                "Technitium rejected TECHNITIUM_TOKEN (invalid-token): the token is wrong, "
                "expired or was revoked",
                "invalid-token",
            )
        message = body.get("errorMessage") if isinstance(body, dict) else None
        raise TechnitiumError(f"Technitium {path} failed: status={status!r} {message or ''}".rstrip(),
                              f"status {status}")

    def list_scopes(self) -> list[dict]:
        scopes = self._get("/api/dhcp/scopes/list").get("scopes")
        if not isinstance(scopes, list):
            raise TechnitiumError("Technitium scopes/list response has no 'scopes' list",
                                  "unexpected response (scopes/list)")
        return [s for s in scopes if isinstance(s, dict) and s.get("name")]

    def get_reservations(self, scope: str) -> list[Reservation]:
        detail = self._get("/api/dhcp/scopes/get", params={"name": scope})
        leases = detail.get("reservedLeases") or []
        if not isinstance(leases, list):
            raise TechnitiumError(f"scope {scope!r}: 'reservedLeases' is not a list",
                                  "unexpected response (scopes/get)")
        return [
            Reservation(
                scope=scope,
                host_name=lease.get("hostName"),
                hardware_address=str(lease.get("hardwareAddress") or ""),
                address=lease.get("address"),
                comments=lease.get("comments"),
            )
            for lease in leases
            if isinstance(lease, dict)
        ]


def select_scopes(scopes: list[dict], configured: list[str]) -> list[str]:
    """The scope names to read, in order.

    With no configured list: every enabled scope. With a list: exactly those, even
    disabled ones, and a name Technitium doesn't know is an error for the run.
    """
    if configured:
        known = {s["name"] for s in scopes}
        missing = [name for name in configured if name not in known]
        if missing:
            raise TechnitiumError(
                f"TECHNITIUM_SCOPES names scope(s) Technitium doesn't have: {', '.join(missing)}",
                f"unknown scope: {', '.join(missing)}",
            )
        return list(configured)
    selected = []
    for scope in scopes:
        if scope.get("enabled", True):
            selected.append(scope["name"])
        else:
            log.debug("Skipping disabled scope %r (name it in TECHNITIUM_SCOPES to read it)", scope["name"])
    return selected


def read_reservations(client: TechnitiumClient, configured: list[str]) -> list[Reservation]:
    """Every static reservation in the selected scopes. Raises on any failure: all or nothing."""
    names = select_scopes(client.list_scopes(), configured)
    reservations: list[Reservation] = []
    for name in names:
        found = client.get_reservations(name)
        log.debug("Scope %r: %d reservation(s)", name, len(found))
        reservations.extend(found)
    log.debug("Read %d reservation(s) from %d scope(s)", len(reservations), len(names))
    return reservations
