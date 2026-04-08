"""Module contains server application."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from mprun.errors import InvalidParametersError, NoSuchJobError, NoSuchWorkerError
from mprun.job_manager import JobManager
from mprun.models import Job, JobDefinition, Worker
from mprun.worker_manager import WorkerManager

logger = logging.getLogger(__name__)
DATA_PATH_ENV = "MPRUN_DATA_PATH"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI voodoo to integrate state."""
    data_path_str = os.getenv(DATA_PATH_ENV)
    data_path = Path.home() / ".mprun" if data_path_str is None else Path(data_path_str)

    logger.info(f"Starting server in {data_path}")
    app.state.job_manager = JobManager(data_path=data_path)
    app.state.worker_manager = WorkerManager()
    try:
        yield
    finally:
        app.state.job_manager.close()


app = FastAPI(
    title="mprun",
    version="0.0.1",
    lifespan=lifespan,
)


def get_job_manager(request: Request) -> JobManager:
    """Dependency to inject the shared JobManager."""
    return request.app.state.job_manager


def get_worker_manager(request: Request) -> WorkerManager:
    """Dependency to inject the shared WorkerManager."""
    return request.app.state.worker_manager


@app.post("/jobs", response_model=Job, status_code=HTTPStatus.CREATED)
def create_job(
    request: JobDefinition,
    jm: JobManager = Depends(get_job_manager),
) -> Job:
    """Create a new job from JobCreateRequest and return it."""
    logger.debug("Received job create request")
    try:
        job = jm.create_job(request)
    except InvalidParametersError as err:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=str(err)
        ) from err
    return job


@app.get("/jobs", response_model=list[Job])
def list_jobs(
    jm: JobManager = Depends(get_job_manager),
) -> list[Job]:
    """Return all existing jobs."""
    logger.debug("Received job list request")
    return jm.get_all()


@app.get("/jobs/{jid}", response_model=Job)
def get_job(
    jid: int,
    jm: JobManager = Depends(get_job_manager),
) -> Job:
    """Return a single job by ID."""
    logger.debug("Received job get request")
    try:
        return jm.get(jid)
    except NoSuchJobError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@app.post("/workers", response_model=Worker, status_code=HTTPStatus.CREATED)
def register_worker(
    name: str,
    wm: WorkerManager = Depends(get_worker_manager),
) -> Worker:
    """Register a new worker."""
    logger.debug("Received worker registration request")

    return wm.register(name=name)


@app.get("/workers", response_model=list[Worker])
def list_workers(
    wm: WorkerManager = Depends(get_worker_manager),
) -> list[Worker]:
    """Return all registered workers."""
    logger.debug("Received worker list request")
    return wm.get_all()


@app.get("/workers/{wid}", response_model=Worker)
def get_worker(
    wid: int,
    wm: WorkerManager = Depends(get_worker_manager),
) -> Worker:
    """Return a single worker by ID."""
    logger.debug(f"Received worker get request for id {wid}")
    try:
        return wm.get(wid=wid)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@app.post("/workers/checkin/{wid}")
def checkin_worker(
    wid: int, wm: WorkerManager = Depends(get_worker_manager)
) -> Response:
    """Endpoint to perform worker checkin."""
    logger.debug(f"Received worker checkin for id {wid}")
    try:
        wm.checkin(wid=wid)
        return Response(status_code=HTTPStatus.OK)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
