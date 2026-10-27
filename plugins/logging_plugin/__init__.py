# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil:    plugins/logging_plugin/__init__.py
Sökväg: Intygsgenerator/plugins/logging_plugin/__init__.py

Intygsgenerator — paketgränssnitt för Logging-pluginet (central loggning).

Exporterar Plugin-klassen som PluginManager förväntar sig, med fail-safe
degraded mode om någon modul inte kan laddas (§21).

Version 9.0.0 (2026-09-21): synkad med plugin.py/core.py/tracer.py/view.py 9.x.
"""

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

__version__: str = "9.2.0"
__author__: str = "Rickard Längkvist"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

PLUGIN_NAME: str = "Logger"
PLUGIN_PRIORITY: int = 1  # §9.2: Logging måste laddas först.

_logger: logging.Logger = logging.getLogger(__name__)
_load_lock = threading.RLock()

CORE_LOADED: bool = False
TRACER_LOADED: bool = False
VIEW_LOADED: bool = False
PLUGIN_LOADED: bool = False
CORE_ERROR: str = ""
TRACER_ERROR: str = ""
VIEW_ERROR: str = ""
PLUGIN_ERROR: str = ""

try:
    from .core import (
        COPYRIGHT as _CORE_COPYRIGHT,
        SESSION_ID,
        LogEntry,
        LogStorage,
        QuickFilter,
        SmartRotatingHandler,
        DetailedFormatter,
        ColorFormatter,
        archive_old_logs,
        export_text,
        export_json,
        export_csv,
        export_html,
        export_markdown,
    )

    CORE_LOADED = True
except Exception as _exc:  # pragma: no cover — fail-safe-gren
    CORE_ERROR = f"{type(_exc).__name__}: {_exc}"
    _logger.error("logging_plugin.core kunde inte laddas: %s", CORE_ERROR)

try:
    from .tracer import FileOperation, FileSystemTracer

    TRACER_LOADED = True
except Exception as _exc:  # pragma: no cover — fail-safe-gren
    TRACER_ERROR = f"{type(_exc).__name__}: {_exc}"
    _logger.error("logging_plugin.tracer kunde inte laddas: %s", TRACER_ERROR)

try:
    from .view import LoggerView

    VIEW_LOADED = True
except Exception as _exc:  # pragma: no cover — fail-safe-gren
    VIEW_ERROR = f"{type(_exc).__name__}: {_exc}"
    _logger.error("logging_plugin.view kunde inte laddas: %s", VIEW_ERROR)

try:
    from .plugin import Plugin

    PLUGIN_LOADED = True
except Exception as _exc:  # pragma: no cover — fail-safe-gren
    PLUGIN_ERROR = f"{type(_exc).__name__}: {_exc}"
    _logger.error("logging_plugin.plugin kunde inte laddas: %s", PLUGIN_ERROR)

    class Plugin:  # type: ignore[no-redef]
        """Fail-safe fallback: rapporterar felet synligt utan att krascha appen."""

        name: str = PLUGIN_NAME
        version: str = __version__
        description: str = "Central loggning (degraded mode — modul kunde inte laddas)."
        priority: int = PLUGIN_PRIORITY

        def __init__(self) -> None:
            self._context = None

        def on_load(self, context) -> None:
            self._context = context
            _logger.error("Logging-plugin i degraded mode: %s", PLUGIN_ERROR or CORE_ERROR)

        def on_unload(self) -> None:
            return None

        def get_tab(self) -> "QWidget | None":
            try:
                from PySide6.QtWidgets import QLabel

                details = " | ".join(x for x in (PLUGIN_ERROR, CORE_ERROR, VIEW_ERROR) if x)
                return QLabel(
                    "<b>Logging-pluginet kunde inte laddas.</b><br>"
                    f"Detaljer: {details or 'okänt fel'}<br>"
                    "Appen fortsätter utan central loggning."
                )
            except Exception:
                return None

        def setup(self, context) -> None:
            self.on_load(context)

        def on_close(self) -> None:
            self.on_unload()


def get_metadata() -> dict:
    """Metadata om paketets hälsotillstånd — användbar i diagnostik."""
    return {
        "name": PLUGIN_NAME,
        "version": __version__,
        "priority": PLUGIN_PRIORITY,
        "copyright": COPYRIGHT,
        "core_loaded": CORE_LOADED,
        "tracer_loaded": TRACER_LOADED,
        "view_loaded": VIEW_LOADED,
        "plugin_loaded": PLUGIN_LOADED,
        "errors": {
            "core": CORE_ERROR,
            "tracer": TRACER_ERROR,
            "view": VIEW_ERROR,
            "plugin": PLUGIN_ERROR,
        },
    }


__all__: list[str] = [
    "Plugin",
    "PLUGIN_NAME",
    "PLUGIN_PRIORITY",
    "COPYRIGHT",
    "get_metadata",
    "SESSION_ID",
    "LogEntry",
    "LogStorage",
    "QuickFilter",
    "FileSystemTracer",
    "LoggerView",
]
