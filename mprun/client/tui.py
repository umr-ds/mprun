"""Textual TUI for interactive experiment browsing."""

from __future__ import annotations

import asyncio
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, override

import httpx
from rapidfuzz import fuzz
from rapidfuzz import process as fuzz_process
from rich.markup import escape as markup_escape
from rich.syntax import Syntax
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from mprun import SERVER_ADDRESS_ENV
from mprun.client.client import (
    DEFAULT_URL,
    delete_experiment,
    download_run_results,
    get_experiment_results_parallel,
    reset_run,
    submit_experiment,
)
from mprun.custom_types import RunId
from mprun.models import Experiment, ExperimentDefinition, ValidationMode

REFRESH_TIME: float = 30.0


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    return (
        datetime.fromtimestamp(ts, tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    )


class ConfirmDeleteDialogue(ModalScreen[bool]):
    """Confirmation dialogue for experiment deletion."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
    ]

    def __init__(self, exp_name: str) -> None:
        """Initialise dialogue.

        Args:
            exp_name: Display name of the experiment to delete.
        """
        super().__init__()
        self.exp_name = exp_name

    @override
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        with Vertical(id="confirm-dialog"):
            yield Static(
                f"Delete [bold]{self.exp_name}[/bold]? This cannot be undone.\n\n"
                "[bold][y][/bold][u]Y[/u]es[bold]/[n][/bold][u]N[/u]o"
            )

    def action_confirm(self) -> None:
        """Confirm deletion."""
        confirmed: bool = True
        self.dismiss(confirmed)

    def action_cancel(self) -> None:
        """Cancel deletion."""
        confirmed: bool = False
        self.dismiss(confirmed)


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

    @override
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


class ConfirmResetDialogue(ModalScreen[bool]):
    """Confirmation dialogue for run reset."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
    ]

    def __init__(self, run_name: str) -> None:
        """Initialise dialogue.

        Args:
            run_name: Display name of the run to reset.
        """
        super().__init__()
        self.run_name = run_name

    @override
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        with Vertical(id="confirm-dialog"):
            yield Static(
                f"Reset [bold]{self.run_name}[/bold]? Results will be permanently deleted.\n\n"
                "[bold][y][/bold][u]Y[/u]es[bold]/[n][/bold][u]N[/u]o"
            )

    def action_confirm(self) -> None:
        """Confirm reset."""
        confirmed: bool = True
        self.dismiss(confirmed)

    def action_cancel(self) -> None:
        """Cancel reset."""
        confirmed: bool = False
        self.dismiss(confirmed)


class DetailView(Screen[None]):
    """Detail-View: runs of a single experiment."""

    TITLE = "Detail-View"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("escape", "dismiss", "Back"),
        ("ctrl+r", "refresh", "Refresh"),
        ("d", "download", "Download"),
        ("r", "reset", "Reset"),
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

    async def _do_download(self, run: dict[str, Any]) -> None:
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

        def _sync_reset() -> None:
            with httpx.Client(base_url=self.base_url) as client:
                reset_run(client, rid)

        try:
            await asyncio.to_thread(_sync_reset)
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


class OverViewScreen(Screen[None]):
    """Over-View: browsable list of all experiments."""

    TITLE = "Over-View"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+r", "refresh", "Refresh"),
        ("d", "download", "Download"),
        ("f", "search", "Find"),
        ("c", "create", "Create-Mode"),
        ("backspace", "remove", "Remove"),
    ]

    def __init__(self, base_url: str) -> None:
        """Initialise Over-View.

        Args:
            base_url: Server base URL.
        """
        super().__init__()
        self.base_url = base_url
        self._experiments: list[dict[str, Any]] = []
        self._visible_experiments: list[dict[str, Any]] = []
        self._search_mode: bool = False

    @override
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

    def _update_detail(self, exp: dict[str, Any] | None) -> None:
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

    async def _do_download_experiment(self, exp: dict[str, Any]) -> None:
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

    def action_remove(self) -> None:
        """Prompt for confirmation then delete the selected experiment."""
        if self._search_mode:
            return
        lv = self.query_one("#experiment-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._visible_experiments):
            return
        exp = self._visible_experiments[idx]

        def on_confirm(result: object) -> None:
            if result:
                self.call_later(self._do_delete_experiment, exp)

        self.app.push_screen(ConfirmDeleteDialogue(exp["name"]), on_confirm)

    async def _do_delete_experiment(self, exp: dict[str, Any]) -> None:
        """Delete an experiment via the server and refresh the list."""
        eid = exp["eid"]

        def _sync_delete() -> None:
            with httpx.Client(base_url=self.base_url) as client:
                delete_experiment(client, eid)

        try:
            await asyncio.to_thread(_sync_delete)
        except httpx.HTTPStatusError as err:
            self.notify(
                f"Delete failed: HTTP {err.response.status_code}", severity="error"
            )
            return
        except httpx.RequestError as err:
            self.notify(f"Delete failed: {err}", severity="error")
            return

        self.notify(f"Deleted {exp['name']}")
        await self._refresh()

    def action_create(self) -> None:
        """Switch to Create-Mode."""
        if self._search_mode:
            return
        self.app.switch_screen(CreateScreen(self.base_url))

    def action_refresh(self) -> None:
        """Refresh immediately."""
        self.call_later(self._refresh)


class ExperimentPreviewModal(ModalScreen[None]):
    """Preview a parsed ExperimentDefinition and optionally submit the job."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
    ]

    def __init__(
        self,
        definition: ExperimentDefinition,
        file_path: Path,
        base_url: str,
    ) -> None:
        """Initialise modal.

        Args:
            definition: Parsed definition to display.
            file_path: Path to the TOML file (passed to submit_experiment).
            base_url: Server base URL.
        """
        super().__init__()
        self._definition = definition
        self._file_path = file_path
        self._base_url = base_url

    @override
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        with Vertical(id="experiment-preview"):
            yield Static(self._build_content(), id="experiment-preview-content")
        yield Footer()

    def _build_content(self) -> str:
        defn = self._definition
        run_count = math.prod(len(v) for v in defn.params.values()) * defn.iterations
        params_lines = "\n".join(
            f"  {markup_escape(k)}: {markup_escape(str(v))}"
            for k, v in defn.params.items()
        )
        results_lines = (
            "\n".join(
                f"  {markup_escape(k)} → {markup_escape(v)}"
                for k, v in defn.results.items()
            )
            or "  none"
        )
        timeout_str = f"{defn.timeout}s" if defn.timeout is not None else "none"
        env_vars_str = (
            ", ".join(markup_escape(k) for k in defn.environment_variables)
            if defn.environment_variables
            else "none"
        )
        env_files_str = (
            ", ".join(
                f"{markup_escape(k)} → {markup_escape(v)}"
                for k, v in defn.environment_files.items()
            )
            if defn.environment_files
            else "none"
        )
        setup = (
            markup_escape(defn.setup_executable) if defn.setup_executable else "none"
        )

        return (
            f"[bold]{markup_escape(defn.name)}[/bold]\n\n"
            f"[dim]Executable:[/dim]   {markup_escape(defn.executable)}\n"
            f"[dim]Setup:[/dim]        {setup}\n"
            f"[dim]Timeout:[/dim]      {timeout_str}\n"
            f"[dim]Iterations:[/dim]   {defn.iterations}\n"
            f"[dim]Total runs:[/dim]   {run_count}\n"
            f"[dim]Env vars:[/dim]     {env_vars_str}\n"
            f"[dim]Env files:[/dim]    {env_files_str}\n"
            f"\n[dim]Parameters:[/dim]\n{params_lines}\n"
            f"\n[dim]Results:[/dim]\n{results_lines}\n\n"
            "Submit? [bold][y][/bold][u]Y[/u]es  [bold][n][/bold][u]N[/u]o"
        )

    async def action_confirm(self) -> None:
        """Submit the experiment and switch to View-Mode on success."""
        file_path = self._file_path
        base_url = self._base_url

        try:
            with httpx.Client(base_url=base_url) as http:
                await asyncio.to_thread(submit_experiment, http, file_path)
        except Exception as e:  # noqa: BLE001
            self.notify(str(e).splitlines()[0], severity="error")
            return

        await self.dismiss()
        await self.app.switch_screen(OverViewScreen(base_url))

    def action_cancel(self) -> None:
        """Close modal without submitting."""
        self.dismiss()


class CreateScreen(Screen[None]):
    """Create-Mode: three-pane file explorer."""

    TITLE = "Create-Mode"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("v", "view_mode", "View-Mode"),
        ("left", "go_up", "Parent"),
        ("right", "go_into", "Enter"),
        ("c", "open_preview", "Create"),
    ]

    def __init__(self, base_url: str) -> None:
        """Initialise Create-Mode.

        Args:
            base_url: Server base URL.
        """
        super().__init__()
        self.base_url = base_url
        self._current_dir: Path = Path.cwd()
        self._entries: list[Path] = []

    @override
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        with Horizontal(id="explorer"):
            yield Static("", id="pane-parent")
            yield ListView(id="pane-current")
            yield Static("", id="pane-preview")
        yield Footer()

    async def on_mount(self) -> None:
        """Load initial directory."""
        await self._load_dir(self._current_dir)

    @staticmethod
    def _list_dir(path: Path) -> list[Path]:
        try:
            return sorted(
                path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return []

    def _dir_color(self) -> str:
        return self.app.get_css_variables().get("accent", "blue")

    @staticmethod
    def _entry_label(path: Path, dir_color: str) -> str:
        name = markup_escape(path.name)
        if path.is_dir():
            return f"[bold {dir_color}]{name}/[/bold {dir_color}]"
        if path.suffix.lower() != ".toml":
            return f"[dim]{name}[/dim]"
        return name

    async def _load_dir(self, path: Path, cursor_on: Path | None = None) -> None:
        self._current_dir = path
        self._entries = self._list_dir(path)
        dir_color = self._dir_color()

        lv = self.query_one("#pane-current", ListView)
        await lv.clear()
        for entry in self._entries:
            await lv.append(ListItem(Label(self._entry_label(entry, dir_color))))

        if cursor_on is not None and cursor_on in self._entries:
            lv.index = self._entries.index(cursor_on)
        elif self._entries:
            lv.index = 0

        lv.focus()
        self._update_parent_pane()
        self._update_preview_pane()

    def _update_parent_pane(self) -> None:
        parent = self._current_dir.parent
        pane = self.query_one("#pane-parent", Static)
        if parent == self._current_dir:
            pane.update("")
            return
        dir_color = self._dir_color()
        lines = []
        for entry in self._list_dir(parent):
            label = self._entry_label(entry, dir_color)
            if entry == self._current_dir:
                lines.append(f"[reverse]{label}[/reverse]")
            else:
                lines.append(label)
        pane.update("\n".join(lines))

    def _update_preview_pane(self) -> None:
        lv = self.query_one("#pane-current", ListView)
        pane = self.query_one("#pane-preview", Static)
        idx = lv.index
        if idx is None or idx >= len(self._entries):
            pane.update("")
            return
        entry = self._entries[idx]
        if entry.is_dir():
            dir_color = self._dir_color()
            sub = self._list_dir(entry)
            pane.update(
                "\n".join(self._entry_label(e, dir_color) for e in sub)
                if sub
                else "[dim]Empty directory[/dim]"
            )
        else:
            pane.update(
                self._read_text_preview(entry, dark=self.app.current_theme.dark)
            )

    @staticmethod
    def _read_text_preview(path: Path, *, dark: bool) -> str | Syntax:
        try:
            with path.open(encoding="utf-8", errors="strict") as f:
                text = f.read(4096)
            if path.suffix.lower() == ".toml":
                return Syntax(
                    text,
                    "toml",
                    theme="ansi_dark" if dark else "ansi_light",
                    background_color="default",
                )
            return markup_escape(text)
        except (UnicodeDecodeError, OSError):
            return "[dim]No Preview Available[/dim]"

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        """Update preview when cursor moves."""
        self._update_preview_pane()

    def _try_open_toml_preview(self) -> None:
        lv = self.query_one("#pane-current", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        if entry.is_dir() or entry.suffix.lower() != ".toml":
            return
        try:
            defn = ExperimentDefinition.load_toml(entry, ValidationMode.DATA_ONLY)
        except Exception as e:  # noqa: BLE001
            self.notify(str(e).splitlines()[0], severity="error")
            return
        self.app.push_screen(ExperimentPreviewModal(defn, entry, self.base_url))

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        """Open TOML preview on Enter; no-op on directories and non-TOML files."""
        self._try_open_toml_preview()

    def action_open_preview(self) -> None:
        """Open TOML preview for selected file; no-op on directories and non-TOML files."""
        self._try_open_toml_preview()

    def action_go_up(self) -> None:
        """Navigate to parent directory."""
        parent = self._current_dir.parent
        if parent != self._current_dir:
            old = self._current_dir
            self.call_later(lambda: self._load_dir(parent, old))

    def action_go_into(self) -> None:
        """Enter selected directory (no-op on files)."""
        lv = self.query_one("#pane-current", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._entries):
            return
        entry = self._entries[idx]
        if entry.is_dir():
            self.call_later(lambda: self._load_dir(entry))

    def action_view_mode(self) -> None:
        """Switch back to View-Mode."""
        self.app.switch_screen(OverViewScreen(self.base_url))


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
