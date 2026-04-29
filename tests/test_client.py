"""Tests for client module."""

from contextlib import nullcontext
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import mprun.client
from mprun import SERVER_ADDRESS_ENV
from mprun.client import client
from mprun.server import DATA_PATH_ENV, server
from tests.helpers.job_helper import TEST_JOB_FILE

runner = CliRunner()


def test_create_job() -> None:
    """Test client Job creation."""
    with (
        TemporaryDirectory(delete=True) as data_dir,
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setenv(DATA_PATH_ENV, data_dir)
        mp.setenv(SERVER_ADDRESS_ENV, "8086")

        with TestClient(server) as http_client:
            mp.setattr(
                mprun.client,
                "_client_factory",
                lambda _url: nullcontext(http_client),
            )

            result = runner.invoke(client, ["create", str(TEST_JOB_FILE)])
            assert result.exit_code == 0
