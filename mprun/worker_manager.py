"""Worker registration and state tracking."""

import asyncio
import logging
from asyncio import Lock, to_thread
from collections.abc import Callable
from pathlib import Path
from time import time
from types import CoroutineType
from typing import Any
from uuid import uuid4

from tinydb import Query, TinyDB
from tinydb.table import Table

from mprun.custom_types import RunId
from mprun.errors import NoSuchRunError, NoSuchWorkerError, WorkerNotDeadError
from mprun.models import WorkerData, WorkerState

logger = logging.getLogger(__name__)

WORKER_TIMEOUT = 600  # timeout is 600s (10 minutes)
WORKER_GC_INTERVAL = 60  # run garbage collector every 60s


class WorkerManager:
    """Tracks registered workers and their run assignments.

    Attributes:
        _data_path (Path): Root directory for all persisted data.
        _db (TinyDB): TinyDB database storing worker metadata.
        _state_mutex (Lock): Guards against concurrent modification.
        _workers (dict[int, WorkerData]): Registered workers keyed by worker ID.
        _gc_task (asyncio.Task[None]): Background task that periodically evicts stale workers.
    """

    _data_path: Path
    _db: TinyDB
    _state_mutex: Lock

    _workers: dict[int, WorkerData]
    _gc_task: asyncio.Task[None]

    _dead_worker_callback: Callable[..., CoroutineType[Any, Any, None]]

    def __init__(
        self,
        data_path: Path,
        dead_worker_callback: Callable[..., CoroutineType[Any, Any, None]],
    ) -> None:
        """Initialise WorkerManager."""
        data_path.mkdir(parents=True, exist_ok=True)
        self._data_path = data_path
        self._db = TinyDB(data_path / "db.json")
        self._state_mutex = Lock()

        docs = self._workers_table.all()
        workers = [WorkerData.model_validate(doc) for doc in docs]
        self._workers = {worker.wid: worker for worker in workers}

        self._dead_worker_callback = dead_worker_callback
        self._gc_task = asyncio.create_task(self._gc_loop())

    @property
    def _workers_table(self) -> Table:
        return self._db.table("workers")

    def close(self) -> None:
        """Close the database connection and stop the garbage collector."""
        self._gc_task.cancel()
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

    async def _get_worker_from_db(self, wid: int) -> WorkerData | None:
        """Fetch a single worker from the database by ID.

        **Not thread-safe.** Caller must hold ``_state_mutex`` before calling.

        Args:
            wid (int): Worker ID to look up.

        Returns:
            WorkerData | None: The matching worker, or ``None`` if not found.
        """
        q = Query()
        docs = await to_thread(self._workers_table.search, q.wid == wid)
        if not docs:
            return None
        return WorkerData.model_validate(docs[0])

    async def get_all(self) -> list[WorkerData]:
        """Return all registered workers (both dead and alive).

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

        Attempting to query a worker which has been marked as ``DEAD`` will result in an error.

        Args:
            wid (int): Worker ID to look up.

        Returns:
            WorkerData: Metadata for the matching worker.

        Raises:
            NoSuchWorkerError: If no (alive) worker with that ID is registered.
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

    async def revive(self, wid: int) -> WorkerData:
        """Revive dead worker.

        Worker may have had networking problems or other temporary problems.
        When it comes back, we want to allow it to reconnect.

        Args:
            wid (int): Reviving worker's ID.

        Raises:
            NoSuchWorkerError: If no worker with this ID exists.
            WorkerNotDeadError: If the worker is not actually dead.
        """
        async with self._state_mutex:
            if wid in self._workers:
                raise WorkerNotDeadError(wid=wid)

            worker = await self._get_worker_from_db(wid=wid)
            if worker is None:
                raise NoSuchWorkerError(wid=wid)

            worker.state = WorkerState.IDLE
            worker.last_check_in = time()
            await self._update(worker_data=worker)
            self._workers[worker.wid] = worker
            return worker

    async def purge(self) -> None:
        """Delete all dead workers permanently."""
        async with self._state_mutex:
            q = Query()
            await to_thread(self._workers_table.remove, q.state == WorkerState.DEAD)

    async def _gc_loop(self) -> None:
        """Background task that periodically evicts stale workers."""
        try:
            while True:
                await asyncio.sleep(WORKER_GC_INTERVAL)
                await self._collect_garbage()
        except asyncio.CancelledError:
            return

    async def _collect_garbage(self) -> None:
        """Scan workers and evict any whose last check-in exceeds the timeout."""
        dead_wids: list[int]
        async with self._state_mutex:
            now = time()
            dead_wids = [
                wid
                for wid, worker in self._workers.items()
                if now - worker.last_check_in > WORKER_TIMEOUT
            ]  # all live workers which have exceeded the timeout

            for wid in dead_wids:
                worker = self._workers[wid]
                worker.state = WorkerState.DEAD
                worker.run = None
                await self._update(worker_data=worker)
                del self._workers[wid]
                logger.info(
                    "Garbage-collected dead worker %d (%s)",
                    wid,
                    worker.name,
                )

        for wid in dead_wids:
            await self._dead_worker_callback(wid=wid)
