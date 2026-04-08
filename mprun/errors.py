"""Module contains errors."""

from dataclasses import dataclass


@dataclass
class InvalidParametersError(ValueError):
    """Raised when the user submits an invalid parameter set.

    Attributes:
        reason (str): The exact reason why the parameters were invalid.
    """

    reason: str

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job Parameters invalid! Reason: {self.reason}"


@dataclass
class NoSuchJobError(LookupError):
    """Raised when trying to retrieve a Job that does not exist.

    Attributes:
        jid (int): Non-existent Job's ID.
    """

    jid: int

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job with ID {self.jid} does not exist!"


@dataclass
class NoSuchWorkerError(LookupError):
    """Raised when trying to retrieve a Worker that does not exist.

    Attributes:
        wid(int): Non-existent Worker's ID.
    """

    wid: int

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Worker with ID {self.wid} does not exist!"
