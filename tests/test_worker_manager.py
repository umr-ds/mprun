"""Tests for worker_manager module."""

from hypothesis import given
from hypothesis import strategies as st

from mprun.models import Worker
from mprun.worker_manager import WorkerManager


@given(name=st.text())
def test_worker_register(name: str) -> None:
    """Test worker registration."""
    manager = WorkerManager()
    assert not manager.workers

    worker = manager.register(name=name)
    assert isinstance(worker, Worker)
    assert worker.name == name

    assert worker.wid in manager.workers
    assert worker == manager.workers[worker.wid]


@given(name=st.text())
def test_worker_checkin(name: str) -> None:
    """Test worker checkin."""
    manager = WorkerManager()
    worker = manager.register(name=name)

    checkin_time = worker.last_checkin
    manager.checkin(worker.wid)

    assert worker.last_checkin > checkin_time
