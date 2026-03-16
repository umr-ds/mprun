"""Module contains tool to manage Jobs."""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from mprun.job import Job, JobCreateRequest


@dataclass
class NoSuchJobError(Exception):
    """Raised when trying to retrieve a Job that does not exist.

    Attributes:
        jid (UUID): Non-existent Job's ID.
    """

    jid: UUID

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job with ID {self.jid} does not exist!"


@dataclass
class JobManager:
    """Manages (creates, deletes, dispatches, etc) Jobs.

    Attributes:
        jobs (list[Job]):
    """

    jobs: list[Job] = field(default_factory=list)

    def all_jobs(self) -> list[Job]:
        """Get list of all existing Jobs."""
        return self.jobs

    def create_job(self, name: str, params: dict[str, list[Any]]) -> Job:
        """Create a new Job.

        Args:
            name (str): Human readable job name. Does not have to be unique.
            params (dict[str, list[Any]]): Job's parameters. Each parameter should be a list of discrete values.
                                           Will be used to generate runs by computing cross product of parameter lists.

        Returns:
            Job: Newly created Job.
        """
        job = Job.new(name=name, params=params)
        self.jobs.append(job)
        return job

    def create_job_from_request(self, request: JobCreateRequest) -> Job:
        """Create a new Job from a request.

        Args:
            request (JobCreateRequest): Request with info for job creation.

        Returns:
            Job: Newly created Job.
        """
        job = Job.new_from_request(request=request)
        self.jobs.append(job)
        return job

    def get_job(self, jid: UUID) -> Job:
        """Get a Job by its ID.

        Args:
            jid (UUID): Job ID to look for.

        Returns:
            Job: Job with matching ID (if found).

        Raises:
            NoSuchJobError: If there is no Job with a matching ID.
        """
        for job in self.jobs:
            if job.jid == jid:
                return job
        raise NoSuchJobError(jid=jid)
