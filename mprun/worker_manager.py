"""Module contains tool to manage Workers."""

from asyncio import Lock
from time import time
from uuid import uuid4

from mprun.errors import NoSuchRunError, NoSuchWorkerError
from mprun.models import WorkerData, WorkerState


class WorkerManager:
    """Manages Workers.

    Attributes:
        workers (dict[worker]): Dictionary of registered workers.
    """

    workers: dict[int, WorkerData]
    _state_mutex: Lock

    def __init__(self) -> None:
        """Initialise WorkerManager."""
        self.workers = {}
        self._state_mutex = Lock()

    async def get_all(self) -> list[WorkerData]:
        """Get list of all registered workers."""
        async with self._state_mutex:
            return list(self.workers.values())

    async def register(self, name: str) -> WorkerData:
        """Registers a new worker with the manager.

        Args:
            name (str): New worker's name.

        Returns:
            WorkerData: Newly created worker model.
        """
        async with self._state_mutex:
            worker = WorkerData.new(name=name)

            # just in case we happen to roll a UUID that already exists
            while worker.wid in self.workers:
                worker.wid = uuid4().int

            self.workers[worker.wid] = worker
            return worker

    async def get(self, wid: int) -> WorkerData:
        """Get worker with given ID.

        Args:
            wid (int): Worker's unique ID.

        Returns:
            WorkerData: Worker model, if it exists.

        Raises:
            NoSuchWorkerError: If no worker with the given id exists.
        """
        async with self._state_mutex:
            if wid not in self.workers:
                raise NoSuchWorkerError(wid=wid)

            return self.workers[wid]

    async def check_in(self, wid: int) -> None:
        """Perform worker check in.

        Sets workers 'last_checkin' to current time.

        Args:
            wid (int): Worker's ID.

        Raises:
            NoSuchWorkerError: If no worker with the given id exists.
        """
        async with self._state_mutex:
            if wid not in self.workers:
                raise NoSuchWorkerError(wid=wid)

            self.workers[wid].last_check_in = time()

    async def assign_run(self, wid: int, rid: int) -> None:
        """Assign Run to worker.

        Stores that woker is currently executing given Run and sets Worker's state to "WORKING".

        Args:
            wid (int): Worker's ID.
            rid (int): Run's ID.

        Raises:
            NoSuchWorkerError: If no worker with the given id exists.
        """
        async with self._state_mutex:
            if wid not in self.workers:
                raise NoSuchWorkerError(wid=wid)

            worker = self.workers[wid]
            worker.state = WorkerState.WORKING
            worker.run = rid

    async def unassign_run(self, wid: int, rid: int, state: WorkerState) -> None:
        """Unassign Run from worker.

        Either because the worker finished executing the run, or because it has died.

        Args:
            wid (int): Worker's ID.
            rid (int): Run's ID.
            state (WorkerState): Worker's new state after unassignment

        Raises:
            NoSuchWorkerError: If no worker with the given id exists.
        """
        async with self._state_mutex:
            if wid not in self.workers:
                raise NoSuchWorkerError(wid=wid)

            worker = self.workers[wid]
            if worker.run != rid:
                raise NoSuchRunError(rid=rid)

            worker.state = state
            worker.run = None
