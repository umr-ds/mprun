"""Pseudo-interface for execution backends."""

from abc import ABC, abstractmethod

from mprun.custom_types import FailureReason


class Backend(ABC):
    """Abstract base class (because Python does not have interfaces) for worker backends."""

    @abstractmethod
    async def prepare_run_environment(self) -> None:
        """Unpack the experiment archive and build the process environment."""
        return NotImplemented

    @abstractmethod
    async def execute_run(self) -> FailureReason | None:
        """Execute run."""
        return NotImplemented

    @abstractmethod
    async def collect_results(self) -> None:
        """Package run outputs and result files into a ZIP archive."""
        return NotImplemented
