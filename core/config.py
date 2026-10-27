# -*- coding: utf-8 -*-
from __future__ import annotations

# core/config.py
# Intygsgenerator — Konfigurationshantering och managers:
#  ConfigManager    — JSON-baserad, trådsäker, atomisk
#  SettingsManager  — alias för ConfigManager
#  AutostartManager — start med OS (Windows/macOS/Linux)
#  IconManager      — appikoner med QPainter-fallback
#  WindowManager    — DPI-medveten fönsterhantering
#  TrayConfig       — system tray-inställningar
#
# Version: 9.1.0
# © Rickard Längkvist 2025–2026
#
# Beroenden: Python 3.12+, PySide6 mjukt

import copy
import fnmatch
import html
import json
import logging
import os
import re
import shlex
import shutil
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, ClassVar

__version__: str = "9.2.0"
__author__: str = "Rickard Längkvist"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

PathLike = str | Path


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    year = datetime.now().year
    return (
        f"© Rickard Längkvist {_START_YEAR}–{year}"
        if year > _START_YEAR
        else f"© Rickard Längkvist {_START_YEAR}"
    )


# ─── Infrastruktur-import med fallback ──────────────────────────────────────

INFRASTRUCTURE_AVAILABLE: bool = False

try:
    from .infrastructure import (
        APP_DISPLAY_NAME,
        APP_NAME,
        DEFAULT_WINDOW_HEIGHT,
        DEFAULT_WINDOW_WIDTH,
        FOLDER_CONFIG,
        FOLDER_ICONS,
        MIN_WINDOW_HEIGHT,
        MIN_WINDOW_WIDTH,
        VERSION,
        AtomicFileWriter,
        CURRENT_PLATFORM,
        DisplayInfo,
        PlatformHelper,
        PlatformType,
    )

    INFRASTRUCTURE_AVAILABLE = True
except ImportError:
    APP_NAME = "Intygsgenerator"
    APP_DISPLAY_NAME = "IntygsDrive"
    VERSION = __version__
    FOLDER_ICONS = "Icon"
    FOLDER_CONFIG = "js"
    DEFAULT_WINDOW_WIDTH = 1100
    DEFAULT_WINDOW_HEIGHT = 750
    MIN_WINDOW_WIDTH = 800
    MIN_WINDOW_HEIGHT = 600

    @dataclass(slots=True)
    class DisplayInfo:
        """Fallback DisplayInfo."""

        width: int = 1920
        height: int = 1080
        dpi: int = 96
        scaling: float = 1.0
        work_area: tuple[int, int, int, int] | None = None

        @property
        def scale_factor(self) -> float:
            return self.scaling

        @property
        def recommended_window_size(self) -> tuple[int, int]:
            return DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT

        def calculate_font_size(self, base_size: int = 10) -> int:
            return base_size

    class PlatformType:
        """Fallback PlatformType."""

        @staticmethod
        def current() -> "PlatformType":
            return PlatformType()

        @property
        def is_windows(self) -> bool:
            return sys.platform == "win32"

        @property
        def is_macos(self) -> bool:
            return sys.platform == "darwin"

        @property
        def is_linux(self) -> bool:
            return sys.platform.startswith("linux")

    CURRENT_PLATFORM = PlatformType()

    class PlatformHelper:
        """Fallback PlatformHelper utan infrastructure.py."""

        @staticmethod
        def is_windows() -> bool:
            return sys.platform == "win32"

        @staticmethod
        def is_macos() -> bool:
            return sys.platform == "darwin"

        @staticmethod
        def is_linux() -> bool:
            return sys.platform.startswith("linux")

        @staticmethod
        def is_frozen() -> bool:
            return bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))

        @staticmethod
        def get_app_path() -> Path:
            if PlatformHelper.is_frozen():
                return Path(sys.executable).parent.resolve()
            return Path(__file__).parent.parent.resolve()

        @staticmethod
        def get_script_path() -> Path | None:
            base = PlatformHelper.get_app_path()
            for filename in ("main.pyw", "main.py"):
                path = base / filename
                if path.exists():
                    return path
            return None

        @staticmethod
        def get_executable_path() -> Path:
            return Path(sys.executable).resolve()

        @staticmethod
        def get_display_info() -> DisplayInfo:
            return DisplayInfo(width=1920, height=1080, dpi=96, scaling=1.0)

    class AtomicFileWriter:
        """Fallback AtomicFileWriter med atomic .tmp → replace."""

        @classmethod
        def write_json(
            cls,
            path: Any,
            data: Any,
            indent: int = 2,
            **kwargs: Any,
        ) -> bool:
            try:
                target = Path(path)
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(f"{target.name}.tmp")
                tmp.write_text(
                    json.dumps(
                        data, indent=indent, ensure_ascii=False,
                        sort_keys=True, default=str,
                    ),
                    encoding="utf-8",
                )
                # 9.2.0 (C3): atomärt replace överallt — ingen unlink-race.
                tmp.replace(target)
                return True
            except Exception:
                logging.getLogger(__name__).exception(
                    "Fallback AtomicFileWriter.write_json failed"
                )
                return False


def _is_windows() -> bool:
    return PlatformHelper.is_windows()


def _is_macos() -> bool:
    return PlatformHelper.is_macos()


def _is_linux() -> bool:
    return PlatformHelper.is_linux()


# ─── ConfigManager ───────────────────────────────────────────────────────────


class ConfigManager:
    """
    JSON-baserad konfigurationshantering.

    Funktioner:
      • Nested keys med punktnotation
      • Atomisk skrivning
      • Trådsäker med RLock
      • Auto-save med debounce 500ms
      • Observer-pattern med wildcard-stöd
      • Singleton per sökväg via get_instance()
    """

    _instances: ClassVar[dict[str, "ConfigManager"]] = {}
    _instances_lock: ClassVar[threading.Lock] = threading.Lock()

    DEFAULT_SETTINGS: ClassVar[dict[str, Any]] = {
        "app.start_minimized": True,
        "app.close_to_tray": True,
        "app.minimize_to_tray": True,
        "app.single_click_open": True,
        "app.show_notifications": True,
        "app.start_with_os": False,
        "app.language": "sv",
        "app.theme": "system",
        "app.check_updates": True,
        "window.width": DEFAULT_WINDOW_WIDTH,
        "window.height": DEFAULT_WINDOW_HEIGHT,
        "window.last_tab": 0,
        "window.maximized": False,
        "ui.hidden_tabs": [],
        "ui.font_size": 10,
        "logging.level": "INFO",
        "logging.max_files": 30,
        "general.theme": "system",
        "mode": "dev",
        "smart_cache": True,
        "metadata.copyright": COPYRIGHT,
    }

    def __init__(
        self,
        path: PathLike,
        auto_save: bool = True,
        create_backup: bool = True,
        debounce_delay: float = 0.5,
    ) -> None:
        self.path = Path(path)
        self.auto_save = auto_save
        self.create_backup = create_backup
        self._debounce_delay = debounce_delay

        self._data: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._log = logging.getLogger(f"{__name__}.ConfigManager")
        self._observers: dict[str, list[Callable[[str, Any, Any], None]]] = {}
        self._save_timer: threading.Timer | None = None
        self._defaults: dict[str, Any] = {}
        self._closed = False

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        self.ensure_defaults(self.DEFAULT_SETTINGS)

    @classmethod
    def get_instance(
        cls,
        path: PathLike | None = None,
        **kwargs: Any,
    ) -> "ConfigManager":
        """Hämta eller skapa singleton-instans per sökväg."""
        if path is None:
            raise ValueError("Path krävs för första ConfigManager-instansen")

        key = str(Path(path).resolve())
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(path, **kwargs)
            return cls._instances[key]

    @classmethod
    def clear_instances(cls) -> None:
        """Stäng och rensa alla singleton-instanser."""
        with cls._instances_lock:
            for instance in cls._instances.values():
                with suppress(Exception):
                    instance.close()
            cls._instances.clear()

    def _load(self) -> bool:
        """Ladda konfiguration från JSON-fil."""
        try:
            if not self.path.exists():
                self._data = {}
                self._log.debug("[Config] Ingen fil hittades — startar med defaults")
                return False

            content = self.path.read_text(encoding="utf-8")
            if not content.strip():
                self._data = {}
                return True

            data = json.loads(content)
            if isinstance(data, dict):
                self._data = data
                self._log.debug(
                    "Laddade %d nycklar från %s",
                    len(self._flatten_keys(data)),
                    self.path.name,
                )
                return True

            self._log.warning("[Config] Rotobjekt är inte dict — återställer")
            self._data = {}
        except json.JSONDecodeError as exc:
            self._log.warning("[Config] Korrupt fil: %s", exc)
            if self.create_backup:
                self._backup_corrupt_file()
            self._data = {}
        except Exception as exc:
            self._log.error("[Config] Laddningsfel: %s", exc)
            self._data = {}

        return False

    def _backup_corrupt_file(self) -> None:
        """Skapa tidsstämplad backup av korrupt configfil."""
        if not self.path.exists():
            return

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{self.path.stem}.corrupt.{timestamp}{self.path.suffix}"
            shutil.copy2(self.path, self.path.with_name(backup_name))
            self._log.info("[Config] Korrupt fil backupad: %s", backup_name)
        except Exception as exc:
            self._log.error("[Config] Backup misslyckades: %s", exc)

    def save(self, force: bool = False) -> bool:
        """Spara konfiguration atomiskt."""
        with self._lock:
            if self._closed and not force:
                return False

            if not force and not self._dirty:
                return True

            if self._save_timer:
                self._save_timer.cancel()
                self._save_timer = None

            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)

                if INFRASTRUCTURE_AVAILABLE:
                    try:
                        success = AtomicFileWriter.write_json(
                            self.path,
                            self._data,
                            indent=2,
                            backup=self.create_backup,
                        )
                    except Exception:
                        success = self._save_fallback()
                else:
                    success = self._save_fallback()

                if success:
                    self._dirty = False
                    self._log.debug("[Config] Sparad: %s", self.path.name)
                return success
            except Exception as exc:
                self._log.error("[Config] Sparning misslyckades: %s", exc)
                return False

    def _save_fallback(self) -> bool:
        """Atomic fallback: .tmp → replace.

        9.2.0 (C3): ingen unlink-race på Windows — os.replace/Path.replace är
        atomärt på alla plattformar (filen finns alltid eller aldrig).
        """
        try:
            tmp = self.path.with_name(f"{self.path.name}.tmp")
            tmp.write_text(
                json.dumps(
                    self._data, indent=2, ensure_ascii=False,
                    sort_keys=True, default=str,
                ),
                encoding="utf-8",
            )
            tmp.replace(self.path)
            return True
        except Exception as exc:
            self._log.error("[Config] Fallback-sparning: %s", exc)
            return False

    def _schedule_save(self) -> None:
        """Schemalägg auto-save med debounce."""
        if not self.auto_save or self._closed:
            return

        with self._lock:
            if self._save_timer:
                self._save_timer.cancel()
            timer = threading.Timer(self._debounce_delay, self.save)
            timer.daemon = True
            self._save_timer = timer
            timer.start()

    def reload(self) -> bool:
        """Ladda om från fil. Osparade ändringar försvinner."""
        with self._lock:
            if self._save_timer:
                self._save_timer.cancel()
                self._save_timer = None
            self._dirty = False
            return self._load()

    def get(self, key: str, default: Any = None) -> Any:
        """Hämta värde med punktnotation."""
        with self._lock:
            parts = key.split(".")
            value: Any = self._data
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return copy.deepcopy(self._defaults.get(key, default))
            return copy.deepcopy(value) if isinstance(value, (dict, list)) else value

    def set(
        self,
        key: str,
        value: Any,
        notify: bool = True,
        auto_save: bool = True,
    ) -> None:
        """Sätt värde med punktnotation."""
        with self._lock:
            old_value = self.get(key)
            parts = key.split(".")
            data = self._data
            for part in parts[:-1]:
                if part not in data or not isinstance(data[part], dict):
                    data[part] = {}
                data = data[part]
            data[parts[-1]] = (
                copy.deepcopy(value) if isinstance(value, (dict, list)) else value
            )
            self._dirty = True

        if auto_save and self.auto_save:
            self._schedule_save()

        if notify and old_value != value:
            self._notify_observers(key, old_value, value)

    def delete(self, key: str, notify: bool = True) -> bool:
        """Ta bort nyckel. Returnerar True om nyckeln fanns."""
        with self._lock:
            parts = key.split(".")
            path_stack: list[tuple[dict[str, Any], str]] = []
            data = self._data

            for part in parts[:-1]:
                if isinstance(data, dict) and part in data:
                    path_stack.append((data, part))
                    data = data[part]
                else:
                    return False

            if not (isinstance(data, dict) and parts[-1] in data):
                return False

            old_value = data[parts[-1]]
            del data[parts[-1]]
            self._dirty = True

            for parent, child_key in reversed(path_stack):
                if not parent[child_key]:
                    del parent[child_key]
                else:
                    break

        if self.auto_save:
            self._schedule_save()

        if notify:
            self._notify_observers(key, old_value, None)

        return True

    def has(self, key: str) -> bool:
        """Kontrollera om nyckel finns.

        9.2.0 (C1): direkt traversering — tidigare deepcopy av sentinel
        gjorde att saknade nycklar svarade True (och ensure_defaults aldrig
        skrev sina värden).
        """
        with self._lock:
            value: Any = self._data
            for part in key.split("."):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return False
            return True

    def exists(self, key: str) -> bool:
        """Alias för has()."""
        return self.has(key)

    def get_or_set(self, key: str, default: Any) -> Any:
        if self.has(key):
            return self.get(key)
        self.set(key, default)
        return default

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    def set_all(self, data: dict[str, Any], notify: bool = True) -> None:
        with self._lock:
            old_data = self._data
            self._data = copy.deepcopy(data)
            self._dirty = True

        if self.auto_save:
            self.save(force=True)

        if notify:
            old_keys = set(self._flatten_keys(old_data))
            new_keys = set(self._flatten_keys(self._data))
            for key in old_keys | new_keys:
                old_value = self._get_nested(old_data, key)
                new_value = self._get_nested(self._data, key)
                if old_value != new_value:
                    self._notify_observers(key, old_value, new_value)

    def merge(self, data: dict[str, Any], overwrite: bool = True) -> None:
        with self._lock:
            self._merge_recursive(self._data, data, overwrite)
            self._dirty = True

        if self.auto_save:
            self.save(force=True)

    def _merge_recursive(
        self,
        target: dict[str, Any],
        source: dict[str, Any],
        overwrite: bool,
    ) -> None:
        for key, value in source.items():
            if (
                key in target
                and isinstance(target[key], dict)
                and isinstance(value, dict)
            ):
                self._merge_recursive(target[key], value, overwrite)
            elif overwrite or key not in target:
                target[key] = copy.deepcopy(value)

    def _get_nested(self, data: dict[str, Any], key: str) -> Any:
        value: Any = data
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return None
        return value

    def keys(self, prefix: str = "") -> list[str]:
        with self._lock:
            all_keys = self._flatten_keys(self._data)
            if prefix:
                return [key for key in all_keys if key.startswith(prefix)]
            return all_keys

    def _flatten_keys(self, data: dict[str, Any], prefix: str = "") -> list[str]:
        result: list[str] = []
        for key, value in data.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                result.extend(self._flatten_keys(value, full_key))
            else:
                result.append(full_key)
        return result

    def set_default(self, key: str, value: Any) -> None:
        self._defaults[key] = copy.deepcopy(value)

    def set_defaults(self, defaults: dict[str, Any]) -> None:
        self._defaults.update(copy.deepcopy(defaults))

    def ensure_defaults(self, defaults: dict[str, Any]) -> None:
        """Sätt värden endast om de inte redan finns."""
        changed = False
        for key, value in defaults.items():
            self._defaults[key] = copy.deepcopy(value)
            if not self.has(key):
                self.set(key, value, notify=False, auto_save=False)
                changed = True

        if changed and self.auto_save:
            self._schedule_save()

    def observe(
        self,
        key: str,
        callback: Callable[[str, Any, Any], None],
    ) -> Callable[[], None]:
        """Prenumerera på ändringar för nyckel. Stödjer wildcard."""
        with self._lock:
            self._observers.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            self.unobserve(key, callback)

        return unsubscribe

    def unobserve(
        self,
        key: str,
        callback: Callable[[str, Any, Any], None],
    ) -> bool:
        with self._lock:
            callbacks = self._observers.get(key)
            if not callbacks:
                return False
            with suppress(ValueError):
                callbacks.remove(callback)
                if not callbacks:
                    del self._observers[key]
                return True
        return False

    def _notify_observers(
        self,
        key: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        callbacks_to_call: list[Callable[[str, Any, Any], None]] = []
        with self._lock:
            callbacks_to_call.extend(self._observers.get(key, []))
            for pattern, callbacks in self._observers.items():
                if "*" in pattern and fnmatch.fnmatch(key, pattern):
                    callbacks_to_call.extend(callbacks)

        for callback in callbacks_to_call:
            try:
                callback(key, old_value, new_value)
            except Exception as exc:
                self._log.error("[Config] Observer-fel '%s': %s", key, exc)

    def increment(self, key: str, amount: int = 1) -> int:
        current = self.get(key, 0)
        if not isinstance(current, (int, float)):
            current = 0
        new_value = int(current + amount)
        self.set(key, new_value)
        return new_value

    def decrement(self, key: str, amount: int = 1) -> int:
        return self.increment(key, -amount)

    def toggle(self, key: str) -> bool:
        new_value = not bool(self.get(key, False))
        self.set(key, new_value)
        return new_value

    def append_to_list(
        self,
        key: str,
        value: Any,
        max_length: int | None = None,
    ) -> None:
        current = self.get(key, [])
        if not isinstance(current, list):
            current = []
        current.append(value)
        if max_length and len(current) > max_length:
            current = current[-max_length:]
        self.set(key, current)

    def remove_from_list(self, key: str, value: Any) -> bool:
        current = self.get(key, [])
        if not isinstance(current, list):
            return False
        try:
            current.remove(value)
            self.set(key, current)
            return True
        except ValueError:
            return False

    def export_to_file(self, path: PathLike) -> bool:
        """Exportera konfiguration till fil."""
        try:
            export_path = Path(path)
            export_path.parent.mkdir(parents=True, exist_ok=True)
            data = self.get_all()
            data["_export_meta"] = {
                "exported_at": datetime.now().isoformat(),
                "app_version": VERSION,
                "source": str(self.path),
                "copyright": COPYRIGHT,
            }

            if INFRASTRUCTURE_AVAILABLE:
                return AtomicFileWriter.write_json(export_path, data, indent=2)

            tmp = export_path.with_name(f"{export_path.name}.tmp")
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(export_path)
            return True
        except Exception as exc:
            self._log.error("[Config] Export misslyckades: %s", exc)
            return False

    def import_from_file(self, path: PathLike, merge: bool = True) -> bool:
        """Importera konfiguration från fil."""
        try:
            import_path = Path(path)
            if not import_path.exists():
                return False

            data = json.loads(import_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False

            data.pop("_export_meta", None)
            if merge:
                self.merge(data)
            else:
                self.set_all(data)
            return True
        except Exception as exc:
            self._log.error("[Config] Import misslyckades: %s", exc)
            return False

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def file_exists(self) -> bool:
        return self.path.exists()

    @property
    def key_count(self) -> int:
        with self._lock:
            return len(self._flatten_keys(self._data))

    def close(self) -> None:
        """Stäng och spara idempotent."""
        if self._closed:
            return

        if self._save_timer:
            self._save_timer.cancel()
            self._save_timer = None

        self.save(force=True)
        self._closed = True

    def __contains__(self, key: str) -> bool:
        return self.has(key)

    def __bool__(self) -> bool:
        # 9.2.0 (C2): en tom config är fortfarande ett giltigt objekt —
        # `if settings:` får aldrig bli False bara för att nycklar saknas.
        return True

    def __getitem__(self, key: str) -> Any:
        if not self.has(key):
            raise KeyError(key)
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __delitem__(self, key: str) -> None:
        if not self.delete(key):
            raise KeyError(key)

    def __len__(self) -> int:
        return self.key_count

    def __repr__(self) -> str:
        return (
            f"<ConfigManager path='{self.path.name}' "
            f"keys={self.key_count} dirty={self._dirty}>"
        )

    def __enter__(self) -> "ConfigManager":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


SettingsManager = ConfigManager


# ─── AutostartManager ───────────────────────────────────────────────────────


class AutostartManager:
    """Hanterar autostart med OS."""

    SETTINGS_KEY: ClassVar[str] = "app.start_with_os"
    _REG_KEY: ClassVar[str] = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(
        self,
        app_name: str = APP_NAME,
        app_path: Path | None = None,
        settings: ConfigManager | None = None,
    ) -> None:
        self.app_name = app_name
        self._settings = settings
        self._log = logging.getLogger(f"{__name__}.AutostartManager")

        if app_path:
            self.app_path = Path(app_path)
        else:
            with suppress(Exception):
                self.app_path = PlatformHelper.get_app_path()
            if not hasattr(self, "app_path"):
                self.app_path = Path(__file__).parent.parent.resolve()

        self.sync_with_system()

    def sync_with_system(self) -> None:
        """Synkronisera sparad inställning med OS-tillstånd."""
        if not self._settings:
            return

        try:
            system_enabled = self._check_system_autostart()
            saved_enabled = self._settings.get(self.SETTINGS_KEY)
            if saved_enabled is not None and bool(saved_enabled) != system_enabled:
                self._log.info(
                    "[Autostart] Mismatch: sparad=%s system=%s",
                    saved_enabled,
                    system_enabled,
                )
                if saved_enabled:
                    self._enable_system()
                else:
                    self._disable_system()
        except Exception as exc:
            self._log.debug("[Autostart] Synk misslyckades: %s", exc)

    def _check_system_autostart(self) -> bool:
        if _is_windows():
            return self._is_enabled_windows()
        if _is_macos():
            return self._is_enabled_macos()
        if _is_linux():
            return self._is_enabled_linux()
        return False

    def is_enabled(self) -> bool:
        if self._settings:
            return bool(self._settings.get(self.SETTINGS_KEY, False))
        return self._check_system_autostart()

    def enable(self) -> bool:
        success = self._enable_system()
        if self._settings:
            self._settings.set(self.SETTINGS_KEY, success)
            self._settings.save(force=True)
        self._log.info("[Autostart] Aktiverad: %s", success)
        return success

    def disable(self) -> bool:
        success = self._disable_system()
        if self._settings:
            self._settings.set(self.SETTINGS_KEY, False)
            self._settings.save(force=True)
        self._log.info("[Autostart] Inaktiverad")
        return success

    def set_enabled(self, enabled: bool) -> bool:
        return self.enable() if enabled else self.disable()

    def toggle(self) -> bool:
        new_state = not self.is_enabled()
        self.set_enabled(new_state)
        return new_state

    def get_command(self) -> str:
        """Returnera startkommando. Windows använder pythonw.exe när möjligt."""
        if PlatformHelper.is_frozen():
            with suppress(Exception):
                return f'"{PlatformHelper.get_executable_path()}"'
            return f'"{sys.executable}"'

        script: Path | None = None
        with suppress(Exception):
            script = PlatformHelper.get_script_path()
        if script is None:
            script = Path(sys.argv[0]).resolve()

        python = sys.executable
        if _is_windows():
            pythonw = Path(python).parent / "pythonw.exe"
            if pythonw.exists():
                python = str(pythonw)

        return f'"{python}" "{script}"'

    def _enable_system(self) -> bool:
        try:
            if _is_windows():
                return self._enable_windows()
            if _is_macos():
                return self._enable_macos()
            if _is_linux():
                return self._enable_linux()
        except Exception as exc:
            self._log.error("[Autostart] Aktivering: %s", exc)
        return False

    def _disable_system(self) -> bool:
        try:
            if _is_windows():
                return self._disable_windows()
            if _is_macos():
                return self._disable_macos()
            if _is_linux():
                return self._disable_linux()
        except Exception as exc:
            self._log.error("[Autostart] Inaktivering: %s", exc)
            return True

    def _is_enabled_windows(self) -> bool:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._REG_KEY, 0, winreg.KEY_READ
            )
            try:
                value, _value_type = winreg.QueryValueEx(key, self.app_name)
                return bool(value)
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _enable_windows(self) -> bool:
        try:
            import winreg

            command = self.get_command()
            if not command:
                return False

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._REG_KEY, 0, winreg.KEY_SET_VALUE
            )
            try:
                winreg.SetValueEx(key, self.app_name, 0, winreg.REG_SZ, command)
                self._log.debug(
                    "[Autostart] Windows: %s = %s", self.app_name, command
                )
                return True
            finally:
                winreg.CloseKey(key)
        except Exception as exc:
            self._log.error("[Autostart] Windows enable: %s", exc)
            return False

    def _disable_windows(self) -> bool:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._REG_KEY, 0, winreg.KEY_SET_VALUE
            )
            try:
                with suppress(FileNotFoundError):
                    winreg.DeleteValue(key, self.app_name)
            finally:
                winreg.CloseKey(key)
            return True
        except Exception as exc:
            self._log.error("[Autostart] Windows disable: %s", exc)
            return False

    def _get_plist_path(self) -> Path:
        slug = re.sub(r"[^a-zA-Z0-9]", "", self.app_name.lower())
        return (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / f"com.rickardlangkvist.{slug}.plist"
        )

    def _is_enabled_macos(self) -> bool:
        return self._get_plist_path().exists()

    def _enable_macos(self) -> bool:
        try:
            plist = self._get_plist_path()
            plist.parent.mkdir(parents=True, exist_ok=True)

            if PlatformHelper.is_frozen():
                program_args = [str(PlatformHelper.get_executable_path())]
            else:
                script = PlatformHelper.get_script_path() or Path(
                    sys.argv[0]
                ).resolve()
                program_args = [sys.executable, str(script)]

            args_xml = "\n".join(
                f"        <string>{html.escape(argument)}</string>"
                for argument in program_args
            )
            slug = re.sub(r"[^a-zA-Z0-9]", "", self.app_name.lower())
            content = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                "<dict>\n"
                "    <key>Label</key>\n"
                f"    <string>com.rickardlangkvist.{slug}</string>\n"
                "    <key>ProgramArguments</key>\n"
                f"<array>\n{args_xml}\n    </array>\n"
                "    <key>RunAtLoad</key>\n"
                "    <true/>\n"
                "    <key>KeepAlive</key>\n"
                "    <false/>\n"
                "</dict>\n"
                "</plist>\n"
            )
            plist.write_text(content, encoding="utf-8")
            self._log.debug("[Autostart] macOS plist: %s", plist)
            return True
        except Exception as exc:
            self._log.error("[Autostart] macOS enable: %s", exc)
            return False

    def _disable_macos(self) -> bool:
        try:
            path = self._get_plist_path()
            if path.exists():
                path.unlink()
            return True
        except Exception as exc:
            self._log.error("[Autostart] macOS disable: %s", exc)
            return False

    def _get_desktop_path(self) -> Path:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "")
        base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
        slug = re.sub(r"[^a-zA-Z0-9-]", "-", self.app_name.lower()).strip("-")
        return base / "autostart" / f"{slug}.desktop"

    def _is_enabled_linux(self) -> bool:
        return self._get_desktop_path().exists()

    def _enable_linux(self) -> bool:
        try:
            desktop = self._get_desktop_path()
            desktop.parent.mkdir(parents=True, exist_ok=True)

            if PlatformHelper.is_frozen():
                exec_cmd = shlex.quote(str(PlatformHelper.get_executable_path()))
            else:
                script = PlatformHelper.get_script_path() or Path(
                    sys.argv[0]
                ).resolve()
                exec_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"

            icon_path = self.app_path / FOLDER_ICONS / "icon.svg"
            if not icon_path.is_file():
                icon_path = self.app_path / FOLDER_ICONS / "icon.png"
            content = (
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={APP_DISPLAY_NAME}\n"
                f"Exec={exec_cmd}\n"
                f"Icon={icon_path if icon_path.is_file() else self.app_name}\n"
                "Terminal=false\n"
                "StartupNotify=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
            desktop.write_text(content, encoding="utf-8")
            with suppress(OSError):
                desktop.chmod(0o755)
            self._log.debug("[Autostart] Linux .desktop: %s", desktop)
            return True
        except Exception as exc:
            self._log.error("[Autostart] Linux enable: %s", exc)
            return False

    def _disable_linux(self) -> bool:
        try:
            path = self._get_desktop_path()
            if path.exists():
                path.unlink()
            return True
        except Exception as exc:
            self._log.error("[Autostart] Linux disable: %s", exc)
            return False

    def __repr__(self) -> str:
        return (
            f"<AutostartManager app='{self.app_name}' "
            f"enabled={self.is_enabled()}>"
        )


# ─── IconManager ─────────────────────────────────────────────────────────────


class IconManager:
    """Hanterar applikationsikoner med PySide6 och QPainter-fallback."""

    SUPPORTED_FORMATS: ClassVar[set[str]] = {
        ".svg", ".ico", ".png", ".gif", ".jpg", ".jpeg", ".icns", ".bmp",
    }
    PRIORITY_ORDER: ClassVar[list[str]] = [
        ".svg", ".png", ".ico", ".icns", ".gif", ".jpg", ".jpeg", ".bmp",
    ]

    def __init__(self, base_path: PathLike) -> None:
        self.base_path = Path(base_path)
        self.icon_folder = self.base_path / FOLDER_ICONS
        self._log = logging.getLogger(f"{__name__}.IconManager")
        self._icon_path_cache: Path | None = None
        self._pil_image_cache: dict[tuple[int, int], Any] = {}
        self._qicon_cache: dict[tuple[int, int], Any] = {}

        with suppress(Exception):
            self.icon_folder.mkdir(parents=True, exist_ok=True)

    def find_icon(self, refresh: bool = False) -> Path | None:
        """Hitta första tillgängliga ikonfil."""
        if not refresh and self._icon_path_cache and self._icon_path_cache.exists():
            return self._icon_path_cache

        if not self.icon_folder.exists():
            return None

        try:
            if not any(self.icon_folder.iterdir()):
                return None
        except Exception:
            return None

        for extension in self.PRIORITY_ORDER:
            for icon_file in self.icon_folder.glob(f"*{extension}"):
                if icon_file.is_file() and not icon_file.name.startswith("."):
                    self._icon_path_cache = icon_file
                    self._log.debug("[IconManager] Ikon: %s", icon_file.name)
                    return icon_file

        return None

    def get_icon_path(self) -> Path | None:
        return self.find_icon()

    def has_custom_icon(self) -> bool:
        return self.find_icon() is not None

    def get_qicon(self, size: tuple[int, int] = (64, 64)) -> Any:
        """Returnera QIcon med QPainter-fallback."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap

        if size in self._qicon_cache:
            return self._qicon_cache[size]

        icon_path = self.find_icon()
        if icon_path and icon_path.exists():
            with suppress(Exception):
                qicon = QIcon(str(icon_path))
                if not qicon.isNull():
                    self._qicon_cache[size] = qicon
                    return qicon

        pil_image = self.get_pil_image(size)
        if pil_image is not None:
            try:
                import io

                buffer = io.BytesIO()
                pil_image.save(buffer, format="PNG")
                buffer.seek(0)
                qimage = QImage.fromData(buffer.read())
                pixmap = QPixmap.fromImage(qimage)
                qicon = QIcon(pixmap)
                if not qicon.isNull():
                    self._qicon_cache[size] = qicon
                    return qicon
            except Exception as exc:
                self._log.debug("[IconManager] PIL→QIcon: %s", exc)

        width, height = size
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(0, 120, 212))
        painter.setPen(Qt.PenStyle.NoPen)
        margin = max(min(width, height) // 10, 2)
        painter.drawEllipse(
            margin, margin, width - (2 * margin), height - (2 * margin)
        )
        painter.setPen(QColor(255, 255, 255))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(max(10, min(width, height) // 2))
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "I")
        painter.end()

        qicon = QIcon(pixmap)
        self._qicon_cache[size] = qicon
        return qicon

    def set_window_icon(self, window: Any) -> bool:
        """Sätt fönsterikon."""
        try:
            qicon = self.get_qicon((64, 64))
            if not qicon.isNull():
                window.setWindowIcon(qicon)
                return True
        except Exception as exc:
            self._log.warning("[IconManager] set_window_icon: %s", exc)
        return False

    def get_pil_image(
        self,
        size: tuple[int, int] = (64, 64),
        refresh: bool = False,
    ) -> Any | None:
        """Returnera PIL Image eller genererad fallback om Pillow finns."""
        if not refresh and size in self._pil_image_cache:
            return self._pil_image_cache[size]

        try:
            from PIL import Image

            icon_path = self.find_icon()
            if icon_path and icon_path.exists():
                image = Image.open(str(icon_path))
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                image = image.resize(size, Image.Resampling.LANCZOS)
            else:
                image = self._create_default_icon(size)

            if image is not None:
                self._pil_image_cache[size] = image
            return image
        except ImportError:
            return None
        except Exception as exc:
            self._log.warning("[IconManager] PIL: %s", exc)
            return self._create_default_icon(size)

    def _create_default_icon(
        self, size: tuple[int, int] = (64, 64)
    ) -> Any | None:
        """Skapa genererad ikon via Pillow."""
        try:
            from PIL import Image, ImageDraw

            image = Image.new("RGBA", size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            margin = max(size[0] // 10, 2)
            draw.ellipse(
                [margin, margin, size[0] - margin, size[1] - margin],
                fill=(0, 120, 212, 255),
            )
            center_x = size[0] // 2
            center_y = size[1] // 2
            bar_width = max(size[0] // 8, 3)
            bar_height = size[1] // 3
            draw.rectangle(
                [
                    center_x - bar_width,
                    center_y - bar_height,
                    center_x + bar_width,
                    center_y + bar_height,
                ],
                fill=(255, 255, 255, 255),
            )
            return image
        except ImportError:
            return None
        except Exception as exc:
            self._log.debug("[IconManager] Default icon: %s", exc)
            return None

    def get_tray_icon(
        self, size: tuple[int, int] | None = None
    ) -> Any | None:
        """Returnera PIL Image för system tray där det behövs."""
        if size is None:
            if _is_windows():
                size = (32, 32)
            elif _is_macos():
                size = (44, 44)
            else:
                size = (24, 24)
        return self.get_pil_image(size)

    def get_icon(self) -> Any | None:
        return self.get_tray_icon()

    def clear_cache(self) -> None:
        self._icon_path_cache = None
        self._pil_image_cache.clear()
        self._qicon_cache.clear()

    def get_icon_info(self) -> dict[str, Any]:
        icon_path = self.find_icon()
        return {
            "has_custom_icon": icon_path is not None,
            "using_default": icon_path is None,
            "icon_folder": str(self.icon_folder),
            "icon_path": str(icon_path) if icon_path else None,
            "folder_exists": self.icon_folder.exists(),
        }

    def __repr__(self) -> str:
        return (
            f"<IconManager folder='{self.icon_folder}' "
            f"has_icon={self.has_custom_icon()}>"
        )


# ─── WindowManager ───────────────────────────────────────────────────────────


class WindowManager:
    """Hanterar fönsterstorlek, position och DPI-skalning via PySide6."""

    def __init__(
        self,
        window: Any | None = None,
        config: ConfigManager | None = None,
    ) -> None:
        self.window = window
        self._config = config
        self._log = logging.getLogger(f"{__name__}.WindowManager")
        self._display_info: DisplayInfo | None = None

    @property
    def settings(self) -> ConfigManager | None:
        return self._config

    def _get_display_info(self) -> DisplayInfo:
        """Hämta skärminformation via PySide6 eller fallback."""
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                screen = app.primaryScreen()
                if screen:
                    geometry = screen.availableGeometry()
                    dpi = int(screen.logicalDotsPerInch())
                    ratio = screen.devicePixelRatio()
                    return DisplayInfo(
                        width=geometry.width(),
                        height=geometry.height(),
                        dpi=dpi,
                        scaling=float(ratio),
                        work_area=(
                            geometry.x(),
                            geometry.y(),
                            geometry.width(),
                            geometry.height(),
                        ),
                    )
        except Exception as exc:
            self._log.debug("[WindowManager] PySide6: %s", exc)

        if INFRASTRUCTURE_AVAILABLE:
            with suppress(Exception):
                return PlatformHelper.get_display_info()

        return DisplayInfo(width=1920, height=1080, dpi=96, scaling=1.0)

    @property
    def display_info(self) -> DisplayInfo:
        if self._display_info is None:
            self._display_info = self._get_display_info()
        return self._display_info

    @property
    def scale_factor(self) -> float:
        return self.display_info.scaling

    def get_font_size(self, base_size: int = 10) -> int:
        return self.display_info.calculate_font_size(base_size)

    def setup_window(
        self,
        title: str = APP_DISPLAY_NAME,
        min_size: tuple[int, int] | None = None,
        center: bool = True,
        restore_position: bool = True,
    ) -> None:
        """Konfigurera fönster med titel, storlek och DPI."""
        if not self.window:
            return

        self.window.setWindowTitle(title)
        display_info = self._get_display_info()
        self._display_info = display_info
        width, height = display_info.recommended_window_size

        if restore_position and self._config:
            saved_width = self._config.get("window.width")
            saved_height = self._config.get("window.height")
            if (
                isinstance(saved_width, int)
                and isinstance(saved_height, int)
                and MIN_WINDOW_WIDTH <= saved_width <= display_info.width
                and MIN_WINDOW_HEIGHT <= saved_height <= display_info.height
            ):
                width, height = saved_width, saved_height

        minimum_width, minimum_height = min_size or (
            MIN_WINDOW_WIDTH,
            MIN_WINDOW_HEIGHT,
        )
        self.window.setMinimumSize(minimum_width, minimum_height)
        self.window.resize(width, height)

        if center:
            self.center_window()

    def center_window(self) -> None:
        if not self.window:
            return

        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                screen = app.primaryScreen()
                if screen:
                    geometry = screen.availableGeometry()
                    frame_width = self.window.width()
                    frame_height = self.window.height()
                    x_pos = geometry.x() + (geometry.width() - frame_width) // 2
                    y_pos = geometry.y() + (geometry.height() - frame_height) // 2
                    self.window.move(
                        max(geometry.x(), x_pos), max(geometry.y(), y_pos)
                    )
        except Exception as exc:
            self._log.debug("[WindowManager] center: %s", exc)

    def save_geometry(self) -> None:
        if not self.window or not self._config:
            return

        try:
            from PySide6.QtCore import Qt

            state = self.window.windowState()
            is_maximized = bool(state & Qt.WindowState.WindowMaximized)
            if not is_maximized:
                geometry = self.window.geometry()
                self._config.set(
                    "window.width", geometry.width(), notify=False
                )
                self._config.set(
                    "window.height", geometry.height(), notify=False
                )
                self._config.set("window.x", geometry.x(), notify=False)
                self._config.set("window.y", geometry.y(), notify=False)
            self._config.set("window.maximized", is_maximized, notify=False)
            self._config.save(force=True)
        except Exception as exc:
            self._log.warning("[WindowManager] save_geometry: %s", exc)

    def restore_geometry(self) -> None:
        if not self.window or not self._config:
            return

        try:
            from PySide6.QtCore import Qt

            display_info = self._get_display_info()
            width = self._config.get("window.width", DEFAULT_WINDOW_WIDTH)
            height = self._config.get("window.height", DEFAULT_WINDOW_HEIGHT)
            x_pos = self._config.get("window.x")
            y_pos = self._config.get("window.y")

            if isinstance(width, int) and isinstance(height, int):
                width = max(MIN_WINDOW_WIDTH, min(width, display_info.width))
                height = max(MIN_WINDOW_HEIGHT, min(height, display_info.height))
            else:
                width, height = DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT

            if isinstance(x_pos, int) and isinstance(y_pos, int):
                x_pos = max(0, min(x_pos, display_info.width - width))
                y_pos = max(0, min(y_pos, display_info.height - height))
                self.window.setGeometry(x_pos, y_pos, width, height)
            else:
                self.window.resize(width, height)
                self.center_window()

            if self._config.get("window.maximized", False):
                self.window.setWindowState(
                    self.window.windowState() | Qt.WindowState.WindowMaximized
                )
        except Exception as exc:
            self._log.warning("[WindowManager] restore_geometry: %s", exc)

    def scaled_size(self, base_size: int) -> int:
        return int(base_size * self.scale_factor)

    def __repr__(self) -> str:
        display_info = self.display_info
        return (
            f"<WindowManager display={display_info.width}x"
            f"{display_info.height} scale={display_info.scaling}>"
        )


# ─── TrayConfig ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class TrayConfig:
    """Konfiguration för system tray."""

    start_minimized: bool = True
    minimize_to_tray: bool = True
    close_to_tray: bool = True
    single_click_open: bool = True
    show_notifications: bool = True
    start_with_os: bool = False

    @classmethod
    def from_config(cls, config: ConfigManager | None) -> "TrayConfig":
        if config is None:
            return cls()

        return cls(
            start_minimized=bool(config.get("app.start_minimized", True)),
            minimize_to_tray=bool(config.get("app.minimize_to_tray", True)),
            close_to_tray=bool(config.get("app.close_to_tray", True)),
            single_click_open=bool(config.get("app.single_click_open", True)),
            show_notifications=bool(
                config.get("app.show_notifications", True)
            ),
            start_with_os=bool(config.get("app.start_with_os", False)),
        )

    def save_to_config(self, config: ConfigManager | None) -> None:
        if config is None:
            return

        config.set("app.start_minimized", self.start_minimized)
        config.set("app.minimize_to_tray", self.minimize_to_tray)
        config.set("app.close_to_tray", self.close_to_tray)
        config.set("app.single_click_open", self.single_click_open)
        config.set("app.show_notifications", self.show_notifications)
        config.set("app.start_with_os", self.start_with_os)

    def should_start_hidden(self) -> bool:
        """True om appen ska starta gömd."""
        return self.start_minimized

    def should_hide_on_close(self) -> bool:
        """True om X-knapp ska minimera istället för att avsluta."""
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


# ─── Export ──────────────────────────────────────────────────────────────────


__all__: list[str] = [
    "__version__",
    "__author__",
    "COPYRIGHT",
    "get_copyright",
    "PathLike",
    "ConfigManager",
    "SettingsManager",
    "AutostartManager",
    "IconManager",
    "WindowManager",
    "TrayConfig",
]
