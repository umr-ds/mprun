"""Tests for models module."""

from pathlib import Path
from tempfile import TemporaryDirectory

from mprun.models import Experiment, ExperimentDefinition, ValidationMode
from tests.helpers.experiment_helper import (
    TEST_EXPERIMENT,
    TEST_EXPERIMENT_FILE,
    copy_experiment_to_test_environment,
)


def test_experiment_creation() -> None:
    """Test experiment creation with a single static example."""
    experiment = Experiment.new(TEST_EXPERIMENT)

    assert len(experiment.runs) == 18


def test_experiment_definition_load() -> None:
    """Assure that tests.helpers.experiment_helper.TEST_EXPERIMENT and experiment_definition.toml have equivalent information."""
    loaded = ExperimentDefinition.load_toml(
        TEST_EXPERIMENT_FILE, validation_mode=ValidationMode.DATA_AND_FILES
    )
    assert loaded == TEST_EXPERIMENT


def test_experiment_definition_dump() -> None:
    """Verify dump_toml round-trips: dump TEST_EXPERIMENT to file, reload raw TOML, compare models.

    Uses model_validate without context to skip file-existence checks (executable etc. not present in temp dir).
    """
    with TemporaryDirectory(delete=True) as test_dir:
        test_file = Path(test_dir) / "test_experiment.toml"
        TEST_EXPERIMENT.dump_toml(test_file)
        reloaded = ExperimentDefinition.load_toml(
            file_path=test_file, validation_mode=ValidationMode.DATA_ONLY
        )
        assert reloaded == TEST_EXPERIMENT


def test_experiment_archive() -> None:
    """Verify experiment archive creation.

    Will create archive in temporary directory, and check if everything is inside.
    """
    with TemporaryDirectory(delete=True) as test_dir:
        directory = Path(test_dir)
        experiment_definition, experiment_definition_path = (
            copy_experiment_to_test_environment(directory=directory)
        )
        archive_path = experiment_definition.create_archive(
            experiment_toml=experiment_definition_path
        )

        assert archive_path == directory / "experiment_archive.zip"
        assert archive_path.is_file()

        experiment_definition.validate_archive(archive_path=archive_path)
