#! /usr/bin/env python3

"""FastAPI server application."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from zipfile import BadZipFile

import uvicorn
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
from fastapi.responses import FileResponse
from pydantic import ValidationError
from typer import Option, Typer

from mprun import DEFAULT_DATA_DIRS, PACKAGE_NAME, __version__
from mprun.custom_types import RunId
from mprun.errors import (
    ArchiveValidationError,
    InvalidParametersError,
    NoSuchExperimentError,
    NoSuchRunError,
    NoSuchWorkerError,
    WorkerNotDeadError,
)
from mprun.experiment_manager import ExperimentManager
from mprun.log import configure_logging
from mprun.models import Experiment, ExperimentDefinition, Run, WorkerData, WorkerState
from mprun.worker_manager import WorkerManager

logger = logging.getLogger(__name__)
DATA_PATH_ENV = "MPRUN_DATA_PATH"
HOST_ENV = "MPRUN_SERVER_HOST"
PORT_ENV = "MPRUN_SERVER_PORT"

EXPERIMENT_DATA_PATH = "experiments"
WORKER_DATA_PATH = "workers"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI voodoo to integrate state."""
    data_path_str = os.getenv(DATA_PATH_ENV)
    if data_path_str is None:
        data_path = DEFAULT_DATA_DIRS.user / "server"
    else:
        data_path = Path(data_path_str)

    logger.info("Starting server in %s", data_path)
    data_path.mkdir(parents=True, exist_ok=True)

    app.state.experiment_manager = ExperimentManager(
        data_path=data_path / EXPERIMENT_DATA_PATH
    )
    app.state.worker_manager = WorkerManager(
        data_path=data_path / WORKER_DATA_PATH,
        dead_worker_callback=app.state.experiment_manager.dead_worker_callback,
    )
    try:
        yield
    finally:
        app.state.experiment_manager.close()
        app.state.worker_manager.close()


server = FastAPI(
    title=PACKAGE_NAME,
    version=__version__,
    lifespan=lifespan,
)
cli = Typer()


def get_experiment_manager(request: Request) -> ExperimentManager:
    """Dependency to inject the shared ExperimentManager."""
    return request.app.state.experiment_manager


def get_worker_manager(request: Request) -> WorkerManager:
    """Dependency to inject the shared WorkerManager."""
    return request.app.state.worker_manager


@server.post("/experiments", response_model=Experiment, status_code=HTTPStatus.CREATED)
async def create_experiment(
    experiment_definition: str = Form(...),
    archive: UploadFile = File(...),
    em: ExperimentManager = Depends(get_experiment_manager),
) -> Experiment:
    """Create a new experiment from a multipart form and return it.

    Accepts the experiment definition as a JSON-encoded form field and the experiment files
    as a ZIP archive. Returns ``201 Created`` on success. Returns ``400`` for invalid
    parameters, ``422`` for a malformed definition or archive, and ``500`` for I/O failures.
    """
    logger.debug("Received experiment create request")
    try:
        definition = ExperimentDefinition.model_validate_json(experiment_definition)
    except ValidationError as err:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(err)
        ) from err

    try:
        experiment = await em.create_experiment(
            definition=definition, archive=archive.file
        )
    except InvalidParametersError as err:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=str(err)
        ) from err
    except (ArchiveValidationError, BadZipFile) as err:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(err)
        ) from err
    except OSError as err:
        logger.exception("I/O error during experiment creation")
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(err)
        ) from err
    return experiment


@server.get("/experiments", response_model=list[Experiment])
async def list_experiments(
    em: ExperimentManager = Depends(get_experiment_manager),
) -> list[Experiment]:
    """Return all existing experiments."""
    logger.debug("Received experiment list request")
    return await em.get_all()


@server.get("/experiments/{eid}", response_model=Experiment)
async def get_experiment(
    eid: int,
    em: ExperimentManager = Depends(get_experiment_manager),
) -> Experiment:
    """Return a single experiment by ID."""
    logger.debug("Received get request for experiment %d", eid)
    try:
        return await em.get_experiment(eid)
    except NoSuchExperimentError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.get("/experiments/{eid}/archive")
async def get_experiment_archive(
    eid: int,
    em: ExperimentManager = Depends(get_experiment_manager),
) -> FileResponse:
    """Return the experiment's original archive as a ZIP file."""
    try:
        _ = await em.get_experiment(eid)
    except NoSuchExperimentError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
    path = em.get_experiment_archive(eid)
    return FileResponse(path=path, media_type="application/zip", filename=path.name)


@server.delete("/experiments/{eid}")
async def delete_experiment(
    eid: int,
    em: ExperimentManager = Depends(get_experiment_manager),
) -> Response:
    """Delete an experiment and all its associated data.

    Returns ``204 No Content`` on success. Returns ``404`` if no experiment with that ID
    exists. Returns ``500`` if deleting the experiment data directory fails.
    """
    logger.debug("Received delete request for experiment %d", eid)
    try:
        await em.delete(eid=eid)
        return Response(status_code=HTTPStatus.NO_CONTENT)
    except NoSuchExperimentError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
    except OSError as err:
        logger.exception("I/O error deleting experiment %d", eid)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail=str(err)
        ) from err


@server.get("/runs/dispatch", response_model=None)
async def dispatch_run(
    wid: int,
    em: ExperimentManager = Depends(get_experiment_manager),
    wm: WorkerManager = Depends(get_worker_manager),
) -> Response:
    """Claim and return the next waiting run for a worker to execute.

    Returns ``204 No Content`` if no runs are currently waiting. On success, returns the
    experiment archive as ``application/zip`` with the serialised ``Run`` object in the
    ``X-Run`` response header. Returns ``404`` if the worker ID is not registered.
    """
    try:
        pending = await em.dispatch_waiting_run()
        if pending is None:
            return Response(status_code=HTTPStatus.NO_CONTENT)

        async with pending:
            await wm.assign_run(wid=wid, run_id=pending.run.run_id)
            archive_path = pending.finalise(wid=wid)
            return FileResponse(
                path=archive_path,
                status_code=HTTPStatus.OK,
                media_type="application/zip",
                headers={"X-Run": pending.run.model_dump_json()},
            )
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.post("/runs/result")
async def run_results(
    wid: int,
    run: str = Form(...),
    results_archive: UploadFile = File(...),
    em: ExperimentManager = Depends(get_experiment_manager),
    wm: WorkerManager = Depends(get_worker_manager),
) -> Response:
    """Accept a completed run's results from a worker.

    Expects the finished ``Run`` object as a JSON-encoded form field and the results as a
    ZIP archive. Marks the worker as idle and persists the results. Returns ``404`` if the
    worker, experiment, or run is not found, and ``422`` if the run JSON is malformed.
    """
    try:
        run_data = Run.model_validate_json(run)

        await wm.unassign_run(wid=wid, run_id=run_data.run_id, state=WorkerState.IDLE)
        await em.submit_run_results(run=run_data, results_archive=results_archive.file)

        return Response(status_code=HTTPStatus.OK)
    except (NoSuchWorkerError, NoSuchExperimentError, NoSuchRunError) as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
    except ValidationError as err:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(err)
        ) from err


@server.post("/runs/error")
async def run_error(
    wid: int,
    run: str = Form(...),
    em: ExperimentManager = Depends(get_experiment_manager),
    wm: WorkerManager = Depends(get_worker_manager),
) -> Response:
    """Accept a failed run's error report from a worker.

    Expects the failed ``Run`` object as a JSON-encoded form field. Used when the worker
    encounters an error before any results could be collected (e.g. archive validation
    failure). Marks the worker as idle and records the failure. Returns ``404`` if the
    worker, experiment, or run is not found, and ``422`` if the run JSON is malformed.
    """
    try:
        run_data = Run.model_validate_json(run)

        await wm.unassign_run(wid=wid, run_id=run_data.run_id, state=WorkerState.IDLE)
        await em.record_run_failure(run=run_data)

        return Response(status_code=HTTPStatus.OK)
    except (NoSuchWorkerError, NoSuchExperimentError, NoSuchRunError) as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
    except ValidationError as err:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(err)
        ) from err


@server.get("/runs/{eid}/{index}/{iteration}", response_model=Run)
async def get_run(
    eid: int,
    index: int,
    iteration: int,
    em: ExperimentManager = Depends(get_experiment_manager),
) -> Run:
    """Return a single run by its composite identity (experiment ID + index)."""
    try:
        return await em.get_run(rid=RunId(eid=eid, index=index, iteration=iteration))
    except NoSuchRunError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.get("/runs/{eid}/{index}/{iteration}/results")
async def get_run_results(
    eid: int,
    index: int,
    iteration: int,
    em: ExperimentManager = Depends(get_experiment_manager),
) -> FileResponse:
    """Download the results archive for a finished Run."""
    rid = RunId(eid=eid, index=index, iteration=iteration)
    try:
        path = await em.get_run_results(rid=rid)
    except FileNotFoundError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
    return FileResponse(path=path, media_type="application/zip", filename=path.name)


@server.post("/runs/{eid}/{index}/{iteration}/reset", response_model=Run)
async def reset_run(
    eid: int,
    index: int,
    iteration: int,
    em: ExperimentManager = Depends(get_experiment_manager),
) -> Run:
    """Reset a run: delete its results and return it to WAITING state."""
    rid = RunId(eid=eid, index=index, iteration=iteration)
    try:
        await em.reset_run(rid=rid)
        return await em.get_run(rid=rid)
    except (NoSuchExperimentError, NoSuchRunError) as err:
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


@server.get("/workers/{wid}", response_model=WorkerData)
async def get_worker(
    wid: int,
    wm: WorkerManager = Depends(get_worker_manager),
) -> WorkerData:
    """Return a single worker by ID."""
    logger.debug("Received worker get request for id %d", wid)
    try:
        return await wm.get(wid=wid)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.post("/workers/check_in/{wid}")
async def check_in_worker(
    wid: int, wm: WorkerManager = Depends(get_worker_manager)
) -> Response:
    """Record a worker heartbeat to confirm it is still alive."""
    logger.debug("Received worker checkin for id %d", wid)
    try:
        await wm.check_in(wid=wid)
        return Response(status_code=HTTPStatus.OK)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.post("/workers/revive/{wid}", response_model=WorkerData)
async def revive_worker(
    wid: int, wm: WorkerManager = Depends(get_worker_manager)
) -> WorkerData:
    """Revive a dead worker, allowing it to reconnect and receive new work."""
    logger.debug("Received worker revive request for id %d", wid)
    try:
        return await wm.revive(wid=wid)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
    except WorkerNotDeadError as err:
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail=str(err)) from err


@cli.command()
def main(
    host: str = Option(
        "127.0.0.1",
        "--host",
        envvar=HOST_ENV,
        help=f"Bind host. Falls back to ${HOST_ENV}.",
    ),
    port: int = Option(
        8000,
        "--port",
        envvar=PORT_ENV,
        help=f"Bind port. Falls back to ${PORT_ENV}.",
    ),
    verbose: bool = Option(False, "-v", "--verbose", help="Enable debug logging"),
    data_path: Path | None = Option(
        None,
        "--data-path",
        "-d",
        envvar=DATA_PATH_ENV,
        help=f"Directory for database and blob storage. Falls back to ${DATA_PATH_ENV}, then platform default.",
    ),
) -> None:
    """Start the server."""
    if data_path is not None:
        os.environ[DATA_PATH_ENV] = str(data_path)

    log_level = logging.DEBUG if verbose else logging.INFO
    configure_logging(log_level)
    uvicorn.run(
        "mprun.server:server",
        host=host,
        port=port,
        log_level=logging.getLevelName(log_level).lower(),
        log_config=None,
    )


if __name__ == "__main__":
    cli()
