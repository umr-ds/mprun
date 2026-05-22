"""Tests for server module."""

import zipfile
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from mprun import SERVER_ADDRESS_ENV
from mprun.models import Experiment, Run, WorkerData
from mprun.server import DATA_PATH_ENV, server
from mprun.types import ActiveState, SuccessState
from tests.helpers.experiment_helper import (
    TEST_EXPERIMENT,
    copy_experiment_to_test_environment,
)


class TestWorkers:
    """Tests for Worker-related endpoints."""

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


class TestExperiments:
    """Tests for Experiment-related endpoints."""

    def test_create_experiment(self) -> None:
        """Test experiment creation."""
        with (
            TemporaryDirectory(delete=True) as test_dir,
            pytest.MonkeyPatch.context() as mp,
        ):
            directory = Path(test_dir)
            mp.setenv(DATA_PATH_ENV, test_dir)
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            experiment_definition, experiment_definition_path = (
                copy_experiment_to_test_environment(directory=directory)
            )
            archive_path = experiment_definition.create_archive(
                experiment_toml=experiment_definition_path
            )

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                response = client.post(
                    "/experiments",
                    data={"experiment_definition": TEST_EXPERIMENT.model_dump_json()},
                    files={
                        "archive": (
                            "experiment_archive.zip",
                            archive_file,
                            "application/zip",
                        )
                    },
                )
                assert response.status_code == HTTPStatus.CREATED
                experiment = Experiment.model_validate(response.json())
                assert experiment.name == TEST_EXPERIMENT.name
                assert experiment.definition.params == TEST_EXPERIMENT.params


class TestRuns:
    """Tests for Run-related endpoints."""

    def test_run_get(self) -> None:
        """Test '/runs/{rid}' endpoint."""
        with (
            TemporaryDirectory(delete=True) as test_dir,
            pytest.MonkeyPatch.context() as mp,
        ):
            directory = Path(test_dir)
            mp.setenv(DATA_PATH_ENV, test_dir)
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            experiment_definition, experiment_definition_path = (
                copy_experiment_to_test_environment(directory=directory)
            )
            archive_path = experiment_definition.create_archive(
                experiment_toml=experiment_definition_path
            )

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                # create experiment
                response = client.post(
                    "/experiments",
                    data={"experiment_definition": TEST_EXPERIMENT.model_dump_json()},
                    files={
                        "archive": (
                            "experiment_archive.zip",
                            archive_file,
                            "application/zip",
                        )
                    },
                )
                response.raise_for_status()
                experiment = Experiment.model_validate(response.json())

                for run in experiment.runs:
                    response = client.get(f"/runs/{run.eid}/{run.index}")
                    response.raise_for_status()
                    retrieved_run = Run.model_validate(response.json())
                    assert run == retrieved_run

    def test_run_dispatch(self) -> None:
        """Test '/runs/dispatch' endpoint."""
        with (
            TemporaryDirectory(delete=True) as test_dir,
            pytest.MonkeyPatch.context() as mp,
        ):
            directory = Path(test_dir)
            mp.setenv(DATA_PATH_ENV, test_dir)
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            experiment_definition, experiment_definition_path = (
                copy_experiment_to_test_environment(directory=directory)
            )
            archive_path = experiment_definition.create_archive(
                experiment_toml=experiment_definition_path
            )

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                # create experiment
                response = client.post(
                    "/experiments",
                    data={"experiment_definition": TEST_EXPERIMENT.model_dump_json()},
                    files={
                        "archive": (
                            "experiment_archive.zip",
                            archive_file,
                            "application/zip",
                        )
                    },
                )
                response.raise_for_status()
                experiment = Experiment.model_validate(response.json())

                # register dummy worker
                response = client.post("/workers", params={"name": "testworker"})
                response.raise_for_status()
                worker = WorkerData.model_validate(response.json())

                # attempt to dispatch a run to this worker
                response = client.get("/runs/dispatch", params={"wid": worker.wid})
                response.raise_for_status()
                assert response.status_code == HTTPStatus.OK

                run = Run.model_validate_json(response.headers["X-Run"], strict=True)
                assert run.eid == experiment.eid

                archive_path = directory / "test_archive.zip"
                with archive_path.open("wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)

                run.definition.validate_archive(archive_path=archive_path)

    def test_run_results_submission(self) -> None:
        """Test '/runs/result' endpoint."""
        with (
            TemporaryDirectory(delete=True) as test_dir,
            pytest.MonkeyPatch.context() as mp,
        ):
            directory = Path(test_dir)
            mp.setenv(DATA_PATH_ENV, test_dir)
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            experiment_definition, experiment_definition_path = (
                copy_experiment_to_test_environment(directory=directory)
            )
            archive_path = experiment_definition.create_archive(
                experiment_toml=experiment_definition_path
            )

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                # create experiment
                response = client.post(
                    "/experiments",
                    data={"experiment_definition": TEST_EXPERIMENT.model_dump_json()},
                    files={
                        "archive": (
                            "experiment_archive.zip",
                            archive_file,
                            "application/zip",
                        )
                    },
                )
                response.raise_for_status()

                # register dummy worker
                response = client.post("/workers", params={"name": "testworker"})
                response.raise_for_status()
                worker = WorkerData.model_validate(response.json())

                # attempt to dispatch a run to this worker
                response = client.get("/runs/dispatch", params={"wid": worker.wid})
                response.raise_for_status()

                run = Run.model_validate_json(response.headers["X-Run"], strict=True)

                run.active_state = ActiveState.FINISHED
                run.success_state = SuccessState.SUCCESS

                buf = BytesIO()
                with zipfile.ZipFile(buf, mode="w") as zf:
                    zf.writestr("result.txt", "ok")
                buf.seek(0)

                response = client.post(
                    "/runs/result",
                    params={"wid": worker.wid},
                    data={"run": run.model_dump_json()},
                    files={"results_archive": ("results.zip", buf, "application/zip")},
                )
                assert response.status_code == HTTPStatus.OK

                response = client.get(f"/runs/{run.eid}/{run.index}")
                response.raise_for_status()
                submitted_run = Run.model_validate(response.json())
                assert submitted_run.active_state == ActiveState.FINISHED
                assert submitted_run.success_state == SuccessState.SUCCESS
