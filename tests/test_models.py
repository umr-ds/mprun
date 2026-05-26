"""Tests for models module."""

from collections.abc import Callable
from pathlib import Path

from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
    ValidationMode,
)
from tests.conftest import TEST_EXPERIMENT, TEST_EXPERIMENT_FILE


def test_experiment_creation() -> None:
    """Static regression anchor: 3 * 3 * 2 parameters expand to 18 runs."""
    definition = ExperimentDefinition(
        name="exp",
        params={"foo": [1, 2, 3], "bar": ["a", "b", "c"], "buzz": [True, False]},
        executable="exe",
        results={},
    )
    experiment = Experiment.new(definition)
    assert len(experiment.runs) == 18


def test_bundled_definition_matches_constant() -> None:
    """The bundled experiment_definition.toml stays in sync with TEST_EXPERIMENT.

    Guards against drift between the real artefact under
    ``tests/artefacts/test_experiment/`` and the in-memory constant the
    smoke test compares against.
    """
    loaded = ExperimentDefinition.load_toml(
        TEST_EXPERIMENT_FILE, validation_mode=ValidationMode.DATA_AND_FILES
    )
    assert loaded == TEST_EXPERIMENT


def test_experiment_archive(
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """``create_archive`` writes a zip in the experiment directory that validates."""
    definition, directory = make_experiment(
        setup=True, environment_files={"env.txt": "/destination/env"}
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    assert archive_path == directory / EXPERIMENT_ARCHIVE_NAME
    assert archive_path.is_file()
    definition.validate_archive(archive_path=archive_path)
