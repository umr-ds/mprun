#! /usr/bin/env python3

"""CLI client for interacting with the server."""

import asyncio
import functools
import os
from collections.abc import Callable
from lzma import LZMAError
from pathlib import Path
from typing import Any

from httpx import HTTPStatusError, RequestError, codes
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from typer import Argument, Context, Exit, Option, Typer, confirm, echo

from mprun import SERVER_ADDRESS_ENV
from mprun.client.client import (
    DEFAULT_URL,
    _client_factory,
    _fmt_duration,
    _fmt_ts,
    delete_experiment,
    download_run_results,
    get_experiment_results,
    purge_dead_workers,
    reset_run,
    submit_experiment,
)
from mprun.client.config import (
    load_client_config,
    resolve_client_config_path,
)
from mprun.client.tui import run_tui
from mprun.custom_types import RunId
from mprun.endpoints import (
    ENDPOINT_EXPERIMENT,
    ENDPOINT_EXPERIMENTS,
    ENDPOINT_WORKERS,
)
from mprun.models import Experiment, WorkerData

console = Console()
client = Typer(no_args_is_help=False)


def _async_command(f: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that runs an async Typer command with ``asyncio.run``."""

    @functools.wraps(f)
    def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return asyncio.run(f(*args, **kwargs))

    return wrapper


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


def _print_workers(workers: list[WorkerData]) -> None:
    """Print a summary table of all registered workers to the console."""
    table = Table("Name", "WID", "Backend", "State", "Joined", "Last Check-in", "Run")
    for w in workers:
        run_str = f"{w.run.eid}-{w.run.index}-{w.run.iteration}" if w.run else "—"
        table.add_row(
            w.registration_data.name,
            str(w.wid),
            ", ".join(w.registration_data.backends),
            w.state,
            _fmt_ts(w.joined),
            _fmt_ts(w.last_check_in),
            run_str,
        )
    console.print(table)


@client.callback(invoke_without_command=True)
def _client_callback(
    ctx: Context,
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
    config: Path | None = Option(
        None,
        "--config",
        "-c",
        help=(
            "Path to TOML config file. "
            "Defaults to user and site config dirs (see platformdirs)."
        ),
    ),
) -> None:
    """Launch interactive TUI when no subcommand is given."""
    cfg_path = resolve_client_config_path(config)
    if cfg_path is not None:
        cfg = load_client_config(cfg_path)
        effective_url = base_url or cfg.server_address
    else:
        effective_url = base_url

    effective_url = effective_url or os.environ.get(SERVER_ADDRESS_ENV, DEFAULT_URL)

    ctx.ensure_object(dict)
    ctx.obj["base_url"] = effective_url

    if ctx.invoked_subcommand is None:
        run_tui(effective_url)
        raise Exit(0)


@client.command("list", help="Get list of all experiments")
@_async_command
async def list_experiments(
    ctx: Context,
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Get list of all experiments."""
    try:
        async with _client_factory(ctx.obj["base_url"]) as http:
            resp = await http.get(ENDPOINT_EXPERIMENTS)
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
@_async_command
async def get_experiment(
    ctx: Context,
    eid: int = Argument(
        help="Experiment's ID (use List command to get all experiments and their IDs)"
    ),
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Get specific experiment by its ID."""
    try:
        async with _client_factory(ctx.obj["base_url"]) as http:
            resp = await http.get(ENDPOINT_EXPERIMENT.format(eid=eid))
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
@_async_command
async def create_experiment(
    ctx: Context,
    experiment_file: str = Argument(help="Path to experiment definition"),
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Create a new experiment from an experiment definition."""
    try:
        async with _client_factory(ctx.obj["base_url"]) as http:
            experiment, resp_text = await submit_experiment(
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
@_async_command
async def delete_experiment_cmd(
    ctx: Context,
    eid: int = Argument(help="ID of the experiment to delete."),
    yes: bool = Option(False, "-y", "--yes", help="Skip confirmation prompt."),
) -> None:
    """Delete an experiment and all its associated data."""
    if not yes:
        confirm(f"Delete experiment {eid}? This cannot be undone.", abort=True)

    try:
        async with _client_factory(ctx.obj["base_url"]) as http:
            await delete_experiment(http_client=http, eid=eid)
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


@client.command("purge", help="Purge all dead workers. This cannot be undone.")
@_async_command
async def purge_workers_cmd(
    ctx: Context,
) -> None:
    """Purge all dead workers. This cannot be undone."""
    try:
        async with _client_factory(ctx.obj["base_url"]) as http:
            await purge_dead_workers(http_client=http)
    except HTTPStatusError as err:
        echo(f"HTTP error {err.response.status_code}: {err}", err=True)
        raise Exit(1) from err

    echo("Purged dead workers")


@client.command("workers", help="Get list of all registered workers")
@_async_command
async def list_workers(
    ctx: Context,
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Get list of all registered workers."""
    try:
        async with _client_factory(ctx.obj["base_url"]) as http:
            resp = await http.get(ENDPOINT_WORKERS)
    except HTTPStatusError as err:
        echo(f"HTTP Error: {err}", err=True)
        raise Exit(1) from err

    try:
        workers = [WorkerData.model_validate(j) for j in resp.json()]

        if print_json:
            echo(resp.text)
        else:
            _print_workers(workers)
        raise Exit(0)
    except ValidationError as err:
        echo(f"Error validating response: {err}", err=True)
        raise Exit(1) from err


@client.command("results", help="Download results archive for a specific run.")
@_async_command
async def get_run_results(
    ctx: Context,
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
        async with _client_factory(ctx.obj["base_url"]) as http:
            dest = await download_run_results(
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
@_async_command
async def reset_run_cmd(
    ctx: Context,
    run_id: str = Argument(help="Run ID (e.g. 12345678-0-0)"),
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
        async with _client_factory(ctx.obj["base_url"]) as http:
            run = await reset_run(
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
@_async_command
async def get_experiment_results_cmd(
    ctx: Context,
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
) -> None:
    """Download results for all runs in an experiment in parallel."""
    try:
        async with _client_factory(ctx.obj["base_url"]) as http:
            resp = await http.get(ENDPOINT_EXPERIMENT.format(eid=eid))
            resp.raise_for_status()

            try:
                experiment = Experiment.model_validate(resp.json())
            except ValidationError as err:
                echo(f"Error validating response: {err}", err=True)
                raise Exit(1) from err

            saved, skipped, failed = await get_experiment_results(
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
