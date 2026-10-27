from __future__ import annotations

# -*- coding: utf-8 -*-
"""
Fil:       plugins/generator/plugin.py
Projekt:   Intygsgenerator
Version:   5.0.2
Ansvar:    Service-plugin för PDF-generering med multi-OS support.
           Delegerar PDF/placeholder/counter till certificates engine.
           Ingen UI-tab. Idempotent livscykel. Thread-safe.

Beroenden: Python 3.12+, plugins/certificates/engine.py

© Rickard Längkvist 2025–{year}
"""

import html
import json
import logging
import os
import platform
import re
import shlex
import subprocess
import sys
import threading
import unicodedata
import weakref
from contextlib import suppress
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Final

if TYPE_CHECKING:
    from core import AppContext

__version__: Final[str] = "5.0.2"
__author__: Final[str] = "Rickard Längkvist"

__all__: list[str] = [
    "GeneratorSettings",
    "AutostartManager",
    "Plugin",
]

PLUGIN_NAME: Final[str] = "Generator"
PLUGIN_VERSION: Final[str] = __version__
PLUGIN_AUTHOR: Final[str] = __author__
PLUGIN_PRIORITY: Final[int] = 50

_START_YEAR: Final[int] = 2025
_CURRENT_YEAR: Final[int] = datetime.now().year
COPYRIGHT: Final[str] = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

IS_WINDOWS: Final[bool] = sys.platform == "win32"
IS_MACOS: Final[bool] = sys.platform == "darwin"
IS_LINUX: Final[bool] = sys.platform.startswith("linux")
IS_FROZEN: Final[bool] = bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))

CONFIG_FILENAME: Final[str] = "generator_settings.json"
CONFIG_DIR_NAME: Final[str] = "js"
APP_ICON_PATH: Final[Path] = Path(__file__).resolve().parents[2] / "Icon" / "icon.svg"

UnsubscribeFunc = Callable[[], None]

_LOGGER = logging.getLogger(__name__)

CORE_AVAILABLE: bool = False
try:
    from core import AppContext as _CoreAppContext  # noqa: F401

    CORE_AVAILABLE = True
except Exception:
    CORE_AVAILABLE = False

ENGINE_AVAILABLE: bool = False
_engine_safe_filename: Any = None

try:
    from plugins.certificates.engine import (
        CertificateGenerator as _EngineCertificateGenerator,
        CounterManager as _EngineCounterManager,
        PDFConverter as _EnginePDFConverter,
        PlatformHelper as _EnginePlatformHelper,
        Template as _EngineTemplate,
        safe_filename as _engine_safe_filename,
    )

    ENGINE_AVAILABLE = True
except Exception:
    _EngineCertificateGenerator = None
    _EngineCounterManager = None
    _EnginePDFConverter = None
    _EnginePlatformHelper = None
    _EngineTemplate = None
    _engine_safe_filename = None


_FORBIDDEN_CHARS: Final[str] = '<>:"/\\|?*\0'


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    year = datetime.now().year
    return (
        f"© Rickard Längkvist {_START_YEAR}–{year}"
        if year > _START_YEAR
        else f"© Rickard Längkvist {_START_YEAR}"
    )


def _log_debug(logger: logging.Logger | None, message: str, *args: Any) -> None:
    if logger is not None:
        logger.debug(message, *args)


def _log_warning(logger: logging.Logger | None, message: str, *args: Any) -> None:
    if logger is not None:
        logger.warning(message, *args)


def _log_error(logger: logging.Logger | None, message: str, *args: Any) -> None:
    if logger is not None:
        logger.error(message, *args)


def _slug(value: str, separator: str = "") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", separator, value.lower()).strip(separator)
    return cleaned or "intygsgenerator"


def _atomic_write_text(path: Path, content: str) -> bool:
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception:
        _LOGGER.exception("[Generator] Atomic write misslyckades: %s", path)
        with suppress(Exception):
            tmp.unlink(missing_ok=True)
        return False


def safe_filename(name: str, max_length: int = 200) -> str:
    """
    Returnera filsystemssäkert filnamn med ÅÄÖ bevarat.

    Delegerar till certificates engine om tillgänglig. Annars:
    NFC-normalisering → ersätt förbjudna tecken → trim → begränsa längd.
    """
    if ENGINE_AVAILABLE and _engine_safe_filename is not None:
        with suppress(Exception):
            return str(_engine_safe_filename(name, max_length))

    if not name or not str(name).strip():
        return "untitled"

    normalized = unicodedata.normalize("NFC", str(name))
    cleaned = "".join(
        "_" if (char in _FORBIDDEN_CHARS or ord(char) < 32) else char
        for char in normalized
    )
    cleaned = re.sub(r"_+", "_", cleaned).strip(". ").strip()
    if max_length > 0:
        cleaned = cleaned[:max_length].rstrip(". ")
    return cleaned or "untitled"


@dataclass(slots=True, eq=True)
class GeneratorSettings:
    """
    Inställningar för Generator-plugin.

    Lagras primärt i settings.json via ConfigManager.
    Faller tillbaka på generator_settings.json för legacy-migration.
    """

    start_with_os: bool = False
    minimize_to_tray: bool = True
    start_minimized: bool = True
    show_notifications: bool = True
    auto_open_pdf: bool = False

    company_name: str = ""
    company_org_number: str = ""
    company_address: str = ""
    company_phone: str = ""
    company_email: str = ""

    @classmethod
    def field_names(cls) -> list[str]:
        """Returnera alla fältnamn."""
        return [item.name for item in fields(cls)]

    def to_dict(self) -> dict[str, Any]:
        """Serialisera till dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GeneratorSettings:
        """Skapa instans från dict. Okända nycklar ignoreras."""
        if not isinstance(data, dict):
            return cls()
        valid = set(cls.field_names())
        filtered = {key: value for key, value in data.items() if key in valid}
        return cls(**filtered)

    @classmethod
    def from_config(cls, config: Any) -> GeneratorSettings:
        """Läs inställningar från ConfigManager-liknande objekt."""
        defaults = cls()
        if config is None:
            return defaults

        getter = getattr(config, "get", None)
        if not callable(getter):
            return defaults

        values: dict[str, Any] = {}
        for field_name in cls.field_names():
            default_value = getattr(defaults, field_name)
            try:
                values[field_name] = getter(f"generator.{field_name}", default_value)
            except Exception:
                _LOGGER.debug(
                    "[GeneratorSettings] Kunde inte läsa generator.%s",
                    field_name,
                    exc_info=True,
                )
                values[field_name] = default_value

        return cls.from_dict(values)

    def save_to_config(self, config: Any) -> bool:
        """Spara till ConfigManager-liknande objekt."""
        if config is None:
            return False

        setter = getattr(config, "set", None)
        saver = getattr(config, "save", None)
        if not callable(setter):
            return False

        try:
            for key, value in self.to_dict().items():
                self._call_setter(setter, f"generator.{key}", value)

            if callable(saver):
                self._call_saver(saver)
            return True
        except Exception:
            _LOGGER.exception("[GeneratorSettings] save_to_config misslyckades")
            return False

    @staticmethod
    def _call_setter(setter: Callable[..., Any], key: str, value: Any) -> None:
        """Anropa setter med progressiv fallback på kwargs."""
        try:
            setter(key, value, notify=False, auto_save=False)
            return
        except TypeError:
            pass

        try:
            setter(key, value, notify=False)
            return
        except TypeError:
            pass

        setter(key, value)

    @staticmethod
    def _call_saver(saver: Callable[..., Any]) -> None:
        """Anropa saver med progressiv fallback."""
        try:
            saver(force=True)
            return
        except TypeError:
            pass

        saver()

    @classmethod
    def load_from_file(cls, path: Path) -> GeneratorSettings:
        """Läs från legacy JSON-fil."""
        if not path.exists():
            return cls()

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return cls.from_dict(data)
        except Exception:
            _LOGGER.exception("[GeneratorSettings] Kunde inte läsa legacy-fil: %s", path)

        return cls()

    def save_to_file(self, path: Path) -> bool:
        """Spara till JSON-fil med atomic write."""
        content = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        return _atomic_write_text(path, content)


class AutostartManager:
    """
    Hanterar autostart med operativsystemet.

    Windows : Registry HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
    macOS   : ~/Library/LaunchAgents/<slug>.plist
    Linux   : ~/.config/autostart/<slug>.desktop
    """

    APP_NAME: ClassVar[str] = "Intygsgenerator"
    _REGISTRY_KEY: ClassVar[str] = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(
        self,
        app_name: str = APP_NAME,
        log: logging.Logger | None = None,
    ) -> None:
        self.app_name = app_name
        self._log = log

    def _debug(self, msg: str) -> None:
        _log_debug(self._log, "[Autostart] %s", msg)

    def _warning(self, msg: str) -> None:
        _log_warning(self._log, "[Autostart] %s", msg)

    def _error(self, msg: str) -> None:
        _log_error(self._log, "[Autostart] %s", msg)

    @property
    def is_enabled(self) -> bool:
        """Kontrollera om autostart är aktiverad i OS."""
        try:
            if IS_WINDOWS:
                return self._check_windows()
            if IS_MACOS:
                return self._get_macos_plist().exists()
            if IS_LINUX:
                return self._get_linux_desktop().exists()
        except Exception as exc:
            self._error(f"Kontroll misslyckades: {exc}")
        return False

    def enable(self, minimized: bool = True) -> bool:
        """Aktivera autostart i OS."""
        try:
            if IS_WINDOWS:
                return self._enable_windows(minimized)
            if IS_MACOS:
                return self._enable_macos(minimized)
            if IS_LINUX:
                return self._enable_linux(minimized)
        except Exception as exc:
            self._error(f"Aktivering misslyckades: {exc}")
        return False

    def disable(self) -> bool:
        """Avaktivera autostart i OS."""
        try:
            if IS_WINDOWS:
                return self._disable_windows()
            if IS_MACOS:
                return self._disable_macos()
            if IS_LINUX:
                return self._disable_linux()
        except Exception as exc:
            self._error(f"Inaktivering misslyckades: {exc}")
        return False

    def set_enabled(self, enabled: bool, minimized: bool = True) -> bool:
        """Sätt autostart till önskat tillstånd."""
        return self.enable(minimized) if enabled else self.disable()

    def toggle(self) -> bool:
        """Växla autostart. Returnerar nytt tillstånd."""
        if self.is_enabled:
            self.disable()
            return False
        self.enable()
        return True

    def _resolve_launcher(self) -> list[str]:
        """Bestäm körbar fil och main.pyw-sökväg."""
        root = Path(__file__).resolve().parent.parent.parent

        if IS_FROZEN:
            executable = Path(sys.executable).resolve()
            return [str(executable)]

        main_path = root / "main.pyw"
        if not main_path.exists():
            fallback = root / "main.py"
            main_path = fallback if fallback.exists() else Path(sys.argv[0]).resolve()

        if IS_WINDOWS:
            executable = Path(sys.executable).resolve()
            if executable.name.lower() == "python.exe":
                pythonw = executable.with_name("pythonw.exe")
                if pythonw.exists():
                    executable = pythonw
            return [str(executable), str(main_path)]

        return [str(Path(sys.executable).resolve()), str(main_path)]

    def _build_command(self, minimized: bool = True) -> list[str]:
        """Bygg kommandolista för autostart."""
        command = self._resolve_launcher()
        if minimized:
            command.append("--minimized")
        return command

    def _command_string(self, minimized: bool = True) -> str:
        """Bygg kommandosträng för autostart."""
        command = self._build_command(minimized)
        if IS_WINDOWS:
            return subprocess.list2cmdline(command)
        return " ".join(shlex.quote(part) for part in command)

    def _check_windows(self) -> bool:
        import winreg

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self._REGISTRY_KEY,
                0,
                winreg.KEY_READ,
            )
            try:
                winreg.QueryValueEx(key, self.app_name)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _enable_windows(self, minimized: bool) -> bool:
        import winreg

        command = self._command_string(minimized)
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self._REGISTRY_KEY,
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.SetValueEx(key, self.app_name, 0, winreg.REG_SZ, command)
                self._debug(f"Windows autostart aktiverad: {command}")
                return True
            finally:
                winreg.CloseKey(key)
        except Exception as exc:
            self._error(f"Windows enable: {exc}")
            return False

    def _disable_windows(self) -> bool:
        import winreg

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                self._REGISTRY_KEY,
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                with suppress(FileNotFoundError):
                    winreg.DeleteValue(key, self.app_name)
            finally:
                winreg.CloseKey(key)
            self._debug("Windows autostart avaktiverad")
            return True
        except Exception as exc:
            self._error(f"Windows disable: {exc}")
            return False

    def _get_macos_plist(self) -> Path:
        slug = _slug(self.app_name)
        return (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / f"com.rickardlangkvist.{slug}.plist"
        )

    def _enable_macos(self, minimized: bool) -> bool:
        command = self._build_command(minimized)
        slug = _slug(self.app_name)
        args_xml = "\n".join(
            f"        <string>{html.escape(argument)}</string>"
            for argument in command
        )
        plist = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "    <key>Label</key>\n"
            f"    <string>com.rickardlangkvist.{slug}</string>\n"
            "    <key>ProgramArguments</key>\n"
            f"    <array>\n{args_xml}\n    </array>\n"
            "    <key>RunAtLoad</key>\n"
            "    <true/>\n"
            "    <key>KeepAlive</key>\n"
            "    <false/>\n"
            "</dict>\n"
            "</plist>\n"
        )
        path = self._get_macos_plist()
        if _atomic_write_text(path, plist):
            self._debug(f"macOS LaunchAgent skapad: {path}")
            return True
        return False

    def _disable_macos(self) -> bool:
        try:
            with suppress(FileNotFoundError):
                self._get_macos_plist().unlink()
            self._debug("macOS LaunchAgent borttagen")
            return True
        except Exception as exc:
            self._error(f"macOS disable: {exc}")
            return False

    def _get_linux_desktop(self) -> Path:
        xdg = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg) if xdg else Path.home() / ".config"
        slug = _slug(self.app_name, separator="-")
        return base / "autostart" / f"{slug}.desktop"

    def _enable_linux(self, minimized: bool) -> bool:
        command = self._command_string(minimized)
        desktop = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=IntygsDrive\n"
            f"Exec={command}\n"
            f"Icon={APP_ICON_PATH}\n"
            "Terminal=false\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Categories=Office;Utility;\n"
        )
        path = self._get_linux_desktop()
        if _atomic_write_text(path, desktop):
            with suppress(Exception):
                path.chmod(0o755)
            self._debug(f"Linux autostart skapad: {path}")
            return True
        return False

    def _disable_linux(self) -> bool:
        try:
            with suppress(FileNotFoundError):
                self._get_linux_desktop().unlink()
            self._debug("Linux autostart borttagen")
            return True
        except Exception as exc:
            self._error(f"Linux disable: {exc}")
            return False


class Plugin:
    """Generator service-plugin utan UI-tab."""

    name: ClassVar[str] = PLUGIN_NAME
    version: ClassVar[str] = PLUGIN_VERSION
    description: ClassVar[str] = "Service-plugin för PDF-generering med multi-OS support"
    author: ClassVar[str] = PLUGIN_AUTHOR
    priority: ClassVar[int] = PLUGIN_PRIORITY
    has_tab: ClassVar[bool] = False
    is_service: ClassVar[bool] = True

    def __init__(self) -> None:
        self._context: Any | None = None
        self._event_bus: Any | None = None
        self._log: logging.Logger | None = None

        self._initialized: bool = False
        self._closed: bool = False
        self._lock: threading.RLock = threading.RLock()

        self._config_path: Path | None = None
        self._output_path: Path | None = None
        self._templates_path: Path | None = None
        self._legacy_settings_path: Path | None = None
        self._config_manager: Any | None = None

        self._settings: GeneratorSettings = GeneratorSettings()

        self._autostart_manager: AutostartManager | None = None
        self._pdf_converter: Any | None = None
        self._counter_manager: Any | None = None
        self._shared_from_certificates: bool = False

        self._subscriptions: list[UnsubscribeFunc] = []
        self._connected: dict[str, weakref.ReferenceType[Any]] = {}

    @property
    def log(self) -> logging.Logger:
        if self._log is None:
            self._log = logging.getLogger(f"Intygsgenerator.{self.name}")
        return self._log

    @property
    def pdf_converter(self) -> Any | None:
        """Exponera PDFConverter."""
        return self._pdf_converter

    @property
    def counter_manager(self) -> Any | None:
        """Exponera CounterManager."""
        return self._counter_manager

    @property
    def autostart_manager(self) -> AutostartManager | None:
        return self._autostart_manager

    @property
    def settings(self) -> dict[str, Any]:
        """Returnera aktuella inställningar som dict."""
        return self._settings.to_dict()

    @property
    def is_pdf_available(self) -> bool:
        """True om PDFConverter är redo."""
        if not self._pdf_converter:
            return False
        with suppress(Exception):
            return bool(getattr(self._pdf_converter, "is_available", False))
        return False

    @property
    def conversion_method(self) -> str:
        """Namn på aktiv konverteringsmetod."""
        if not self._pdf_converter:
            return "Ingen"
        with suppress(Exception):
            return str(getattr(self._pdf_converter, "method_name", "Okänd"))
        return "Okänd"

    @property
    def is_autostart_enabled(self) -> bool:
        """True om autostart är aktiverad i OS."""
        if not self._autostart_manager:
            return False
        return self._autostart_manager.is_enabled

    def on_load(self, context: Any) -> None:
        """Initiera plugin. Idempotent."""
        with self._lock:
            if self._initialized:
                return

            self._context = context
            self._event_bus = getattr(context, "event_bus", None)

            if hasattr(context, "get_logger"):
                with suppress(Exception):
                    self._log = context.get_logger(self.name)

            self._setup_paths()
            self._config_manager = self._resolve_config_manager(context)
            self._settings = self._load_settings()
            self._init_components()
            self._subscribe_events()
            self._apply_autostart()

            self._initialized = True
            self._closed = False

        self.log.info(
            "⚙️ %s v%s initierat — PDF: %s | OS: %s | engine: %s",
            self.name,
            self.version,
            self.conversion_method,
            platform.system(),
            "OK" if ENGINE_AVAILABLE else "fallback",
        )

    def setup(self, context: Any) -> None:
        """Legacy-alias för on_load."""
        self.on_load(context)

    def on_ready(self) -> None:
        """Anropas via plugin_manager.ready-event."""
        self._connect_plugins()
        self._emit(
            "generator.ready",
            {
                "version": self.version,
                "pdf_available": self.is_pdf_available,
                "method": self.conversion_method,
                "autostart": self.is_autostart_enabled,
                "engine": ENGINE_AVAILABLE,
                "shared_from_certificates": self._shared_from_certificates,
            },
        )
        self.log.info("✅ %s redo", self.name)

    def on_unload(self) -> None:
        """Städa resurser. Idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        self.log.info("👋 %s avslutas...", self.name)
        self._save_settings()

        if self._counter_manager and not self._shared_from_certificates:
            with suppress(Exception):
                save_fn = getattr(self._counter_manager, "save", None)
                if callable(save_fn):
                    save_fn()

        for unsubscribe in self._subscriptions:
            with suppress(Exception):
                unsubscribe()

        self._subscriptions.clear()
        self._connected.clear()
        self.log.info("✓ %s avslutat", self.name)

    def on_close(self) -> None:
        """Legacy-alias för on_unload."""
        self.on_unload()

    def get_tab(self) -> None:
        """Ingen UI-tab."""
        return None

    @staticmethod
    def _resolve_config_manager(context: Any) -> Any | None:
        """Hitta ConfigManager från context."""
        for attr in ("settings", "config_manager", "config"):
            value = getattr(context, attr, None)
            if value is not None:
                return value
        return None

    def _setup_paths(self) -> None:
        """Sätt upp sökvägar från context eller defaults."""
        if self._context and hasattr(self._context, "get_path"):
            for key, attr in (
                ("config", "_config_path"),
                ("output", "_output_path"),
                ("templates", "_templates_path"),
            ):
                with suppress(Exception):
                    setattr(self, attr, self._context.get_path(key))

        base = Path(__file__).resolve().parent.parent.parent
        if not self._config_path:
            self._config_path = base / CONFIG_DIR_NAME
        if not self._output_path:
            self._output_path = base / "Out"
        if not self._templates_path:
            self._templates_path = base / "Mallar"

        self._legacy_settings_path = self._config_path / CONFIG_FILENAME

        for path in (self._config_path, self._output_path, self._templates_path):
            with suppress(Exception):
                if path:
                    path.mkdir(parents=True, exist_ok=True)

    def _load_settings(self) -> GeneratorSettings:
        """Ladda inställningar från ConfigManager eller legacy-fil."""
        if self._config_manager is not None:
            settings = GeneratorSettings.from_config(self._config_manager)
            defaults = GeneratorSettings()

            if settings == defaults and self._legacy_settings_path:
                legacy = GeneratorSettings.load_from_file(self._legacy_settings_path)
                if legacy != defaults:
                    self.log.info(
                        "[%s] Migrerar legacy settings: %s → settings.json",
                        self.name,
                        self._legacy_settings_path.name,
                    )
                    legacy.save_to_config(self._config_manager)
                    return legacy

            return settings

        if self._legacy_settings_path:
            return GeneratorSettings.load_from_file(self._legacy_settings_path)

        return GeneratorSettings()

    def _save_settings(self) -> bool:
        """Spara inställningar."""
        if self._config_manager is not None:
            return self._settings.save_to_config(self._config_manager)
        if self._legacy_settings_path:
            return self._settings.save_to_file(self._legacy_settings_path)
        return False

    def _init_components(self) -> None:
        """Initiera AutostartManager och engine-komponenter."""
        self._autostart_manager = AutostartManager(
            app_name="Intygsgenerator",
            log=self.log,
        )

        if not ENGINE_AVAILABLE:
            self.log.info(
                "[%s] Certificates engine ej tillgänglig — begränsat läge",
                self.name,
            )
            return

        try:
            config_file = (
                self._config_path / "certificates_counters.json"
                if self._config_path
                else None
            )
            self._counter_manager = _EngineCounterManager(
                config_manager=self._config_manager,
                config_path=config_file,
            )
            self._pdf_converter = _EnginePDFConverter(log=self.log)
            self._shared_from_certificates = False
            self.log.debug(
                "[%s] Engine initierad (PDF: %s)",
                self.name,
                self.conversion_method,
            )
        except Exception as exc:
            self._counter_manager = None
            self._pdf_converter = None
            self.log.warning("[%s] Engine init misslyckades: %s", self.name, exc)

    def _subscribe_events(self) -> None:
        """Prenumerera på EventBus-events."""
        handlers: list[tuple[str, Callable[[Any], Any]]] = [
            ("settings.changed", self._on_settings_changed),
            ("app.closing", self._on_app_closing),
            ("app.shutdown", self._on_app_closing),
            ("certificates.imported", self._on_certs_imported),
            ("plugin_manager.ready", self._on_plugin_manager_ready),
        ]

        for event_name, handler in handlers:
            unsubscribe = self._subscribe(event_name, handler)
            if unsubscribe:
                self._subscriptions.append(unsubscribe)

    def _apply_autostart(self) -> None:
        """Synka OS-autostart med settings."""
        if not self._autostart_manager:
            return

        should_enable = self._settings.start_with_os
        current = self._autostart_manager.is_enabled

        if should_enable and not current:
            self._autostart_manager.enable(minimized=True)
            self.log.debug("[%s] Autostart aktiverad från settings", self.name)
        elif not should_enable and current:
            self._autostart_manager.disable()
            self.log.debug("[%s] Autostart avaktiverad från settings", self.name)

    def _on_plugin_manager_ready(self, data: Any) -> None:
        """Trigga on_ready via plugin_manager.ready-event."""
        if not self._initialized or self._closed:
            return
        with suppress(Exception):
            self.on_ready()

    def _on_settings_changed(self, data: Any) -> None:
        """Hantera settings.changed."""
        if not isinstance(data, dict):
            return

        key = str(data.get("key", ""))
        value = data.get("value")
        if not key.startswith("generator."):
            return

        attr = key.removeprefix("generator.")
        if not hasattr(self._settings, attr):
            return

        setattr(self._settings, attr, value)
        self._save_settings()

        if attr == "start_with_os":
            self._apply_autostart()

        self._emit("generator.settings_changed", {"key": attr, "value": value})

    def _on_app_closing(self, data: Any = None) -> None:
        """Hantera app.closing / app.shutdown."""
        self.on_unload()

    def _on_certs_imported(self, data: Any) -> None:
        """Logga import av intyg."""
        if not isinstance(data, dict):
            return

        template = str(data.get("template", "")).strip()
        count = int(data.get("count", 0) or 0)
        if template and count > 0:
            self.log.info(
                "[%s] Certificates imported: %s +%d",
                self.name,
                template,
                count,
            )

    def _connect_plugins(self) -> None:
        """Koppla svaga referenser till kända plugins."""
        plugin_map: dict[str, list[str]] = {
            "certificates": ["Intyg", "Certificates", "certificates"],
            "statistics": ["Statistik", "Statistics", "statistics"],
            "settings": ["Inställningar", "Settings", "settings"],
            "tray": ["System Tray", "system_tray"],
        }

        for key, names in plugin_map.items():
            for plugin_name in names:
                plugin = self._get_plugin(plugin_name)
                if plugin is None:
                    continue

                self._connected[key] = weakref.ref(plugin)
                self.log.debug("🔗 [%s] Kopplad till %s", self.name, plugin_name)

                if key == "certificates":
                    self._adopt_certificates_services(plugin)
                break

    def _adopt_certificates_services(self, plugin: Any) -> None:
        """Återanvänd PDFConverter och CounterManager från certificates-plugin."""
        adopted_counter: Any | None = None
        adopted_converter: Any | None = None

        for attr in ("counter_manager", "_counter_manager"):
            value = getattr(plugin, attr, None)
            if value is not None:
                adopted_counter = value
                break

        for attr in ("pdf_converter", "_pdf_converter"):
            value = getattr(plugin, attr, None)
            if value is not None:
                adopted_converter = value
                break

        if adopted_counter is not None:
            self._counter_manager = adopted_counter
        if adopted_converter is not None:
            self._pdf_converter = adopted_converter

        if adopted_counter is not None or adopted_converter is not None:
            self._shared_from_certificates = True
            self.log.info(
                "[%s] Återanvänder services från certificates-plugin (counter=%s, converter=%s)",
                self.name,
                "OK" if adopted_counter is not None else "saknas",
                "OK" if adopted_converter is not None else "saknas",
            )

    def _get_plugin(self, name: str) -> Any | None:
        """Hämta plugin via context."""
        if not self._context:
            return None

        getter = getattr(self._context, "get_plugin", None)
        if callable(getter):
            with suppress(Exception):
                result = getter(name)
                if result is not None:
                    return result

        for attr in ("_plugin_manager", "plugin_manager"):
            manager = getattr(self._context, attr, None)
            if manager is None:
                continue
            get_plugin = getattr(manager, "get_plugin", None)
            if callable(get_plugin):
                with suppress(Exception):
                    result = get_plugin(name)
                    if result is not None:
                        return result

        plugins_dict = getattr(self._context, "_plugins", None)
        if isinstance(plugins_dict, dict):
            return plugins_dict.get(name)

        return None

    def get_connected_plugin(self, name: str) -> Any | None:
        """Returnera kopplad plugin via svag referens."""
        ref = self._connected.get(name)
        return ref() if ref else None

    def enable_autostart(self, minimized: bool = True) -> bool:
        """Aktivera autostart och spara setting."""
        if not self._autostart_manager:
            return False

        success = self._autostart_manager.enable(minimized)
        if success:
            self._settings.start_with_os = True
            self._save_settings()
        return success

    def disable_autostart(self) -> bool:
        """Avaktivera autostart och spara setting."""
        if not self._autostart_manager:
            return False

        success = self._autostart_manager.disable()
        if success:
            self._settings.start_with_os = False
            self._save_settings()
        return success

    def toggle_autostart(self) -> bool:
        """Växla autostart. Returnerar nytt tillstånd."""
        if self.is_autostart_enabled:
            self.disable_autostart()
            return False
        self.enable_autostart()
        return True

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Hämta setting."""
        return getattr(self._settings, key, default)

    def set_setting(self, key: str, value: Any) -> bool:
        """Sätt setting och spara."""
        if not hasattr(self._settings, key):
            return False

        setattr(self._settings, key, value)
        if key == "start_with_os":
            self._apply_autostart()
        return self._save_settings()

    def get_counter(self, template_name: str) -> int:
        """Returnera aktuell räknare för mall."""
        if self._counter_manager:
            with suppress(Exception):
                return int(self._counter_manager.get_current(template_name))
        return 0

    def get_all_counters(self) -> dict[str, int]:
        """Returnera alla räknare."""
        if self._counter_manager:
            with suppress(Exception):
                raw = self._counter_manager.get_all()
                if isinstance(raw, dict):
                    return {str(key): int(value) for key, value in raw.items()}
        return {}

    def get_total_count(self) -> int:
        """Returnera totalt antal genererade intyg."""
        if self._counter_manager:
            with suppress(Exception):
                return int(self._counter_manager.get_total())
        return 0

    def convert_to_pdf(
        self,
        input_path: str | Path,
        output_path: str | Path | None = None,
    ) -> tuple[bool, Path | None]:
        """Konvertera fil till PDF."""
        if not self._pdf_converter:
            return False, None

        try:
            result = self._pdf_converter.convert(
                Path(input_path),
                Path(output_path) if output_path else None,
            )
            if result:
                result_path = Path(result)
                if result_path.exists():
                    return True, result_path
        except Exception as exc:
            self.log.error("[%s] PDF conversion failed: %s", self.name, exc)

        return False, None

    def create_certificate_generator(self) -> Any | None:
        """Skapa CertificateGenerator-instans."""
        if not ENGINE_AVAILABLE or _EngineCertificateGenerator is None:
            return None

        if not all((self._output_path, self._counter_manager, self._pdf_converter)):
            return None

        try:
            return _EngineCertificateGenerator(
                self._output_path,
                self._counter_manager,
                self._pdf_converter,
                self.log,
            )
        except Exception as exc:
            self.log.error("[%s] create_certificate_generator failed: %s", self.name, exc)
            return None

    def open_file(self, path: str | Path) -> bool:
        """Öppna fil med OS-standardprogram."""
        target = Path(path)

        if ENGINE_AVAILABLE and _EnginePlatformHelper is not None:
            with suppress(Exception):
                return bool(_EnginePlatformHelper.open_file(target))

        try:
            if not target.exists():
                return False
            if IS_WINDOWS:
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif IS_MACOS:
                subprocess.run(["open", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target)], check=False)
            return True
        except Exception:
            self.log.exception("[%s] open_file failed: %s", self.name, target)
            return False

    def open_folder(self, path: str | Path) -> bool:
        """Öppna mapp i filhanterare."""
        target = Path(path)
        if target.is_file():
            target = target.parent
        with suppress(Exception):
            target.mkdir(parents=True, exist_ok=True)
        return self.open_file(target)

    def open_output_folder(self) -> bool:
        """Öppna Out/-mappen."""
        if self._output_path:
            return self.open_folder(self._output_path)
        return False

    def sanitize_filename(self, filename: str, max_length: int = 200) -> str:
        """Sanera filnamn och bevara ÅÄÖ."""
        return safe_filename(filename, max_length)

    def get_statistics(self) -> dict[str, Any]:
        """Returnera status/statistik."""
        connected: dict[str, bool] = {
            key: bool(ref() if ref else None)
            for key, ref in self._connected.items()
        }

        return {
            "plugin_name": self.name,
            "plugin_version": self.version,
            "platform": platform.system(),
            "python": platform.python_version(),
            "pdf_available": self.is_pdf_available,
            "conversion_method": self.conversion_method,
            "autostart_enabled": self.is_autostart_enabled,
            "engine_available": ENGINE_AVAILABLE,
            "shared_from_certificates": self._shared_from_certificates,
            "total_generated": self.get_total_count(),
            "counters": self.get_all_counters(),
            "connected_plugins": connected,
            "settings": self._settings.to_dict(),
            "copyright": COPYRIGHT,
        }

    def _emit(self, event: str, data: Any = None) -> None:
        """Emittera event via EventBus eller context.emit_event."""
        payload = data if isinstance(data, dict) else {}

        if self._event_bus and hasattr(self._event_bus, "emit"):
            with suppress(Exception):
                self._event_bus.emit(event, payload)
                return

        if self._context and hasattr(self._context, "emit_event"):
            with suppress(Exception):
                self._context.emit_event(event, payload)

    def _subscribe(
        self,
        event: str,
        handler: Callable[[Any], Any],
    ) -> UnsubscribeFunc | None:
        """Prenumerera på EventBus-event."""
        if self._event_bus and hasattr(self._event_bus, "subscribe"):
            with suppress(Exception):
                unsubscribe = self._event_bus.subscribe(event, handler)
                if callable(unsubscribe):
                    return unsubscribe
        return None

    def emit(self, event: str, data: Any = None) -> None:
        """Publik emit-metod."""
        self._emit(event, data)

    def subscribe(
        self,
        event: str,
        handler: Callable[[Any], Any],
    ) -> UnsubscribeFunc:
        """Publik subscribe. Returnerar alltid callable unsubscribe."""
        unsubscribe = self._subscribe(event, handler)
        return unsubscribe if unsubscribe is not None else (lambda: None)

    def __repr__(self) -> str:
        return (
            f"<Plugin(Generator) v{self.version} "
            f"init={self._initialized} "
            f"pdf={self.is_pdf_available} "
            f"shared={self._shared_from_certificates}>"
        )

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"