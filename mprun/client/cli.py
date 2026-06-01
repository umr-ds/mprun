#! /usr/bin/env python3

"""CLI client for interacting with the server."""

from lzma import LZMAError
from pathlib import Path

from httpx import HTTPStatusError, RequestError, codes
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from typer import Argument, Context, Exit, Option, Typer, echo

from mprun.client.client import (
    _client_factory,
    download_run_results,
    get_experiment_results_parallel,
    submit_experiment,
)
from mprun.client.tui import run_tui
from mprun.custom_types import RunId
from mprun.models import Experiment

console = Console()
client = Typer(no_args_is_help=False)


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
    for runs in experiment.runs:
        for run in runs:
            table.add_row(str(run.run_id), run.active_state, run.success_state)
    console.print(table)


@client.callback(invoke_without_command=True)
def _tui_entry(ctx: Context) -> None:
    """Launch interactive TUI when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        run_tui()
        raise Exit(0)


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
        with _client_factory(base_url) as http:
            resp = http.get("/experiments")
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
        with _client_factory(base_url) as http:
            resp = http.get(f"/experiments/{eid}")
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
    try:
        with _client_factory(base_url) as http:
            experiment, resp_text = submit_experiment(
                http_client=http, experiment_path=Path(experiment_file)
            )
    except (
        OSError,
        LZMAError,
        ValidationError,
    ) as err:
        echo(f"Failed to load experiment: {err}", err=True)
        raise Exit(1) from err
    except RequestError as err:
        echo(f"Server unreachable: {err}", err=True)
        raise Exit(1) from err
    except HTTPStatusError as err:
        _echo_create_experiment_http_error(err)
        raise Exit(1) from err

    if print_json:
        echo(resp_text)
    else:
        _print_experiment(experiment)


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
        echo(
            f"Invalid run ID {run_id!r}: expected {{eid}}-{{index}}-{{iteration}}",
            err=True,
        )
        raise Exit(1) from err

    try:
        with _client_factory(base_url) as http:
            dest = download_run_results(
                http_client=http,
                eid=rid.eid,
                index=rid.index,
                iteration=rid.iteration,
                output=output or Path(),
            )
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

            try:
                experiment = Experiment.model_validate(resp.json())
            except ValidationError as err:
                echo(f"Error validating response: {err}", err=True)
                raise Exit(1) from err

            saved, skipped, failed = get_experiment_results_parallel(
                http_client=http,
                experiment=experiment,
                out_dir=output or Path(),
            )
    except HTTPStatusError as err:
        if err.response.status_code == codes.NOT_FOUND:
            echo(f"No experiment with ID {eid}", err=True)
        else:
            echo(f"HTTP error {err.response.status_code}: {err}", err=True)
        raise Exit(1) from err

    for path in sorted(saved):
        echo(f"Saved: {path}")
    if skipped:
        echo(f"No results yet for run indices: {sorted(skipped)}")
    if failed:
        raise Exit(1)


if __name__ == "__main__":
    client()
