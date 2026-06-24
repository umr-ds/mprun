"""Docker execution backend."""

import logging
from asyncio import to_thread
from pathlib import Path
from shutil import unpack_archive

from docker import DockerClient
from docker.models.images import Image

from mprun.errors import RunFailureError
from mprun.models import Run
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
    """

    config: DockerBackendConfig
    docker_client: DockerClient

    run: Run
    experiment_archive_path: Path
    results_archive_path: Path
    execution_dir: Path

    run_image: Image | None = None

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

        await to_thread(
            unpack_archive,
            filename=self.experiment_archive_path,
            extract_dir=self.execution_dir,
            format="zip",
        )

        try:
            image, build_logs = await to_thread(
                self.docker_client.images.build, path=str(self.execution_dir), pull=True
            )
            self.run_image = image
            with (self.execution_dir / DOCKER_BUILD_LOGS).open("rb") as f:
                f.write(build_logs)
        except Exception as err:
            logger.exception("Run %s failed to build", self.run.run_id)
            raise RunFailureError(run=self.run, reason=err) from err

        logger.info("Finished preparing for Run %s", self.run.run_id)

    async def execute_run(self) -> None:
        """Execute run.

        Raises:
            RunFailure: If the executable does not finish within its timeout / if it returns a code != 0.
        """
        # TODO: run experiment inside container

    async def collect_results(self) -> None:
        """Package run outputs and result files into a ZIP archive."""
        # TODO: collect results from inside container
