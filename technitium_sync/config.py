"""Configuration from environment variables, validated once at startup."""

import logging
import re
from typing import Annotated

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

STREAM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MIN_INTERVAL = 60
MAX_REPORTED_INTERVAL = 86400  # STD accepts snapshot_interval_seconds 60-86400


class ConfigError(Exception):
    """Raised with a message that names every bad environment variable."""


class Settings(BaseSettings):
    """Every field maps to the upper-case environment variable of the same name."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    technitium_url: str
    technitium_token: SecretStr
    # Comma list. Empty means every *enabled* scope; a scope named here is read
    # even when it's disabled in Technitium.
    technitium_scopes: Annotated[list[str], NoDecode] = Field(default_factory=list)
    technitium_verify_tls: bool = True

    std_url: str
    std_ingest_key: SecretStr
    std_verify_tls: bool = True

    stream_id: str = "technitium-dhcp"
    stream_display_name: str = Field(default="DHCP", min_length=1, max_length=32)

    interval_seconds: int = Field(default=300, ge=MIN_INTERVAL)
    log_level: str = "INFO"
    # Still written after each accepted snapshot, but no longer read by the healthcheck.
    heartbeat_file: str = "/tmp/technitium-sync.heartbeat"

    health_port: int = Field(default=8080, ge=1, le=65535)
    # Seconds /healthz tolerates since the last good push. Unset = 3 x interval_seconds.
    health_max_age: int | None = Field(default=None, validate_default=True)

    @field_validator("technitium_url", "std_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not re.match(r"^https?://[^/\s]+", value):
            raise ValueError("must be an http:// or https:// URL, e.g. http://technitium:5380")
        return value

    @field_validator("technitium_token", "std_ingest_key")
    @classmethod
    def _not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return SecretStr(value.get_secret_value().strip())

    @field_validator("technitium_scopes", mode="before")
    @classmethod
    def _split_scopes(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = value.split(",")
        scopes = []
        for name in value:
            name = str(name).strip()
            if name and name not in scopes:
                scopes.append(name)
        return scopes

    @field_validator("stream_id")
    @classmethod
    def _stream_id(cls, value: str) -> str:
        if not STREAM_ID_RE.match(value):
            raise ValueError(
                "must match ^[a-z0-9][a-z0-9_-]{0,62}$ (lower-case letters, digits, '-' and '_')"
            )
        return value

    @field_validator("health_max_age")
    @classmethod
    def _health_max_age(cls, value: int | None, info: ValidationInfo) -> int | None:
        interval = info.data.get("interval_seconds")
        if interval is None:  # INTERVAL_SECONDS is itself invalid and reported
            return value
        if value is None:
            return 3 * interval
        if value < interval:
            raise ValueError(f"must be at least INTERVAL_SECONDS ({interval})")
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in LOG_LEVELS:
            raise ValueError(f"must be one of {', '.join(LOG_LEVELS)}")
        return value

    @property
    def reported_interval_seconds(self) -> int:
        """`INTERVAL_SECONDS` clamped to the range STD accepts for `snapshot_interval_seconds`."""
        return min(self.interval_seconds, MAX_REPORTED_INTERVAL)

    @property
    def log_level_number(self) -> int:
        return getattr(logging, self.log_level)


def load_settings(**overrides) -> Settings:
    """Build `Settings` from the environment, or raise `ConfigError` naming each bad variable."""
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            name = str(error["loc"][0]).upper() if error.get("loc") else "?"
            message = error.get("msg", "invalid value")
            if error.get("type") == "missing":
                message = "is required but not set"
            lines.append(f"  {name}: {message}")
        raise ConfigError("Invalid configuration:\n" + "\n".join(lines)) from None
