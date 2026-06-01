"""Textual TUI for interactive experiment browsing."""

from __future__ import annotations

import os
from typing import ClassVar

import httpx
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header

from mprun import SERVER_ADDRESS_ENV
from mprun.client.client import DEFAULT_URL

CSS = """
DataTable {
    height: 1fr;
}
"""


class ExperimentTui(App):
    """TUI for browsing experiments."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh now"),
    ]

    CSS: ClassVar[str] = CSS

    def __init__(self, base_url: str = DEFAULT_URL) -> None:
        """Initialise the TUI.

        Args:
            base_url: Server base URL.
        """
        super().__init__()
        self.base_url = base_url

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        yield DataTable()
        yield Footer()

    async def on_mount(self) -> None:
        """Set up table columns and start periodic refresh."""
        table = self.query_one(DataTable)
        table.add_columns("Name", "ID", "Active", "Success")
        table.cursor_type = "row"
        table.zebra_stripes = True
        self.set_interval(1, self._refresh)
        await self._refresh()

    async def _refresh(self) -> None:
        """Fetch experiments from server and update the table."""
        async with httpx.AsyncClient(base_url=self.base_url) as http:
            try:
                resp = await http.get("/experiments")
                resp.raise_for_status()
                experiments = resp.json()
            except httpx.HTTPStatusError as err:
                self._show_error(f"HTTP {err.response.status_code}")
                return
            except httpx.RequestError as err:
                self._show_error(f"Connection error: {err}")
                return

        table = self.query_one(DataTable)
        table.clear()
        for exp in experiments:
            table.add_row(
                exp["name"],
                str(exp["eid"]),
                exp["active_state"],
                exp["success_state"],
            )

    def _show_error(self, message: str) -> None:
        """Show an error message in the table."""
        table = self.query_one(DataTable)
        table.clear()
        table.add_row("--", message, "", "")

    def action_refresh(self) -> None:
        """Refresh immediately."""
        self.call_later(self._refresh)


def run_tui(base_url: str | None = None) -> None:
    """Launch the TUI.

    Args:
        base_url: Server base URL. Falls back to ``MPRUN_SERVER_ADDRESS`` env var,
            then ``http://localhost:8000``.
    """
    url = base_url or os.environ.get(SERVER_ADDRESS_ENV, DEFAULT_URL)
    app = ExperimentTui(base_url=url)
    app.run()
