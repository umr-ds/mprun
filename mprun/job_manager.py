"""Module contains tool to manage Jobs."""

from dataclasses import dataclass
from pathlib import Path

from tinydb import Query, TinyDB
from tinydb.table import Table

from mprun.models import Job, JobDefinition


@dataclass
class NoSuchJobError(Exception):
    """Raised when trying to retrieve a Job that does not exist.

    Attributes:
        jid (int): Non-existent Job's ID.
    """

    jid: int

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job with ID {self.jid} does not exist!"


class JobManager:
    """Manages (creates, deletes, dispatches, etc) Jobs.

    Attributes:
        data_path (Path): Base-path for the data directory. Will be used to store database & job data.
    """

    data_path: Path
    _db: TinyDB

    def __init__(self, data_path: Path) -> None:
        """Initialise JobManager.

        Args:
            data_path (Path): Base-path for the data directory. Will be used to store database & job data.
        """
        data_path.mkdir(parents=True, exist_ok=True)
        self.data_path = data_path
        self._db = TinyDB(data_path / "db.json")

    @property
    def _jobs_table(self) -> Table:
        return self._db.table("jobs")

    def close(self) -> None:
        """Close database & shut down."""
        self._db.close()

    def all_jobs(self) -> list[Job]:
        """Get list of all existing Jobs."""
        docs = self._jobs_table.all()
        return [Job.model_validate(doc) for doc in docs]

    def create_job(self, definition: JobDefinition) -> Job:
        """Create a new Job.

        Args:
            definition (JobDefinition): Definition for new job.

        Returns:
            Job: Newly created Job.
        """
        job = Job.new(definition=definition)
        self._jobs_table.insert(job.model_dump())
        return job

    def get_job(self, jid: int) -> Job:
        """Get a Job by its ID.

        Args:
            jid (int): Job ID to look for.

        Returns:
            Job: Job with matching ID (if found).

        Raises:
            NoSuchJobError: If there is no Job with a matching ID.
        """
        q = Query()
        doc = self._jobs_table.get(q.jid == jid)
        if not doc:
            raise NoSuchJobError(jid=jid)
        return Job.model_validate(doc)
