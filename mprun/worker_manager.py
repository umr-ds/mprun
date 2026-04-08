"""Module contains tool to manage Workers."""

from mprun.errors import NoSuchWorkerError
from mprun.models import Worker


class WorkerManager:
    """Manages Workers.

    Attributes:
        workers (list[worker]): List of registered workers.
    """

    workers: list[Worker]

    def __init__(self) -> None:
        """Initialise WorkerManager."""
        self.workers = []

    def get_all(self) -> list[Worker]:
        """Get list of all registered workers."""
        return self.workers

    def register(self, name: str) -> Worker:
        """Registers a new worker with the manager.

        Args:
            name (str): New worker's name.

        Returns:
            Worker: Newly created worker model.
        """
        worker = Worker.new(name=name)
        self.workers.append(worker)
        return worker

    def get(self, wid: int) -> Worker:
        """Get worker with given ID.

        Args:
            wid (int): Worker's unique ID.

        Returns:
            Worker: Worker model, if it exists.

        Raises:
            NoSuchWorkerError: If no worker with the given id exists.
        """
        for worker in self.workers:
            if worker.wid == wid:
                return worker

        raise NoSuchWorkerError(wid=wid)
