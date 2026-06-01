"""Textual TUI for interactive experiment browsing."""

from __future__ import annotations

import os
from typing import ClassVar

import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from mprun import SERVER_ADDRESS_ENV
from mprun.client.client import DEFAULT_URL

REFRESH_TIME: float = 30.0

CSS = """
#content {
    height: 1fr;
}

#experiment-list, #run-list {
    width: 30%;
    border: solid $accent;
}

#detail-panel, #run-panel {
    width: 1fr;
    border: solid $accent;
    padding: 1 2;
}
"""


class DetailView(Screen):
    """Detail-View: runs of a single experiment."""

    TITLE = "Detail-View"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss", "Back"),
        ("r", "refresh", "Refresh now"),
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
        self._runs: list[dict] = []

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

    def _update_run_panel(self, run: dict | None, exp_name: str) -> None:
        """Render run metadata into the detail panel."""
        panel = self.query_one("#run-panel", Static)
        if run is None:
            panel.update("No runs")
            return

        wid = run.get("wid")
        params = run.get("params", {})
        params_lines = "\n".join(f"  {k}: {v}" for k, v in params.items())
        name = f"{exp_name}-{run['index']}-{run['iteration']}"

        panel.update(
            f"[bold]{name}[/bold]\n\n"
            f"[dim]Index:[/dim]      {run['index']}\n"
            f"[dim]Iteration:[/dim]  {run['iteration']}\n"
            f"[dim]Active:[/dim]     {run['active_state']}\n"
            f"[dim]Success:[/dim]    {run['success_state']}\n"
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

    def action_refresh(self) -> None:
        """Refresh immediately."""
        self.call_later(self._refresh)


class ExperimentTui(App):
    """TUI for browsing experiments."""

    TITLE = "Over-View"

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
        self._experiments: list[dict] = []

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        with Horizontal(id="content"):
            yield ListView(id="experiment-list")
            yield Static("No experiment selected", id="detail-panel")
        yield Footer()

    async def on_mount(self) -> None:
        """Start periodic refresh."""
        self.set_interval(REFRESH_TIME, self._refresh)
        await self._refresh()

    async def _refresh(self) -> None:
        """Fetch experiments from server and update the list."""
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

        self._experiments = experiments
        lv = self.query_one("#experiment-list", ListView)
        prev_index = lv.index if lv.index is not None else 0

        await lv.clear()
        for exp in experiments:
            await lv.append(ListItem(Label(exp["name"])))

        if experiments:
            new_index = min(prev_index, len(experiments) - 1)
            lv.index = new_index
            self._update_detail(experiments[new_index])
        else:
            self._update_detail(None)

    def _update_detail(self, exp: dict | None) -> None:
        """Render experiment metadata into the detail panel."""
        panel = self.query_one("#detail-panel", Static)
        if exp is None:
            panel.update("No experiments")
            return

        defn = exp.get("definition", {})
        runs = exp.get("runs", [])
        total_runs = sum(len(inner) for inner in runs)

        params = defn.get("params", {})
        params_lines = "\n".join(f"  {k}: {v}" for k, v in params.items())

        timeout = defn.get("timeout")
        timeout_str = f"{timeout}s" if timeout is not None else "none"

        env_vars = defn.get("environment_variables") or {}
        env_vars_str = ", ".join(env_vars.keys()) if env_vars else "none"

        setup = defn.get("setup_executable") or "none"

        panel.update(
            f"[bold]{exp['name']}[/bold]\n\n"
            f"[dim]ID:[/dim]           {exp['eid']}\n"
            f"[dim]Active:[/dim]       {exp['active_state']}\n"
            f"[dim]Success:[/dim]      {exp['success_state']}\n"
            f"[dim]Runs:[/dim]         {total_runs}\n"
            f"[dim]Iterations:[/dim]   {defn.get('iterations', 1)}\n"
            f"[dim]Executable:[/dim]   {defn.get('executable', '?')}\n"
            f"[dim]Setup:[/dim]        {setup}\n"
            f"[dim]Timeout:[/dim]      {timeout_str}\n"
            f"[dim]Env vars:[/dim]     {env_vars_str}\n"
            f"\n[dim]Parameters:[/dim]\n{params_lines}"
        )

    def _show_error(self, message: str) -> None:
        """Show an error in the detail panel."""
        panel = self.query_one("#detail-panel", Static)
        panel.update(f"[red]Error: {message}[/red]")

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        """Update detail panel when cursor moves."""
        lv = self.query_one("#experiment-list", ListView)
        idx = lv.index
        if idx is not None and idx < len(self._experiments):
            self._update_detail(self._experiments[idx])

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        """Open Detail-View for the selected experiment."""
        lv = self.query_one("#experiment-list", ListView)
        idx = lv.index
        if idx is not None and idx < len(self._experiments):
            exp = self._experiments[idx]
            self.push_screen(DetailView(base_url=self.base_url, eid=exp["eid"]))

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
