"""Module contains tool to manage Jobs."""

from asyncio import Lock, to_thread
from pathlib import Path
from shutil import copy, copyfileobj
from tempfile import TemporaryDirectory
from typing import BinaryIO

from tinydb import Query, TinyDB
from tinydb.table import Table

from mprun.errors import NoSuchJobError
from mprun.models import JOB_ARCHIVE_NAME, Job, JobDefinition, Run


class JobManager:
    """Manages (creates, deletes, dispatches, etc) Jobs.

    Attributes:
        data_path (Path): Base-path for the data directory. Will be used to store database & job data.
    """

    data_path: Path
    _db: TinyDB
    _state_mutex: Lock

    def __init__(self, data_path: Path) -> None:
        """Initialise JobManager.

        Args:
            data_path (Path): Base-path for the data directory. Will be used to store database & job data.
        """
        data_path.mkdir(parents=True, exist_ok=True)
        self.data_path = data_path
        self._db = TinyDB(data_path / "db.json")
        self._state_mutex = Lock()

    @property
    def _jobs_table(self) -> Table:
        return self._db.table("jobs")

    def close(self) -> None:
        """Close database & shut down."""
        self._db.close()

    async def get_all(self) -> list[Job]:
        """Get list of all existing Jobs."""
        async with self._state_mutex:
            docs = await to_thread(self._jobs_table.all)
            return [Job.model_validate(doc) for doc in docs]

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
                job_path = self.data_path / str(job.jid)
                job_path.mkdir(parents=False, exist_ok=False)
                job_archive = job_path / JOB_ARCHIVE_NAME

                await to_thread(
                    copy, src=tmp_archive, dst=job_archive, follow_symlinks=False
                )

            await to_thread(self._jobs_table.insert, job.model_dump())

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
            q = Query()
            doc = await to_thread(self._jobs_table.get, q.jid == jid)
            if not doc:
                raise NoSuchJobError(jid=jid)
            return Job.model_validate(doc)

    async def dispatch_waiting_run(self, wid: int) -> Run | None:
        """Get a waiting Run.

        Manager will check if there are any runs with the 'WAITING' state and return one, if available.
        If dispatchable Run is found, set its state to "RUNNING" and its wid to the provided one.

        Args:
            wid: ID of Worker that's requesting work.

        Returns:
            Run | None: Run-object if a waiting Run is available, None if none available.
        """
        async with self._state_mutex:
            dispatch_query = Query()
            dispatchable = await to_thread(
                self._jobs_table.search, dispatch_query.waiting_runs > 0
            )

            for job_data in dispatchable:
                job = Job.model_validate(job_data)
                run = job.dispatch_run()
                if run is None:
                    continue

                run.wid = wid

                update = Query()
                await to_thread(
                    self._jobs_table.update, job.model_dump(), update.jid == job.jid
                )
                return run

            return None
