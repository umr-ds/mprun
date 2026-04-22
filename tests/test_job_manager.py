"""Tests for job_manager module."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from mprun.job_manager import JobManager
from tests.helpers import EXAMPLE_JOB


@pytest.mark.asyncio
async def test_create() -> None:
    """Test Job creation with randomised data."""
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
