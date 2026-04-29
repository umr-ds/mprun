#! /usr/bin/env python3

"""Module contains client application."""

from pathlib import Path

from httpx import Client, HTTPStatusError, codes
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from typer import Argument, Exit, Option, Typer, echo

from mprun.models import Job, JobDefinition, ValidationMode

console = Console()
app = Typer()


DEFAULT_URL = "http://localhost:8000"


def _get_client(base_url: str | None) -> Client:
    if base_url is None:
        base_url = DEFAULT_URL
    return Client(base_url=base_url)


def _print_jobs(jobs: list[Job]) -> None:
    table = Table("Name", "ID", "State")
    for job in jobs:
        table.add_row(job.name, str(job.jid), job.state)
    console.print(table)


def _print_job(job: Job) -> None:
    table = Table("Name", "ID", "State", title="Job")
    table.add_row(job.name, str(job.jid), job.state)
    console.print(table)

    table = Table("Name", "State", title="Runs")
    for run in job.runs:
        table.add_row(run.name, run.state)
    console.print(table)


@app.command("list", help="Get list of all jobs")
def list_jobs(
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Get list of all jobs."""
    try:
        with _get_client(base_url) as client:
            resp = client.get("/jobs")
    except HTTPStatusError as err:
        echo(f"HTTP Error: {err}", err=True)
        raise Exit(1) from err

    try:
        jobs = [Job.model_validate(j) for j in resp.json()]

        if print_json:
            echo(resp.text)
        else:
            _print_jobs(jobs)
        raise Exit(0)
    except ValidationError as err:
        echo(f"Error validating response: {err}", err=True)
        raise Exit(1) from err


@app.command("get", help="Get specific job by its ID")
def get_job(
    jid: int = Argument(
        help="Job's ID (use List command to get all jobs and their IDs)"
    ),
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Get specific job by its ID."""
    try:
        with _get_client(base_url) as client:
            resp = client.get(f"/jobs/{jid}")
    except HTTPStatusError as err:
        if err.response.status_code == codes.NOT_FOUND:
            echo(f"No job with ID {jid}", err=True)
        else:
            echo(f"HTTP Error: {err}", err=True)
        raise Exit(1) from err

    job: Job
    try:
        job = Job.model_validate(resp.json())
    except ValidationError as err:
        echo(f"Error validating response: {err}", err=True)
        raise Exit(1) from err

    if print_json:
        echo(resp.text)
    else:
        _print_job(job)


@app.command("create", help="Create a new job from a job definition.")
def create_job(
    job_file: str = Argument(help="Path to job definition"),
    base_url: str | None = Option(
        None, "-u", "--base-url", help="Base URL of the server."
    ),
    print_json: bool = Option(
        False, "-j", "--json", metavar="json", help="Output raw json"
    ),
) -> None:
    """Create a new job from a job definition."""
    job_path = Path(job_file)
    if not job_path.exists():
        echo(f"File {job_path} does not exist.", err=True)
        raise Exit(1)

    job_definition: JobDefinition
    try:
        job_definition = JobDefinition.load_toml(
            file_path=job_path, validation_mode=ValidationMode.DATA_AND_FILES
        )
    except OSError as err:
        echo(f"Error reading file: {err}", err=True)
        raise Exit(1) from err
    except ValidationError as err:
        echo(f"Error validating job definition: {err}", err=True)
        raise Exit(1) from err

    echo(f"Creating job with name {job_definition.name}")

    try:
        with _get_client(base_url) as client:
            resp = client.post(
                "/jobs", json=job_definition.model_dump()
            ).raise_for_status()
    except HTTPStatusError as err:
        echo(f"HTTP Error: {err}", err=True)
        raise Exit(1) from err

    job: Job
    try:
        job = Job.model_validate(resp.json())
    except ValidationError as err:
        echo(f"Error validating response: {err}", err=True)
        raise Exit(1) from err

    if print_json:
        echo(resp.text)
    else:
        _print_job(job)


if __name__ == "__main__":
    app()
