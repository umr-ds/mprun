"""Tests for the native execution backend."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest

from mprun.errors import RunFailureError
from mprun.models import (
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
)
from mprun.worker.backends import NativeBackend
from mprun.worker.worker import RESULTS_ARCHIVE_NAME
from tests.conftest import copy_experiment_to_test_environment


def _make_backend(
    definition: ExperimentDefinition,
    experiment_archive_path: Path,
    results_archive_path: Path,
    execution_dir: Path,
) -> NativeBackend:
    """Build a NativeBackend for the first run of the given experiment."""
    run = Experiment.new(definition=definition).runs[0][0]
    return NativeBackend(
        run=run,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=execution_dir,
    )


@pytest.mark.asyncio
async def test_execute_run(tmp_path: Path) -> None:
    """Smoke test: native backend executes the bundled real experiment scripts."""
    experiment_definition, experiment_definition_path = (
        copy_experiment_to_test_environment(directory=tmp_path)
    )
    experiment_archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    with TemporaryDirectory() as exec_dir:
        backend = _make_backend(
            definition=experiment_definition,
            experiment_archive_path=experiment_archive_path,
            results_archive_path=results_archive_path,
            execution_dir=Path(exec_dir),
        )
        await backend.prepare_run_environment()
        await backend.execute_run()

    # no exception means success


@pytest.mark.asyncio
async def test_collect_results(tmp_path: Path) -> None:
    """Smoke test: result archive contains every file the bundled scripts produce."""
    experiment_definition, experiment_definition_path = (
        copy_experiment_to_test_environment(directory=tmp_path)
    )
    experiment_archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    with TemporaryDirectory() as exec_dir:
        exec_path = Path(exec_dir)
        backend = _make_backend(
            definition=experiment_definition,
            experiment_archive_path=experiment_archive_path,
            results_archive_path=results_archive_path,
            execution_dir=exec_path,
        )
        await backend.prepare_run_environment()
        await backend.execute_run()

        await backend.collect_results()

    assert results_archive_path.is_file()

    with ZipFile(results_archive_path, "r") as zf:
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
    """execute_run raises RunFailureError when the main executable exits with non-zero status."""
    definition, directory = make_experiment(
        executable_content="#!/usr/bin/env python3\nimport sys; sys.exit(1)\n",
    )
    experiment_archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    with TemporaryDirectory() as exec_dir:
        backend = _make_backend(
            definition=definition,
            experiment_archive_path=experiment_archive_path,
            results_archive_path=results_archive_path,
            execution_dir=Path(exec_dir),
        )
        await backend.prepare_run_environment()
        with pytest.raises(RunFailureError):
            await backend.execute_run()


@pytest.mark.asyncio
async def test_execute_run_fails_when_setup_exits_nonzero(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """execute_run raises RunFailureError when the setup executable exits with non-zero status."""
    definition, directory = make_experiment(
        setup=True,
        setup_content="#!/usr/bin/env python3\nimport sys; sys.exit(2)\n",
    )
    experiment_archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    with TemporaryDirectory() as exec_dir:
        backend = _make_backend(
            definition=definition,
            experiment_archive_path=experiment_archive_path,
            results_archive_path=results_archive_path,
            execution_dir=Path(exec_dir),
        )
        await backend.prepare_run_environment()
        with pytest.raises(RunFailureError):
            await backend.execute_run()


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
    experiment_archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    with TemporaryDirectory() as exec_dir:
        exec_path = Path(exec_dir)
        backend = _make_backend(
            definition=definition,
            experiment_archive_path=experiment_archive_path,
            results_archive_path=results_archive_path,
            execution_dir=exec_path,
        )
        await backend.prepare_run_environment()
        await backend.execute_run()
        assert (exec_path / "stdout").read_text() == "hello"
