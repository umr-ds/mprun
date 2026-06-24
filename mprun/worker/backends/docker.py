"""Docker execution backend."""

from pathlib import Path

from docker import DockerClient

from mprun.worker.config import DockerBackendConfig


class DockerBackend:
    """Runs experiments inside a docker container.

    Attributes:
        config (DockerBackendConfig): Config for this execution backend.
        docker_client (DockerClient): Client connected to the docker daemon.
        experiment_archive_path (Path): Path to the Run's experiment archive.
        results_archive_path (Path): Path to the Run's (eventual) experiment archive.
        execution_dir (Path): (Temporary directory) for Run execution.
    """

    config: DockerBackendConfig
    docker_client: DockerClient
    experiment_archive_path: Path
    results_archive_path: Path
    execution_dir: Path

    def __init__(
        self,
        config: DockerBackendConfig,
        experiment_archive_path: Path,
        results_archive_path: Path,
        execution_dir: Path,
    ) -> None:
        """Initialise the backend.

        Args:
            config (DockerBackendConfig): Config for this execution backend.
            experiment_archive_path (Path): Path to the Run's experiment archive.
            results_archive_path (Path): Path to the Run's (eventual) experiment archive.
            execution_dir (Path): (Temporary directory) for Run execution.
        """
        self.config = config
        self.experiment_archive_path = experiment_archive_path
        self.results_archive_path = results_archive_path
        self.execution_dir = execution_dir

        self.docker_client = DockerClient(base_url=config.base_url, version="auto")

    async def prepare_run_environment(self) -> None:
        """Unpack the experiment archive and build the process environment.

        Raises:
            RunFailure: If something goes wrong during environment preparation.
        """

    async def execute_run(self) -> None:
        """Execute run.

        Raises:
            RunFailure: If the executable does not finish within its timeout / if it returns a code != 0.
        """

    async def collect_results(self) -> None:
        """Package run outputs and result files into a ZIP archive."""
