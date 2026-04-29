"""Tests for server module."""

from http import HTTPStatus
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from mprun import SERVER_ADDRESS_ENV
from mprun.models import Job, WorkerData
from mprun.server import DATA_PATH_ENV, server
from tests.helpers.job_helper import (
    TEST_JOB,
    copy_job_to_test_environment,
)


class TestWorkers:
    """Tests for worker-related endpoints."""

    @given(name=st.text())
    def test_woker_register(self, name: str) -> None:
        """Test worker registration."""
        with (
            TemporaryDirectory(delete=True) as data_dir,
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setenv(DATA_PATH_ENV, data_dir)
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                response = client.post("/workers", params={"name": name})
                assert response.status_code == HTTPStatus.CREATED
                worker = WorkerData.model_validate(response.json())
                assert worker.name == name

    @given(names=st.lists(elements=st.text()))
    def test_worker_list(self, names: list[str]) -> None:
        """Test worker list-endpoint."""
        with (
            TemporaryDirectory(delete=True) as data_dir,
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setenv(DATA_PATH_ENV, data_dir)
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                for name in names:
                    response = client.post("/workers", params={"name": name})
                    assert response.status_code == HTTPStatus.CREATED

                response = client.get("/workers")
                assert response.status_code == HTTPStatus.OK
                workers = [WorkerData.model_validate(j) for j in response.json()]

                assert len(workers) == len(names)

    @given(name=st.text())
    def test_worker_get(self, name: str) -> None:
        """Test worker get-endpoint."""
        with (
            TemporaryDirectory(delete=True) as data_dir,
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setenv(DATA_PATH_ENV, data_dir)
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                response = client.post("/workers", params={"name": name})
                assert response.status_code == HTTPStatus.CREATED
                worker = WorkerData.model_validate(response.json())

                response = client.get(f"/workers/{worker.wid}")
                assert response.status_code == HTTPStatus.OK
                worker_get = WorkerData.model_validate(response.json())

                assert worker_get == worker


class TestJobs:
    """Tests for job-related endpoints."""

    def test_create_job(self) -> None:
        """Test jobs creation."""
        with (
            TemporaryDirectory(delete=True) as test_dir,
            pytest.MonkeyPatch.context() as mp,
        ):
            directory = Path(test_dir)
            mp.setenv(DATA_PATH_ENV, test_dir)
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            job_definition, job_definition_path = copy_job_to_test_environment(
                directory=directory
            )
            archive_path = job_definition.create_archive(job_toml=job_definition_path)

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                response = client.post(
                    "/jobs",
                    data={"job_definition": TEST_JOB.model_dump_json()},
                    files={
                        "archive": ("job_archive.zip", archive_file, "application/zip")
                    },
                )
                assert response.status_code == HTTPStatus.CREATED
                job = Job.model_validate(response.json())
                assert job.name == TEST_JOB.name
                assert job.definition.params == TEST_JOB.params
