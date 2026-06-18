"""TUI application shell, helpers, and entry point."""

from __future__ import annotations

import os
from typing import ClassVar

from textual.app import App

from mprun import SERVER_ADDRESS_ENV
from mprun.client.client import DEFAULT_URL

REFRESH_TIME: float = 30.0


class ExperimentTui(App[None]):
    """TUI application shell."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
    ]

    CSS_PATH = "tui.tcss"

    def __init__(self, base_url: str = DEFAULT_URL) -> None:
        """Initialise the TUI.

        Args:
            base_url: Server base URL.
        """
        super().__init__()
        self.base_url = base_url

    async def on_mount(self) -> None:
        """Push the initial screen."""
        from mprun.client.tui.overview import OverViewScreen  # noqa: PLC0415

        await self.push_screen(OverViewScreen(self.base_url))


def run_tui(base_url: str | None = None) -> None:
    """Launch the TUI.

    Args:
        base_url: Server base URL. Falls back to ``MPRUN_SERVER_ADDRESS`` env var,
            then ``http://localhost:8000``.
    """
    url = base_url or os.environ.get(SERVER_ADDRESS_ENV, DEFAULT_URL)
    app = ExperimentTui(base_url=url)
    app.run()
