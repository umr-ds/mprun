"""Textual TUI for interactive experiment browsing."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import httpx
from rapidfuzz import fuzz
from rapidfuzz import process as fuzz_process
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from mprun import SERVER_ADDRESS_ENV
from mprun.client.client import (
    DEFAULT_URL,
    download_run_results,
    get_experiment_results_parallel,
)
from mprun.models import Experiment

REFRESH_TIME: float = 30.0


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    return (
        datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    )


class ConfirmDownloadDialogue(ModalScreen[bool]):
    """Confirmation dialogue for result download."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
    ]

    def __init__(self, run_name: str) -> None:
        """Initialise dialogue.

        Args:
            run_name: Display name of the run to download.
        """
        super().__init__()
        self.run_name = run_name

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        with Vertical(id="confirm-dialog"):
            yield Static(
                f"Download results for [bold]{self.run_name}[/bold]?\n\n"
                "[bold][y][/bold][u]Y[/u]es[bold]/[n][/bold][u]N[/u]o"
            )

    def action_confirm(self) -> None:
        """Confirm download."""
        confirmed: bool = True
        self.dismiss(confirmed)

    def action_cancel(self) -> None:
        """Cancel download."""
        confirmed: bool = False
        self.dismiss(confirmed)


class DetailView(Screen):
    """Detail-View: runs of a single experiment."""

    TITLE = "Detail-View"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss", "Back"),
        ("r", "refresh", "Refresh now"),
        ("d", "download", "Download results"),
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
            f"[dim]Started:[/dim]    {_fmt_ts(run.get('started_running'))}\n"
            f"[dim]Finished:[/dim]   {_fmt_ts(run.get('finished_running'))}\n"
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

    async def _do_download(self, run: dict) -> None:
        """Download run results to the current working directory."""
        eid = run["eid"]
        index = run["index"]
        iteration = run["iteration"]
        output = Path.cwd()

        def _sync_download() -> Path | None:
            with httpx.Client(base_url=self.base_url) as client:
                return download_run_results(client, eid, index, iteration, output)

        try:
            result = await asyncio.to_thread(_sync_download)
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


class OverViewScreen(Screen):
    """Over-View: browsable list of all experiments."""

    TITLE = "Over-View"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("r", "refresh", "Refresh now"),
        ("d", "download", "Download results"),
        ("f", "search", "Search"),
        ("c", "create", "Create-Mode"),
    ]

    def __init__(self, base_url: str) -> None:
        """Initialise Over-View.

        Args:
            base_url: Server base URL.
        """
        super().__init__()
        self.base_url = base_url
        self._experiments: list[dict] = []
        self._visible_experiments: list[dict] = []
        self._search_mode: bool = False

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        with Horizontal(id="content"):
            with Vertical(id="list-container"):
                yield Input(placeholder="Search…", id="search-bar", disabled=True)
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

        self._experiments = sorted(
            experiments, key=lambda e: e.get("creation_timestamp") or 0, reverse=True
        )
        await self._apply_filter()

    async def _apply_filter(self) -> None:
        """Rebuild the experiment list applying the current search query."""
        query = self.query_one("#search-bar", Input).value.lower()
        if query:
            names = [e.get("name", "") for e in self._experiments]
            matches = fuzz_process.extract(
                query, names, scorer=fuzz.WRatio, score_cutoff=60
            )
            matches.sort(key=lambda m: m[1], reverse=True)
            self._visible_experiments = [self._experiments[m[2]] for m in matches]
        else:
            self._visible_experiments = list(self._experiments)
        lv = self.query_one("#experiment-list", ListView)
        prev_index = lv.index if lv.index is not None else 0
        max_name = max(
            (len(e.get("name", "")) for e in self._visible_experiments), default=0
        )
        await lv.clear()
        for exp in self._visible_experiments:
            name = exp.get("name", "")
            ts = _fmt_ts(exp.get("creation_timestamp"))
            await lv.append(ListItem(Label(f"{name:<{max_name}}  [dim]{ts}[/dim]")))
        if self._visible_experiments:
            new_index = min(prev_index, len(self._visible_experiments) - 1)
            lv.index = new_index
            self._update_detail(self._visible_experiments[new_index])
        else:
            self._update_detail(None)

    def action_search(self) -> None:
        """Enter search mode."""
        self._search_mode = True
        search_bar = self.query_one("#search-bar", Input)
        search_bar.disabled = False
        search_bar.display = True
        search_bar.focus()

    def _exit_search(self) -> None:
        """Exit search mode and restore full list."""
        self._search_mode = False
        search_bar = self.query_one("#search-bar", Input)
        search_bar.value = ""
        search_bar.disabled = True
        search_bar.display = False
        self.query_one("#experiment-list", ListView).focus()
        self.call_later(self._apply_filter)

    async def on_input_changed(self, _event: Input.Changed) -> None:
        """Filter list as user types."""
        await self._apply_filter()

    async def on_input_submitted(self, _event: Input.Submitted) -> None:
        """Open selected experiment on Enter."""
        lv = self.query_one("#experiment-list", ListView)
        idx = lv.index
        if idx is not None and idx < len(self._visible_experiments):
            exp = self._visible_experiments[idx]
            self._exit_search()
            self.app.push_screen(DetailView(base_url=self.base_url, eid=exp["eid"]))

    def on_key(self, event: events.Key) -> None:
        """Forward arrow keys to list and Escape to exit search while in search mode."""
        if not self._search_mode:
            return
        lv = self.query_one("#experiment-list", ListView)
        if event.key == "escape":
            self._exit_search()
            event.stop()
        elif event.key == "up":
            lv.action_cursor_up()
            event.stop()
        elif event.key == "down":
            lv.action_cursor_down()
            event.stop()

    def _update_detail(self, exp: dict | None) -> None:
        """Render experiment metadata into the detail panel."""
        panel = self.query_one("#detail-panel", Static)
        if exp is None:
            panel.update("No experiments")
            return

        experiment = Experiment.model_validate(exp)
        defn = experiment.definition
        total_runs = sum(len(inner) for inner in experiment.runs)

        params_lines = "\n".join(f"  {k}: {v}" for k, v in defn.params.items())
        timeout_str = f"{defn.timeout}s" if defn.timeout is not None else "none"
        env_vars_str = (
            ", ".join(defn.environment_variables.keys())
            if defn.environment_variables
            else "none"
        )
        setup = defn.setup_executable or "none"

        panel.update(
            f"[bold]{experiment.name}[/bold]\n\n"
            f"[dim]ID:[/dim]           {experiment.eid}\n"
            f"[dim]Active:[/dim]       {experiment.active_state}\n"
            f"[dim]Success:[/dim]      {experiment.success_state}\n"
            f"[dim]Created:[/dim]      {_fmt_ts(experiment.creation_timestamp)}\n"
            f"[dim]Started:[/dim]      {_fmt_ts(experiment.started_running)}\n"
            f"[dim]Finished:[/dim]     {_fmt_ts(experiment.finished_running)}\n"
            f"[dim]Runs:[/dim]         {total_runs}\n"
            f"[dim]Iterations:[/dim]   {defn.iterations}\n"
            f"[dim]Executable:[/dim]   {defn.executable}\n"
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
        if idx is not None and idx < len(self._visible_experiments):
            self._update_detail(self._visible_experiments[idx])

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        """Open Detail-View for the selected experiment."""
        lv = self.query_one("#experiment-list", ListView)
        idx = lv.index
        if idx is not None and idx < len(self._visible_experiments):
            exp = self._visible_experiments[idx]
            self.app.push_screen(DetailView(base_url=self.base_url, eid=exp["eid"]))

    def action_download(self) -> None:
        """Prompt for confirmation then download all results for the selected experiment."""
        lv = self.query_one("#experiment-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._visible_experiments):
            return
        exp = self._visible_experiments[idx]

        def on_confirm(result: object) -> None:
            if result:
                self.call_later(self._do_download_experiment, exp)

        self.app.push_screen(ConfirmDownloadDialogue(exp["name"]), on_confirm)

    async def _do_download_experiment(self, exp: dict) -> None:
        """Download all run results for an experiment into a subdirectory named by EID."""
        experiment = Experiment.model_validate(exp)
        out_dir = Path.cwd() / str(exp["eid"])
        out_dir.mkdir(exist_ok=True)

        def _sync_download() -> tuple[list[Path], list[int], bool]:
            with httpx.Client(base_url=self.base_url) as client:
                return get_experiment_results_parallel(client, experiment, out_dir)

        try:
            saved, skipped, failed = await asyncio.to_thread(_sync_download)
        except (httpx.HTTPStatusError, httpx.RequestError) as err:
            self.notify(f"Download failed: {err}", severity="error")
            return

        parts = [f"{len(saved)} saved"]
        if skipped:
            parts.append(f"{len(skipped)} not ready")
        if failed:
            parts.append("some errors")
        self.notify(
            f"{', '.join(parts)} → {out_dir}",
            severity="warning" if failed else "information",
        )

    def action_create(self) -> None:
        """Switch to Create-Mode."""
        if self._search_mode:
            return
        self.app.switch_screen(CreateScreen(self.base_url))

    def action_refresh(self) -> None:
        """Refresh immediately."""
        self.call_later(self._refresh)


class CreateScreen(Screen):
    """Create-Mode: placeholder for experiment creation."""

    TITLE = "Create-Mode"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("v", "view_mode", "View-Mode"),
    ]

    def __init__(self, base_url: str) -> None:
        """Initialise Create-Mode.

        Args:
            base_url: Server base URL.
        """
        super().__init__()
        self.base_url = base_url

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        yield Static("Create-Mode — not yet implemented", id="create-placeholder")
        yield Footer()

    def action_view_mode(self) -> None:
        """Switch back to View-Mode."""
        self.app.switch_screen(OverViewScreen(self.base_url))


class ExperimentTui(App):
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
