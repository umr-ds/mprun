"""Module contains errors."""

from dataclasses import dataclass


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


@dataclass
class NoSuchJobError(Exception):
    """Raised when trying to retrieve a Job that does not exist.

    Attributes:
        jid (int): Non-existent Job's ID.
    """

    jid: int

    def __str__(self) -> str:
        """Error's string representation."""
        return f"Job with ID {self.jid} does not exist!"
