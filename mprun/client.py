#! /usr/bin/env python3

"""Module contains client application."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from lzma import LZMAError
from pathlib import Path

from httpx import Client, HTTPStatusError, codes
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from typer import Argument, Exit, Option, Typer, echo

from mprun.models import Experiment, ExperimentDefinition, ValidationMode

console = Console()
client = Typer()


DEFAULT_URL = "http://localhost:8000"


def _default_client_factory(base_url: str | None) -> AbstractContextManager[Client]:
    return Client(base_url=base_url or DEFAULT_URL)


# allows us to inject different client for testing
_client_factory: Callable[[str | None], AbstractContextManager[Client]] = (
    _default_client_factory
)


def _print_experiments(experiments: list[Experiment]) -> None:
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
    table = Table("Name", "ID", "Active", "Success", title="Experiment")
    table.add_row(
        experiment.name,
        str(experiment.eid),
        experiment.active_state,
        experiment.success_state,
    )
    console.print(table)

    table = Table("Name", "Active", "Success", title="Runs")
    for run in experiment.runs:
        table.add_row(run.name, run.active_state, run.success_state)
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
    """Attempt to load the experiment definition & create the experiment archive.

    On error, produce an appropriate error message and quit.
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
    """Attempt to extract details from HTTP error and produce an appropriate error message."""
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


if __name__ == "__main__":
    client()
