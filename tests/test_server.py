"""Tests for server module."""

import zipfile
from collections.abc import Callable
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st

from mprun import SERVER_ADDRESS_ENV
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
    Run,
    WorkerData,
)
from mprun.server import DATA_PATH_ENV, server
from mprun.types import ActiveState, SuccessState


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

    def test_create_experiment(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """POST /experiments persists the definition and echoes it back."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            definition, directory = make_experiment()
            archive_path = definition.create_archive(
                experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
            )

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                response = client.post(
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
                assert response.status_code == HTTPStatus.CREATED
                experiment = Experiment.model_validate(response.json())
                assert experiment.name == definition.name
                assert experiment.definition.params == definition.params


class TestRuns:
    """Tests for Run-related endpoints."""

    def test_run_get(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """GET /runs/{eid}/{index} returns every run that the experiment expanded into."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            definition, directory = make_experiment()
            archive_path = definition.create_archive(
                experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
            )

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                response = client.post(
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
                experiment = Experiment.model_validate(response.json())

                for run in experiment.runs:
                    response = client.get(f"/runs/{run.eid}/{run.index}")
                    response.raise_for_status()
                    retrieved_run = Run.model_validate(response.json())
                    assert run == retrieved_run

    def test_run_dispatch(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """GET /runs/dispatch streams back the experiment's archive."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            definition, directory = make_experiment()
            archive_path = definition.create_archive(
                experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
            )

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                response = client.post(
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
                experiment = Experiment.model_validate(response.json())

                response = client.post("/workers", params={"name": "testworker"})
                response.raise_for_status()
                worker = WorkerData.model_validate(response.json())

                response = client.get("/runs/dispatch", params={"wid": worker.wid})
                response.raise_for_status()
                assert response.status_code == HTTPStatus.OK

                run = Run.model_validate_json(response.headers["X-Run"], strict=True)
                assert run.eid == experiment.eid

                downloaded_archive = tmp_path / "downloaded.zip"
                with downloaded_archive.open("wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)

                run.definition.validate_archive(archive_path=downloaded_archive)

    def test_run_results_submission(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """POST /runs/result persists submitted state and results archive."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            definition, directory = make_experiment()
            archive_path = definition.create_archive(
                experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
            )

            with TestClient(server) as client, archive_path.open("rb") as archive_file:
                response = client.post(
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

                response = client.post("/workers", params={"name": "testworker"})
                response.raise_for_status()
                worker = WorkerData.model_validate(response.json())

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
