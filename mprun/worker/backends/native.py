"""Native execution backend."""

import asyncio
import asyncio.subprocess
import logging
from asyncio import to_thread, wait_for
from dataclasses import dataclass
from os import environ
from pathlib import Path
from shutil import copy, copytree, unpack_archive
from typing import BinaryIO, override
from zipfile import ZIP_LZMA, ZipFile

from mprun.custom_types import ActiveState
from mprun.errors import ExecutableReturnError, RunFailureError
from mprun.models import (
    Run,
    add_path_to_archive,
)
from mprun.worker import RESULTS_ARCHIVE_NAME
from mprun.worker.backends import Backend

logger = logging.getLogger(__name__)


@dataclass
class NativeBackend(Backend):
    """Runs experiments directly on the worker's host system without any sandboxing.

    Attributes:
        run (Run): The run that's going to be executed.
        archive_path (Path): Path to the Run's experiment archive.
        home_dir (Path): Worker's home directory - necessary for building the results archive.
        execution_dir (Path): (Temporary directory) for Run execution.
    """

    run: Run
    archive_path: Path
    home_dir: Path
    execution_dir: Path

    @override
    async def prepare_run_environment(self) -> None:
        """Unpack the experiment archive and build the process environment.

        Extracts the archive into ``execution_dir``, ensures executables have the execute bit
        set, and merges any ``environment_variables`` from the definition into a copy of the
        current process environment. Environment files are copied to their configured
        destinations on the local filesystem.

        Raises:
            RunFailureError: If something goes wrong during environment preparation.
        """
        logger.info("Preparing for Run %s", self.run.run_id)

        try:
            await to_thread(
                unpack_archive,
                filename=self.archive_path,
                extract_dir=self.execution_dir,
                format="zip",
            )

            # Set execute permissions for executables
            if self.run.definition.setup_executable:
                setup_script = self.execution_dir / self.run.definition.setup_executable
                await to_thread(setup_script.chmod, setup_script.stat().st_mode | 0o111)
            main_script = self.execution_dir / self.run.definition.executable
            await to_thread(main_script.chmod, main_script.stat().st_mode | 0o111)

            if self.run.definition.environment_files is not None:
                logger.debug("Copying environment files to destinations")
                for (
                    env_file,
                    destination,
                ) in self.run.definition.environment_files.items():
                    source = self.execution_dir / env_file
                    logger.debug("Copying %s to %s", source, destination)
                    if source.is_file():
                        await to_thread(copy, source, destination)
                    elif source.is_dir():
                        await to_thread(
                            copytree, source, destination, dirs_exist_ok=True
                        )
        except Exception as err:
            raise RunFailureError(run=self.run, reason=err) from err

        logger.info("Finished preparing for Run %s", self.run.run_id)

    async def execute(
        self,
        args: list[Path | str],
        stdout: BinaryIO,
        stderr: BinaryIO,
        env: dict[str, str],
    ) -> None:
        """Execute single executable.

        Args:
            args (list[Path | str): Path to executable + list of arguments in ``--arg value`` form.
            stdout (BinaryIO): Oen file to write executable's stdout to.
            stderr (BinaryIO): Oen file to write executable's stderr to.
            env (dict[str, str]): Environment variables for executable.

        Raises:
            RunFailure: If the executable does not finish within its timeout / if it returns a code != 0.
        """
        logger.debug("Executing: %s", args)

        try:
            process = await asyncio.subprocess.create_subprocess_exec(
                *args,
                shell=False,
                stdout=stdout,
                stderr=stderr,
                cwd=self.execution_dir,
                env=env,
            )
        except Exception as err:
            raise RunFailureError(run=self.run, reason=err) from err

        if self.run.definition.timeout:
            try:
                await wait_for(process.wait(), self.run.definition.timeout)
            except TimeoutError as err:
                logger.debug("%s did not finish within timeout, aborting.", args)
                process.kill()
                await process.wait()
                raise RunFailureError(run=self.run, reason=err) from err
        else:
            await process.wait()
        return_code = process.returncode
        logger.debug("%s finished with exit code %d", args, return_code)
        if return_code is not None and return_code != 0:
            raise RunFailureError(
                run=self.run,
                reason=ExecutableReturnError(name=str(args[0]), code=return_code),
            )

    @override
    async def execute_run(self) -> None:
        """Execute run.

        Runs the setup executable (if configured), then the main executable.
        Each executable's stdout and stderr are captured to files in ``execution_dir``. If a timeout is configured, the process is killed on expiry.

        Raises:
            RunFailure: If the executable does not finish within its timeout / if it returns a code != 0.
        """
        logger.info("Executing Run %s", self.run.run_id)

        try:
            env = environ.copy()
            if self.run.definition.environment_variables is not None:
                env.update(self.run.definition.environment_variables)

            if self.run.definition.setup_executable is not None:
                logger.debug("Running setup executable")
                setup_stdout_path = self.execution_dir / "stdout.setup"
                setup_stderr_path = self.execution_dir / "stderr.setup"
                program = self.execution_dir / self.run.definition.setup_executable
                with (
                    setup_stdout_path.open("wb") as setup_stdout_file,
                    setup_stderr_path.open("wb") as setup_stderr_file,
                ):
                    await self.execute(
                        args=[program],
                        stdout=setup_stdout_file,
                        stderr=setup_stderr_file,
                        env=env,
                    )

            stdout_path = self.execution_dir / "stdout"
            stderr_path = self.execution_dir / "stderr"
            args = [
                self.execution_dir / self.run.definition.executable,
                *self.run.assemble_args(),
            ]
            logger.debug("Running main executable")
            with (
                stdout_path.open("wb") as stdout_file,
                stderr_path.open("wb") as stderr_file,
            ):
                self.run.active_state = ActiveState.RUNNING
                await self.execute(
                    args=args,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=env,
                )
        except RunFailureError:
            raise
        except Exception as err:
            raise RunFailureError(run=self.run, reason=err) from err

        logger.info("Finished executing Run %s", self.run.run_id)

    async def _add_result(
        self, zf: ZipFile, name_local: Path, name_archive: str
    ) -> None:
        """Add a file or directory to an open ZIP archive.

        If ``name_local`` is a file, it is added directly. If it is a directory, all files
        within it are added recursively, preserving relative paths under ``name_archive``.
        Paths that do not exist are silently skipped.

        Args:
            zf (ZipFile): Open, writable ZIP archive to add the result to.
            name_local (Path): Filesystem path of the file or directory to add.
            name_archive (str): Path to use as the entry name inside the archive.

        Raises:
            RunFailureError: If adding the result to the archive fails.
        """
        logger.debug("Adding result %s as %s", name_local, name_archive)

        try:
            await to_thread(
                add_path_to_archive,
                zf=zf,
                name_local=name_local,
                name_archive=name_archive,
            )
        except RunFailureError:
            raise
        except Exception as err:
            raise RunFailureError(run=self.run, reason=err) from err

    @override
    async def collect_results(self) -> None:
        """Package run outputs and result files into a ZIP archive at ``home_dir/results.zip``.

        Always includes captured stdout/stderr (and their setup equivalents if present). Then
        adds every file or directory listed in the run's ``results`` definition.

        Raises:
            RunFailureError: If packaging results fails.
        """
        logger.info("Collecting results for %s", self.run.run_id)

        try:
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

                for result_local, result_archive in self.run.definition.results.items():
                    name_local = Path(result_local)
                    if not name_local.is_absolute():
                        name_local = self.execution_dir / name_local
                    await self._add_result(
                        zf=zf,
                        name_local=name_local,
                        name_archive=result_archive,
                    )
        except RunFailureError:
            raise
        except Exception as err:
            raise RunFailureError(run=self.run, reason=err) from err

        logger.info("Finished collecting results for %s", self.run.run_id)
