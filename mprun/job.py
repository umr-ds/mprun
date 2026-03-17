"""Module contains Job class and all other associated types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from itertools import product
from typing import Any
from uuid import uuid4, uuid5

from pydantic import BaseModel


@dataclass
class InvalidParametersError(Exception):
    """Raised when the user submits an invalid parameter set.

    Attributes:
        reason (str): The exact reason why the parameters were invalid.
    """

    reason: str

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job Parameters invalid! Reason: {self.reason}"


def _expand_parameters(params: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Expand parameter set by creating coss-product of all parameters.

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


class State(IntEnum):
    """Possible states for both Jobs and Runs.

    Meaning for Run:
        WAITING: Run has nod been dispatched
        RUNNING: Run has been dispatched, has not finished
        FINISHED: Run has finished without error
        FAILED: Run has finished with an error

    Meaning for Job:
        WAITING: All runs are waiting
        RUNNING: At least one run is running
        FINISHED: All runs have finished without error
        FAILED: At least one run has finished with an error
    """

    WAITING = 1
    RUNNING = 2
    FINISHED = 3
    FAILED = 4


class JobCreateRequest(BaseModel):
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
        state (State): Job's state. See docstring of State enum for behaviour documentation.
        params (dict[str, list[Any]]): Job's parameters. Each parameter should be a list of discrete values.
                                       Will be used to generate runs by computing cross product of parameter lists.
        runs (list[Run]): List of runs that were generated from parameters.
    """

    jid: int
    name: str
    state: State
    params: dict[str, list[Any]]
    runs: list[Run]

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
    def new(cls, name: str, params: dict[str, list[Any]]) -> Job:
        """Create a new Job from a name and parameter set.

        Args:
            name (str): Human readable job name. Does not have to be unique.
            params (dict[str, list[Any]]): Job's parameters. Each parameter should be a list of discrete values.
                                           Will be used to generate runs by computing cross product of parameter lists.

        Other class attributes will be generated automatically.
        """
        jid = uuid4()
        state = State.WAITING
        runs: list[Run] = []
        expanded = _expand_parameters(params)
        for index, param_set in enumerate(expanded):
            runs.append(
                Run(
                    jid=jid.int,
                    rid=uuid5(namespace=jid, name=bytes(index)).int,
                    index=index,
                    name=f"{name}-{index}",
                    state=State.WAITING,
                    params=param_set,
                ),
            )

        return Job(jid=jid.int, name=name, state=state, params=params, runs=runs)

    @classmethod
    def new_from_request(cls, request: JobCreateRequest) -> Job:
        """Create new Job from a request.

        Args:
            request (JobCreateRequest): Request with necessary info.
        """
        return cls.new(name=request.name, params=request.params)


class Run(BaseModel):
    """A single run of a Job.

    Attributes:
        jid (int): Job ID of the parent Job. (Integer representation of a UUID for serialisability)
        index (int): Run's index amongst its brethren.
        rid (int): Unique identifier for this Run. (Integer representation of a UUID for serialisability)
                    Generated with uuid.uuid5, using parent's job ID as namespace and index as name.
        name (str): Human-readable name. Generated using {job_name}-{index}.
        state (State): Run's state. See docstring of State enum for behaviour documentation.
        params (dict[str, Any]): Run's parameter set. Has one value from each of the parent Job's parameter lists.
    """

    jid: int
    index: int
    rid: int
    name: str
    state: State
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
