"""Helpers that might be useful across multiple different tests."""

from mprun.models import JobDefinition

TEST_JOB = JobDefinition(
    name="test job",
    params={
        "foo": [1, 2, 3],
        "bar": ["one", "two", "three"],
        "buzz": [True, False],
    },
)
