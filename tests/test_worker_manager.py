"""Tests for worker_manager module."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mprun.errors import NoSuchWorkerError, WorkerNotDeadError
from mprun.models import WorkerData, WorkerState
from mprun.worker_manager import WORKER_TIMEOUT, WorkerManager


async def dummy_callback(wid: int) -> None:
    """Dummy callback for testing."""


@pytest.mark.asyncio
@given(name=st.text())
async def test_worker_register(name: str) -> None:
    """Test worker registration."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )
        assert not manager._workers

        worker = await manager.register(name=name)
        assert isinstance(worker, WorkerData)
        assert worker.name == name

        assert worker.wid in manager._workers
        assert worker == manager._workers[worker.wid]


@pytest.mark.asyncio
@given(name=st.text())
async def test_worker_checkin(name: str) -> None:
    """Test worker checkin."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )
        worker = await manager.register(name=name)

        checkin_time = worker.last_check_in
        await manager.check_in(worker.wid)

        assert worker.last_check_in > checkin_time


@pytest.mark.asyncio
@given(name=st.text())
async def test_evict_dead_worker_from_memory(name: str) -> None:
    """Dead workers are removed from the in-memory _workers dict after garbage collection."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        now = 1000.0
        stale = now - (WORKER_TIMEOUT + 1)

        with patch("mprun.worker_manager.time") as mock_time:
            mock_time.return_value = now
            worker = await manager.register(name=name)
            worker.last_check_in = stale

            await manager._collect_garbage()

            assert worker.wid not in manager._workers
            assert worker.state == WorkerState.DEAD


@pytest.mark.asyncio
@given(name=st.text())
async def test_evicted_worker_persisted_as_dead(name: str) -> None:
    """Evicted workers remain in the database with state DEAD."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        now = 1000.0
        stale = now - (WORKER_TIMEOUT + 1)

        with patch("mprun.worker_manager.time") as mock_time:
            mock_time.return_value = now
            worker = await manager.register(name=name)
            wid = worker.wid
            worker.last_check_in = stale

            await manager._collect_garbage()

            all_workers = await manager.get_all()
            dead = [w for w in all_workers if w.wid == wid]
            assert len(dead) == 1
            assert dead[0].state == WorkerState.DEAD


@pytest.mark.asyncio
@given(name=st.text())
async def test_evicted_worker_raises_on_get(name: str) -> None:
    """get() raises NoSuchWorkerError for an evicted worker."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        now = 1000.0
        stale = now - (WORKER_TIMEOUT + 1)

        with patch("mprun.worker_manager.time") as mock_time:
            mock_time.return_value = now
            worker = await manager.register(name=name)
            wid = worker.wid
            worker.last_check_in = stale

            await manager._collect_garbage()

            with pytest.raises(NoSuchWorkerError):
                await manager.get(wid)


@pytest.mark.asyncio
@given(name=st.text())
async def test_live_worker_not_evicted(name: str) -> None:
    """Workers with recent check-in are not collected."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        now = 1000.0

        with patch("mprun.worker_manager.time") as mock_time:
            mock_time.return_value = now
            worker = await manager.register(name=name)

            await manager._collect_garbage()

            assert worker.wid in manager._workers


@pytest.mark.asyncio
@given(name=st.text())
async def test_checkin_prevents_eviction(name: str) -> None:
    """A check-in before garbage collection resets the staleness clock."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        now = 1000.0
        stale = now - (WORKER_TIMEOUT + 1)

        with patch("mprun.worker_manager.time") as mock_time:
            mock_time.return_value = now
            worker = await manager.register(name=name)
            worker.last_check_in = stale

            mock_time.return_value = now + 1
            await manager.check_in(worker.wid)

            mock_time.return_value = now + 2
            await manager._collect_garbage()

            assert worker.wid in manager._workers


@pytest.mark.asyncio
@given(name=st.text())
async def test_only_stale_workers_evicted(name: str) -> None:
    """Garbage collection only evicts stale workers, not all."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        now = 1000.0
        stale = now - (WORKER_TIMEOUT + 1)

        with patch("mprun.worker_manager.time") as mock_time:
            mock_time.return_value = now
            live = await manager.register(name=f"{name}-live")
            dead = await manager.register(name=f"{name}-dead")
            dead.last_check_in = stale

            await manager._collect_garbage()

            assert live.wid in manager._workers
            assert dead.wid not in manager._workers


@pytest.mark.asyncio
async def test_revive_returns_worker() -> None:
    """Revive returns the revived worker with IDLE state."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        worker = await manager.register(name="testworker")

        with patch("mprun.worker_manager.time") as mock_time:
            now = 1000.0
            mock_time.return_value = now
            worker.last_check_in = now - (WORKER_TIMEOUT + 1)
            await manager._collect_garbage()

        revived = await manager.revive(wid=worker.wid)
        assert revived.wid == worker.wid
        assert revived.state == WorkerState.IDLE
        assert revived.name == "testworker"
        assert revived.wid in manager._workers


@pytest.mark.asyncio
async def test_revive_unknown_worker() -> None:
    """Revive raises NoSuchWorkerError for an unknown wid."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        with pytest.raises(NoSuchWorkerError):
            await manager.revive(wid=99999)


@pytest.mark.asyncio
async def test_revive_alive_worker() -> None:
    """Revive raises WorkerNotDeadError for a worker that is not dead."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(
            data_path=test_directory, dead_worker_callback=dummy_callback
        )

        worker = await manager.register(name="testworker")

        with pytest.raises(WorkerNotDeadError):
            await manager.revive(wid=worker.wid)
