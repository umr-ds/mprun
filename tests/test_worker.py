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
from mprun.errors import NoRunError
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    ActiveState,
    ExperimentDefinition,
    FailureReason,
    Run,
    SuccessState,
    WorkerData,
    WorkerRegistration,
)
from mprun.server import DATA_PATH_ENV, lifespan, server
from mprun.worker import RESULTS_ARCHIVE_NAME
from mprun.worker.worker import Worker


def _idle_worker(home_dir: Path) -> Worker:
    """Build a Worker with no assigned run."""
    return Worker(
        http_client=AsyncClient(),
        meta_data=WorkerData.new(
            registration_data=WorkerRegistration(
                name="testworker", backend=WorkerBackend.NATIVE
            )
        ),
        home_dir=home_dir,
    )


@pytest.mark.asyncio
@given(name=st.text())
async def test_register(name: str) -> None:
    """Test worker registration."""
    with (
        TemporaryDirectory(delete=True) as data_dir,
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setenv(DATA_PATH_ENV, data_dir)

        async with (
            lifespan(server),
            AsyncClient(
                transport=ASGITransport(app=server), base_url="http://test"
            ) as client,
        ):
            registration_data = WorkerRegistration(
                name=name, backend=WorkerBackend.NATIVE
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
    with (
        TemporaryDirectory(delete=True) as data_dir,
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setenv(DATA_PATH_ENV, f"{data_dir}/server")

        async with (
            lifespan(server),
            AsyncClient(
                transport=ASGITransport(app=server), base_url="http://test"
            ) as client,
        ):
            home_dir = Path(data_dir) / "worker"
            registration_data = WorkerRegistration(
                name=name, backend=WorkerBackend.NATIVE
            )
            metadata = await Worker.register(
                client=client, registration_data=registration_data
            )
            worker = Worker(http_client=client, meta_data=metadata, home_dir=home_dir)
            await worker.check_in()


@pytest.mark.asyncio
async def test_get_run(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Worker can fetch dispatched work from the server."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))

        async with (
            lifespan(server),
            AsyncClient(
                transport=ASGITransport(app=server), base_url="http://test"
            ) as client,
        ):
            home_dir = tmp_path / "worker"
            registration_data = WorkerRegistration(
                name="test_worker", backend=WorkerBackend.NATIVE
            )
            metadata = await Worker.register(
                client=client, registration_data=registration_data
            )
            worker = Worker(http_client=client, meta_data=metadata, home_dir=home_dir)

            assert worker.working is None
            await worker.get_work()
            assert worker.working is None  # no experiment present yet

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

            await worker.get_work()
            assert worker.working is not None
            assert worker.archive_path.is_file(follow_symlinks=False)


@pytest.mark.asyncio
async def test_results_upload(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Worker uploads results; server reflects the new run state."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))

        async with (
            lifespan(server),
            AsyncClient(
                transport=ASGITransport(app=server), base_url="http://test"
            ) as client,
        ):
            home_dir = tmp_path / "worker"
            registration_data = WorkerRegistration(
                name="test_worker", backend=WorkerBackend.NATIVE
            )
            metadata = await Worker.register(
                client=client, registration_data=registration_data
            )
            worker = Worker(http_client=client, meta_data=metadata, home_dir=home_dir)

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

            await worker.get_work()
            assert worker.working is not None

            worker.working.active_state = ActiveState.FINISHED
            worker.working.success_state = SuccessState.SUCCESS

            home_dir.mkdir(parents=True, exist_ok=True)
            results_path = home_dir / RESULTS_ARCHIVE_NAME
            with ZipFile(results_path, mode="w") as zf:
                zf.writestr("result.txt", "ok")

            await worker.upload_results()

            response = await client.get(
                f"/runs/{worker.working.eid}/{worker.working.index}/{worker.working.iteration}"
            )
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
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))

        async with (
            lifespan(server),
            AsyncClient(
                transport=ASGITransport(app=server), base_url="http://test"
            ) as client,
        ):
            home_dir = tmp_path / "worker"
            registration_data = WorkerRegistration(
                name="test_worker", backend=WorkerBackend.NATIVE
            )
            metadata = await Worker.register(
                client=client, registration_data=registration_data
            )
            worker = Worker(http_client=client, meta_data=metadata, home_dir=home_dir)

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

            await worker.get_work()
            assert worker.working is not None

            worker.working.active_state = ActiveState.FINISHED
            worker.working.success_state = SuccessState.FAILED
            worker.working.failure_reason = FailureReason.BAD_ARCHIVE

            await worker.report_error()

            response = await client.get(
                f"/runs/{worker.working.eid}/{worker.working.index}/{worker.working.iteration}"
            )
            response.raise_for_status()
            submitted_run = Run.model_validate(response.json())
            assert submitted_run.active_state == ActiveState.FINISHED
            assert submitted_run.success_state == SuccessState.FAILED
            assert submitted_run.failure_reason == FailureReason.BAD_ARCHIVE


@pytest.mark.asyncio
async def test_worker_raises_no_run_error_when_idle(tmp_path: Path) -> None:
    """Run-dependent methods raise NoRunError when no run is assigned."""
    worker = _idle_worker(tmp_path / "worker")

    with pytest.raises(NoRunError):
        await worker.upload_results()
    with pytest.raises(NoRunError):
        await worker.report_error()
