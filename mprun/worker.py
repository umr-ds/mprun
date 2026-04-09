#! /usr/bin/env python3

"""Module contains worker application."""

from logging import getLogger
from os import getenv
from sys import exit as goodbye

from httpx import Client, HTTPStatusError

from mprun import SERVER_ADDRESS_ENV
from mprun.models import Worker

logger = getLogger(__name__)
WORKER_NAME_ENV = "MPRUN_WORKER_NAME"


def register(client: Client, name: str) -> Worker:
    """Register with the server.

    Args:
        client (Client): HTTP client pointed to the server's base URL.
        name (str): (Human readable) Name to register with.

    Returns:
        Worker: Worker data returned by the server, if registration was successful
    """
    response = client.post("/workers", params={"name": name})
    response.raise_for_status()

    return Worker.model_validate(response.json())


def main() -> None:
    """Run worker."""
    server_address = getenv(SERVER_ADDRESS_ENV)
    if server_address is None:
        logger.fatal(f"Environment variable {SERVER_ADDRESS_ENV} not set!")
        goodbye(1)

    if not server_address.startswith("http://"):
        server_address = f"http://{server_address}"

    client = Client(base_url=server_address)

    name = getenv(WORKER_NAME_ENV)
    if name is None:
        logger.fatal(f"Environment variable {WORKER_NAME_ENV} not set!")
        goodbye(1)

    try:
        _ = register(client=client, name=name)
    except HTTPStatusError as err:
        logger.fatal("Worker registration fialed: %s", err, exc_info=True)
        goodbye(1)


if __name__ == "__main__":
    main()
