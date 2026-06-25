"""Tests for the Docker execution backend."""

from pathlib import Path
from zipfile import ZipFile

import pytest

from mprun.custom_types import WorkerBackend
from mprun.errors import RunFailureError
from mprun.models import (
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
)
from mprun.worker.backends import DockerBackend
from mprun.worker.config import DockerBackendConfig
from mprun.worker.worker import RESULTS_ARCHIVE_NAME
from tests.conftest import copy_docker_experiment_to_test_environment

DOCKERFILE_STUB = """\
FROM python:3.12-slim
WORKDIR /workspace
"""

pytestmark = pytest.mark.usefixtures("docker_available")


def _make_backend(
    definition: ExperimentDefinition,
    experiment_archive_path: Path,
    results_archive_path: Path,
    execution_dir: Path,
    docker_config: DockerBackendConfig | None = None,
) -> DockerBackend:
    """Build a DockerBackend for the first run of the given experiment."""
    run = Experiment.new(definition=definition).runs[0][0]
    return DockerBackend(
        config=docker_config or DockerBackendConfig(),
        run=run,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=execution_dir,
    )


def _build_docker_experiment(
    directory: Path,
    *,
    executable: str = "main.py",
    executable_content: str = "#!/usr/bin/env python3\npass\n",
    timeout: int | None = None,
    environment_variables: dict[str, str] | None = None,
) -> tuple[ExperimentDefinition, Path]:
    """Build a minimal Docker-backed experiment with a stub Dockerfile.

    Writes the executable, a minimal Dockerfile, and the TOML definition
    into ``directory`` then returns the parsed definition and the directory.
    """
    directory.mkdir(parents=True, exist_ok=True)

    main_script = directory / executable
    main_script.write_text(executable_content)
    main_script.chmod(0o755)

    (directory / "Dockerfile").write_text(DOCKERFILE_STUB)

    definition = ExperimentDefinition(
        name="exp",
        params={"x": [1]},
        backends={WorkerBackend.DOCKER},
        executable=executable,
        results={},
        timeout=timeout,
        environment_variables=environment_variables,
    )
    definition.dump_toml(directory / EXPERIMENT_DEFINITION_NAME)
    return definition, directory


@pytest.mark.asyncio
async def test_execute_run(tmp_path: Path) -> None:
    """Smoke test: docker backend executes the bundled real experiment scripts."""
    experiment_definition, experiment_definition_path = (
        copy_docker_experiment_to_test_environment(directory=tmp_path)
    )
    experiment_archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()
    backend = _make_backend(
        definition=experiment_definition,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=exec_dir,
    )
    await backend.prepare_run_environment()
    await backend.execute_run()


@pytest.mark.asyncio
async def test_collect_results(tmp_path: Path) -> None:
    """Smoke test: result archive contains every file the bundled scripts produce."""
    experiment_definition, experiment_definition_path = (
        copy_docker_experiment_to_test_environment(directory=tmp_path)
    )
    experiment_archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()
    backend = _make_backend(
        definition=experiment_definition,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=exec_dir,
    )
    await backend.prepare_run_environment()
    await backend.execute_run()
    await backend.collect_results()

    assert results_archive_path.is_file()

    with ZipFile(results_archive_path, "r") as zf:
        contents = zf.namelist()
        assert "stdout" in contents
        assert "stderr" in contents
        assert "envfile" in contents
        assert "test_file.txt" in contents
        assert "test_dir/nested_file.txt" in contents
        assert "working_file.txt" in contents
        assert "working_dir/nested_working_file.txt" in contents


@pytest.mark.asyncio
async def test_execute_run_fails_when_main_exits_nonzero(
    tmp_path: Path,
) -> None:
    """execute_run raises RunFailureError when the main executable exits non-zero."""
    definition, directory = _build_docker_experiment(
        directory=tmp_path / "experiment",
        executable_content="#!/usr/bin/env python3\nimport sys; sys.exit(1)\n",
    )
    experiment_archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()
    backend = _make_backend(
        definition=definition,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=exec_dir,
    )
    await backend.prepare_run_environment()
    with pytest.raises(RunFailureError):
        await backend.execute_run()


@pytest.mark.asyncio
async def test_execute_run_fails_on_timeout(tmp_path: Path) -> None:
    """execute_run raises RunFailureError when the container exceeds its timeout."""
    definition, directory = _build_docker_experiment(
        directory=tmp_path / "experiment",
        executable_content=("#!/usr/bin/env python3\nimport time; time.sleep(3600)\n"),
        timeout=1,
    )
    experiment_archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()
    backend = _make_backend(
        definition=definition,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=exec_dir,
    )
    await backend.prepare_run_environment()
    with pytest.raises(RunFailureError):
        await backend.execute_run()


@pytest.mark.asyncio
async def test_execute_run_passes_env_vars_to_container(
    tmp_path: Path,
) -> None:
    """environment_variables from the definition are visible inside the container."""
    definition, directory = _build_docker_experiment(
        directory=tmp_path / "experiment",
        executable_content=(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "print(os.environ.get('TEST_VAR', '<unset>'))\n"
        ),
        environment_variables={"TEST_VAR": "hello"},
    )
    experiment_archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()
    backend = _make_backend(
        definition=definition,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=exec_dir,
    )
    await backend.prepare_run_environment()
    await backend.execute_run()
    await backend.collect_results()

    with ZipFile(results_archive_path, "r") as zf:
        stdout = zf.read("stdout").decode().strip()
    assert "hello" in stdout


@pytest.mark.asyncio
async def test_build_logs_saved(tmp_path: Path) -> None:
    """docker_build.log is written to execution_dir after prepare_run_environment."""
    experiment_definition, experiment_definition_path = (
        copy_docker_experiment_to_test_environment(directory=tmp_path)
    )
    experiment_archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()
    backend = _make_backend(
        definition=experiment_definition,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=exec_dir,
    )
    await backend.prepare_run_environment()

    build_log = exec_dir / "docker_build.log"
    assert build_log.is_file()
    assert build_log.stat().st_size > 0


@pytest.mark.asyncio
async def test_build_failure_raises(tmp_path: Path) -> None:
    """prepare_run_environment raises RunFailureError when Docker build fails."""
    directory = tmp_path / "experiment"
    directory.mkdir()

    (directory / "Dockerfile").write_text("FROM nonexistent_base_image_xyz:999\n")

    executable = directory / "main.py"
    executable.write_text("#!/usr/bin/env python3\npass\n")
    executable.chmod(0o755)

    definition = ExperimentDefinition(
        name="exp",
        params={"x": [1]},
        backends={WorkerBackend.DOCKER},
        executable="main.py",
        results={},
    )
    definition.dump_toml(directory / EXPERIMENT_DEFINITION_NAME)

    experiment_archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )
    results_archive_path = tmp_path / RESULTS_ARCHIVE_NAME

    exec_dir = tmp_path / "exec"
    exec_dir.mkdir()
    backend = _make_backend(
        definition=definition,
        experiment_archive_path=experiment_archive_path,
        results_archive_path=results_archive_path,
        execution_dir=exec_dir,
    )
    with pytest.raises(RunFailureError):
        await backend.prepare_run_environment()
