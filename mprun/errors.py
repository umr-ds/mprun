"""Custom exception types."""

from dataclasses import dataclass

from mprun.custom_types import RunId


@dataclass(frozen=True)
class ArchiveValidationError(ValueError):
    """Raised when an experiment archive fails validation.

    Attributes:
        reason (str): Human-readable explanation of why validation failed.
    """

    reason: str

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

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Experiment parameters invalid! Reason: {self.reason}"


class NoRunError(AttributeError):
    """Raised when a worker method requires an active run but none is assigned.

    Indicates a programming error — callers must check ``working`` before invoking
    methods that require an active run.
    """

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

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Worker with ID {self.wid} does not exist!"
