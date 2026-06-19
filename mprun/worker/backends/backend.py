"""Pseudo-interface for execution backends."""

from abc import ABC, abstractmethod


class Backend(ABC):
    """Abstract base class (because Python does not have interfaces) for worker backends."""

    @abstractmethod
    async def prepare_run_environment(self) -> None:
        """Unpack the experiment archive and build the process environment.

        Raises:
            RunFailure: If something goes wrong during environment preparation.
        """
        return NotImplemented

    @abstractmethod
    async def execute_run(self) -> None:
        """Execute run.

        Raises:
            RunFailure: If the executable does not finish within its timeout / if it returns a code != 0.
        """
        return NotImplemented

    @abstractmethod
    async def collect_results(self) -> None:
        """Package run outputs and result files into a ZIP archive."""
        return NotImplemented
