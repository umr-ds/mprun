"""Helpers that might be useful across multiple different tests."""

from pathlib import Path
from shutil import copy2, copytree

from mprun.models import ExperimentDefinition, ValidationMode

TEST_ROOT = Path(__file__).resolve().parent.parent
TEST_EXPERIMENT_DIRECTORY = TEST_ROOT / "artefacts" / "test_experiment"
TEST_EXPERIMENT_FILE = TEST_EXPERIMENT_DIRECTORY / "experiment_definition.toml"

TEST_EXPERIMENT = ExperimentDefinition(
    name="test experiment",
    params={
        "foo": [1, 2, 3],
        "bar": ["one", "two", "three"],
        "buzz": [True, False],
    },
    executable="main_script.py",
    setup_executable="setup_script.py",
    results={
        "/tmp/envfile": "envfile",
        "/tmp/test_file.txt": "test_file.txt",
        "/tmp/test_dir": "test_dir",
        "working_file.txt": "working_file.txt",
        "working_dir": "working_dir",
    },
    environment_variables={
        "FOO": "bar",
        "TEST_VARIABLE": "test_value",
    },
    environment_files={"envfile.txt": "/tmp/envfile"},
)


def copy_experiment_to_test_environment(
    directory: Path,
) -> tuple[ExperimentDefinition, Path]:
    """Copy the contents of ``artefacts/test_experiment`` to the test environment, and load the ExperimentDefinition."""
    copytree(
        TEST_EXPERIMENT_DIRECTORY,
        directory,
        symlinks=False,
        dirs_exist_ok=True,
        copy_function=copy2,
    )  # copy Experiment files to clean test directory
    experiment_definition_path = directory / "experiment_definition.toml"
    experiment_definition = ExperimentDefinition.load_toml(
        experiment_definition_path,
        validation_mode=ValidationMode.DATA_AND_FILES,
    )
    return experiment_definition, experiment_definition_path
