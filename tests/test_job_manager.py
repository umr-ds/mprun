"""Tests for job_manager module."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from mprun.job_manager import JobManager
from mprun.models import Job, JobState, Run
from tests.helpers.job_helper import copy_job_to_test_environment


@pytest.mark.asyncio
async def test_create() -> None:
    """Test Job creation."""
    with TemporaryDirectory(delete=True) as test_dir:
        directory = Path(test_dir)
        manager = JobManager(data_path=directory)

        job_description, job_description_path = copy_job_to_test_environment(
            directory=directory
        )
        archive_path = job_description.create_archive(job_toml=job_description_path)

        all_jobs = await manager.get_all()
        assert not all_jobs

        job: Job
        with archive_path.open("rb") as f:
            job = await manager.create_job(definition=job_description, archive=f)

        retrieved = await manager.get(jid=job.jid)
        assert job == retrieved

        all_jobs = await manager.get_all()
        assert len(all_jobs) == 1
        assert all_jobs[0] == job


@pytest.mark.asyncio
async def test_dispatch() -> None:
    """Test Job dispatching."""
    with TemporaryDirectory(delete=True) as test_dir:
        directory = Path(test_dir)
        manager = JobManager(data_path=directory)

        dispatched = await manager.dispatch_waiting_run(0)
        assert dispatched is None

        job_description, job_description_path = copy_job_to_test_environment(
            directory=directory
        )
        archive_path = job_description.create_archive(job_toml=job_description_path)

        job: Job
        with archive_path.open("rb") as f:
            job = await manager.create_job(definition=job_description, archive=f)

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
