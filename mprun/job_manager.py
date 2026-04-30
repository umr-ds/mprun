"""Module contains tool to manage Jobs."""

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

from mprun.errors import NoSuchJobError
from mprun.models import JOB_ARCHIVE_NAME, ActiveState, Job, JobDefinition, Run


class JobManager:
    """Manages (creates, deletes, dispatches, etc) Jobs.

    Attributes:
        _data_path (Path): Base-path for the data directory. Will be used to store database & job data.
        _db (TinyDB): Database for Job metadata.
        _state_mutex (Lock): Mutex to prevent concurrent state modification.

        _jobs (dict[Job]): All active jobs.
    """

    _data_path: Path
    _db: TinyDB
    _state_mutex: Lock

    _jobs: dict[int, Job]
    _pending_dispatches: set[int]

    def __init__(self, data_path: Path) -> None:
        """Initialise JobManager.

        Args:
            data_path (Path): Base-path for the data directory. Will be used to store database & job data.
        """
        data_path.mkdir(parents=True, exist_ok=True)
        self._data_path = data_path
        self._db = TinyDB(data_path / "db.json")
        self._state_mutex = Lock()

        docs = self._jobs_table.all()
        jobs = [Job.model_validate(doc) for doc in docs]
        self._jobs = {job.jid: job for job in jobs if job.active}
        self._pending_dispatches = set()

    @property
    def _jobs_table(self) -> Table:
        return self._db.table("jobs")

    async def _update(self, job: Job) -> None:
        """Update Job's data in database.

        *IMPORTANT*: This method is *NOT* thread safe. The caller MUST have locked the manager's state_mutex before calling.
        """
        update = Query()
        await to_thread(
            self._jobs_table.update, job.model_dump(), update.jid == job.jid
        )

    def close(self) -> None:
        """Close database & shut down."""
        self._db.close()

    async def get_all(self) -> list[Job]:
        """Get list of all existing Jobs."""
        async with self._state_mutex:
            docs = await to_thread(self._jobs_table.all)
            return [Job.model_validate(doc) for doc in docs]

    def get_job_archive(self, job: Job) -> Path:
        """Gets path to Job's archive."""
        return self._data_path / str(job.jid) / JOB_ARCHIVE_NAME

    async def create_job(self, definition: JobDefinition, archive: BinaryIO) -> Job:
        """Create a new Job.

        Args:
            definition (JobDefinition): Definition for new job.
            archive (BinaryIO): Job archive containing the Job's files.

        Returns:
            Job: Newly created Job.

        Raises:
            ArchiveValidationError: If archive contents do not match the JobDefinition.
            zipfile.BadZipFile: If archive is not a valid ZIP file.
            OSError: If writing archive to disk fails (e.g. disk full, permission denied).
            FileExistsError: If job data directory already exists (UUID collision).
        """
        async with self._state_mutex:
            job = Job.new(definition=definition)

            with TemporaryDirectory(delete=True) as tmp_dir:
                # copy archive to temporary directory for validation
                tmp_archive = Path(tmp_dir) / JOB_ARCHIVE_NAME
                with tmp_archive.open("wb") as f:
                    await to_thread(copyfileobj, archive, f)
                definition.validate_archive(archive_path=tmp_archive)

                # if validation successful, store archive permanently
                job_path = self._data_path / str(job.jid)
                job_path.mkdir(parents=False, exist_ok=False)
                job_archive = job_path / JOB_ARCHIVE_NAME

                await to_thread(
                    copy, src=tmp_archive, dst=job_archive, follow_symlinks=False
                )

            await to_thread(self._jobs_table.insert, job.model_dump())
            self._jobs[job.jid] = job

            return job

    async def get(self, jid: int) -> Job:
        """Get a Job by its ID.

        Args:
            jid (int): Job ID to look for.

        Returns:
            Job: Job with matching ID (if found).

        Raises:
            NoSuchJobError: If there is no Job with a matching ID.
        """
        async with self._state_mutex:
            job = self._jobs.get(jid)
            if job is None:
                raise NoSuchJobError(jid=jid)
            return job

    async def dispatch_waiting_run(self) -> PendingDispatch | None:
        """Get a waiting Run.

        Manager will check if there are any runs with the 'WAITING' state and return one, if available.
        If dispatchable Run is found, set its state to "RUNNING" and its wid to the provided one.

        Returns:
            Run | None: Run-object if a waiting Run is available, None if none available.
        """
        async with self._state_mutex:
            jobs = [job for job in self._jobs.values() if len(job.waiting_runs) > 0]

            for job in jobs:
                runs = [
                    run
                    for run in job.waiting_runs
                    if run.rid not in self._pending_dispatches
                ]
                if not runs:
                    continue

                run = runs[0]
                self._pending_dispatches.add(run.rid)

                return PendingDispatch(manager=self, job=job, run=run)

            return None

    async def commit(self, operation: PendingDispatch) -> None:
        """Commit a pending operation and modify local state accordingly."""
        async with self._state_mutex:
            operation.run.active_state = ActiveState.RUNNING
            operation.run.wid = operation.wid

            if operation.job.active_state == ActiveState.WAITING:
                operation.job.active_state = ActiveState.RUNNING

            await self._update(job=operation.job)

            self._pending_dispatches.remove(operation.run.rid)

    async def cancel(self, operation: PendingDispatch) -> None:
        """Cancel a pending operation."""
        self._pending_dispatches.remove(operation.run.rid)


@dataclass
class PendingDispatch:
    """Represents a Run that has been marked for dispatching, but not assigned to a worker.

    Attributes:
        manager (JobManager): Responsible Job Manager.
        job (Job): The Run's parent Job.
        run (Run): The actual Run.
        wid (int | None): None if no worker has been assigned. Once assigned, the Worker's ID.
        _finalised (bool): Whether the dispatch has been finalised.
    """

    manager: JobManager
    job: Job
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

    def finalise(self, wid: int) -> None:
        """Finalise pending dispatch.

        Args:
            wid (int): ID of the worker that the Run is dispatched to.
        """
        self.wid = wid
        self._finalised = True
