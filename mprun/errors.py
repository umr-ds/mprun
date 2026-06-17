"""Custom exception types."""

from dataclasses import dataclass
from typing import Any, override

from mprun.custom_types import RunId


@dataclass(frozen=True)
class ArchiveValidationError(ValueError):
    """Raised when an experiment archive fails validation.

    Attributes:
        reason (str): Human-readable explanation of why validation failed.
    """

    reason: str

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Experiment archive invalid! Reason: {self.reason}"


@dataclass(frozen=True)
class InvalidParametersError(ValueError):
    """Raised when an invalid parameter set is submitted for an experiment.

    Attributes:
        reason (str): Human-readable explanation of why the parameters are invalid.
    """

    reason: str

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Experiment parameters invalid! Reason: {self.reason}"


class NoRunError(AttributeError):
    """Raised when a worker method requires an active run but none is assigned.

    Indicates a programming error — callers must check ``working`` before invoking
    methods that require an active run.
    """

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return "There is no run!"


@dataclass(frozen=True)
class NoSuchExperimentError(LookupError):
    """Raised when an experiment with the requested ID does not exist.

    Attributes:
        eid (int): The ID that was looked up.
    """

    eid: int

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Experiment with ID {self.eid} does not exist!"


@dataclass(frozen=True)
class NoSuchRunError(LookupError):
    """Raised when a run with the requested identity does not exist.

    Attributes:
        run_id (RunId): The identity that was looked up.
    """

    run_id: RunId

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Run {self.run_id} does not exist!"


@dataclass(frozen=True)
class NoSuchWorkerError(LookupError):
    """Raised when a worker with the requested ID does not exist.

    Attributes:
        wid (int): The ID that was looked up.
    """

    wid: int

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Worker with ID {self.wid} does not exist!"


@dataclass(frozen=True)
class WorkerDeadError(Exception):
    """Raised when trying to do something with a worker that's dead.

    Attributes:
        wid (int): Dead Worker's ID
    """

    wid: int

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Worker with ID {self.wid} is dead!"


@dataclass(frozen=True)
class WorkerNotDeadError(Exception):
    """Raised when trying to revive a worker that's not actually dead.

    Attributes:
        wid (int): Not dead Worker's ID
    """

    wid: int

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Worker with ID {self.wid} is not dead!"


@dataclass(frozen=True)
class InconsistentConfigurationError(ValueError):
    """Raised when a components configuration has conflicts.

    Attributes:
        name (str): Name of the config item.
        expected (Any): What the value should have been.
        got (Any): What the value actually was.
    """

    name: str
    expected: Any
    got: Any

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return (
            f"Configuration value {self.name}: expected {self.expected}, got {self.got}"
        )


class NoSavedMetadataError(Exception):
    """Raised if there is no saved metadata, when there should be."""
