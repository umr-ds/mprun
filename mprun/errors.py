"""Module contains errors."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ArchiveValidationError(ValueError):
    """Raised when a Job archive fails validation."""

    reason: str

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job archive invalid! Reason: {self.reason}"


@dataclass(frozen=True)
class InvalidParametersError(ValueError):
    """Raised when the user submits an invalid parameter set.

    Attributes:
        reason (str): The exact reason why the parameters were invalid.
    """

    reason: str

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job parameters invalid! Reason: {self.reason}"


@dataclass(frozen=True)
class NoSuchJobError(LookupError):
    """Raised when trying to retrieve a Job that does not exist.

    Attributes:
        jid (int): Non-existent Job's ID.
    """

    jid: int

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job with ID {self.jid} does not exist!"


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
