"""Tests for experiment_manager module."""

import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest

from mprun.experiment_manager import ExperimentManager, PendingDispatch
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
)
from mprun.types import ActiveState, SuccessState


async def _create_experiment(
    manager: ExperimentManager,
    definition: ExperimentDefinition,
    directory: Path,
) -> Experiment:
    """Build the archive on disk and register the experiment with ``manager``."""
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    with archive_path.open("rb") as f:
        return await manager.create_experiment(definition=definition, archive=f)


@pytest.mark.asyncio
async def test_create(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Experiment creation persists archive and makes the experiment retrievable."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment()

    assert not await manager.get_all()

    experiment = await _create_experiment(manager, definition, directory)

    retrieved = await manager.get_experiment(eid=experiment.eid)
    assert experiment == retrieved

    all_experiments = await manager.get_all()
    assert len(all_experiments) == 1
    assert all_experiments[0] == experiment

    stored_archive = manager._data_path / str(experiment.eid) / EXPERIMENT_ARCHIVE_NAME
    assert stored_archive.is_file()
    experiment.definition.validate_archive(archive_path=stored_archive)


@pytest.mark.asyncio
async def test_dispatch(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Dispatching a waiting run transitions the experiment to RUNNING."""
    manager = ExperimentManager(data_path=tmp_path / "data")

    assert await manager.dispatch_waiting_run() is None

    definition, directory = make_experiment()
    experiment = await _create_experiment(manager, definition, directory)

    retrieved = await manager.get_experiment(eid=experiment.eid)
    assert retrieved.active_state == ActiveState.WAITING

    dispatched = await manager.dispatch_waiting_run()
    assert isinstance(dispatched, PendingDispatch)
    assert dispatched.experiment.eid == experiment.eid

    async with dispatched:
        dispatched.finalise(wid=0)

    retrieved = await manager.get_experiment(eid=experiment.eid)
    assert retrieved.active_state == ActiveState.RUNNING

    retrieved_run = retrieved.runs[dispatched.run.index]
    assert retrieved_run.run_id == dispatched.run.run_id
    assert retrieved_run.active_state == ActiveState.RUNNING


@pytest.mark.asyncio
async def test_results_submit(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Submitting results updates run state and persists the results archive."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment()
    experiment = await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=0)
    run = dispatched.run

    run.active_state = ActiveState.FINISHED
    run.success_state = SuccessState.SUCCESS

    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("result.txt", "ok")
    buf.seek(0)

    await manager.submit_run_results(run=run, results_archive=buf)

    result_path = manager._run_results_path(rid=run.run_id)
    assert result_path.is_file()

    retrieved = await manager.get_experiment(eid=experiment.eid)
    submitted_run = retrieved.runs[run.index]
    assert submitted_run.active_state == ActiveState.FINISHED
    assert submitted_run.success_state == SuccessState.SUCCESS
    assert retrieved.active_state == ActiveState.RUNNING
