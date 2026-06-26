"""Worker configuration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import tomlkit
from pydantic import BaseModel, field_validator

from mprun import DEFAULT_CONFIG_DIRS, DEFAULT_DATA_DIRS, AppPaths
from mprun.custom_types import WorkerBackend
from mprun.models import WorkerRegistration

DEFAULT_WORKER_CONFIG_PATHS = AppPaths(
    user=DEFAULT_CONFIG_DIRS.user / "worker.toml",
    site=DEFAULT_CONFIG_DIRS.site / "worker.toml",
)


class DockerBackendConfig(BaseModel):
    """Configuration for the docker execution backend.

    Attributes:
        base_url (str): URL or UNIX socket where the docker daemon is listening.
    """

    base_url: str = "unix:///var/run/docker.sock"


class WorkerConfig(BaseModel):
    """Worker configuration, loaded from a TOML file.

    Attributes:
        server_address (str): Address of the ``mprun``-server.
        name (str): Worker's (human-readable) name. Does not have to be unique.
        home_directory (Path): Path to the Worker's home directory. Used to store metadata, registration data, archives, etc.
        log_level (int): Set's Worker's logging level.

        backends (set[WorkerBackend]): Which backends this worker supports. See ``mprun.custom_types.WorkerBackend``.
        docker_backend (DockerBackendConfig): cConfiguration for the docker execution backend.
    """

    server_address: str
    name: str
    home_directory: Path = DEFAULT_DATA_DIRS.user / "worker"
    log_level: int = logging.INFO

    backends: set[WorkerBackend]
    docker_backend: DockerBackendConfig = DockerBackendConfig()

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

    @field_validator("home_directory", mode="before")
    @classmethod
    def _validate_home_directory(cls, v: Any) -> Path:  # noqa: ANN401
        if isinstance(v, Path):
            return v
        if isinstance(v, str):
            return Path(v)
        msg = f"home_directory must be Path or str, was: {type(v)}"
        raise ValueError(msg)

    @property
    def registration_data(self) -> WorkerRegistration:
        """Extracts the relevant data that needs to be sent to the server for registration."""
        return WorkerRegistration(name=self.name, backends=self.backends)

    @classmethod
    def load_worker_config(cls, path: Path) -> WorkerConfig:
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
            data = tomlkit.load(f).unwrap()

            if "backends" in data:
                data["backends"] = {WorkerBackend(b) for b in data["backends"]}
            return WorkerConfig.model_validate(data, strict=True)

    @staticmethod
    def resolve_worker_config_path(explicit: Path | None) -> Path | None:
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
