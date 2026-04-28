"""Tests for models module."""

from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from tomlkit import load

from mprun.models import Job, JobDefinition
from tests.helpers.job_helper import TEST_JOB

HERE = Path(__file__).resolve().parent
TEST_JOB_DIRECTORY = HERE / "artefacts" / "test_job"
TEST_JOB_FILE = TEST_JOB_DIRECTORY / "job_definition.toml"


def test_job_creation() -> None:
    """Test Job creation with a single static example."""
    job = Job.new(TEST_JOB)

    assert len(job.runs) == 18


def test_job_definition_load() -> None:
    """Assure that tests.helpers.job_helper.TEST_JOB and job_definition.toml have equivalent information."""
    loaded = JobDefinition.from_toml(TEST_JOB_FILE)
    assert loaded == TEST_JOB


def test_job_definition_dump() -> None:
    """Verify dump_toml round-trips: dump TEST_JOB to file, reload raw TOML, compare models.

    Uses model_validate without context to skip file-existence checks (executable etc. not present in temp dir).
    """
    with TemporaryDirectory(delete=True) as test_dir:
        test_file = Path(test_dir) / "test_job.toml"
        TEST_JOB.dump_toml(test_file)
        with test_file.open("rb") as f:
            raw = load(f).unwrap()
        reloaded = JobDefinition.model_validate(raw, strict=True)
        assert reloaded == TEST_JOB


def test_job_archive() -> None:
    """Verify Job archive creation.

    Will create archive int temporary directory, and check if everything is inside.
    """
    with TemporaryDirectory(delete=True) as test_dir:
        directory = Path(test_dir)
        copytree(
            TEST_JOB_DIRECTORY, directory, symlinks=False, dirs_exist_ok=True
        )  # copy Job files to clean test directory
        job_definition = JobDefinition.from_toml(directory / "job_definition.toml")
        job_definition.create_archive(directory / "job_definition.toml")

        archive_path = directory / "job_archive.zip"
        assert archive_path.is_file()

        with ZipFile(archive_path, mode="r") as zf:  # check if everything is there
            contents = zf.namelist()

            assert job_definition.executable in contents
            assert job_definition.setup_executable in contents
            assert job_definition.environment_files is not None
            for env_file in job_definition.environment_files:
                assert env_file in contents
