"""Client configuration."""

from pathlib import Path

import tomlkit
from pydantic import BaseModel, field_validator

from mprun import DEFAULT_CONFIG_DIRS
from mprun.custom_types import AppPaths

DEFAULT_CLIENT_CONFIG_PATHS = AppPaths(
    user=DEFAULT_CONFIG_DIRS.user / "client.toml",
    site=DEFAULT_CONFIG_DIRS.site / "client.toml",
)


class ClientConfig(BaseModel):
    """Client configuration, loaded from a TOML file."""

    server_address: str

    @field_validator("server_address", mode="before")
    @classmethod
    def _validate_server_address(cls, v: str) -> str:
        if isinstance(v, str) and "://" not in v:
            return f"http://{v}"
        return v


def resolve_client_config_path(explicit: Path | None) -> Path | None:
    """Find the client config file on disk.

    Args:
        explicit: An explicitly provided path. Takes absolute precedence.

    Returns:
        The path to the first existing config file, or ``None`` if no config file
        was found.
    """
    if explicit is not None:
        return explicit if explicit.is_file() else None

    if DEFAULT_CLIENT_CONFIG_PATHS.user.is_file():
        return DEFAULT_CLIENT_CONFIG_PATHS.user

    if DEFAULT_CLIENT_CONFIG_PATHS.site.is_file():
        return DEFAULT_CLIENT_CONFIG_PATHS.site

    return None


def load_client_config(path: Path) -> ClientConfig:
    """Load a ``ClientConfig`` from a TOML file.

    Args:
        path: Path to the TOML config file.

    Returns:
        The parsed ``ClientConfig``.

    Raises:
        OSError: If the file cannot be read.
        pydantic.ValidationError: If the file contents are not a valid ``ClientConfig``.
        tomlkit.exceptions.TOMLKitError: If the file is not valid TOML.
    """
    with path.open("rb") as f:
        return ClientConfig.model_validate(tomlkit.load(f).unwrap())
