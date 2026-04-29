"""Helpers that might be useful across multiple different tests."""

from pathlib import Path

from mprun.models import JobDefinition

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
    results=["/tmp/envfile"],
    environment_variables={"foo": "bar"},
    environment_files={"envfile.txt": "/tmp/envfile"},
)
