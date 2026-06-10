#! /usr/bin/env python3

"""CLI client for interacting with the server."""

from datetime import UTC, datetime, timedelta
from lzma import LZMAError
from pathlib import Path

from httpx import HTTPStatusError, RequestError, codes
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from typer import Argument, Context, Exit, Option, Typer, confirm, echo

from mprun.client.client import (
    _client_factory,
    delete_experiment,
    download_run_results,
    get_experiment_results_parallel,
    reset_run,
    submit_experiment,
)
from mprun.client.tui import run_tui
from mprun.custom_types import RunId
from mprun.models import Experiment

console = Console()
client = Typer(no_args_is_help=False)


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
    table = Table("Name", "ID", "Active", "Success", "Created", "Runtime", "Runs")
    for experiment in experiments:
        table.add_row(
            experiment.name,
            str(experiment.eid),
            experiment.active_state,
            experiment.success_state,
            _fmt_ts(experiment.creation_timestamp),
            _fmt_duration(experiment.started_running, experiment.finished_running),
            str(sum(len(inner) for inner in experiment.runs)),
        )
    console.print(table)


def _print_experiment(experiment: Experiment) -> None:
    """Print a detailed view of a single experiment and its runs to the console."""
    total_runs = sum(len(inner) for inner in experiment.runs)

    table = Table(
        "Name",
        "ID",
        "Active",
        "Success",
        "Created",
        "Started",
        "Finished",
        "Runtime",
        "Runs",
        title="Experiment",
    )
    table.add_row(
        experiment.name,
        str(experiment.eid),
        experiment.active_state,
        experiment.success_state,
        _fmt_ts(experiment.creation_timestamp),
        _fmt_ts(experiment.started_running),
        _fmt_ts(experiment.finished_running),
        _fmt_duration(experiment.started_running, experiment.finished_running),
        str(total_runs),
    )
    console.print(table)

    table = Table(
        "ID",
        "Active",
        "Success",
        "Started",
        "Finished",
        "Runtime",
        "Worker",
        title="Runs",
    )
    for runs in experiment.runs:
        for run in runs:
            failure = (
                f" [red]({run.failure_reason})[/red]" if run.failure_reason else ""
            )
            table.add_row(
                str(run.run_id),
                run.active_state,
                f"{run.success_state}{failure}",
                _fmt_ts(run.started_running),
                _fmt_ts(run.finished_running),
                _fmt_duration(run.started_running, run.finished_running),
                str(run.wid) if run.wid is not None else "—",
            )
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


@client.command("delete", help="Delete an experiment and all its associated data.")
def delete_experiment_cmd(
    eid: int = Argument(help="ID of the experiment to delete."),
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
    yes: bool = Option(False, "-y", "--yes", help="Skip confirmation prompt."),
) -> None:
    """Delete an experiment and all its associated data."""
    if not yes:
        confirm(f"Delete experiment {eid}? This cannot be undone.", abort=True)

    try:
        with _client_factory(base_url) as http:
            delete_experiment(http_client=http, eid=eid)
    except HTTPStatusError as err:
        if err.response.status_code == codes.NOT_FOUND:
            echo(f"No experiment with ID {eid}", err=True)
        elif err.response.status_code == codes.INTERNAL_SERVER_ERROR:
            try:
                detail = err.response.json().get("detail", str(err))
            except Exception:  # noqa: BLE001
                detail = str(err)
            echo(f"Server error deleting experiment {eid}: {detail}", err=True)
        else:
            echo(f"HTTP error {err.response.status_code}: {err}", err=True)
        raise Exit(1) from err

    echo(f"Deleted experiment {eid}")


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


@client.command(
    "reset", help="Reset a run: delete results and return to WAITING state."
)
def reset_run_cmd(
    run_id: str = Argument(help="Run ID (e.g. 12345678-0-0)"),
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
) -> None:
    """Reset a run: delete its results and return it to WAITING state."""
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
            run = reset_run(
                http_client=http,
                rid=rid,
            )
    except HTTPStatusError as err:
        if err.response.status_code == codes.NOT_FOUND:
            echo(f"No run {run_id!r}", err=True)
        else:
            echo(f"HTTP error {err.response.status_code}: {err}", err=True)
        raise Exit(1) from err

    echo(f"Reset run {run_id!r} → {run.active_state} / {run.success_state}")


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
