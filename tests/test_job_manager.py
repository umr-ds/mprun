"""Tests for job_manager module."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from mprun.job_manager import JobManager
from mprun.models import JobState, Run
from tests.helpers import EXAMPLE_JOB


@pytest.mark.asyncio
async def test_create() -> None:
    """Test Job creation."""
    with TemporaryDirectory(delete=True) as data_dir:
        data_path = Path(data_dir)
        manager = JobManager(data_path=data_path)

        all_jobs = await manager.get_all()
        assert not all_jobs

        job = await manager.create_job(definition=EXAMPLE_JOB)

        retrieved = await manager.get(jid=job.jid)
        assert job == retrieved

        all_jobs = await manager.get_all()
        assert len(all_jobs) == 1
        assert all_jobs[0] == job


@pytest.mark.asyncio
async def test_dispatch() -> None:
    """Test Job dispatching."""
    with TemporaryDirectory(delete=True) as data_dir:
        data_path = Path(data_dir)
        manager = JobManager(data_path=data_path)

        dispatched = await manager.dispatch_waiting_run(0)
        assert dispatched is None

        job = await manager.create_job(definition=EXAMPLE_JOB)

        retrieved = await manager.get(jid=job.jid)
        assert retrieved.state == JobState.WAITING

        dispatched = await manager.dispatch_waiting_run(wid=0)
        assert isinstance(dispatched, Run)
        assert dispatched.jid == job.jid

        retrieved = await manager.get(jid=job.jid)
        assert retrieved.state == JobState.RUNNING

        found = False
        for run in retrieved.runs:
            if run == dispatched:
                found = True
                assert run.state == JobState.RUNNING
                break
        assert found
