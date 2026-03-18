"""Module contains server application."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request

from mprun.job import InvalidParametersError, Job, JobDefinition
from mprun.job_manager import JobManager, NoSuchJobError

logger = logging.getLogger(__name__)
DATA_PATH_ENV = "MPRUN_DATA_PATH"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI voodoo to integrate state."""
    data_path_str = os.getenv(DATA_PATH_ENV)
    data_path = Path.home() / ".mprun" if data_path_str is None else Path(data_path_str)

    logger.info(f"Starting server in {data_path}")
    app.state.job_manager = JobManager(data_path=data_path)
    try:
        yield
    finally:
        app.state.job_manager.close()


app = FastAPI(
    title="mprun",
    version="0.1.0",
    lifespan=lifespan,
)


def get_job_manager(request: Request) -> JobManager:
    """Dependency to inject the shared JobManager."""
    return request.app.state.job_manager


@app.post("/jobs", response_model=Job, status_code=201)
def create_job(
    request: JobDefinition,
    jm: JobManager = Depends(get_job_manager),
) -> Job:
    """Create a new job from JobCreateRequest and return it."""
    logger.debug("Received job create request")
    try:
        job = jm.create_job(request)
    except InvalidParametersError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job


@app.get("/jobs", response_model=list[Job])
def list_jobs(
    jm: JobManager = Depends(get_job_manager),
) -> list[Job]:
    """Return all existing jobs."""
    logger.debug("Received job list request")
    return jm.all_jobs()


@app.get("/jobs/{jid}", response_model=Job)
def get_job(
    jid: int,
    jm: JobManager = Depends(get_job_manager),
) -> Job:
    """Return a single job by ID."""
    logger.debug("Received job get request")
    try:
        return jm.get_job(jid)
    except NoSuchJobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
