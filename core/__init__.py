from __future__ import annotations

# core/__init__.py
# Sökväg: Intygsgenerator/core/__init__.py
# Intygsgenerator — Kärnarkitektur: EventBus, IPlugin, AppContext,
# PluginManager, TabManager samt re-export av core/config.py.
#
# Appversion: 0.0.1
# © Rickard Längkvist 2025–2026
#
# Beroenden: Python 3.12+, core/config.py mjukt.

import fnmatch
import importlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any

__version__: str = "0.0.1"
__author__: str = "Rickard Längkvist"

APP_NAME: str = "Intygsgenerator"
APP_DISPLAY_NAME: str = "IntygsDrive"
VERSION: str = __version__

FOLDER_PLUGINS: str = "plugins"
FOLDER_TEMPLATES: str = "Mallar"
FOLDER_OUTPUT: str = "Out"
FOLDER_CONFIG: str = "js"
FOLDER_ICONS: str = "Icon"
FOLDER_LOGS: str = "logs"
FOLDER_BACKUP: str = "Backup"
FOLDER_SENT: str = "Skickade"

SESSION_ID: str = uuid.uuid4().hex[:12]

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_logger = logging.getLogger(__name__)


def _evt_core(event: str, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
    """10.8.0: fail-safe strukturerad loggpost (core.*) — central loggning av
    allt som händer i kärnan, AI-läsbar i JSONL."""
    try:
        _logger.log(level, msg, extra={"event": event, "ctx": ctx or None})
    except Exception:
        pass


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    year = datetime.now().year
    return (
        f"© Rickard Längkvist {_START_YEAR}–{year}"
        if year > _START_YEAR
        else f"© Rickard Längkvist {_START_YEAR}"
    )


# ─── Config-import med fallback ─────────────────────────────────────────────

CONFIG_AVAILABLE: bool = False

try:
    from .config import (
        AutostartManager,
        ConfigManager,
        IconManager,
        SettingsManager,
        TrayConfig,
        WindowManager,
    )

    CONFIG_AVAILABLE = True
except Exception as _config_exc:
    _CONFIG_ERROR_MESSAGE: str = f"{type(_config_exc).__name__}: {_config_exc}"

    class ConfigManager:
        """Fallback ConfigManager när core/config.py inte kan laddas."""

        _instances: dict[Path, ConfigManager] = {}
        _instances_lock = threading.RLock()

        def __init__(
            self,
            path: Any = None,
            auto_save: bool = True,
            **kwargs: Any,
        ) -> None:
            self._path = Path(path) if path else None
            self._auto_save = auto_save
            self._data: dict[str, Any] = {}
            self._observers: dict[str, list[Callable[[str, Any, Any], None]]] = {}
            self._lock = threading.RLock()

            if self._path and self._path.exists():
                with suppress(Exception):
                    raw = self._path.read_text(encoding="utf-8")
                    loaded = json.loads(raw)
                    if isinstance(loaded, dict):
                        self._data = loaded

        @classmethod
        def get_instance(cls, path: Any = None) -> ConfigManager:
            key = Path(path or Path(FOLDER_CONFIG) / "settings.json").resolve()
            with cls._instances_lock:
                if key not in cls._instances:
                    cls._instances[key] = cls(key)
                return cls._instances[key]

        def get(self, key: str, default: Any = None) -> Any:
            with self._lock:
                current: Any = self._data
                for part in key.split("."):
                    if not isinstance(current, dict):
                        return default
                    if part not in current:
                        return default
                    current = current[part]
                return current

        def set(self, key: str, value: Any, **kwargs: Any) -> None:
            old_value = self.get(key)
            with self._lock:
                parts = key.split(".")
                current = self._data
                for part in parts[:-1]:
                    if part not in current or not isinstance(current[part], dict):
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = value

            self._notify_observers(key, value, old_value)
            if self._auto_save:
                self._save()

        def delete(self, key: str) -> None:
            old_value = self.get(key)
            with self._lock:
                parts = key.split(".")
                current = self._data
                for part in parts[:-1]:
                    if not isinstance(current, dict):
                        return
                    current = current.get(part, {})
                if isinstance(current, dict):
                    current.pop(parts[-1], None)

            self._notify_observers(key, None, old_value)
            if self._auto_save:
                self._save()

        def has(self, key: str) -> bool:
            sentinel = object()
            return self.get(key, sentinel) is not sentinel

        def exists(self, key: str) -> bool:
            return self.has(key)

        def keys(self) -> list[str]:
            with self._lock:
                return list(self._data.keys())

        def get_or_set(self, key: str, default: Any) -> Any:
            if not self.has(key):
                self.set(key, default)
            return self.get(key, default)

        def get_all(self) -> dict[str, Any]:
            with self._lock:
                return json.loads(
                    json.dumps(self._data, ensure_ascii=False, default=str)
                )

        def set_all(self, data: dict[str, Any]) -> None:
            with self._lock:
                self._data = dict(data)
            if self._auto_save:
                self._save()

        def merge(self, data: dict[str, Any]) -> None:
            with self._lock:
                self._deep_merge(self._data, data)
            if self._auto_save:
                self._save()

        def set_defaults(self, defaults: dict[str, Any]) -> None:
            self.ensure_defaults(defaults)

        def ensure_defaults(self, defaults: dict[str, Any]) -> None:
            changed = False
            for key, value in defaults.items():
                if not self.has(key):
                    self.set(key, value)
                    changed = True
            if changed and self._auto_save:
                self._save()

        def observe(
            self,
            key: str,
            callback: Callable[[str, Any, Any], None],
        ) -> Callable[[], None]:
            with self._lock:
                self._observers.setdefault(key, []).append(callback)

            def unsubscribe() -> None:
                self.unobserve(key, callback)

            return unsubscribe

        def unobserve(
            self,
            key: str,
            callback: Callable[[str, Any, Any], None],
        ) -> None:
            with self._lock:
                callbacks = self._observers.get(key, [])
                with suppress(ValueError):
                    callbacks.remove(callback)
                if not callbacks and key in self._observers:
                    del self._observers[key]

        def increment(self, key: str, amount: int = 1) -> int:
            value = int(self.get(key, 0) or 0) + amount
            self.set(key, value)
            return value

        def decrement(self, key: str, amount: int = 1) -> int:
            return self.increment(key, -amount)

        def toggle(self, key: str) -> bool:
            value = not bool(self.get(key, False))
            self.set(key, value)
            return value

        def append_to_list(self, key: str, value: Any) -> None:
            values = self.get(key, [])
            if not isinstance(values, list):
                values = []
            values.append(value)
            self.set(key, values)

        def remove_from_list(self, key: str, value: Any) -> None:
            values = self.get(key, [])
            if not isinstance(values, list):
                return
            with suppress(ValueError):
                values.remove(value)
            self.set(key, values)

        def export_to_file(self, path: Any) -> bool:
            try:
                target = Path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(
                        self.get_all(), indent=2, ensure_ascii=False
                    ),
                    encoding="utf-8",
                )
                return True
            except Exception:
                _logger.exception("Fallback ConfigManager export failed")
                return False

        def import_from_file(self, path: Any) -> bool:
            try:
                source = Path(path)
                data = json.loads(source.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return False
                self.set_all(data)
                return True
            except Exception:
                _logger.exception("Fallback ConfigManager import failed")
                return False

        def save(self, force: bool = False) -> bool:
            return self._save()

        def reload(self) -> bool:
            if not self._path or not self._path.exists():
                return False
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    with self._lock:
                        self._data = data
                    return True
            except Exception:
                _logger.exception("Fallback ConfigManager reload failed")
            return False

        def close(self) -> None:
            self._save()

        def _save(self) -> bool:
            if not self._path:
                return False
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_name(f"{self._path.name}.tmp")
                tmp.write_text(
                    json.dumps(
                        self._data, indent=2, ensure_ascii=False, default=str
                    ),
                    encoding="utf-8",
                )
                tmp.replace(self._path)
                return True
            except Exception:
                _logger.exception("Fallback ConfigManager save failed")
                return False

        def _notify_observers(
            self, key: str, value: Any, old_value: Any
        ) -> None:
            callbacks: list[Callable[[str, Any, Any], None]] = []
            with self._lock:
                callbacks.extend(self._observers.get(key, []))
                callbacks.extend(self._observers.get("*", []))

            for callback in callbacks:
                try:
                    callback(key, value, old_value)
                except Exception:
                    _logger.exception("Fallback ConfigManager observer failed")

        @staticmethod
        def _deep_merge(
            target: dict[str, Any], source: dict[str, Any]
        ) -> None:
            for key, value in source.items():
                if isinstance(value, dict) and isinstance(
                    target.get(key), dict
                ):
                    ConfigManager._deep_merge(target[key], value)
                else:
                    target[key] = value

        def __contains__(self, key: str) -> bool:
            return self.has(key)

    SettingsManager = ConfigManager

    class AutostartManager:
        """Fallback AutostartManager."""

        def __init__(
            self,
            app_name: str = APP_NAME,
            settings: Any = None,
            **kwargs: Any,
        ) -> None:
            self.app_name = app_name
            self._settings = settings

        def is_enabled(self) -> bool:
            if self._settings:
                return bool(
                    self._settings.get("app.start_with_os", False)
                )
            return False

        def enable(self) -> bool:
            if self._settings:
                self._settings.set("app.start_with_os", True)
            return False

        def disable(self) -> bool:
            if self._settings:
                self._settings.set("app.start_with_os", False)
            return True

        def set_enabled(self, value: bool) -> bool:
            return self.enable() if value else self.disable()

        def toggle(self) -> bool:
            return self.set_enabled(not self.is_enabled())

        def sync_with_system(self) -> None:
            return None

    class IconManager:
        """Fallback IconManager."""

        def __init__(self, base_path: Any = None) -> None:
            self.icon_folder = Path(base_path or ".") / FOLDER_ICONS

        def set_window_icon(self, window: Any) -> bool:
            icon = self.get_qicon()
            if icon is not None and hasattr(window, "setWindowIcon"):
                with suppress(Exception):
                    window.setWindowIcon(icon)
                return True
            return False

        def get_qicon(self, size: tuple[int, int] = (64, 64)) -> Any:
            with suppress(Exception):
                from PySide6.QtGui import QIcon

                icon_path = self.find_icon()
                return QIcon(str(icon_path)) if icon_path else QIcon()
            return None

        def get_pil_image(self, size: tuple[int, int] = (64, 64)) -> Any:
            return None

        def get_tray_icon(self, size: tuple[int, int] = (64, 64)) -> Any:
            return self.get_pil_image(size)

        def get_icon(self) -> Any:
            return self.get_qicon()

        def get_icon_path(self) -> Path | None:
            return self.find_icon()

        def has_custom_icon(self) -> bool:
            return self.find_icon() is not None

        def find_icon(self, refresh: bool = False) -> Path | None:
            for filename in ("icon.png", "icon.ico", "app.png", "icon.icns"):
                path = self.icon_folder / filename
                if path.exists():
                    return path
            return None

        def clear_cache(self) -> None:
            return None

        def get_icon_info(self) -> dict[str, Any]:
            icon_path = self.find_icon()
            return {
                "has_custom_icon": icon_path is not None,
                "path": str(icon_path) if icon_path else "",
            }

    class WindowManager:
        """Fallback WindowManager."""

        def __init__(self, window: Any = None, config: Any = None) -> None:
            self.window = window
            self.config = config

        def save_geometry(self) -> None:
            return None

        def restore_geometry(self) -> None:
            return None

        def center_window(self) -> None:
            return None

        def setup_window(self) -> None:
            return None

    @dataclass(slots=True)
    class TrayConfig:
        """Fallback TrayConfig."""

        start_minimized: bool = True
        minimize_to_tray: bool = True
        close_to_tray: bool = True
        single_click_open: bool = True
        show_notifications: bool = True
        start_with_os: bool = False

        @classmethod
        def from_config(cls, config: Any) -> TrayConfig:
            if config is None:
                return cls()
            return cls(
                start_minimized=bool(
                    config.get("app.start_minimized", True)
                ),
                minimize_to_tray=bool(
                    config.get("app.minimize_to_tray", True)
                ),
                close_to_tray=bool(
                    config.get("app.close_to_tray", True)
                ),
                single_click_open=bool(
                    config.get("app.single_click_open", True)
                ),
                show_notifications=bool(
                    config.get("app.show_notifications", True)
                ),
                start_with_os=bool(
                    config.get("app.start_with_os", False)
                ),
            )

        def save_to_config(self, config: Any) -> None:
            with suppress(Exception):
                config.set("app.start_minimized", self.start_minimized)
                config.set("app.minimize_to_tray", self.minimize_to_tray)
                config.set("app.close_to_tray", self.close_to_tray)
                config.set(
                    "app.single_click_open", self.single_click_open
                )
                config.set(
                    "app.show_notifications", self.show_notifications
                )
                config.set("app.start_with_os", self.start_with_os)

        def should_start_hidden(self) -> bool:
            return self.start_minimized

        def should_hide_on_close(self) -> bool:
            return self.close_to_tray

        def to_dict(self) -> dict[str, Any]:
            return {
                "start_minimized": self.start_minimized,
                "minimize_to_tray": self.minimize_to_tray,
                "close_to_tray": self.close_to_tray,
                "single_click_open": self.single_click_open,
                "show_notifications": self.show_notifications,
                "start_with_os": self.start_with_os,
            }


# ─── Infrastructure-import ──────────────────────────────────────────────────

INFRASTRUCTURE_AVAILABLE: bool = False
try:
    from .infrastructure import (
        AtomicFileWriter,
        PlatformHelper as _InfraPlatformHelper,
        clear_pycache,
    )

    INFRASTRUCTURE_AVAILABLE = True
except Exception:
    INFRASTRUCTURE_AVAILABLE = False

    def clear_pycache(root, *, dry_run: bool = False, log=None) -> dict[str, int]:
        """Fallback när infrastructure.py saknas: gör ingenting, returnerar nollor."""
        return {"dirs": 0, "files": 0, "bytes": 0, "errors": 0}


# ─── Platform ───────────────────────────────────────────────────────────────


class Platform(Enum):
    """Plattformsdetektering."""

    WINDOWS = auto()
    MACOS = auto()
    LINUX = auto()
    UNKNOWN = auto()

    @classmethod
    def current(cls) -> Platform:
        if sys.platform in {"win32", "cygwin"}:
            return cls.WINDOWS
        if sys.platform == "darwin":
            return cls.MACOS
        if sys.platform.startswith("linux"):
            return cls.LINUX
        return cls.UNKNOWN

    @property
    def is_windows(self) -> bool:
        return self == Platform.WINDOWS

    @property
    def is_macos(self) -> bool:
        return self == Platform.MACOS

    @property
    def is_linux(self) -> bool:
        return self == Platform.LINUX

    @property
    def display_name(self) -> str:
        names = {
            Platform.WINDOWS: "Windows",
            Platform.MACOS: "macOS",
            Platform.LINUX: "Linux",
            Platform.UNKNOWN: "Unknown",
        }
        return names.get(self, "Unknown")


CURRENT_PLATFORM: Platform = Platform.current()


class PlatformHelper:
    """Plattformsoberoende hjälpfunktioner."""

    @staticmethod
    def is_windows() -> bool:
        return CURRENT_PLATFORM.is_windows

    @staticmethod
    def is_macos() -> bool:
        return CURRENT_PLATFORM.is_macos

    @staticmethod
    def is_linux() -> bool:
        return CURRENT_PLATFORM.is_linux

    @staticmethod
    def get_platform_name() -> str:
        return CURRENT_PLATFORM.display_name

    @staticmethod
    def open_file(path: Any) -> bool:
        """Öppna fil eller mapp utan att blockera UI-tråden (§18)."""
        target = Path(path)
        if not target.exists():
            return False

        try:
            if CURRENT_PLATFORM.is_windows:
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                command = (
                    ["open", str(target)]
                    if CURRENT_PLATFORM.is_macos
                    else ["xdg-open", str(target)]
                )
                subprocess.Popen(
                    command,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return True
        except Exception:
            _logger.exception("Could not open file: %s", target)
            return False

    @staticmethod
    def open_folder(path: Any) -> bool:
        target = Path(path)
        if target.is_file():
            target = target.parent
        return PlatformHelper.open_file(target)


# ─── EventBus ───────────────────────────────────────────────────────────────


class EventBus:
    """Thread-safe EventBus med wildcard-stöd.

    10.8.0: strukturerade `core.*`-events (event_error/event_slow) och
    `stats()` för AI-diagnostik; inga tysta `print_exc()` (osynliga under
    pythonw).
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Any]]] = {}
        self._lock = threading.RLock()
        self._logger = logging.getLogger(f"{APP_NAME}.EventBus")
        self._active = True
        self._emit_counts: dict[str, int] = {}
        self._error_counts: dict[str, int] = {}
        self._slow_counts: dict[str, int] = {}
        self._stats_lock = threading.Lock()

    def subscribe(
        self,
        event: str,
        handler: Callable[..., Any],
    ) -> Callable[[], None]:
        """Prenumerera på event och returnera unsubscribe-funktion."""
        if not isinstance(event, str) or not event:
            raise ValueError("event måste vara en icke-tom sträng")
        if not callable(handler):
            raise TypeError("handler måste vara callable")

        with self._lock:
            self._handlers.setdefault(event, []).append(handler)

        def unsubscribe() -> None:
            self.unsubscribe(event, handler)

        return unsubscribe

    def unsubscribe(self, event: str, handler: Callable[..., Any]) -> None:
        """Avregistrera event-handler."""
        with self._lock:
            handlers = self._handlers.get(event)
            if not handlers:
                return
            with suppress(ValueError):
                handlers.remove(handler)
            if not handlers:
                self._handlers.pop(event, None)

    def emit(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> list[Any]:
        """Emittera event till alla prenumeranter inklusive wildcard."""
        if not self._active:
            return []

        payload = data if isinstance(data, dict) else {}
        handlers = self._collect_handlers(event)
        results: list[Any] = []
        with self._stats_lock:
            self._emit_counts[event] = self._emit_counts.get(event, 0) + 1

        for handler in handlers:
            start = time.perf_counter()
            try:
                result = handler(payload)
                if result is not None:
                    results.append(result)
            except Exception as exc:
                with self._stats_lock:
                    self._error_counts[event] = self._error_counts.get(event, 0) + 1
                self._logger.exception(
                    "Event handler failed for %s", event
                )
                _evt_core(
                    "core.event_error",
                    f"Event-handler kraschade för {event}: {type(exc).__name__}: {exc}",
                    logging.ERROR,
                    event_topic=event,
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                if duration_ms > 50:
                    with self._stats_lock:
                        self._slow_counts[event] = self._slow_counts.get(event, 0) + 1
                    self._logger.warning(
                        "Slow event handler for %s: %.1fms",
                        event,
                        duration_ms,
                    )
                    _evt_core(
                        "core.event_slow",
                        f"Långsam handler för {event}: {duration_ms:.1f} ms",
                        logging.WARNING,
                        event_topic=event,
                        ms=round(duration_ms, 1),
                    )

        return results

    def stats(self) -> dict[str, Any]:
        """10.8.0: EventBus-hälsa för AI-diagnostik."""
        with self._stats_lock:
            return {
                "emits": dict(sorted(self._emit_counts.items(), key=lambda kv: -kv[1])[:25]),
                "errors": dict(self._error_counts),
                "slow": dict(self._slow_counts),
                "topics": len(self._handlers),
            }

    def _collect_handlers(
        self, event: str
    ) -> list[Callable[..., Any]]:
        handlers: list[Callable[..., Any]] = []
        with self._lock:
            handlers.extend(self._handlers.get(event, []))
            for pattern, pattern_handlers in self._handlers.items():
                if "*" in pattern and fnmatch.fnmatch(event, pattern):
                    for handler in pattern_handlers:
                        if handler not in handlers:
                            handlers.append(handler)
        return handlers

    def shutdown(self) -> None:
        """Stäng EventBus och ta bort alla handlers."""
        with self._lock:
            self._active = False
            self._handlers.clear()


# ─── IPlugin ────────────────────────────────────────────────────────────────


class IPlugin:
    """Duck-typing-kontrakt för plugins."""

    name: str = "Plugin"
    version: str = "1.0.0"
    description: str = ""
    priority: int = 50

    def on_load(self, context: AppContext) -> None:
        return None

    def on_unload(self) -> None:
        return None

    def get_tab(self) -> Any | None:
        return None

    def setup(self, context: AppContext) -> None:
        return None

    def on_close(self) -> None:
        self.on_unload()


# ─── AppContext ──────────────────────────────────────────────────────────────


class AppContext:
    """Delad kontext för alla plugins."""

    _DEFAULT_PATH_MAP: dict[str, str] = {
        "templates": FOLDER_TEMPLATES,
        "output": FOLDER_OUTPUT,
        "config": FOLDER_CONFIG,
        "plugins": FOLDER_PLUGINS,
        "icons": FOLDER_ICONS,
        "logs": FOLDER_LOGS,
        "backup": FOLDER_BACKUP,
        "sent": FOLDER_SENT,
    }

    def __init__(
        self,
        root: Any,
        config_manager: Any,
        base_path: Path,
        session_id: str,
    ) -> None:
        self.root: Any = root
        self.config_manager: Any = config_manager
        self.settings: Any = config_manager
        self.config: Any = config_manager
        self.base_path: Path = Path(base_path)
        self.session_id: str = session_id
        self.event_bus: EventBus = EventBus()
        self._path_map: dict[str, str] = dict(self._DEFAULT_PATH_MAP)
        self._plugin_manager: PluginManager | None = None
        self.plugin_manager: PluginManager | None = None
        self._notebook: Any = None
        self._is_hidden: bool = True
        self._logger: logging.Logger | None = None
        self._lock = threading.RLock()

    def get_logger(self, name: str) -> logging.Logger:
        if self._logger is not None:
            return self._logger.getChild(name)
        return logging.getLogger(f"{APP_NAME}.{name}")

    def set_logger(self, logger: logging.Logger) -> None:
        """Sätt rotlogger som underloggers skapas från."""
        self._logger = logger

    def get_path(self, key: str) -> Path:
        """Returnera och skapa mapp för nyckel.

        10.8.0: misslyckad mappskapning är inte längre tyst — varning +
        `core.path_failed` så att "hittar inte mappar" syns i loggen.
        """
        lookup_key = str(key).lower()
        folder = self._path_map.get(lookup_key, key)
        path = self.base_path / folder
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            (self._logger or _logger).warning(
                "Kunde inte skapa mapp %s: %s", path, exc
            )
            _evt_core(
                "core.path_failed",
                f"Kunde inte skapa mapp {path}: {exc}",
                logging.WARNING,
                path=str(path),
                error=f"{type(exc).__name__}: {exc}",
            )
        return path

    def register_path(self, key: str, relative_folder: str) -> None:
        """Registrera anpassad relativ sökväg för nyckel."""
        with self._lock:
            self._path_map[key.lower()] = relative_folder

    def emit_event(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> list[Any]:
        return self.event_bus.emit(event, data or {})

    def subscribe(
        self,
        event: str,
        handler: Callable[..., Any],
    ) -> Callable[[], None]:
        return self.event_bus.subscribe(event, handler)

    def show_window(self) -> None:
        if self.root and hasattr(self.root, "show_signal"):
            with suppress(Exception):
                self.root.show_signal.emit()
        elif self.root and hasattr(self.root, "showNormal"):
            with suppress(Exception):
                self.root.showNormal()
                self.root.raise_()
                self.root.activateWindow()
        self._is_hidden = False

    def hide_window(self) -> None:
        if self.root and hasattr(self.root, "hide_signal"):
            with suppress(Exception):
                self.root.hide_signal.emit()
        elif self.root and hasattr(self.root, "hide"):
            with suppress(Exception):
                self.root.hide()
        self._is_hidden = True

    def toggle_window(self) -> bool:
        """Toggla fönster. Returnerar True om fönstret nu är synligt."""
        if self._is_hidden:
            self.show_window()
            return True
        self.hide_window()
        return False

    def show_notification(self, title: str, message: str) -> None:
        self.emit_event(
            "notification",
            {"title": title, "message": message, "level": "info"},
        )

    def get_plugin(self, name: str) -> Any | None:
        manager = self._plugin_manager or self.plugin_manager
        return manager.get_plugin(name) if manager else None


# ─── PluginManager ───────────────────────────────────────────────────────────


class PluginManager:
    """Hanterar plugin-livscykeln."""

    def __init__(self, context: AppContext) -> None:
        self.context: AppContext = context
        self.loaded: dict[str, Any] = {}
        self.failed: dict[str, str] = {}
        self._order: list[str] = []
        self._found: list[tuple[str, str]] = []
        self._logger = logging.getLogger(f"{APP_NAME}.PluginManager")
        context._plugin_manager = self
        context.plugin_manager = self

    def discover(self) -> int:
        """Skanna plugins/-mappen och hitta plugin-paket."""
        plugins_path = self.context.get_path("plugins")
        parent = str(plugins_path.parent)

        if parent not in sys.path:
            sys.path.insert(0, parent)

        self._found.clear()
        if not plugins_path.exists():
            self._logger.warning(
                "Plugins path does not exist: %s", plugins_path
            )
            return 0

        for package in sorted(plugins_path.iterdir()):
            if not package.is_dir() or package.name.startswith("_"):
                continue
            if (package / "__init__.py").exists():
                self._found.append(
                    (package.name, f"plugins.{package.name}")
                )

        self._logger.info("Scanning plugins in: %s", plugins_path)
        self._logger.info(
            "Discovered %d: %s",
            len(self._found),
            ", ".join(name for name, _module in self._found),
        )
        return len(self._found)

    def load_all(self) -> int:
        """Ladda alla hittade plugins i prioritetsordning."""
        candidates: list[tuple[str, type[Any], int]] = []

        for package_name, module_path in self._found:
            plugin_class = self._import_plugin_class(
                package_name, module_path
            )
            if plugin_class is not None:
                candidates.append(
                    (
                        package_name,
                        plugin_class,
                        self._read_priority(plugin_class),
                    )
                )

        candidates.sort(key=lambda item: item[2])

        loaded_count = 0
        for package_name, plugin_class, _priority in candidates:
            if self._load_one(package_name, plugin_class):
                loaded_count += 1

        self._logger.info(
            "Loaded %d/%d, failed: %d",
            loaded_count,
            len(self._found),
            len(self.failed),
        )
        return loaded_count

    def _import_plugin_class(
        self,
        package_name: str,
        module_path: str,
    ) -> type[Any] | None:
        try:
            module = importlib.import_module(module_path)
            plugin_class = getattr(module, "Plugin", None)

            if plugin_class is None:
                submodule = importlib.import_module(
                    f"{module_path}.plugin"
                )
                plugin_class = getattr(submodule, "Plugin", None)

            if plugin_class is None or not isinstance(
                plugin_class, type
            ):
                self.failed[package_name] = (
                    "Ingen Plugin-klass hittades"
                )
                self.context.emit_event(
                    "plugin.failed",
                    {
                        "name": package_name,
                        "error": self.failed[package_name],
                    },
                )
                return None

            return plugin_class
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.failed[package_name] = error
            self._logger.warning("  ✗ %s: %s", package_name, exc)
            _evt_core(
                "core.plugin_import_failed",
                f"Plugin-import misslyckades: {package_name}: {error}",
                logging.ERROR,
                plugin=package_name,
                error=error,
            )
            self.context.emit_event(
                "plugin.failed", {"name": package_name, "error": error}
            )
            return None

    def _load_one(
        self, package_name: str, plugin_class: type[Any]
    ) -> bool:
        started = time.perf_counter()
        try:
            plugin = plugin_class()

            if hasattr(plugin, "on_load") and callable(plugin.on_load):
                plugin.on_load(self.context)
            elif hasattr(plugin, "setup") and callable(plugin.setup):
                plugin.setup(self.context)
            else:
                self.failed[package_name] = (
                    "Saknar on_load() och setup()"
                )
                return False

            plugin_name = self._get_plugin_name(plugin, package_name)
            self.loaded[plugin_name] = plugin
            self._order.append(plugin_name)
            self.failed.pop(package_name, None)
            self.failed.pop(plugin_name, None)

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            version = getattr(plugin, "version", "?")
            priority = getattr(plugin, "priority", "?")

            self._logger.info(
                "  ✓ %s v%s prio=%s (%dms)",
                plugin_name,
                version,
                priority,
                elapsed_ms,
            )
            self.context.emit_event(
                "plugin.loaded",
                {"name": plugin_name, "version": str(version)},
            )
            return True
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            error = f"{type(exc).__name__}: {exc}"
            self.failed[package_name] = error
            self._logger.error(
                "  ✗ %s: %s (%dms)", package_name, error, elapsed_ms
            )
            _evt_core(
                "core.plugin_load_failed",
                f"Plugin-load misslyckades: {package_name}: {error} ({elapsed_ms} ms)",
                logging.ERROR,
                plugin=package_name,
                error=error,
                ms=elapsed_ms,
            )
            self.context.emit_event(
                "plugin.failed", {"name": package_name, "error": error}
            )
            return False

    @staticmethod
    def _read_priority(plugin_class: type[Any]) -> int:
        value = plugin_class.__dict__.get("priority") or getattr(
            plugin_class, "priority", 50
        )
        try:
            if isinstance(value, property):
                with suppress(Exception):
                    instance = object.__new__(plugin_class)
                    return int(value.fget(instance))
                return 50
            return int(value)
        except (TypeError, ValueError):
            return 50

    @staticmethod
    def _get_plugin_name(plugin: Any, fallback: str) -> str:
        try:
            value = plugin.name
            if isinstance(value, str) and value:
                return value
        except Exception:
            return fallback
        return fallback

    def get_tabs(self) -> list[tuple[str, Any]]:
        """Hämta UI-tabbar från alla laddade plugins."""
        tabs: list[tuple[str, Any]] = []

        for name in self._order:
            plugin = self.loaded.get(name)
            if plugin is None:
                continue

            get_tab_fn = getattr(plugin, "get_tab", None)
            if not callable(get_tab_fn):
                continue

            try:
                tab = get_tab_fn()
                if tab is None:
                    continue

                with suppress(ImportError):
                    from PySide6.QtWidgets import QWidget

                    if not isinstance(tab, QWidget):
                        self._logger.debug(
                            "Tab '%s' ignorerad — inte QWidget (%s)",
                            name,
                            type(tab).__name__,
                        )
                        continue

                tabs.append((name, tab))
            except Exception as exc:
                self._logger.warning(
                    "get_tab() fel för '%s': %s", name, exc
                )
                traceback.print_exc()

        self._logger.info("Created %d UI tabs", len(tabs))
        return tabs

    def notify_ready(self) -> None:
        """Emittera plugin_manager.ready utan direkt on_ready-anrop."""
        self._logger.info(
            "Emitting plugin_manager.ready (%d plugins loaded)",
            len(self.loaded),
        )
        self.context.emit_event(
            "plugin_manager.ready",
            {
                "loaded": len(self.loaded),
                "total": len(self._found),
                "names": list(self.loaded.keys()),
            },
        )

    def shutdown(self) -> None:
        """Avsluta alla plugins i omvänd laddningsordning."""
        self._logger.info(
            "Shutting down %d plugins...", len(self.loaded)
        )

        for name in reversed(self._order):
            plugin = self.loaded.get(name)
            if plugin is None:
                continue

            try:
                if hasattr(plugin, "on_unload") and callable(
                    plugin.on_unload
                ):
                    plugin.on_unload()
                elif hasattr(plugin, "on_close") and callable(
                    plugin.on_close
                ):
                    plugin.on_close()
                self.context.emit_event(
                    "plugin.unloaded", {"name": name}
                )
                self._logger.debug("  ✓ %s avslutat", name)
            except Exception:
                self._logger.exception(
                    "  ✗ %s kunde inte avslutas", name
                )

        self.loaded.clear()
        self._order.clear()

    def get_plugin(self, name: str) -> Any | None:
        if name in self.loaded:
            return self.loaded[name]

        normalized = name.casefold()
        for plugin_name, plugin in self.loaded.items():
            if plugin_name.casefold() == normalized:
                return plugin
        return None

    def __iter__(self) -> Iterator[Any]:
        return iter(self.loaded.values())

    def __len__(self) -> int:
        return len(self.loaded)

    def __contains__(self, name: str) -> bool:
        return self.get_plugin(name) is not None


# ─── TabManager ──────────────────────────────────────────────────────────────


class TabManager:
    """Hantera visa/dölja av plugin-tabbar i QTabWidget."""

    def __init__(self, notebook: Any) -> None:
        self.notebook = notebook
        self._tabs: dict[str, Any] = {}
        self._labels: dict[str, str] = {}
        self._order: list[str] = []

    def add_tab(self, name: str, widget: Any, label: str = "") -> None:
        display = label or name
        self._tabs[name] = widget
        self._labels[name] = display
        if name not in self._order:
            self._order.append(name)
        self.notebook.addTab(widget, display)

    def hide_tab(self, name: str) -> None:
        if name not in self._tabs:
            return

        index = self.notebook.indexOf(self._tabs[name])
        if index >= 0:
            self.notebook.removeTab(index)

    def show_tab(self, name: str) -> None:
        if name not in self._tabs:
            return

        widget = self._tabs[name]
        if self.notebook.indexOf(widget) >= 0:
            return

        insert_index = self._calculate_insert_index(name)
        self.notebook.insertTab(
            insert_index, widget, self._labels.get(name, name)
        )

    def _calculate_insert_index(self, name: str) -> int:
        wanted_order = (
            self._order.index(name)
            if name in self._order
            else len(self._order)
        )
        visible_before = 0

        for other_name in self._order:
            if other_name == name:
                break
            other_widget = self._tabs.get(other_name)
            if (
                other_widget is not None
                and self.notebook.indexOf(other_widget) >= 0
            ):
                visible_before += 1

        return min(visible_before, self.notebook.count())

    def toggle_tab(self, name: str) -> bool:
        if self.is_visible(name):
            self.hide_tab(name)
            return False
        self.show_tab(name)
        return True

    def is_visible(self, name: str) -> bool:
        if name not in self._tabs:
            return False
        return self.notebook.indexOf(self._tabs[name]) >= 0

    def get_all_names(self) -> list[str]:
        return list(self._tabs)

    def get_visible_names(self) -> list[str]:
        return [name for name in self._tabs if self.is_visible(name)]

    def get_hidden_names(self) -> list[str]:
        return [name for name in self._tabs if not self.is_visible(name)]


# ─── Export ──────────────────────────────────────────────────────────────────

__all__: list[str] = [
    "__version__",
    "__author__",
    "APP_NAME",
    "APP_DISPLAY_NAME",
    "VERSION",
    "SESSION_ID",
    "COPYRIGHT",
    "get_copyright",
    "FOLDER_PLUGINS",
    "FOLDER_TEMPLATES",
    "FOLDER_OUTPUT",
    "FOLDER_CONFIG",
    "FOLDER_ICONS",
    "FOLDER_LOGS",
    "FOLDER_BACKUP",
    "FOLDER_SENT",
    "INFRASTRUCTURE_AVAILABLE",
    "CONFIG_AVAILABLE",
    "Platform",
    "CURRENT_PLATFORM",
    "PlatformHelper",
    "EventBus",
    "IPlugin",
    "AppContext",
    "PluginManager",
    "TabManager",
    "ConfigManager",
    "SettingsManager",
    "AutostartManager",
    "IconManager",
    "WindowManager",
    "TrayConfig",
]
