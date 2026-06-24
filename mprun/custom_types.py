"""Shared types used across the package."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, override

type TOMLScalar = (
    str | int | float | bool
)  # TOML-serialisable types for Experiment params


class ActiveState(StrEnum):
    """Active states shared by both Experiments and Runs.

    Meaning for a Run:
        WAITING: Run has not yet been dispatched to a worker.
        RUNNING: Run has been dispatched but has not finished.
        FINISHED: Run has finished (regardless of success or failure).

    Meaning for an Experiment:
        WAITING: All runs are WAITING.
        RUNNING: At least one run is not WAITING, and at least one is not FINISHED
            (i.e. the experiment is in progress).
        FINISHED: All runs are FINISHED.
    """

    WAITING = "WAITING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class SuccessState(StrEnum):
    """Success states shared by both Experiments and Runs.

    Meaning for a Run:
        PENDING: Run has not yet finished.
        SUCCESS: Run finished with exit code 0.
        FAILED: Run finished with a non-zero exit code or timed out.

    Meaning for an Experiment:
        PENDING: No runs have failed yet, and at least one has not finished.
        SUCCESS: All runs finished successfully.
        FAILED: At least one run failed.
    """

    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class RunId(NamedTuple):
    """Composite identity for a Run: parent experiment ID + position index."""

    eid: int
    index: int
    iteration: int

    @override
    def __str__(self) -> str:
        """Return the RunId as ``{eid}-{index}``."""
        return f"{self.eid}-{self.index}-{self.iteration}"

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
        eid_str, index_str, iteration_str = value.split("-", maxsplit=2)
        return cls(eid=int(eid_str), index=int(index_str), iteration=int(iteration_str))


class AppPaths(NamedTuple):
    """NamedTuple for representing default paths.

    E.g. config paths, data paths, etc.

    Attributes:
        user (Path): User-specific path (e.g. ``~/.local/share``, ``~/.config``, etc.)
        site (Path): System-wide path (e.g. ``usr/local/share``, ``/etc``, etc.)
    """

    user: Path
    site: Path


class WorkerBackend(StrEnum):
    """Possible worker backends."""

    NATIVE = "NATIVE"
    DOCKER = "DOCKER"
