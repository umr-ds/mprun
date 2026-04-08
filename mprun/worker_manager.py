"""Module contains tool to manage Workers."""

from time import time
from uuid import uuid4

from mprun.errors import NoSuchWorkerError
from mprun.models import Worker


class WorkerManager:
    """Manages Workers.

    Attributes:
        workers (dict[worker]): Dictionary of registered workers.
    """

    workers: dict[int, Worker]

    def __init__(self) -> None:
        """Initialise WorkerManager."""
        self.workers = {}

    def get_all(self) -> list[Worker]:
        """Get list of all registered workers."""
        return list(self.workers.values())

    def register(self, name: str) -> Worker:
        """Registers a new worker with the manager.

        Args:
            name (str): New worker's name.

        Returns:
            Worker: Newly created worker model.
        """
        worker = Worker.new(name=name)

        # just in case we happen to roll a UUID that already exists
        while worker.wid in self.workers:
            worker.wid = uuid4().int

        self.workers[worker.wid] = worker
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
        if wid not in self.workers:
            raise NoSuchWorkerError

        return self.workers[wid]

    def checkin(self, wid: int) -> None:
        """Perform worker checkin.

        Sets workers 'last_checkin' to current time.

        Args:
            wid (int): Worker's ID.

        Raises:
            NoSuchWorkerError: If no worker with the given id exists.
        """
        if wid not in self.workers:
            raise NoSuchWorkerError

        self.workers[wid].last_checkin = time()
