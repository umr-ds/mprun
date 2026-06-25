"""Hypothesis strategies shared across property-based tests."""

from hypothesis import strategies as st

from mprun.custom_types import ActiveState, SuccessState, TOMLScalar, WorkerBackend
from mprun.models import ExperimentDefinition

identifier = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]*", fullmatch=True)

safe_str_value = st.text(
    alphabet=st.characters(
        min_codepoint=0x20,
        max_codepoint=0x7E,
        exclude_characters='"\\',
    ),
    max_size=20,
)

toml_scalar = st.one_of(
    safe_str_value,
    st.integers(min_value=-(2**63), max_value=2**63 - 1),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
)


@st.composite
def params(draw: st.DrawFn) -> dict[str, list[TOMLScalar]]:
    """Strategy for non-empty parameter dicts as accepted by ``Experiment.new``."""
    keys = draw(st.lists(identifier, min_size=1, max_size=4, unique=True))
    return {key: draw(st.lists(toml_scalar, min_size=1, max_size=3)) for key in keys}


run_states = st.lists(
    st.tuples(
        st.sampled_from(list(ActiveState)),
        st.sampled_from(list(SuccessState)),
    ),
    min_size=1,
    max_size=8,
)


@st.composite
def experiment_definition(draw: st.DrawFn) -> ExperimentDefinition:
    """Strategy for ``ExperimentDefinition`` instances with TOML-safe field values."""
    return ExperimentDefinition(
        name=draw(identifier),
        params=draw(params()),
        executable=draw(identifier),
        backends=draw(st.sets(elements=st.sampled_from(WorkerBackend), min_size=1)),
        setup_executable=draw(st.one_of(st.none(), identifier)),
        results=draw(st.dictionaries(identifier, safe_str_value, max_size=3)),
        environment_variables=draw(
            st.one_of(
                st.none(),
                st.dictionaries(identifier, safe_str_value, max_size=3),
            ),
        ),
        environment_files=draw(
            st.one_of(
                st.none(),
                st.dictionaries(identifier, safe_str_value, max_size=3),
            ),
        ),
    )
