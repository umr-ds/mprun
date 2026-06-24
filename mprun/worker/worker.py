#! /usr/bin/env python3

"""Worker daemon."""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import logging
from asyncio import Task, create_task, sleep, to_thread
from http import HTTPStatus
from pathlib import Path
from shutil import copy
from tempfile import TemporaryDirectory
from time import time
from zipfile import BadZipFile

from httpx import AsyncClient, HTTPStatusError
from pydantic import ValidationError
from typer import Exit, Option, Typer

from mprun.custom_types import ActiveState, SuccessState, WorkerBackend
from mprun.errors import (
    ArchiveValidationError,
    InconsistentConfigurationError,
    RunFailureError,
)
from mprun.log import configure_logging
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    Run,
    WorkerData,
    WorkerRegistration,
)
from mprun.worker.backends import Backend, NativeBackend
from mprun.worker.config import (
    WorkerConfig,
    load_worker_config,
    resolve_worker_config_path,
)

logger = logging.getLogger(__name__)
cli = Typer()

REGISTRATION_FILE_NAME = "registration.json"
METADATA_FILE_NAME = "metadata.json"
RESULTS_ARCHIVE_NAME = "results.zip"
SLEEP_TIME = 60


class Worker:
    """Worker daemon that polls the server for runs, executes them, and uploads their results.

    Attributes:
        config (WorkerConfig): Worker configuration.
        http_client (AsyncClient): HTTP client configured with the server's base URL.
        meta_data (WorkerData): Worker metadata returned by the server on registration.
        _runner_task (Task): Background asyncio task running ``executor_loop``.
    """

    config: WorkerConfig
    http_client: AsyncClient
    meta_data: WorkerData

    _runner_task: Task[None]

    def __init__(
        self, config: WorkerConfig, http_client: AsyncClient, meta_data: WorkerData
    ) -> None:
        """Initialise the Worker.

        Args:
            config (WorkerConfig): Worker configuration.
            http_client (AsyncClient): Configured HTTP client pointing to the server's base URL.
            meta_data (WorkerData): Worker metadata obtained from the server during registration.
        """
        self.config = config
        self.http_client = http_client
        self.meta_data = meta_data

        self.config.home_directory.mkdir(parents=True, exist_ok=True)

    @property
    def experiment_archive_path(self) -> Path:
        """Path to the Experiment's archive."""
        return self.config.home_directory / EXPERIMENT_ARCHIVE_NAME

    @property
    def results_archive_path(self) -> Path:
        """Path ro the Run's results archive."""
        return self.config.home_directory / RESULTS_ARCHIVE_NAME

    @staticmethod
    async def register(
        client: AsyncClient, registration_data: WorkerRegistration
    ) -> WorkerData:
        """Register this worker with the server.

        Args:
            client (AsyncClient): HTTP client configured with the server's base URL.
            registration_data (WorkerRegistration): Data provided by the worker for registration.

        Returns:
            WorkerData: Worker metadata returned by the server on successful registration.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
        """
        logger.info("Registering with server")
        response = await client.post(
            "/workers",
            data={"registration": registration_data.model_dump_json()},
        )
        response.raise_for_status()

        worker_data = WorkerData.model_validate(response.json())
        logger.info("Registered successfully with ID: %d", worker_data.wid)

        return worker_data

    @staticmethod
    async def revive(client: AsyncClient, wid: int) -> WorkerData | None:
        """Revive worker.

        Useful if Worker has been restarted, or had connectivity issues, and you don't want it to re-register as a new worker.

        Args:
            client (AsyncClient): HTTP client configured with the server's base URL.
            wid (int): Worker's ID.

        Returns:
            WorkerData: Worker metadata returned by the server on successful revive.
                None if the server responds with status 409 (this worker has not been marked as dead yet).

        Raises:
            HTTPStatusError: If the server returns a non-2xx response (except 409).
        """
        logger.info("Attempting to revive")
        response = await client.post(f"/workers/revive/{wid}")
        if response.status_code == HTTPStatus.CONFLICT:
            logger.info(
                "Server responded with status 409: We were not marked as dead yet"
            )
            return None
        response.raise_for_status()

        worker_data = WorkerData.model_validate(response.json())
        logger.info("Successful revive")

        return worker_data

    @classmethod
    async def init(cls, config: WorkerConfig) -> Worker:
        """Create and register a new Worker with the server.

        Args:
            config (WorkerConfig): Worker configuration.

        Returns:
            Worker: Fully initialised worker, ready to call ``run``.

        Raises:
            HTTPStatusError: If registration with the server fails.
            InconsistentConfigurationError: If the saved registered name differs from the configured name.
            NoSavedMetadataError: When there's no saved metadata to read.
        """
        logger.info("Initialising worker")

        client = AsyncClient(base_url=config.server_address)

        config.home_directory.mkdir(parents=True, exist_ok=True)
        meta_data_path = config.home_directory / METADATA_FILE_NAME
        meta_data: WorkerData

        if not await to_thread(
            meta_data_path.is_file
        ):  # no saved registration data available -> register new worker
            meta_data = await Worker.register(
                client=client, registration_data=config.registration_data
            )
            with meta_data_path.open("w") as f:
                await to_thread(f.write, meta_data.model_dump_json())
            return cls(http_client=client, meta_data=meta_data, config=config)

        with meta_data_path.open("rb") as f:
            data = await to_thread(f.read)
            meta_data = WorkerData.model_validate_json(json_data=data)

        if meta_data.registration_data.name != config.name:
            raise InconsistentConfigurationError(
                name="name", expected=meta_data.registration_data.name, got=config.name
            )

        w_dat = await Worker.revive(client=client, wid=meta_data.wid)
        if w_dat is not None:
            meta_data = w_dat
            with meta_data_path.open("w") as f:
                await to_thread(f.write, meta_data.model_dump_json())

        return cls(http_client=client, meta_data=meta_data, config=config)

    async def executor_loop(self) -> None:
        """Poll the server for work and execute runs.

        Wakes every ``SLEEP_TIME`` seconds, queries the server for a waiting run.
        If one is available, executes it, collects results, uploads them, and cleans up before
        sleeping again.
        If errors occur with Run execution, these are communicated to the server.
        """
        logger.info("Starting worker executor loop")
        while True:
            try:
                run = await self._fetch_work()
                if run is None:
                    logger.info("No work to get. Sleeping")
                    await sleep(SLEEP_TIME)
                    continue
                await self._process_run(run)
            except Exception:
                logger.exception("Unhandled error in executor loop, sleeping")
                await sleep(SLEEP_TIME)

    async def _fetch_work(self) -> Run | None:
        """Fetch a waiting run from the server, or ``None`` if none available.

        Returns:
            The dispatched run, or ``None`` if nothing is waiting or a recoverable
            error occurred.
        """
        try:
            return await self.get_work()
        except HTTPStatusError:
            logger.exception("Error performing operation with server")
        except RunFailureError as err:
            logger.exception("Error validating/saving Run")
            try:
                await self.report_error(failure=err)
            except Exception:
                logger.exception("Failed reporting error to server")
        except Exception:
            logger.exception("Unexpected error getting work")
        return None

    async def _process_run(self, run: Run) -> None:
        """Execute, collect, and upload a single run.

        Args:
            run: The run to process.
        """
        with TemporaryDirectory(delete=True) as tmp_dir:
            execution_dir = Path(tmp_dir)
            backend: Backend
            match self.meta_data.registration_data.backend:
                case WorkerBackend.NATIVE:
                    backend = NativeBackend(
                        run=run,
                        experiment_archive_path=self.experiment_archive_path,
                        results_archive_path=self.results_archive_path,
                        execution_dir=execution_dir,
                    )

            try:
                await backend.prepare_run_environment()
            except RunFailureError as err:
                try:
                    await self.report_error(failure=err)
                except Exception:
                    logger.exception("Failed reporting error to server")
                return

            try:
                await backend.execute_run()
                logger.info("Finished execution")
                run.success_state = SuccessState.SUCCESS
                run.failure_reason = None
            except RunFailureError as err:
                logger.exception("Run failed execution")
                run.success_state = SuccessState.FAILED
                run.failure_reason = str(err)

            run.active_state = ActiveState.FINISHED
            run.finished_running = time()

            logger.info("Updating Run state")
            try:
                await backend.collect_results()
            except RunFailureError as err:
                logger.exception("Failed collecting results")
                run.success_state = SuccessState.FAILED
                run.failure_reason = f"{run.failure_reason or ''}; collect: {err}"
                try:
                    await self.report_error(
                        failure=RunFailureError(run=run, reason=err)
                    )
                except Exception:
                    logger.exception("Failed reporting error to server")
                return
            except Exception:
                logger.exception("Unexpected error collecting results")
                run.success_state = SuccessState.FAILED
                return

            try:
                await self.upload_results(run=run)
            except (HTTPStatusError, OSError):
                logger.exception("Failed uploading results")
            except Exception:
                logger.exception("Unexpected error uploading results")

    async def get_work(self) -> Run | None:
        """Query the server for a waiting run.

        Streams the experiment archive from the server into a temporary directory, validates it
        against the run's ``ExperimentDefinition``, and copies it to ``self.archive_path``. The
        archive persists there until the next dispatch overwrites it.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
            RunFailure: If we are unable to verify & store the Run.
        """
        logger.debug("Have no work to do, asking the server...")
        async with self.http_client.stream(
            "GET", "/runs/dispatch", params={"wid": self.meta_data.wid}
        ) as response:
            response.raise_for_status()

            if response.status_code == HTTPStatus.NO_CONTENT:
                logger.debug("Server has no work for us.")
                return None

            try:
                run = Run.model_validate_json(response.headers["X-Run"], strict=True)
            except ValidationError:
                logger.exception("Run model validation failed")
                return None  # We can't really tell the server which run failed validation if we can't get its ID...

            logger.debug("Received run: %s", run.run_id)

            with TemporaryDirectory(delete=True) as archive_dir:
                logger.debug("Saving Experiment archive")
                archive_path = Path(archive_dir) / EXPERIMENT_ARCHIVE_NAME
                try:
                    with archive_path.open("wb") as f:
                        async for chunk in response.aiter_bytes():
                            await to_thread(f.write, chunk)
                except OSError as err:
                    logger.exception("Failed storing archive on disk")
                    raise RunFailureError(run=run, reason=err) from err

                logger.debug("Validating Experiment archive")
                try:
                    await to_thread(
                        run.definition.validate_archive, archive_path=archive_path
                    )
                except (ArchiveValidationError, BadZipFile) as err:
                    logger.exception("Archive validation failed")
                    raise RunFailureError(run=run, reason=err) from err

                logger.debug("Archive validated successfully, saving it for execution")

                try:
                    await to_thread(copy, archive_path, self.experiment_archive_path)
                except OSError as err:
                    logger.exception("Failed copying archive")
                    raise RunFailureError(run=run, reason=err) from err

        return run

    async def report_error(self, failure: RunFailureError) -> None:
        """Report a run error to the server without a results archive.

        Used when a run fails before any results could be collected (e.g. archive
        validation failure).

        Args:
            failure (RunFailureError): Exception generated by failure.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
        """
        failure.run.active_state = ActiveState.FINISHED
        failure.run.success_state = SuccessState.FAILED
        failure.run.failure_reason = str(failure.reason)
        failure.run.finished_running = time()

        logger.info(
            "Reporting error for run %s: %s",
            failure.run.run_id,
            failure.run.failure_reason,
        )
        response = await self.http_client.post(
            "/runs/error",
            params={"wid": self.meta_data.wid},
            data={"run": failure.run.model_dump_json()},
        )
        response.raise_for_status()
        logger.debug("Error reported successfully")

    async def upload_results(self, run: Run) -> None:
        """Upload the results archive for the current run to the server.

        Args:
            run (Run): Finished Run.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
        """
        logger.info("Uploading results for run %s", run.run_id)
        with self.results_archive_path.open("rb") as f:
            response = await self.http_client.post(
                "/runs/result",
                params={"wid": self.meta_data.wid},
                data={"run": run.model_dump_json()},
                files={"results_archive": (RESULTS_ARCHIVE_NAME, f, "application/zip")},
            )
        response.raise_for_status()
        logger.debug("Results uploaded successfully")

    async def run(self) -> None:
        """Start the worker's main loop.

        Spawns ``executor_loop`` as a background task, then sends a heartbeat to the server
        every ``SLEEP_TIME`` seconds. HTTP errors during check-in are logged and skipped.
        Runs indefinitely.
        """
        logger.info("Starting worker main loop")
        self._runner_task = create_task(self.executor_loop())

        while True:
            try:
                await self.check_in()
            except Exception:
                logger.exception("Error performing checkin with server")
            await sleep(SLEEP_TIME)

    async def check_in(self) -> None:
        """Send a heartbeat to the server and update ``last_check_in``.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
            httpx.RequestError: If a network or transport error occurs.
        """
        logger.debug("Performing worker check in")
        response = await self.http_client.post(
            f"/workers/check_in/{self.meta_data.wid}"
        )
        response.raise_for_status()
        self.meta_data.last_check_in = time()


async def _run(config: WorkerConfig) -> None:
    try:
        worker = await Worker.init(config=config)
    except Exception as err:
        logger.critical("Worker initialisation failed: %s", err, exc_info=True)
        raise Exit(1) from err
    await worker.run()


@cli.command()
def main(
    config: Path | None = Option(
        None,
        "--config",
        "-c",
        help=(
            "Path to TOML config file. "
            "Defaults to user and site config dirs (see platformdirs)."
        ),
    ),
) -> None:
    """Start the worker daemon.

    All settings are read from a TOML config file. Use ``-c`` to provide
    a path; otherwise the default locations are checked.
    """
    cfg_path = resolve_worker_config_path(config)
    if cfg_path is None:
        logger.critical(
            "No config file found. "
            "Use --config or place a worker.toml in the default location."
        )
        raise Exit(1)

    cfg = load_worker_config(cfg_path)
    configure_logging(cfg.log_level)

    asyncio.run(_run(config=cfg))


if __name__ == "__main__":
    main()
