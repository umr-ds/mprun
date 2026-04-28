"""Helpers that might be useful across multiple different tests."""

from mprun.models import JobDefinition

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
