"""Tests for experiment_manager module."""

import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest

from mprun.errors import NoSuchExperimentError, NoSuchRunError
from mprun.experiment_manager import ExperimentManager, PendingDispatch
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
    Run,
)
from mprun.types import ActiveState, RunId, SuccessState


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


async def _submit_success(manager: ExperimentManager, run: Run) -> None:
    """Mark ``run`` finished + successful and submit a placeholder results archive."""
    run.active_state = ActiveState.FINISHED
    run.success_state = SuccessState.SUCCESS
    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("result.txt", "ok")
    buf.seek(0)
    await manager.submit_run_results(run=run, results_archive=buf)


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

    await _submit_success(manager, run)

    result_path = manager._run_results_path(rid=run.run_id)
    assert result_path.is_file()

    retrieved = await manager.get_experiment(eid=experiment.eid)
    submitted_run = retrieved.runs[run.index]
    assert submitted_run.active_state == ActiveState.FINISHED
    assert submitted_run.success_state == SuccessState.SUCCESS
    assert retrieved.active_state == ActiveState.RUNNING


@pytest.mark.asyncio
async def test_get_experiment_missing_raises(tmp_path: Path) -> None:
    """get_experiment raises NoSuchExperimentError for an unknown eid."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    with pytest.raises(NoSuchExperimentError):
        await manager.get_experiment(eid=12345)


@pytest.mark.asyncio
async def test_get_run_missing_raises(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """get_run raises NoSuchRunError for out-of-range index and unknown eid."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment()
    experiment = await _create_experiment(manager, definition, directory)

    with pytest.raises(NoSuchRunError):
        await manager.get_run(rid=RunId(eid=experiment.eid, index=999))
    with pytest.raises(NoSuchRunError):
        await manager.get_run(rid=RunId(eid=99999, index=0))


@pytest.mark.asyncio
async def test_dispatch_exhausts(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """After every run has been dispatched, the next dispatch call returns None."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1, 2, 3]})
    experiment = await _create_experiment(manager, definition, directory)

    for _ in range(len(experiment.runs)):
        dispatched = await manager.dispatch_waiting_run()
        assert dispatched is not None
        async with dispatched:
            dispatched.finalise(wid=0)

    assert await manager.dispatch_waiting_run() is None


@pytest.mark.asyncio
async def test_pending_dispatch_blocks_redispatch_until_cancel(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """A held pending dispatch blocks the same run from being dispatched twice.

    Exiting the context manager without ``finalise`` cancels the dispatch and
    frees the run for re-dispatch.
    """
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})  # single run
    await _create_experiment(manager, definition, directory)

    first = await manager.dispatch_waiting_run()
    assert first is not None

    second = await manager.dispatch_waiting_run()
    assert second is None  # blocked by the still-pending first dispatch

    async with first:
        pass  # no finalise → cancel

    third = await manager.dispatch_waiting_run()
    assert third is not None
    assert third.run.run_id == first.run.run_id


@pytest.mark.asyncio
async def test_submitting_all_results_finishes_experiment(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Submitting results for every run transitions the experiment to FINISHED/SUCCESS."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1, 2]})
    experiment = await _create_experiment(manager, definition, directory)

    for _ in range(len(experiment.runs)):
        dispatched = await manager.dispatch_waiting_run()
        assert dispatched is not None
        async with dispatched:
            dispatched.finalise(wid=0)
        await _submit_success(manager, dispatched.run)

    retrieved = await manager.get_experiment(eid=experiment.eid)
    assert retrieved.active_state == ActiveState.FINISHED
    assert retrieved.success_state == SuccessState.SUCCESS


@pytest.mark.asyncio
async def test_get_run_results_missing_raises(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """get_run_results raises FileNotFoundError when no archive has been uploaded."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment()
    experiment = await _create_experiment(manager, definition, directory)

    with pytest.raises(FileNotFoundError):
        await manager.get_run_results(rid=experiment.runs[0].run_id)


@pytest.mark.asyncio
async def test_persistence_across_manager_restart(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """A fresh ExperimentManager loads existing experiments from its data path."""
    data_path = tmp_path / "data"
    manager = ExperimentManager(data_path=data_path)
    definition, directory = make_experiment()
    experiment = await _create_experiment(manager, definition, directory)
    manager.close()

    revived = ExperimentManager(data_path=data_path)
    try:
        retrieved = await revived.get_experiment(eid=experiment.eid)
        assert retrieved == experiment
    finally:
        revived.close()
