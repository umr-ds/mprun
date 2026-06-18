"""Confirmation dialogues."""

from __future__ import annotations

from typing import ClassVar, override

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


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


class ConfirmPurgeDialogue(ModalScreen[bool]):
    """Confirmation dialogue for purging dead workers."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("y", "confirm", "Yes"),
        ("n", "cancel", "No"),
        ("escape", "cancel", "No"),
    ]

    @override
    def compose(self) -> ComposeResult:
        """Create child widgets."""
        with Vertical(id="confirm-dialog"):
            yield Static(
                "Purge all dead workers? This cannot be undone.\n\n"
                "[bold][y][/bold][u]Y[/u]es[bold]/[n][/bold][u]N[/u]o"
            )

    def action_confirm(self) -> None:
        """Confirm purge."""
        confirmed: bool = True
        self.dismiss(confirmed)

    def action_cancel(self) -> None:
        """Cancel purge."""
        confirmed: bool = False
        self.dismiss(confirmed)
