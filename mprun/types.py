"""Module contains custom types shared across the package."""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple


class ActiveState(StrEnum):
    """Possible active states for both Experiments and Runs.

    Meaning for Run:
        WAITING: Run has not been dispatched.
        RUNNING: Run has been dispatched, has not finished.
        FINISHED: Run has finished.

    Meaning for Experiment:
        WAITING: All runs are waiting.
        RUNNING: At least one run is running.
        FINISHED: All runs have finished.
    """

    WAITING = "WAITING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class SuccessState(StrEnum):
    """Possible success states for Experiments and Runs.

    Meaning for Runs:
        PENDING: This Run has not finished
        SUCCESS: This Run has finished without error.
        FAILED: This Run has finished with an error.

    Meaning for Experiments:
        PENDING: There are still Runs with state PENDING, no Runs with state FAILED
        SUCCESS: All Runs have state SUCCESS
        FAILED: At least one Run has state FAILED
    """

    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class RunId(NamedTuple):
    """Composite identity for a Run: parent experiment ID + position index."""

    eid: int
    index: int

    def __str__(self) -> str:
        """Return string representation of RunId."""
        return f"{self.eid}-{self.index}"

    @classmethod
    def from_str(cls, value: str) -> RunId:
        """Parse a RunId from its string representation.

        Args:
            value (str): String of the form ``{eid}-{index}``.

        Returns:
            RunId: Parsed RunId.

        Raises:
            ValueError: If ``value`` is not in the expected format.
        """
        eid_str, index_str = value.split("-", maxsplit=1)
        return cls(eid=int(eid_str), index=int(index_str))
