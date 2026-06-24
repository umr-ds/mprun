"""Interface for execution backends."""

from typing import Protocol


class Backend(Protocol):
    """Interface for worker backends."""

    async def prepare_run_environment(self) -> None:
        """Unpack the experiment archive and build the process environment.

        Raises:
            RunFailure: If something goes wrong during environment preparation.
        """
        ...

    async def execute_run(self) -> None:
        """Execute run.

        Raises:
            RunFailure: If the executable does not finish within its timeout / if it returns a code != 0.
            RunNotPreparedError: If ``prepare_run_environment`` has not been called before, or raised an error.
        """
        ...

    async def collect_results(self) -> None:
        """Package run outputs and result files into a ZIP archive.

        Raises:
            RunFailureError: If packaging results fails.
            RunNotExecutedError: If ``execute_run`` has not been called before, or raised an error.
        """
        ...
