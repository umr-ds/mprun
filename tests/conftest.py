"""Shared pytest fixtures for the mprun test suite."""

from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from shutil import copy2, copytree
from typing import TypedDict, Unpack
from zipfile import ZIP_LZMA, ZipFile

import pytest

from mprun.custom_types import TOMLScalar
from mprun.models import (
    EXPERIMENT_DEFINITION_NAME,
    ExperimentDefinition,
    ValidationMode,
)

TEST_ROOT = Path(__file__).resolve().parent
TEST_EXPERIMENT_DIRECTORY = TEST_ROOT / "artefacts" / "test_experiment"
TEST_EXPERIMENT_FILE = TEST_EXPERIMENT_DIRECTORY / EXPERIMENT_DEFINITION_NAME

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
    """Copy the bundled real artefact into ``directory`` and load the definition.

    Used by the end-to-end smoke tests that exercise the full flow against a
    real on-disk experiment. New tests should prefer ``build_experiment`` or
    the ``make_experiment`` fixture instead.
    """
    copytree(
        TEST_EXPERIMENT_DIRECTORY,
        directory,
        symlinks=False,
        dirs_exist_ok=True,
        copy_function=copy2,
    )
    experiment_definition_path = directory / EXPERIMENT_DEFINITION_NAME
    experiment_definition = ExperimentDefinition.load_toml(
        experiment_definition_path,
        validation_mode=ValidationMode.DATA_AND_FILES,
    )
    return experiment_definition, experiment_definition_path


_STUB_SCRIPT = "#!/usr/bin/env python3\npass\n"


def _write_executable(path: Path, content: str = _STUB_SCRIPT) -> None:
    path.write_text(content)
    path.chmod(0o755)


class ExperimentKwargs(TypedDict, total=False):
    """Optional keyword arguments accepted by :func:`build_experiment`."""

    name: str
    params: dict[str, list[TOMLScalar]] | None
    executable: str
    executable_content: str
    setup: bool
    setup_content: str
    results: dict[str, str] | None
    environment_variables: dict[str, str] | None
    environment_files: dict[str, str] | None
    timeout: int | None


def build_experiment(  # noqa: PLR0913
    directory: Path,
    *,
    name: str = "exp",
    params: dict[str, list[TOMLScalar]] | None = None,
    executable: str = "main.py",
    executable_content: str = _STUB_SCRIPT,
    setup: bool = False,
    setup_content: str = _STUB_SCRIPT,
    results: dict[str, str] | None = None,
    environment_variables: dict[str, str] | None = None,
    environment_files: dict[str, str] | None = None,
    timeout: int | None = None,
) -> tuple[ExperimentDefinition, Path]:
    """Materialise a minimal Experiment inside ``directory``.

    Writes stub executables, a TOML definition, and any opt-in extras
    (``setup`` script, environment files) into ``directory`` and returns
    the parsed ``ExperimentDefinition`` alongside the directory itself.

    ``executable_content`` / ``setup_content`` override the default no-op
    stub when a test needs the script to do real work (e.g. exit non-zero
    or print environment variables).

    Callable from anywhere; safe to use from inside ``@given`` tests where
    fixtures interact poorly with Hypothesis.
    """
    directory.mkdir(parents=True, exist_ok=True)

    _write_executable(directory / executable, content=executable_content)

    setup_name: str | None = None
    if setup:
        setup_name = "setup.py"
        _write_executable(directory / setup_name, content=setup_content)

    if environment_files:
        for env_file in environment_files:
            (directory / env_file).write_text("env\n")

    definition = ExperimentDefinition(
        name=name,
        params=params if params is not None else {"x": [1, 2]},
        executable=executable,
        setup_executable=setup_name,
        results=results if results is not None else {"out.txt": "out.txt"},
        environment_variables=environment_variables,
        environment_files=environment_files,
        timeout=timeout,
    )
    definition.dump_toml(directory / EXPERIMENT_DEFINITION_NAME)

    return definition, directory


@pytest.fixture
def make_experiment(
    tmp_path: Path,
) -> Callable[..., tuple[ExperimentDefinition, Path]]:
    """Return a factory wrapping ``build_experiment`` with per-test ``tmp_path``.

    Each call materialises a fresh sub-directory under ``tmp_path``. Tests
    inside ``@given`` should bypass this fixture and call ``build_experiment``
    directly against a path of their own choosing.
    """
    counter = 0

    def _make(
        **kwargs: Unpack[ExperimentKwargs],
    ) -> tuple[ExperimentDefinition, Path]:
        nonlocal counter
        directory = tmp_path / f"experiment_{counter}"
        counter += 1
        return build_experiment(directory, **kwargs)

    return _make


@pytest.fixture
def make_archive_bytes() -> Callable[[dict[str, bytes]], BytesIO]:
    """Return a factory that builds an in-memory zip archive.

    Useful for exercising archive-validation logic against deliberately
    malformed archives without touching the filesystem.
    """

    def _make(files: dict[str, bytes]) -> BytesIO:
        buf = BytesIO()
        with ZipFile(buf, mode="w", compression=ZIP_LZMA, allowZip64=True) as zf:
            for archive_name, contents in files.items():
                zf.writestr(archive_name, contents)
        buf.seek(0)
        return buf

    return _make
