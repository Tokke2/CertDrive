from __future__ import annotations

# -*- coding: utf-8 -*-
"""
Fil:       plugins/generator/__init__.py
Projekt:   Intygsgenerator
Version:   5.0.1
Ansvar:    Service-plugin för PDF-generering. Initiering och metadata.
           Delegerar till plugin.py och certificates engine.
           Ingen UI-tab. Fail-safe fallback vid importfel.

© Rickard Längkvist 2025–{year}
"""

import logging
import threading
from datetime import datetime
from typing import Any, Final

__version__: Final[str] = "5.0.1"
__author__: Final[str] = "Rickard Längkvist"

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

_logger = logging.getLogger(__name__)
_state_lock = threading.RLock()
_load_errors: list[str] = []
_fully_loaded: bool = False

PLUGIN_INFO: dict[str, Any] = {
    "name": PLUGIN_NAME,
    "version": PLUGIN_VERSION,
    "author": PLUGIN_AUTHOR,
    "description": "Service-plugin för PDF-generering med multi-OS support",
    "copyright": COPYRIGHT,
    "has_tab": False,
    "is_service": True,
    "tab_priority": PLUGIN_PRIORITY,
    "priority": PLUGIN_PRIORITY,
    "requires": ["certificates"],
    "optional": [],
    "platforms": ["windows", "darwin", "linux"],
    "min_python": "3.12",
    "events": {
        "emits": [
            "generator.ready",
            "generator.settings_changed",
            "notification",
            "error.occurred",
        ],
        "listens_to": [
            "settings.changed",
            "app.closing",
            "app.shutdown",
            "certificates.imported",
            "plugin_manager.ready",
        ],
    },
}


try:
    from .plugin import Plugin

    with _state_lock:
        _fully_loaded = True
    _logger.debug("[Generator] plugin.py laddad framgångsrikt v%s", __version__)
except Exception as exc:
    error_message = f"{type(exc).__name__}: {exc}"
    with _state_lock:
        _load_errors.append(error_message)
        _fully_loaded = False

    _logger.critical(
        "[Generator] plugin.py kunde inte importeras: %s",
        exc,
        exc_info=True,
    )

    class Plugin:
        """Fallback-plugin vid importfel."""

        name: str = PLUGIN_NAME
        version: str = PLUGIN_VERSION
        description: str = "⚠ Generator-tjänst ej tillgänglig"
        priority: int = PLUGIN_PRIORITY
        has_tab: bool = False
        is_service: bool = True

        def __init__(self) -> None:
            self._context: Any = None
            self._log = _logger

        def on_load(self, context: Any) -> None:
            self._context = context
            with suppress_get_logger(context, "GeneratorFallback") as logger:
                if logger is not None:
                    self._log = logger

            self._log.error("[Generator] Fallback laddad: %s", error_message)
            self._emit(
                "error.occurred",
                {
                    "source": "generator",
                    "message": f"Generator kunde inte laddas: {error_message}",
                    "level": "error",
                },
            )

        def setup(self, context: Any) -> None:
            self.on_load(context)

        def on_unload(self) -> None:
            self._context = None

        def on_close(self) -> None:
            self.on_unload()

        def get_tab(self) -> Any | None:
            return None

        def get_statistics(self) -> dict[str, Any]:
            return {
                "available": False,
                "fallback": True,
                "fully_loaded": False,
                "load_errors": get_load_errors(),
                "version": self.version,
            }

        def _emit(self, event_name: str, data: dict[str, Any]) -> None:
            if self._context is None:
                return

            try:
                emit_event = getattr(self._context, "emit_event", None)
                if callable(emit_event):
                    emit_event(event_name, data)
                    return

                event_bus = getattr(self._context, "event_bus", None)
                emit = getattr(event_bus, "emit", None)
                if callable(emit):
                    emit(event_name, data)
            except Exception:
                self._log.exception("[Generator] Fallback kunde inte emit:a %s", event_name)


class suppress_get_logger:
    """Liten context manager för säker context.get_logger utan silent exception."""

    def __init__(self, context: Any, name: str) -> None:
        self._context = context
        self._name = name
        self._logger: logging.Logger | None = None

    def __enter__(self) -> logging.Logger | None:
        try:
            get_logger = getattr(self._context, "get_logger", None)
            if callable(get_logger):
                self._logger = get_logger(self._name)
        except Exception:
            _logger.debug("[Generator] Kunde inte hämta context logger", exc_info=True)
        return self._logger

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        return False


def get_plugin_info() -> dict[str, Any]:
    """Returnera kopia av PLUGIN_INFO med aktuell laddningsstatus."""
    with _state_lock:
        fully_loaded = _fully_loaded
        load_errors = list(_load_errors)

    return {
        **PLUGIN_INFO,
        "plugin_class": Plugin,
        "fully_loaded": fully_loaded,
        "load_errors": load_errors,
    }


def get_version() -> str:
    """Returnera aktuell version."""
    return PLUGIN_VERSION


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    year = datetime.now().year
    return (
        f"© Rickard Längkvist {_START_YEAR}–{year}"
        if year > _START_YEAR
        else f"© Rickard Längkvist {_START_YEAR}"
    )


def is_fully_loaded() -> bool:
    """Returnera True om plugin.py laddades utan fel."""
    with _state_lock:
        return _fully_loaded


def get_load_errors() -> list[str]:
    """Returnera lista med laddningsfel."""
    with _state_lock:
        return list(_load_errors)


__all__: list[str] = [
    "Plugin",
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "PLUGIN_AUTHOR",
    "PLUGIN_PRIORITY",
    "COPYRIGHT",
    "PLUGIN_INFO",
    "__version__",
    "__author__",
    "get_plugin_info",
    "get_version",
    "get_copyright",
    "is_fully_loaded",
    "get_load_errors",
]