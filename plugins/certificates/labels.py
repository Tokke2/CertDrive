from __future__ import annotations

"""
Fil:    plugins/certificates/labels.py
Sökväg: Intygsgenerator/plugins/certificates/labels.py
Version: 1.0.0 (ny, 2026-09-25)

Utskrift av postetiketter på Brother QL-500 (och andra Brother-skrivare)
via Brother b-PAC SDK (COM-objektet "bpac.Document") och P-touch-mallar
(.lbx) som användaren lägger i mappen Etiketter/ bredvid Mallar/.

- Etikettmallar: alla *.lbx direkt i Etiketter/ (Word/P-touch-tempfiler ignoreras).
- Fältfyllning: finns ett objekt i .lbx-mallen vars namn matchar ett intygsfält
  (t.ex. "REG", "VIN", "NAMN") fylls det med intygets värde; övrigt skrivs
  ut exakt som mallen ser ut.
- b-PAC saknas → tydligt fel + nedladdningslänk (stående order 6).
- Allt loggas via event-API:t (label.*). Inga tysta undantag.

Krav på Windows: "b-PAC Client Component" från Brother i samma bitness som
Python (64-bitars Python → 64-bitars b-PAC) samt QL-500-drivrutinen.
© Rickard Längkvist 2025–2026
"""

import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

__version__: Final[str] = "1.0.0"

_logger = logging.getLogger("Intyg.labels")

LABEL_FOLDER_NAME: Final[str] = "Etiketter"
BPAC_PROGID: Final[str] = "bpac.Document"
BPAC_DOWNLOAD_URL: Final[str] = "https://support.brother.com/g/s/es/dev/en/bpac/download/index.html"
_IGNORED_PREFIXES: Final[tuple[str, ...]] = ("~$", ".", "_")
_BPAC_PRINT_DEFAULT: Final[int] = 0x0  # bpoDefault


def _evt(event: str, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
    """Strukturerad loggpost (label.*) i appens centrala logg."""
    _logger.log(level, msg, extra={"event": event, "ctx": ctx or None})


@dataclass(slots=True)
class LabelResult:
    """Resultat av en etikettutskrift."""

    ok: bool
    label: str
    copies: int
    filled: list[str] = field(default_factory=list)
    error: str = ""
    duration: float = 0.0


def label_folder(templates_path: Path | None) -> Path | None:
    """Etiketter/ ligger bredvid Mallar/ (samma appkatalog)."""
    if templates_path is None:
        return None
    return Path(templates_path).parent / LABEL_FOLDER_NAME


def find_labels(folder: Path | None) -> list[Path]:
    """Alla giltiga .lbx-mallar direkt i Etiketter/, sorterade på namn."""
    if folder is None or not folder.is_dir():
        return []
    try:
        labels = [
            path for path in folder.iterdir()
            if path.suffix.casefold() == ".lbx"
            and not path.name.startswith(_IGNORED_PREFIXES)
            and path.is_file()
        ]
    except OSError as exc:
        _evt("label.scan_failed", f"Kunde inte läsa {folder}: {exc}",
             level=logging.WARNING, path=str(folder), error=str(exc))
        return []
    labels.sort(key=lambda path: path.name.casefold())
    return labels


def bpac_available() -> bool:
    """True om b-PAC COM-komponenten är registrerad (endast Windows)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, BPAC_PROGID):
            return True
    except OSError:
        return False


def print_label(
    label_path: Path,
    copies: int = 1,
    values: dict[str, str] | None = None,
    printer_name: str = "",
) -> LabelResult:
    """Skriv ut en .lbx-etikett via b-PAC. Körs i bakgrundstråd (COM initieras här)."""
    started = time.perf_counter()
    copies = max(1, min(99, int(copies)))
    name = label_path.name
    if sys.platform != "win32":
        return LabelResult(False, name, copies, error="Etikettutskrift via b-PAC kräver Windows")
    if not label_path.is_file():
        return LabelResult(False, name, copies, error=f"Etikettmallen saknas: {label_path}")
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return LabelResult(False, name, copies,
                           error="pywin32 saknas — installera paketet 'pywin32'")

    pythoncom.CoInitialize()
    document: Any = None
    filled: list[str] = []
    try:
        try:
            document = win32com.client.Dispatch(BPAC_PROGID)
        except Exception as exc:
            raise RuntimeError(
                "Brother b-PAC Client Component saknas eller har fel bitness "
                f"(64-bitars Python kräver 64-bitars b-PAC). Hämta: {BPAC_DOWNLOAD_URL}"
            ) from exc
        if not document.Open(str(label_path)):
            raise RuntimeError(f"b-PAC kunde inte öppna {name} (felkod {document.ErrorCode})")
        if printer_name:
            document.SetPrinter(printer_name, True)
        for key, value in (values or {}).items():
            obj = document.GetObject(str(key))
            if obj is not None:
                obj.Text = str(value)
                filled.append(str(key))
        document.StartPrint(label_path.stem, _BPAC_PRINT_DEFAULT)
        document.PrintOut(copies, _BPAC_PRINT_DEFAULT)
        if not document.EndPrint():
            raise RuntimeError(f"Utskriften misslyckades (b-PAC felkod {document.ErrorCode})")
        return LabelResult(True, name, copies, filled, duration=time.perf_counter() - started)
    except Exception as exc:
        _logger.exception("[LABEL] Utskrift av %s misslyckades", name)
        return LabelResult(False, name, copies, filled, error=str(exc),
                           duration=time.perf_counter() - started)
    finally:
        if document is not None:
            try:
                document.Close()
            except Exception:
                _logger.exception("[LABEL] Kunde inte stänga b-PAC-dokument")
        pythoncom.CoUninitialize()
