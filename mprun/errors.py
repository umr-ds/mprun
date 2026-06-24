"""Custom exception types."""

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, override

from mprun.custom_types import RunId

if TYPE_CHECKING:
    from mprun.models import Run


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


@dataclass(frozen=True)
class RunFailureError(Exception):
    """Raised when a run fails.

    Attributes:
        run (Run): The run that failed.
        reason (str): Reason for the failure
    """

    run: Run
    reason: Exception

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Run {self.run.name} failed for reason: {self.reason!s}"


@dataclass(frozen=True)
class ExecutableReturnError(Exception):
    """Raised if an executable exits with a status != 0.

    Attributes:
        name (str): Executable's name.
        code (int): Status code returned by executable.
    """

    name: str
    code: int

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Executable {self.name} returned with {self.code}"


@dataclass(frozen=True)
class RunTimeoutError(TimeoutError):
    """Raised when a run's executable exceeds its configured timeout.

    Attributes:
        seconds (int): Timeout duration (in seconds) that was exceeded.
    """

    seconds: int

    @override
    def __str__(self) -> str:
        """Error's string representation."""
        return f"Timed out after {timedelta(seconds=self.seconds)!s}"


class RunNotPreparedError(Exception):
    """Raised by the worker's execution backend if you try to execute the run before preparing the execution environment."""


class RunNotExecutedError(Exception):
    """Raised by the worker's execution backend if you try to collect results before executing the run."""
