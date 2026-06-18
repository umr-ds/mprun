"""Worker-View: list of all registered workers."""

from __future__ import annotations

from typing import Any, ClassVar, override

import httpx
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from mprun.client.client import _fmt_ts, purge_dead_workers
from mprun.client.tui import REFRESH_TIME
from mprun.client.tui.confirmations import ConfirmPurgeDialogue
from mprun.models import WorkerData


class WorkerView(Screen[None]):
    """Worker-View: list of all registered workers."""

    TITLE = "Workers"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("ctrl+r", "refresh", "Refresh"),
        ("g", "group", "Group"),
        ("p", "purge", "Purge"),
        ("v", "view_mode", "View-Mode"),
        ("c", "create_mode", "Create-Mode"),
    ]

    _STATE_ORDER: ClassVar[dict[str, int]] = {"IDLE": 0, "WORKING": 1, "DEAD": 2}

    def __init__(self, base_url: str) -> None:
        """Initialise Worker-View.

        Args:
            base_url: Server base URL.
        """
        super().__init__()
        self.base_url = base_url
        self._workers: list[dict[str, Any]] = []
        self._grouped: bool = False
        self._list_refs: list[int] = []  # index into _workers, -1 = header

    @override
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Header(show_clock=True)
        with Horizontal(id="content"):
            yield ListView(id="worker-list")
            yield Static("No worker selected", id="worker-panel")
        yield Footer()

    async def on_mount(self) -> None:
        """Start periodic refresh."""
        self.set_interval(REFRESH_TIME, self._refresh)
        await self._refresh()

    async def _refresh(self) -> None:
        """Fetch workers from server and update the list."""
        async with httpx.AsyncClient(base_url=self.base_url) as http:
            try:
                resp = await http.get("/workers")
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as err:
                self._show_error(f"HTTP {err.response.status_code}")
                return
            except httpx.RequestError as err:
                self._show_error(f"Connection error: {err}")
                return

        self._workers = sorted(data, key=lambda w: w.get("wid", 0))
        lv = self.query_one("#worker-list", ListView)
        prev_index = lv.index if lv.index is not None else 0

        await lv.clear()
        self._list_refs.clear()

        if self._grouped:
            buckets: dict[str, list[int]] = {"IDLE": [], "WORKING": [], "DEAD": []}
            for i, worker in enumerate(self._workers):
                state = worker.get("state", "DEAD")
                buckets.setdefault(state, []).append(i)
            for state in ("IDLE", "WORKING", "DEAD"):
                indices = buckets.get(state, [])
                if not indices:
                    continue
                state_colour = {"IDLE": "green", "WORKING": "yellow", "DEAD": "red"}[
                    state
                ]
                await lv.append(
                    ListItem(
                        Label(
                            f"[bold {state_colour}]{state}[/bold {state_colour}]"
                            f" ({len(indices)})"
                        )
                    )
                )
                self._list_refs.append(-1)
                for idx in indices:
                    w = self._workers[idx]
                    name = w.get("registration_data", {}).get("name", "?")
                    await lv.append(ListItem(Label(f"  {name}")))
                    self._list_refs.append(idx)
        else:
            for i, worker in enumerate(self._workers):
                name = worker.get("registration_data", {}).get("name", "?")
                state = worker.get("state", "?")
                await lv.append(ListItem(Label(f"{name}  [dim]{state}[/dim]")))
                self._list_refs.append(i)

        if self._list_refs:
            new_index = min(prev_index, len(self._list_refs) - 1)
            lv.index = new_index
            self._update_panel_for_index(new_index)
        else:
            self._update_panel(None)

    def _update_panel(self, worker: dict[str, Any] | None) -> None:
        """Render worker metadata into the detail panel."""
        panel = self.query_one("#worker-panel", Static)
        if worker is None:
            panel.update("No workers")
            return

        w = WorkerData.model_validate(worker)
        run_str = f"{w.run.eid}-{w.run.index}-{w.run.iteration}" if w.run else "none"

        state_colour = {"IDLE": "green", "WORKING": "yellow", "DEAD": "red"}.get(
            w.state, ""
        )

        panel.update(
            f"[bold]{w.registration_data.name}[/bold]\n\n"
            f"[dim]WID:[/dim]         {w.wid}\n"
            f"[dim]Backend:[/dim]     {w.registration_data.backend}\n"
            f"[dim]State:[/dim]       [{state_colour}]{w.state}[/{state_colour}]\n"
            f"[dim]Joined:[/dim]      {_fmt_ts(w.joined)}\n"
            f"[dim]Last check-in:[/dim] {_fmt_ts(w.last_check_in)}\n"
            f"[dim]Current run:[/dim]  {run_str}"
        )

    def _show_error(self, message: str) -> None:
        """Show an error in the worker panel."""
        panel = self.query_one("#worker-panel", Static)
        panel.update(f"[red]Error: {message}[/red]")

    def _update_panel_for_index(self, index: int) -> None:
        """Update the detail panel for a list index, skipping headers."""
        if index < 0 or index >= len(self._list_refs):
            self._update_panel(None)
            return
        ref = self._list_refs[index]
        if ref < 0:
            self._update_panel(None)
            return
        self._update_panel(self._workers[ref])

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        """Update worker panel when cursor moves."""
        lv = self.query_one("#worker-list", ListView)
        idx = lv.index
        if idx is not None:
            self._update_panel_for_index(idx)

    def action_refresh(self) -> None:
        """Refresh immediately."""
        self.call_later(self._refresh)

    def action_group(self) -> None:
        """Toggle grouping workers by state."""
        self._grouped = not self._grouped
        self.call_later(self._refresh)

    def action_purge(self) -> None:
        """Prompt for confirmation then purge dead workers."""

        def on_confirm(result: object) -> None:
            if result:
                self.call_later(self._do_purge)

        self.app.push_screen(ConfirmPurgeDialogue(), on_confirm)

    async def _do_purge(self) -> None:
        """Purge dead workers via the server and refresh the list."""
        try:
            async with httpx.AsyncClient(base_url=self.base_url) as client:
                await purge_dead_workers(client)
        except httpx.HTTPStatusError as err:
            self.notify(
                f"Purge failed: HTTP {err.response.status_code}", severity="error"
            )
            return
        except httpx.RequestError as err:
            self.notify(f"Purge failed: {err}", severity="error")
            return

        self.notify("Purged dead workers")
        await self._refresh()

    def action_view_mode(self) -> None:
        """Switch to View-Mode."""
        from mprun.client.tui.overview import OverViewScreen  # noqa: PLC0415

        self.app.switch_screen(OverViewScreen(self.base_url))

    def action_create_mode(self) -> None:
        """Switch to Create-Mode."""
        from mprun.client.tui.create import CreateScreen  # noqa: PLC0415

        self.app.switch_screen(CreateScreen(self.base_url))
