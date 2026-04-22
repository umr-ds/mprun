"""Module contains Pydantic models."""

from __future__ import annotations

from enum import StrEnum
from itertools import product
from time import time
from typing import Any
from uuid import uuid4, uuid5

from pydantic import BaseModel

from mprun.errors import InvalidParametersError


def _expand_parameters(params: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Expand parameter set by creating cross-product of all parameters.

    Args:
        params (dict[str, list[Any]]): Dictionary of Lists of parameters

    Returns:
        list[dict[str, Any]]: List of Dicts of single parameter set, each containing one value from the provided lists.
                              All possible permutations.
    """
    if not params:
        raise InvalidParametersError(reason="Empty parameters not allowed")

    for key, values in params.items():
        if not key:
            raise InvalidParametersError(
                reason="Parameter keys must not be empty string"
            )
        if not values:
            raise InvalidParametersError(reason="Parameter lists must not be empty")

    expanded: list[dict[str, Any]] = []

    keys = list(params.keys())
    for values in product(*params.values()):
        expanded.append(dict(zip(keys, values, strict=True)))

    return expanded


class JobState(StrEnum):
    """Possible states for both Jobs and Runs.

    Meaning for Run:
        WAITING: Run has not been dispatched.
        RUNNING: Run has been dispatched, has not finished.
        FINISHED: Run has finished without error.
        FAILED: Run has finished with an error.

    Meaning for Job:
        WAITING: All runs are waiting.
        RUNNING: At least one run is running.
        FINISHED: All runs have finished without error.
        FAILED: At least one run has finished with an error.
    """

    WAITING = "WAITING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class JobDefinition(BaseModel):
    """Minimal info to create a new Job.

    Args:
        name (str): Human readable job name. Does not have to be unique.
        params (dict[str, list[Any]]): Job's parameters. Each parameter should be a list of discrete values.
                                       Will be used to generate runs by computing cross product of parameter lists.
    """

    name: str
    params: dict[str, list[Any]]


class Job(BaseModel):
    """Parametrised job.

    The Job acts as the container for a list of Runs, which are generated during initialisation from the Job's parameters.

    Attributes:
        jid (int): Unique identifier of this job. Generated automatically from uuid.uuid4.
                   (Integer representation of a UUID for serialisability)
        name (str): Human readable job name. Does not have to be unique.
        state (JobState): Job's state. See JobState enum for behaviour documentation.
        params (dict[str, list[Any]]): Job's parameters. Each parameter should be a list of discrete values.
                                       Will be used to generate runs by computing cross product of parameter lists.
        runs (list[Run]): List of Runs that were generated from parameters.
        waiting_runs (int): Number of Runs that are waiting for dispatch.
    """

    jid: int
    name: str
    state: JobState
    params: dict[str, list[Any]]
    runs: list[Run]
    waiting_runs: int

    def __str__(self) -> str:
        """Job's string representation."""
        return f"Job({self.name})"

    def __hash__(self) -> int:
        """Compute hash of Job."""
        return hash(self.jid)

    def __eq__(self, other: object) -> bool:
        """Check if Jobs are equal."""
        if isinstance(other, Job):
            return self.jid == other.jid
        return False

    @classmethod
    def new(cls, definition: JobDefinition) -> Job:
        """Create new Job from a JobDefinition.

        Args:
            definition (JobDefinition): Definition of new Job
        """
        jid = uuid4()
        state = JobState.WAITING
        runs: list[Run] = []
        expanded = _expand_parameters(definition.params)
        for index, param_set in enumerate(expanded):
            runs.append(
                Run(
                    jid=jid.int,
                    rid=uuid5(namespace=jid, name=bytes(index)).int,
                    index=index,
                    name=f"{definition.name}-{index}",
                    state=JobState.WAITING,
                    params=param_set,
                ),
            )

        return Job(
            jid=jid.int,
            name=definition.name,
            state=state,
            params=definition.params,
            runs=runs,
            waiting_runs=len(
                runs,
            ),
        )

    def dispatch_run(self) -> Run | None:
        """Dispatches waiting Run.

        Checks this Job's Run to see if there is at least one with state "WAITING".
        If more than one waiting Run exists, we do not guarantee the order in which they are dispatched.

        Returns:
            Run | None: Run-object if a waiting Run exists, None otherwise.
        """
        waiting = [run for run in self.runs if run.state == JobState.WAITING]
        if not waiting:
            return None

        dispatched = waiting[0]
        dispatched.state = JobState.RUNNING
        self.waiting_runs -= 1
        if self.state == JobState.WAITING:
            self.state = JobState.RUNNING

        return dispatched


class Run(BaseModel):
    """A single run of a Job.

    Attributes:
        jid (int): Job ID of the parent Job. (Integer representation of a UUID for serialisability)
        index (int): Run's index amongst its brethren.
        rid (int): Unique identifier for this Run. (Integer representation of a UUID for serialisability)
                    Generated with uuid.uuid5, using parent's job ID as namespace and index as name.
        wid (int | None): If this Run has been dispatched to a worker, this attribute contains the worker's ID.
                          None if Run has not yet been dispatched.
        name (str): Human-readable name. Generated using {job_name}-{index}.
        state (JobState): Run's state. See JobState enum for behaviour documentation.
        params (dict[str, Any]): Run's parameter set. Has one value from each of the parent Job's parameter lists.
    """

    jid: int
    index: int
    rid: int
    wid: int | None = None
    name: str
    state: JobState
    params: dict[str, Any]

    def __str__(self) -> str:
        """Return a string representation of the Job."""
        return f"Run({self.name})"

    def __hash__(self) -> int:
        """Compute hash of Run."""
        return hash(self.rid)

    def __eq__(self, other: object) -> bool:
        """Check if Runs are equal."""
        if isinstance(other, Run):
            return self.rid == other.rid
        return False


class WorkerState(StrEnum):
    """Possible states for Workers.

    IDLE: Worker is not currently executing a Job.
    WORKING: Worker is currently executing a Job.
    DEAD: Worker is unreachable.
    """

    IDLE = "IDLE"
    WORKING = "WORKING"
    DEAD = "DEAD"


class WorkerData(BaseModel):
    """A Worker.

    Attributes:
        wid (int): Unique identifier - integer representation of a UUID.
        name (str): Human readable name. Does not have to be unique, but is encouraged to be.
        state (WorkerState): Worker's state. See WorkerState enum for behaviour documentation.
        run: (int): If the worker is currently executing a Run, this attribute sotres that Run's ID.
    """

    wid: int
    name: str
    state: WorkerState
    last_checkin: float
    run: int | None = None

    @classmethod
    def new(cls, name: str) -> WorkerData:
        """Create new worker.

        Args:
            name (str): Human readable name. Does not have to be unique, but is encouraged to be.
        """
        return WorkerData(
            wid=uuid4().int, name=name, state=WorkerState.IDLE, last_checkin=time()
        )
