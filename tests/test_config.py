import pytest

from technitium_sync.config import ConfigError, load_settings

REQUIRED = {
    "TECHNITIUM_URL": "http://technitium:5380/",
    "TECHNITIUM_TOKEN": "t",
    "STD_URL": "http://std:8815",
    "STD_INGEST_KEY": "std_x_y",
}


@pytest.fixture
def env(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)
    return monkeypatch


def test_defaults(env):
    s = load_settings()
    assert s.technitium_url == "http://technitium:5380"  # trailing slash stripped
    assert s.technitium_scopes == []
    assert s.stream_id == "technitium-dhcp"
    assert s.stream_display_name == "DHCP"
    assert s.interval_seconds == 300
    assert s.log_level == "INFO"
    assert s.technitium_verify_tls is True and s.std_verify_tls is True
    assert s.heartbeat_file == "/tmp/technitium-sync.heartbeat"


def test_missing_values_are_named(monkeypatch):
    with pytest.raises(ConfigError) as exc:
        load_settings()
    text = str(exc.value)
    for name in REQUIRED:
        assert f"{name}: is required" in text


def test_secrets_are_not_shown(env):
    s = load_settings()
    assert "std_x_y" not in repr(s) and "std_x_y" not in str(s.std_ingest_key)


@pytest.mark.parametrize("value", ["Bad", "-leading", "has space", "a" * 64, ""])
def test_bad_stream_id(env, value):
    env.setenv("STREAM_ID", value)
    with pytest.raises(ConfigError, match="STREAM_ID"):
        load_settings()


@pytest.mark.parametrize("value", ["a", "dhcp_2", "technitium-dhcp", "a" * 63])
def test_good_stream_id(env, value):
    env.setenv("STREAM_ID", value)
    assert load_settings().stream_id == value


def test_interval_below_minimum(env):
    env.setenv("INTERVAL_SECONDS", "59")
    with pytest.raises(ConfigError, match="INTERVAL_SECONDS"):
        load_settings()
    env.setenv("INTERVAL_SECONDS", "60")
    assert load_settings().interval_seconds == 60


@pytest.mark.parametrize("raw, expected", [
    ("", []),
    ("LAN", ["LAN"]),
    ("LAN, IoT ,Guest Wi-Fi", ["LAN", "IoT", "Guest Wi-Fi"]),
    ("LAN,,IoT,", ["LAN", "IoT"]),
    ("LAN,LAN", ["LAN"]),
])
def test_scope_list_parsing(env, raw, expected):
    env.setenv("TECHNITIUM_SCOPES", raw)
    assert load_settings().technitium_scopes == expected


def test_bad_url(env):
    env.setenv("STD_URL", "std:8815")
    with pytest.raises(ConfigError, match="STD_URL"):
        load_settings()


def test_display_name_length(env):
    env.setenv("STREAM_DISPLAY_NAME", "x" * 33)
    with pytest.raises(ConfigError, match="STREAM_DISPLAY_NAME"):
        load_settings()


def test_log_level(env):
    env.setenv("LOG_LEVEL", "debug")
    assert load_settings().log_level == "DEBUG"
    env.setenv("LOG_LEVEL", "chatty")
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_settings()


def test_verify_tls_false(env):
    env.setenv("STD_VERIFY_TLS", "false")
    assert load_settings().std_verify_tls is False


def test_blank_token(env):
    env.setenv("TECHNITIUM_TOKEN", "  ")
    with pytest.raises(ConfigError, match="TECHNITIUM_TOKEN"):
        load_settings()
