"""Worker registration and state tracking."""

from asyncio import Lock
from time import time
from uuid import uuid4

from mprun.errors import NoSuchRunError, NoSuchWorkerError
from mprun.models import WorkerData, WorkerState
from mprun.types import RunId


class WorkerManager:
    """Tracks registered workers and their run assignments. In-memory only; not persisted.

    Attributes:
        workers (dict[int, WorkerData]): Registered workers keyed by worker ID.
        _state_mutex (Lock): Guards ``workers`` against concurrent modification.
    """

    workers: dict[int, WorkerData]
    _state_mutex: Lock

    def __init__(self) -> None:
        """Initialise WorkerManager."""
        self.workers = {}
        self._state_mutex = Lock()

    async def get_all(self) -> list[WorkerData]:
        """Return all registered workers.

        Returns:
            list[WorkerData]: Snapshot of all currently registered workers.
        """
        async with self._state_mutex:
            return list(self.workers.values())

    async def register(self, name: str) -> WorkerData:
        """Register a new worker and return its metadata.

        Assigns a unique UUID-derived ID, retrying on the rare chance of a collision.

        Args:
            name (str): Human-readable name for the new worker.

        Returns:
            WorkerData: Metadata for the newly registered worker.
        """
        async with self._state_mutex:
            worker = WorkerData.new(name=name)

            # just in case we happen to roll a UUID that already exists
            while worker.wid in self.workers:
                worker.wid = uuid4().int

            self.workers[worker.wid] = worker
            return worker

    async def get(self, wid: int) -> WorkerData:
        """Return a single worker by ID.

        Args:
            wid (int): Worker ID to look up.

        Returns:
            WorkerData: Metadata for the matching worker.

        Raises:
            NoSuchWorkerError: If no worker with that ID is registered.
        """
        async with self._state_mutex:
            if wid not in self.workers:
                raise NoSuchWorkerError(wid=wid)

            return self.workers[wid]

    async def check_in(self, wid: int) -> None:
        """Record a worker heartbeat by updating its ``last_check_in`` timestamp.

        Args:
            wid (int): ID of the worker checking in.

        Raises:
            NoSuchWorkerError: If no worker with that ID is registered.
        """
        async with self._state_mutex:
            if wid not in self.workers:
                raise NoSuchWorkerError(wid=wid)

            self.workers[wid].last_check_in = time()

    async def assign_run(self, wid: int, run_id: RunId) -> None:
        """Assign a run to a worker and set its state to WORKING.

        Args:
            wid (int): ID of the worker to assign the run to.
            run_id (RunId): Composite identity of the run being assigned.

        Raises:
            NoSuchWorkerError: If no worker with that ID is registered.
        """
        async with self._state_mutex:
            if wid not in self.workers:
                raise NoSuchWorkerError(wid=wid)

            worker = self.workers[wid]
            worker.state = WorkerState.WORKING
            worker.run = run_id

    async def unassign_run(self, wid: int, run_id: RunId, state: WorkerState) -> None:
        """Clear a worker's run assignment and transition it to a new state.

        Used when a run completes normally or when the worker is marked as dead.

        Args:
            wid (int): ID of the worker to update.
            run_id (RunId): Expected composite identity of the run currently assigned to the
                worker. Must match the worker's current ``run`` field.
            state (WorkerState): State to transition the worker to after unassignment.

        Raises:
            NoSuchWorkerError: If no worker with that ID is registered.
            NoSuchRunError: If the worker's current run does not match ``run_id``.
        """
        async with self._state_mutex:
            if wid not in self.workers:
                raise NoSuchWorkerError(wid=wid)

            worker = self.workers[wid]
            if worker.run != run_id:
                raise NoSuchRunError(run_id=run_id)

            worker.state = state
            worker.run = None
