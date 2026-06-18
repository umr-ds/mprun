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
from typer import Exit, Option, Typer

from mprun.custom_types import ActiveState, FailureReason, SuccessState, WorkerBackend
from mprun.errors import (
    ArchiveValidationError,
    InconsistentConfigurationError,
    NoRunError,
    NoSavedMetadataError,
)
from mprun.log import configure_logging
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    Run,
    WorkerData,
)
from mprun.worker import RESULTS_ARCHIVE_NAME
from mprun.worker.backends import Backend, NativeBackend
from mprun.worker.config import (
    RegistrationData,
    WorkerConfig,
    load_worker_config,
    resolve_worker_config_path,
)

logger = logging.getLogger(__name__)
cli = Typer()

REGISTRATION_FILE_NAME = "registration.json"
METADATA_FILE_NAME = "metadata.json"
SLEEP_TIME = 60


class Worker:
    """Worker daemon that polls the server for runs, executes them, and uploads their results.

    Attributes:
        http_client (AsyncClient): HTTP client configured with the server's base URL.
        meta_data (WorkerData): Worker metadata returned by the server on registration.
        working (Run | None): The run currently being executed, or ``None`` if idle.
        home_dir (Path): Root directory for worker-local data (archives, results).
        archive_path (Path): Destination path for the current experiment's ZIP archive.
        _runner_task (Task): Background asyncio task running ``executor_loop``.
    """

    http_client: AsyncClient
    meta_data: WorkerData
    working: Run | None
    home_dir: Path
    archive_path: Path

    _runner_task: Task[None]

    def __init__(
        self, http_client: AsyncClient, meta_data: WorkerData, home_dir: Path
    ) -> None:
        """Initialise the Worker.

        Args:
            http_client (AsyncClient): Configured HTTP client pointing to the server's base URL.
            meta_data (WorkerData): Worker metadata obtained from the server during registration.
            home_dir (Path): Root directory for worker-local storage. Will be created if it does not exist.
        """
        self.http_client = http_client
        self.meta_data = meta_data
        self.working = None
        self.home_dir = home_dir
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.archive_path = self.home_dir / EXPERIMENT_ARCHIVE_NAME

    @staticmethod
    async def register(client: AsyncClient, name: str) -> WorkerData:
        """Register this worker with the server.

        Args:
            client (AsyncClient): HTTP client configured with the server's base URL.
            name (str): Human-readable name to register under.

        Returns:
            WorkerData: Worker metadata returned by the server on successful registration.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
        """
        logger.info("Registering with server")
        response = await client.post("/workers", params={"name": name})
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
    async def init(cls, server_address: str, name: str, config: WorkerConfig) -> Worker:
        """Create and register a new Worker with the server.

        Args:
            server_address (str): Base URL of the server. An ``http://`` prefix is added if
                absent.
            name (str): Human-readable worker name passed to the server on registration.
            config (WorkerConfig): Worker configuration.

        Returns:
            Worker: Fully initialised worker, ready to call ``run``.

        Raises:
            HTTPStatusError: If registration with the server fails.
            InconsistentConfigurationError: If the saved registered name differs from the configured name.
            NoSavedMetadataError: When there's no saved metadata to read.
        """
        logger.info("Initialising worker")

        client = AsyncClient(base_url=server_address)

        config.home_directory.mkdir(parents=True, exist_ok=True)
        registration_path = config.home_directory / REGISTRATION_FILE_NAME
        meta_data_path = config.home_directory / METADATA_FILE_NAME

        registration_data: RegistrationData

        if not await to_thread(
            registration_path.is_file
        ):  # no saved registration data available -> register new worker
            meta_data = await Worker.register(client=client, name=name)
            with meta_data_path.open("w") as f:
                await to_thread(f.write, meta_data.model_dump_json())
            registration_data = RegistrationData(name=config.name, wid=meta_data.wid)
            with registration_path.open("w") as f:
                await to_thread(f.write, registration_data.model_dump_json())
            return cls(
                http_client=client, meta_data=meta_data, home_dir=config.home_directory
            )

        with registration_path.open("rb") as f:
            data = await to_thread(f.read)
            registration_data = RegistrationData.model_validate_json(json_data=data)

        if registration_data.name != config.name:
            raise InconsistentConfigurationError(
                name="name", expected=registration_data.name, got=config.name
            )

        wdat = await Worker.revive(client=client, wid=registration_data.wid)
        if wdat is None:
            if not await to_thread(meta_data_path.is_file):
                raise NoSavedMetadataError
            with meta_data_path.open("rb") as f:
                data = await to_thread(f.read)
                meta_data = WorkerData.model_validate_json(json_data=data)
        else:
            meta_data = wdat
            with meta_data_path.open("w") as f:
                await to_thread(f.write, meta_data.model_dump_json())

        return cls(
            http_client=client, meta_data=meta_data, home_dir=config.home_directory
        )

    async def executor_loop(self) -> None:
        """Poll the server for work and execute runs.

        Wakes every ``SLEEP_TIME`` seconds. When idle, queries the server for a waiting run.
        If one is available, executes it, collects results, uploads them, and cleans up before
        sleeping again. HTTP errors during dispatch are logged and skipped.
        """
        logger.info("Starting worker executor loop")
        while True:
            try:
                await self.get_work()
                if self.working is None:
                    logger.info("No work to get. Sleeping")
                    await sleep(SLEEP_TIME)
                    continue

                with TemporaryDirectory(delete=True) as tmp_dir:
                    execution_dir = Path(tmp_dir)
                    backend: Backend
                    match self.meta_data.backend:
                        case WorkerBackend.NATIVE:
                            backend = NativeBackend(
                                run=self.working,
                                archive_path=self.archive_path,
                                home_dir=self.home_dir,
                                execution_dir=execution_dir,
                            )

                    failure = await backend.execute_run()
                    logger.info("Finished execution with failure state %s", failure)

                    if failure is None:
                        self.working.success_state = SuccessState.SUCCESS
                        self.working.failure_reason = None
                    else:
                        self.working.success_state = SuccessState.FAILED
                        self.working.failure_reason = failure
                    self.working.active_state = ActiveState.FINISHED
                    self.working.finished_running = time()

                    logger.info("Updating Run state")
                    await backend.collect_results()
                    await self.upload_results()
            except (ArchiveValidationError, BadZipFile):
                logger.exception("Archive validation failed")
                await self.report_error()
            except HTTPStatusError:
                logger.exception("Error performing operation with server")
            except OSError:
                logger.exception("Encountered OSError")
            except (
                Exception
            ) as err:  # Worker process should survive unexpected exceptions
                logger.critical(
                    "Encountered unexpected exception: %s", err, exc_info=True
                )
            finally:
                if self.working is not None:
                    self.working = None

    async def get_work(self) -> None:
        """Query the server for a waiting run.

        Streams the experiment archive from the server into a temporary directory, validates it
        against the run's ``ExperimentDefinition``, and copies it to ``self.archive_path``. The
        archive persists there until the next dispatch overwrites it.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
            ArchiveValidationError: If the received archive does not match the run's definition.
                The run is stored on ``self.working`` with failure state set before raising,
                so that the caller can report the error to the server.
            zipfile.BadZipFile: If the received archive is not a valid ZIP file.
                Same ``self.working`` semantics as ``ArchiveValidationError``.
            OSError: If writing the archive to disk fails.
        """
        logger.debug("Have no work to do, asking the server...")
        async with self.http_client.stream(
            "GET", "/runs/dispatch", params={"wid": self.meta_data.wid}
        ) as response:
            response.raise_for_status()

            if response.status_code == HTTPStatus.NO_CONTENT:
                logger.debug("Server has no work for us.")
                return

            run = Run.model_validate_json(response.headers["X-Run"], strict=True)
            logger.debug("Received run: %s", run.run_id)

            with TemporaryDirectory(delete=True) as archive_dir:
                logger.debug("Saving Experiment archive")
                archive_path = Path(archive_dir) / EXPERIMENT_ARCHIVE_NAME
                with archive_path.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        await to_thread(f.write, chunk)

                logger.debug("Validating Experiment archive")
                try:
                    await to_thread(
                        run.definition.validate_archive, archive_path=archive_path
                    )
                except (ArchiveValidationError, BadZipFile):
                    logger.exception("Archive validation failed")
                    run.active_state = ActiveState.FINISHED
                    run.success_state = SuccessState.FAILED
                    run.failure_reason = FailureReason.BAD_ARCHIVE
                    run.finished_running = time()
                    self.working = run
                    raise

                logger.debug("Archive validated successfully, saving it for execution")
                await to_thread(copy, archive_path, self.archive_path)

        self.working = run

    async def report_error(self) -> None:
        """Report a run error to the server without a results archive.

        Used when a run fails before any results could be collected (e.g. archive
        validation failure).

        Raises:
            NoRunError: If called when no run is assigned (``self.working`` is ``None``).
            HTTPStatusError: If the server returns a non-2xx response.
        """
        if self.working is None:
            raise NoRunError

        logger.info(
            "Reporting error for run %s: %s",
            self.working.run_id,
            self.working.failure_reason,
        )
        response = await self.http_client.post(
            "/runs/error",
            params={"wid": self.meta_data.wid},
            data={"run": self.working.model_dump_json()},
        )
        response.raise_for_status()
        logger.debug("Error reported successfully")

    async def upload_results(self) -> None:
        """Upload the results archive for the current run to the server.

        Raises:
            NoRunError: If called when no run is assigned (``self.working`` is ``None``).
            HTTPStatusError: If the server returns a non-2xx response.
        """
        if self.working is None:
            raise NoRunError

        archive_path = self.home_dir / RESULTS_ARCHIVE_NAME
        logger.info("Uploading results for run %s", self.working.run_id)
        with archive_path.open("rb") as f:
            response = await self.http_client.post(
                "/runs/result",
                params={"wid": self.meta_data.wid},
                data={"run": self.working.model_dump_json()},
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
            except HTTPStatusError:
                logger.exception("Error performing checkin with server")
            await sleep(SLEEP_TIME)

    async def check_in(self) -> None:
        """Send a heartbeat to the server and update ``last_check_in``.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
        """
        logger.debug("Performing worker check in")
        response = await self.http_client.post(
            f"/workers/check_in/{self.meta_data.wid}"
        )
        response.raise_for_status()
        self.meta_data.last_check_in = time()


async def _run(server_address: str, config: WorkerConfig) -> None:
    try:
        worker = await Worker.init(
            server_address=server_address, name=config.name, config=config
        )
    except HTTPStatusError as err:
        logger.critical("Worker registration failed: %s", err, exc_info=True)
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

    asyncio.run(
        _run(
            server_address=cfg.server_address,
            config=cfg,
        )
    )


if __name__ == "__main__":
    main()
