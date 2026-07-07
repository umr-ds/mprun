"""Docker execution backend."""

import asyncio
import logging
import os
import tarfile
from io import BytesIO
from pathlib import Path
from shutil import unpack_archive
from zipfile import ZIP_LZMA, ZipFile

from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from requests import exceptions as requests_exceptions

from mprun.errors import (
    ExecutableReturnError,
    RunFailureError,
    RunNotExecutedError,
    RunNotPreparedError,
    RunTimeoutError,
)
from mprun.models import Run, add_path_to_archive
from mprun.worker.config import DockerBackendConfig

logger = logging.getLogger(__name__)


DOCKER_BUILD_LOGS = "docker_build.log"


class DockerBackend:
    """Runs experiments inside a docker container.

    Attributes:
        config (DockerBackendConfig): Config for this execution backend.
        docker_client (DockerClient): Client connected to the docker daemon.

        run (Run): The run that's going to be executed.
        experiment_archive_path (Path): Path to the Run's experiment archive.
        results_archive_path (Path): Path to the Run's (eventual) experiment archive.
        execution_dir (Path): (Temporary directory) for Run execution.

        _run_image (Image | None): Metadata of the docker image for this run.
            ``None`` if ``self.prepare_run_environment`` has not been executed.
        _run_container (Container | None): Container in which the run has been executed.
            ``None`` if ``self.execute_run`` has not been executed.
    """

    config: DockerBackendConfig
    docker_client: DockerClient

    run: Run
    experiment_archive_path: Path
    results_archive_path: Path
    execution_dir: Path

    _run_image: Image | None = None
    _run_container: Container | None = None

    def __init__(
        self,
        config: DockerBackendConfig,
        run: Run,
        experiment_archive_path: Path,
        results_archive_path: Path,
        execution_dir: Path,
    ) -> None:
        """Initialise the backend.

        Args:
            config (DockerBackendConfig): Config for this execution backend.
            run (Run): The run that's going to be executed.
            experiment_archive_path (Path): Path to the Run's experiment archive.
            results_archive_path (Path): Path to the Run's (eventual) experiment archive.
            execution_dir (Path): (Temporary directory) for Run execution.
        """
        self.config = config
        self.docker_client = DockerClient(base_url=config.base_url, version="auto")

        self.run = run
        self.experiment_archive_path = experiment_archive_path
        self.results_archive_path = results_archive_path
        self.execution_dir = execution_dir

    async def prepare_run_environment(self) -> None:
        """Unpack the experiment archive and build the process environment.

        The archive must include a ``Dockerfile`` which will be used to build the image.
        The ``Dockerfile`` is responsible for copying the experiment's ``environment_files`` to the correct locations in the image.

        Raises:
            RunFailure: If something goes wrong during environment preparation / docker build process.
        """
        logger.info("Preparing for Run %s", self.run.run_id)

        await asyncio.to_thread(
            unpack_archive,
            filename=self.experiment_archive_path,
            extract_dir=self.execution_dir,
            format="zip",
        )

        # Set execute permissions for the main executable (volume-mounted)
        main_script = self.execution_dir / self.run.definition.executable
        await asyncio.to_thread(main_script.chmod, main_script.stat().st_mode | 0o111)

        try:
            image, build_logs = await asyncio.to_thread(
                self.docker_client.images.build, path=str(self.execution_dir), pull=True
            )
            self._run_image = image
            log_bytes: list[bytes] = []
            for chunk in build_logs:
                if isinstance(chunk, dict) and "stream" in chunk:
                    log_bytes.append(chunk["stream"].encode())
            with (self.execution_dir / DOCKER_BUILD_LOGS).open("wb") as f:
                for entry in log_bytes:
                    f.write(entry)
        except Exception as err:
            logger.exception("Run %s failed to build", self.run.run_id)
            raise RunFailureError(run=self.run, reason=err) from err

        logger.info("Finished preparing for Run %s", self.run.run_id)

    async def execute_run(self) -> None:
        """Execute run.

        Runs the main executable inside the container.

        Raises:
            RunFailure: If the executable does not finish within its timeout / if it returns a code != 0.
        """
        if self._run_image is None:
            raise RunNotPreparedError

        container: Container = self.docker_client.containers.run(
            image=self._run_image.id,
            command=[
                f"./{self.run.definition.executable}",
                *self.run.assemble_args(),
            ],
            environment=self.run.definition.environment_variables,
            volumes={str(self.execution_dir): {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            detach=True,
            auto_remove=False,
        )

        self._run_container = container

        try:
            if self.run.definition.timeout:
                try:
                    response = await asyncio.to_thread(
                        container.wait, timeout=self.run.definition.timeout
                    )
                except (
                    requests_exceptions.ReadTimeout,
                    requests_exceptions.ConnectionError,
                ) as err:
                    logger.warning("Run %s exceeded timeout", self.run.run_id)
                    await asyncio.to_thread(container.kill)
                    raise RunFailureError(
                        run=self.run,
                        reason=RunTimeoutError(seconds=self.run.definition.timeout),
                    ) from err
            else:
                response = await asyncio.to_thread(container.wait)

            if response["StatusCode"] != 0:
                raise RunFailureError(
                    run=self.run,
                    reason=ExecutableReturnError(
                        name=self.run.definition.executable,
                        code=response["StatusCode"],
                    ),
                )
        finally:
            # Chown root-owned files so the host process can clean them up.
            # collect_results also performs this; doing it here handles the
            # case where collect_results is never called (e.g. in tests).
            await self._chown_workspace(container)

    @staticmethod
    async def _chown_workspace(container: Container) -> None:
        """Chown the workspace volume mount to the host user's UID/GID."""
        try:
            uid = os.getuid()
            gid = os.getgid()
            await asyncio.to_thread(
                container.exec_run,
                f"chown -R {uid}:{gid} /workspace",
            )
        except Exception:
            logger.exception("Failed to chown workspace in container")

    async def collect_results(self) -> None:
        """Package run outputs and result files into a ZIP archive.

        Raises:
            RunFailureError: If packaging results fails.
            RunNotExecutedError: If ``execute_run`` has not been called before, or raised an error.
        """
        if self._run_container is None:
            raise RunNotExecutedError

        try:
            with ZipFile(
                self.results_archive_path,
                mode="w",
                compression=ZIP_LZMA,
                allowZip64=True,
            ) as zf:
                # add captured stdout & stderr
                stdout = await asyncio.to_thread(
                    self._run_container.logs, stdout=True, stderr=False
                )
                await asyncio.to_thread(
                    zf.writestr,
                    zinfo_or_arcname="stdout",
                    data=stdout,
                )
                stderr = await asyncio.to_thread(
                    self._run_container.logs, stdout=False, stderr=True
                )
                await asyncio.to_thread(
                    zf.writestr,
                    zinfo_or_arcname="stderr",
                    data=stderr,
                )

                # add results files
                for lp, archive_path in self.run.definition.results.items():
                    local_path = Path(lp)

                    # Always use get_archive to copy from inside the container.
                    # Relative paths are relative to /workspace (the working dir).
                    container_path = (
                        str(local_path)
                        if local_path.is_absolute()
                        else f"/workspace/{lp}"
                    )
                    bits, _stat = await asyncio.to_thread(
                        self._run_container.get_archive, container_path
                    )
                    tar_bytes = b"".join(bits)
                    extract_dir = (
                        self.execution_dir / f"__collect_{lp.replace('/', '_')}"
                    )
                    extract_dir.mkdir(parents=True, exist_ok=True)
                    with tarfile.open(fileobj=BytesIO(tar_bytes)) as tar:
                        await asyncio.to_thread(tar.extractall, path=str(extract_dir))

                    await asyncio.to_thread(
                        add_path_to_archive,
                        zf=zf,
                        name_local=extract_dir / local_path.name,
                        name_archive=archive_path,
                    )
        except RunFailureError:
            raise
        except Exception as err:
            raise RunFailureError(run=self.run, reason=err) from err
        finally:
            if self._run_container is not None:
                await self._chown_workspace(self._run_container)
                await asyncio.to_thread(self._run_container.remove)
