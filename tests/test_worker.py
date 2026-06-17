"""Tests for worker module.

``test_execute_run`` and ``test_collect_results`` are the project's
end-to-end smoke tests: they execute the bundled real scripts under
``tests/artefacts/test_experiment/`` and assert on the files those scripts
produce. The remaining tests use the synthetic ``make_experiment``
factory so they can exercise the worker's API surface without depending
on the real artefact.
"""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import given
from hypothesis import strategies as st

from mprun.errors import NoRunError
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    ActiveState,
    Experiment,
    ExperimentDefinition,
    FailureReason,
    Run,
    SuccessState,
    WorkerData,
)
from mprun.server import DATA_PATH_ENV, lifespan, server
from mprun.worker.worker import RESULTS_ARCHIVE_NAME, Worker
from tests.conftest import copy_experiment_to_test_environment


def _idle_worker(home_dir: Path) -> Worker:
    """Build a Worker with no assigned run."""
    return Worker(
        http_client=AsyncClient(),
        meta_data=WorkerData.new(name="testworker"),
        home_dir=home_dir,
    )


def _ready_worker(
    home_dir: Path,
    definition: ExperimentDefinition,
    archive_path: Path,
) -> Worker:
    """Build a Worker pre-assigned to ``definition``'s first run with archive in place."""
    worker = _idle_worker(home_dir)
    worker.working = Experiment.new(definition=definition).runs[0][0]
    worker.archive_path = archive_path
    return worker


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
            worker = await Worker.register(client=client, name=name)
            assert isinstance(worker, WorkerData)
            assert worker.name == name


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
            metadata = await Worker.register(client=client, name=name)
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
            metadata = await Worker.register(client=client, name="test_worker")
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
async def test_execute_run(tmp_path: Path) -> None:
    """Smoke test: worker executes the bundled real experiment scripts."""
    worker_data = WorkerData.new(name="test_worker")
    dummy_client = AsyncClient()

    experiment_definition, experiment_definition_path = (
        copy_experiment_to_test_environment(directory=tmp_path)
    )
    archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )
    experiment = Experiment.new(definition=experiment_definition)

    worker = Worker(http_client=dummy_client, meta_data=worker_data, home_dir=tmp_path)

    worker.working = experiment.runs[0][0]
    worker.archive_path = archive_path

    with TemporaryDirectory() as exec_dir:
        failure = await worker.execute_run(execution_dir=Path(exec_dir))

    assert failure is None


@pytest.mark.asyncio
async def test_collect_results(tmp_path: Path) -> None:
    """Smoke test: result archive contains every file the bundled scripts produce."""
    worker_data = WorkerData.new(name="test_worker")
    dummy_client = AsyncClient()

    experiment_definition, experiment_definition_path = (
        copy_experiment_to_test_environment(directory=tmp_path)
    )
    archive_path = experiment_definition.create_archive(
        experiment_toml=experiment_definition_path
    )
    experiment = Experiment.new(definition=experiment_definition)

    worker = Worker(http_client=dummy_client, meta_data=worker_data, home_dir=tmp_path)

    worker.working = experiment.runs[0][0]
    worker.archive_path = archive_path

    with TemporaryDirectory() as exec_dir:
        exec_path = Path(exec_dir)
        failure = await worker.execute_run(execution_dir=exec_path)
        assert failure is None

        await worker.collect_results(execution_dir=exec_path)

    results_archive = worker.home_dir / RESULTS_ARCHIVE_NAME
    assert results_archive.is_file()

    with ZipFile(results_archive, "r") as zf:
        contents = zf.namelist()
        assert "stdout.setup" in contents
        assert "stderr.setup" in contents
        assert "stdout" in contents
        assert "stderr" in contents
        assert "envfile" in contents
        assert "test_file.txt" in contents
        assert "test_dir/nested_file.txt" in contents
        assert "working_file.txt" in contents
        assert "working_dir/nested_working_file.txt" in contents


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
            metadata = await Worker.register(client=client, name="test_worker")
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
            metadata = await Worker.register(client=client, name="test_worker")
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
async def test_execute_run_fails_when_main_exits_nonzero(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """execute_run returns FAILED when the main executable exits with non-zero status."""
    definition, directory = make_experiment(
        executable_content="#!/usr/bin/env python3\nimport sys; sys.exit(1)\n",
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    worker = _ready_worker(tmp_path / "worker", definition, archive_path)

    with TemporaryDirectory() as exec_dir:
        assert (
            await worker.execute_run(execution_dir=Path(exec_dir))
            == FailureReason.RETURN
        )


@pytest.mark.asyncio
async def test_execute_run_fails_when_setup_exits_nonzero(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """execute_run returns FAILED when the setup executable exits with non-zero status."""
    definition, directory = make_experiment(
        setup=True,
        setup_content="#!/usr/bin/env python3\nimport sys; sys.exit(2)\n",
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    worker = _ready_worker(tmp_path / "worker", definition, archive_path)

    with TemporaryDirectory() as exec_dir:
        assert (
            await worker.execute_run(execution_dir=Path(exec_dir))
            == FailureReason.RETURN
        )


@pytest.mark.asyncio
async def test_execute_run_passes_env_vars_to_subprocess(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """environment_variables from the definition are visible inside the run subprocess."""
    definition, directory = make_experiment(
        executable_content=(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "sys.stdout.write(os.environ.get('MPRUN_TEST', '<unset>'))\n"
        ),
        environment_variables={"MPRUN_TEST": "hello"},
    )
    archive_path = definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
    )

    worker = _ready_worker(tmp_path / "worker", definition, archive_path)

    with TemporaryDirectory() as exec_dir:
        exec_path = Path(exec_dir)
        assert await worker.execute_run(execution_dir=exec_path) is None
        assert (exec_path / "stdout").read_text() == "hello"


@pytest.mark.asyncio
async def test_worker_raises_no_run_error_when_idle(tmp_path: Path) -> None:
    """Run-dependent methods raise NoRunError when no run is assigned."""
    worker = _idle_worker(tmp_path / "worker")

    dummy_dir = tmp_path / "worker"
    with pytest.raises(NoRunError):
        await worker.execute_run(execution_dir=dummy_dir)
    with pytest.raises(NoRunError):
        await worker.prepare_run_environment(execution_dir=dummy_dir)
    with pytest.raises(NoRunError):
        await worker.collect_results(execution_dir=dummy_dir)
    with pytest.raises(NoRunError):
        await worker.upload_results()
    with pytest.raises(NoRunError):
        await worker.report_error()
