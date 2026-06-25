"""Tests for worker module."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import given
from hypothesis import strategies as st

from mprun.custom_types import WorkerBackend
from mprun.errors import RunFailureError
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    ActiveState,
    ExperimentDefinition,
    Run,
    SuccessState,
    WorkerData,
    WorkerRegistration,
)
from mprun.server.server import lifespan, server
from mprun.worker.config import WorkerConfig
from mprun.worker.worker import RESULTS_ARCHIVE_NAME, Worker
from tests.conftest import TEST_SERVER_PORT, configure_server_for_test

TEST_WORKER_NAME = "test_worker"


def generate_test_worker_config(
    home_directory: Path, name: str = TEST_WORKER_NAME
) -> WorkerConfig:
    """Generate ``WorkerConfig`` for use in test cases."""
    return WorkerConfig(
        name=name,
        server_address=f"http://localhost:{TEST_SERVER_PORT}",
        home_directory=home_directory,
        backends={WorkerBackend.NATIVE},
    )


@pytest.mark.asyncio
@given(name=st.text())
async def test_register(name: str) -> None:
    """Test worker registration."""
    with TemporaryDirectory(delete=True) as data_dir:
        configure_server_for_test(Path(data_dir))

        async with (
            lifespan(server),
            AsyncClient(
                transport=ASGITransport(app=server), base_url="http://test"
            ) as client,
        ):
            registration_data = WorkerRegistration(
                name=name, backends={WorkerBackend.NATIVE}
            )
            worker = await Worker.register(
                client=client, registration_data=registration_data
            )
            assert isinstance(worker, WorkerData)
            assert worker.registration_data.name == name


@pytest.mark.asyncio
@given(name=st.text())
async def test_checkin(name: str) -> None:
    """Test worker checkin."""
    with TemporaryDirectory(delete=True) as data_dir:
        configure_server_for_test(Path(data_dir) / "server")

        async with (
            lifespan(server),
            AsyncClient(
                transport=ASGITransport(app=server), base_url="http://test"
            ) as client,
        ):
            home_dir = Path(data_dir) / "worker"
            registration_data = WorkerRegistration(
                name=name, backends={WorkerBackend.NATIVE}
            )
            metadata = await Worker.register(
                client=client, registration_data=registration_data
            )
            worker = Worker(
                http_client=client,
                meta_data=metadata,
                config=generate_test_worker_config(name=name, home_directory=home_dir),
            )
            await worker.check_in()


@pytest.mark.asyncio
async def test_get_run(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Worker can fetch dispatched work from the server."""
    configure_server_for_test(tmp_path / "server")

    async with (
        lifespan(server),
        AsyncClient(
            transport=ASGITransport(app=server), base_url="http://test"
        ) as client,
    ):
        home_dir = tmp_path / "worker"
        registration_data = WorkerRegistration(
            name=TEST_WORKER_NAME, backends={WorkerBackend.NATIVE}
        )
        metadata = await Worker.register(
            client=client, registration_data=registration_data
        )
        worker = Worker(
            http_client=client,
            meta_data=metadata,
            config=generate_test_worker_config(home_directory=home_dir),
        )

        run = await worker.get_work()
        assert run is None  # no experiment present yet

        definition, experiment_dir = make_experiment()
        archive_path = definition.create_archive(
            experiment_toml=experiment_dir / EXPERIMENT_DEFINITION_NAME
        )
        with archive_path.open("rb") as archive_file:
            response = await client.post(
                "/experiments",
                data={"experiment_definition": definition.model_dump_json()},
                files={
                    "archive": (
                        EXPERIMENT_ARCHIVE_NAME,
                        archive_file,
                        "application/zip",
                    )
                },
            )
            response.raise_for_status()

        run = await worker.get_work()
        assert run is not None
        assert worker.experiment_archive_path.is_file(follow_symlinks=False)


@pytest.mark.asyncio
async def test_results_upload(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Worker uploads results; server reflects the new run state."""
    configure_server_for_test(tmp_path / "server")

    async with (
        lifespan(server),
        AsyncClient(
            transport=ASGITransport(app=server), base_url="http://test"
        ) as client,
    ):
        home_dir = tmp_path / "worker"
        registration_data = WorkerRegistration(
            name=TEST_WORKER_NAME, backends={WorkerBackend.NATIVE}
        )
        metadata = await Worker.register(
            client=client, registration_data=registration_data
        )
        worker = Worker(
            http_client=client,
            meta_data=metadata,
            config=generate_test_worker_config(home_directory=home_dir),
        )

        definition, experiment_dir = make_experiment()
        archive_path = definition.create_archive(
            experiment_toml=experiment_dir / EXPERIMENT_DEFINITION_NAME
        )
        with archive_path.open("rb") as archive_file:
            response = await client.post(
                "/experiments",
                data={"experiment_definition": definition.model_dump_json()},
                files={
                    "archive": (
                        EXPERIMENT_ARCHIVE_NAME,
                        archive_file,
                        "application/zip",
                    )
                },
            )
            response.raise_for_status()

        run = await worker.get_work()
        assert run is not None

        run.active_state = ActiveState.FINISHED
        run.success_state = SuccessState.SUCCESS

        home_dir.mkdir(parents=True, exist_ok=True)
        results_path = home_dir / RESULTS_ARCHIVE_NAME
        with ZipFile(results_path, mode="w") as zf:
            zf.writestr("result.txt", "ok")

        await worker.upload_results(run=run)

        response = await client.get(f"/runs/{run.eid}/{run.index}/{run.iteration}")
        response.raise_for_status()
        submitted_run = Run.model_validate(response.json())
        assert submitted_run.active_state == ActiveState.FINISHED
        assert submitted_run.success_state == SuccessState.SUCCESS


@pytest.mark.asyncio
async def test_report_error(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Worker reports an archive validation error; server records the failure."""
    configure_server_for_test(tmp_path / "server")

    async with (
        lifespan(server),
        AsyncClient(
            transport=ASGITransport(app=server), base_url="http://test"
        ) as client,
    ):
        home_dir = tmp_path / "worker"
        registration_data = WorkerRegistration(
            name=TEST_WORKER_NAME, backends={WorkerBackend.NATIVE}
        )
        metadata = await Worker.register(
            client=client, registration_data=registration_data
        )
        worker = Worker(
            http_client=client,
            meta_data=metadata,
            config=generate_test_worker_config(home_directory=home_dir),
        )

        definition, experiment_dir = make_experiment()
        archive_path = definition.create_archive(
            experiment_toml=experiment_dir / EXPERIMENT_DEFINITION_NAME
        )
        with archive_path.open("rb") as archive_file:
            response = await client.post(
                "/experiments",
                data={"experiment_definition": definition.model_dump_json()},
                files={
                    "archive": (
                        EXPERIMENT_ARCHIVE_NAME,
                        archive_file,
                        "application/zip",
                    )
                },
            )
            response.raise_for_status()

        run = await worker.get_work()
        assert run is not None

        failure = RunFailureError(run=run, reason=Exception("BAD_ARCHIVE"))

        await worker.report_error(failure=failure)

        response = await client.get(f"/runs/{run.eid}/{run.index}/{run.iteration}")
        response.raise_for_status()
        submitted_run = Run.model_validate(response.json())
        assert submitted_run.active_state == ActiveState.FINISHED
        assert submitted_run.success_state == SuccessState.FAILED
        assert submitted_run.failure_reason == "BAD_ARCHIVE"


@pytest.mark.asyncio
async def test_get_work_returns_none_when_idle(tmp_path: Path) -> None:
    """get_work returns None when the server has no work."""
    configure_server_for_test(tmp_path / "server")

    async with (
        lifespan(server),
        AsyncClient(
            transport=ASGITransport(app=server), base_url="http://test"
        ) as client,
    ):
        home_dir = tmp_path / "worker"
        metadata = await Worker.register(
            client=client,
            registration_data=WorkerRegistration(
                name=TEST_WORKER_NAME, backends={WorkerBackend.NATIVE}
            ),
        )
        worker = Worker(
            http_client=client,
            meta_data=metadata,
            config=generate_test_worker_config(home_directory=home_dir),
        )

        run = await worker.get_work()
        assert run is None
