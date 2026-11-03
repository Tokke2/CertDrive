"""Installed-printer discovery and persistent label-printer selection."""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger("Intyg.labels")


@dataclass(frozen=True, slots=True)
class PrinterInspection:
    name: str = ""
    driver: str = ""
    ready: bool = False
    message: str = ""


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


def inspect_printer(printer_name: str = "") -> PrinterInspection:
    """Check whether Windows can open a selected printer and read its driver."""
    if sys.platform != "win32":
        return PrinterInspection(message="Etikettutskrift kräver Windows.")
    try:
        import win32print
    except ImportError:
        return PrinterInspection(message="pywin32/Windows utskriftstjänst saknas.")

    try:
        name = str(printer_name or win32print.GetDefaultPrinter() or "").strip()
    except Exception as exc:
        return PrinterInspection(message=f"Windows standardskrivare kunde inte läsas: {exc}")
    if not name:
        return PrinterInspection(message="Ingen standardskrivare är vald i Windows.")

    handle: Any = None
    try:
        handle = win32print.OpenPrinter(name)
        info = win32print.GetPrinter(handle, 2)
        if isinstance(info, dict):
            driver = str(info.get("pDriverName", "") or "").strip()
            status = int(info.get("Status", 0) or 0)
        elif isinstance(info, (tuple, list)):
            driver = str(info[4] if len(info) > 4 else "").strip()
            status = int(info[18] if len(info) > 18 else 0)
        else:
            driver, status = "", 0

        problems = (
            ("PRINTER_STATUS_ERROR", "Fel rapporterat av skrivaren"),
            ("PRINTER_STATUS_PAPER_OUT", "Papper/etiketter saknas"),
            ("PRINTER_STATUS_OFFLINE", "Skrivaren är offline"),
            ("PRINTER_STATUS_PAPER_JAM", "Pappersstopp rapporterat"),
            ("PRINTER_STATUS_NO_TONER", "Förbrukningsmaterial saknas"),
            ("PRINTER_STATUS_DOOR_OPEN", "Skrivarluckan är öppen"),
            ("PRINTER_STATUS_NOT_AVAILABLE", "Skrivaren är inte tillgänglig"),
            ("PRINTER_STATUS_USER_INTERVENTION", "Skrivaren kräver åtgärd"),
        )
        messages = [
            text for constant, text in problems
            if status & int(getattr(win32print, constant, 0))
        ]
        if not driver:
            messages.append("Windows rapporterar ingen installerad skrivardrivrutin")
        ready = bool(driver) and not messages
        summary = "; ".join(messages) if messages else (
            "Windows-spoolern kan öppna skrivaren; fysisk utskrift testas separat."
        )
        return PrinterInspection(name=name, driver=driver, ready=ready, message=summary)
    except Exception as exc:
        return PrinterInspection(name=name, message=f"Skrivaren kunde inte öppnas: {exc}")
    finally:
        if handle is not None:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                _logger.debug("Kunde inte stänga skrivaren %s", name, exc_info=True)


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
