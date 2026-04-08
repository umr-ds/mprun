"""Helpers that might be useful across multiple different tests."""

from hypothesis import strategies as st

from mprun.models import JobDefinition


@st.composite
def draw_job_definition(draw: st.DrawFn) -> JobDefinition:
    """Hypothesis composite strategy to directly draw a JobDefinition."""
    return JobDefinition(
        name=draw(st.text()),
        params=draw(
            st.dictionaries(
                keys=st.text(min_size=1, max_size=20),
                values=st.lists(st.integers(), min_size=1, max_size=5),
                min_size=1,
                max_size=5,
            )
        ),
    )
