"""Helpers that might be useful across multiple different tests."""

from mprun.models import JobDefinition

EXAMPLE_JOB = JobDefinition(
    name="example job",
    params={
        "foo": [1, 2, 3],
        "bar": ["one", "two", "three"],
        "buzz": [True, False],
    },
)
