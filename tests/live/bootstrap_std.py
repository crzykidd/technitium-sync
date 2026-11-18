"""Bootstrap the throwaway STD: log in as the default admin and create API keys.

Creates `ingest` (the sidecar's key), `ingest2` (to provoke the 403 "another key's stream")
and `write` (to preview/enable/disable streams over the management API). Prints
`STD_INGEST_KEY=...`, `STD_INGEST_KEY2=...`, `STD_WRITE_KEY=...` lines.
Mirrors Settings -> API -> Create key in the STD UI (session login + CSRF form post).
"""

import os
import re

import httpx

BASE = os.environ.get("STD_URL", "http://std:8815")
USER, PASSWORD = "admin", os.environ.get("STD_ADMIN_PASSWORD", "changeme123")


def csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html) or \
        re.search(r'name="csrf-token" content="([^"]+)"', html)
    if not m:
        raise SystemExit("no csrf token found")
    return m.group(1)


def main():
    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
        page = c.get("/login")
        r = c.post("/login", data={"csrf_token": csrf(page.text), "username": USER,
                                   "password": PASSWORD})
        if "Invalid username" in r.text:
            raise SystemExit("STD login failed")
        token = csrf(c.get("/settings?section=api").text)
        for var, name, scope in (("STD_INGEST_KEY", "technitium-sync", "ingest"),
                                 ("STD_INGEST_KEY2", "other-sidecar", "ingest"),
                                 ("STD_WRITE_KEY", "livetest-admin", "write")):
            r = c.post("/settings/api-keys", data={"csrf_token": token, "name": name, "scope": scope})
            m = re.search(r'value="(std_[A-Za-z0-9]+_[A-Za-z0-9_-]+)"', r.text)
            if not m:
                raise SystemExit(f"could not create key {name}: HTTP {r.status_code}")
            print(f"{var}={m.group(1)}")


if __name__ == "__main__":
    main()
