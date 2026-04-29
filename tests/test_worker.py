"""Tests for worker module."""

from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from mprun.models import WorkerData
from mprun.server import DATA_PATH_ENV, server
from mprun.worker import Worker


@given(name=st.text())
def test_register(name: str) -> None:
    """Test worker registration."""
    with (
        TemporaryDirectory(delete=True) as data_dir,
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setenv(DATA_PATH_ENV, data_dir)

        with TestClient(server) as client:
            worker = Worker.register(client=client, name=name)
            assert isinstance(worker, WorkerData)
            assert worker.name == name


@given(name=st.text())
def test_checkin(name: str) -> None:
    """Test worker checkin."""
    with (
        TemporaryDirectory(delete=True) as data_dir,
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setenv(DATA_PATH_ENV, data_dir)

        with TestClient(server) as client:
            metadata = Worker.register(client=client, name=name)
            worker = Worker(client=client, metadata=metadata)
            worker.checkin()
