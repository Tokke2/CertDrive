from __future__ import annotations

"""
main.pyw
Intygsgenerator — bootstrap, single-instance, pluginlivscykel och huvudfönster.

Huvudfönstret skapar aldrig en QSystemTrayIcon. Den enda tray-ikonen ägs av
plugins/system_tray/.

Appversion 0.0.1 (versionsnumret ändras endast på uttrycklig begäran)
====================================================
1. ERBJUD INSTALLATION AV SAKNADE TILLÄGG: startup-inventarie
   (DependencyInventory) + dialog efter ready: "📦 Installera paket"
   (pip i bakgrundstråd, dep.*-loggad), "🌐 Office-alternativ" (webb +
   kopierbart winget/brew/apt-kommando), "Senare", "Visa inte igen".
2. 2000000% BÄTTRE AUTOSTART: AutostartGuard — självläkande multi-OS
   (Windows-registret med pythonw, macOS LaunchAgent, Linux XDG .desktop),
   verify-after-write + repair, autostart.*-events.
3. ADAPTIV UI: alla fasta pixelmått skalas med fontmetrik via _scale()
   (§17/§30); radbrytning på header-undertext.
4. LOGGNING AV PRECIS ALLT: main.*-events — setup-steg med ms, lock, tabs,
   geometry-beslut, tray-tillgänglighet, ready, cleanup.
"""

import json
import logging
import os
import platform
import re
import shutil
import signal
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

__version__: str = "0.0.1"
__all__: list[str] = [
    "SingleInstanceLock",
    "InstanceActivationServer",
    "MainWindow",
    "Application",
    "main",
]

_MIN_PYTHON: tuple[int, int] = (3, 12)
if sys.version_info < _MIN_PYTHON:
    print(
        f"Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+ krävs.",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(1)

# Måste sättas före QApplication för att undvika Qt-fontbrus vid startup.
os.environ.setdefault(
    "QT_LOGGING_RULES",
    "qt.qpa.fonts.warning=false;qt.text.font.db=false",
)

APP_PATH: Path = Path(__file__).resolve().parent
if str(APP_PATH) not in sys.path:
    sys.path.insert(0, str(APP_PATH))

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_SYSTEM: str = platform.system()
_IS_WINDOWS: bool = _SYSTEM == "Windows"
_IS_MACOS: bool = _SYSTEM == "Darwin"

_logger: logging.Logger = logging.getLogger("Intygsgenerator.main")


def _evt(event: str, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
    """Fail-safe strukturerad loggpost (main.*) — får aldrig krascha booten."""
    try:
        _logger.log(level, msg, extra={"event": event, "ctx": ctx or None})
    except Exception:
        pass


def _scale(base: int) -> int:
    """Skalar pixelmått efter fontmetrik (DPI/teckenstorlek) — §17/§30.

    Fail-safe: utan QApplication/fontmetrik returneras basmåttet.
    """
    try:
        app = QApplication.instance()
        if app is not None:
            height = app.fontMetrics().height()
            if height > 0:
                return max(base, int(round(base * height / 16.0)))
    except Exception:
        pass
    return base


class DependencyInventory:
    """Inventerar saknade tillägg per OS och kan installera dem (10.8.0)."""

    SUITE_URLS: dict[str, str] = {
        "LibreOffice": "https://www.libreoffice.org/download/download-libreoffice/",
        "OpenOffice": "https://www.openoffice.org/download/",
        "Microsoft Word": "https://www.microsoft.com/microsoft-365/word",
    }

    @staticmethod
    def _can_import(module: str) -> bool:
        try:
            __import__(module)
            return True
        except Exception:
            return False

    @classmethod
    def missing_python_packages(cls) -> list[dict[str, str]]:
        missing: list[dict[str, str]] = []
        for module, why in (
            ("pypdf", "PDF-sammanslagning (primär motor, §7)"),
            ("psutil", "Minneskontroll i diagnostik"),
        ):
            if not cls._can_import(module):
                missing.append({"name": module, "why": why})
        if _IS_WINDOWS and not cls._can_import("win32com.client"):
            missing.append({"name": "pywin32", "why": "MS Word som PDF-konverterare (Word COM)"})
        if (_IS_WINDOWS or _IS_MACOS) and not cls._can_import("docx2pdf"):
            missing.append({"name": "docx2pdf", "why": "Alternativ DOCX→PDF via Word"})
        return missing

    @classmethod
    def office_suites(cls) -> list[dict[str, Any]]:
        """Status + OS-specifika installationskommandon för office-sviter."""
        import glob as _glob

        def _lo_found() -> bool:
            if _IS_WINDOWS:
                roots = [os.environ.get(k, "") for k in
                         ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432")]
                if any(r and _glob.glob(os.path.join(r, "LibreOffice*", "program", "soffice.exe"))
                       for r in roots):
                    return True
                return bool(shutil.which("soffice.exe") or shutil.which("libreoffice.exe"))
            if _IS_MACOS:
                return bool(_glob.glob("/Applications/LibreOffice*.app/Contents/MacOS/soffice"))
            return bool(shutil.which("libreoffice") or shutil.which("soffice"))

        def _oo_found() -> bool:
            if _IS_WINDOWS:
                roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)")]
                return any(r and _glob.glob(os.path.join(r, "OpenOffice*", "program", "soffice.exe"))
                           for r in roots)
            if _IS_MACOS:
                return bool(_glob.glob("/Applications/OpenOffice.app/Contents/MacOS/soffice"))
            return bool(shutil.which("openoffice"))

        def _hint(suite: str) -> str:
            if suite == "LibreOffice":
                return ("winget install TheDocumentFoundation.LibreOffice" if _IS_WINDOWS else
                        "brew install --cask libreoffice" if _IS_MACOS else
                        "sudo apt install libreoffice")
            if suite == "OpenOffice":
                return ("winget install Apache.OpenOffice" if _IS_WINDOWS else
                        "brew install --cask openoffice" if _IS_MACOS else
                        "sudo apt install openoffice")
            return ""

        word_usable = (_IS_WINDOWS and cls._can_import("win32com.client")) or cls._can_import("docx2pdf")
        return [
            {"name": "Microsoft Word", "found": word_usable,
             "note": "används via COM/docx2pdf", "hint": "",
             "url": cls.SUITE_URLS["Microsoft Word"]},
            {"name": "LibreOffice", "found": _lo_found(),
             "note": "alla OS, headless", "hint": _hint("LibreOffice"),
             "url": cls.SUITE_URLS["LibreOffice"]},
            {"name": "OpenOffice", "found": _oo_found(),
             "note": "samma soffice-CLI", "hint": _hint("OpenOffice"),
             "url": cls.SUITE_URLS["OpenOffice"]},
        ]

    @staticmethod
    def pip_install(package: str, on_done: "Callable[[bool, str], None] | None" = None) -> threading.Thread:
        """Installera Python-paket via pip i bakgrundstråd (blockerar aldrig UI)."""

        def run() -> None:
            _evt("dep.install_started", f"Installerar {package} …", package=package)
            ok, tail = False, ""
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", package],
                    capture_output=True, text=True, timeout=600,
                )
                ok = proc.returncode == 0
                tail = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
            except Exception as exc:
                tail = f"{type(exc).__name__}: {exc}"
            if ok:
                _evt("dep.install_done", f"{package} installerat", package=package)
            else:
                _evt("dep.install_failed", f"Installation av {package} misslyckades",
                     level=logging.ERROR, package=package, detail=tail[-300:])
            if callable(on_done):
                on_done(ok, tail)

        thread = threading.Thread(target=run, name=f"pip-{package}", daemon=True)
        thread.start()
        return thread


class AutostartGuard:
    """Självläkande multi-OS-autostart (10.8.0, '2000000% bättre').

    Installerar, verifierar (verify-after-write) och reparerar autostart-posten
    på Windows (HKCU Run + pythonw), macOS (LaunchAgent) och Linux (XDG
    .desktop). Allt loggas med autostart.*-events; fel är icke-kritiska.
    """

    ENTRY: str = "Intygsgenerator"
    _MAC_LABEL: str = "com.rickardlangkvist.intygsgenerator"

    def __init__(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    @staticmethod
    def _program_arguments() -> list[str]:
        """Skapa samma startargument för alla operativsystem och paketformat."""
        if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
            return [str(Path(sys.executable).resolve())]

        interpreter = sys.executable
        if _IS_WINDOWS:
            # pythonw undviker konsolfönster men behåller python.exe som fallback.
            pythonw = Path(interpreter).with_name("pythonw.exe")
            if pythonw.is_file():
                interpreter = str(pythonw)
        return [interpreter, str(Path(__file__).resolve())]

    @classmethod
    def _command(cls) -> str:
        args = cls._program_arguments()
        if _IS_WINDOWS:
            return subprocess.list2cmdline(args)
        return shlex.join(args)

    def ensure(self) -> None:
        try:
            if _IS_WINDOWS:
                self._windows()
            elif _IS_MACOS:
                self._macos()
            else:
                self._linux()
        except Exception as exc:
            _evt("autostart.failed", f"Autostart-fel: {exc}",
                 level=logging.ERROR, error=f"{type(exc).__name__}: {exc}")

    # ── Windows: registret ────────────────────────────────────────────────
    def _windows(self) -> None:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            if not self.enabled:
                try:
                    winreg.DeleteValue(key, self.ENTRY)
                    _evt("autostart.removed", "Autostart borttagen (Windows)")
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    _evt("autostart.remove_failed", f"Kunde inte ta bort autostart: {exc}",
                         level=logging.WARNING, error=str(exc))
                return
            command = self._command()
            current: Any = None
            try:
                current, _ = winreg.QueryValueEx(key, self.ENTRY)
            except OSError:
                current = None
            if current == command:
                _evt("autostart.verified", "Autostart redan korrekt (Windows)",
                     level=logging.DEBUG)
                return
            winreg.SetValueEx(key, self.ENTRY, 0, winreg.REG_SZ, command)
            again, _ = winreg.QueryValueEx(key, self.ENTRY)
            if again == command:
                _evt("autostart.installed", "Autostart installerad + verifierad (Windows)")
            else:
                _evt("autostart.verify_failed", "Autostart skrevs men verifierades inte (Windows)",
                     level=logging.WARNING)

    # ── macOS: LaunchAgent ────────────────────────────────────────────────
    def _macos(self) -> None:
        import plistlib

        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{self._MAC_LABEL}.plist"
        if not self.enabled:
            if plist_path.exists():
                plist_path.unlink()
                _evt("autostart.removed", "Autostart borttagen (macOS)")
            return
        data = {
            "Label": self._MAC_LABEL,
            "ProgramArguments": self._program_arguments(),
            "RunAtLoad": True,
        }
        payload = plistlib.dumps(data)
        if plist_path.exists() and plist_path.read_bytes() == payload:
            _evt("autostart.verified", "Autostart redan korrekt (macOS)", level=logging.DEBUG)
            return
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_bytes(payload)
        if plist_path.read_bytes() == payload:
            _evt("autostart.installed", "Autostart installerad + verifierad (macOS)")
        else:
            _evt("autostart.verify_failed", "Autostart verifierades inte (macOS)",
                 level=logging.WARNING)

    # ── Linux: XDG .desktop ───────────────────────────────────────────────
    @staticmethod
    def _desktop_icon() -> str:
        for name in ("icon.svg", "icon.png", "icon.ico"):
            path = APP_PATH / "Icon" / name
            if path.is_file():
                return str(path)
        return ""

    def _linux(self) -> None:
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        desktop = Path(xdg) / "autostart" / "intygsgenerator.desktop"
        if not self.enabled:
            if desktop.exists():
                desktop.unlink()
                _evt("autostart.removed", "Autostart borttagen (Linux)")
            return
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=IntygsDrive\n"
            f"Exec={self._command()}\n"
            f"Icon={self._desktop_icon()}\n"
            "Terminal=false\n"
            "StartupNotify=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        if desktop.exists() and desktop.read_text(encoding="utf-8") == content:
            _evt("autostart.verified", "Autostart redan korrekt (Linux)", level=logging.DEBUG)
            return
        desktop.parent.mkdir(parents=True, exist_ok=True)
        desktop.write_text(content, encoding="utf-8")
        with suppress(OSError):
            desktop.chmod(0o755)
        if desktop.read_text(encoding="utf-8") == content:
            _evt("autostart.installed", "Autostart installerad + verifierad (Linux)")
        else:
            _evt("autostart.verify_failed", "Autostart verifierades inte (Linux)",
                 level=logging.WARNING)


def _stderr(message: str) -> None:
    """Skriv ett bootstrap-fel utan att förutsätta att loggpluginet laddats."""
    print(message, file=sys.stderr, flush=True)


def _configure_platform() -> None:
    """Gör små, säkra plattformsspecifika förberedelser."""
    if _IS_WINDOWS:
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "RickardLangkvist.Intygsgenerator"
            )
        except (AttributeError, OSError) as exc:
            _logger.debug("Kunde inte sätta Windows AppUserModelID: %s", exc)
    elif _IS_MACOS:
        os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


_configure_platform()

try:
    from core import (
        APP_DISPLAY_NAME,
        APP_NAME,
        FOLDER_BACKUP,
        FOLDER_CONFIG,
        FOLDER_ICONS,
        FOLDER_LOGS,
        FOLDER_OUTPUT,
        FOLDER_PLUGINS,
        FOLDER_SENT,
        FOLDER_TEMPLATES,
        SESSION_ID,
        AppContext,
        ConfigManager,
        PluginManager,
    )
    from core.updater import (
        check_latest_release,
        get_current_commit,
        launch_pending_update_helper,
        stage_release_update,
        UPDATE_INTERVAL_HOURS,
    )
except Exception as exc:
    _stderr(f"KRITISKT: core/ kunde inte laddas: {exc}")
    _stderr(traceback.format_exc())
    raise SystemExit(2) from exc

try:
    from PySide6.QtCore import (
        QEvent,
        QIODevice,
        QObject,
        QPoint,
        QRect,
        QRectF,
        QSize,
        Qt,
        QTimer,
        Signal,
        Slot,
    )
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtGui import (
        QAction,
        QBrush,
        QColor,
        QCloseEvent,
        QIcon,
        QLinearGradient,
        QPainter,
        QPen,
        QPixmap,
    )
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMenu,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QStatusBar,
        QStyle,
        QSystemTrayIcon,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
except Exception as exc:
    _stderr(f"PySide6 saknas eller kunde inte laddas: {exc}")
    raise SystemExit(3) from exc

class _UpdateCheckSignals(QObject):
    """Marshala resultat från releasekontrollen tillbaka till Qt-tråden."""

    completed = Signal(object)


APP_VERSION: str = __version__
_INSTANCE_NAME: str = APP_NAME


def _instance_channel_name(name: str) -> str:
    """Skapa ett kort, plattformsoberoende namn för QLocalServer."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"{slug or 'intygsgenerator'}-activate"


def _coerce_int(value: Any, default: int) -> int:
    """Konvertera säkert ett konfigvärde till heltal."""
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _make_fallback_icon(size: int = 96) -> QIcon:
    """Skapa en modern vektorliknande dokumentikon via QPainter.

    COLORS-001 tillåter hårdkodade färger för just QPainter-fallbackikonen.
    """
    edge = max(32, int(size))
    pixmap = QPixmap(edge, edge)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    margin = max(3, edge // 16)
    canvas = QRectF(margin, margin, edge - 2 * margin, edge - 2 * margin)
    radius = max(8.0, edge * 0.22)
    gradient = QLinearGradient(canvas.topLeft(), canvas.bottomRight())
    gradient.setColorAt(0.0, QColor(38, 128, 235))
    gradient.setColorAt(1.0, QColor(91, 57, 196))
    painter.setPen(QPen(QColor(26, 84, 166), max(1, edge // 30)))
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(canvas, radius, radius)

    document = QRectF(
        edge * 0.30,
        edge * 0.23,
        edge * 0.40,
        edge * 0.54,
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 245))
    painter.drawRoundedRect(document, edge * 0.04, edge * 0.04)

    line_pen = QPen(
        QColor(54, 106, 192),
        max(2, edge // 25),
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    painter.setPen(line_pen)
    painter.drawLine(
        int(edge * 0.38), int(edge * 0.40),
        int(edge * 0.62), int(edge * 0.40),
    )
    painter.drawLine(
        int(edge * 0.38), int(edge * 0.50),
        int(edge * 0.56), int(edge * 0.50),
    )

    check_pen = QPen(
        QColor(40, 184, 121),
        max(3, edge // 18),
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    painter.setPen(check_pen)
    painter.drawLine(
        int(edge * 0.39), int(edge * 0.64),
        int(edge * 0.47), int(edge * 0.70),
    )
    painter.drawLine(
        int(edge * 0.47), int(edge * 0.70),
        int(edge * 0.63), int(edge * 0.58),
    )
    painter.end()
    return QIcon(pixmap)


def _load_app_icon() -> QIcon:
    """Ladda en högupplöst appikon eller använd en säker QPainter-fallback."""
    icon_folder = APP_PATH / FOLDER_ICONS
    for filename in (
        "icon.svg",
        "icon.png",
        "icon.ico",
        "app.svg",
        "app.png",
        "app.ico",
        "icon.icns",
    ):
        candidate = icon_folder / filename
        if not candidate.is_file():
            continue
        icon = QIcon(str(candidate))
        if not icon.isNull():
            return icon
    return _make_fallback_icon()


class SingleInstanceLock:
    """OS-nativt fil-lås med PID-metadata.

    Windows använder msvcrt.locking och Linux/macOS använder fcntl.flock.
    Lockfilen lämnas kvar när processen stängs för att undvika en unlink-race;
    nästa ägare skriver alltid över metadata efter ett lyckat lås.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._file = self._lock_path()
        self._metadata_file = self._file.with_name(f"{self._file.name}.json")
        self._handle: Any | None = None
        self._locked = False
        self._last_error = ""

    @property
    def last_error(self) -> str:
        return self._last_error

    def _lock_path(self) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", self._name.casefold()).strip("-")
        return Path(tempfile.gettempdir()) / f"{slug or 'intygsgenerator'}.lock"

    def acquire(self) -> bool:
        """Försök bli den enda processen som äger låset."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._file.open("a+b")
            self._ensure_lock_byte()
            self._handle.seek(0)

            if _IS_WINDOWS:
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(
                    self._handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )

            self._locked = True
            self._write_metadata()
            return True
        except (OSError, ImportError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._close_handle()
            return False
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            _logger.exception("Single-instance-lås kunde inte skapas")
            self._close_handle()
            return False

    def _ensure_lock_byte(self) -> None:
        if self._handle is None:
            raise RuntimeError("Lockfilens filhandtag saknas")
        self._handle.seek(0, os.SEEK_END)
        if self._handle.tell() == 0:
            self._handle.write(b"\0")
            self._handle.flush()
        self._handle.seek(0)

    def _write_metadata(self) -> None:
        """Skriv PID-metadata atomiskt i en separat JSON-fil.

        Lockfilens första byte får vara orörd medan den är låst, vilket gör
        msvcrt-lås stabila även på Windows.
        """
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "version": APP_VERSION,
                "started_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        )
        temp_path = self._metadata_file.with_name(
            f"{self._metadata_file.name}.{os.getpid()}.tmp"
        )
        try:
            temp_path.write_text(payload, encoding="utf-8")
            os.replace(temp_path, self._metadata_file)
        except OSError as exc:
            _logger.warning("Kunde inte skriva instansmetadata: %s", exc)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                _logger.debug(
                    "Kunde inte rensa temporär instansmetadata", exc_info=True
                )

    def get_pid(self) -> int | None:
        """Läs ägarens PID från den atomiska JSON-metadatan."""
        try:
            raw = self._metadata_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        pid = data.get("pid") if isinstance(data, dict) else None
        if isinstance(pid, bool):
            return None
        try:
            return int(pid) if int(pid) > 0 else None
        except (TypeError, ValueError):
            return None

    def release(self) -> None:
        """Släpp OS-låset och stäng handtaget idempotent."""
        if self._handle is None:
            return
        try:
            if self._locked:
                self._handle.seek(0)
                if _IS_WINDOWS:
                    import msvcrt

                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError) as exc:
            _logger.warning("Kunde inte släppa single-instance-lås: %s", exc)
        finally:
            self._close_handle()
            self._locked = False

    def _close_handle(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        except OSError as exc:
            _logger.debug("Kunde inte stänga lockfil: %s", exc)
        finally:
            self._handle = None


class InstanceActivationServer(QObject):
    """Lokal IPC-server som fokuserar första instansen vid dubbelstart."""

    def __init__(self, name: str, window: "MainWindow") -> None:
        super().__init__()
        self._name = _instance_channel_name(name)
        self._window = window
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

    def start(self) -> bool:
        """Starta IPC-servern efter att fil-låset ägs av denna process."""
        try:
            QLocalServer.removeServer(self._name)
            if self._server.listen(self._name):
                return True
            _logger.warning(
                "Kunde inte starta instanskanal %s: %s",
                self._name,
                self._server.errorString(),
            )
        except Exception:
            _logger.exception("Kunde inte starta instanskanal %s", self._name)
        return False

    def close(self) -> None:
        """Stäng IPC-servern och rensa dess lokala kanal."""
        try:
            self._server.close()
            QLocalServer.removeServer(self._name)
        except Exception:
            _logger.exception("Kunde inte stänga instanskanal %s", self._name)

    @classmethod
    def notify_existing_instance(
        cls,
        name: str,
        timeout_ms: int = 700,
    ) -> bool:
        """Skicka ett kort aktiveringsmeddelande till första instansen."""
        socket = QLocalSocket()
        try:
            socket.connectToServer(
                _instance_channel_name(name),
                QIODevice.OpenModeFlag.WriteOnly,
            )
            if not socket.waitForConnected(timeout_ms):
                return False
            socket.write(b"activate\n")
            socket.flush()
            socket.waitForBytesWritten(timeout_ms)
            socket.disconnectFromServer()
            return True
        except Exception:
            _logger.debug("Kunde inte signalera befintlig instans", exc_info=True)
            return False
        finally:
            socket.deleteLater()

    @Slot()
    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            socket.setParent(self)
            socket.readyRead.connect(lambda sock=socket: self._read_command(sock))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable() > 0:
                self._read_command(socket)

    def _read_command(self, socket: QLocalSocket) -> None:
        command = bytes(socket.readAll()).decode("utf-8", errors="replace").strip()
        if command == "activate":
            self._window.show_signal.emit()
        socket.disconnectFromServer()


@dataclass(slots=True)
class _TabRecord:
    """En pluginflik och dess användarvalda synlighet."""

    name: str
    widget: QWidget
    icon: QIcon
    visible: bool = True


class MainWindow(QMainWindow):
    """Responsivt huvudfönster med flikhantering och native ikoner.

    Skapar ingen tray-ikon. X minimerar till tray när tray-pluginet faktiskt är
    tillgängligt, och flikar kan visas/döljas via Visa-menyn eller högerklick.
    """

    show_signal = Signal()
    hide_signal = Signal()
    quit_signal = Signal()
    status_signal = Signal(str)
    templates_scanned_signal = Signal(object)

    def __init__(self, app: "Application") -> None:
        super().__init__()
        self._app = app
        self._icon = QIcon()
        self._tabs: QTabWidget | None = None
        self._status_label: QLabel | None = None
        self._template_label: QLabel | None = None
        self._view_menu: QMenu | None = None
        self._tab_records: list[_TabRecord] = []
        self._hidden_tabs: set[str] = set()
        self._empty_tab: QWidget | None = None
        self._template_names: list[str] = []
        self._templates_count = 0
        self._templates_scanned = False

        self.show_signal.connect(self._on_show)
        self.hide_signal.connect(self._on_hide)
        self.quit_signal.connect(self._on_quit)
        self.status_signal.connect(self._on_status_message)
        self.templates_scanned_signal.connect(self._on_templates_scanned)

    def setup(self, icon: QIcon) -> None:
        """Bygg det lätta fönsterskalet före pluginflikar laddas lazy."""
        self._icon = icon
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(icon)
        # 10.8.0: adaptiva minimått (DPI/fontmetrik, §17/§30).
        self.setMinimumSize(_scale(760), _scale(560))
        self.resize(1120, 760)
        self.setAccessibleName(APP_DISPLAY_NAME)
        self._build_menu()
        self._build_central()
        self._build_statusbar()
        self.show_loading_state()

    def set_context(self, context: Any) -> None:
        """Gör QTabWidget tillgänglig för kompatibla core-/pluginfunktioner."""
        if context is None or self._tabs is None:
            return
        try:
            context._notebook = self._tabs
        except Exception:
            _logger.exception("Kunde inte registrera huvudflikar i AppContext")

    def show_loading_state(self) -> None:
        """Visa ett billigt, professionellt laddningsläge innan plugin-UI byggs."""
        if self._tabs is None:
            return
        self._tabs.clear()
        loading = QWidget(self)
        layout = QVBoxLayout(loading)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel(loading)
        icon_label.setPixmap(self._icon.pixmap(QSize(_scale(72), _scale(72))))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        title = QLabel("Förbereder IntygsDrive", loading)
        title_font = title.font()
        title_font.setPointSize(max(15, title_font.pointSize() + 5))
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "Laddar pluginmoduler och startar mallskanning i bakgrunden…",
            loading,
        )
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        progress = QProgressBar(loading)
        progress.setRange(0, 0)
        progress.setMaximumWidth(_scale(340))
        progress.setMinimumHeight(_scale(18))
        layout.addWidget(progress, alignment=Qt.AlignmentFlag.AlignCenter)
        self._tabs.addTab(
            loading,
            self._standard_icon("SP_ComputerIcon"),
            "Start",
        )

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&Arkiv")
        self._add_action(
            file_menu,
            "Minimera till systemfältet",
            "SP_TitleBarMinButton",
            self.hide_signal.emit,
        )
        file_menu.addSeparator()
        self._add_action(
            file_menu,
            "Avsluta",
            "SP_DialogCloseButton",
            self.quit_signal.emit,
        )

        self._view_menu = bar.addMenu("&Visa")

        tools_menu = bar.addMenu("&Verktyg")
        folders: tuple[tuple[str, str, Path], ...] = (
            ("Öppna mallar", "SP_DirOpenIcon", APP_PATH / FOLDER_TEMPLATES),
            ("Öppna output", "SP_DirOpenIcon", APP_PATH / FOLDER_OUTPUT),
            ("Öppna plugins", "SP_DirOpenIcon", APP_PATH / FOLDER_PLUGINS),
            ("Öppna loggar", "SP_FileDialogDetailedView", APP_PATH / FOLDER_LOGS),
            ("Öppna backup", "SP_DriveHDIcon", APP_PATH / FOLDER_BACKUP),
            ("Öppna skickade", "SP_DirOpenIcon", APP_PATH / FOLDER_SENT),
        )
        for label, icon_name, folder in folders:
            self._add_action(
                tools_menu,
                label,
                icon_name,
                lambda _checked=False, path=folder: self._open_folder(path),
            )

        help_menu = bar.addMenu("&Hjälp")
        self._add_action(
            help_menu,
            f"Om {APP_DISPLAY_NAME}",
            "SP_MessageBoxInformation",
            self._show_about,
        )

    def _build_central(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 2)
        layout.setSpacing(8)
        layout.addWidget(self._build_header())

        self._tabs = QTabWidget(central)
        self._tabs.setDocumentMode(True)
        self._tabs.setUsesScrollButtons(True)
        self._tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self._tabs.setIconSize(QSize(_scale(18), _scale(18)))
        self._tabs.setAccessibleName("Arbetsflikar")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        tab_bar = self._tabs.tabBar()
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._show_tab_context_menu)
        layout.addWidget(self._tabs, stretch=1)

    def _build_header(self) -> QWidget:
        frame = QFrame(self)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        icon_label = QLabel(frame)
        icon_label.setPixmap(self._icon.pixmap(QSize(_scale(42), _scale(42))))
        icon_label.setFixedSize(QSize(_scale(46), _scale(46)))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(1)
        title = QLabel(APP_DISPLAY_NAME, frame)
        title_font = title.font()
        title_font.setPointSize(max(13, title_font.pointSize() + 3))
        title_font.setBold(True)
        title.setFont(title_font)
        text_layout.addWidget(title)
        subtitle = QLabel("Fordonsintyg • mallar • PDF", frame)
        subtitle.setWordWrap(True)  # 10.8.0: text klippas aldrig (§30)
        subtitle.setToolTip("Mallar läses från Mallar/<MallNamn>/ i bakgrunden.")
        text_layout.addWidget(subtitle)
        layout.addLayout(text_layout, stretch=1)

        status = QLabel("Pluginbaserad • lokal • snabb", frame)
        status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        status.setToolTip("Moduler laddas isolerat och kommunicerar via EventBus.")
        layout.addWidget(status)
        return frame

    def _build_statusbar(self) -> None:
        statusbar = QStatusBar(self)
        self.setStatusBar(statusbar)
        self._status_label = QLabel("Startar…", statusbar)
        self._status_label.setMinimumWidth(_scale(260))
        self._status_label.setToolTip("Aktuell status")
        statusbar.addWidget(self._status_label, stretch=1)

        self._template_label = QLabel("Mallar: väntar", statusbar)
        self._template_label.setToolTip("Mallskanningen körs i bakgrunden.")
        statusbar.addPermanentWidget(self._template_label)
        statusbar.addPermanentWidget(QLabel(COPYRIGHT, statusbar))

    def _standard_icon(self, name: str) -> QIcon:
        fallback = QStyle.StandardPixmap.SP_FileIcon
        pixmap_type = getattr(QStyle.StandardPixmap, name, fallback)
        return self.style().standardIcon(pixmap_type)

    def _add_action(
        self,
        menu: QMenu,
        text: str,
        icon_name: str,
        callback: Callable[..., Any],
    ) -> QAction:
        action = QAction(self._standard_icon(icon_name), text, menu)
        action.triggered.connect(lambda _checked=False: callback())
        menu.addAction(action)
        return action

    def populate_tabs(self) -> None:
        """Montera pluginflikar i ett repaint-pass för att undvika UI-flimmer."""
        if self._tabs is None:
            return
        manager = self._app.plugin_manager
        if manager is None:
            self._show_plugin_error("Pluginhanteraren är inte tillgänglig.")
            return

        try:
            plugin_tabs = manager.get_tabs()
        except Exception:
            _logger.exception("Kunde inte hämta pluginflikar")
            self._show_plugin_error("Pluginflikarna kunde inte laddas. Se loggarna.")
            return

        records: list[_TabRecord] = []
        seen_names: set[str] = set()
        for raw_name, widget in plugin_tabs:
            name = str(raw_name).strip() or "Plugin"
            if name in seen_names or not isinstance(widget, QWidget):
                continue
            seen_names.add(name)
            records.append(_TabRecord(name, widget, self._tab_icon(name)))

        self._tab_records = records
        self._hidden_tabs = self._load_hidden_tabs() & seen_names
        for record in self._tab_records:
            record.visible = record.name not in self._hidden_tabs
        _evt("main.tabs_populated", f"{len(records)} pluginflikar monterade",
             tabs=[record.name for record in records],
             visible=sum(1 for record in records if record.visible))

        self._tabs.setUpdatesEnabled(False)
        try:
            self._sync_tabs()
        finally:
            self._tabs.setUpdatesEnabled(True)
        self._rebuild_view_menu()
        self._restore_last_tab()
        self._refresh_statusbar()

    def _tab_icon(self, name: str) -> QIcon:
        lowered = name.casefold()
        if "intyg" in lowered or "cert" in lowered:
            return self._standard_icon("SP_FileIcon")
        if "stat" in lowered:
            return self._standard_icon("SP_ComputerIcon")
        if "inställ" in lowered or "setting" in lowered:
            return self._standard_icon("SP_FileDialogDetailedView")
        if "tray" in lowered or "system" in lowered:
            return self._standard_icon("SP_DesktopIcon")
        if "logg" in lowered:
            return self._standard_icon("SP_FileDialogContentsView")
        return self._standard_icon("SP_FileIcon")

    def _show_plugin_error(self, message: str) -> None:
        if self._tabs is None:
            return
        self._tabs.clear()
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Pluginfel", page)
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(max(14, title_font.pointSize() + 3))
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        detail = QLabel(message, page)
        detail.setWordWrap(True)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(detail)
        self._tabs.addTab(
            page, self._standard_icon("SP_MessageBoxWarning"), "Status"
        )

    def _sync_tabs(self, preferred_name: str = "") -> None:
        if self._tabs is None:
            return
        current_name = preferred_name or self._current_tab_name()
        while self._tabs.count():
            self._tabs.removeTab(0)

        visible_records = [record for record in self._tab_records if record.visible]
        if not visible_records:
            self._tabs.addTab(
                self._get_empty_tab(),
                self._standard_icon("SP_MessageBoxInformation"),
                "Start",
            )
            return

        index_to_select = 0
        for index, record in enumerate(visible_records):
            self._tabs.addTab(record.widget, record.icon, record.name)
            if record.name == current_name:
                index_to_select = index
        self._tabs.setCurrentIndex(index_to_select)

    def _get_empty_tab(self) -> QWidget:
        if self._empty_tab is not None:
            return self._empty_tab
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Alla arbetsflikar är dolda", page)
        font = title.font()
        font.setBold(True)
        font.setPointSize(max(14, font.pointSize() + 3))
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("Öppna Visa-menyn eller återställ flikarna här.", page)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        button = QPushButton("Visa alla flikar", page)
        button.setMinimumHeight(34)
        button.clicked.connect(self._show_all_tabs)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
        self._empty_tab = page
        return page

    def _rebuild_view_menu(self) -> None:
        if self._view_menu is None:
            return
        self._view_menu.clear()
        restore_action = self._add_action(
            self._view_menu,
            "Visa alla flikar",
            "SP_DialogApplyButton",
            self._show_all_tabs,
        )
        restore_action.setEnabled(bool(self._hidden_tabs))
        if self._tab_records:
            self._view_menu.addSeparator()

        for record in self._tab_records:
            action = QAction(record.icon, record.name, self._view_menu)
            action.setCheckable(True)
            action.setChecked(record.visible)
            action.toggled.connect(
                lambda visible, tab_name=record.name: self._set_tab_visible(
                    tab_name,
                    visible,
                )
            )
            self._view_menu.addAction(action)

    def _show_tab_context_menu(self, position: QPoint) -> None:
        if self._tabs is None:
            return
        menu = QMenu(self)
        for record in self._tab_records:
            action = QAction(record.icon, record.name, menu)
            action.setCheckable(True)
            action.setChecked(record.visible)
            action.toggled.connect(
                lambda visible, tab_name=record.name: self._set_tab_visible(
                    tab_name,
                    visible,
                )
            )
            menu.addAction(action)
        if self._tab_records:
            menu.addSeparator()
        self._add_action(
            menu,
            "Visa alla flikar",
            "SP_DialogApplyButton",
            self._show_all_tabs,
        )
        menu.exec(self._tabs.tabBar().mapToGlobal(position))

    def _set_tab_visible(self, name: str, visible: bool) -> None:
        for record in self._tab_records:
            if record.name == name:
                record.visible = visible
                break
        else:
            return

        if visible:
            self._hidden_tabs.discard(name)
        else:
            self._hidden_tabs.add(name)
        if self._tabs:
            self._tabs.setUpdatesEnabled(False)
        try:
            self._sync_tabs(preferred_name=name if visible else "")
        finally:
            if self._tabs:
                self._tabs.setUpdatesEnabled(True)
        self._save_hidden_tabs()
        self._rebuild_view_menu()
        self._refresh_statusbar()

    @Slot()
    def _show_all_tabs(self) -> None:
        if not self._tab_records:
            return
        self._hidden_tabs.clear()
        for record in self._tab_records:
            record.visible = True
        if self._tabs:
            self._tabs.setUpdatesEnabled(False)
        try:
            self._sync_tabs()
        finally:
            if self._tabs:
                self._tabs.setUpdatesEnabled(True)
        self._save_hidden_tabs()
        self._rebuild_view_menu()
        self._refresh_statusbar()

    def _load_hidden_tabs(self) -> set[str]:
        raw = self._app.config_value("window.hidden_tabs", [])
        if not isinstance(raw, list):
            return set()
        return {str(name) for name in raw if isinstance(name, str) and name}

    def _save_hidden_tabs(self) -> None:
        self._app.set_config_value(
            "window.hidden_tabs",
            sorted(self._hidden_tabs, key=str.casefold),
            auto_save=True,
        )

    def _restore_last_tab(self) -> None:
        if self._tabs is None or self._tabs.count() == 0:
            return
        last_tab = _coerce_int(self._app.config_value("window.last_tab", 0), 0)
        if 0 <= last_tab < self._tabs.count():
            self._tabs.setCurrentIndex(last_tab)

    def _current_tab_name(self) -> str:
        if self._tabs is None:
            return ""
        index = self._tabs.currentIndex()
        return self._tabs.tabText(index) if index >= 0 else ""

    @Slot(int)
    def _on_tab_changed(self, index: int) -> None:
        if index >= 0:
            self._app.set_config_value("window.last_tab", index, auto_save=True)
        self._refresh_statusbar()

    def _refresh_statusbar(self) -> None:
        if self._status_label is None:
            return
        plugin_count = len(self._app.plugin_manager) if self._app.plugin_manager else 0
        visible_tabs = sum(1 for record in self._tab_records if record.visible)
        self._status_label.setText(
            f"v{APP_VERSION} • {plugin_count} plugins • {visible_tabs} flikar"
        )
        if self._template_label is not None:
            if self._templates_scanned:
                self._template_label.setText(f"Mallar: {self._templates_count}")
                tooltip = (
                    "Hittade: " + ", ".join(self._template_names)
                    if self._template_names
                    else "Ingen mallundermapp med en giltig DOCX hittades."
                )
                self._template_label.setToolTip(tooltip)
            else:
                self._template_label.setText("Mallar: väntar")
                self._template_label.setToolTip(
                    "Mallskanningen körs i bakgrunden."
                )

    @Slot(str)
    def _on_status_message(self, message: str) -> None:
        if self._status_label is not None and message.strip():
            self._status_label.setText(message.strip())

    @Slot(object)
    def _on_templates_scanned(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        count = _coerce_int(data.get("count"), 0)
        raw_names = data.get("names", [])
        names = (
            [str(name) for name in raw_names] if isinstance(raw_names, list) else []
        )
        self._templates_count = max(0, count)
        self._template_names = sorted(names, key=str.casefold)
        self._templates_scanned = True
        self._refresh_statusbar()
        if self._templates_count:
            self.status_signal.emit(
                f"✓ {self._templates_count} mallar är klara att använda"
            )
        else:
            self.status_signal.emit("⚠ Inga giltiga mallar hittades")

    @Slot()
    def _on_show(self) -> None:
        if self.isMaximized():
            self.showMaximized()
        else:
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self._app.set_hidden_state(False)
        if _IS_WINDOWS:
            try:
                import ctypes

                ctypes.windll.user32.AllowSetForegroundWindow(-1)
                ctypes.windll.user32.SetForegroundWindow(int(self.winId()))
            except (AttributeError, OSError) as exc:
                _logger.debug("Kunde inte fokusera Windows-fönster: %s", exc)

    @Slot()
    def _on_hide(self) -> None:
        self.hide()
        self._app.set_hidden_state(True)

    @Slot()
    def _on_quit(self) -> None:
        self._app.exit()

    def changeEvent(self, event: QEvent) -> None:
        """Route the window's minimize action to the tray when configured."""
        super().changeEvent(event)
        if (
            event.type() == QEvent.Type.WindowStateChange
            and self.isMinimized()
            and not self._app.is_exiting
            and self._app.is_tray_available()
            and bool(self._app.config_value("app.tray.minimize_to_tray",
                                            self._app.config_value("app.minimize_to_tray", True)))
        ):
            QTimer.singleShot(0, self._hide_minimized_window)

    @Slot()
    def _hide_minimized_window(self) -> None:
        if self.isMinimized() and not self._app.is_exiting:
            self.hide_signal.emit()

    def closeEvent(self, event: QCloseEvent) -> None:
        """X sends the window to the system tray when that option is enabled."""
        if self._app.is_exiting:
            event.accept()
            return
        close_to_tray = bool(
            self._app.config_value("app.close_to_tray",
                                   self._app.config_value("app.tray.close_to_tray", True))
        )
        if self._app.is_tray_available() and close_to_tray:
            event.ignore()
            self.hide_signal.emit()
            return
        if self._app.is_tray_available() and not close_to_tray:
            self._app.exit()
            event.accept()
            return

        answer = QMessageBox.question(
            self,
            "Avsluta IntygsDrive?",
            "Systemfältet är inte tillgängligt. Vill du avsluta programmet?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._app.exit()
            event.accept()
        else:
            event.ignore()

    def _open_folder(self, folder: Path) -> None:
        if self._app.open_folder(folder):
            return
        QMessageBox.warning(
            self,
            "Kunde inte öppna mapp",
            f"Mappen kunde inte öppnas:\n{folder}",
        )

    def _show_about(self) -> None:
        plugin_count = (
            len(self._app.plugin_manager) if self._app.plugin_manager else 0
        )
        QMessageBox.information(
            self,
            f"Om {APP_DISPLAY_NAME}",
            "\n".join(
                (
                    APP_DISPLAY_NAME,
                    f"Version {APP_VERSION}",
                    "",
                    "Snabb lokal generering av professionella fordonsintyg.",
                    "Mallskanning och PDF-arbete körs utanför UI-tråden.",
                    "",
                    f"Plugins: {plugin_count}",
                    f"Plattform: {_SYSTEM}",
                    f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                    f"Session: {SESSION_ID}",
                    "",
                    COPYRIGHT,
                )
            ),
        )

    def save_geometry(self) -> None:
        """Spara fönstergeometri atomiskt via ConfigManagers debouncer."""
        geometry = self.normalGeometry() if self.isMaximized() else self.geometry()
        values: tuple[tuple[str, Any], ...] = (
            ("window.x", int(geometry.x())),
            ("window.y", int(geometry.y())),
            ("window.width", int(geometry.width())),
            ("window.height", int(geometry.height())),
            ("window.maximized", self.isMaximized()),
        )
        for key, value in values:
            self._app.set_config_value(key, value, auto_save=False)
        self._save_hidden_tabs()
        self._app.save_config(force=True)


class Application:
    """Applikationens livscykel och icke-blockerande startup-orkestrering."""

    def __init__(self) -> None:
        self.qapp: QApplication | None = None
        self.window: MainWindow | None = None
        self.context: Any | None = None
        self.plugin_manager: PluginManager | None = None
        self._config: Any | None = None
        self._icon = QIcon()
        self._activation_server: InstanceActivationServer | None = None
        self._event_unsubscribers: list[Callable[[], None]] = []
        self._cleanup_done = False
        self._exit_requested = False
        self._ui_ready = False
        self._ready_requested = False
        self._ready_emitted = False
        self._startup_template_count: int | None = None  # 10.10.0
        self._startup_warnings: list[str] = []
        self._missing_deps: list[dict[str, str]] = []
        self._missing_suites: list[dict[str, Any]] = []
        self._tray_logged = False
        self._update_signals = _UpdateCheckSignals()
        self._update_signals.completed.connect(self._on_release_check_completed)
        self._update_thread: threading.Thread | None = None
        self._update_timer: QTimer | None = None

    @property
    def config(self) -> Any | None:
        return self._config

    @property
    def is_exiting(self) -> bool:
        return self._exit_requested

    def setup(self) -> bool:
        """Sätt upp kärna, fönsterskal och plugins utan extra mallomskanning."""
        steps: tuple[tuple[str, Callable[[], None], bool], ...] = (
            ("mappar", self._setup_folders, False),
            ("konfiguration", self._setup_config, True),
            ("autostart", self._ensure_autostart, False),  # 10.8.0
            ("Qt", self._setup_qapp, True),
            ("beroenden", self._check_dependencies, False),  # 10.8.0
            ("fönster", self._setup_window, True),
            ("kontext", self._setup_context, True),
            ("tema", self._apply_theme, False),
            ("events", self._subscribe_application_events, False),
            ("plugins", self._setup_plugins, True),
            ("UI", self._setup_ui, True),
            ("visning", self._apply_start_visibility, False),
            ("signaler", self._setup_signals, False),
        )
        for step_name, step, critical in steps:
            started = time.monotonic()
            try:
                step()
            except Exception:
                _logger.exception("Setup-fel i steg '%s'", step_name)
                _evt("main.step_failed", f"Setup-steg '{step_name}' misslyckades",
                     level=logging.ERROR, step=step_name)
                if critical:
                    return False
                continue
            _evt("main.step", f"Setup-steg '{step_name}' klart",
                 level=logging.DEBUG, step=step_name,
                 ms=round((time.monotonic() - started) * 1000, 1))
        return True

    # ── 10.8.0: autostart + beroenden ─────────────────────────────────────

    def _ensure_autostart(self) -> None:
        """Självläkande multi-OS-autostart enligt app.start_with_os."""
        AutostartGuard(bool(self.config_value("app.start_with_os", True))).ensure()

    def _check_dependencies(self) -> None:
        """Inventera saknade tillägg; erbjud installation efter ready."""
        self._missing_deps = DependencyInventory.missing_python_packages()
        # 10.12.2: det räcker med EN fungerande konverterare. Finns LibreOffice
        # (eller Word) erbjuds inga andra sviter — OpenOffice föreslås aldrig i onödan.
        suites = DependencyInventory.office_suites()
        if any(suite["found"] for suite in suites):
            self._missing_suites = []
        else:
            self._missing_suites = [suite for suite in suites if suite["name"] != "OpenOffice"]
        _evt(
            "main.deps_checked",
            f"Beroenden inventerade: {len(self._missing_deps)} paket, "
            f"{len(self._missing_suites)} sviter saknas",
            missing_packages=[item["name"] for item in self._missing_deps],
            missing_suites=[suite["name"] for suite in self._missing_suites],
        )

    def _show_dependency_offer(self) -> None:
        """Erbjud installation av alla saknade tillägg (en gång per session)."""
        if not self._missing_deps and not self._missing_suites:
            return
        if bool(self.config_value("deps.hide_offer", False)):
            return
        lines = ["Följande tillägg saknas eller rekommenderas:", ""]
        lines.extend(f"• {item['name']} — {item['why']}" for item in self._missing_deps)
        lines.extend(f"• {suite['name']} — {suite['note']}" for suite in self._missing_suites)
        box = QMessageBox(self.window)
        box.setWindowTitle("📦 Saknade tillägg")
        box.setText("\n".join(lines))
        box.setInformativeText(
            "Installera Python-paketen nu (bakgrundstråd), eller hämta "
            "office-svit via webb/kommando."
        )
        install_btn = box.addButton("📦 Installera paket", QMessageBox.ButtonRole.AcceptRole)
        office_btn = box.addButton("🌐 Office-alternativ", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Senare", QMessageBox.ButtonRole.RejectRole)
        hide_btn = box.addButton("Visa inte igen", QMessageBox.ButtonRole.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        _evt("main.deps_offer_answer", "Användaren svarade på beroende-erbjudandet",
             choice=getattr(clicked, "text", "") or "")
        if clicked == hide_btn:
            self.set_config_value("deps.hide_offer", True, auto_save=True)
            return
        if clicked == office_btn:
            self._show_office_alternatives()
            return
        if clicked == install_btn and self._missing_deps:
            self._install_missing_packages([item["name"] for item in self._missing_deps])

    def _show_office_alternatives(self) -> None:
        """Öppna nedladdningssida + kopiera installationskommando för saknad svit."""
        for suite in self._missing_suites:
            url = str(suite.get("url", ""))
            hint = str(suite.get("hint", ""))
            if url:
                try:
                    import webbrowser

                    webbrowser.open(url)
                except Exception:
                    _logger.debug("Kunde inte öppna nedladdningssida", exc_info=True)
            if hint:
                try:
                    if self.qapp is not None:
                        from PySide6.QtGui import QGuiApplication

                        clipboard = QGuiApplication.clipboard()
                        if clipboard is not None:
                            clipboard.setText(hint)
                except Exception:
                    _logger.debug("Kunde inte kopiera installationskommando", exc_info=True)
            _evt("main.office_alternative_shown", f"Office-alternativ visat: {suite['name']}",
                 suite=suite["name"], command=hint, url=url)
            break  # en svit i taget håller dialogen tydlig

    def _install_missing_packages(self, packages: list[str]) -> None:
        """Sekventiell pip-installation i bakgrundstråd med summering."""
        results: dict[str, bool] = {}

        def chain(remaining: list[str]) -> None:
            if not remaining:
                QTimer.singleShot(0, lambda: self._summarize_installs(results))
                return
            package = remaining[0]

            def on_done(ok: bool, _tail: str) -> None:
                results[package] = ok
                chain(remaining[1:])

            DependencyInventory.pip_install(package, on_done)

        chain(list(packages))

    def _summarize_installs(self, results: dict[str, bool]) -> None:
        ok_count = sum(1 for value in results.values() if value)
        failed = [name for name, value in results.items() if not value]
        _evt("main.deps_install_summary", f"Installationer klara: {ok_count}/{len(results)} OK",
             ok=ok_count, failed=failed)
        if self.window is None:
            return
        message = f"✅ {ok_count}/{len(results)} paket installerades."
        if failed:
            message += "\n\n❌ Misslyckades: " + ", ".join(failed)
            message += "\n\nProva manuellt: python -m pip install " + " ".join(failed)
        else:
            message += "\n\nStarta om appen för att aktivera dem."
        QMessageBox.information(self.window, "Installation klar", message)

    def notify_ready(self) -> None:
        """Begär ready-events efter att lazy UI-flikarna har monterats."""
        self._ready_requested = True
        if self._ui_ready:
            QTimer.singleShot(0, self._emit_ready_events)

    def run(self) -> int:
        if self.qapp is None:
            return 1
        try:
            return self.qapp.exec()
        except KeyboardInterrupt:
            return 130
        finally:
            self._cleanup()

    def exit(self) -> None:
        """Avsluta idempotent, med ordnad plugin- och config-cleanup."""
        if self._exit_requested:
            return
        self._exit_requested = True
        self._cleanup()
        if self.window is not None:
            self.window.hide()
        if self.qapp is not None:
            self.qapp.quit()

    def set_hidden_state(self, hidden: bool) -> None:
        if self.context is None:
            return
        try:
            self.context._is_hidden = hidden
        except Exception:
            _logger.debug(
                "Kunde inte uppdatera AppContext._is_hidden", exc_info=True
            )

    def config_value(self, key: str, default: Any = None) -> Any:
        if self._config is None:
            return default
        getter = getattr(self._config, "get", None)
        if not callable(getter):
            return default
        try:
            return getter(key, default)
        except TypeError:
            try:
                value = getter(key)
                return default if value is None else value
            except Exception:
                _logger.exception("Kunde inte läsa inställning %s", key)
                return default
        except Exception:
            _logger.exception("Kunde inte läsa inställning %s", key)
            return default

    def set_config_value(
        self,
        key: str,
        value: Any,
        *,
        auto_save: bool,
    ) -> bool:
        """Skriv med bästa kompatibla ConfigManager-signatur."""
        if self._config is None:
            return False
        setter = getattr(self._config, "set", None)
        if not callable(setter):
            return False
        try:
            setter(key, value, notify=False, auto_save=auto_save)
            return True
        except TypeError:
            try:
                setter(key, value)
                return True
            except Exception:
                _logger.exception("Kunde inte spara inställning %s", key)
                return False
        except Exception:
            _logger.exception("Kunde inte spara inställning %s", key)
            return False

    def save_config(self, *, force: bool) -> None:
        if self._config is None:
            return
        saver = getattr(self._config, "save", None)
        if not callable(saver):
            return
        try:
            saver(force=force)
        except TypeError:
            try:
                saver()
            except Exception:
                _logger.exception("Kunde inte spara konfiguration")
        except Exception:
            _logger.exception("Kunde inte spara konfiguration")

    def open_folder(self, folder: Path) -> bool:
        """Öppna en mapp utan att blockera UI-tråden."""
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _logger.error("Kunde inte skapa mapp %s: %s", folder, exc)
            return False

        try:
            if _IS_WINDOWS:
                startfile = getattr(os, "startfile", None)
                if callable(startfile):
                    startfile(str(folder))
                    return True
                return False
            command = ["open", str(folder)] if _IS_MACOS else ["xdg-open", str(folder)]
            subprocess.Popen(
                command,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, ValueError) as exc:
            _logger.error("Kunde inte öppna mapp %s: %s", folder, exc)
            return False

    def start_instance_activation_server(self) -> bool:
        if self.window is None:
            return False
        self._activation_server = InstanceActivationServer(_INSTANCE_NAME, self.window)
        return self._activation_server.start()

    def is_tray_available(self) -> bool:
        """Dölj endast vid startup när system_tray-pluginet är aktivt."""
        if self.plugin_manager is None:
            return QSystemTrayIcon.isSystemTrayAvailable()
        plugin: Any | None = None
        for plugin_name in ("System Tray", "SystemTray"):
            try:
                plugin = self.plugin_manager.get_plugin(plugin_name)
            except Exception:
                _logger.debug(
                    "Kunde inte slå upp tray-plugin %s", plugin_name, exc_info=True
                )
            if plugin is not None:
                break
        if plugin is None:
            return False
        checker = getattr(plugin, "is_tray_available", None)
        if not callable(checker):
            return False
        try:
            available = bool(checker())
        except Exception:
            _logger.exception("Tray-pluginets tillgänglighetskontroll misslyckades")
            return False
        if not self._tray_logged:
            self._tray_logged = True
            _evt("main.tray_available", f"Tray tillgänglig vid startup: {available}",
                 level=logging.DEBUG, available=available)
        return available

    def _setup_folders(self) -> None:
        """Skapa driftsmappar utan att skapa användarprofiler automatiskt."""
        folders: tuple[Path, ...] = (
            APP_PATH / FOLDER_TEMPLATES,
            APP_PATH / FOLDER_OUTPUT,
            APP_PATH / FOLDER_PLUGINS,
            APP_PATH / FOLDER_BACKUP,
            APP_PATH / FOLDER_SENT,
            APP_PATH / FOLDER_LOGS,
            APP_PATH / FOLDER_ICONS,
            APP_PATH / FOLDER_CONFIG,
        )
        for folder in folders:
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                warning = f"Kunde inte skapa {folder}: {exc}"
                self._startup_warnings.append(warning)
                _stderr(warning)

    def _setup_config(self) -> None:
        config_path = APP_PATH / FOLDER_CONFIG / "settings.json"
        self._config = ConfigManager(config_path, auto_save=True)

        legacy_theme = self.config_value("general.theme", "system")
        theme = self.config_value("app.theme", None)
        if not isinstance(theme, str) or theme not in {"light", "dark", "system"}:
            theme = (
                legacy_theme
                if legacy_theme in {"light", "dark", "system"}
                else "system"
            )
            self.set_config_value("app.theme", theme, auto_save=False)

        # Migrate the old Settings key; new installations start with the OS by default.
        stored_start_with_os = self.config_value("app.start_with_os", None)
        stored_autostart = self.config_value("app.autostart", None)
        if stored_start_with_os is None and stored_autostart is None:
            start_with_os = True
        else:
            start_with_os = bool(stored_start_with_os or stored_autostart)
        self.set_config_value("app.start_with_os", start_with_os, auto_save=False)
        self.set_config_value("app.autostart", start_with_os, auto_save=False)

        defaults: dict[str, Any] = {
            "app.start_minimized": True,
            "app.close_to_tray": True,
            "app.start_with_os": True,
            "app.theme": theme,
            "general.theme": theme,
            "window.width": 1120,
            "window.height": 760,
            "window.last_tab": 0,
            "window.maximized": False,
            "window.hidden_tabs": [],
        }
        set_defaults = getattr(self._config, "set_defaults", None)
        if not callable(set_defaults):
            set_defaults = getattr(self._config, "ensure_defaults", None)
        if callable(set_defaults):
            set_defaults(defaults)
            return
        for key, value in defaults.items():
            if self.config_value(key, None) is None:
                self.set_config_value(key, value, auto_save=False)

    def _setup_qapp(self) -> None:
        self.qapp = QApplication.instance() or QApplication(sys.argv)
        self.qapp.setApplicationName(APP_NAME)
        self.qapp.setApplicationDisplayName(APP_DISPLAY_NAME)
        self.qapp.setApplicationVersion(APP_VERSION)
        self.qapp.setOrganizationName("Rickard Längkvist")
        self.qapp.setQuitOnLastWindowClosed(False)
        self._icon = _load_app_icon()
        self.qapp.setWindowIcon(self._icon)

    def _setup_window(self) -> None:
        self.window = MainWindow(self)
        self.window.setup(self._icon)
        self._restore_window_geometry()

    def _restore_window_geometry(self) -> None:
        if self.window is None:
            return
        primary = QApplication.primaryScreen()
        if primary is None:
            return
        available = primary.availableGeometry()
        width = max(760, _coerce_int(self.config_value("window.width", 1120), 1120))
        height = max(560, _coerce_int(self.config_value("window.height", 760), 760))
        width = min(width, max(available.width(), 760))
        height = min(height, max(available.height(), 560))
        pos_x = self.config_value("window.x", None)
        pos_y = self.config_value("window.y", None)

        if (
            isinstance(pos_x, (int, float))
            and not isinstance(pos_x, bool)
            and isinstance(pos_y, (int, float))
            and not isinstance(pos_y, bool)
        ):
            candidate = QRect(int(pos_x), int(pos_y), width, height)
            screens = QApplication.screens()
            if any(
                screen.availableGeometry().intersects(candidate) for screen in screens
            ):
                self.window.setGeometry(candidate)
                _evt("main.geometry_restored", "Sparad fönstergeometri återställd",
                     level=logging.DEBUG, mode="saved", width=width, height=height)
            else:
                self._center_window(width, height, available)
                _evt("main.geometry_restored", "Sparad geometri utanför skärmar → centrerat",
                     level=logging.DEBUG, mode="centered", width=width, height=height)
        else:
            self._center_window(width, height, available)
            _evt("main.geometry_restored", "Ingen sparad geometri → centrerat",
                 level=logging.DEBUG, mode="centered", width=width, height=height)

        # 10.11.0: Öppna ALLTID maximerat. Den sparade (ej maximerade) geometrin
        # ligger kvar som "återställ-storlek", så användaren kan justera fönstret
        # efteråt genom att klicka på Återställ och sedan ändra storlek/position.
        if bool(self.config_value("window.start_maximized", True)):
            with suppress(Exception):
                self.window.setWindowState(Qt.WindowState.WindowMaximized)
                _evt("main.geometry_restored", "Öppnar maximerat (justerbart efteråt)",
                     level=logging.DEBUG, mode="start_maximized")
        elif bool(self.config_value("window.maximized", False)):
            self.window.setWindowState(
                self.window.windowState() | Qt.WindowState.WindowMaximized
            )

    def _center_window(self, width: int, height: int, available: QRect) -> None:
        if self.window is None:
            return
        x = available.x() + max(0, (available.width() - width) // 2)
        y = available.y() + max(0, (available.height() - height) // 2)
        self.window.setGeometry(x, y, width, height)

    def _setup_context(self) -> None:
        if self.window is None:
            raise RuntimeError("Huvudfönstret saknas")
        self.context = AppContext(
            root=self.window,
            config_manager=self._config,
            base_path=APP_PATH,
            session_id=SESSION_ID,
        )
        self.window.set_context(self.context)

    def _apply_theme(self) -> None:
        if self.qapp is None:
            return
        from core.themes import Theme, ThemeManager

        theme_name = str(self.config_value("app.theme", "system"))
        theme = Theme.from_string(theme_name)
        manager = ThemeManager.get_instance(context=self.context)
        manager.apply_theme(theme)

    def _subscribe_application_events(self) -> None:
        if self.context is None:
            return
        event_bus = getattr(self.context, "event_bus", None)
        if event_bus is None:
            return
        event_map: tuple[tuple[str, Callable[[Any], None]], ...] = (
            ("window.show", self._on_window_show_event),
            ("window.hide", self._on_window_hide_event),
            ("tray.restore", self._on_window_show_event),
            ("tray.minimize", self._on_window_hide_event),
            ("app.quit", self._on_quit_event),
            ("app.quit_confirmed", self._on_quit_event),
            ("settings.theme_changed", self._on_theme_changed),
            ("templates.scanned", self._on_templates_scanned),
            ("status.message", self._on_status_message),
            ("error.occurred", self._on_error_event),
            ("refresh_ui", self._on_refresh_ui),
        )
        for event_name, handler in event_map:
            try:
                unsubscribe = event_bus.subscribe(event_name, handler)
                if callable(unsubscribe):
                    self._event_unsubscribers.append(unsubscribe)
            except Exception:
                _logger.exception("Kunde inte prenumerera på event %s", event_name)

    def _setup_plugins(self) -> None:
        if self.context is None:
            raise RuntimeError("AppContext saknas")
        self.plugin_manager = PluginManager(self.context)
        self.plugin_manager.discover()
        self.plugin_manager.load_all()
        self.context._plugin_manager = self.plugin_manager

    def _setup_ui(self) -> None:
        """Lägg tabbygget på första event-loop-varvet för snabbare startup."""
        if self.qapp is None:
            raise RuntimeError("QApplication saknas")
        QTimer.singleShot(0, self._populate_plugin_tabs)

    @Slot()
    def _populate_plugin_tabs(self) -> None:
        if self._ui_ready:
            return
        if self.window is not None:
            self.window.populate_tabs()
        self._ui_ready = True
        if self._ready_requested:
            QTimer.singleShot(0, self._emit_ready_events)

    def _apply_start_visibility(self) -> None:
        if self.window is None:
            return
        start_minimized = bool(self.config_value("app.start_minimized", True))
        if start_minimized and self.is_tray_available():
            self._maximize_after_show()
            self.window.hide_signal.emit()
            return
        if start_minimized:
            _logger.warning("Systemfältet saknas — huvudfönstret visas i stället")
        self._maximize_after_show()
        self.window.show_signal.emit()

    def _maximize_after_show(self) -> None:
        """10.11.0: maximera efter att fönstret visats (säkrast i Qt, särskilt
        från tray och vid uppstart minimerat)."""

        def _run() -> None:
            with suppress(Exception):
                if self.window is not None and bool(
                    self.config_value("window.start_maximized", True)
                ):
                    self.window.showMaximized()

        QTimer.singleShot(0, _run)

    def _setup_signals(self) -> None:
        def handle_signal(_signal_number: int, _frame: Any) -> None:
            if self.qapp is not None:
                QTimer.singleShot(0, self.exit)
            else:
                self.exit()

        try:
            signal.signal(signal.SIGINT, handle_signal)
            signal.signal(signal.SIGTERM, handle_signal)
            if _IS_WINDOWS:
                signal.signal(signal.SIGBREAK, handle_signal)
        except (AttributeError, OSError, ValueError) as exc:
            _logger.debug("Kunde inte registrera alla OS-signaler: %s", exc)

    @Slot()
    def _emit_ready_events(self) -> None:
        if self._ready_emitted or not self._ui_ready:
            return
        self._ready_emitted = True
        if self.plugin_manager is not None:
            try:
                self.plugin_manager.notify_ready()
            except Exception:
                _logger.exception("PluginManager.notify_ready misslyckades")

        plugin_names = self._loaded_plugin_names()
        self._emit_event(
            "app.startup",
            {
                "version": APP_VERSION,
                "session": SESSION_ID,
                "plugins": plugin_names,
            },
        )
        self._emit_event("app.ready", {"version": APP_VERSION})
        self._emit_event("refresh_ui", {})
        # 10.10.0: självdiagnos efter att pluginerna hunnit arbeta.
        QTimer.singleShot(8000, self._log_startup_summary)
        _evt("main.ready", "Appen redo — UI monterat och ready-events emitterade",
             plugins=len(plugin_names))
        # 10.8.0: erbjud installation av saknade tillägg när UI är redo.
        QTimer.singleShot(400, self._show_dependency_offer)
        # Kontrollera vid start och därefter var tolfte timme (två gånger per dygn).
        QTimer.singleShot(5000, self._start_release_check)
        if self._update_timer is None:
            self._update_timer = QTimer(self.qapp)
            self._update_timer.setInterval(UPDATE_INTERVAL_HOURS * 60 * 60 * 1000)
            self._update_timer.timeout.connect(self._start_release_check)
            self._update_timer.start()
        # Ingen fast malluppdateringstimer här. Certificates-pluginet startar
        # en enda bakgrundsskanning självt och sammanslår manuella refresh-event.

    def _start_release_check(self) -> None:
        """Kontrollera en publicerad GitHub Release utan att blockera UI."""
        if not bool(self.config_value("app.check_updates", True)):
            _evt("update.check_skipped", "Automatisk releasekontroll är avstängd")
            return
        if self._update_thread is not None and self._update_thread.is_alive():
            return

        def check() -> None:
            try:
                release = check_latest_release(
                    APP_VERSION,
                    current_commit=get_current_commit(APP_PATH),
                    timeout=8.0,
                )
                staged = None
                if release is not None:
                    staged = stage_release_update(APP_PATH, release, timeout=45.0)
                self._update_signals.completed.emit({
                    "release": release,
                    "staged": staged,
                    "error": None,
                })
            except Exception as exc:
                self._update_signals.completed.emit({
                    "release": None,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        self._update_thread = threading.Thread(
            target=check, name="github-release-check", daemon=True
        )
        self._update_thread.start()
        _evt("update.check_started", "Kontrollerar GitHub Releases i bakgrunden")

    @Slot(object)
    def _on_release_check_completed(self, result: Any) -> None:
        if not isinstance(result, dict):
            return
        error = result.get("error")
        if error:
            _evt("update.check_failed", "Automatisk GitHub-uppdatering misslyckades",
                 level=logging.WARNING, error=str(error))
            if self.window is not None and not self._exit_requested:
                self.window.status_signal.emit("⚠ Automatisk uppdatering misslyckades; försöker igen senare.")
            return
        release = result.get("release")
        if release is None:
            _evt("update.current", "Ingen nyare GitHub Release hittades",
                 level=logging.DEBUG, version=APP_VERSION)
            return

        staged = result.get("staged")
        if staged is None:
            return
        if not getattr(staged, "staged", False):
            _evt("update.current", "Nedladdade appfiler är redan aktuella",
                 level=logging.DEBUG, version=release.version, commit=release.commit_sha)
            return

        _evt("update.staged", "Nyare release hämtad och väntar på att appen stängs",
             version=release.version, commit=release.commit_sha)
        if self.window is not None and not self._exit_requested:
            self.window.status_signal.emit(
                f"⬇ Uppdatering {release.version} hämtad — installeras automatiskt när appen stängs."
            )

    def _log_startup_summary(self) -> None:
        """10.10.0: synlig startup-självdiagnos.

        Fångar garanterat de två tysta problemen användaren drabbats av:
        (1) systray-ikonen saknas, (2) mallar läses inte in — med orsak,
        som WARNING så att det syns i både logg och felräknare.
        """
        problems: list[str] = []
        tray_state: str = "okänd"
        tray_plugin: Any = None
        if self.plugin_manager is not None:
            for name in ("System Tray", "SystemTray"):
                with suppress(Exception):
                    tray_plugin = self.plugin_manager.get_plugin(name)
                if tray_plugin is not None:
                    break
        if tray_plugin is None:
            tray_state = "saknas"
            problems.append("system_tray-pluginet laddades inte")
        else:
            checker = getattr(tray_plugin, "is_icon_visible", None)
            if callable(checker):
                with suppress(Exception):
                    visible = bool(checker())
                    tray_state = "synlig" if visible else "ej synlig"
                    if not visible:
                        problems.append(
                            "systray-ikonen är inte synlig (ikon skapades men visas ej)"
                        )
            else:
                tray_state = "okänd (gammal build?)"
        templates = self._startup_template_count
        if templates is None:  # 10.12.1: skanningen hann inte bli klar
            problems.append("mallskanningen var inte klar efter 8 s (långsam disk eller hängd skanning)")
        elif templates == 0:
            problems.append("inga mallar lästes in — kontrollera Mallar/-mappen (varje mall kräver egen undermapp med .docx)")
        context = {
            "tray": tray_state,
            "templates": templates,
            "plugins": len(self._loaded_plugin_names()),
        }
        if problems:
            _evt("main.startup_summary",
                 "STARTPROBLEM: " + "; ".join(problems),
                 level=logging.WARNING, **context)
            _logger.warning("[MAIN] Startproblem: %s", "; ".join(problems))
        else:
            _evt("main.startup_summary",
                 f"Start OK: tray={tray_state}, mallar={templates}",
                 **context)

    def _loaded_plugin_names(self) -> list[str]:
        if self.plugin_manager is None:
            return []
        loaded = getattr(self.plugin_manager, "loaded", None)
        if isinstance(loaded, dict):
            return [str(name) for name in loaded]
        try:
            return [
                str(getattr(plugin, "name", plugin)) for plugin in self.plugin_manager
            ]
        except Exception:
            return []

    def _emit_event(self, event_name: str, data: dict[str, Any]) -> None:
        if self.context is None:
            return
        emitter = getattr(self.context, "emit_event", None)
        if not callable(emitter):
            return
        try:
            emitter(event_name, data)
        except Exception:
            _logger.exception("Kunde inte emittera event %s", event_name)

    def _on_window_show_event(self, _data: Any = None) -> None:
        if self.window is not None:
            self.window.show_signal.emit()

    def _on_window_hide_event(self, _data: Any = None) -> None:
        if self.window is not None:
            self.window.hide_signal.emit()

    def _on_quit_event(self, _data: Any = None) -> None:
        self.exit()

    def _on_theme_changed(self, data: Any = None) -> None:
        if not isinstance(data, dict):
            return
        theme_name = str(data.get("theme", data.get("value", "system")))
        if theme_name not in {"light", "dark", "system"}:
            return
        self.set_config_value("app.theme", theme_name, auto_save=True)
        self.set_config_value("general.theme", theme_name, auto_save=True)
        try:
            from core.themes import Theme, ThemeManager

            ThemeManager.get_instance(context=self.context).apply_theme(
                Theme.from_string(theme_name)
            )
        except Exception:
            _logger.exception("Kunde inte applicera tema %s", theme_name)

    def _on_templates_scanned(self, data: Any = None) -> None:
        payload = dict(data) if isinstance(data, dict) else {}
        if "count" in payload:  # 10.10.0: spara för startup-självdiagnos
            try:
                self._startup_template_count = int(payload["count"])
            except (TypeError, ValueError):
                _logger.warning("[MAIN] Ogiltigt mallantal i scan-payload: %r", payload.get("count"))
        if self.window is not None:
            self.window.templates_scanned_signal.emit(payload)

    def _on_status_message(self, data: Any = None) -> None:
        if self.window is None:
            return
        if isinstance(data, dict):
            message = str(data.get("message", "") or "")
        else:
            message = str(data or "")
        if message:
            self.window.status_signal.emit(message)

    def _on_error_event(self, data: Any = None) -> None:
        if self.window is None or not isinstance(data, dict):
            return
        source = str(data.get("source", "App") or "App")
        message = str(data.get("message", "Okänt fel") or "Okänt fel")
        self.window.status_signal.emit(f"⚠ {source}: {message}")

    def _on_refresh_ui(self, _data: Any = None) -> None:
        if self.window is not None:
            self.window._refresh_statusbar()

    def _cleanup(self) -> None:
        if self._cleanup_done:
            return
        self._cleanup_done = True

        self._emit_event("app.closing", {})
        if self.window is not None:
            self.window.save_geometry()
        self._emit_event("app.shutdown", {})

        for unsubscribe in self._event_unsubscribers:
            try:
                unsubscribe()
            except Exception:
                _logger.exception("Kunde inte avregistrera EventBus-prenumeration")
        self._event_unsubscribers.clear()

        if self.plugin_manager is not None:
            try:
                self.plugin_manager.shutdown()
            except Exception:
                _logger.exception("PluginManager.shutdown misslyckades")

        if self._activation_server is not None:
            self._activation_server.close()
            self._activation_server = None

        # Rensa högst en gång per sjudagarsperiod innan konfigurationen stängs,
        # så senaste lyckade körning kan sparas atomiskt.
        self._cleanup_tech_cache()

        if self._config is not None:
            closer = getattr(self._config, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    _logger.exception("Kunde inte stänga ConfigManager")

        if launch_pending_update_helper(APP_PATH, os.getpid()):
            _evt("update.helper_started",
                 "Uppdateringshjälparen installerar hämtad kod efter att appen stängts")

        _evt("main.cleanup_done", "Cleanup slutförd — appen avslutas normalt")

    def _cleanup_tech_cache(self) -> dict[str, int]:
        """Rensa teknisk Python-cache högst en gång per vecka när smart_cache är på.

        Användardata (settings, loggar, Mallar, Out, Skickade, Backup) lämnas
        orörda. Datumet sparas först efter lyckad rensning.
        """
        empty: dict[str, int] = {"dirs": 0, "files": 0, "bytes": 0, "errors": 0}
        if not bool(self.config_value("smart_cache", True)):
            _evt("main.cache_skipped", "Teknisk cache sparas (smart_cache avstangd)",
                 level=logging.DEBUG)
            return empty

        today = datetime.now().date()
        last_raw = self.config_value("smart_cache_last_cleanup", "")
        try:
            last_cleanup = datetime.fromisoformat(str(last_raw)).date()
        except (TypeError, ValueError):
            last_cleanup = None
        if last_cleanup is not None and (today - last_cleanup).days < 7:
            _evt("main.cache_skipped", "Cache rensades nyligen; veckointervall gäller",
                 level=logging.DEBUG, last_cleanup=last_cleanup.isoformat(),
                 days_since=(today - last_cleanup).days)
            return empty
        try:
            stats = clear_pycache(APP_PATH, dry_run=False)
        except Exception as exc:
            _evt("main.cache_clear_failed", f"Kunde inte rensa teknisk cache: {exc}",
                 level=logging.WARNING, error=str(exc))
            return empty
        if not stats.get("errors", 0):
            self.set_config_value(
                "smart_cache_last_cleanup", today.isoformat(), auto_save=False
            )
            self.save_config(force=True)
        else:
            _evt("main.cache_clear_partial", "Cache-rensningen blev delvis; försök igen senare",
                 level=logging.WARNING, errors=stats.get("errors", 0))
        msg = "Rensade {} __pycache__-mappar ({} filer, {} KB)".format(
            stats.get("dirs", 0), stats.get("files", 0), round(stats.get("bytes", 0) / 1024.0, 1)
        )
        _evt("main.cache_cleared", msg,
             dirs=stats.get("dirs", 0), files=stats.get("files", 0),
             kb=round(stats.get("bytes", 0) / 1024.0, 1))
        return stats


def _kill_old_instance(pid: int) -> bool:
    """10.9.0: avsluta en gammal instans efter användarens bekräftelse."""
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True, timeout=10, check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)
        _evt("main.old_instance_killed", f"Gammal instans avslutad: PID {pid}",
             level=logging.WARNING, pid=pid)
        return True
    except Exception as exc:
        _evt("main.old_instance_kill_failed",
             f"Kunde inte avsluta PID {pid}: {exc}",
             level=logging.ERROR, pid=pid, error=str(exc))
        return False


def _show_already_running_message(lock: SingleInstanceLock) -> str:
    """10.9.0: interaktiv dialog vid upptaget lås.

    Returnerar valt alternativ: "show" | "restart" | "cancel".
    Löser scenariot "osynlig zombie håller låset": användaren kan nu
    stänga den gamla instansen direkt i stället för att stå handfallen.
    """
    qapp = QApplication.instance() or QApplication(sys.argv[:1])
    qapp.setApplicationDisplayName(APP_DISPLAY_NAME)
    pid = lock.get_pid()
    message = f"{APP_DISPLAY_NAME} körs redan."
    if pid is not None:
        message += f"\nProcess-ID: {pid}"
    message += "\n\nOm appen är osynlig (ingen ikon i systemfältet) kan du stänga den gamla instansen här."
    if lock.last_error:
        message += f"\n\nTeknisk information: {lock.last_error}"
    box = QMessageBox(None)
    box.setWindowTitle("Redan igång")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(message)
    show_btn = box.addButton(" Försök visa befintlig", QMessageBox.ButtonRole.AcceptRole)
    restart_btn = box.addButton(
        "🔄 Stäng gammal och starta ny", QMessageBox.ButtonRole.DestructiveRole,
    )
    box.addButton("Avbryt", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked is show_btn:
        return "show"
    if clicked is restart_btn:
        return "restart"
    return "cancel"


def main() -> int:
    """Starta appen med single-instance-lås och lokal aktivering av första instans."""
    lock = SingleInstanceLock(_INSTANCE_NAME)
    if not lock.acquire():
        _evt("main.lock_busy", "Låset upptaget — signalerar befintlig instans",
             level=logging.WARNING, error=lock.last_error)
        if InstanceActivationServer.notify_existing_instance(_INSTANCE_NAME):
            return 10
        action = _show_already_running_message(lock)
        if action == "show":
            InstanceActivationServer.notify_existing_instance(_INSTANCE_NAME)
            return 10
        if action == "restart":
            pid = lock.get_pid()
            if pid is not None and _kill_old_instance(pid):
                time.sleep(1.0)
                if lock.acquire():
                    _evt("main.lock_acquired", "Lås förvärvat efter omstart",
                         pid=os.getpid())
                else:
                    _stderr("Kunde inte förvärva låset efter omstart.")
                    return 1
            else:
                _stderr("Kunde inte avsluta den gamla instansen.")
                return 1
        return 10
    _evt("main.lock_acquired", "Single-instance-lås förvärvat", pid=os.getpid())

    application = Application()
    if not application.setup():
        application.exit()
        lock.release()
        return 1

    application.start_instance_activation_server()
    application.notify_ready()
    try:
        return application.run()
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
