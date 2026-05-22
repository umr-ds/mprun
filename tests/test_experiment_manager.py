"""Tests for experiment_manager module."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from mprun.experiment_manager import ExperimentManager, PendingDispatch
from mprun.models import EXPERIMENT_ARCHIVE_NAME, ActiveState, Experiment
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

        found = False
        for run in retrieved.runs:
            if run == dispatched.run:
                found = True
                assert run.active_state == ActiveState.RUNNING
                break
        assert found
