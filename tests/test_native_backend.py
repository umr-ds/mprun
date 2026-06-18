"""Tests for the native execution backend."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest

from mprun.custom_types import FailureReason
from mprun.models import (
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
)
from mprun.worker import RESULTS_ARCHIVE_NAME
from mprun.worker.backends import NativeBackend
from tests.conftest import copy_experiment_to_test_environment


def _make_backend(
    home_dir: Path,
    definition: ExperimentDefinition,
    archive_path: Path,
    execution_dir: Path,
) -> NativeBackend:
    """Build a NativeBackend for the first run of the given experiment."""
    run = Experiment.new(definition=definition).runs[0][0]
    return NativeBackend(
        run=run,
        archive_path=archive_path,
        home_dir=home_dir,
        execution_dir=execution_dir,
    )


@pytest.mark.asyncio
async def test_execute_run(tmp_path: Path) -> None:
    """Smoke test: native backend executes the bundled real experiment scripts."""
    experiment_definition, experiment_definition_path = (
        copy_experiment_to_test_environment(directory=tmp_path)
    )
    archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )

    home_dir = tmp_path / "worker"
    home_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as exec_dir:
        backend = _make_backend(
            home_dir=home_dir,
            definition=experiment_definition,
            archive_path=archive_path,
            execution_dir=Path(exec_dir),
        )
        failure = await backend.execute_run()

    assert failure is None


@pytest.mark.asyncio
async def test_collect_results(tmp_path: Path) -> None:
    """Smoke test: result archive contains every file the bundled scripts produce."""
    experiment_definition, experiment_definition_path = (
        copy_experiment_to_test_environment(directory=tmp_path)
    )
    archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )

    home_dir = tmp_path / "worker"
    home_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as exec_dir:
        exec_path = Path(exec_dir)
        backend = _make_backend(
            home_dir=home_dir,
            definition=experiment_definition,
            archive_path=archive_path,
            execution_dir=exec_path,
        )
        failure = await backend.execute_run()
        assert failure is None

        await backend.collect_results()

    results_archive = home_dir / RESULTS_ARCHIVE_NAME
    assert results_archive.is_file()

    with ZipFile(results_archive, "r") as zf:
        contents = zf.namelist()
        assert "stdout.setup" in contents
        assert "stderr.setup" in contents
        assert "stdout" in contents
        assert "stderr" in contents
        assert "envfile" in contents
        assert "test_file.txt" in contents
        assert "test_dir/nested_file.txt" in contents
        assert "working_file.txt" in contents
        assert "working_dir/nested_working_file.txt" in contents


@pytest.mark.asyncio
async def test_execute_run_fails_when_main_exits_nonzero(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """execute_run returns FAILED when the main executable exits with non-zero status."""
    definition, directory = make_experiment(
        executable_content="#!/usr/bin/env python3\nimport sys; sys.exit(1)\n",
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    home_dir = tmp_path / "worker"
    home_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as exec_dir:
        backend = _make_backend(
            home_dir=home_dir,
            definition=definition,
            archive_path=archive_path,
            execution_dir=Path(exec_dir),
        )
        assert await backend.execute_run() == FailureReason.RETURN


@pytest.mark.asyncio
async def test_execute_run_fails_when_setup_exits_nonzero(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """execute_run returns FAILED when the setup executable exits with non-zero status."""
    definition, directory = make_experiment(
        setup=True,
        setup_content="#!/usr/bin/env python3\nimport sys; sys.exit(2)\n",
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    home_dir = tmp_path / "worker"
    home_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as exec_dir:
        backend = _make_backend(
            home_dir=home_dir,
            definition=definition,
            archive_path=archive_path,
            execution_dir=Path(exec_dir),
        )
        assert await backend.execute_run() == FailureReason.RETURN


@pytest.mark.asyncio
async def test_execute_run_passes_env_vars_to_subprocess(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """environment_variables from the definition are visible inside the run subprocess."""
    definition, directory = make_experiment(
        executable_content=(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "sys.stdout.write(os.environ.get('MPRUN_TEST', '<unset>'))\n"
        ),
        environment_variables={"MPRUN_TEST": "hello"},
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    home_dir = tmp_path / "worker"
    home_dir.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory() as exec_dir:
        exec_path = Path(exec_dir)
        backend = _make_backend(
            home_dir=home_dir,
            definition=definition,
            archive_path=archive_path,
            execution_dir=exec_path,
        )
        assert await backend.execute_run() is None
        assert (exec_path / "stdout").read_text() == "hello"
