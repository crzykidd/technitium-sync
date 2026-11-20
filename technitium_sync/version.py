"""The version, read from the bare-semver `VERSION` file at the repo root.

`VERSION` is the single source of truth (release-prep-and-cut). The Docker image
copies it to `/app/VERSION` next to the package, so the same lookup works in the
image and in a checkout.
"""

from pathlib import Path

PRODUCER = "technitium-sync"

_VERSION_FILE = Path(__file__).resolve().parent.parent / "VERSION"


def _read_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def build_info() -> dict[str, str]:
    """Commit and channel written by the Dockerfile to `version.txt`; empty outside the image."""
    info = {}
    try:
        for line in (_VERSION_FILE.parent / "version.txt").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key and value:
                info[key.strip()] = value.strip()
    except OSError:
        pass
    return info


__version__ = _read_version()
USER_AGENT = f"{PRODUCER}/{__version__}"
