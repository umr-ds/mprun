"""Detail-View: runs of a single experiment."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, override

import httpx
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from mprun.client.client import download_run_results, reset_run
from mprun.client.tui import REFRESH_TIME, _fmt_duration, _fmt_ts
from mprun.client.tui.confirmations import ConfirmDownloadDialogue, ConfirmResetDialogue
from mprun.custom_types import RunId


class DetailView(Screen[None]):
    """Detail-View: runs of a single experiment."""

    TITLE = "Detail-View"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+r", "refresh", "Refresh"),
        ("d", "download", "Download"),
        ("r", "reset", "Reset"),
        ("escape", "dismiss", "Back"),
    ]

    def __init__(self, base_url: str, eid: int) -> None:
        """Initialise Detail-View.

        Args:
            base_url: Server base URL.
            eid: Experiment ID to display.
        """
        super().__init__()
        self.base_url = base_url
        self.eid = eid
        self._runs: list[dict[str, Any]] = []

    @override
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        with Horizontal(id="content"):
            yield ListView(id="run-list")
            yield Static("No run selected", id="run-panel")
        yield Footer()

    async def on_mount(self) -> None:
        """Start periodic refresh."""
        self.set_interval(REFRESH_TIME, self._refresh)
        await self._refresh()

    async def _refresh(self) -> None:
        """Fetch experiment from server and update the run list."""
        async with httpx.AsyncClient(base_url=self.base_url) as http:
            try:
                resp = await http.get(f"/experiments/{self.eid}")
                resp.raise_for_status()
                exp = resp.json()
            except httpx.HTTPStatusError as err:
                self._show_error(f"HTTP {err.response.status_code}")
                return
            except httpx.RequestError as err:
                self._show_error(f"Connection error: {err}")
                return

        self._runs = [run for inner in exp.get("runs", []) for run in inner]
        exp_name = exp.get("name", "?")

        lv = self.query_one("#run-list", ListView)
        prev_index = lv.index if lv.index is not None else 0

        await lv.clear()
        for run in self._runs:
            label = f"{exp_name}-{run['index']}-{run['iteration']}"
            await lv.append(ListItem(Label(label)))

        if self._runs:
            new_index = min(prev_index, len(self._runs) - 1)
            lv.index = new_index
            self._update_run_panel(self._runs[new_index], exp_name)
        else:
            self._update_run_panel(None, exp_name)

    def _update_run_panel(self, run: dict[str, Any] | None, exp_name: str) -> None:
        """Render run metadata into the detail panel."""
        panel = self.query_one("#run-panel", Static)
        if run is None:
            panel.update("No runs")
            return

        wid = run.get("wid")
        failure_reason = run.get("failure_reason")
        failure_line = (
            f"[dim]Failure:[/dim]    [red]{failure_reason}[/red]\n"
            if failure_reason
            else ""
        )
        params = run.get("params", {})
        params_lines = "\n".join(f"  {k}: {v}" for k, v in params.items())
        name = f"{exp_name}-{run['index']}-{run['iteration']}"

        panel.update(
            f"[bold]{name}[/bold]\n\n"
            f"[dim]Index:[/dim]      {run['index']}\n"
            f"[dim]Iteration:[/dim]  {run['iteration']}\n"
            f"[dim]Active:[/dim]     {run['active_state']}\n"
            f"[dim]Success:[/dim]    {run['success_state']}\n"
            f"{failure_line}"
            f"[dim]Started:[/dim]    {_fmt_ts(run.get('started_running'))}\n"
            f"[dim]Finished:[/dim]   {_fmt_ts(run.get('finished_running'))}\n"
            f"[dim]Runtime:[/dim]    {_fmt_duration(run.get('started_running'), run.get('finished_running'))}\n"
            f"[dim]Worker:[/dim]     {wid if wid is not None else 'none'}\n"
            f"\n[dim]Parameters:[/dim]\n{params_lines}"
        )

    def _show_error(self, message: str) -> None:
        """Show an error in the run panel."""
        panel = self.query_one("#run-panel", Static)
        panel.update(f"[red]Error: {message}[/red]")

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        """Update run panel when cursor moves."""
        lv = self.query_one("#run-list", ListView)
        idx = lv.index
        if idx is not None and idx < len(self._runs):
            exp_name = self._runs[idx].get("definition", {}).get("name", "?")
            self._update_run_panel(self._runs[idx], exp_name)

    def action_download(self) -> None:
        """Prompt for confirmation then download selected run's results."""
        lv = self.query_one("#run-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._runs):
            return
        run = self._runs[idx]
        exp_name = run.get("definition", {}).get("name", "?")
        run_name = f"{exp_name}-{run['index']}-{run['iteration']}"

        def on_confirm(result: object) -> None:
            if result:
                self.call_later(self._do_download, run)

        self.app.push_screen(ConfirmDownloadDialogue(run_name), on_confirm)

    async def _do_download(self, run: dict[str, Any]) -> None:
        """Download run results to the current working directory."""
        eid = run["eid"]
        index = run["index"]
        iteration = run["iteration"]
        output = Path.cwd()

        try:
            async with httpx.AsyncClient(base_url=self.base_url) as client:
                result = await download_run_results(
                    client, eid, index, iteration, output
                )
        except (httpx.HTTPStatusError, httpx.RequestError) as err:
            self.notify(f"Download failed: {err}", severity="error")
            return

        if result is None:
            self.notify("No results available yet", severity="warning")
        else:
            self.notify(f"Saved to {result}")

    def action_refresh(self) -> None:
        """Refresh immediately."""
        self.call_later(self._refresh)

    def action_reset(self) -> None:
        """Prompt for confirmation then reset the selected run."""
        lv = self.query_one("#run-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._runs):
            return
        run = self._runs[idx]
        exp_name = run.get("definition", {}).get("name", "?")
        run_name = f"{exp_name}-{run['index']}-{run['iteration']}"

        def on_confirm(result: object) -> None:
            if result:
                self.call_later(self._do_reset, run)

        self.app.push_screen(ConfirmResetDialogue(run_name), on_confirm)

    async def _do_reset(self, run: dict[str, Any]) -> None:
        """Reset the run via the server and refresh the view."""
        rid = RunId(eid=run["eid"], index=run["index"], iteration=run["iteration"])

        try:
            async with httpx.AsyncClient(base_url=self.base_url) as client:
                await reset_run(client, rid)
        except httpx.HTTPStatusError as err:
            self.notify(
                f"Reset failed: HTTP {err.response.status_code}", severity="error"
            )
            return
        except httpx.RequestError as err:
            self.notify(f"Reset failed: {err}", severity="error")
            return

        self.notify(f"Reset {run['index']}-{run['iteration']}")
        await self._refresh()
