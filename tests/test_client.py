"""Tests for client module."""

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import mprun.client
from mprun import SERVER_ADDRESS_ENV
from mprun.client import client
from mprun.models import EXPERIMENT_DEFINITION_NAME, ExperimentDefinition
from mprun.server import DATA_PATH_ENV, server

runner = CliRunner()


def test_create_experiment(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Client ``create`` command posts the definition + archive and exits 0."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
        mp.setenv(SERVER_ADDRESS_ENV, "8086")

        _, directory = make_experiment()
        toml_path = directory / EXPERIMENT_DEFINITION_NAME

        with TestClient(server) as http_client:
            mp.setattr(
                mprun.client,
                "_client_factory",
                lambda _url: nullcontext(http_client),
            )

            result = runner.invoke(client, ["create", str(toml_path)])
            assert result.exit_code == 0
