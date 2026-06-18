"""Create-Mode: file explorer and experiment submission."""

from __future__ import annotations

import math
from pathlib import Path
from typing import ClassVar, override

import httpx
from rich.markup import escape as markup_escape
from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from mprun.client.client import submit_experiment
from mprun.models import ExperimentDefinition, ValidationMode


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
            async with httpx.AsyncClient(base_url=base_url) as http:
                await submit_experiment(http, file_path)
        except Exception as e:  # noqa: BLE001
            self.notify(str(e).splitlines()[0], severity="error")
            return

        await self.dismiss()
        from mprun.client.tui.overview import OverViewScreen  # noqa: PLC0415

        await self.app.switch_screen(OverViewScreen(base_url))

    def action_cancel(self) -> None:
        """Close modal without submitting."""
        self.dismiss()


class CreateScreen(Screen[None]):
    """Create-Mode: three-pane file explorer."""

    TITLE = "Create-Mode"

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("v", "view_mode", "View-Mode"),
        ("w", "workers", "Workers"),
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
        from mprun.client.tui.overview import OverViewScreen  # noqa: PLC0415

        self.app.switch_screen(OverViewScreen(self.base_url))

    def action_workers(self) -> None:
        """Switch to Worker-View."""
        from mprun.client.tui.worker import WorkerView  # noqa: PLC0415

        self.app.switch_screen(WorkerView(self.base_url))
