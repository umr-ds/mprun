"""Tests for experiment_manager module."""

import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from mprun.experiment_manager import ExperimentManager, PendingDispatch
from mprun.models import EXPERIMENT_ARCHIVE_NAME, Experiment
from mprun.types import ActiveState, SuccessState
from tests.helpers.experiment_helper import copy_experiment_to_test_environment


@pytest.mark.asyncio
async def test_create() -> None:
    """Test experiment creation."""
    with TemporaryDirectory(delete=True) as test_dir:
        directory = Path(test_dir)
        manager = ExperimentManager(data_path=directory)

        experiment_description, experiment_description_path = (
            copy_experiment_to_test_environment(directory=directory)
        )
        archive_path = experiment_description.create_archive(
            experiment_toml=experiment_description_path
        )

        all_experiments = await manager.get_all()
        assert not all_experiments

        experiment: Experiment
        with archive_path.open("rb") as f:
            experiment = await manager.create_experiment(
                definition=experiment_description, archive=f
            )

        retrieved = await manager.get_experiment(eid=experiment.eid)
        assert experiment == retrieved

        all_experiments = await manager.get_all()
        assert len(all_experiments) == 1
        assert all_experiments[0] == experiment

        archive_path = (
            manager._data_path / str(experiment.eid) / EXPERIMENT_ARCHIVE_NAME
        )
        assert archive_path.is_file()
        experiment.definition.validate_archive(archive_path=archive_path)


@pytest.mark.asyncio
async def test_dispatch() -> None:
    """Test experiment dispatching."""
    with TemporaryDirectory(delete=True) as test_dir:
        directory = Path(test_dir)
        manager = ExperimentManager(data_path=directory)

        dispatched = await manager.dispatch_waiting_run()
        assert dispatched is None

        experiment_description, experiment_description_path = (
            copy_experiment_to_test_environment(directory=directory)
        )
        archive_path = experiment_description.create_archive(
            experiment_toml=experiment_description_path
        )

        experiment: Experiment
        with archive_path.open("rb") as f:
            experiment = await manager.create_experiment(
                definition=experiment_description, archive=f
            )

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
async def test_results_submit() -> None:
    """Test Run results submission."""
    with TemporaryDirectory(delete=True) as test_dir:
        directory = Path(test_dir)
        manager = ExperimentManager(data_path=directory)

        experiment_description, experiment_description_path = (
            copy_experiment_to_test_environment(directory=directory)
        )
        archive_path = experiment_description.create_archive(
            experiment_toml=experiment_description_path
        )

        experiment: Experiment
        with archive_path.open("rb") as f:
            experiment = await manager.create_experiment(
                definition=experiment_description, archive=f
            )

        wid = 0
        dispatched = await manager.dispatch_waiting_run()
        assert dispatched is not None
        async with dispatched:
            dispatched.finalise(wid=wid)
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
