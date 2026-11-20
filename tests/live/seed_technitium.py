"""Seed the throwaway Technitium with fake DHCP scopes/reservations and a View-only API token.

Runs inside the compose network (`run.sh seed`). Prints `TECHNITIUM_TOKEN=<token>` on the last
line (the token is written to tests/live/.env by run.sh, never logged elsewhere).
All data is fake: locally administered MACs (02:00:00:...) and 10.99.x.x addresses.
"""

import os
import sys

import httpx

BASE = os.environ.get("TECHNITIUM_URL", "http://technitium:5380")
ADMIN_PASS = os.environ["TECH_ADMIN_PASSWORD"]
SYNC_USER, SYNC_GROUP = "technitium-sync", "tsync-dhcp-view"
SYNC_PASS = os.environ.get("TSYNC_USER_PASSWORD", "tsync-test-Passw0rd!")


def call(token, path, **params):
    r = httpx.get(f"{BASE}/api/{path}", params=params, headers={"Authorization": f"Bearer {token}"},
                  timeout=30)
    body = r.json()
    if body.get("status") != "ok":
        raise SystemExit(f"{path} {params} -> {body}")
    return body.get("response", {})


def login():
    r = httpx.get(f"{BASE}/api/user/login", params={"user": "admin", "pass": ADMIN_PASS,
                                                      "includeInfo": "true"}, timeout=30).json()
    assert r["status"] == "ok", r
    return r["token"], r.get("info", {})


# (scope, network, mask, router) ; reservations: (host or "", mac, ip, comment)
SCOPES = {
    "LAN": ("10.99.10.0", "10.99.10.1", "10.99.10.254", "255.255.255.0", [
        ("lt-nas", "02:00:00:00:10:01", "10.99.10.11", "storage box"),
        ("lt-printer", "02:00:00:00:10:02", "10.99.10.12", "hall"),
        ("lt-tv", "02:00:00:00:10:03", "10.99.10.13", ""),
        ("lt-camera", "02:00:00:00:10:04", "10.99.10.14", "x" * 300),
        ("lt-laptop", "02:00:00:00:10:05", "10.99.10.15", "guest"),
        ("", "02:00:00:00:10:06", "10.99.10.16", "no hostname on purpose"),
    ]),
    "IoT": ("10.99.20.0", "10.99.20.1", "10.99.20.254", "255.255.255.0", [
        ("lt-plug1", "02:00:00:00:20:01", "10.99.20.21", "kitchen"),
        ("lt-plug2", "02:00:00:00:20:02", "10.99.20.22", "garage"),
        ("lt-dup", "02:00:00:00:10:03", "10.99.20.23", "same MAC as lt-tv in LAN"),
    ]),
    "Lab": ("10.99.30.0", "10.99.30.1", "10.99.30.254", "255.255.255.0", [
        ("lt-lab1", "02:00:00:00:30:01", "10.99.30.31", "disabled scope"),
        ("lt-lab2", "02:00:00:00:30:02", "10.99.30.32", "disabled scope"),
    ]),
}


def main():
    token, info = login()
    print("Technitium version:", info.get("version"), file=sys.stderr)

    # View-only user: DhcpServer View only, via a dedicated group.
    call(token, "admin/groups/create", group=SYNC_GROUP, description="technitium-sync read-only")
    call_user = httpx.get(f"{BASE}/api/admin/users/create",
                          params={"user": SYNC_USER, "pass": SYNC_PASS, "displayName": "technitium-sync"},
                          headers={"Authorization": f"Bearer {token}"}).json()
    assert call_user["status"] == "ok", call_user
    call(token, "admin/users/set", user=SYNC_USER, memberOfGroups=SYNC_GROUP)
    call(token, "admin/permissions/set", section="DhcpServer",
         groupPermissions=f"Administrators|true|true|true|{SYNC_GROUP}|true|false|false")

    for name, (_net, start, end, mask, leases) in SCOPES.items():
        call(token, "dhcp/scopes/set", name=name, startingAddress=start, endingAddress=end,
             subnetMask=mask, routerAddress=start)
        for host, mac, ip, comment in leases:
            params = {"name": name, "hardwareAddress": mac, "ipAddress": ip}
            if host:
                params["hostName"] = host
            if comment:
                params["comments"] = comment
            call(token, "dhcp/scopes/addReservedLease", **params)

    for name in ("LAN", "IoT"):
        try:
            call(token, "dhcp/scopes/enable", name=name)
        except SystemExit as exc:
            print(f"WARN: could not enable {name}: {exc}", file=sys.stderr)

    call(token, "dhcp/scopes/disable", name="Lab")  # scopes/set leaves a new scope enabled

    tok = call(token, "admin/sessions/createToken", user=SYNC_USER, tokenName="technitium-sync")
    print(f"TECHNITIUM_TOKEN={tok['token']}")


if __name__ == "__main__":
    main()
