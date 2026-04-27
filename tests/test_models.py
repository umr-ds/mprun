"""Tests for models module."""

from pathlib import Path

from mprun.models import Job, JobDefinition
from tests.helpers.job_helper import TEST_JOB

HERE = Path(__file__).resolve().parent
TEST_JOB_FILE = HERE / "helpers" / "test_job.toml"


def test_job_creation() -> None:
    """Test Job creation with a single static example."""
    job = Job.new(TEST_JOB)

    assert len(job.runs) == 18


def test_job_equivalence() -> None:
    """Assure that tests.helpers.job_helper.TEST_JOB and test_job.toml have equivalent information."""
    loaded = JobDefinition.from_toml(TEST_JOB_FILE)
    assert loaded == TEST_JOB
