"""Tests for job module."""

from mprun.models import Job
from tests.helpers import EXAMPLE_JOB


def test__job_creation() -> None:
    """Test Job creation with a single static example."""
    job = Job.new(EXAMPLE_JOB)

    assert len(job.runs) == 18
