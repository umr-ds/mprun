#! /usr/bin/env python3

"""CLI client for interacting with the server."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from lzma import LZMAError
from pathlib import Path

from httpx import Client, HTTPStatusError, codes
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from typer import Argument, Exit, Option, Typer, echo

from mprun.custom_types import RunId
from mprun.models import Experiment, ExperimentDefinition, ValidationMode

console = Console()
client = Typer()


DEFAULT_URL = "http://localhost:8000"


def _default_client_factory(base_url: str | None) -> AbstractContextManager[Client]:
    """Return an instance of httpx.Client configured with ``base_url``, falling back to ``DEFAULT_URL``."""
    return Client(base_url=base_url or DEFAULT_URL)


# allows us to inject different client for testing
_client_factory: Callable[[str | None], AbstractContextManager[Client]] = (
    _default_client_factory
)


def _print_experiments(experiments: list[Experiment]) -> None:
    """Print a summary table of multiple experiments to the console."""
    table = Table("Name", "ID", "Active", "Success")
    for experiment in experiments:
        table.add_row(
            experiment.name,
            str(experiment.eid),
            experiment.active_state,
            experiment.success_state,
        )
    console.print(table)


def _print_experiment(experiment: Experiment) -> None:
    """Print a detailed view of a single experiment and its runs to the console."""
    table = Table("Name", "ID", "Active", "Success", title="Experiment")
    table.add_row(
        experiment.name,
        str(experiment.eid),
        experiment.active_state,
        experiment.success_state,
    )
    console.print(table)

    table = Table("ID", "Active", "Success", title="Runs")
    for run in experiment.runs:
        table.add_row(str(run.run_id), run.active_state, run.success_state)
    console.print(table)


@client.command("list", help="Get list of all experiments")
def list_experiments(
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Get list of all experiments."""
    try:
        with _client_factory(base_url) as client:
            resp = client.get("/experiments")
    except HTTPStatusError as err:
        echo(f"HTTP Error: {err}", err=True)
        raise Exit(1) from err

    try:
        experiments = [Experiment.model_validate(j) for j in resp.json()]

        if print_json:
            echo(resp.text)
        else:
            _print_experiments(experiments)
        raise Exit(0)
    except ValidationError as err:
        echo(f"Error validating response: {err}", err=True)
        raise Exit(1) from err


@client.command("get", help="Get specific experiment by its ID")
def get_experiment(
    eid: int = Argument(
        help="Experiment's ID (use List command to get all experiments and their IDs)"
    ),
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Get specific experiment by its ID."""
    try:
        with _client_factory(base_url) as client:
            resp = client.get(f"/experiments/{eid}")
    except HTTPStatusError as err:
        if err.response.status_code == codes.NOT_FOUND:
            echo(f"No experiment with ID {eid}", err=True)
        else:
            echo(f"HTTP Error: {err}", err=True)
        raise Exit(1) from err

    experiment: Experiment
    try:
        experiment = Experiment.model_validate(resp.json())
    except ValidationError as err:
        echo(f"Error validating response: {err}", err=True)
        raise Exit(1) from err

    if print_json:
        echo(resp.text)
    else:
        _print_experiment(experiment)


def _load_experiment_archive(
    experiment_path: Path,
) -> tuple[ExperimentDefinition, Path]:
    """Load an experiment definition from a TOML file and create its ZIP archive.

    On any error, prints a message to stderr and raises ``Exit(1)``.

    Args:
        experiment_path (Path): Path to the experiment's TOML definition file.

    Returns:
        tuple[ExperimentDefinition, Path]: The parsed definition and the path to the
            created archive.
    """
    try:
        definition = ExperimentDefinition.load_toml(
            file_path=experiment_path, validation_mode=ValidationMode.DATA_AND_FILES
        )
        archive_path = definition.create_archive(experiment_toml=experiment_path)
    except FileNotFoundError as err:
        echo(f"Error accessing file: {err}", err=True)
        raise Exit(1) from err
    except PermissionError as err:
        echo(f"Error accessing file: {err}", err=True)
        raise Exit(1) from err
    except OSError as err:
        echo(f"Error reading file: {err}", err=True)
        raise Exit(1) from err
    except ValidationError as err:
        echo(f"Error validating experiment definition: {err}", err=True)
        raise Exit(1) from err
    except LZMAError as err:
        echo(f"Error compressing archive: {err}", err=True)
        raise Exit(1) from err
    return definition, archive_path


def _echo_create_experiment_http_error(err: HTTPStatusError) -> None:
    """Print a human-readable error message to stderr for an HTTP error from the create endpoint.

    Extracts the ``detail`` field from the JSON response body when available, and maps
    known HTTP status codes to specific messages.

    Args:
        err (HTTPStatusError): The HTTP error raised by the create experiment request.
    """
    try:
        detail = err.response.json().get("detail", str(err))
    except Exception:  # noqa: BLE001
        detail = str(err)
    status = err.response.status_code
    if status == codes.BAD_REQUEST:
        echo(f"Invalid experiment parameters: {detail}", err=True)
    elif status == codes.UNPROCESSABLE_ENTITY:
        echo(f"Invalid experiment definition or archive: {detail}", err=True)
    elif status == codes.INTERNAL_SERVER_ERROR:
        echo(f"Server error: {detail}", err=True)
    else:
        echo(f"HTTP error {status}: {detail}", err=True)


@client.command("create", help="Create a new experiment from an experiment definition.")
def create_experiment(
    experiment_file: str = Argument(help="Path to experiment definition"),
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Create a new experiment from an experiment definition."""
    experiment_path = Path(experiment_file)
    if not experiment_path.exists():
        echo(f"File {experiment_path} does not exist.", err=True)
        raise Exit(1)

    experiment_definition, archive_path = _load_experiment_archive(experiment_path)

    echo(f"Creating experiment with name {experiment_definition.name}")

    try:
        with (
            archive_path.open("rb") as archive_file,
            _client_factory(base_url) as client,
        ):
            resp = client.post(
                "/experiments",
                data={"experiment_definition": experiment_definition.model_dump_json()},
                files={
                    "archive": (
                        "experiment_archive.zip",
                        archive_file,
                        "application/zip",
                    )
                },
            ).raise_for_status()
    except HTTPStatusError as err:
        _echo_create_experiment_http_error(err)
        raise Exit(1) from err

    experiment: Experiment
    try:
        experiment = Experiment.model_validate(resp.json())
    except ValidationError as err:
        echo(f"Error validating response: {err}", err=True)
        raise Exit(1) from err

    if print_json:
        echo(resp.text)
    else:
        _print_experiment(experiment)


def _download_run_results(
    base_url: str | None, eid: int, index: int, output: Path
) -> Path | None:
    """Download the results archive for a single run and write it to disk.

    Args:
        base_url (str | None): Server base URL, or ``None`` to use ``DEFAULT_URL``.
        eid (int): Experiment ID.
        index (int): Run index within the experiment.
        output (Path): Directory to write the results archive into.

    Returns:
        Path | None: Path to the saved archive, or ``None`` if no results are available yet
            (server returned 404).

    Raises:
        HTTPStatusError: If the server returns any non-2xx response other than 404.
    """
    with _client_factory(base_url) as http:
        try:
            resp = http.get(f"/runs/{eid}/{index}/results").raise_for_status()
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


@client.command("results", help="Download results archive for a specific run.")
def get_run_results(
    run_id: str = Argument(help="Run ID"),
    output: Path | None = Option(
        None,
        "-o",
        "--output",
        help="Directory to save the results archive to. Defaults to current directory.",
        file_okay=False,
        dir_okay=True,
        writable=True,
    ),
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
) -> None:
    """Download results archive for a specific run."""
    try:
        rid = RunId.from_str(run_id)
    except ValueError as err:
        echo(f"Invalid run ID {run_id!r}: expected {{eid}}-{{index}}", err=True)
        raise Exit(1) from err

    try:
        dest = _download_run_results(base_url, rid.eid, rid.index, output or Path())
    except HTTPStatusError as err:
        if err.response.status_code == codes.NOT_FOUND:
            echo(f"No results for run {run_id!r}", err=True)
        else:
            echo(f"HTTP error {err.response.status_code}: {err}", err=True)
        raise Exit(1) from err

    if dest is None:
        echo(f"No results for run {run_id!r}", err=True)
        raise Exit(1)
    echo(f"Saved to {dest}")


@client.command("get-results", help="Download results for all runs in an experiment.")
def get_experiment_results(
    eid: int = Argument(help="Experiment ID"),
    output: Path | None = Option(
        None,
        "-o",
        "--output",
        help="Directory to save results to. Defaults to current directory.",
        file_okay=False,
        dir_okay=True,
        writable=True,
    ),
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
) -> None:
    """Download results for all runs in an experiment in parallel."""
    try:
        with _client_factory(base_url) as http:
            resp = http.get(f"/experiments/{eid}").raise_for_status()
    except HTTPStatusError as err:
        if err.response.status_code == codes.NOT_FOUND:
            echo(f"No experiment with ID {eid}", err=True)
        else:
            echo(f"HTTP error {err.response.status_code}: {err}", err=True)
        raise Exit(1) from err

    try:
        experiment = Experiment.model_validate(resp.json())
    except ValidationError as err:
        echo(f"Error validating response: {err}", err=True)
        raise Exit(1) from err

    out_dir = output or Path()
    saved: list[Path] = []
    skipped: list[int] = []
    failed = False

    with ThreadPoolExecutor() as pool:
        futures = {
            pool.submit(
                _download_run_results, base_url, run.eid, run.index, out_dir
            ): run.index
            for run in experiment.runs
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                result = future.result()
            except HTTPStatusError as err:
                echo(f"HTTP error downloading run {index}: {err}", err=True)
                failed = True
                continue
            if result is None:
                skipped.append(index)
            else:
                saved.append(result)

    for path in sorted(saved):
        echo(f"Saved: {path}")
    if skipped:
        echo(f"No results yet for run indices: {sorted(skipped)}")
    if failed:
        raise Exit(1)


if __name__ == "__main__":
    client()
