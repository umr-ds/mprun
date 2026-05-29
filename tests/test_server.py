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
from mprun.custom_types import ActiveState, SuccessState
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
    Run,
    WorkerData,
)
from mprun.server import DATA_PATH_ENV, server


def _post_experiment(
    client: TestClient,
    definition: ExperimentDefinition,
    archive_path: Path,
) -> Experiment:
    """POST the experiment to the server and return the created ``Experiment``."""
    with archive_path.open("rb") as archive_file:
        response = client.post(
            "/experiments",
            data={"experiment_definition": definition.model_dump_json()},
            files={
                "archive": (EXPERIMENT_ARCHIVE_NAME, archive_file, "application/zip"),
            },
        )
    response.raise_for_status()
    return Experiment.model_validate(response.json())


def _register_worker(client: TestClient, name: str = "testworker") -> WorkerData:
    """Register a worker and return its data."""
    response = client.post("/workers", params={"name": name})
    response.raise_for_status()
    return WorkerData.model_validate(response.json())


def _build_archive(definition: ExperimentDefinition, directory: Path) -> Path:
    """Build the experiment archive under ``directory`` and return its path."""
    return definition.create_archive(
        experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
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

    def test_worker_get_missing(self, tmp_path: Path) -> None:
        """GET /workers/{wid} returns 404 for an unknown worker."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                response = client.get("/workers/99999")
                assert response.status_code == HTTPStatus.NOT_FOUND

    def test_worker_check_in(self, tmp_path: Path) -> None:
        """POST /workers/check_in/{wid} returns 200 for known and 404 for unknown."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                worker = _register_worker(client)

                response = client.post(f"/workers/check_in/{worker.wid}")
                assert response.status_code == HTTPStatus.OK

                response = client.post("/workers/check_in/99999")
                assert response.status_code == HTTPStatus.NOT_FOUND


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

    def test_create_invalid_definition(self, tmp_path: Path) -> None:
        """POST /experiments with malformed definition JSON returns 422."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                response = client.post(
                    "/experiments",
                    data={"experiment_definition": "{not valid json"},
                    files={"archive": ("a.zip", b"\x00", "application/zip")},
                )
                assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

    def test_list_experiments(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """GET /experiments returns every created experiment."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                response = client.get("/experiments")
                response.raise_for_status()
                assert response.json() == []

                created: list[Experiment] = []
                for name in ("first", "second"):
                    definition, directory = make_experiment(name=name)
                    created.append(
                        _post_experiment(
                            client, definition, _build_archive(definition, directory)
                        )
                    )

                response = client.get("/experiments")
                response.raise_for_status()
                listed = [Experiment.model_validate(e) for e in response.json()]
                assert {e.eid for e in listed} == {e.eid for e in created}

    def test_get_experiment_missing(self, tmp_path: Path) -> None:
        """GET /experiments/{eid} returns 404 for an unknown experiment."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                response = client.get("/experiments/99999")
                assert response.status_code == HTTPStatus.NOT_FOUND


class TestRuns:
    """Tests for Run-related endpoints."""

    def test_run_get(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """GET /runs/{eid}/{index}/{iteration} returns every run that the experiment expanded into."""
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

                for runs in experiment.runs:
                    for run in runs:
                        response = client.get(
                            f"/runs/{run.eid}/{run.index}/{run.iteration}"
                        )
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

                response = client.get(f"/runs/{run.eid}/{run.index}/{run.iteration}")
                response.raise_for_status()
                submitted_run = Run.model_validate(response.json())
                assert submitted_run.active_state == ActiveState.FINISHED
                assert submitted_run.success_state == SuccessState.SUCCESS

    def test_run_get_missing(self, tmp_path: Path) -> None:
        """GET /runs/{eid}/{index} returns 404 for an unknown run."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                response = client.get("/runs/99999/0")
                assert response.status_code == HTTPStatus.NOT_FOUND

    def test_dispatch_no_content_when_no_waiting_runs(
        self,
        tmp_path: Path,
    ) -> None:
        """GET /runs/dispatch returns 204 when no experiments have waiting runs."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            with TestClient(server) as client:
                worker = _register_worker(client)
                response = client.get("/runs/dispatch", params={"wid": worker.wid})
                assert response.status_code == HTTPStatus.NO_CONTENT

    def test_dispatch_unknown_worker(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """GET /runs/dispatch with an unknown wid returns 404."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            definition, directory = make_experiment()
            archive_path = _build_archive(definition, directory)

            with TestClient(server) as client:
                _post_experiment(client, definition, archive_path)
                response = client.get("/runs/dispatch", params={"wid": 99999})
                assert response.status_code == HTTPStatus.NOT_FOUND

    def test_results_unknown_worker(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """POST /runs/result with an unknown wid returns 404."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            definition, directory = make_experiment()
            archive_path = _build_archive(definition, directory)

            with TestClient(server) as client:
                _post_experiment(client, definition, archive_path)
                worker = _register_worker(client)

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
                    params={"wid": 99999},
                    data={"run": run.model_dump_json()},
                    files={"results_archive": ("results.zip", buf, "application/zip")},
                )
                assert response.status_code == HTTPStatus.NOT_FOUND

    def test_run_results_download(
        self,
        tmp_path: Path,
        make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
    ) -> None:
        """GET /runs/{eid}/{index}/results returns the archive after submission, 404 before."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
            mp.setenv(SERVER_ADDRESS_ENV, "8086")

            definition, directory = make_experiment()
            archive_path = _build_archive(definition, directory)

            with TestClient(server) as client:
                experiment = _post_experiment(client, definition, archive_path)
                worker = _register_worker(client)

                # before submission: 404
                run = experiment.runs[0][0]
                response = client.get(
                    f"/runs/{run.eid}/{run.index}/{run.iteration}/results"
                )
                assert response.status_code == HTTPStatus.NOT_FOUND

                # dispatch + submit
                response = client.get("/runs/dispatch", params={"wid": worker.wid})
                response.raise_for_status()
                dispatched_run = Run.model_validate_json(
                    response.headers["X-Run"], strict=True
                )
                dispatched_run.active_state = ActiveState.FINISHED
                dispatched_run.success_state = SuccessState.SUCCESS

                buf = BytesIO()
                with zipfile.ZipFile(buf, mode="w") as zf:
                    zf.writestr("result.txt", "ok")
                buf.seek(0)

                response = client.post(
                    "/runs/result",
                    params={"wid": worker.wid},
                    data={"run": dispatched_run.model_dump_json()},
                    files={"results_archive": ("results.zip", buf, "application/zip")},
                )
                response.raise_for_status()

                # after submission: 200 with archive contents
                response = client.get(
                    f"/runs/{dispatched_run.eid}/{dispatched_run.index}/{dispatched_run.iteration}/results"
                )
                assert response.status_code == HTTPStatus.OK
                assert response.headers["content-type"] == "application/zip"
                with zipfile.ZipFile(BytesIO(response.content)) as zf:
                    assert "result.txt" in zf.namelist()
