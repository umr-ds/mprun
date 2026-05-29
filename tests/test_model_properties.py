"""Property-based tests for ``mprun.models``.

Hypothesis interacts poorly with pytest fixtures (function-scoped fixtures
are created once per test, not per example), so these tests build their
inputs inline rather than relying on the shared ``make_experiment``
factory. Tests that need a scratch directory use ``TemporaryDirectory``
inside the test body so each example gets its own clean filesystem state.

Strategies live in ``tests/strategies.py``.
"""

from math import prod
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given

from mprun.custom_types import ActiveState, SuccessState, TOMLScalar
from mprun.models import (
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
    Run,
    ValidationMode,
    _expand_parameters,
)
from tests import strategies


@given(params=strategies.params())
def test_expand_parameters_cross_product(params: dict[str, list[TOMLScalar]]) -> None:
    """Cross product covers every combination exactly once and uses only declared values."""
    result = _expand_parameters(params)

    assert len(result) == prod(len(values) for values in params.values())
    for combination in result:
        assert set(combination) == set(params)
        for key, value in combination.items():
            assert value in params[key]


@given(params=strategies.params())
def test_experiment_new_run_count(params: dict[str, list[TOMLScalar]]) -> None:
    """``Experiment.new`` produces one Run per parameter combination, indices sequential."""
    definition = ExperimentDefinition(
        name="exp",
        params=params,
        executable="exe",
        results={},
    )
    experiment = Experiment.new(definition)

    assert len(experiment.runs) == prod(len(values) for values in params.values())
    for index, runs in enumerate(experiment.runs):
        for iteration, run in enumerate(runs):
            assert run.index == index
            assert run.iteration == iteration
            assert run.eid == experiment.eid


def _expected_active(
    states: list[tuple[ActiveState, SuccessState]],
) -> ActiveState:
    actives = [active for active, _ in states]
    if all(active == ActiveState.WAITING for active in actives):
        return ActiveState.WAITING
    if all(active == ActiveState.FINISHED for active in actives):
        return ActiveState.FINISHED
    return ActiveState.RUNNING


def _expected_success(
    states: list[tuple[ActiveState, SuccessState]],
) -> SuccessState:
    successes = [success for _, success in states]
    if all(success == SuccessState.SUCCESS for success in successes):
        return SuccessState.SUCCESS
    if any(success == SuccessState.FAILED for success in successes):
        return SuccessState.FAILED
    return SuccessState.PENDING


@given(states=strategies.run_states)
def test_recalculate_state(states: list[tuple[ActiveState, SuccessState]]) -> None:
    """Aggregated Experiment state matches the rules documented on the enum."""
    definition = ExperimentDefinition(
        name="exp",
        params={"x": [1]},
        executable="exe",
        results={},
    )
    experiment = Experiment.new(definition)
    experiment.runs = [
        [
            Run(
                definition=definition,
                eid=experiment.eid,
                index=index,
                iteration=1,
                active_state=active,
                success_state=success,
                params={"x": 1},
            )
            for index, (active, success) in enumerate(states)
        ]
    ]

    experiment.recalculate_state()

    assert experiment.active_state == _expected_active(states)
    assert experiment.success_state == _expected_success(states)


@given(definition=strategies.experiment_definition())
def test_toml_roundtrip(definition: ExperimentDefinition) -> None:
    """``dump_toml`` followed by ``load_toml`` produces an equal definition."""
    with TemporaryDirectory(delete=True) as tmp:
        path = Path(tmp) / EXPERIMENT_DEFINITION_NAME
        definition.dump_toml(path)
        reloaded = ExperimentDefinition.load_toml(
            file_path=path, validation_mode=ValidationMode.DATA_ONLY
        )
        assert reloaded == definition
