"""Tests for worker module."""

from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import given
from hypothesis import strategies as st

from mprun.models import RESULTS_ARCHIVE_NAME, Job, SuccessState, WorkerData
from mprun.server import DATA_PATH_ENV, lifespan, server
from mprun.worker import Worker
from tests.helpers.job_helper import (
    TEST_JOB,
    copy_job_to_test_environment,
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
@given(name=st.text())
async def test_get_run(name: str) -> None:
    """The work retrieval."""
    with (
        TemporaryDirectory(delete=True) as data_dir,
        pytest.MonkeyPatch.context() as mp,
    ):
        directory = Path(data_dir)
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

            assert worker.working is None  # at first, the worker is working on nothing
            await worker.get_work()
            assert (
                worker.working is None
            )  # if there's no jobs present, we can get no work

            # submit example job
            job_definition, job_definition_path = copy_job_to_test_environment(
                directory=directory
            )
            archive_path = job_definition.create_archive(job_toml=job_definition_path)
            with archive_path.open("rb") as archive_file:
                response = await client.post(
                    "/jobs",
                    data={"job_definition": TEST_JOB.model_dump_json()},
                    files={
                        "archive": ("job_archive.zip", archive_file, "application/zip")
                    },
                )
                response.raise_for_status()

            # now try getting work again
            run = await worker.get_work()
            assert run is not None
            assert worker.archive_path.is_file(follow_symlinks=False)


@pytest.mark.asyncio
@given(name=st.text())
async def test_execute_run(name: str) -> None:
    """Test run execution."""
    with TemporaryDirectory(delete=True) as data_dir:
        directory = Path(data_dir)
        worker_data = WorkerData.new(name=name)
        dummy_client = AsyncClient()

        job_definition, job_definition_path = copy_job_to_test_environment(
            directory=directory
        )
        archive_path = job_definition.create_archive(job_toml=job_definition_path)
        job = Job.new(definition=job_definition)

        worker = Worker(
            http_client=dummy_client, meta_data=worker_data, home_dir=directory
        )

        worker.working = job.runs[0]
        worker.archive_path = archive_path

        success = await worker.execute_run()

        assert success == SuccessState.SUCCESS


@pytest.mark.asyncio
@given(name=st.text())
async def test_collect_results(name: str) -> None:
    """Test result collection."""
    with TemporaryDirectory(delete=True) as data_dir:
        directory = Path(data_dir)
        worker_data = WorkerData.new(name=name)
        dummy_client = AsyncClient()

        job_definition, job_definition_path = copy_job_to_test_environment(
            directory=directory
        )
        archive_path = job_definition.create_archive(job_toml=job_definition_path)
        job = Job.new(definition=job_definition)

        worker = Worker(
            http_client=dummy_client, meta_data=worker_data, home_dir=directory
        )

        worker.working = job.runs[0]
        worker.archive_path = archive_path

        success = await worker.execute_run()
        assert success == SuccessState.SUCCESS

        await worker.collect_results()

        # Check if results archive exists
        results_archive = worker.home_dir / RESULTS_ARCHIVE_NAME
        assert results_archive.is_file()

        # Check if results archive contains expected files
        with ZipFile(results_archive, "r") as zf:
            contents = zf.namelist()
            # stdout.setup and stderr.setup are only created if setup executable is run
            assert "stdout.setup" in contents
            assert "stderr.setup" in contents
            # stdout and stderr are only created if main executable is run
            assert "stdout" in contents
            assert "stderr" in contents
            assert "envfile" in contents
            assert "test_file.txt" in contents
            assert "test_dir/nested_file.txt" in contents
            assert "working_file.txt" in contents
            assert "working_dir/nested_working_file.txt" in contents
