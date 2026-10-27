from __future__ import annotations

"""
Fil:    plugins/settings/workers.py
Sökväg: Intygsgenerator/plugins/settings/workers.py

Intygsgenerator — säkra QRunnable-workers för användarinitierad backup och
återställning. All fil-I/O sker utanför UI-tråden.
"""

import json
import logging
import os
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

__version__: str = "8.8.0"
__all__: list[str] = [
    "BackupOptions",
    "WorkerSignals",
    "BackupWorker",
    "RestoreWorker",
]

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_logger: logging.Logger = logging.getLogger(__name__)
_MAX_LOG_FILE_BYTES: int = 50_000_000
_MAX_RESTORE_FILE_BYTES: int = 500_000_000
_ALLOWED_RESTORE_ROOTS: frozenset[str] = frozenset({"config", "Mallar", "profiles"})


@dataclass(frozen=True, slots=True)
class BackupOptions:
    """Val för en användarinitierad backup."""

    include_logs: bool = False
    include_output: bool = False
    app_version: str = __version__


class WorkerSignals(QObject):
    """Trådsäkra progress- och slutresultatsignaler."""

    progress = Signal(str, int)
    finished = Signal(bool, str)


def _context_path(context: Any, key: str) -> Path | None:
    getter = getattr(context, "get_path", None)
    if not callable(getter):
        return None
    try:
        value = getter(key)
        return Path(value) if value else None
    except Exception:
        _logger.exception("[SETTINGS] Kunde inte hämta context-sökväg %s", key)
        return None


def _iter_files(root: Path, patterns: tuple[str, ...] = ("*",)) -> Iterator[Path]:
    """Iterera säkra vanliga filer utan Word-temp- och dolda filer."""
    if not root.is_dir():
        return
    seen: set[Path] = set()
    for pattern in patterns:
        try:
            candidates = root.rglob(pattern)
        except OSError:
            continue
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
                if resolved in seen or not candidate.is_file():
                    continue
                if any(part.startswith(".") for part in candidate.relative_to(root).parts):
                    continue
                if candidate.name.startswith("~$"):
                    continue
                seen.add(resolved)
                yield candidate
            except (OSError, ValueError):
                continue


def _archive_name(prefix: str, root: Path, file_path: Path) -> str:
    return str(PurePosixPath(prefix) / PurePosixPath(file_path.relative_to(root)))


def _safe_archive_member(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    if path.parts[0] not in _ALLOWED_RESTORE_ROOTS:
        return None
    return path


class BackupWorker(QRunnable):
    """Skapa en atomisk ZIP-backup i en QRunnable."""

    def __init__(
        self,
        backup_path: Path,
        context: Any,
        options: BackupOptions,
        profiles_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._backup_path = Path(backup_path)
        self._context = context
        self._options = options
        self._profiles_path = Path(profiles_path) if profiles_path else None
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        temporary: Path | None = None
        started = time.perf_counter()  # 8.8.0: tidsmätning för logg
        try:
            self._backup_path.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = self._backup_path / f"backup_{timestamp}.zip"
            temporary = backup_file.with_suffix(".zip.tmp")
            groups = self._collect_groups()
            total = max(1, sum(len(files) for _, _, files in groups))
            written = 0
            self.signals.progress.emit("Förbereder backup…", 0)

            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr(
                    "_metadata.json",
                    json.dumps(
                        {
                            "created": datetime.now().isoformat(timespec="seconds"),
                            "version": self._options.app_version,
                            "include_logs": self._options.include_logs,
                            "include_output": self._options.include_output,
                            "platform": os.name,
                            "copyright": COPYRIGHT,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                for prefix, root, files in groups:
                    for file_path in files:
                        archive.write(file_path, _archive_name(prefix, root, file_path))
                        written += 1
                        percent = min(99, int(written * 100 / total))
                        self.signals.progress.emit(f"Säkerhetskopierar {file_path.name}", percent)
            temporary.replace(backup_file)
            self.signals.progress.emit("Backup klar", 100)
            # 8.8.0: strukturerad sw.*-logg med antal filer + ms.
            _logger.info(
                "[SETTINGS] Backup skapad: %s (%d filer)",
                backup_file.name, written,
                extra={"event": "sw.backup_done",
                       "ctx": {"file": backup_file.name, "files": written,
                               "ms": round((time.perf_counter() - started) * 1000, 1)}},
            )
            self.signals.finished.emit(True, f"Backup skapad: {backup_file.name}")
        except Exception as exc:
            _logger.exception(
                "[SETTINGS] Backup misslyckades",
                extra={"event": "sw.backup_failed",
                       "ctx": {"error": f"{type(exc).__name__}: {exc}",
                               "ms": round((time.perf_counter() - started) * 1000, 1)}},
            )
            self.signals.finished.emit(False, str(exc))
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    _logger.warning("[SETTINGS] Kunde inte rensa temporär backup: %s", temporary)

    def _collect_groups(self) -> list[tuple[str, Path, list[Path]]]:
        groups: list[tuple[str, Path, list[Path]]] = []
        config_path = _context_path(self._context, "config")
        templates_path = _context_path(self._context, "templates")
        logs_path = _context_path(self._context, "logs")
        output_path = _context_path(self._context, "output")

        if config_path and config_path.is_dir():
            groups.append(("config", config_path, list(_iter_files(config_path))))
        if templates_path and templates_path.is_dir():
            groups.append(("Mallar", templates_path, list(_iter_files(templates_path))))
        if self._profiles_path and self._profiles_path.is_dir():
            groups.append(("profiles", self._profiles_path, list(_iter_files(self._profiles_path, ("*.json",)))))
        if self._options.include_logs and logs_path and logs_path.is_dir():
            files = [path for path in _iter_files(logs_path, ("*.log", "*.log.*")) if path.stat().st_size <= _MAX_LOG_FILE_BYTES]
            groups.append(("logs", logs_path, files))
        if self._options.include_output and output_path and output_path.is_dir():
            groups.append(("Out", output_path, list(_iter_files(output_path, ("*.pdf",)))))
        return groups


class RestoreWorker(QRunnable):
    """Återställ godkända backupdelar säkert utan Zip Slip-risk."""

    def __init__(
        self,
        backup_file: Path,
        context: Any,
        profiles_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._backup_file = Path(backup_file)
        self._context = context
        self._profiles_path = Path(profiles_path) if profiles_path else None
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        started = time.perf_counter()  # 8.8.0: tidsmätning för logg
        try:
            if not self._backup_file.is_file():
                raise FileNotFoundError(self._backup_file)
            targets = {
                "config": _context_path(self._context, "config"),
                "Mallar": _context_path(self._context, "templates"),
                "profiles": self._profiles_path,
            }
            self.signals.progress.emit("Kontrollerar backup…", 0)
            with zipfile.ZipFile(self._backup_file, "r") as archive:
                members = self._validated_members(archive)
                total = max(1, len(members))
                restored = 0
                for info, safe_path in members:
                    target_root = targets.get(safe_path.parts[0])
                    if target_root is None:
                        continue
                    destination = target_root.joinpath(*safe_path.parts[1:])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source, destination.open("wb") as target:
                        while chunk := source.read(1024 * 1024):
                            target.write(chunk)
                    restored += 1
                    self.signals.progress.emit(f"Återställer {safe_path.name}", min(99, int(restored * 100 / total)))
            self.signals.progress.emit("Återställning klar", 100)
            # 8.8.0: strukturerad sw.*-logg med antal filer + ms.
            _logger.info(
                "[SETTINGS] Återställning klar: %d filer", restored,
                extra={"event": "sw.restore_done",
                       "ctx": {"files": restored,
                               "ms": round((time.perf_counter() - started) * 1000, 1)}},
            )
            self.signals.finished.emit(True, "Återställning klar — starta om programmet")
        except Exception as exc:
            _logger.exception(
                "[SETTINGS] Återställning misslyckades",
                extra={"event": "sw.restore_failed",
                       "ctx": {"error": f"{type(exc).__name__}: {exc}",
                               "ms": round((time.perf_counter() - started) * 1000, 1)}},
            )
            self.signals.finished.emit(False, str(exc))

    @staticmethod
    def _validated_members(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
        valid: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        for info in archive.infolist():
            if info.is_dir() or info.file_size > _MAX_RESTORE_FILE_BYTES:
                continue
            safe_path = _safe_archive_member(info.filename)
            if safe_path is not None:
                valid.append((info, safe_path))
        if not valid:
            raise ValueError("Backupen innehåller inga återställningsbara filer")
        return valid
