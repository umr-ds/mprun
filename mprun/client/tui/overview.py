"""Over-View: browsable list of all experiments."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, override

import httpx
from rapidfuzz import fuzz
from rapidfuzz import process as fuzz_process
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from mprun.client.client import (
    _fmt_duration,
    _fmt_ts,
    delete_experiment,
    get_experiment_results,
)
from mprun.client.tui import REFRESH_TIME
from mprun.client.tui.confirmations import (
    ConfirmDeleteDialogue,
    ConfirmDownloadDialogue,
)
from mprun.client.tui.detail import DetailView
from mprun.models import Experiment


class OverViewScreen(Screen[None]):
    """Over-View: browsable list of all experiments."""

    TITLE = "Over-View"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+r", "refresh", "Refresh"),
        ("d", "download", "Download"),
        ("f", "search", "Find"),
        ("c", "create", "Create-Mode"),
        ("w", "workers", "Workers"),
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
            f"[dim]Runtime:[/dim]      {_fmt_duration(experiment.started_running, experiment.finished_running)}\n"
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

        try:
            async with httpx.AsyncClient(base_url=self.base_url) as client:
                saved, skipped, failed = await get_experiment_results(
                    client, experiment, out_dir
                )
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

        try:
            async with httpx.AsyncClient(base_url=self.base_url) as client:
                await delete_experiment(client, eid)
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
        from mprun.client.tui.create import CreateScreen  # noqa: PLC0415

        self.app.switch_screen(CreateScreen(self.base_url))

    def action_workers(self) -> None:
        """Switch to Worker-View."""
        if self._search_mode:
            return
        from mprun.client.tui.worker import WorkerView  # noqa: PLC0415

        self.app.switch_screen(WorkerView(self.base_url))

    def action_refresh(self) -> None:
        """Refresh immediately."""
        self.call_later(self._refresh)
