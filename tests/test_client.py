"""Tests for client module."""

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import mprun.client
from mprun import SERVER_ADDRESS_ENV
from mprun.client import client
from mprun.server import DATA_PATH_ENV, server
from tests.helpers.experiment_helper import copy_experiment_to_test_environment

runner = CliRunner()


def test_create_experiment() -> None:
    """Test client experiment creation."""
    with (
        TemporaryDirectory(delete=True) as test_dir,
        pytest.MonkeyPatch.context() as mp,
    ):
        directory = Path(test_dir)
        mp.setenv(DATA_PATH_ENV, test_dir)
        mp.setenv(SERVER_ADDRESS_ENV, "8086")

        _, test_experiment_definition = copy_experiment_to_test_environment(
            directory=directory
        )

        with TestClient(server) as http_client:
            mp.setattr(
                mprun.client,
                "_client_factory",
                lambda _url: nullcontext(http_client),
            )

            result = runner.invoke(client, ["create", str(test_experiment_definition)])
            assert result.exit_code == 0
