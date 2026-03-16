"""Module contains server application."""

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException

from mprun.job import InvalidParametersError, Job, JobCreateRequest
from mprun.job_manager import JobManager, NoSuchJobError

# JobManager singleton for the app lifetime
job_manager = JobManager()


app = FastAPI(
    title="mprun",
    version="0.1.0",
)


def get_job_manager() -> JobManager:
    """Dependency to inject the shared JobManager."""
    return job_manager


@app.post("/jobs", response_model=Job, status_code=201)
def create_job(
    request: JobCreateRequest,
    jm: JobManager = Depends(get_job_manager),
) -> Job:
    """Create a new job from JobCreateRequest and return it."""
    try:
        job = jm.create_job_from_request(request)
    except InvalidParametersError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job


@app.get("/jobs", response_model=list[Job])
def list_jobs(
    jm: JobManager = Depends(get_job_manager),
) -> list[Job]:
    """Return all existing jobs."""
    return jm.all_jobs()


@app.get("/jobs/{jid}", response_model=Job)
def get_job(
    jid: UUID,
    jm: JobManager = Depends(get_job_manager),
) -> Job:
    """Return a single job by ID."""
    try:
        return jm.get_job(jid)
    except NoSuchJobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

