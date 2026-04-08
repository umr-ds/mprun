"""Tests for job_manager module."""

from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given

from mprun.job_manager import JobManager
from mprun.models import JobDefinition
from tests.helpers import draw_job_definition


@given(job_definition=draw_job_definition())
def test__create_randomised(job_definition: JobDefinition) -> None:
    """Test Job creation with randomised data."""
    with TemporaryDirectory(delete=True) as data_dir:
        data_path = Path(data_dir)
        manager = JobManager(data_path=data_path)

        job = manager.create_job(definition=job_definition)

        retrieved = manager.get(jid=job.jid)
        assert job == retrieved
