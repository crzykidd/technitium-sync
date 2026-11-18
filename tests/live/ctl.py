"""Scenario helper, run inside the compose network via `./run.sh ctl <command> [args]`.

Technitium (as admin):  t-list | t-add NAME MAC IP | t-setip MAC IP | t-del SCOPE MAC | t-token-bad
STD (write key):        s-status | s-preview | s-enable | s-disable | s-entries | s-post-ingest2
"""

import json
import os
import sys

import httpx

TB = "http://technitium:5380"
SB = "http://std:8815"
E = os.environ


def tcall(token, path, **p):
    r = httpx.get(f"{TB}/api/{path}", params=p, headers={"Authorization": f"Bearer {token}"}, timeout=30).json()
    if r.get("status") != "ok":
        raise SystemExit(f"{path} {p}: {r}")
    return r["response"]


def admin():
    r = httpx.get(f"{TB}/api/user/login", params={"user": "admin", "pass": E["TECH_ADMIN_PASSWORD"]}).json()
    return r["token"]


def std(method, path, key="STD_WRITE_KEY", **kw):
    r = httpx.request(method, SB + path, headers={"Authorization": f"Bearer {E[key]}"}, timeout=30, **kw)
    try:
        body = r.json()
    except ValueError:
        body = r.text
    return r.status_code, body


def pj(x):
    print(json.dumps(x, indent=1, sort_keys=True))


def main(a):
    cmd = a[0]
    if cmd == "t-list":
        t = admin()
        for s in tcall(t, "dhcp/scopes/list")["scopes"]:
            d = tcall(t, "dhcp/scopes/get", name=s["name"])
            print(s["name"], "enabled" if s["enabled"] else "disabled")
            for lease in d.get("reservedLeases", []):
                print("  ", lease["hostName"], lease["hardwareAddress"], lease["address"], (lease.get("comments") or "")[:20])
    elif cmd == "t-add":
        tcall(admin(), "dhcp/scopes/addReservedLease", name="LAN", hostName=a[1], hardwareAddress=a[2], ipAddress=a[3])
    elif cmd == "t-setip":  # change a LAN reservation's IP: remove + add keeping host name
        t = admin()
        mac = a[1].lower().replace(":", "-")
        for lease in tcall(t, "dhcp/scopes/get", name="LAN")["reservedLeases"]:
            if lease["hardwareAddress"].lower().replace(":", "-") == mac:
                tcall(t, "dhcp/scopes/removeReservedLease", name="LAN", hardwareAddress=lease["hardwareAddress"])
                tcall(t, "dhcp/scopes/addReservedLease", name="LAN", hardwareAddress=lease["hardwareAddress"],
                      ipAddress=a[2], hostName=lease["hostName"], comments=lease.get("comments") or "")
    elif cmd == "t-del":
        tcall(admin(), "dhcp/scopes/removeReservedLease", name=a[1], hardwareAddress=a[2])
    elif cmd == "s-status":
        pj(std("GET", "/api/v1/streams/technitium-dhcp"))
    elif cmd == "s-preview":
        pj(std("GET", "/api/v1/streams/technitium-dhcp/preview"))
    elif cmd == "s-enable":
        pj(std("POST", "/api/v1/streams/technitium-dhcp/enable"))
    elif cmd == "s-disable":
        pj(std("POST", "/api/v1/streams/technitium-dhcp/disable", json={"cleanup": "keep"}))
    elif cmd == "s-entries":
        code, body = std("GET", "/api/v1/entries?limit=1000")
        for e in sorted(body.get("items", []), key=lambda e: e.get("name") or ""):
            inv = e.get("inventory") or {}
            c = (inv.get("attributes") or {}).get("comment")
            print(e.get("name"), inv.get("ip"), inv.get("mac"), inv.get("network"),
                  "missing=%s" % inv.get("missing"), "comment_len=%s" % (len(c) if c else None))
        print("total", body.get("total"))
    elif cmd == "s-raw":
        pj(std(a[1], a[2]))
    elif cmd == "s-post-ingest2":
        body = {"display_name": "DHCP", "producer": "x", "producer_version": "0", "key_type": "mac", "items": []}
        pj(std("PUT", "/api/v1/streams/technitium-dhcp/snapshot", key="STD_INGEST_KEY2", json=body))
    else:
        raise SystemExit(__doc__)


main(sys.argv[1:])
