"""Configuration models and resolution helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator
from tomlkit import load

from mprun import DEFAULT_CONFIG_DIRS, DEFAULT_DATA_DIRS, AppPaths


class RegistrationData(BaseModel):
    """Worker registration data.

    Dumped to JSON file upon successful registration and read upon worker restart.
    """

    name: str
    wid: int


class WorkerConfig(BaseModel):
    """Worker configuration, loaded from a TOML file."""

    server_address: str
    name: str
    home_directory: Path = DEFAULT_DATA_DIRS.user / "worker"
    log_level: int = logging.INFO

    @field_validator("server_address", mode="before")
    @classmethod
    def _validate_server_address(cls, v: str) -> str:
        if isinstance(v, str) and "://" not in v:
            return f"http://{v}"
        return v

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


DEFAULT_WORKER_CONFIG_PATHS = AppPaths(
    user=DEFAULT_CONFIG_DIRS.user / "worker.toml",
    site=DEFAULT_CONFIG_DIRS.site / "worker.toml",
)


def resolve_worker_config_path(explicit: Path | None = None) -> Path | None:
    """Find the worker config file on disk.

    Args:
        explicit: An explicitly provided path. Takes absolute precedence.

    Returns:
        The path to the first existing config file, or ``None`` if no config file
        was found.
    """
    if explicit is not None:
        return explicit if explicit.is_file() else None

    if DEFAULT_WORKER_CONFIG_PATHS.user.is_file():
        return DEFAULT_WORKER_CONFIG_PATHS.user

    if DEFAULT_WORKER_CONFIG_PATHS.site.is_file():
        return DEFAULT_WORKER_CONFIG_PATHS.site

    return None


def load_worker_config(path: Path) -> WorkerConfig:
    """Load a ``WorkerConfig`` from a TOML file.

    Args:
        path: Path to the TOML config file.

    Returns:
        The parsed ``WorkerConfig``.

    Raises:
        OSError: If the file cannot be read.
        pydantic.ValidationError: If the file contents are not a valid ``WorkerConfig``.
        tomlkit.exceptions.TOMLKitError: If the file is not valid TOML.
    """
    with path.open("rb") as f:
        return WorkerConfig.model_validate(load(f).unwrap())
