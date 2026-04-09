"""Tests for worker module."""

from os import putenv
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from mprun.models import Worker
from mprun.server import DATA_PATH_ENV, app
from mprun.worker import register


@given(name=st.text())
def test_register(name: str) -> None:
    """Test registration."""
    with TemporaryDirectory(delete=True) as data_dir:
        putenv(DATA_PATH_ENV, data_dir)

        with TestClient(app) as client:
            worker = register(client=client, name=name)
            assert isinstance(worker, Worker)
            assert worker.name == name
