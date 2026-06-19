"""Tests for experiment_manager module."""

import time
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest

from mprun.custom_types import ActiveState, RunId, SuccessState
from mprun.errors import NoSuchExperimentError, NoSuchRunError
from mprun.experiment_manager import ExperimentManager, PendingDispatch
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
    Run,
)


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

    retrieved_run = retrieved.runs[dispatched.run.index][dispatched.run.iteration]
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
    submitted_run = retrieved.runs[run.index][run.iteration]
    assert submitted_run.active_state == ActiveState.FINISHED
    assert submitted_run.success_state == SuccessState.SUCCESS
    assert retrieved.active_state == ActiveState.RUNNING


@pytest.mark.asyncio
async def test_record_run_failure(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """record_run_failure updates run state without a results archive on disk."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment()
    await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=0)
    run = dispatched.run

    run.active_state = ActiveState.FINISHED
    run.success_state = SuccessState.FAILED
    run.failure_reason = "BAD_ARCHIVE"
    run.finished_running = time.time()
    await manager.record_run_failure(run=run)

    result_path = manager._run_results_path(rid=run.run_id)
    assert not result_path.is_file()

    retrieved = await manager.get_experiment(eid=dispatched.experiment.eid)
    submitted_run = retrieved.runs[run.index][run.iteration]
    assert submitted_run.active_state == ActiveState.FINISHED
    assert submitted_run.success_state == SuccessState.FAILED
    assert submitted_run.failure_reason == "BAD_ARCHIVE"
    assert retrieved.success_state == SuccessState.FAILED


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
        await manager.get_run(rid=RunId(eid=experiment.eid, index=999, iteration=0))
    with pytest.raises(NoSuchRunError):
        await manager.get_run(rid=RunId(eid=99999, index=0, iteration=0))


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
        await manager.get_run_results(rid=experiment.runs[0][0].run_id)


@pytest.mark.asyncio
async def test_delete_removes_experiment(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Delete removes the experiment from DB, cache, and data directory."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment()
    experiment = await _create_experiment(manager, definition, directory)

    data_dir = manager._experiment_path(eid=experiment.eid)
    assert data_dir.is_dir()

    await manager.delete(eid=experiment.eid)

    assert not data_dir.exists()
    assert experiment.eid not in manager._experiments
    assert await manager.get_all() == []

    with pytest.raises(NoSuchExperimentError):
        await manager.get_experiment(eid=experiment.eid)


@pytest.mark.asyncio
async def test_delete_missing_raises(tmp_path: Path) -> None:
    """Delete raises NoSuchExperimentError for an unknown eid."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    with pytest.raises(NoSuchExperimentError):
        await manager.delete(eid=99999)


@pytest.mark.asyncio
async def test_delete_removes_results_archives(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Delete removes results archives along with the experiment directory."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    experiment = await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=0)
    await _submit_success(manager, dispatched.run)

    result_path = manager._run_results_path(rid=dispatched.run.run_id)
    assert result_path.is_file()

    data_dir = manager._experiment_path(eid=experiment.eid)
    await manager.delete(eid=experiment.eid)

    assert not data_dir.exists()
    assert not result_path.exists()


@pytest.mark.asyncio
async def test_delete_cancels_pending_dispatches(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Delete purges pending dispatches for the deleted experiment's runs."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    await _create_experiment(manager, definition, directory)

    pending = await manager.dispatch_waiting_run()
    assert pending is not None
    assert pending.run.run_id in manager._pending_dispatches

    await manager.delete(eid=pending.run.eid)

    assert pending.run.run_id not in manager._pending_dispatches

    # context exit should not raise even though the experiment is gone
    async with pending:
        pass  # no finalise → cancel, which is now a no-op discard


@pytest.mark.asyncio
async def test_delete_finished_experiment(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Finished experiments are evicted from the cache but remain deletable via DB."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    experiment = await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=0)
    await _submit_success(manager, dispatched.run)

    # experiment is finished → evicted from cache
    assert experiment.eid not in manager._experiments

    await manager.delete(eid=experiment.eid)

    assert await manager.get_all() == []


@pytest.mark.asyncio
async def test_delete_does_not_affect_other_experiments(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Deleting one experiment leaves other experiments intact."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition_a, directory_a = make_experiment()
    definition_b, directory_b = make_experiment()
    exp_a = await _create_experiment(manager, definition_a, directory_a)
    exp_b = await _create_experiment(manager, definition_b, directory_b)

    await manager.delete(eid=exp_a.eid)

    remaining = await manager.get_all()
    assert len(remaining) == 1
    assert remaining[0].eid == exp_b.eid
    assert manager._experiment_path(eid=exp_b.eid).is_dir()


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


@pytest.mark.asyncio
async def test_reset_run(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """reset_run deletes results file and reverts run to WAITING/PENDING."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    experiment = await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=0)
    await _submit_success(manager, dispatched.run)

    rid = dispatched.run.run_id
    result_path = manager._run_results_path(rid=rid)
    assert result_path.is_file()

    await manager.reset_run(rid=rid)

    assert not result_path.is_file()

    retrieved = await manager.get_run(rid=rid)
    assert retrieved.active_state == ActiveState.WAITING
    assert retrieved.success_state == SuccessState.PENDING
    assert retrieved.wid is None
    assert retrieved.started_running is None
    assert retrieved.finished_running is None
    assert retrieved.failure_reason is None

    parent = await manager.get_experiment(eid=experiment.eid)
    assert parent.active_state == ActiveState.WAITING
    assert parent.success_state == SuccessState.PENDING


@pytest.mark.asyncio
async def test_reset_run_evicted_experiment(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """reset_run works even when the parent experiment has been evicted from cache."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    experiment = await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=0)
    await _submit_success(manager, dispatched.run)

    assert experiment.eid not in manager._experiments

    rid = dispatched.run.run_id
    await manager.reset_run(rid=rid)

    assert experiment.eid in manager._experiments
    assert rid in manager._runs
    retrieved = await manager.get_run(rid=rid)
    assert retrieved.active_state == ActiveState.WAITING


@pytest.mark.asyncio
async def test_reset_run_unknown_run(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """reset_run raises NoSuchRunError for an invalid run identity."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    experiment = await _create_experiment(manager, definition, directory)

    with pytest.raises(NoSuchRunError):
        await manager.reset_run(rid=RunId(eid=experiment.eid, index=999, iteration=999))


@pytest.mark.asyncio
async def test_reset_run_unknown_experiment(
    tmp_path: Path,
) -> None:
    """reset_run raises NoSuchExperimentError for an unknown eid."""
    manager = ExperimentManager(data_path=tmp_path / "data")

    with pytest.raises(NoSuchExperimentError):
        await manager.reset_run(rid=RunId(eid=99999, index=0, iteration=0))


@pytest.mark.asyncio
async def test_reset_run_clears_pending_dispatch(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """reset_run removes the run from _pending_dispatches if present."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    await _create_experiment(manager, definition, directory)

    pending = await manager.dispatch_waiting_run()
    assert pending is not None
    assert pending.run.run_id in manager._pending_dispatches

    await manager.reset_run(rid=pending.run.run_id)

    assert pending.run.run_id not in manager._pending_dispatches


@pytest.mark.asyncio
async def test_dead_worker_callback_resets_running_run(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """dead_worker_callback resets a RUNNING run back to WAITING."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    experiment = await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=7)

    assert experiment.active_state == ActiveState.RUNNING

    await manager.dead_worker_callback(wid=7)

    run = manager._runs[experiment.runs[0][0].run_id]
    assert run.active_state == ActiveState.WAITING
    assert run.wid is None
    assert experiment.active_state == ActiveState.WAITING


@pytest.mark.asyncio
async def test_dead_worker_callback_noop_wrong_wid(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """dead_worker_callback does nothing if no RUNNING run matches the wid."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    experiment = await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=7)

    await manager.dead_worker_callback(wid=99)

    run = manager._runs[experiment.runs[0][0].run_id]
    assert run.active_state == ActiveState.RUNNING
    assert run.wid == 7
    assert experiment.active_state == ActiveState.RUNNING


@pytest.mark.asyncio
async def test_dead_worker_callback_noop_no_running_runs(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """dead_worker_callback is a no-op when no runs are RUNNING."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1]})
    experiment = await _create_experiment(manager, definition, directory)

    await manager.dead_worker_callback(wid=7)

    run = manager._runs[experiment.runs[0][0].run_id]
    assert run.active_state == ActiveState.WAITING
    assert experiment.active_state == ActiveState.WAITING


@pytest.mark.asyncio
async def test_dead_worker_callback_resets_multiple_runs(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """dead_worker_callback resets all RUNNING runs belonging to the dead worker."""
    manager = ExperimentManager(data_path=tmp_path / "data")
    definition, directory = make_experiment(params={"x": [1, 2, 3]})
    experiment = await _create_experiment(manager, definition, directory)

    for _ in range(3):
        dispatched = await manager.dispatch_waiting_run()
        assert dispatched is not None
        async with dispatched:
            dispatched.finalise(wid=7)

    assert experiment.active_state == ActiveState.RUNNING
    running_count = sum(
        1 for r in manager._runs.values() if r.active_state == ActiveState.RUNNING
    )
    assert running_count == 3

    await manager.dead_worker_callback(wid=7)

    for run in manager._runs.values():
        assert run.active_state == ActiveState.WAITING
        assert run.wid is None
    assert experiment.active_state == ActiveState.WAITING


@pytest.mark.asyncio
async def test_dead_worker_callback_only_resets_dead_worker(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """dead_worker_callback only resets runs for the given wid, not other workers."""
    manager = ExperimentManager(data_path=tmp_path / "data")

    def1, dir1 = make_experiment(params={"x": [1]})
    def2, dir2 = make_experiment(params={"x": [2]})
    exp1 = await _create_experiment(manager, def1, dir1)
    exp2 = await _create_experiment(manager, def2, dir2)

    d1 = await manager.dispatch_waiting_run()
    assert d1 is not None
    async with d1:
        d1.finalise(wid=1)

    d2 = await manager.dispatch_waiting_run()
    assert d2 is not None
    async with d2:
        d2.finalise(wid=2)

    await manager.dead_worker_callback(wid=1)

    run1 = manager._runs[exp1.runs[0][0].run_id]
    run2 = manager._runs[exp2.runs[0][0].run_id]
    assert run1.active_state == ActiveState.WAITING
    assert run1.wid is None
    assert run2.active_state == ActiveState.RUNNING
    assert run2.wid == 2
    assert exp1.active_state == ActiveState.WAITING
    assert exp2.active_state == ActiveState.RUNNING


@pytest.mark.asyncio
async def test_dead_worker_callback_persists_reset(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """dead_worker_callback reset is persisted and survives manager restart."""
    data_path = tmp_path / "data"
    manager = ExperimentManager(data_path=data_path)
    definition, directory = make_experiment(params={"x": [1]})
    await _create_experiment(manager, definition, directory)

    dispatched = await manager.dispatch_waiting_run()
    assert dispatched is not None
    async with dispatched:
        dispatched.finalise(wid=7)

    await manager.dead_worker_callback(wid=7)
    rid = dispatched.run.run_id
    manager.close()

    revived = ExperimentManager(data_path=data_path)
    try:
        retrieved = await revived.get_run(rid=rid)
        assert retrieved.active_state == ActiveState.WAITING
        assert retrieved.wid is None
    finally:
        revived.close()
