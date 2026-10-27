# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil:    plugins/logging_plugin/tracer.py
Sökväg: Intygsgenerator/plugins/logging_plugin/tracer.py

Intygsgenerator — filsystemspårning (kompatibel med plugin.py 9.x).

Varje spårad filoperation rapporteras som ett `FileOperation`-objekt till
callbacken (pluginens `_on_file_operation`): operation, plugin-attribution,
anropare, sökväg och detail.

Version 9.1.0 (2026-09-22) — STOPPAR SJÄLVSPÅRNINGSLOOPEN:
  Problem i 9.0.0: `_caller_info()` använde `inspect.stack()`, som via
  `linecache` läser källfiler → `os.stat` → tracern fångade sina egna
  spår → loggade → JSONWriter skrev → nya filoperationer → …
  Resultat: tusentals "[FS] STAT Linecache …"-rader/timme, loggen drunknade
  och appen blev extremt långsam (bl.a. hann systemfältet aldrig starta).

  Åtgärder:
  1. `_caller_info()` använder `sys._getframe()` — ingen inspect, ingen
     linecache, inga källfilläsningar (mycket snabbare, ingen feedback).
  2. Lågvärdiga operationer (STAT/EXISTS/ISFILE/ISDIR/LSTAT) rapporteras
     ENBART vid fel (SAKNAS/NEKAD) — lyckade anrop loggas inte alls.
  3. Brusfilter utökat: stdlib (sys.prefix/base_prefix/stdlib, site-packages),
     __pycache__, appens egen loggmapp samt moduler som logging/queue/
     threading/json/linecache/inspect/importlib/PySide6 m.fl.
  4. Loggtrådar (JSONWriter/LogProcessor/LogCompressor) spåras inte alls.
  5. Ingen `Path.resolve()` i filtervägen (dyr + gör I/O) — enbart
     strängjämförelse.

Kvar sedan 9.0.0: install/uninstall/restore, stats(), find_similar(),
bounded deque, fail-safe callback.
"""

import builtins
import difflib
import glob as _glob
import os
import sys
import sysconfig
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Deque

__version__: str = "9.1.0"
__all__: list[str] = ["FileOperation", "FileSystemTracer"]

_MAX_RECENT: int = 1_000


def _stdlib_prefixes() -> tuple[str, ...]:
    """Sökvägsprefix för Pythons stdlib/installation (brus)."""
    out: list[str] = []
    for value in (
        getattr(sys, "base_prefix", None),
        getattr(sys, "prefix", None),
        getattr(sys, "exec_prefix", None),
    ):
        if value:
            out.append(str(value).lower())
    try:
        out.append(str(sysconfig.get_paths()["stdlib"]).lower())
    except Exception:
        pass
    return tuple(dict.fromkeys(out))


_STDLIB_PREFIXES: tuple[str, ...] = _stdlib_prefixes()

# Moduler som aldrig ska räknas som "appkod" vid attribution.
_NOISE_MODULE_PREFIXES: tuple[str, ...] = (
    "logging", "logging_plugin", "plugins.logging_plugin", "queue", "threading",
    "json", "linecache", "inspect", "importlib", "zipimport", "codecs",
    "encodings", "traceback", "PySide6", "shiboken", "asyncio", "concurrent",
    "os", "shutil", "tempfile", "copy", "dataclasses", "enum", "typing",
    "pathlib", "glob", "fnmatch", "re", "gzip", "csv", "html", "uuid",
    "datetime", "collections", "contextlib", "io", "builtins", "_bootstrap",
    "_frozen", "zipfile", "platform", "subprocess", "signal", "atexit",
    "weakref", "functools", "itertools", "site", "sysconfig", "gettext",
    "warnings", "abc", "types", "operator", "keyword", "tokenize", "ast",
    "dis", "opcode", "pickle", "struct", "zlib", "bz2", "lzma", "hashlib",
    "secrets", "random", "statistics", "decimal", "fractions", "numbers",
    "math", "cmath", "array", "mmap", "select", "socket", "ssl", "email",
    "http", "urllib", "xml", "sqlite3", "ctypes", "multiprocessing",
)

# Trådar som tillhör loggningen — spåras inte (bryter feedback-loopen).
_LOG_THREAD_NAMES: frozenset[str] = frozenset(
    {"JSONWriter", "LogProcessor", "LogCompressor"}
)

# Operationer som bara rapporteras vid fel (lyckade är brus).
_LOW_VALUE_OPS: frozenset[str] = frozenset(
    {"STAT", "LSTAT", "EXISTS", "ISFILE", "ISDIR"}
)


def _is_noise_module(name: str) -> bool:
    if not name:
        return True
    if name == __name__:
        return True
    for prefix in _NOISE_MODULE_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return True
    return False


@dataclass(slots=True)
class FileOperation:
    """En spårad filoperation."""

    operation: str = ""
    plugin: str = ""
    caller: str = ""
    path: str = ""
    detail: str = ""


class FileSystemTracer:
    """Reversibel filsystemspårare med callback per operation."""

    def __init__(self, callback: Callable[[FileOperation], None]) -> None:
        self._callback = callback
        self._orig: dict[tuple[Any, str], Any] = {}
        self._tls = threading.local()
        self._lock = threading.Lock()
        self._installed = False
        self._logs_dir: Path | None = None
        self._logs_prefix: str = ""
        self._counts: dict[str, int] = {}
        self._recent: Deque[FileOperation] = deque(maxlen=_MAX_RECENT)
        self.miss_count = 0
        self._disabled = False

    @property
    def is_active(self) -> bool:
        """True om spårningen faktiskt är installerad."""
        return bool(self._installed) and not self._disabled

    # ── publik yta ────────────────────────────────────────────────────────

    def install(self, logs_dir: Path | str | None = None) -> None:
        # 6) NÖDSTRÖMBRYTARE: INTYGG_FS_TRACE=0 stänger av filsystemspårningen
        # helt (tyst app, ingen risk för spårnings-loop). Kräver omstart.
        if str(os.environ.get("INTYGG_FS_TRACE", "1")).strip().lower() in {
            "0", "false", "no", "off", "",
        }:
            self._disabled = True
            return
        with self._lock:
            if self._installed:
                return
            if logs_dir is not None:
                self._logs_dir = Path(logs_dir)
                with _suppress_all():
                    self._logs_prefix = str(self._logs_dir.resolve()).lower()
            self._patch_module(builtins, ["open"])
            self._patch_module(os, ["listdir", "scandir", "walk", "stat",
                                    "lstat", "remove", "unlink", "mkdir",
                                    "makedirs"])
            self._patch_module(os.path, ["exists", "isfile", "isdir"])
            self._patch_module(_glob, ["glob", "iglob"])
            for method in ("open", "read_text", "read_bytes", "write_text",
                           "write_bytes", "unlink", "mkdir", "stat", "exists",
                           "is_file", "is_dir", "iterdir", "glob", "rglob"):
                self._patch_classmethod(Path, method)
            self._installed = True

    def uninstall(self) -> None:
        with self._lock:
            for (obj, name), original in self._orig.items():
                with _suppress_all():
                    setattr(obj, name, original)
            self._orig.clear()
            self._installed = False

    # alias för bakåtkompatibilitet med äldre tester/anrop
    restore = uninstall

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "operations": dict(self._counts),
                "misses": self.miss_count,
                "recent": len(self._recent),
            }

    def find_similar(self, path_s: str, limit: int = 4) -> list[str]:
        """'Menade du …?'-förslag för saknade filer (AI-felsökning)."""
        suggestions: list[str] = []
        with _suppress_all():
            parent = Path(path_s).parent
            if parent.is_dir():
                names = [p.name for p in parent.iterdir()][:2000]
                suggestions = difflib.get_close_matches(
                    Path(path_s).name, names, n=limit, cutoff=0.6
                )
        return suggestions

    # ── patchning ─────────────────────────────────────────────────────────

    def _patch_module(self, module: Any, names: list[str]) -> None:
        for name in names:
            original = getattr(module, name, None)
            if original is None or not callable(original):
                continue
            setattr(module, name, self._wrap(original, name.upper()))
            self._orig[(module, name)] = original

    def _patch_classmethod(self, cls: type, name: str) -> None:
        original = getattr(cls, name, None)
        if original is None:
            return
        setattr(cls, name, self._wrap(original, name.upper()))
        self._orig[(cls, name)] = original

    def _wrap(self, fn: Callable[..., Any], operation: str) -> Callable[..., Any]:
        tracer = self

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 4) Loggtrådar spåras inte alls → ingen feedback-loop.
            if threading.current_thread().name in _LOG_THREAD_NAMES:
                return fn(*args, **kwargs)
            depth = getattr(tracer._tls, "depth", 0)
            if depth:  # re-entrancy
                return fn(*args, **kwargs)

            target = args[0] if args else None
            path_s: str | None = None
            if isinstance(target, (str, os.PathLike)):
                with _suppress_all():
                    path_s = os.fspath(target)
            if path_s is None or tracer._is_noise(path_s):
                return fn(*args, **kwargs)

            tracer._tls.depth = depth + 1
            try:
                result = fn(*args, **kwargs)
            except FileNotFoundError as exc:
                tracer._tls.depth = depth
                tracer._report(operation, path_s, f"SAKNAS: {exc}", True)
                raise
            except PermissionError as exc:
                tracer._tls.depth = depth
                tracer._report(operation, path_s, f"NEKAD: {exc}", True)
                raise
            except Exception:
                tracer._tls.depth = depth
                raise
            tracer._tls.depth = depth

            # 2) Lågvärdiga operationer rapporteras bara vid fel.
            if operation in _LOW_VALUE_OPS:
                return result
            tracer._report(operation, path_s, "")
            return result

        wrapper.__name__ = getattr(fn, "__name__", operation)  # type: ignore[attr-defined]
        return wrapper

    # ── filter & rapport ──────────────────────────────────────────────────

    def _is_noise(self, path_s: str) -> bool:
        """3) Brusfilter — enbart strängjämförelse (ingen resolve/I-O)."""
        low = path_s.lower()
        if not low:
            return True
        if "__pycache__" in low or "site-packages" in low:
            return True
        if self._logs_prefix and low.startswith(self._logs_prefix):
            return True
        for prefix in _STDLIB_PREFIXES:
            if prefix and low.startswith(prefix):
                return True
        return False

    @staticmethod
    def _caller_info() -> tuple[str, str]:
        """1) Attribut utan inspect/linecache (sys._getframe)."""
        frame = sys._getframe(1)
        walked = 0
        while frame is not None and walked < 24:
            module = frame.f_globals.get("__name__", "") or ""
            if not _is_noise_module(module):
                parts = module.split(".")
                if len(parts) > 1 and parts[0] in {"plugins", "core"}:
                    plugin = parts[1].capitalize()
                else:
                    plugin = (parts[0] or "?").capitalize()
                try:
                    fname = os.path.basename(frame.f_code.co_filename)
                except Exception:
                    fname = "?"
                return plugin, f"{fname}:{frame.f_lineno}"
            frame = frame.f_back
            walked += 1
        return "?", "?"

    def _report(
        self,
        operation: str,
        path_s: str,
        detail: str,
        count_miss: bool = False,
    ) -> None:
        plugin, caller = self._caller_info()
        op = FileOperation(
            operation=operation,
            plugin=plugin,
            caller=caller,
            path=path_s,
            detail=detail,
        )
        with self._lock:
            self._counts[operation] = self._counts.get(operation, 0) + 1
            self._recent.append(op)
            if count_miss:
                self.miss_count += 1
        try:
            self._callback(op)
        except Exception:
            pass  # fail-safe: spårning får aldrig krascha appen


class _suppress_all:
    """Minimal, snabb contextmanager (undviker import av contextlib i modul)."""

    __slots__ = ()

    def __enter__(self) -> "_suppress_all":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return True
