#! /usr/bin/env python3

"""Module contains server application."""

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

from mprun.errors import (
    ArchiveValidationError,
    InvalidParametersError,
    NoSuchExperimentError,
    NoSuchRunError,
    NoSuchWorkerError,
)
from mprun.experiment_manager import ExperimentManager
from mprun.models import Experiment, ExperimentDefinition, Run, WorkerData, WorkerState
from mprun.worker_manager import WorkerManager

logger = logging.getLogger(__name__)
DATA_PATH_ENV = "MPRUN_DATA_PATH"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI voodoo to integrate state."""
    data_path_str = os.getenv(DATA_PATH_ENV)
    data_path = Path.home() / ".mprun" if data_path_str is None else Path(data_path_str)

    logger.info(f"Starting server in {data_path}")
    app.state.experiment_manager = ExperimentManager(data_path=data_path)
    app.state.worker_manager = WorkerManager()
    try:
        yield
    finally:
        app.state.experiment_manager.close()


server = FastAPI(
    title="mprun",
    version="0.0.1",
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
    """Create a new experiment from multipart form (ExperimentDefinition JSON + archive) and return it."""
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
    logger.debug("Received experiment get request")
    try:
        return await em.get_experiment(eid)
    except NoSuchExperimentError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.get("/runs/dispatch", response_model=None)
async def dispatch_run(
    wid: int,
    em: ExperimentManager = Depends(get_experiment_manager),
    wm: WorkerManager = Depends(get_worker_manager),
) -> Response:
    """Workers query this endpoint to get a run to execute.

    Returns the Experiment archive as the response body (application/zip) with the
    Run object serialised as JSON in the `X-Run` header.
    """
    try:
        pending = await em.dispatch_waiting_run()
        if pending is None:
            return Response(status_code=HTTPStatus.NO_CONTENT)

        async with pending:
            await wm.assign_run(wid=wid, rid=pending.run.rid)
            archive_path = pending.finalise(wid=wid)
            return FileResponse(
                path=archive_path,
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
    """Endpoint for workers to submit run results."""
    try:
        run_data = Run.model_validate_json(run)

        await wm.unassign_run(wid=wid, rid=run_data.rid, state=WorkerState.IDLE)
        await em.submit_run_results(run=run_data, results_archive=results_archive.file)

        return Response(status_code=HTTPStatus.OK)
    except (NoSuchWorkerError, NoSuchExperimentError, NoSuchRunError) as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err
    except ValidationError as err:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(err)
        ) from err


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
    logger.debug(f"Received worker get request for id {wid}")
    try:
        return await wm.get(wid=wid)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@server.post("/workers/check_in/{wid}")
async def check_in_worker(
    wid: int, wm: WorkerManager = Depends(get_worker_manager)
) -> Response:
    """Endpoint to perform worker checkin."""
    logger.debug(f"Received worker checkin for id {wid}")
    try:
        await wm.check_in(wid=wid)
        return Response(status_code=HTTPStatus.OK)
    except NoSuchWorkerError as err:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=str(err)) from err


@cli.command()
def main(
    host: str = Option("127.0.0.1", help="Bind host"),
    port: int = Option(8000, help="Bind port"),
    verbose: bool = Option(False, "-v", "--verbose", help="Enable debug logging"),
) -> None:
    """Start the mprun server."""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=log_level)
    uvicorn.run(
        "mprun.server:server",
        host=host,
        port=port,
        log_level=logging.getLevelName(log_level).lower(),
    )


if __name__ == "__main__":
    cli()
