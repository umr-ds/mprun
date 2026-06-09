#! /usr/bin/env python3

"""Worker daemon."""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import logging
from asyncio import Task, create_task, sleep, to_thread, wait_for
from http import HTTPStatus
from os import environ
from pathlib import Path
from shutil import copy, copytree, rmtree, unpack_archive
from tempfile import TemporaryDirectory
from time import time
from typing import BinaryIO
from zipfile import ZIP_LZMA, BadZipFile, ZipFile

from httpx import AsyncClient, HTTPStatusError
from typer import Exit, Option, Typer

from mprun import SERVER_ADDRESS_ENV
from mprun.custom_types import ActiveState, FailureReason, SuccessState
from mprun.errors import ArchiveValidationError, NoRunError
from mprun.log import configure_logging
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    Run,
    WorkerData,
    add_path_to_archive,
)

logger = logging.getLogger(__name__)
cli = Typer()

WORKER_NAME_ENV = "MPRUN_WORKER_NAME"
WORKER_HOME_DIR = "MPRUN_WORKER_DIRECTORY"
RESULTS_ARCHIVE_NAME = "results.zip"
SLEEP_TIME = 60


class Worker:
    """Worker daemon that polls the server for runs, executes them, and uploads their results.

    Attributes:
        http_client (AsyncClient): HTTP client configured with the server's base URL.
        meta_data (WorkerData): Worker metadata returned by the server on registration.
        working (Run | None): The run currently being executed, or ``None`` if idle.
        home_dir (Path): Root directory for worker-local data (archives, results).
        execution_dir (Path): Working directory used during run execution; cleared after each run.
        archive_path (Path): Destination path for the current experiment's ZIP archive.
        _runner_task (Task): Background asyncio task running ``executor_loop``.
    """

    http_client: AsyncClient
    meta_data: WorkerData
    working: Run | None
    home_dir: Path
    execution_dir: Path
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
        self.execution_dir = self.home_dir / "exec"
        self.execution_dir.mkdir(parents=True, exist_ok=True)
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

    @classmethod
    async def init(cls, server_address: str, name: str, home_directory: Path) -> Worker:
        """Create and register a new Worker with the server.

        Args:
            server_address (str): Base URL of the server. An ``http://`` prefix is added if
                absent.
            name (str): Human-readable worker name passed to the server on registration.
            home_directory (Path): Root directory for worker-local storage.

        Returns:
            Worker: Fully initialised worker, ready to call ``run``.

        Raises:
            HTTPStatusError: If registration with the server fails.
        """
        logger.info("Initialising worker")
        if not server_address.startswith("http://"):
            server_address = f"http://{server_address}"

        client = AsyncClient(base_url=server_address)
        meta_data = await Worker.register(client=client, name=name)

        return cls(http_client=client, meta_data=meta_data, home_dir=home_directory)

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

                failure = await self.execute_run()
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
                await self.collect_results()
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
                    await self.cleanup()

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

    async def execute_run(self) -> FailureReason | None:
        """Execute the current run.

        Runs the setup executable (if configured), then the main executable. Environment files
        are copied and environment variables applied via ``prepare_run_environment`` before
        either executable starts. Each executable's stdout and stderr are captured to files in
        ``execution_dir``. If a timeout is configured, the process is killed on expiry.

        Returns:
            FailureReason | None: If the execution failed, the reason for the failure, otherwise None.

        Raises:
            NoRunError: If called when no run is assigned (``self.working`` is ``None``).
        """
        if self.working is None:
            raise NoRunError

        self.working.active_state = ActiveState.RUNNING

        logger.info("Executing Run %s", self.working.run_id)
        env = await self.prepare_run_environment()

        if self.working.definition.setup_executable is not None:
            logger.debug("Running setup executable")
            setup_stdout_path = self.execution_dir / "stdout.setup"
            setup_stderr_path = self.execution_dir / "stderr.setup"
            program = self.execution_dir / self.working.definition.setup_executable
            with (
                setup_stdout_path.open("wb") as setup_stdout_file,
                setup_stderr_path.open("wb") as setup_stderr_file,
            ):
                failure = await self.execute(
                    args=[program],
                    stdout=setup_stdout_file,
                    stderr=setup_stderr_file,
                    env=env,
                )
                if failure is not None:
                    return failure

        stdout_path = self.execution_dir / "stdout"
        stderr_path = self.execution_dir / "stderr"
        args = [
            self.execution_dir / self.working.definition.executable,
            *self.working.assemble_args(),
        ]
        logger.debug("Running main executable")
        with (
            stdout_path.open("wb") as stdout_file,
            stderr_path.open("wb") as stderr_file,
        ):
            return await self.execute(
                args=args, stdout=stdout_file, stderr=stderr_file, env=env
            )

    async def execute(
        self,
        args: list[Path | str],
        stdout: BinaryIO,
        stderr: BinaryIO,
        env: dict[str, str],
    ) -> FailureReason | None:
        """Execute single executable.

        Args:
            args (list[Path | str): Path to executable + list of arguments in ``--arg value`` form.
            stdout (BinaryIO): Oen file to write executable's stdout to.
            stderr (BinaryIO): Oen file to write executable's stderr to.
            env (dict[str, str]): Environment variables for executable.

        Returns:
            FailureReason | None: If the execution failed, the reason for the failure, otherwise None.
        """
        if self.working is None:
            raise NoRunError

        logger.debug("Executing: %s", args)

        process = await asyncio.subprocess.create_subprocess_exec(
            *args,
            shell=False,
            stdout=stdout,
            stderr=stderr,
            cwd=self.execution_dir,
            env=env,
        )
        if self.working.definition.timeout:
            try:
                await wait_for(process.wait(), self.working.definition.timeout)
            except TimeoutError:
                process.kill()
                await process.wait()
                return FailureReason.TIMEOUT
        else:
            await process.wait()
        if process.returncode == 0:
            return None
        return FailureReason.RETURN

    async def prepare_run_environment(self) -> dict[str, str]:
        """Unpack the experiment archive and build the process environment.

        Extracts the archive into ``execution_dir``, ensures executables have the execute bit
        set, and merges any ``environment_variables`` from the definition into a copy of the
        current process environment. Environment files are copied to their configured
        destinations on the local filesystem.

        Returns:
            dict[str, str]: Full environment mapping to pass to the subprocess.

        Raises:
            NoRunError: If called when no run is assigned (``self.working`` is ``None``).
        """
        if self.working is None:
            raise NoRunError

        logger.debug("Preparing for Run.")
        await to_thread(
            unpack_archive,
            filename=self.archive_path,
            extract_dir=self.execution_dir,
            format="zip",
        )

        # Set execute permissions for executables
        if self.working.definition.setup_executable:
            setup_script = self.execution_dir / self.working.definition.setup_executable
            await to_thread(setup_script.chmod, setup_script.stat().st_mode | 0o111)
        main_script = self.execution_dir / self.working.definition.executable
        await to_thread(main_script.chmod, main_script.stat().st_mode | 0o111)

        env = environ.copy()
        if self.working.definition.environment_variables is not None:
            env.update(self.working.definition.environment_variables)

        if self.working.definition.environment_files is not None:
            logger.debug("Copying environment files to destinations")
            for (
                env_file,
                destination,
            ) in self.working.definition.environment_files.items():
                source = self.execution_dir / env_file
                logger.debug("Copying %s to %s", source, destination)
                if source.is_file():
                    await to_thread(copy, source, destination)
                elif source.is_dir():
                    await to_thread(copytree, source, destination, dirs_exist_ok=True)

        return env

    async def collect_results(self) -> None:
        """Package run outputs and result files into a ZIP archive at ``home_dir/results.zip``.

        Always includes captured stdout/stderr (and their setup equivalents if present). Then
        adds every file or directory listed in the run's ``results`` definition.

        Raises:
            NoRunError: If called when no run is assigned (``self.working`` is ``None``).
        """
        if self.working is None:
            raise NoRunError

        logger.info("Collecting Run results")

        archive_path = self.home_dir / RESULTS_ARCHIVE_NAME
        with ZipFile(
            archive_path, mode="w", compression=ZIP_LZMA, allowZip64=True
        ) as zf:
            setup_stdout_path = self.execution_dir / "stdout.setup"
            if await to_thread(setup_stdout_path.is_file):
                await to_thread(zf.write, setup_stdout_path, "stdout.setup")
            setup_stderr_path = self.execution_dir / "stderr.setup"
            if await to_thread(setup_stderr_path.is_file):
                await to_thread(zf.write, setup_stderr_path, "stderr.setup")
            stdout_path = self.execution_dir / "stdout"
            if await to_thread(stdout_path.is_file):
                await to_thread(zf.write, stdout_path, "stdout")
            stderr_path = self.execution_dir / "stderr"
            if await to_thread(stderr_path.is_file):
                await to_thread(zf.write, stderr_path, "stderr")

            for result_local, result_archive in self.working.definition.results.items():
                # Use absolute path for name_local
                name_local = Path(result_local)
                if not name_local.is_absolute():
                    name_local = self.execution_dir / name_local
                await Worker.add_result(
                    zf=zf,
                    name_local=name_local,
                    name_archive=result_archive,
                )

    async def cleanup(self) -> None:
        """Clean up execution directory."""
        logger.info("Cleaning up execution directory")
        await to_thread(rmtree, self.execution_dir)
        await to_thread(self.execution_dir.mkdir)

    @staticmethod
    async def add_result(zf: ZipFile, name_local: Path, name_archive: str) -> None:
        """Add a file or directory to an open ZIP archive.

        If ``name_local`` is a file, it is added directly. If it is a directory, all files
        within it are added recursively, preserving relative paths under ``name_archive``.
        Paths that do not exist are silently skipped.

        Args:
            zf (ZipFile): Open, writable ZIP archive to add the result to.
            name_local (Path): Filesystem path of the file or directory to add.
            name_archive (str): Path to use as the entry name inside the archive.
        """
        logger.debug("Adding result %s as %s", name_local, name_archive)
        await to_thread(
            add_path_to_archive, zf=zf, name_local=name_local, name_archive=name_archive
        )

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


async def _run(server_address: str, name: str, home_directory: Path) -> None:
    try:
        worker = await Worker.init(
            server_address=server_address, name=name, home_directory=home_directory
        )
    except HTTPStatusError as err:
        logger.critical("Worker registration failed: %s", err, exc_info=True)
        raise Exit(1) from err
    await worker.run()


@cli.command()
def main(
    verbose: bool = Option(False, "-v", "--verbose", help="Enable debug logging"),
    server_address: str | None = Option(
        None,
        "--server-address",
        "-s",
        envvar=SERVER_ADDRESS_ENV,
        help=f"Server address. Falls back to ${SERVER_ADDRESS_ENV}.",
    ),
    name: str | None = Option(
        None,
        "--name",
        "-n",
        envvar=WORKER_NAME_ENV,
        help=f"Worker name. Falls back to ${WORKER_NAME_ENV}.",
    ),
    home_directory: Path | None = Option(
        None,
        "--home-directory",
        "-d",
        envvar=WORKER_HOME_DIR,
        help=f"Worker home directory. Falls back to ${WORKER_HOME_DIR}.",
    ),
) -> None:
    """Start the worker daemon.

    CLI arguments take precedence over environment variables (``MPRUN_SERVER_ADDRESS``,
    ``MPRUN_WORKER_NAME``, ``MPRUN_WORKER_DIRECTORY``).
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    configure_logging(log_level)

    if server_address is None:
        logger.critical(
            "Server address not set. Use --server-address or $%s.", SERVER_ADDRESS_ENV
        )
        raise Exit(1)

    if name is None:
        logger.critical("Worker name not set. Use --name or $%s.", WORKER_NAME_ENV)
        raise Exit(1)

    if home_directory is None:
        logger.critical(
            "Home directory not set. Use --home-directory or $%s.", WORKER_HOME_DIR
        )
        raise Exit(1)

    asyncio.run(
        _run(server_address=server_address, name=name, home_directory=home_directory)
    )


if __name__ == "__main__":
    main()
