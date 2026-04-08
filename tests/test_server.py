"""Tests for server module."""

from http import HTTPStatus
from os import putenv
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from mprun.models import Worker
from mprun.server import DATA_PATH_ENV, app


class TestWorkers:
    """Tests for worker-related endpoints."""

    @given(name=st.text())
    def test_woker_register(self, name: str) -> None:
        """Test worker registration."""
        with TemporaryDirectory(delete=True) as data_dir:
            putenv(DATA_PATH_ENV, data_dir)

            with TestClient(app) as client:
                response = client.post("/workers", params={"name": name})
                assert response.status_code == HTTPStatus.CREATED
                worker = Worker.model_validate(response.json())
                assert worker.name == name

    @given(names=st.lists(elements=st.text()))
    def test_worker_list(self, names: list[str]) -> None:
        """Test worker list-endpoint."""
        with TemporaryDirectory(delete=True) as data_dir:
            putenv(DATA_PATH_ENV, data_dir)

            with TestClient(app) as client:
                for name in names:
                    response = client.post("/workers", params={"name": name})
                    assert response.status_code == HTTPStatus.CREATED

                response = client.get("/workers")
                assert response.status_code == HTTPStatus.OK
                workers = [Worker.model_validate(j) for j in response.json()]

                assert len(workers) == len(names)

    @given(name=st.text())
    def test_worker_get(self, name: str) -> None:
        """Test worker get-endpoint."""
        with TemporaryDirectory(delete=True) as data_dir:
            putenv(DATA_PATH_ENV, data_dir)

            with TestClient(app) as client:
                response = client.post("/workers", params={"name": name})
                assert response.status_code == HTTPStatus.CREATED
                worker = Worker.model_validate(response.json())

                response = client.get(f"/workers/{worker.wid}")
                assert response.status_code == HTTPStatus.OK
                worker_get = Worker.model_validate(response.json())

                assert worker_get == worker
