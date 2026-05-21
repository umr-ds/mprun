"""Helpers that might be useful across multiple different tests."""

from pathlib import Path
from shutil import copytree

from mprun.models import JobDefinition, ValidationMode

TEST_ROOT = Path(__file__).resolve().parent.parent
TEST_JOB_DIRECTORY = TEST_ROOT / "artefacts" / "test_job"
TEST_JOB_FILE = TEST_JOB_DIRECTORY / "job_definition.toml"

TEST_JOB = JobDefinition(
    name="test job",
    params={
        "foo": [1, 2, 3],
        "bar": ["one", "two", "three"],
        "buzz": [True, False],
    },
    executable="main_script.py",
    setup_executable="setup_script.py",
    results={"/tmp/envfile": "envfile"},
    environment_variables={"foo": "bar"},
    environment_files={"envfile.txt": "/tmp/envfile"},
)


def copy_job_to_test_environment(directory: Path) -> tuple[JobDefinition, Path]:
    """Copy the contents of ``artefacts/test_job` to the test environment, and load the JobDefinition."""
    copytree(
        TEST_JOB_DIRECTORY, directory, symlinks=False, dirs_exist_ok=True
    )  # copy Job files to clean test directory
    job_definition_path = directory / "job_definition.toml"
    job_definition = JobDefinition.load_toml(
        job_definition_path,
        validation_mode=ValidationMode.DATA_AND_FILES,
    )
    return job_definition, job_definition_path
