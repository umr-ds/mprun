"""Module contains tool to manage Experiments."""

from __future__ import annotations

from asyncio import Lock, to_thread
from dataclasses import dataclass
from pathlib import Path
from shutil import copy, copyfileobj
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import BinaryIO

from tinydb import Query, TinyDB
from tinydb.table import Table

from mprun.errors import NoSuchExperimentError, NoSuchRunError
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    ActiveState,
    Experiment,
    ExperimentDefinition,
    Run,
)


class ExperimentManager:
    """Manages (creates, deletes, dispatches, etc) Experiments.

    Attributes:
        _data_path (Path): Base-path for the data directory. Will be used to store database & experiment data.
        _db (TinyDB): Database for Experiment metadata.
        _state_mutex (Lock): Mutex to prevent concurrent state modification.

        _experiments (dict[Experiment]): All active experiments.
    """

    _data_path: Path
    _db: TinyDB
    _state_mutex: Lock

    _experiments: dict[int, Experiment]
    _runs: dict[int, Run]
    _pending_dispatches: set[int]

    def __init__(self, data_path: Path) -> None:
        """Initialise ExperimentManager.

        Args:
            data_path (Path): Base-path for the data directory. Will be used to store database & experiment data.
        """
        data_path.mkdir(parents=True, exist_ok=True)
        self._data_path = data_path
        self._db = TinyDB(data_path / "db.json")
        self._state_mutex = Lock()

        docs = self._experiments_table.all()
        experiments = [Experiment.model_validate(doc) for doc in docs]
        self._experiments = {}
        self._runs = {}

        for experiment in experiments:
            if experiment.active:
                self._experiments[experiment.eid] = experiment
                runs = {run.rid: run for run in experiment.runs}
                self._runs.update(runs)
        self._pending_dispatches = set()

    @property
    def _experiments_table(self) -> Table:
        return self._db.table("experiments")

    async def _update(self, experiment: Experiment) -> None:
        """Update Experiment's data in database.

        *IMPORTANT*: This method is *NOT* thread safe. The caller MUST have locked the manager's state_mutex before calling.
        """
        update = Query()
        await to_thread(
            self._experiments_table.update,
            experiment.model_dump(),
            update.eid == experiment.eid,
        )

    def close(self) -> None:
        """Close database & shut down."""
        self._db.close()

    async def get_all(self) -> list[Experiment]:
        """Get list of all existing Experiments."""
        async with self._state_mutex:
            docs = await to_thread(self._experiments_table.all)
            return [Experiment.model_validate(doc) for doc in docs]

    def get_experiment_archive(self, experiment: Experiment) -> Path:
        """Gets path to Experiment's archive."""
        return self._data_path / str(experiment.eid) / EXPERIMENT_ARCHIVE_NAME

    def _experiment_path(self, experiment: Experiment) -> Path:
        return self._data_path / str(experiment.eid)

    async def create_experiment(
        self, definition: ExperimentDefinition, archive: BinaryIO
    ) -> Experiment:
        """Create a new Experiment.

        Args:
            definition (ExperimentDefinition): Definition for new experiment.
            archive (BinaryIO): Experiment archive containing the Experiment's files.

        Returns:
            Experiment: Newly created Experiment.

        Raises:
            ArchiveValidationError: If archive contents do not match the ExperimentDefinition.
            zipfile.BadZipFile: If archive is not a valid ZIP file.
            OSError: If writing archive to disk fails (e.g. disk full, permission denied).
            FileExistsError: If experiment data directory already exists (UUID collision).
        """
        async with self._state_mutex:
            experiment = Experiment.new(definition=definition)

            with TemporaryDirectory(delete=True) as tmp_dir:
                # copy archive to temporary directory for validation
                tmp_archive = Path(tmp_dir) / EXPERIMENT_ARCHIVE_NAME
                with tmp_archive.open("wb") as f:
                    await to_thread(copyfileobj, archive, f)
                definition.validate_archive(archive_path=tmp_archive)

                # if validation successful, store archive permanently
                experiment_path = self._experiment_path(experiment=experiment)
                experiment_path.mkdir(parents=False, exist_ok=False)
                experiment_archive = experiment_path / EXPERIMENT_ARCHIVE_NAME

                await to_thread(
                    copy, src=tmp_archive, dst=experiment_archive, follow_symlinks=False
                )

            await to_thread(self._experiments_table.insert, experiment.model_dump())
            self._experiments[experiment.eid] = experiment
            runs = {run.rid: run for run in experiment.runs}
            self._runs.update(runs)

            return experiment

    async def get_experiment(self, eid: int) -> Experiment:
        """Get an Experiment by its ID.

        Args:
            eid (int): Experiment ID to look for.

        Returns:
            Experiment: Experiment with matching ID (if found).

        Raises:
            NoSuchExperimentError: If there is no Experiment with a matching ID.
        """
        async with self._state_mutex:
            experiment = self._experiments.get(eid)
            if experiment is None:
                raise NoSuchExperimentError(eid=eid)
            return experiment

    async def get_run(self, rid: int) -> Run:
        """Get a Run by its ID.

        Args:
            rid (int): Run ID to look for.

        Returns:
            Run: Run with matching ID (if found).

        Raises:
            NoSuchRunError: If there is no Run with a matching ID.
        """
        async with self._state_mutex:
            if rid not in self._runs:
                raise NoSuchRunError(rid=rid)
            return self._runs[rid]

    async def dispatch_waiting_run(self) -> PendingDispatch | None:
        """Get a waiting Run.

        Manager will check if there are any runs with the 'WAITING' state and return one, if available.
        If dispatchable Run is found, set its state to "RUNNING" and its wid to the provided one.

        Returns:
            Run | None: Run-object if a waiting Run is available, None if none available.
        """
        async with self._state_mutex:
            experiments = [
                exp for exp in self._experiments.values() if len(exp.waiting_runs) > 0
            ]

            for experiment in experiments:
                runs = [
                    run
                    for run in experiment.waiting_runs
                    if run.rid not in self._pending_dispatches
                ]
                if not runs:
                    continue

                run = runs[0]
                self._pending_dispatches.add(run.rid)

                return PendingDispatch(manager=self, experiment=experiment, run=run)

            return None

    async def submit_run_results(self, run: Run, results_archive: BinaryIO) -> None:
        """Submit results from a run."""
        async with self._state_mutex:
            if run.rid not in self._runs:
                raise NoSuchRunError(rid=run.rid)
            if run.eid not in self._experiments:
                raise NoSuchExperimentError(eid=run.eid)

            experiment = self._experiments[run.eid]

            result_archive_path = (
                self._experiment_path(experiment=experiment) / f"results_{run.rid}.zip"
            )
            with result_archive_path.open("wb") as f:
                await to_thread(copyfileobj, results_archive, f)

            runs = [
                other_run for other_run in experiment.runs if other_run.rid != run.rid
            ]
            runs.append(run)
            experiment.runs = runs

            self._runs[run.rid] = run

            experiment.recalculate_state()
            await self._update(experiment=experiment)

    async def commit(self, operation: PendingDispatch) -> None:
        """Commit a pending operation and modify local state accordingly."""
        async with self._state_mutex:
            operation.run.active_state = ActiveState.RUNNING
            operation.run.wid = operation.wid
            operation.experiment.recalculate_state()

            await self._update(experiment=operation.experiment)

            self._pending_dispatches.remove(operation.run.rid)

    async def cancel(self, operation: PendingDispatch) -> None:
        """Cancel a pending operation."""
        self._pending_dispatches.remove(operation.run.rid)


@dataclass
class PendingDispatch:
    """Represents a Run that has been marked for dispatching, but not assigned to a worker.

    Attributes:
        manager (ExperimentManager): Responsible Experiment Manager.
        experiment (Experiment): The Run's parent Experiment.
        run (Run): The actual Run.
        wid (int | None): None if no worker has been assigned. Once assigned, the Worker's ID.
        _finalised (bool): Whether the dispatch has been finalised.
    """

    manager: ExperimentManager
    experiment: Experiment
    run: Run
    wid: int | None = None
    _finalised: bool = False

    async def __aenter__(self) -> PendingDispatch:
        """Enter context manager."""
        return self

    async def __aexit__(
        self,
        type_: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Exit context manager.

        If the dispatch was finalised, we commit it, otherwise we cancel it.
        """
        if type_ is None and self._finalised:
            await self.manager.commit(self)
        else:
            await self.manager.cancel(self)
        return None

    def finalise(self, wid: int) -> Path:
        """Finalise pending dispatch and return path to parent Experiment's archive.

        Args:
            wid (int): ID of the worker that the Run is dispatched to.

        Returns:
            Path: Filesystem path to the Experiment's archive.
        """
        self.wid = wid
        self._finalised = True
        return self.manager.get_experiment_archive(experiment=self.experiment)
