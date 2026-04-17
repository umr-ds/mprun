"""Tests for job_manager module."""

from pathlib import Path
from tempfile import TemporaryDirectory

from mprun.job_manager import JobManager
from tests.helpers import EXAMPLE_JOB


def test_create() -> None:
    """Test Job creation with randomised data."""
    with TemporaryDirectory(delete=True) as data_dir:
        data_path = Path(data_dir)
        manager = JobManager(data_path=data_path)

        job = manager.create_job(definition=EXAMPLE_JOB)

        retrieved = manager.get(jid=job.jid)
        assert job == retrieved
