"""Shared logging configuration."""

from __future__ import annotations

import logging
from typing import ClassVar, override

_FMT = "%(asctime)s %(levelname)s:%(name)-24s: \x00%(message)s"


class ColorFormatter(logging.Formatter):
    """Logging formatter that colours each line by level and renders the message in white."""

    _COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: "\033[90m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[31m",
    }
    _RESET = "\033[0m"
    _WHITE = "\033[97m"

    @override
    def format(self, record: logging.LogRecord) -> str:
        """Format ``record`` with ANSI color codes."""
        color = self._COLORS.get(record.levelno, "")
        full = super().format(record)
        prefix, _, msg = full.partition("\x00")
        return f"{color}{prefix}{self._WHITE}{msg}{self._RESET}"


def configure_logging(level: int) -> None:
    """Configure root logger with ``ColorFormatter`` at ``level``."""
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter(fmt=_FMT))
    logging.basicConfig(level=level, handlers=[handler])
