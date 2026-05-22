"""Module contains errors."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveValidationError(ValueError):
    """Raised when an Experiment archive fails validation."""

    reason: str

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Experiment archive invalid! Reason: {self.reason}"


@dataclass(frozen=True)
class InvalidParametersError(ValueError):
    """Raised when the user submits an invalid parameter set.

    Attributes:
        reason (str): The exact reason why the parameters were invalid.
    """

    reason: str

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Experiment parameters invalid! Reason: {self.reason}"


dataclass(frozen=True)


class NoRunError(AttributeError):
    """Raised when Worker tries to execute a Run, but has no run assigned.

    This should never happen!
    """

    def __str__(self) -> str:
        """Error's string representation."""
        return "There is no run!"


@dataclass(frozen=True)
class NoSuchExperimentError(LookupError):
    """Raised when trying to retrieve an Experiment that does not exist.

    Attributes:
        eid (int): Non-existent Experiment's ID.
    """

    eid: int

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Experiment with ID {self.eid} does not exist!"


@dataclass(frozen=True)
class NoSuchRunError(LookupError):
    """Raised when trying to retrieve a Run that does not exist.

    Attributes:
        rid (int): Non-existent Run's ID.
    """

    rid: int

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Run with ID {self.rid} does not exist!"


@dataclass(frozen=True)
class NoSuchWorkerError(LookupError):
    """Raised when trying to retrieve a Worker that does not exist.

    Attributes:
        wid(int): Non-existent Worker's ID.
    """

    wid: int

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Worker with ID {self.wid} does not exist!"
