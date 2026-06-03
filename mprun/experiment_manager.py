"""Experiment lifecycle management."""

from __future__ import annotations

import errno
import logging
import os
from asyncio import Lock, to_thread
from dataclasses import dataclass
from pathlib import Path
from shutil import copy, copyfileobj, rmtree
from tempfile import TemporaryDirectory
from time import time
from types import TracebackType
from typing import BinaryIO

from tinydb import Query, TinyDB
from tinydb.table import Table

from mprun.custom_types import ActiveState, RunId
from mprun.errors import NoSuchExperimentError, NoSuchRunError
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    Experiment,
    ExperimentDefinition,
    Run,
)

logger = logging.getLogger(__name__)


def _copy_to_file(src: BinaryIO, dst: Path) -> None:
    with dst.open("wb") as f:
        copyfileobj(src, f)


class ExperimentManager:
    """Creates, dispatches, and tracks Experiments and their Runs.

    Attributes:
        _data_path (Path): Root directory for all persisted data (database and experiment archives).
        _db (TinyDB): TinyDB database storing experiment metadata.
        _state_mutex (Lock): Async lock guarding all mutable state. Every method that reads or
            writes ``_experiments``, ``_runs``, or ``_pending_dispatches`` must hold this lock.
        _experiments (dict[int, Experiment]): In-memory cache of active experiments, keyed by
            experiment ID. Finished experiments are evicted on completion.
        _runs (dict[RunId, Run]): In-memory cache of runs belonging to active experiments.
        _pending_dispatches (set[RunId]): Run IDs currently being dispatched but not yet
            committed to a worker. Prevents the same run from being handed out twice.
    """

    _data_path: Path
    _db: TinyDB
    _state_mutex: Lock

    _experiments: dict[int, Experiment]
    _runs: dict[RunId, Run]
    _pending_dispatches: set[RunId]

    def __init__(self, data_path: Path) -> None:
        """Initialise the ExperimentManager.

        Loads all active experiments from the database into memory. Finished experiments are
        not cached but remain queryable via the database.

        Args:
            data_path (Path): Root directory for all persisted data. Created if it does not exist.
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
                runs = {run.run_id: run for runs in experiment.runs for run in runs}
                self._runs.update(runs)
        self._pending_dispatches = set()
        logger.info(
            "ExperimentManager initialised: %d active experiments, %d runs loaded",
            len(self._experiments),
            len(self._runs),
        )

    @property
    def _experiments_table(self) -> Table:
        return self._db.table("experiments")

    async def _update(self, experiment: Experiment) -> None:
        """Persist the current state of an experiment to the database.

        **Not thread-safe.** Caller must hold ``_state_mutex`` before calling.
        """
        update = Query()
        await to_thread(
            self._experiments_table.update,
            experiment.model_dump(),
            update.eid == experiment.eid,
        )

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    async def get_all(self) -> list[Experiment]:
        """Return all experiments, including finished ones.

        Returns:
            list[Experiment]: All experiments stored in the database.
        """
        async with self._state_mutex:
            docs = await to_thread(self._experiments_table.all)
            return [Experiment.model_validate(doc) for doc in docs]

    def get_experiment_archive(self, experiment: Experiment) -> Path:
        """Return the filesystem path to an experiment's archive.

        Args:
            experiment (Experiment): The experiment whose archive path to return.

        Returns:
            Path: Path to the experiment's ZIP archive.
        """
        return self._experiment_path(eid=experiment.eid) / EXPERIMENT_ARCHIVE_NAME

    def _experiment_path(self, eid: int) -> Path:
        return self._data_path / str(eid)

    def _run_results_path(self, rid: RunId) -> Path:
        return (
            self._experiment_path(eid=rid.eid)
            / f"results_{rid.eid}_{rid.index}_{rid.iteration}.zip"
        )

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
            runs = {run.run_id: run for runs in experiment.runs for run in runs}
            self._runs.update(runs)

            logger.info(
                "Experiment created: eid=%d name=%r runs=%d",
                experiment.eid,
                experiment.definition.name,
                len(runs),
            )
            return experiment

    async def _get_experiment_from_db(self, eid: int) -> Experiment | None:
        """Fetch a single experiment from the database by ID.

        **Not thread-safe.** Caller must hold ``_state_mutex`` before calling.

        Args:
            eid (int): Experiment ID to look up.

        Returns:
            Experiment | None: The matching experiment, or ``None`` if not found.
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
                logger.debug("Experiment %d cache hit", eid)
                return experiment
            logger.debug("Experiment %d cache miss: querying database", eid)
            experiment = await self._get_experiment_from_db(eid)
            if experiment is None:
                logger.debug("No such experiment: %d", eid)
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
                logger.debug("Run %s cache hit", rid)
                return run
            logger.debug("Run %s cache miss: querying database", rid)
            experiment = await self._get_experiment_from_db(rid.eid)
            if (
                experiment is None
                or rid.index < 0
                or rid.index >= len(experiment.runs)
                or rid.iteration < 1
                or rid.iteration >= experiment.definition.iterations
            ):
                raise NoSuchRunError(run_id=rid)
            return experiment.runs[rid.index][rid.iteration]

    async def dispatch_waiting_run(self) -> PendingDispatch | None:
        """Claim a waiting run for dispatch.

        Finds the first run with ``WAITING`` state that is not already being dispatched, marks
        it as pending, and returns a ``PendingDispatch`` context manager. The caller must
        call ``finalise`` on it and let the context manager exit to commit the
        dispatch; any exception (or not calling ``finalise``) causes an automatic cancel.

        Returns:
            PendingDispatch | None: A pending dispatch for the claimed run, or ``None`` if no
                waiting runs are available.
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
                logger.info(
                    "Run %s claimed for dispatch",
                    run.run_id,
                )
                run.started_running = time()
                return PendingDispatch(manager=self, experiment=experiment, run=run)

            logger.debug("No waiting runs available")
            return None

    async def submit_run_results(self, run: Run, results_archive: BinaryIO) -> None:
        """Persist the results of a completed run and update experiment state.

        Stores the results archive to disk, updates the run and experiment states, and evicts the
        experiment from memory if all its runs have finished.

        Args:
            run (Run): The completed run, with its final state already set.
            results_archive (BinaryIO): Binary stream of the results ZIP archive.

        Raises:
            NoSuchRunError: If the run is not tracked by this manager.
            NoSuchExperimentError: If the parent experiment is not tracked by this manager.
            OSError: If writing the results archive to disk fails.
        """
        async with self._state_mutex:
            if run.run_id not in self._runs:
                logger.error(
                    "Submit results for Run %s failed: no such Run", run.run_id
                )
                raise NoSuchRunError(run_id=run.run_id)
            if run.eid not in self._experiments:
                logger.error(
                    "Submit results for Run %s failed: no experiment %d",
                    run.run_id,
                    run.eid,
                )
                raise NoSuchExperimentError(eid=run.eid)

            experiment = self._experiments[run.eid]

            result_archive_path = self._run_results_path(rid=run.run_id)
            await to_thread(_copy_to_file, results_archive, result_archive_path)

            experiment.runs[run.index][run.iteration] = run
            self._runs[run.run_id] = run

            experiment.recalculate_state()
            await self._update(experiment=experiment)

            logger.info(
                "Run results stored: rid=%s state=%s",
                run.run_id,
                run.active_state,
            )

            # if the experiment is finished now, we can evict it from memory
            if not experiment.active:
                del self._experiments[experiment.eid]
                for runs in experiment.runs:
                    for finished_run in runs:
                        self._runs.pop(finished_run.run_id, None)
                logger.info("Experiment finished and evicted: eid=%d", experiment.eid)

    async def get_run_results(self, rid: RunId) -> Path:
        """Return the path to a run's results archive.

        Args:
            rid (RunId): Composite run identity (eid + index).

        Returns:
            Path: Path to the run's results ZIP archive.

        Raises:
            FileNotFoundError: If no results archive exists — either the run does not exist or
                has not finished yet.
        """
        async with self._state_mutex:
            path = self._run_results_path(rid=rid)
            if await to_thread(path.is_file):
                return path
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path))

    async def get_experiment_results(self, eid: int) -> list[Path]:
        """Return paths to all available results archives for an experiment.

        Only includes runs that have actually finished (i.e. produced a results archive).

        Args:
            eid (int): Experiment ID.

        Returns:
            list[Path]: Sorted paths to per-run results archives.

        Raises:
            NoSuchExperimentError: If no experiment with that ID exists.
        """
        async with self._state_mutex:
            experiment_directory = self._experiment_path(eid=eid)
            if not await to_thread(experiment_directory.is_dir):
                raise NoSuchExperimentError(eid=eid)
            archives = await to_thread(experiment_directory.glob, "results_*.zip")
            return sorted(archives)

    async def commit(self, operation: PendingDispatch) -> None:
        """Commit a pending dispatch: mark the run as RUNNING, assign the worker, and persist.

        Rolls back the state change if the database write fails.

        Args:
            operation (PendingDispatch): The dispatch to commit. Must have ``wid`` set via
                ``finalise`` before calling.
        """
        async with self._state_mutex:
            prev_active = operation.run.active_state
            prev_wid = operation.run.wid
            operation.run.active_state = ActiveState.RUNNING
            operation.run.wid = operation.wid
            operation.experiment.recalculate_state()
            try:
                await self._update(experiment=operation.experiment)
                logger.info(
                    "Run dispatched: rid=%s, wid=%d",
                    operation.run.run_id,
                    operation.wid,
                )
            except OSError:
                logger.exception(
                    "Dispatch commit failed, rolling back: rid=%s wid=%d",
                    operation.run.run_id,
                    operation.wid,
                )
                operation.run.active_state = prev_active
                operation.run.wid = prev_wid
                operation.experiment.recalculate_state()
                raise
            finally:
                self._pending_dispatches.discard(operation.run.run_id)

    async def cancel(self, operation: PendingDispatch) -> None:
        """Cancel a pending dispatch, releasing the run back to the waiting pool.

        Args:
            operation (PendingDispatch): The dispatch to cancel.
        """
        async with self._state_mutex:
            self._pending_dispatches.discard(operation.run.run_id)
            operation.run.started_running = None
            logger.debug("Dispatch cancelled: rid=%s", operation.run.run_id)

    async def delete(self, eid: int) -> None:
        """Delete an experiment and all its associated data.

        Removes the experiment from the database, evicts it from the in-memory cache,
        cancels any pending dispatches for its runs, and deletes its data directory
        (archive + results archives) from disk.

        Raises:
            NoSuchExperimentError: If no experiment with that ID exists.
            OSError: If deleting the experiment data directory fails (e.g. permission denied).
        """
        async with self._state_mutex:
            experiment = await self._get_experiment_from_db(eid=eid)
            if experiment is None:
                raise NoSuchExperimentError(eid=eid)

            if experiment.active:
                logger.warning(
                    "Deleting active experiment eid=%d; any in-flight runs will be orphaned",
                    eid,
                )

            # cancel any pending dispatches for this experiment's runs
            run_ids = {run.run_id for runs in experiment.runs for run in runs}
            orphaned = self._pending_dispatches & run_ids
            if orphaned:
                logger.warning(
                    "Cancelling %d pending dispatch(es) for deleted experiment eid=%d",
                    len(orphaned),
                    eid,
                )
                self._pending_dispatches -= orphaned

            # delete experiment from database
            q = Query()
            await to_thread(self._experiments_table.remove, q.eid == eid)

            # drop experiment from cache
            self._experiments.pop(eid, None)
            for run_id in run_ids:
                self._runs.pop(run_id, None)

            # delete experiment data directory (archive + any results archives)
            data_dir = self._experiment_path(eid=eid)
            if await to_thread(data_dir.is_dir):
                await to_thread(rmtree, data_dir)
                logger.info("Experiment deleted: eid=%d", eid)


@dataclass
class PendingDispatch:
    """A run claimed for dispatch but not yet committed to a worker.

    Use as an async context manager. Call ``finalise`` with the target worker ID before the
    context exits to commit the dispatch; any unhandled exception causes an automatic cancel.

    Attributes:
        manager (ExperimentManager): Manager that owns this dispatch.
        experiment (Experiment): Parent experiment of the run being dispatched.
        run (Run): The run being dispatched.
        wid (int | None): Worker ID assigned via ``finalise``, or ``None`` if not yet assigned.
    """

    manager: ExperimentManager
    experiment: Experiment
    run: Run
    wid: int | None = None
    _finalised: bool = False

    async def __aenter__(self) -> PendingDispatch:
        """Enter the dispatch context."""
        return self

    async def __aexit__(
        self,
        type_: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit if finalised and no exception occurred; cancel otherwise."""
        if type_ is None and self._finalised:
            await self.manager.commit(self)
        else:
            await self.manager.cancel(self)
        return None

    def finalise(self, wid: int) -> Path:
        """Assign a worker to this dispatch and mark it ready to commit.

        Args:
            wid (int): ID of the worker the run is being dispatched to.

        Returns:
            Path: Filesystem path to the parent experiment's archive.
        """
        self.wid = wid
        self._finalised = True
        return self.manager.get_experiment_archive(experiment=self.experiment)
