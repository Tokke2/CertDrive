from __future__ import annotations

# plugins/settings/core.py
# Sökväg: Intygsgenerator/plugins/settings/core.py
# Intygsgenerator — Enums, dataklasser, managers för Settings-plugin.
#
# Version: 9.0.0
# © Rickard Längkvist 2025–2026
#
# Nytt i v9.0.0:
#   • "text" nyckel i PALETTES (BUG-015 konsistens)
#   • AutostartManager: sys.argv[0] istället för import __main__
#   • Cachad detect_system_theme (TTL 30s)
#   • Ta bort encoding-kommentar
#
# Beroenden: Python 3.12+, PySide6 (valfritt), core/ (valfritt)

import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable

__version__: str = "9.1.0"
__author__: str = "Rickard Längkvist"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_logger: logging.Logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# PLATTFORMSHJÄLPARE
# ══════════════════════════════════════════════════════════════════════════════

try:
    from core.infrastructure import (
        PlatformHelper as _CorePlatformHelper,
    )

    _CORE_PLATFORM_AVAILABLE: bool = True
except Exception as exc:
    _CorePlatformHelper = None
    _CORE_PLATFORM_AVAILABLE = False
    _logger.debug("[SETTINGS] core.infrastructure saknas eller kunde inte laddas: %s", exc)


class PlatformHelper:
    """Plattformshjälpare (MUST-015 cross-platform)."""

    @staticmethod
    def is_windows() -> bool:
        if _CORE_PLATFORM_AVAILABLE and _CorePlatformHelper:
            try:
                return _CorePlatformHelper.is_windows()
            except Exception:
                _logger.debug("[SETTINGS] Core PlatformHelper.is_windows misslyckades", exc_info=True)
        return sys.platform == "win32"

    @staticmethod
    def is_macos() -> bool:
        if _CORE_PLATFORM_AVAILABLE and _CorePlatformHelper:
            try:
                return _CorePlatformHelper.is_macos()
            except Exception:
                _logger.debug("[SETTINGS] Core PlatformHelper.is_macos misslyckades", exc_info=True)
        return sys.platform == "darwin"

    @staticmethod
    def is_linux() -> bool:
        if _CORE_PLATFORM_AVAILABLE and _CorePlatformHelper:
            try:
                return _CorePlatformHelper.is_linux()
            except Exception:
                _logger.debug("[SETTINGS] Core PlatformHelper.is_linux misslyckades", exc_info=True)
        return sys.platform.startswith("linux")

    @staticmethod
    def get_platform_name() -> str:
        if PlatformHelper.is_windows():
            return "Windows"
        if PlatformHelper.is_macos():
            return "macOS"
        if PlatformHelper.is_linux():
            return "Linux"
        return "Unknown"

    @staticmethod
    def get_home_dir() -> Path:
        return Path.home()

    @staticmethod
    def get_config_dir(
        app_name: str = "Intygsgenerator",
    ) -> Path:
        if PlatformHelper.is_windows():
            base = Path(
                os.environ.get(
                    "APPDATA", str(Path.home())
                )
            )
        elif PlatformHelper.is_macos():
            base = (
                Path.home()
                / "Library"
                / "Application Support"
            )
        else:
            base = Path(
                os.environ.get(
                    "XDG_CONFIG_HOME",
                    str(Path.home() / ".config"),
                )
            )
        return base / app_name

    @staticmethod
    def open_file(path: Path | str) -> bool:
        """Öppna fil eller mapp utan att blockera UI-tråden."""
        target = Path(path)
        if not target.exists():
            _logger.warning("[SETTINGS] Sökväg saknas: %s", target)
            return False
        if _CORE_PLATFORM_AVAILABLE and _CorePlatformHelper:
            try:
                return bool(_CorePlatformHelper.open_file(target))
            except Exception:
                _logger.exception("[SETTINGS] Core PlatformHelper kunde inte öppna %s", target)
        try:
            if PlatformHelper.is_windows():
                startfile = getattr(os, "startfile", None)
                if not callable(startfile):
                    return False
                startfile(str(target))
                return True
            command = ["open", str(target)] if PlatformHelper.is_macos() else ["xdg-open", str(target)]
            subprocess.Popen(
                command,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, ValueError) as exc:
            _logger.error("[SETTINGS] Kunde inte öppna %s: %s", target, exc)
            return False

    @staticmethod
    def open_folder(path: Path | str) -> bool:
        target = Path(path)
        if target.is_file():
            target = target.parent
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _logger.error("[SETTINGS] Kunde inte skapa mapp %s: %s", target, exc)
            return False
        return PlatformHelper.open_file(target)

    @staticmethod
    def open_url(url: str) -> bool:
        try:
            import webbrowser

            return bool(webbrowser.open(url))
        except Exception:
            _logger.exception("[SETTINGS] Kunde inte öppna URL: %s", url)
            return False


# ══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ══════════════════════════════════════════════════════════════════════════════


class Theme(Enum):
    """3 teman (§13). Värden matchar ConfigManager-nycklar."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"

    @property
    def display_name(self) -> str:
        return {
            Theme.SYSTEM: "Följ systemet",
            Theme.LIGHT: "Ljust",
            Theme.DARK: "Mörkt",
        }[self]

    @property
    def icon(self) -> str:
        return {
            Theme.SYSTEM: "🖥️",
            Theme.LIGHT: "☀️",
            Theme.DARK: "🌙",
        }[self]

    @classmethod
    def from_string(cls, value: str) -> Theme:
        try:
            return cls(value.lower())
        except ValueError:
            return cls.SYSTEM


class Language(Enum):
    """Gränssnittsspråk."""

    SWEDISH = "sv"
    ENGLISH = "en"

    @property
    def display_name(self) -> str:
        return {
            Language.SWEDISH: "Svenska 🇸🇪",
            Language.ENGLISH: "English 🇬🇧",
        }[self]

    @property
    def flag(self) -> str:
        return {
            Language.SWEDISH: "🇸🇪",
            Language.ENGLISH: "🇬🇧",
        }[self]

    @property
    def code(self) -> str:
        return self.value

    @classmethod
    def from_string(cls, value: str) -> Language:
        try:
            return cls(value.lower())
        except ValueError:
            return cls.SWEDISH


class HealthStatus(Enum):
    """Hälsostatus för systemkontroller."""

    OK = auto()
    WARNING = auto()
    ERROR = auto()
    UNKNOWN = auto()

    @property
    def icon(self) -> str:
        return {
            HealthStatus.OK: "✅",
            HealthStatus.WARNING: "⚠️",
            HealthStatus.ERROR: "❌",
            HealthStatus.UNKNOWN: "❓",
        }[self]

    @property
    def display_name(self) -> str:
        return {
            HealthStatus.OK: "OK",
            HealthStatus.WARNING: "Varning",
            HealthStatus.ERROR: "Fel",
            HealthStatus.UNKNOWN: "Okänd",
        }[self]


class SettingSection(Enum):
    """Inställningssektioner (flik-ID i plugin.py)."""

    STARTUP = "startup"
    GENERAL = "general"
    APPEARANCE = "appearance"
    SHORTCUTS = "shortcuts"
    PDF = "pdf"
    BACKUP = "backup"
    PROFILES = "profiles"
    NOTIFICATIONS = "notifications"
    PLUGINS = "plugins"
    ADVANCED = "advanced"
    DIAGNOSTICS = "diagnostics"
    ABOUT = "about"

    @property
    def display_name(self) -> str:
        return {
            SettingSection.STARTUP: "Start",
            SettingSection.GENERAL: "Allmänt",
            SettingSection.APPEARANCE: "Utseende",
            SettingSection.SHORTCUTS: "Genvägar",
            SettingSection.PDF: "PDF",
            SettingSection.BACKUP: "Backup",
            SettingSection.PROFILES: "Profiler",
            SettingSection.NOTIFICATIONS: "Notiser",
            SettingSection.PLUGINS: "Plugins",
            SettingSection.ADVANCED: "Avancerat",
            SettingSection.DIAGNOSTICS: "Diagnostik",
            SettingSection.ABOUT: "Om",
        }.get(self, self.value)

    @property
    def icon(self) -> str:
        return {
            SettingSection.STARTUP: "🚀",
            SettingSection.GENERAL: "⚙️",
            SettingSection.APPEARANCE: "🎨",
            SettingSection.SHORTCUTS: "⌨️",
            SettingSection.PDF: "📄",
            SettingSection.BACKUP: "💾",
            SettingSection.PROFILES: "👤",
            SettingSection.NOTIFICATIONS: "🔔",
            SettingSection.PLUGINS: "🧩",
            SettingSection.ADVANCED: "🔧",
            SettingSection.DIAGNOSTICS: "🏥",
            SettingSection.ABOUT: "ℹ️",
        }.get(self, "⚙️")


# ══════════════════════════════════════════════════════════════════════════════
# DATAKLASSER
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class SettingDefinition:
    """Definition av en inställning."""

    key: str
    label: str
    description: str = ""
    type: str = "text"
    default: Any = None
    choices: list[Any] = field(default_factory=list)
    choice_labels: dict[str, str] = field(
        default_factory=dict
    )
    min_value: int | float | None = None
    max_value: int | float | None = None
    section: str = "general"
    requires_restart: bool = False
    validator: (
        Callable[[Any], tuple[bool, str]] | None
    ) = None
    icon: str = ""
    tooltip: str = ""
    depends_on: str | None = None

    def validate(
        self, value: Any
    ) -> tuple[bool, str]:
        if self.validator:
            try:
                return self.validator(value)
            except Exception as exc:
                return False, str(exc)
        try:
            if self.type == "bool":
                if not isinstance(value, bool):
                    if isinstance(value, str):
                        value = value.lower() in (
                            "true", "1", "yes", "ja"
                        )
                    else:
                        bool(value)
            elif self.type == "int":
                try:
                    v = int(value)
                except (ValueError, TypeError):
                    return False, "Måste vara heltal"
                if (
                    self.min_value is not None
                    and v < self.min_value
                ):
                    return False, (
                        f"Minst {int(self.min_value)}"
                    )
                if (
                    self.max_value is not None
                    and v > self.max_value
                ):
                    return False, (
                        f"Max {int(self.max_value)}"
                    )
            elif self.type == "float":
                try:
                    v = float(value)
                except (ValueError, TypeError):
                    return False, "Måste vara decimaltal"
                if (
                    self.min_value is not None
                    and v < self.min_value
                ):
                    return False, (
                        f"Minst {self.min_value}"
                    )
                if (
                    self.max_value is not None
                    and v > self.max_value
                ):
                    return False, (
                        f"Max {self.max_value}"
                    )
            elif self.type == "choice" and self.choices:
                if value not in self.choices:
                    return (
                        False,
                        f"Ogiltigt val: {value!r}",
                    )
            elif self.type == "path":
                if value:
                    parent = Path(value).parent
                    if not parent.exists():
                        return False, (
                            f"Sökväg saknas: {parent}"
                        )
            return True, ""
        except Exception as exc:
            return False, f"Valideringsfel: {exc}"

    def get_choice_label(self, value: Any) -> str:
        if self.choice_labels:
            return self.choice_labels.get(
                str(value), str(value)
            )
        return str(value)

    def get_typed_value(self, value: Any) -> Any:
        try:
            if self.type == "bool":
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return value.lower() in (
                        "true", "1", "yes", "ja"
                    )
                return bool(value)
            elif self.type == "int":
                return int(value)
            elif self.type == "float":
                return float(value)
            return value
        except (ValueError, TypeError):
            return self.default


@dataclass
class Profile:
    """Konfigurationsprofil."""

    name: str
    description: str = ""
    created: datetime = field(
        default_factory=datetime.now
    )
    modified: datetime = field(
        default_factory=datetime.now
    )
    settings: dict[str, Any] = field(
        default_factory=dict
    )
    is_default: bool = False
    icon: str = "👤"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "created": self.created.isoformat(),
            "modified": self.modified.isoformat(),
            "settings": dict(self.settings),
            "is_default": self.is_default,
            "icon": self.icon,
        }

    @classmethod
    def from_dict(
        cls, data: dict[str, Any]
    ) -> Profile:
        created = datetime.now()
        if data.get("created"):
            try:
                created = datetime.fromisoformat(
                    data["created"]
                )
            except (ValueError, TypeError):
                pass
        modified = datetime.now()
        if data.get("modified"):
            try:
                modified = datetime.fromisoformat(
                    data["modified"]
                )
            except (ValueError, TypeError):
                pass
        return cls(
            name=data.get("name", "Namnlös"),
            description=data.get("description", ""),
            created=created,
            modified=modified,
            settings=data.get("settings", {}),
            is_default=data.get("is_default", False),
            icon=data.get("icon", "👤"),
        )

    def update_modified(self) -> None:
        self.modified = datetime.now()


@dataclass
class HealthCheck:
    """Resultat av en systemhälsokontroll."""

    name: str
    status: HealthStatus
    message: str
    details: str = ""
    category: str = "System"
    fix_action: Callable[[], None] | None = None
    fix_label: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status == HealthStatus.OK

    @property
    def needs_attention(self) -> bool:
        return self.status in (
            HealthStatus.WARNING,
            HealthStatus.ERROR,
        )


@dataclass
class Shortcut:
    """En tangentbordsgenväg."""

    action: str
    label: str
    default_key: str
    current_key: str = ""
    category: str = "Allmänt"
    description: str = ""
    icon: str = "⌨️"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.current_key:
            self.current_key = self.default_key

    @property
    def is_modified(self) -> bool:
        return self.current_key != self.default_key

    def reset(self) -> None:
        self.current_key = self.default_key

    def to_qkeysequence_string(self) -> str:
        return self.current_key


@dataclass
class TabInfo:
    """Information om en flik i inställnings-UI."""

    id: str
    name: str
    icon: str = ""
    visible: bool = True
    order: int = 0

    @property
    def display_text(self) -> str:
        if self.icon:
            return f"{self.icon} {self.name}"
        return self.name


# ══════════════════════════════════════════════════════════════════════════════
# AUTOSTART MANAGER
# ══════════════════════════════════════════════════════════════════════════════


class AutostartManager:
    """Hanterar autostart (Windows/macOS/Linux).

    Delegerar till core/config.py om tillgänglig.
    """

    def __init__(
        self,
        app_name: str = "Intygsgenerator",
        app_path: str | None = None,
        app_args: list[str] | None = None,
    ) -> None:
        self._app_name = app_name
        self._app_args = app_args or []
        if app_path:
            self._app_path = app_path
        elif sys.platform == "win32":
            py = sys.executable
            self._app_path = py.replace(
                "python.exe", "pythonw.exe"
            )
        else:
            self._app_path = sys.executable
        # Script-argument via sys.argv (säkrare än __main__)
        if sys.argv and sys.argv[0]:
            script = os.path.abspath(sys.argv[0])
            if script not in self._app_args:
                self._app_args = (
                    [script] + self._app_args
                )
        # Delegera till core
        self._core_manager: Any | None = None
        try:
            from core.config import (
                AutostartManager as CoreAutostart,
            )

            self._core_manager = CoreAutostart()
        except Exception as exc:
            _logger.debug("[SETTINGS] core.config.AutostartManager ej tillgänglig: %s", exc)

    def is_enabled(self) -> bool:
        if self._core_manager:
            try:
                return self._core_manager.is_enabled()
            except Exception:
                _logger.debug("[SETTINGS] Core-autostart is_enabled misslyckades", exc_info=True)
        try:
            if PlatformHelper.is_windows():
                return self._is_enabled_windows()
            if PlatformHelper.is_macos():
                return self._is_enabled_macos()
            return self._is_enabled_linux()
        except Exception:
            _logger.debug("[SETTINGS] Kunde inte läsa autostart-status", exc_info=True)
            return False

    def enable(self) -> bool:
        if self._core_manager:
            try:
                self._core_manager.enable()
                return True
            except Exception:
                _logger.debug("[SETTINGS] Core-autostart enable misslyckades", exc_info=True)
        try:
            if PlatformHelper.is_windows():
                return self._enable_windows()
            if PlatformHelper.is_macos():
                return self._enable_macos()
            return self._enable_linux()
        except Exception:
            _logger.exception("[SETTINGS] Kunde inte aktivera autostart")
            return False

    def disable(self) -> bool:
        if self._core_manager:
            try:
                result = self._core_manager.disable()
                return bool(True if result is None else result)
            except Exception:
                _logger.exception("[SETTINGS] Core-autostart kunde inte avaktiveras")
        try:
            if PlatformHelper.is_windows():
                return self._disable_windows()
            if PlatformHelper.is_macos():
                return self._disable_macos()
            return self._disable_linux()
        except Exception:
            _logger.exception("[SETTINGS] Kunde inte avaktivera autostart")
            return False

    def set_enabled(self, enabled: bool) -> bool:
        """Sätt önskat autostartläge med ett kompatibelt gemensamt API."""
        return self.enable() if enabled else self.disable()

    def toggle(self) -> bool:
        """Växla autostart och returnera det nya önskade läget vid framgång."""
        enabled = not self.is_enabled()
        return enabled if self.set_enabled(enabled) else not enabled

    def get_command(self) -> str:
        """Returnera exakt kommando som används vid autostart."""
        parts = [self._app_path, *self._app_args]
        if PlatformHelper.is_windows():
            return " ".join(f'"{part}"' for part in parts)
        return " ".join(shlex.quote(part) for part in parts)

    def sync(self, enabled: bool | None = None) -> bool:
        """Verifiera eller synkronisera lagrat autostartläge mot operativsystemet."""
        return self.is_enabled() if enabled is None else self.set_enabled(enabled)

    def sync_with_system(self, enabled: bool | None = None) -> bool:
        return self.sync(enabled)

    # ── Windows ──────────────────────────────────────────────────────────

    def _build_command_windows(self) -> str:
        parts = [f'"{self._app_path}"'] + [
            f'"{a}"' for a in self._app_args
        ]
        return " ".join(parts)

    def _is_enabled_windows(self) -> bool:
        import winreg  # type: ignore[import-not-found]

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows"
                r"\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            try:
                winreg.QueryValueEx(
                    key, self._app_name
                )
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _enable_windows(self) -> bool:
        import winreg  # type: ignore[import-not-found]

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows"
                r"\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(
                key,
                self._app_name,
                0,
                winreg.REG_SZ,
                self._build_command_windows(),
            )
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def _disable_windows(self) -> bool:
        import winreg  # type: ignore[import-not-found]

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows"
                r"\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.DeleteValue(
                    key, self._app_name
                )
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    # ── macOS ─────────────────────────────────────────────────────────────

    def _plist_path(self) -> Path:
        return (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / f"com.{self._app_name.lower()}.plist"
        )

    def _is_enabled_macos(self) -> bool:
        return self._plist_path().exists()

    def _enable_macos(self) -> bool:
        path = self._plist_path()
        path.parent.mkdir(
            parents=True, exist_ok=True
        )
        args_xml = "\n".join(
            f"        <string>{a}</string>"
            for a in [self._app_path]
            + self._app_args
        )
        content = (
            '<?xml version="1.0" '
            'encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC '
            '"-//Apple//DTD PLIST 1.0//EN"\n'
            '  "http://www.apple.com/DTDs/'
            'PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n<dict>\n'
            "    <key>Label</key>\n"
            f"    <string>"
            f"com.{self._app_name.lower()}"
            f"</string>\n"
            "    <key>ProgramArguments</key>\n"
            f"    <array>\n{args_xml}\n"
            "    </array>\n"
            "    <key>RunAtLoad</key>\n"
            "    <true/>\n"
            "    <key>KeepAlive</key>\n"
            "    <false/>\n"
            "</dict>\n</plist>"
        )
        path.write_text(content, encoding="utf-8")
        return True

    def _disable_macos(self) -> bool:
        p = self._plist_path()
        if p.exists():
            p.unlink()
        return True

    # ── Linux ─────────────────────────────────────────────────────────────

    def _desktop_path(self) -> Path:
        config = Path(
            os.environ.get(
                "XDG_CONFIG_HOME",
                str(Path.home() / ".config"),
            )
        )
        return (
            config
            / "autostart"
            / f"{self._app_name.lower()}.desktop"
        )

    def _is_enabled_linux(self) -> bool:
        desktop = self._desktop_path()
        if not desktop.exists():
            return False
        try:
            content = desktop.read_text(
                encoding="utf-8"
            )
            if "Hidden=true" in content:
                return False
            if (
                "X-GNOME-Autostart-enabled=false"
                in content
            ):
                return False
        except Exception:
            _logger.debug("[SETTINGS] Kunde inte läsa .desktop-fil", exc_info=True)
        return True

    def _enable_linux(self) -> bool:
        desktop = self._desktop_path()
        desktop.parent.mkdir(
            parents=True, exist_ok=True
        )
        exec_parts = [self._app_path] + (
            self._app_args
        )
        exec_line = " ".join(
            f'"{p}"' for p in exec_parts
        )
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={self._app_name}\n"
            f"Exec={exec_line}\n"
            f"Icon={self._app_name.lower()}\n"
            "Comment=IntygsDrive\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            "X-GNOME-Autostart-Delay=2\n"
        )
        desktop.write_text(
            content, encoding="utf-8"
        )
        desktop.chmod(0o755)
        return True

    def _disable_linux(self) -> bool:
        desktop = self._desktop_path()
        if desktop.exists():
            desktop.unlink()
        return True


# ══════════════════════════════════════════════════════════════════════════════
# THEME MANAGER (COLORS-001)
# ══════════════════════════════════════════════════════════════════════════════


class ThemeManager:
    """Hanterar tema med PySide6 QPalette.

    COLORS-001: ThemeManager.PALETTES är den ENDA
    platsen där färgvärden definieras.
    """

    PALETTES: dict[str, dict[str, str]] = {
        "light": {
            "bg": "#ffffff",
            "fg": "#222222",
            "text": "#222222",
            "accent": "#2980b9",
            "accent_hover": "#3498db",
            "accent_fg": "#ffffff",
            "secondary_bg": "#f5f5f5",
            "tertiary_bg": "#e8e8e8",
            "border": "#d0d0d0",
            "border_focus": "#80bdff",
            "success": "#27ae60",
            "success_bg": "#eafaf1",
            "warning": "#f39c12",
            "warning_bg": "#fef9e7",
            "error": "#e74c3c",
            "error_bg": "#fdedec",
            "info": "#2980b9",
            "info_bg": "#eaf4fb",
            "disabled": "#aaaaaa",
            "disabled_bg": "#f0f0f0",
            "link": "#2980b9",
            "card_bg": "#ffffff",
            "input_bg": "#ffffff",
            "selection_bg": "#2980b9",
            "selection_fg": "#ffffff",
            "placeholder": "#aaaaaa",
            "tooltip_bg": "#fffde7",
            "tooltip_fg": "#333333",
        },
        "dark": {
            "bg": "#1e1e1e",
            "fg": "#e4e4e4",
            "text": "#e4e4e4",
            "accent": "#3498db",
            "accent_hover": "#5dade2",
            "accent_fg": "#ffffff",
            "secondary_bg": "#252526",
            "tertiary_bg": "#2d2d30",
            "border": "#3c3c3c",
            "border_focus": "#007acc",
            "success": "#4ec9b0",
            "success_bg": "#1a3a2e",
            "warning": "#dcdcaa",
            "warning_bg": "#2e2a1a",
            "error": "#f44747",
            "error_bg": "#2e1a1a",
            "info": "#9cdcfe",
            "info_bg": "#1a2e3a",
            "disabled": "#666666",
            "disabled_bg": "#2d2d30",
            "link": "#4fc3f7",
            "card_bg": "#252526",
            "input_bg": "#3c3c3c",
            "selection_bg": "#264f78",
            "selection_fg": "#ffffff",
            "placeholder": "#666666",
            "tooltip_bg": "#3c3c3c",
            "tooltip_fg": "#e4e4e4",
        },
    }

    _DETECT_TTL: float = 30.0  # Cache TTL i sekunder

    def __init__(self) -> None:
        self._current_theme: Theme = Theme.SYSTEM
        self._resolved: str = "light"
        self._observers: list[
            Callable[[str], None]
        ] = []
        self._observer_lock = threading.RLock()
        self._detect_cache: str | None = None
        self._detect_time: float = 0.0

    @property
    def current_theme(self) -> Theme:
        return self._current_theme

    @property
    def resolved_theme(self) -> str:
        return self._resolved

    @property
    def is_dark(self) -> bool:
        return self._resolved == "dark"

    @property
    def colors(self) -> dict[str, str]:
        return self.PALETTES[self._resolved]

    # ── Observers ─────────────────────────────────────────────────────────

    def add_observer(
        self, callback: Callable[[str], None]
    ) -> None:
        with self._observer_lock:
            if callback not in self._observers:
                self._observers.append(callback)

    def remove_observer(
        self, callback: Callable[[str], None]
    ) -> None:
        with self._observer_lock:
            if callback in self._observers:
                self._observers.remove(callback)

    def _notify(self) -> None:
        with self._observer_lock:
            observers = tuple(self._observers)
        for callback in observers:
            try:
                callback(self._resolved)
            except Exception:
                _logger.exception("[SETTINGS] Tema-observer misslyckades")

    # ── Systemtema-detektering (cachad, TTL 30s) ─────────────────────────

    def detect_system_theme(self) -> str:
        """Detektera systemtema (cachad i 30s).

        9.1.0: cache-träffar och färska detekteringar loggas strukturerat
        (`st.theme_detected`) så att startup-kostnaden syns i loggen.
        """
        now = time.monotonic()
        if (
            self._detect_cache is not None
            and (now - self._detect_time)
            < self._DETECT_TTL
        ):
            return self._detect_cache
        started = time.perf_counter()
        try:
            if PlatformHelper.is_windows():
                result = self._detect_windows()
            elif PlatformHelper.is_macos():
                result = self._detect_macos()
            else:
                result = self._detect_linux()
        except Exception:
            _logger.debug("[SETTINGS] Systemtema-detektering misslyckades", exc_info=True)
            result = "light"
        self._detect_cache = result
        self._detect_time = now
        _logger.debug(
            "[SETTINGS] Systemtema detekterat: %s", result,
            extra={"event": "st.theme_detected",
                   "ctx": {"resolved": result,
                           "ms": round((time.perf_counter() - started) * 1000, 1)}},
        )
        return result

    def _detect_windows(self) -> str:
        try:
            import winreg  # type: ignore[import-not-found]

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows"
                r"\CurrentVersion\Themes"
                r"\Personalize",
            )
            value, _ = winreg.QueryValueEx(
                key, "AppsUseLightTheme"
            )
            winreg.CloseKey(key)
            return "light" if value == 1 else "dark"
        except Exception:
            return "light"

    def _detect_macos(self) -> str:
        try:
            result = subprocess.run(
                [
                    "defaults", "read", "-g",
                    "AppleInterfaceStyle",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return (
                "dark"
                if "Dark" in result.stdout
                else "light"
            )
        except Exception:
            return "light"

    def _detect_linux(self) -> str:
        for cmd in (
            [
                "gsettings", "get",
                "org.gnome.desktop.interface",
                "color-scheme",
            ],
            [
                "gsettings", "get",
                "org.gnome.desktop.interface",
                "gtk-theme",
            ],
        ):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if "dark" in result.stdout.lower():
                    return "dark"
            except Exception:
                pass
        try:
            kde = (
                Path.home()
                / ".config"
                / "kdeglobals"
            )
            if kde.exists():
                content = kde.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )
                if "dark" in content.lower():
                    return "dark"
        except Exception:
            _logger.debug("[SETTINGS] Kunde inte läsa kdeglobals", exc_info=True)
        return "light"

    # ── Tema-applicering ──────────────────────────────────────────────────

    def apply_theme(self, theme: Theme) -> None:
        """Applicera tema via QPalette (COLORS-001).

        9.1.0: strukturerad `st.theme_applied`-logg med ms för AI-felsökning.
        """
        started = time.perf_counter()
        self._current_theme = theme
        self._resolved = (
            self.detect_system_theme()
            if theme == Theme.SYSTEM
            else theme.value
        )
        self._apply_qpalette()
        self._notify()
        _logger.info(
            "[SETTINGS] Tema applicerat: %s (löst: %s)",
            theme.value, self._resolved,
            extra={"event": "st.theme_applied",
                   "ctx": {"theme": theme.value, "resolved": self._resolved,
                           "ms": round((time.perf_counter() - started) * 1000, 1)}},
        )

    def _apply_qpalette(self) -> None:
        """Bygg och sätt QPalette på QApplication."""
        try:
            from PySide6.QtGui import (
                QColor,
                QPalette,
            )
            from PySide6.QtWidgets import (
                QApplication,
            )

            app = QApplication.instance()
            if not app:
                return
            c = self.colors
            palette = QPalette()

            if self._resolved == "dark":
                palette.setColor(
                    QPalette.ColorRole.Window,
                    QColor(c["bg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.WindowText,
                    QColor(c["fg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.Base,
                    QColor(c["input_bg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.AlternateBase,
                    QColor(c["secondary_bg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.ToolTipBase,
                    QColor(c["tooltip_bg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.ToolTipText,
                    QColor(c["tooltip_fg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.Text,
                    QColor(c["text"]),
                )
                palette.setColor(
                    QPalette.ColorRole.Button,
                    QColor(c["secondary_bg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.ButtonText,
                    QColor(c["fg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.BrightText,
                    QColor("#ffffff"),
                )
                palette.setColor(
                    QPalette.ColorRole.Link,
                    QColor(c["link"]),
                )
                palette.setColor(
                    QPalette.ColorRole.Highlight,
                    QColor(c["selection_bg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.HighlightedText,
                    QColor(c["selection_fg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.PlaceholderText,
                    QColor(c["placeholder"]),
                )
                # Disabled-grupp
                for role, color_key in (
                    (
                        QPalette.ColorRole.Text,
                        "disabled",
                    ),
                    (
                        QPalette.ColorRole.ButtonText,
                        "disabled",
                    ),
                    (
                        QPalette.ColorRole.WindowText,
                        "disabled",
                    ),
                ):
                    palette.setColor(
                        QPalette.ColorGroup.Disabled,
                        role,
                        QColor(c[color_key]),
                    )
            else:
                palette = (
                    app.style().standardPalette()
                )
                palette.setColor(
                    QPalette.ColorRole.Highlight,
                    QColor(c["selection_bg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.HighlightedText,
                    QColor(c["selection_fg"]),
                )
                palette.setColor(
                    QPalette.ColorRole.Link,
                    QColor(c["link"]),
                )
                palette.setColor(
                    QPalette.ColorRole.PlaceholderText,
                    QColor(c["placeholder"]),
                )

            app.setPalette(palette)

            # Stil per OS
            try:
                from PySide6.QtWidgets import (
                    QStyleFactory,
                )

                available = QStyleFactory.keys()
                if self._resolved == "dark":
                    if "Fusion" in available:
                        app.setStyle("Fusion")
                else:
                    if (
                        PlatformHelper.is_windows()
                        and "WindowsVista"
                        in available
                    ):
                        app.setStyle("WindowsVista")
                    elif (
                        PlatformHelper.is_macos()
                        and "macOS" in available
                    ):
                        app.setStyle("macOS")
                    elif "Fusion" in available:
                        app.setStyle("Fusion")
            except Exception:
                _logger.debug("[SETTINGS] Kunde inte sätta QStyle", exc_info=True)
        except ImportError:
            _logger.debug("[SETTINGS] PySide6 saknas — QPalette hoppas över")
        except Exception:
            _logger.exception("[SETTINGS] Kunde inte applicera QPalette")

    def refresh_system_theme(self) -> bool:
        """Uppdatera om systemtemat ändrats."""
        if self._current_theme != Theme.SYSTEM:
            return False
        # Tvinga ny detektering
        self._detect_cache = None
        new = self.detect_system_theme()
        if new != self._resolved:
            self.apply_theme(Theme.SYSTEM)
            return True
        return False

    def get_color(
        self, key: str, default: str = "#000000"
    ) -> str:
        """Returnera färg från aktuellt tema."""
        return self.colors.get(key, default)


# ══════════════════════════════════════════════════════════════════════════════
# SHORTCUT MANAGER
# ══════════════════════════════════════════════════════════════════════════════


class ShortcutManager:
    """Hanterar tangentbordsgenvägar."""

    DEFAULT_SHORTCUTS: list[Shortcut] = [
        Shortcut(
            "generate_pdf", "Generera PDF",
            "Ctrl+G", category="Intyg", icon="📄",
            description="Generera PDF av intyget",
        ),
        Shortcut(
            "new_certificate", "Nytt intyg",
            "Ctrl+N", category="Intyg", icon="📝",
            description="Skapa nytt intyg",
        ),
        Shortcut(
            "clear_fields", "Rensa fält",
            "Ctrl+L", category="Intyg", icon="🗑️",
            description="Rensa inmatningsfält",
        ),
        Shortcut(
            "import_certificate", "Importera",
            "Ctrl+I", category="Intyg", icon="📥",
            description="Importera intyg",
        ),
        Shortcut(
            "open_output", "Öppna output",
            "Ctrl+O", category="Navigering",
            icon="📂",
            description="Öppna output-mapp",
        ),
        Shortcut(
            "open_templates", "Öppna mallar",
            "Ctrl+T", category="Navigering",
            icon="📁",
            description="Öppna mall-mapp",
        ),
        Shortcut(
            "settings", "Inställningar",
            "Ctrl+,", category="Navigering",
            icon="⚙️",
            description="Öppna inställningar",
        ),
        Shortcut(
            "refresh", "Uppdatera", "F5",
            category="Allmänt", icon="🔄",
            description="Uppdatera vyn",
        ),
        Shortcut(
            "quit", "Avsluta", "Ctrl+Q",
            category="Allmänt", icon="🚪",
            description="Avsluta till tray",
        ),
        Shortcut(
            "help", "Hjälp", "F1",
            category="Allmänt", icon="❓",
            description="Visa hjälp",
        ),
        Shortcut(
            "backup", "Backup", "Ctrl+B",
            category="Verktyg", icon="💾",
            description="Skapa backup",
        ),
        Shortcut(
            "minimize_tray", "Minimera",
            "Ctrl+M", category="Fönster",
            icon="📥",
            description="Minimera till tray",
        ),
    ]

    def __init__(
        self, settings_provider: Any = None
    ) -> None:
        self._settings: Any = settings_provider
        self._shortcuts: dict[str, Shortcut] = {}
        self._load_shortcuts()

    def _load_shortcuts(self) -> None:
        saved: dict[str, str] = {}
        if self._settings:
            try:
                saved = (
                    self._settings.get(
                        "shortcuts", {}
                    )
                    or {}
                )
            except Exception:
                _logger.debug("[SETTINGS] Kunde inte läsa sparade genvägar", exc_info=True)
        for s in self.DEFAULT_SHORTCUTS:
            current = saved.get(
                s.action, s.default_key
            )
            self._shortcuts[s.action] = Shortcut(
                action=s.action,
                label=s.label,
                default_key=s.default_key,
                current_key=current,
                category=s.category,
                description=s.description,
                icon=s.icon,
                enabled=s.enabled,
            )

    def _save_shortcuts(self) -> None:
        if self._settings:
            try:
                modified = {
                    action: s.current_key
                    for action, s in (
                        self._shortcuts.items()
                    )
                    if s.is_modified
                }
                self._settings.set(
                    "shortcuts", modified
                )
            except Exception:
                _logger.debug("[SETTINGS] Kunde inte spara genvägar", exc_info=True)

    def get_shortcut(
        self, action: str
    ) -> Shortcut | None:
        return self._shortcuts.get(action)

    def get_all(self) -> list[Shortcut]:
        return list(self._shortcuts.values())

    def get_by_category(
        self,
    ) -> dict[str, list[Shortcut]]:
        cats: dict[str, list[Shortcut]] = {}
        for s in self._shortcuts.values():
            cats.setdefault(
                s.category, []
            ).append(s)
        for lst in cats.values():
            lst.sort(key=lambda x: x.label)
        return cats

    def set_shortcut(
        self, action: str, key: str
    ) -> tuple[bool, str]:
        if action not in self._shortcuts:
            return False, f"Okänd: {action}"
        key = key.strip()
        if not key:
            return False, "Kan inte vara tom"
        key_lower = key.lower()
        for other_action, other in (
            self._shortcuts.items()
        ):
            if (
                other_action != action
                and other.current_key.lower()
                == key_lower
            ):
                return False, (
                    f"Används av '{other.label}'"
                )
        self._shortcuts[action].current_key = key
        self._save_shortcuts()
        return True, ""

    def reset_shortcut(
        self, action: str
    ) -> bool:
        if action in self._shortcuts:
            self._shortcuts[action].reset()
            self._save_shortcuts()
            return True
        return False

    def reset_all(self) -> None:
        for s in self._shortcuts.values():
            s.reset()
        self._save_shortcuts()

    def get_platform_modifier(self) -> str:
        return (
            "Cmd"
            if PlatformHelper.is_macos()
            else "Ctrl"
        )

    def get_display_string(
        self, action: str
    ) -> str:
        s = self.get_shortcut(action)
        if not s:
            return ""
        key = s.current_key
        if PlatformHelper.is_macos():
            key = (
                key.replace("Ctrl+", "⌘")
                .replace("Alt+", "⌥")
                .replace("Shift+", "⇧")
                .replace("Cmd+", "⌘")
            )
        return key


# ══════════════════════════════════════════════════════════════════════════════
# EXPORTS
# ══════════════════════════════════════════════════════════════════════════════

__all__: list[str] = [
    "__version__",
    "__author__",
    "COPYRIGHT",
    "PlatformHelper",
    "Theme",
    "Language",
    "HealthStatus",
    "SettingSection",
    "SettingDefinition",
    "Profile",
    "HealthCheck",
    "Shortcut",
    "TabInfo",
    "AutostartManager",
    "ThemeManager",
    "ShortcutManager",
]
