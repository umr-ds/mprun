"""Tests for job_manager module."""

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from mprun.job_manager import JobManager


@given(
    name=st.text(),
    params=st.dictionaries(
        keys=st.text(min_size=1, max_size=20),
        values=st.lists(st.integers(), min_size=1, max_size=5),
        min_size=1,
        max_size=5,
    ),
)
def test__create_randomised(name: str, params: dict[str, list[Any]]) -> None:
    """Test Job creation with randomised data."""
    manager = JobManager()
    job = manager.create_job(name=name, params=params)

    retrieved = manager.get_job(jid=job.jid)
    assert job == retrieved
