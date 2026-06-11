"""Tests for worker_manager module."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mprun.models import WorkerData
from mprun.worker_manager import WorkerManager


@pytest.mark.asyncio
@given(name=st.text())
async def test_worker_register(name: str) -> None:
    """Test worker registration."""
    with TemporaryDirectory(delete=True) as tmp_dir:
        test_directory = Path(tmp_dir)
        manager = WorkerManager(data_path=test_directory)
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
        manager = WorkerManager(data_path=test_directory)
        worker = await manager.register(name=name)

        checkin_time = worker.last_check_in
        await manager.check_in(worker.wid)

        assert worker.last_check_in > checkin_time
