"""Module contains tool to manage Experiments."""

from __future__ import annotations

import errno
import os
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
    Experiment,
    ExperimentDefinition,
    Run,
)
from mprun.types import ActiveState, RunId


def _copy_to_file(src: BinaryIO, dst: Path) -> None:
    with dst.open("wb") as f:
        copyfileobj(src, f)


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
    _runs: dict[RunId, Run]
    _pending_dispatches: set[RunId]

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
                runs = {run.run_id: run for run in experiment.runs}
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

    def _experiment_path(self, eid: int) -> Path:
        return self._data_path / str(eid)

    def _run_results_path(self, rid: RunId) -> Path:
        return self._experiment_path(eid=rid.eid) / f"results_{rid.index}.zip"

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
                await to_thread(_copy_to_file, archive, tmp_archive)
                definition.validate_archive(archive_path=tmp_archive)

                # if validation successful, store archive permanently
                experiment_path = self._experiment_path(eid=experiment.eid)
                await to_thread(experiment_path.mkdir, parents=False, exist_ok=False)
                experiment_archive = experiment_path / EXPERIMENT_ARCHIVE_NAME

                await to_thread(
                    copy, src=tmp_archive, dst=experiment_archive, follow_symlinks=False
                )

            await to_thread(self._experiments_table.insert, experiment.model_dump())
            self._experiments[experiment.eid] = experiment
            runs = {run.run_id: run for run in experiment.runs}
            self._runs.update(runs)

            return experiment

    async def _get_experiment_from_db(self, eid: int) -> Experiment | None:
        """Fetch a single experiment from TinyDB by eid.

        *IMPORTANT*: This method is *NOT* thread safe. The caller MUST have locked the manager's state_mutex before calling.
        """
        q = Query()
        docs = await to_thread(self._experiments_table.search, q.eid == eid)
        if not docs:
            return None
        return Experiment.model_validate(docs[0])

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
            if experiment is not None:
                return experiment
            experiment = await self._get_experiment_from_db(eid)
            if experiment is None:
                raise NoSuchExperimentError(eid=eid)
            return experiment

    async def get_run(self, rid: RunId) -> Run:
        """Get a Run by its composite identity.

        Args:
            rid (RunId): Composite run identity (eid + index).

        Returns:
            Run: Run with matching identity (if found).

        Raises:
            NoSuchRunError: If there is no Run with a matching identity.
        """
        async with self._state_mutex:
            run = self._runs.get(rid)
            if run is not None:
                return run
            experiment = await self._get_experiment_from_db(rid.eid)
            if experiment is None or rid.index < 0 or rid.index >= len(experiment.runs):
                raise NoSuchRunError(run_id=rid)
            return experiment.runs[rid.index]

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
                    if run.run_id not in self._pending_dispatches
                ]
                if not runs:
                    continue

                run = runs[0]
                self._pending_dispatches.add(run.run_id)

                return PendingDispatch(manager=self, experiment=experiment, run=run)

            return None

    async def submit_run_results(self, run: Run, results_archive: BinaryIO) -> None:
        """Submit results from a run."""
        async with self._state_mutex:
            if run.run_id not in self._runs:
                raise NoSuchRunError(run_id=run.run_id)
            if run.eid not in self._experiments:
                raise NoSuchExperimentError(eid=run.eid)

            experiment = self._experiments[run.eid]

            result_archive_path = self._run_results_path(rid=run.run_id)
            await to_thread(_copy_to_file, results_archive, result_archive_path)

            experiment.runs[run.index] = run
            self._runs[run.run_id] = run

            experiment.recalculate_state()
            await self._update(experiment=experiment)

            # if the experiment is finished now, we can evict it from memory
            if not experiment.active:
                del self._experiments[experiment.eid]
                for finished_run in experiment.runs:
                    self._runs.pop(finished_run.run_id, None)

    async def get_run_results(self, rid: RunId) -> Path:
        """Gets the path of the Run's results archive.

        Args:
            rid (RunId): Run's ID.

        Returns:
            Path: Path to the Run's results archive - if it exists.

        Raises:
            FileNotFoundError: If there is no results archive - either because the Run does not exist, or it hasn't finished yet.
        """
        async with self._state_mutex:
            path = self._run_results_path(rid=rid)
            if await to_thread(path.is_file):
                return path
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path))

    async def get_experiment_results(self, eid: int) -> list[Path]:
        """Gets the paths of all the Experiment's Run's results archives.

        Args:
            eid (int): Experiment's ID.

        Returns:
            list[Path]: Paths to results archives (only includes Runs which have actually finished).

        Raises:
            NoSuchExperimentError: If there is no experiment with that ID.
        """
        async with self._state_mutex:
            experiment_directory = self._experiment_path(eid=eid)
            if not await to_thread(experiment_directory.is_dir):
                raise NoSuchExperimentError(eid=eid)
            archives = await to_thread(experiment_directory.glob, "results_*.zip")
            return sorted(archives)

    async def commit(self, operation: PendingDispatch) -> None:
        """Commit a pending operation and modify local state accordingly."""
        async with self._state_mutex:
            prev_active = operation.run.active_state
            prev_wid = operation.run.wid
            operation.run.active_state = ActiveState.RUNNING
            operation.run.wid = operation.wid
            operation.experiment.recalculate_state()
            try:
                await self._update(experiment=operation.experiment)
            except OSError:
                operation.run.active_state = prev_active
                operation.run.wid = prev_wid
                operation.experiment.recalculate_state()
                raise
            finally:
                self._pending_dispatches.discard(operation.run.run_id)

    async def cancel(self, operation: PendingDispatch) -> None:
        """Cancel a pending operation."""
        async with self._state_mutex:
            self._pending_dispatches.discard(operation.run.run_id)


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
