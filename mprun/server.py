"""Module contains server application."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import ValidationError

from mprun.errors import InvalidParametersError, NoSuchJobError, NoSuchWorkerError
from mprun.job_manager import JobManager
from mprun.models import Job, JobDefinition, Run, WorkerData
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


server = FastAPI(
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


@server.post("/jobs", response_model=Job, status_code=HTTPStatus.CREATED)
async def create_job(
    job_definition: str = Form(...),
    archive: UploadFile = File(...),
    jm: JobManager = Depends(get_job_manager),
) -> Job:
    """Create a new job from multipart form (JobDefinition JSON + archive) and return it."""
    logger.debug("Received job create request")
    try:
        definition = JobDefinition.model_validate_json(job_definition)
    except ValidationError as err:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(err)
        ) from err
    try:
        job = await jm.create_job(definition=definition, archive=archive.file)
    except InvalidParametersError as err:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=str(err)
        ) from err
    return job


@server.get("/jobs", response_model=list[Job])
async def list_jobs(
    jm: JobManager = Depends(get_job_manager),
) -> list[Job]:
    """Return all existing jobs."""
    logger.debug("Received job list request")
    return await jm.get_all()


@server.get("/jobs/{jid}", response_model=Job)
async def get_job(
    jid: int,
    jm: JobManager = Depends(get_job_manager),
) -> Job:
    """Return a single job by ID."""
    logger.debug("Received job get request")
    try:
        return await jm.get(jid)
    except NoSuchJobError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.post("/workers", response_model=WorkerData, status_code=HTTPStatus.CREATED)
async def register_worker(
    name: str,
    wm: WorkerManager = Depends(get_worker_manager),
) -> WorkerData:
    """Register a new worker."""
    logger.debug("Received worker registration request")

    return await wm.register(name=name)


@server.get("/workers", response_model=list[WorkerData])
async def list_workers(
    wm: WorkerManager = Depends(get_worker_manager),
) -> list[WorkerData]:
    """Return all registered workers."""
    logger.debug("Received worker list request")
    return await wm.get_all()


@server.get("/workers/run", response_model=None)
async def get_run_for_worker(
    wid: int,
    jm: JobManager = Depends(get_job_manager),
    wm: WorkerManager = Depends(get_worker_manager),
) -> Run | Response:
    """Workers query this endpoint to get a run to execute."""
    try:
        _ = await wm.get(wid=wid)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err

    dispatched_run = await jm.dispatch_waiting_run(wid=wid)
    if dispatched_run is None:
        return Response(status_code=HTTPStatus.NO_CONTENT)

    try:
        await wm.assign_run(wid=wid, rid=dispatched_run.rid)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err

    return dispatched_run


@server.get("/workers/{wid}", response_model=WorkerData)
async def get_worker(
    wid: int,
    wm: WorkerManager = Depends(get_worker_manager),
) -> WorkerData:
    """Return a single worker by ID."""
    logger.debug(f"Received worker get request for id {wid}")
    try:
        return await wm.get(wid=wid)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.post("/workers/checkin/{wid}")
async def checkin_worker(
    wid: int, wm: WorkerManager = Depends(get_worker_manager)
) -> Response:
    """Endpoint to perform worker checkin."""
    logger.debug(f"Received worker checkin for id {wid}")
    try:
        await wm.checkin(wid=wid)
        return Response(status_code=HTTPStatus.OK)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
