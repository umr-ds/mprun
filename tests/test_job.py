"""Tests for job module."""

from math import prod
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from mprun.models import Job, JobDefinition


def test__job_creation() -> None:
    """Test Job creation with a single static example."""
    example_params = {
        "param_1": [0, 1, 2, 3],
        "param_2": ["foo", "bar"],
        "param_3": [True, False],
    }

    definition = JobDefinition(name="testjob", params=example_params)
    job = Job.new(definition)

    assert len(job.runs) == 16


@given(
    name=st.text(),
    params=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.lists(st.integers(), min_size=1, max_size=5),
        min_size=1,
        max_size=5,
    ),
)
def test__job_creation_properties(name: str, params: dict[str, list[Any]]) -> None:
    """Test Job creation with randomised data."""
    total_runs = prod([len(par) for par in params.values()])

    definition = JobDefinition(name=name, params=params)
    job = Job.new(definition)

    assert len(job.runs) == total_runs
