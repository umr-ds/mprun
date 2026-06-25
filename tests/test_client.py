"""Tests for client helper functions."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from mprun import SERVER_ADDRESS_ENV
from mprun.client.client import delete_experiment, purge_dead_workers, reset_run
from mprun.custom_types import ActiveState, RunId, SuccessState, WorkerBackend
from mprun.models import (
    EXPERIMENT_ARCHIVE_NAME,
    EXPERIMENT_DEFINITION_NAME,
    Experiment,
    ExperimentDefinition,
    WorkerRegistration,
)
from mprun.server.server import server
from mprun.worker_manager import WORKER_TIMEOUT
from tests.conftest import configure_server_for_test


@asynccontextmanager
async def _patched_async_client(
    tmp_path: Path,
) -> AsyncIterator[tuple[TestClient, httpx.AsyncClient]]:
    """Set up a server and return both a sync TestClient and an async HTTP client."""
    with pytest.MonkeyPatch.context() as mp:
        configure_server_for_test(tmp_path / "server")
        mp.setenv(SERVER_ADDRESS_ENV, "8086")
        transport = httpx.ASGITransport(app=server)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as async_client:
            with TestClient(server) as test_client:
                yield test_client, async_client


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


@pytest.mark.asyncio
async def test_delete_experiment(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """delete_experiment() removes the experiment from the server."""
    async with _patched_async_client(tmp_path) as (tc, async_client):
        definition, directory = make_experiment(name="alpha")
        archive_path = definition.create_archive(
            experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
        )
        experiment = _post_experiment(tc, definition, archive_path)

        await delete_experiment(async_client, experiment.eid)

        response = tc.get(f"/experiments/{experiment.eid}")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_experiment_missing(tmp_path: Path) -> None:
    """delete_experiment() raises HTTPStatusError for an unknown eid."""
    async with _patched_async_client(tmp_path) as (_tc, async_client):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await delete_experiment(async_client, 99999)
        assert exc_info.value.response.status_code == 404


@pytest.mark.asyncio
async def test_purge_dead_workers(tmp_path: Path) -> None:
    """purge_dead_workers() removes dead workers from the server."""
    async with _patched_async_client(tmp_path) as (tc, async_client):
        registration_data = WorkerRegistration(
            name="doomed", backends={WorkerBackend.NATIVE}
        )
        response = tc.post(
            "/workers", data={"registration": registration_data.model_dump_json()}
        )
        response.raise_for_status()
        worker = response.json()

        app = cast(Any, tc.app)
        wm = app.state.worker_manager
        with patch("mprun.worker_manager.time") as mock_time:
            mock_time.return_value = worker["last_check_in"] + WORKER_TIMEOUT + 1
            await wm._collect_garbage()

        await purge_dead_workers(async_client)

        response = tc.get("/workers")
        response.raise_for_status()
        workers = response.json()
        assert not any(w["wid"] == worker["wid"] for w in workers)


@pytest.mark.asyncio
async def test_purge_dead_workers_empty(tmp_path: Path) -> None:
    """purge_dead_workers() succeeds with no dead workers."""
    async with _patched_async_client(tmp_path) as (_tc, async_client):
        await purge_dead_workers(async_client)


@pytest.mark.asyncio
async def test_reset_run(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """reset_run() returns a run to WAITING / PENDING state."""
    async with _patched_async_client(tmp_path) as (tc, async_client):
        definition, directory = make_experiment(params={"x": [1]})
        archive_path = definition.create_archive(
            experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
        )
        experiment = _post_experiment(tc, definition, archive_path)

        run = experiment.runs[0][0]
        rid = RunId(eid=run.eid, index=run.index, iteration=run.iteration)

        reset = await reset_run(async_client, rid)
        assert reset.active_state == ActiveState.WAITING
        assert reset.success_state == SuccessState.PENDING
        assert reset.wid is None
        assert reset.failure_reason is None


@pytest.mark.asyncio
async def test_reset_run_missing(
    tmp_path: Path,
    make_experiment: Callable[..., tuple[ExperimentDefinition, Path]],
) -> None:
    """reset_run() raises HTTPStatusError for an unknown run."""
    async with _patched_async_client(tmp_path) as (tc, async_client):
        definition, directory = make_experiment(params={"x": [1]})
        archive_path = definition.create_archive(
            experiment_toml=directory / EXPERIMENT_DEFINITION_NAME
        )
        _post_experiment(tc, definition, archive_path)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await reset_run(async_client, RunId(eid=99999, index=0, iteration=0))
        assert exc_info.value.response.status_code == 404
