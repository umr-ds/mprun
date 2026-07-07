"""Server configuration."""

import logging
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import BaseModel, field_validator

from mprun import DEFAULT_CONFIG_DIRS, DEFAULT_DATA_DIRS
from mprun.custom_types import AppPaths

DEFAULT_SERVER_CONFIG_PATHS = AppPaths(
    user=DEFAULT_CONFIG_DIRS.user / "server.toml",
    site=DEFAULT_CONFIG_DIRS.site / "server.toml",
)


class ServerConfig(BaseModel):
    """Server configuration, loaded from a TOML file."""

    host: str
    port: int
    home_directory: Path = DEFAULT_DATA_DIRS.user / "server"
    log_level: int = logging.INFO

    @field_validator("log_level", mode="before")
    @classmethod
    def _validate_log_level(cls, v: Any) -> int:  # noqa: ANN401
        known: dict[str, int] = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        if isinstance(v, str):
            level = known.get(v.upper())
            if level is None:
                msg = f"Unknown log level: {v!r}"
                raise ValueError(msg)
            return level
        if isinstance(v, int) and not isinstance(v, bool):
            if v not in known.values():
                msg = f"Unknown log level: {v}"
                raise ValueError(msg)
            return v
        msg = f"log_level must be str or int, got {type(v)}"
        raise ValueError(msg)


def resolve_server_config_path(explicit: Path | None) -> Path | None:
    """Find the server config file on disk.

    Args:
        explicit: An explicitly provided path. Takes absolute precedence.

    Returns:
        The path to the first existing config file, or ``None`` if no config file
        was found.
    """
    if explicit is not None:
        return explicit if explicit.is_file() else None

    if DEFAULT_SERVER_CONFIG_PATHS.user.is_file():
        return DEFAULT_SERVER_CONFIG_PATHS.user

    if DEFAULT_SERVER_CONFIG_PATHS.site.is_file():
        return DEFAULT_SERVER_CONFIG_PATHS.site

    return None


def load_server_config(path: Path) -> ServerConfig:
    """Load a ``ServerConfig`` from a TOML file.

    Args:
        path: Path to the TOML config file.

    Returns:
        The parsed ``ServerConfig``.

    Raises:
        OSError: If the file cannot be read.
        pydantic.ValidationError: If the file contents are not a valid ``ServerConfig``.
        tomlkit.exceptions.TOMLKitError: If the file is not valid TOML.
    """
    with path.open("rb") as f:
        return ServerConfig.model_validate(tomlkit.load(f).unwrap())
