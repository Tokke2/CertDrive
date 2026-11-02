"""Installed-printer discovery and persistent label-printer selection."""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

_logger = logging.getLogger("Intyg.labels")


def installed_printers() -> list[str]:
    """Return Windows-installed local/network printers, or an empty list."""
    if sys.platform != "win32":
        return []
    try:
        import win32print
    except ImportError:
        return []
    try:
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        rows = win32print.EnumPrinters(flags, None, 2)
    except Exception:
        _logger.exception("[LABEL] Kunde inte läsa installerade skrivare")
        return []

    names: dict[str, str] = {}
    for row in rows or ():
        if isinstance(row, dict):
            name = row.get("pPrinterName", "")
        elif isinstance(row, (tuple, list)) and len(row) > 1:
            name = row[1]
        else:
            name = ""
        name = str(name or "").strip()
        if name:
            names.setdefault(name.casefold(), name)
    return sorted(names.values(), key=str.casefold)


def default_printer() -> str:
    """Return the current Windows default printer if it can be determined."""
    if sys.platform != "win32":
        return ""
    try:
        import win32print

        return str(win32print.GetDefaultPrinter() or "").strip()
    except Exception:
        return ""


def load_printer_name(path: Path | str) -> str:
    """Load the saved printer name; an empty name means Windows default."""
    try:
        data: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("printer", "") or "").strip()


def save_printer_name(path: Path | str, printer_name: str) -> None:
    """Atomically persist a printer selection without touching customer data."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump({"version": 1, "printer": str(printer_name or "").strip()}, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
