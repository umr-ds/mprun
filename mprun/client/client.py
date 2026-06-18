"""HTTP client helpers shared across CLI and TUI."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient, HTTPStatusError, codes
from typer import echo

from mprun.custom_types import RunId
from mprun.models import Experiment, ExperimentDefinition, Run, ValidationMode

DEFAULT_URL = "http://localhost:8000"


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    return (
        datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    )


def _fmt_duration(started: float | None, finished: float | None) -> str:
    if started is None or finished is None:
        return "—"
    delta = finished - started
    if delta < 0:
        return "—"
    return str(timedelta(seconds=int(delta)))


def _default_client_factory(
    base_url: str | None,
) -> AbstractAsyncContextManager[AsyncClient]:
    """Return an ``httpx.AsyncClient`` configured with ``base_url``, falling back to ``DEFAULT_URL``."""
    return AsyncClient(base_url=base_url or DEFAULT_URL)


# allows us to inject different client for testing
_client_factory: Callable[[str | None], AbstractAsyncContextManager[AsyncClient]] = (
    _default_client_factory
)


def load_experiment_archive(
    experiment_path: Path,
) -> tuple[ExperimentDefinition, Path]:
    """Load an experiment definition from a TOML file and create its ZIP archive.

    Args:
        experiment_path (Path): Path to the experiment's TOML definition file.

    Returns:
        tuple[ExperimentDefinition, Path]: The parsed definition and the path to the
            created archive.

    Raises:
        FileNotFoundError: If any referenced file does not exist.
        PermissionError: If any referenced file cannot be accessed.
        OSError: If any file cannot be read.
        ValidationError: If the definition fails schema validation.
        LZMAError: If archive compression fails.
    """
    definition = ExperimentDefinition.load_toml(
        file_path=experiment_path, validation_mode=ValidationMode.DATA_AND_FILES
    )
    archive_path = definition.create_archive(experiment_toml=experiment_path)
    return definition, archive_path


async def download_run_results(
    http_client: AsyncClient, eid: int, index: int, iteration: int, output: Path
) -> Path | None:
    """Download the results archive for a single run and write it to disk.

    Args:
        http_client (AsyncClient): HTTP client to use for the request.
        eid (int): Experiment ID.
        index (int): Run index within the experiment.
        iteration (int): Iteration within an argument set.
        output (Path): Directory to write the results archive into.

    Returns:
        Path | None: Path to the saved archive, or ``None`` if no results are available yet
            (server returned 404).

    Raises:
        HTTPStatusError: If the server returns any non-2xx response other than 404.
    """
    try:
        resp = await http_client.get(f"/runs/{eid}/{index}/{iteration}/results")
        resp.raise_for_status()
    except HTTPStatusError as err:
        if err.response.status_code == codes.NOT_FOUND:
            return None
        raise

    disposition = resp.headers.get("content-disposition", "")
    filename = f"results_{eid}_{index}.zip"
    for raw_part in disposition.split(";"):
        stripped = raw_part.strip()
        if stripped.startswith("filename="):
            filename = stripped.removeprefix("filename=").strip('"')
            break

    dest = output / filename
    dest.write_bytes(resp.content)
    return dest


async def get_experiment_results(
    http_client: AsyncClient,
    experiment: Experiment,
    out_dir: Path,
) -> tuple[list[Path], list[int], bool]:
    """Download results for all runs in an experiment concurrently.

    Args:
        http_client (AsyncClient): HTTP client to use for all requests.
        experiment (Experiment): The experiment whose runs to download.
        out_dir (Path): Directory to save archives into.

    Returns:
        tuple[list[Path], list[int], bool]: Saved paths, skipped run indices, and
            whether any download failed.
    """

    async def _download_one(run: Run) -> tuple[Path | None, int, bool]:
        try:
            result = await download_run_results(
                http_client=http_client,
                eid=run.eid,
                index=run.index,
                iteration=run.iteration,
                output=out_dir,
            )
        except HTTPStatusError as err:
            echo(f"HTTP error downloading run {run.index}: {err}", err=True)
            return None, run.index, True
        return result, run.index, False

    tasks = [_download_one(run) for runs in experiment.runs for run in runs]
    results = await asyncio.gather(*tasks)

    saved: list[Path] = []
    skipped: list[int] = []
    failed = False

    for result, run_index, errored in results:
        if errored:
            failed = True
        elif result is None:
            skipped.append(run_index)
        else:
            saved.append(result)

    return saved, skipped, failed


async def submit_experiment(
    http_client: AsyncClient,
    experiment_path: Path,
) -> tuple[Experiment, str]:
    """Load, archive, and POST an experiment definition to the server.

    Checks that ``experiment_path`` exists, loads and archives the definition,
    then POSTs both to the server.

    Args:
        http_client (AsyncClient): HTTP client to use for the request.
        experiment_path (Path): Path to the experiment TOML definition file.

    Returns:
        tuple[Experiment, str]: The created experiment and the raw response JSON text.

    Raises:
        FileNotFoundError: If ``experiment_path`` does not exist or a referenced file is missing.
        PermissionError: If any file cannot be accessed.
        OSError: If any file cannot be read.
        ValidationError: If the definition or the response body fails schema validation.
        LZMAError: If archive compression fails.
        RequestError: If the server is unreachable or the request fails at the transport level.
        HTTPStatusError: If the server returns a non-2xx response.
    """
    if not experiment_path.exists():  # noqa: ASYNC240
        raise FileNotFoundError(experiment_path)

    definition, archive_path = load_experiment_archive(experiment_path)

    with archive_path.open("rb") as archive_file:
        resp = await http_client.post(
            "/experiments",
            data={"experiment_definition": definition.model_dump_json()},
            files={
                "archive": (
                    "experiment_archive.zip",
                    archive_file,
                    "application/zip",
                )
            },
        )
        resp.raise_for_status()
    return Experiment.model_validate(resp.json()), resp.text


async def purge_dead_workers(
    http_client: AsyncClient,
) -> None:
    """Purge all dead workers from the server.

    Args:
        http_client (AsyncClient): HTTP client to use for the request.

    Raises:
        HTTPStatusError: If the server returns a non-2xx response.
    """
    resp = await http_client.delete("/workers/dead")
    resp.raise_for_status()


async def delete_experiment(
    http_client: AsyncClient,
    eid: int,
) -> None:
    """Delete an experiment by ID.

    Args:
        http_client (AsyncClient): HTTP client to use for the request.
        eid (int): ID of the experiment to delete.

    Raises:
        HTTPStatusError: If the server returns a non-2xx response (e.g. 404 if not found,
            500 if the server fails to remove the experiment data from disk).
    """
    resp = await http_client.delete(f"/experiments/{eid}")
    resp.raise_for_status()


async def reset_run(
    http_client: AsyncClient,
    rid: RunId,
) -> Run:
    """Reset a run: delete its results and return it to WAITING state.

    Args:
        http_client (AsyncClient): HTTP client to use for the request.
        rid (RunId): Composite run identity.

    Returns:
        Run: The reset run with WAITING/PENDING state.

    Raises:
        HTTPStatusError: If the server returns a non-2xx response (e.g. 404 if not found).
    """
    resp = await http_client.post(f"/runs/{rid.eid}/{rid.index}/{rid.iteration}/reset")
    resp.raise_for_status()
    return Run.model_validate(resp.json())
