"""Tests for worker module."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from httpx import ASGITransport, AsyncClient
from hypothesis import given
from hypothesis import strategies as st

from mprun.models import WorkerData
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
            await worker.get_work()
            assert worker.working is not None
            assert worker.archive_path.is_file(follow_symlinks=False)
