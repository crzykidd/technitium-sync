import json
from pathlib import Path

import pytest

from technitium_sync.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
TECHNITIUM = "http://technitium.test:5380"
STD = "http://std.test:8815"

ENV_VARS = (
    "TECHNITIUM_URL", "TECHNITIUM_TOKEN", "TECHNITIUM_SCOPES", "TECHNITIUM_VERIFY_TLS",
    "STD_URL", "STD_INGEST_KEY", "STD_VERIFY_TLS", "STREAM_ID", "STREAM_DISPLAY_NAME",
    "INTERVAL_SECONDS", "LOG_LEVEL", "HEARTBEAT_FILE",
)


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """The developer's shell must not leak into config tests."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        technitium_url=TECHNITIUM,
        technitium_token="tech-token",
        std_url=STD,
        std_ingest_key="std_abc_secret",
        heartbeat_file=str(tmp_path / "heartbeat"),
    )
