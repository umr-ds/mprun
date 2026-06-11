"""Worker registration and state tracking."""

from asyncio import Lock, to_thread
from pathlib import Path
from time import time
from uuid import uuid4

from tinydb import Query, TinyDB
from tinydb.table import Table

from mprun.custom_types import RunId
from mprun.errors import NoSuchRunError, NoSuchWorkerError
from mprun.models import WorkerData, WorkerState

WORKER_TIMEOUT = 600  # timeout is 600s (10 minutes)


class WorkerManager:
    """Tracks registered workers and their run assignments.

    Attributes:
        _data_path (Path): Root directory for all persisted data.
        _db (TinyDB): TinyDB database storing worker metadata.
        _state_mutex (Lock): Guards against concurrent modification.
        _workers (dict[int, WorkerData]): Registered workers keyed by worker ID.
    """

    _data_path: Path
    _db: TinyDB
    _state_mutex: Lock

    _workers: dict[int, WorkerData]

    def __init__(self, data_path: Path) -> None:
        """Initialise WorkerManager."""
        data_path.mkdir(parents=True, exist_ok=True)
        self._data_path = data_path
        self._db = TinyDB(data_path / "db.json")
        self._state_mutex = Lock()

        docs = self._workers_table.all()
        workers = [WorkerData.model_validate(doc) for doc in docs]
        self._workers = {worker.wid: worker for worker in workers}

    @property
    def _workers_table(self) -> Table:
        return self._db.table("workers")

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    async def _update(self, worker_data: WorkerData) -> None:
        """Persist the current state of a worker to the database.

        **Not thread-safe.** Caller must hold ``_state_mutex`` before calling.
        """
        update = Query()
        await to_thread(
            self._workers_table.update,
            worker_data.model_dump(),
            update.wid == worker_data.wid,
        )

    async def get_all(self) -> list[WorkerData]:
        """Return all registered workers.

        Returns:
            list[WorkerData]: Snapshot of all currently registered workers.
        """
        async with self._state_mutex:
            docs = await to_thread(self._workers_table.all)
            return [WorkerData.model_validate(doc) for doc in docs]

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
            while worker.wid in self._workers:
                worker.wid = uuid4().int

            await to_thread(self._workers_table.insert, worker.model_dump())
            self._workers[worker.wid] = worker
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
            if wid not in self._workers:
                raise NoSuchWorkerError(wid=wid)

            return self._workers[wid]

    async def check_in(self, wid: int) -> None:
        """Record a worker heartbeat by updating its ``last_check_in`` timestamp.

        Args:
            wid (int): ID of the worker checking in.

        Raises:
            NoSuchWorkerError: If no worker with that ID is registered.
        """
        async with self._state_mutex:
            if wid not in self._workers:
                raise NoSuchWorkerError(wid=wid)

            worker = self._workers[wid]
            worker.last_check_in = time()
            await self._update(worker_data=worker)

    async def assign_run(self, wid: int, run_id: RunId) -> None:
        """Assign a run to a worker and set its state to WORKING.

        Args:
            wid (int): ID of the worker to assign the run to.
            run_id (RunId): Composite identity of the run being assigned.

        Raises:
            NoSuchWorkerError: If no worker with that ID is registered.
        """
        async with self._state_mutex:
            if wid not in self._workers:
                raise NoSuchWorkerError(wid=wid)

            worker = self._workers[wid]
            worker.state = WorkerState.WORKING
            worker.run = run_id
            await self._update(worker_data=worker)

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
            if wid not in self._workers:
                raise NoSuchWorkerError(wid=wid)

            worker = self._workers[wid]
            if worker.run != run_id:
                raise NoSuchRunError(run_id=run_id)

            worker.state = state
            worker.run = None
            await self._update(worker_data=worker)
