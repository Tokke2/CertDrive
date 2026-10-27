from __future__ import annotations

# plugins/certificates/services.py
# Sökväg: Intygsgenerator/plugins/certificates/services.py
# Intygsgenerator — PDF-konvertering, PDF-sammanslagning, räknare och
# intygsgenerering för Certificates-pluginet.
#
# Modeller, validering och DOCX-placeholderhantering ligger i engine.py. Denna
# fil innehåller enbart I/O-tunga tjänster som körs från QRunnable i plugin.py.
#
# Version: 10.2.0
# © Rickard Längkvist 2025–2026
#
# Changelog 10.2.0 (förbättringsrunda, order 2026-08-26):
#   H1  LibreOffice-detektering via glob-mönster (framtidssäker, inga fasta versionslistor)
#   H5  Konverterarlås frigörs mellan retry-sömner (backoff blockerar inte andra jobb)
#   H6  Valfri EventBus-emitter: certificate.progress (percent/message/template)
#   H7  MergeReport: hoppade bilagor rapporteras i stället för att smygas förbi
#   H8  _unique_path med övre gräns (UNIQUE_PATH_MAX) + fellogg
#   +   Tidig målvalidering (målmapp skapas före första försöket)
#   +   merge_detailed() med MergeReport; merge() behåller kompatibel signatur
#   +   pypdf/PyPDF2-sammanslagning förenad till en implementation (ingen dubbel kod)
#   Publikt API från 10.1.0 är fullt bakåtkompatibelt.

import contextlib
import fnmatch
import glob
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .engine import (
    FOLDER_CONFIG,
    BatchResult,
    ConversionMethod,
    GenerationResult,
    PlaceholderParser,
    PlatformHelper,
    Template,
    safe_filename,
)

__version__: str = "10.3.0"
__all__: list[str] = [
    "CONVERT_TIMEOUT_DEFAULT",
    "CONVERT_TIMEOUT_MIN",
    "CONVERT_TIMEOUT_MAX",
    "RETRY_ATTEMPTS",
    "RETRY_BACKOFF_FACTOR",
    "RETRY_BASE_DELAY",
    "UNIQUE_PATH_MAX",
    "MergeReport",
    "PDFConverter",
    "PDFMerger",
    "CounterManager",
    "CertificateGenerator",
]

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

CONVERT_TIMEOUT_DEFAULT: int = 120
CONVERT_TIMEOUT_MIN: int = 30
CONVERT_TIMEOUT_MAX: int = 600
RETRY_ATTEMPTS: int = 3
RETRY_BACKOFF_FACTOR: float = 2.0
RETRY_BASE_DELAY: float = 1.0
UNIQUE_PATH_MAX: int = 1000

_logger: logging.Logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MergeReport:
    """Detaljerat resultat av en PDF-sammanslagning (H7)."""

    success: bool = False
    pages: int = 0
    skipped: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def skipped_names(self) -> list[str]:
        return [path.name for path in self.skipped]


class PDFConverter:
    """Konvertera DOCX till PDF med cachad metoddetektion och begränsad retry.

    Kedja enligt §7: LibreOffice → docx2pdf → Word COM (Windows) → unoconv →
    AbiWord. Detekteringen är TTL-cachad; LibreOffice hittas via glob-mönster
    (H1) så att nya versioner hittas utan kodändring.
    """

    _lo_cache: Path | None = None
    _lo_cache_timestamp: float = 0.0
    _lo_cache_ttl_seconds: float = 300.0
    _lo_cache_lock = threading.RLock()

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or _logger
        self._lock = threading.RLock()
        self._method = ConversionMethod.NONE
        self._libreoffice_path: Path | None = None
        self.refresh()

    @property
    def is_available(self) -> bool:
        return self._method is not ConversionMethod.NONE

    @property
    def method(self) -> ConversionMethod:
        return self._method

    @property
    def method_name(self) -> str:
        return self._method.display_name

    def refresh(self) -> None:
        """Uppdatera metoddetektering; LibreOffice-sökningen är TTL-cachad."""
        with self._lock:
            self._libreoffice_path = self._find_libreoffice_cached()
            if self._libreoffice_path is not None:
                self._method = ConversionMethod.LIBREOFFICE
                return
            if self._has_docx2pdf():
                self._method = ConversionMethod.DOCX2PDF
                return
            if PlatformHelper.is_windows() and self._has_word_com():
                self._method = ConversionMethod.COMTYPES
                return
            if shutil.which("unoconv"):
                self._method = ConversionMethod.UNOCONV
                return
            if shutil.which("abiword"):
                self._method = ConversionMethod.ABIWORD
                return
            self._method = ConversionMethod.NONE

    def convert(
        self,
        source: Path,
        destination: Path | None = None,
        timeout: int = CONVERT_TIMEOUT_DEFAULT,
    ) -> Path | None:
        """Konvertera med max tre försök och exponentiell väntan mellan försöken.

        Retry-sömnen sker UTANFÖR konverterarlåset (H5) så att backoff inte
        blockerar andra konverteringsjobb på samma konverterarinstans.
        """
        source_path = Path(source)
        target_path = Path(destination) if destination is not None else source_path.with_suffix(".pdf")
        if not source_path.is_file():
            self._log.error("[PDFConverter] DOCX saknas: %s", source_path)
            return None
        if not self.is_available:
            self._log.error("[PDFConverter] Ingen tillgänglig konverterare")
            return None
        try:
            safe_timeout = max(CONVERT_TIMEOUT_MIN, min(CONVERT_TIMEOUT_MAX, int(timeout)))
        except (TypeError, ValueError):
            safe_timeout = CONVERT_TIMEOUT_DEFAULT

        # Tidig målvalidering: skapa målmappen före första försöket så att
        # inget försök går förlorat på grund av saknad katalog.
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._log.error("[PDFConverter] Kunde inte skapa målmapp %s: %s", target_path.parent, exc)
            return None

        for attempt in range(RETRY_ATTEMPTS):
            with self._lock:
                result = self._convert_once(source_path, target_path, safe_timeout)
            if result is not None and result.is_file() and result.stat().st_size > 0:
                return result
            if attempt < RETRY_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (RETRY_BACKOFF_FACTOR ** attempt)
                self._log.warning(
                    "[PDFConverter] Försök %d/%d misslyckades; nytt försök om %.1f s",
                    attempt + 1,
                    RETRY_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)  # H5: sömn utanför låset
        self._log.error("[PDFConverter] Alla konverteringsförsök misslyckades")
        return None

    def _convert_once(self, source: Path, destination: Path, timeout: int) -> Path | None:
        if self._method is ConversionMethod.LIBREOFFICE:
            return self._convert_libreoffice(source, destination, timeout)
        if self._method is ConversionMethod.DOCX2PDF:
            return self._convert_docx2pdf(source, destination, timeout)
        if self._method is ConversionMethod.COMTYPES:
            return self._convert_word_com(source, destination, timeout)
        if self._method is ConversionMethod.UNOCONV:
            return self._convert_unoconv(source, destination, timeout)
        if self._method is ConversionMethod.ABIWORD:
            return self._convert_abiword(source, destination, timeout)
        return None

    @classmethod
    def _find_libreoffice_cached(cls) -> Path | None:
        with cls._lo_cache_lock:
            now = time.monotonic()
            if (
                cls._lo_cache is not None
                and now - cls._lo_cache_timestamp < cls._lo_cache_ttl_seconds
                and cls._lo_cache.is_file()
            ):
                return cls._lo_cache
            cls._lo_cache = cls._find_libreoffice()
            cls._lo_cache_timestamp = now
            return cls._lo_cache

    @staticmethod
    def _first_glob(patterns: list[str]) -> Path | None:
        """Returnera första existerande träffen för glob-mönster (H1)."""
        for pattern in patterns:
            for match in sorted(glob.glob(pattern)):
                candidate = Path(match)
                if candidate.is_file():
                    return candidate
        return None

    @classmethod
    def _find_libreoffice(cls) -> Path | None:
        """Hitta soffice utan dyr rekursiv filsystemssökning.

        H1: glob-mönster i stället för fast versionslista — hittar alla
        framtida LibreOffice-versioner utan kodändring.
        """
        if PlatformHelper.is_windows():
            bases: list[str] = [
                base
                for base in (
                    os.environ.get("PROGRAMFILES", ""),
                    os.environ.get("PROGRAMFILES(X86)", ""),
                    os.environ.get("PROGRAMW6432", ""),
                    r"C:\Program Files",
                    r"C:\Program Files (x86)",
                )
                if base
            ]
            patterns: list[str] = []
            for base in dict.fromkeys(bases):  # dedup, bevara ordning
                patterns.append(os.path.join(base, "LibreOffice*", "program", "soffice.exe"))
                patterns.append(os.path.join(base, "LibreOffice*", "program", "soffice.bin"))
                # 10.3.0: multi-office — OpenOffice har samma soffice-CLI.
                patterns.append(os.path.join(base, "OpenOffice*", "program", "soffice.exe"))
                patterns.append(os.path.join(base, "OpenOffice*", "program", "soffice.bin"))
            found = cls._first_glob(patterns)
            if found is not None:
                return found
            which_found = shutil.which("soffice.exe") or shutil.which("libreoffice.exe")
            return Path(which_found) if which_found else None

        if PlatformHelper.is_macos():
            found = cls._first_glob(
                [
                    "/Applications/LibreOffice*.app/Contents/MacOS/soffice",
                    str(Path.home() / "Applications" / "LibreOffice*.app" / "Contents" / "MacOS" / "soffice"),
                    # 10.3.0: multi-office — OpenOffice på macOS.
                    "/Applications/OpenOffice.app/Contents/MacOS/soffice",
                    str(Path.home() / "Applications" / "OpenOffice.app" / "Contents" / "MacOS" / "soffice"),
                ]
            )
            if found is not None:
                return found
            which_found = shutil.which("soffice") or shutil.which("libreoffice")
            return Path(which_found) if which_found else None

        which_found = (
            shutil.which("libreoffice") or shutil.which("soffice")
            or shutil.which("openoffice")  # 10.3.0: multi-office
        )
        if which_found:
            return Path(which_found)
        return cls._first_glob(
            [
                "/usr/bin/libreoffice",
                "/usr/bin/soffice",
                "/usr/local/bin/libreoffice",
                "/usr/local/bin/soffice",
                "/snap/bin/libreoffice",
                "/opt/libreoffice*/program/soffice",
                "/usr/lib/libreoffice/program/soffice",
                "/opt/openoffice*/program/soffice",
                "/usr/lib/openoffice/program/soffice",
            ]
        )

    @staticmethod
    def _has_docx2pdf() -> bool:
        try:
            import docx2pdf  # noqa: F401

            return True
        except ImportError:
            return False

    @staticmethod
    def _has_word_com() -> bool:
        try:
            import win32com.client  # noqa: F401

            return True
        except ImportError:
            return False

    def _convert_libreoffice(self, source: Path, destination: Path, timeout: int) -> Path | None:
        if self._libreoffice_path is None:
            return None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="intyg_lo_") as raw_directory:
                work_directory = Path(raw_directory)
                profile_directory = work_directory / "profile"
                output_directory = work_directory / "output"
                profile_directory.mkdir()
                output_directory.mkdir()
                command = [
                    str(self._libreoffice_path),
                    f"-env:UserInstallation={profile_directory.as_uri()}",
                    "--headless",
                    "--invisible",
                    "--nologo",
                    "--nofirststartwizard",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output_directory),
                    str(source),
                ]
                result = subprocess.run(
                    command,
                    capture_output=True,
                    check=False,
                    timeout=timeout,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                expected = output_directory / source.with_suffix(".pdf").name
                if result.returncode != 0 or not expected.is_file():
                    self._log.warning("[PDFConverter] LibreOffice: %s", result.stderr.strip())
                    return None
                temporary = destination.with_suffix(f"{destination.suffix}.tmp")
                shutil.copy2(expected, temporary)
                temporary.replace(destination)
                return destination
        except subprocess.TimeoutExpired:
            self._log.error("[PDFConverter] LibreOffice överskred timeout")
        except Exception:
            self._log.exception("[PDFConverter] LibreOffice-konvertering misslyckades")
        return None

    def _convert_docx2pdf(self, source: Path, destination: Path, timeout: int) -> Path | None:
        result: dict[str, Exception | None] = {"error": None}

        def convert() -> None:
            try:
                import docx2pdf

                destination.parent.mkdir(parents=True, exist_ok=True)
                docx2pdf.convert(str(source), str(destination))
            except Exception as exc:
                result["error"] = exc

        worker = threading.Thread(target=convert, daemon=True, name="docx2pdf-convert")
        worker.start()
        worker.join(timeout=timeout)
        if worker.is_alive():
            self._log.error("[PDFConverter] docx2pdf överskred timeout")
            return None
        if result["error"] is not None:
            self._log.warning("[PDFConverter] docx2pdf: %s", result["error"])
            return None
        return destination if destination.is_file() else None

    def _convert_word_com(self, source: Path, destination: Path, timeout: int) -> Path | None:
        if not PlatformHelper.is_windows():
            return None
        result: dict[str, Exception | None] = {"error": None}

        def convert() -> None:
            word: Any | None = None
            document: Any | None = None
            initialized = False
            try:
                try:
                    import pythoncom

                    pythoncom.CoInitialize()
                    initialized = True
                except ImportError:
                    pass
                import win32com.client

                destination.parent.mkdir(parents=True, exist_ok=True)
                word = win32com.client.DispatchEx("Word.Application")
                word.Visible = False
                document = word.Documents.Open(str(source.resolve()))
                document.SaveAs(str(destination.resolve()), FileFormat=17)
            except Exception as exc:
                result["error"] = exc
            finally:
                if document is not None:
                    with contextlib.suppress(Exception):
                        document.Close(False)
                if word is not None:
                    with contextlib.suppress(Exception):
                        word.Quit()
                if initialized:
                    with contextlib.suppress(Exception):
                        pythoncom.CoUninitialize()

        worker = threading.Thread(target=convert, daemon=True, name="word-com-convert")
        worker.start()
        worker.join(timeout=timeout)
        if worker.is_alive() or result["error"] is not None:
            self._log.warning("[PDFConverter] Word COM-konvertering misslyckades eller nådde timeout")
            return None
        return destination if destination.is_file() else None

    def _convert_unoconv(self, source: Path, destination: Path, timeout: int) -> Path | None:
        return self._run_converter_command(
            ["unoconv", "-f", "pdf", "-o", str(destination), str(source)],
            destination,
            timeout,
        )

    def _convert_abiword(self, source: Path, destination: Path, timeout: int) -> Path | None:
        return self._run_converter_command(
            ["abiword", "--to=pdf", f"--to-name={destination}", str(source)],
            destination,
            timeout,
        )

    def _run_converter_command(self, command: list[str], destination: Path, timeout: int) -> Path | None:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
            if result.returncode == 0 and destination.is_file():
                return destination
            self._log.warning("[PDFConverter] Kommando misslyckades: %s", command[0])
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._log.warning("[PDFConverter] %s", exc)
        return None


class PDFMerger:
    """Slå ihop PDF-filer atomiskt med pypdf som primär motor.

    merge() behåller den kompatibla signaturen (bool, sidantal). merge_detailed()
    returnerar MergeReport (H7) som även visar hoppade bilagor.
    """

    @staticmethod
    def merge(files: list[Path], destination: Path, log: logging.Logger | None = None) -> tuple[bool, int]:
        report = PDFMerger.merge_detailed(files, destination, log)
        return report.success, report.pages

    @staticmethod
    def merge_detailed(files: list[Path], destination: Path, log: logging.Logger | None = None) -> MergeReport:
        logger = log or _logger
        report = MergeReport()
        valid = [
            Path(path)
            for path in files
            if Path(path).is_file() and Path(path).suffix.casefold() == ".pdf"
        ]
        invalid = [Path(path) for path in files if Path(path) not in valid]
        for path in invalid:
            report.skipped.append(Path(path))
            report.warnings.append(f"Icke-PDF eller saknad fil hoppades över: {path}")
            logger.warning("[PDFMerger] Icke-PDF eller saknad fil hoppades över: %s", path)
        if not valid:
            logger.error("[PDFMerger] Inga giltiga PDF-filer att slå ihop")
            return report
        if len(valid) == 1:
            return PDFMerger._copy_single(valid[0], Path(destination), logger, report)
        return PDFMerger._merge_with_engines(valid, Path(destination), logger, report)

    @staticmethod
    def _copy_single(
        source: Path,
        destination: Path,
        log: logging.Logger,
        report: MergeReport,
    ) -> MergeReport:
        temporary: Path | None = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".pdf", delete=False) as handle:
                temporary = Path(handle.name)
            shutil.copy2(source, temporary)
            temporary.replace(destination)
            report.success = True
            report.pages = PDFMerger.get_page_count(destination)
            return report
        except Exception:
            log.exception("[PDFMerger] Kunde inte kopiera enskild PDF")
            return report
        finally:
            if temporary is not None and temporary.exists():
                with contextlib.suppress(OSError):
                    temporary.unlink()

    @staticmethod
    def _merge_with_engines(
        files: list[Path],
        destination: Path,
        log: logging.Logger,
        report: MergeReport,
    ) -> MergeReport:
        """Prova pypdf, sedan PyPDF2 — en gemensam implementation (10.2.0)."""
        engines: list[tuple[str, Any, Any]] = []
        try:
            from pypdf import PdfReader as PypdfReader, PdfWriter as PypdfWriter

            engines.append(("pypdf", PypdfReader, PypdfWriter))
        except ImportError:
            pass
        try:
            from PyPDF2 import PdfReader as PyPDF2Reader, PdfWriter as PyPDF2Writer

            engines.append(("PyPDF2", PyPDF2Reader, PyPDF2Writer))
        except ImportError:
            pass
        if not engines:
            log.error("[PDFMerger] Varken pypdf eller PyPDF2 är tillgänglig")
            return report

        engine_name, reader_cls, writer_cls = engines[0]
        result = PDFMerger._merge_with_reader_writer(files, destination, log, report, reader_cls, writer_cls)
        if result.success or result.pages > 0:
            return result
        # Primärmotorn misslyckades helt — prova fallbackmotorn
        if len(engines) > 1:
            fallback_name, fb_reader, fb_writer = engines[1]
            log.warning("[PDFMerger] %s misslyckades; provar %s", engine_name, fallback_name)
            retry_report = MergeReport(skipped=list(report.skipped), warnings=list(report.warnings))
            return PDFMerger._merge_with_reader_writer(files, destination, log, retry_report, fb_reader, fb_writer)
        return result

    @staticmethod
    def _merge_with_reader_writer(
        files: list[Path],
        destination: Path,
        log: logging.Logger,
        report: MergeReport,
        reader_cls: Any,
        writer_cls: Any,
    ) -> MergeReport:
        temporary: Path | None = None
        try:
            writer = writer_cls()
            pages = 0
            for file_path in files:
                try:
                    reader = reader_cls(str(file_path))
                except Exception as exc:
                    report.skipped.append(file_path)
                    report.warnings.append(f"Oläsbar PDF hoppades över: {file_path.name} ({exc})")
                    log.warning("[PDFMerger] Oläsbar PDF hoppades över: %s (%s)", file_path.name, exc)
                    continue
                if getattr(reader, "is_encrypted", False):
                    try:
                        reader.decrypt("")
                    except Exception:
                        report.skipped.append(file_path)
                        report.warnings.append(f"Krypterad PDF hoppades över: {file_path.name}")
                        log.warning("[PDFMerger] Krypterad PDF hoppades över: %s", file_path.name)
                        continue
                for page in reader.pages:
                    writer.add_page(page)
                    pages += 1
            if pages == 0:
                return report
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".pdf", delete=False) as handle:
                temporary = Path(handle.name)
                writer.write(handle)
            temporary.replace(destination)
            report.success = True
            report.pages = pages
            return report
        except Exception:
            log.exception("[PDFMerger] PDF-sammanslagning misslyckades")
            return report
        finally:
            if temporary is not None and temporary.exists():
                with contextlib.suppress(OSError):
                    temporary.unlink()

    @staticmethod
    def get_page_count(path: Path) -> int:
        target = Path(path)
        if not target.is_file() or target.stat().st_size == 0:
            return 0
        for module_name in ("pypdf", "PyPDF2"):
            try:
                module = __import__(module_name, fromlist=["PdfReader"])
                reader = module.PdfReader(str(target))
                if getattr(reader, "is_encrypted", False):
                    reader.decrypt("")
                return len(reader.pages)
            except Exception:
                continue
        return 0


class CounterManager:
    """Trådsäker räknare med ConfigManager-stöd och atomisk JSON-fallback."""

    _CONFIG_KEY: str = "certificates.counters"
    _DEFAULT_FILE: str = "counters.json"

    def __init__(self, config_manager: Any = None, config_path: Path | None = None) -> None:
        self._config_manager = config_manager
        self._path = Path(config_path) if config_path is not None else Path(__file__).resolve().parent.parent.parent / FOLDER_CONFIG / self._DEFAULT_FILE
        self._lock = threading.RLock()
        self._values: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        raw: Any = None
        getter = getattr(self._config_manager, "get", None)
        if callable(getter):
            try:
                raw = getter(self._CONFIG_KEY, {})
            except Exception:
                _logger.exception("[CounterManager] Kunde inte läsa ConfigManager")
        if (not isinstance(raw, dict) or not raw) and self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                _logger.exception("[CounterManager] Kunde inte läsa %s", self._path)
        values = raw.get("counters", raw) if isinstance(raw, dict) else {}
        if isinstance(values, dict):
            self._values = self._coerce(values)

    @staticmethod
    def _coerce(values: dict[str, Any]) -> dict[str, int]:
        result: dict[str, int] = {}
        for key, value in values.items():
            if isinstance(value, bool):
                continue
            try:
                result[str(key)] = max(0, int(value))
            except (TypeError, ValueError):
                continue
        return result

    def get_current(self, name: str) -> int:
        with self._lock:
            return self._values.get(str(name), 0)

    def get_next(self, name: str, start: int = 1) -> int:
        try:
            minimum = max(1, int(start))
        except (TypeError, ValueError):
            minimum = 1
        with self._lock:
            return max(self._values.get(str(name), 0) + 1, minimum)

    def increment(self, name: str, start: int = 1) -> int:
        with self._lock:
            value = self.get_next(name, start)
            self._values[str(name)] = value
            self._save_locked()
            return value

    def set(self, name: str, value: int) -> None:
        if isinstance(value, bool):
            raise ValueError("Räknarvärde måste vara ett heltal")
        with self._lock:
            self._values[str(name)] = max(0, int(value))
            self._save_locked()

    def reset(self, name: str) -> None:
        self.set(name, 0)

    def get_all(self) -> dict[str, int]:
        with self._lock:
            return dict(self._values)

    def get_total(self) -> int:
        with self._lock:
            return sum(self._values.values())

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        setter = getattr(self._config_manager, "set", None)
        if callable(setter):
            try:
                setter(self._CONFIG_KEY, dict(self._values), notify=False)
                return
            except TypeError:
                try:
                    setter(self._CONFIG_KEY, dict(self._values))
                    return
                except Exception:
                    _logger.exception("[CounterManager] Kunde inte spara ConfigManager")
            except Exception:
                _logger.exception("[CounterManager] Kunde inte spara ConfigManager")
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps({"counters": self._values}, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError:
            _logger.exception("[CounterManager] Kunde inte spara %s", self._path)
        finally:
            if temporary.exists():
                with contextlib.suppress(OSError):
                    temporary.unlink()


class CertificateGenerator:
    """Skapa PDF-intyg från Template, CounterManager och PDFConverter.

    H6: optional `event_emitter` får `certificate.progress`-event med
    {template, percent, message} — frikopplat från EventBus i övrigt.
    """

    def __init__(
        self,
        out_base: Path,
        counters: CounterManager,
        converter: PDFConverter,
        log: logging.Logger | None = None,
        event_emitter: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._out_base = Path(out_base)
        self._counters = counters
        self._converter = converter
        self._log = log or _logger
        self._event_emitter = event_emitter
        self._lock = threading.RLock()

    def _emit_event(self, event_name: str, data: dict[str, Any]) -> None:
        if self._event_emitter is None:
            return
        try:
            self._event_emitter(event_name, data)
        except Exception:
            self._log.exception("[CertificateGenerator] Event-emitter misslyckades för %s", event_name)

    def generate(
        self,
        template: Template,
        values: dict[str, str],
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> GenerationResult:
        started = time.perf_counter()
        template_name = getattr(template, "name", "")

        def progress(message: str, percent: int) -> None:
            clamped = max(0, min(100, percent))
            if progress_callback is not None:
                try:
                    progress_callback(message, clamped)
                except Exception:
                    self._log.exception("[CertificateGenerator] Progress-callback misslyckades")
            self._emit_event(
                "certificate.progress",
                {"template": template_name, "percent": clamped, "message": message},
            )

        with self._lock:
            try:
                if not template.is_valid:
                    return GenerationResult(False, error=f"Mallen '{template_name}' är ogiltig", template_name=template_name)
                if not self._converter.is_available:
                    return GenerationResult(False, error="Ingen PDF-konverterare är tillgänglig", template_name=template_name)

                progress("Hämtar löpnummer…", 10)
                number = self._counters.increment(template_name, start=template.config.counter_start)
                now = datetime.now()
                replacements = self._build_replacements(values, number, now)

                with tempfile.TemporaryDirectory(prefix="intyg_") as raw_directory:
                    work_directory = Path(raw_directory)
                    temporary_docx = work_directory / "intyg.docx"
                    temporary_pdf = work_directory / "intyg.pdf"
                    progress("Fyller DOCX-mall…", 30)
                    if not PlaceholderParser.replace_in_docx(template.docx_file, temporary_docx, replacements):
                        return GenerationResult(False, error="Kunde inte fylla DOCX-mallen", template_name=template_name)
                    progress("Konverterar till PDF…", 60)
                    if self._converter.convert(temporary_docx, temporary_pdf) is None:
                        return GenerationResult(
                            False,
                            error=f"PDF-konvertering misslyckades ({self._converter.method_name})",
                            template_name=template_name,
                        )
                    progress("Slår ihop bilagor…", 82)
                    output_directory = self._out_base / template_name
                    output_directory.mkdir(parents=True, exist_ok=True)
                    filename = self._build_filename(template, values, number, now)
                    output_path = self._unique_path(output_directory / filename)
                    merge_report = PDFMerger.merge_detailed(
                        [temporary_pdf, *template.get_ordered_attachments()],
                        output_path,
                        self._log,
                    )
                    if not merge_report.success:
                        return GenerationResult(False, error="PDF-sammanslagning misslyckades", template_name=template_name)
                    duration = time.perf_counter() - started
                    progress("Klart", 100)
                    result = GenerationResult(
                        True,
                        output_path=output_path,
                        number=number,
                        filename=output_path.name,
                        page_count=merge_report.pages,
                        duration=duration,
                        template_name=template_name,
                    )
                    # H7: rapportera hoppade bilagor när resultatobjektet stödjer det
                    if merge_report.skipped:
                        self._log.warning(
                            "[CertificateGenerator] %d bilaga(or) hoppades över för '%s': %s",
                            len(merge_report.skipped),
                            template_name,
                            ", ".join(merge_report.skipped_names),
                        )
                        with contextlib.suppress(AttributeError, TypeError):
                            result.skipped_attachments = merge_report.skipped_names
                    return result
            except Exception as exc:
                self._log.exception("[CertificateGenerator] Generering misslyckades")
                return GenerationResult(
                    False,
                    error=str(exc),
                    duration=time.perf_counter() - started,
                    template_name=template_name,
                )

    def generate_batch(
        self,
        template: Template,
        batch_values: list[dict[str, str]],
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> BatchResult:
        started = time.perf_counter()
        results: list[GenerationResult] = []
        succeeded = 0
        total = len(batch_values)
        for index, values in enumerate(batch_values, start=1):
            def item_progress(message: str, percent: int, row=index) -> None:
                if progress_callback is None or total == 0:
                    return
                progress_callback(f"[{row}/{total}] {message}", int(((row - 1) + percent / 100) * 100 / total))

            result = self.generate(template, values, item_progress)
            results.append(result)
            if result.success:
                succeeded += 1
        return BatchResult(
            total=total,
            succeeded=succeeded,
            failed=total - succeeded,
            results=results,
            duration=time.perf_counter() - started,
        )

    def _build_filename(
        self,
        template: Template,
        values: dict[str, str],
        number: int,
        now: datetime,
    ) -> str:
        vin = self._first_value(values, "VIN", "vin", "Vin", "Chassi", "chassi")
        reg = self._first_value(values, "Reg", "reg", "REG", "Regnr", "regnr", "REGNR")
        context = {
            "number": str(number),
            "nr": str(number),
            "template": template.name,
            "mall": template.name,
            "prefix": template.config.counter_prefix or template.name,
            "vin": vin,
            "VIN": vin,
            "reg": reg,
            "Reg": reg,
            "REG": reg,
            "date": now.strftime("%Y-%m-%d"),
            "datum": now.strftime("%Y-%m-%d"),
        }
        pattern = template.config.filename_pattern or "{number} Intyg {template} {vin} {reg} {date}"
        try:
            stem = pattern.format(**context)
        except (KeyError, ValueError):
            stem = f"{number} Intyg {template.name} {vin} {reg} {now:%Y-%m-%d}"
        filename = safe_filename(f"{stem}.pdf")
        return filename if filename.casefold().endswith(".pdf") else f"{filename}.pdf"

    @staticmethod
    def _unique_path(path: Path) -> Path:
        """Hitta unikt filnamn; stoppar vid UNIQUE_PATH_MAX (H8)."""
        if not path.exists():
            return path
        number = 1
        while number <= UNIQUE_PATH_MAX:
            candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
            if not candidate.exists():
                return candidate
            number += 1
        _logger.error(
            "[CertificateGenerator] Kunde inte hitta unikt filnamn inom %d försök: %s",
            UNIQUE_PATH_MAX,
            path,
        )
        return path.with_name(f"{path.stem}_{int(time.time())}{path.suffix}")

    @staticmethod
    def _first_value(values: dict[str, str], *keys: str) -> str:
        for key in keys:
            value = values.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    @staticmethod
    def _build_replacements(values: dict[str, str], number: int, now: datetime) -> dict[str, str]:
        number_text = str(number)
        automatic = {
            "intyg": number_text,
            "nummer": number_text,
            "number": number_text,
            "nr": number_text,
            "löpnummer": number_text,
            "id": number_text,
            "datum": now.strftime("%Y-%m-%d"),
            "date": now.strftime("%Y-%m-%d"),
            "idag": now.strftime("%Y-%m-%d"),
            "today": now.strftime("%Y-%m-%d"),
            "år_nu": str(now.year),
            "currentyear": str(now.year),
            "månad": now.strftime("%m"),
            "month": now.strftime("%m"),
            "dag": now.strftime("%d"),
            "day": now.strftime("%d"),
            "tid": now.strftime("%H:%M"),
            "time": now.strftime("%H:%M"),
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
        replacements: dict[str, str] = {}
        for key, value in automatic.items():
            replacements[key] = value
            replacements[key.capitalize()] = value
            replacements[key.upper()] = value
        for key, value in values.items():
            text = str(value) if value is not None else ""
            replacements[str(key)] = text
            replacements[str(key).casefold()] = text
            replacements[str(key).upper()] = text
        return replacements
