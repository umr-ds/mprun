"""Tests for client module."""

import asyncio
import json
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import mprun.client.cli
from mprun import SERVER_ADDRESS_ENV
from mprun.client.cli import client
from mprun.custom_types import ActiveState, SuccessState
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
    Run,
)
from mprun.server import DATA_PATH_ENV, server

runner = CliRunner()


@contextmanager
def _patched_client(tmp_path: Path) -> Iterator[TestClient]:
    """Patch ``_client_factory`` in the cli module to talk to a FastAPI server via ASGITransport."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(DATA_PATH_ENV, str(tmp_path / "server"))
        mp.setenv(SERVER_ADDRESS_ENV, "8086")
        transport = httpx.ASGITransport(app=server)
        async_client = httpx.AsyncClient(transport=transport, base_url="http://test")
        with TestClient(server) as test_client:

            def factory(
                _url: str | None,
            ) -> httpx.AsyncClient:
                return async_client

            mp.setattr(mprun.client.cli, "_client_factory", factory)
            yield test_client

        # Clean-up the async client.  Test functions are sync, so we cannot
        # ``await aclose()``.  Create a fresh, temporary event loop just for
        # closing.
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(async_client.aclose())
            loop.close()
        except Exception:  # noqa: BLE001
            pass


def _post_experiment(
    http_client: TestClient,
    definition: ExperimentDefinition,
    archive_path: Path,
) -> Experiment:
    """POST the experiment + archive directly and return the created ``Experiment``."""
    with archive_path.open("rb") as f:
        response = http_client.post(
            "/experiments",
            data={"experiment_definition": definition.model_dump_json()},
            files={"archive": (EXPERIMENT_ARCHIVE_NAME, f, "application/zip")},
        )
    response.raise_for_status()
    return Experiment.model_validate(response.json())


def _dispatch_and_submit(
    http_client: TestClient, results_payload: bytes = b"ok"
) -> Run:
    """Register a worker, dispatch a run, submit dummy results, return the run."""
    response = http_client.post("/workers", params={"name": "w"})
    response.raise_for_status()
    wid = response.json()["wid"]

    response = http_client.get("/runs/dispatch", params={"wid": wid})
    response.raise_for_status()
    run = Run.model_validate_json(response.headers["X-Run"], strict=True)
    run.active_state = ActiveState.FINISHED
    run.success_state = SuccessState.SUCCESS

    buf = BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("result.txt", results_payload)
    buf.seek(0)

    response = http_client.post(
        "/runs/result",
        params={"wid": wid},
        data={"run": run.model_dump_json()},
        files={"results_archive": ("results.zip", buf, "application/zip")},
    )
    response.raise_for_status()
    return run


def test_create_experiment(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """Client ``create`` command posts the definition + archive and exits 0."""
    _, directory = make_experiment()
    toml_path = directory / EXPERIMENT_DEFINITION_NAME

    with _patched_client(tmp_path):
        result = runner.invoke(client, ["create", str(toml_path)])
    assert result.exit_code == 0


def test_create_file_not_found(tmp_path: Path) -> None:
    """``create`` exits 1 when the TOML path does not exist."""
    result = runner.invoke(client, ["create", str(tmp_path / "missing.toml")])
    assert result.exit_code == 1


def test_create_invalid_toml(tmp_path: Path) -> None:
    """``create`` exits 1 when the TOML is missing required fields."""
    bad = tmp_path / "bad.toml"
    bad.write_text('name = "exp"\n')  # missing executable, params, results
    result = runner.invoke(client, ["create", str(bad)])
    assert result.exit_code == 1


def test_list_empty(tmp_path: Path) -> None:
    """``list`` succeeds when there are no experiments."""
    with _patched_client(tmp_path):
        result = runner.invoke(client, ["list"])
    assert result.exit_code == 0


def test_list_with_experiments(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """``list`` prints every created experiment by name."""
    with _patched_client(tmp_path) as http_client:
        for name in ("alpha", "beta"):
            definition, directory = make_experiment(name=name)
            archive_path = definition.create_archive(
                experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
            )
            _post_experiment(http_client, definition, archive_path)
        result = runner.invoke(client, ["list"])
    assert result.exit_code == 0
    assert "alpha" in result.output
    assert "beta" in result.output


def test_list_json(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """``list --json`` emits a parseable JSON array of experiments."""
    with _patched_client(tmp_path) as http_client:
        definition, directory = make_experiment(name="alpha")
        archive_path = definition.create_archive(
            experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
        )
        _post_experiment(http_client, definition, archive_path)
        result = runner.invoke(client, ["list", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert payload[0]["name"] == "alpha"


def test_get_happy(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """``get`` prints the requested experiment when it exists."""
    with _patched_client(tmp_path) as http_client:
        definition, directory = make_experiment(name="alpha")
        archive_path = definition.create_archive(
            experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
        )
        experiment = _post_experiment(http_client, definition, archive_path)
        result = runner.invoke(client, ["get", str(experiment.eid)])
    assert result.exit_code == 0
    assert "alpha" in result.output


def test_get_missing(tmp_path: Path) -> None:
    """``get`` exits 1 when no experiment with the given eid exists."""
    with _patched_client(tmp_path):
        result = runner.invoke(client, ["get", "99999"])
    assert result.exit_code == 1


def test_results_invalid_run_id_format(tmp_path: Path) -> None:
    """``results`` exits 1 on a malformed run ID."""
    with _patched_client(tmp_path):
        result = runner.invoke(client, ["results", "not-a-run-id"])
    assert result.exit_code == 1


def test_results_download(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """``results`` downloads a submitted results archive into the output directory."""
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    with _patched_client(tmp_path) as http_client:
        definition, directory = make_experiment(params={"x": [1]})  # one run
        archive_path = definition.create_archive(
            experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
        )
        _post_experiment(http_client, definition, archive_path)
        run = _dispatch_and_submit(http_client)

        result = runner.invoke(
            client,
            ["results", f"{run.eid}-{run.index}-{run.iteration}", "-o", str(out_dir)],
        )
    assert result.exit_code == 0
    saved = list(out_dir.iterdir())
    assert len(saved) == 1
    with zipfile.ZipFile(saved[0]) as zf:
        assert "result.txt" in zf.namelist()


def test_get_results_missing_experiment(tmp_path: Path) -> None:
    """``get-results`` exits 1 when the experiment does not exist."""
    with _patched_client(tmp_path):
        result = runner.invoke(client, ["get-results", "99999"])
    assert result.exit_code == 1
