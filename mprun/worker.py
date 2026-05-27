#! /usr/bin/env python3

"""Worker daemon."""

from __future__ import annotations

import asyncio
import asyncio.subprocess
import logging
from asyncio import Lock, Task, create_task, sleep, to_thread, wait_for
from http import HTTPStatus
from os import environ, getenv
from pathlib import Path
from shutil import copy, copytree, rmtree, unpack_archive
from tempfile import TemporaryDirectory
from time import time
from zipfile import ZIP_LZMA, ZipFile

from httpx import AsyncClient, HTTPStatusError
from typer import Exit, Option, Typer

from mprun import SERVER_ADDRESS_ENV
from mprun.errors import NoRunError
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    Run,
    WorkerData,
)
from mprun.types import SuccessState

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
        _state_mutex (Lock): Guards ``working`` against concurrent access between the executor
            loop and the check-in loop.
        _runner_task (Task): Background asyncio task running ``executor_loop``.
    """

    http_client: AsyncClient
    meta_data: WorkerData
    working: Run | None
    home_dir: Path
    execution_dir: Path
    archive_path: Path

    _state_mutex: Lock
    _runner_task: Task

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
        self._state_mutex = Lock()

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
        logger.info(f"Registered successfully with ID: {worker_data.wid}")

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
            await sleep(SLEEP_TIME)

            async with self._state_mutex:
                if self.working is None:
                    try:
                        run = await self.get_work()
                        if run is None:
                            continue
                        self.working = run
                    except HTTPStatusError as err:
                        logger.error(
                            "Error performing checkin with server: %s",
                            err,
                            exc_info=True,
                        )
                        continue
                    state = await self.execute_run()
                    self.working.success_state = state
                    await self.collect_results()
                    await self.upload_results()
                    self.working = None
                    await self.cleanup()

    async def get_work(self) -> Run | None:
        """Query the server for a waiting run.

        Streams the experiment archive from the server into a temporary directory, validates it
        against the run's ``ExperimentDefinition``, and copies it to ``self.archive_path``. The
        archive persists there until the next dispatch overwrites it.

        Returns:
            Run | None: The run to execute, or ``None`` if the server has no work available.

        Raises:
            HTTPStatusError: If the server returns a non-2xx response.
            ArchiveValidationError: If the received archive does not match the run's definition.
            zipfile.BadZipFile: If the received archive is not a valid ZIP file.
            OSError: If writing the archive to disk fails.
        """
        logger.debug("Have no work to do, asking the server...")
        async with self.http_client.stream(
            "GET", "/runs/dispatch", params={"wid": self.meta_data.wid}
        ) as response:
            response.raise_for_status()

            if response.status_code == HTTPStatus.NO_CONTENT:
                logger.debug("Server has no work for us.")
                return None

            run = Run.model_validate_json(response.headers["X-Run"], strict=True)
            logger.debug(f"Received run: {run.run_id}")

            with TemporaryDirectory(delete=True) as archive_dir:
                logger.debug("Saving Experiment archive")
                archive_path = Path(archive_dir) / EXPERIMENT_ARCHIVE_NAME
                with archive_path.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        await to_thread(f.write, chunk)

                logger.debug("Validating Experiment archive")
                await to_thread(
                    run.definition.validate_archive, archive_path=archive_path
                )
                # TODO: tell the server if the archive failed validation
                logger.debug("Archive validated successfully, saving it for execution")
                await to_thread(copy, archive_path, self.archive_path)

        return run

    async def execute_run(self) -> SuccessState:
        """Execute the current run.

        Runs the setup executable (if configured), then the main executable. Environment files
        are copied and environment variables applied via ``prepare_run_environment`` before
        either executable starts. Each executable's stdout and stderr are captured to files in
        ``execution_dir``. If a timeout is configured, the process is killed on expiry.

        Returns:
            SuccessState: ``SUCCESS`` if all executables exited with code 0; ``FAILED`` if any
                exited with a non-zero code or exceeded the timeout.

        Raises:
            NoRunError: If called when no run is assigned (``self.working`` is ``None``).
        """
        if self.working is None:
            raise NoRunError

        logger.info(f"Executing Run {self.working.run_id}")
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
                process = await asyncio.subprocess.create_subprocess_exec(
                    program,
                    shell=False,
                    stdout=setup_stdout_file,
                    stderr=setup_stderr_file,
                    cwd=self.execution_dir,
                    env=env,
                )
                if self.working.definition.timeout:
                    try:
                        await wait_for(process.wait(), self.working.definition.timeout)
                    except TimeoutError:
                        process.kill()
                        return SuccessState.FAILED
                else:
                    await process.wait()
                if process.returncode != 0:
                    return SuccessState.FAILED

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
            process = await asyncio.subprocess.create_subprocess_exec(
                *args,
                shell=False,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=self.execution_dir,
                env=env,
            )
            if self.working.definition.timeout:
                try:
                    await wait_for(process.wait(), self.working.definition.timeout)
                except TimeoutError:
                    process.kill()
                    return SuccessState.FAILED
            else:
                await process.wait()
            if process.returncode != 0:
                return SuccessState.FAILED
            return SuccessState.SUCCESS

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
                logger.debug(f"Copying {source} to {destination}")
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
                    name_archive=Path(result_archive),
                )

    async def cleanup(self) -> None:
        """Clean up execution directory."""
        await to_thread(rmtree, self.execution_dir)
        await to_thread(self.execution_dir.mkdir)

    @staticmethod
    async def add_result(zf: ZipFile, name_local: Path, name_archive: Path) -> None:
        """Add a file or directory to an open ZIP archive.

        If ``name_local`` is a file, it is added directly. If it is a directory, all files
        within it are added recursively, preserving relative paths under ``name_archive``.
        Paths that do not exist are silently skipped.

        Args:
            zf (ZipFile): Open, writable ZIP archive to add the result to.
            name_local (Path): Filesystem path of the file or directory to add.
            name_archive (Path): Path to use as the entry name inside the archive.
        """
        if await to_thread(name_local.is_file):
            await to_thread(zf.write, name_local, name_archive)
        elif await to_thread(name_local.is_dir):
            for root, _, files in await to_thread(name_local.walk):
                for file in files:
                    file_path = root / file
                    arcname = name_archive / file_path.relative_to(name_local)
                    await to_thread(zf.write, file_path, arcname)

    async def upload_results(self) -> None:
        """Upload the results archive for the current run to the server.

        Raises:
            NoRunError: If called when no run is assigned (``self.working`` is ``None``).
            HTTPStatusError: If the server returns a non-2xx response.
        """
        if self.working is None:
            raise NoRunError

        archive_path = self.home_dir / RESULTS_ARCHIVE_NAME
        logger.info(f"Uploading results for run {self.working.run_id}")
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
            await sleep(SLEEP_TIME)
            try:
                await self.check_in()
            except HTTPStatusError as err:
                logger.error(
                    "Error performing checkin with server: %s", err, exc_info=True
                )

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


@cli.command()
def main(
    verbose: bool = Option(False, "-v", "--verbose", help="Enable debug logging"),
) -> None:
    """Start the worker daemon.

    Reads ``MPRUN_SERVER_ADDRESS``, ``MPRUN_WORKER_NAME``, and ``MPRUN_WORKER_DIRECTORY``
    from the environment, registers with the server, and enters the main loop.
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=log_level)

    server_address = getenv(SERVER_ADDRESS_ENV)
    if server_address is None:
        logger.fatal(f"Environment variable {SERVER_ADDRESS_ENV} not set!")
        raise Exit(1)

    name = getenv(WORKER_NAME_ENV)
    if name is None:
        logger.fatal(f"Environment variable {WORKER_NAME_ENV} not set!")
        raise Exit(1)

    home_directory = getenv(WORKER_HOME_DIR)
    if home_directory is None:
        logger.fatal(f"Environment variable {WORKER_HOME_DIR} not set!")
        raise Exit(1)
    home_directory = Path(home_directory)

    try:
        worker = asyncio.run(
            Worker.init(
                server_address=server_address, name=name, home_directory=home_directory
            )
        )
    except HTTPStatusError as err:
        logger.fatal("Worker registration failed: %s", err, exc_info=True)
        raise Exit(1) from err

    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
