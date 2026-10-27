from __future__ import annotations
# -*- coding: utf-8 -*-

"""
Fil:       plugins/template_designer/core.py
Sökväg:    Intygsgenerator/plugins/template_designer/core.py
Projekt:   Intygsgenerator
Version:   4.2.1
Ansvar:    Datamodeller, logik och TemplateManager för mall-designer.
           Ren Python-modul — inga UI-beroenden.
Beroenden: python-docx (mjukt), core.infrastructure (mjukt)
© Rickard Längkvist 2025
"""

import hashlib
import json
import logging
import re
import shutil
import threading
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any

# ══════════════════════════════════════════════════════════════════════════════
# METADATA
# ══════════════════════════════════════════════════════════════════════════════

__version__: str = "4.2.2"
__author__: str = "Rickard Längkvist"
__all__: list[str] = [
    "__version__", "COPYRIGHT",
    "FieldType", "ValidationLevel", "TemplateStatus", "UndoActionType",
    "ValidationMessage", "ValidationResult", "FieldDefinition",
    "TemplateConfig", "TemplateInfo", "UndoAction",
    "PlaceholderExtractor", "FieldValidator", "UndoManager",
    "TemplateManager", "PlatformInfo",
    "ToolTip", "ScrollableFrame", "SearchableListbox",
    "StatusBar", "ProgressDialog",
    "RESERVED_NAMES", "DEFAULT_CATEGORIES", "AUTO_FIELDS",
    "PLACEHOLDER_PATTERNS", "FIELD_VALIDATORS",
    "VIN_PATTERN", "VIN_LENGTH",
    "FOLDER_TEMPLATES", "FOLDER_OUTPUT", "FOLDER_CONFIG",
]

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

# ══════════════════════════════════════════════════════════════════════════════
# KONSTANTER
# ══════════════════════════════════════════════════════════════════════════════

FOLDER_TEMPLATES: str = "Mallar"
FOLDER_OUTPUT: str = "Out"
FOLDER_CONFIG: str = "js"          # BUG-007: ej ".js"

VIN_LENGTH: int = 17
VIN_PATTERN: re.Pattern[str] = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
REG_PATTERN: re.Pattern[str] = re.compile(
    r"^[A-ZÅÄÖ]{3}\s?\d{2}[A-ZÅÄÖ0-9]$", re.IGNORECASE
)

PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(
    r"::\s*([A-Za-z0-9_åäöÅÄÖ]+)\s*::", re.IGNORECASE
)
PLACEHOLDER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (PLACEHOLDER_PATTERN, "::{}::"),
]

RESERVED_NAMES: frozenset[str] = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5",
    "LPT1", "LPT2", "LPT3",
    "SYSTEM", "WINDOWS", "TEMP", "TMP",
})

DEFAULT_CATEGORIES: list[str] = [
    "25kW", "35kW", "Passagerare", "Demontering", "Övriga",
]

AUTO_FIELDS: frozenset[str] = frozenset({
    "intyg", "nr", "datum", "idag",
    "år_nu", "currentyear", "månad", "month",
    "dag", "day", "tid", "time", "timestamp",
})

FIELD_VALIDATORS: dict[str, str] = {
    "vin":   r"^[A-HJ-NPR-Z0-9]{17}$",
    "reg":   r"^[A-ZÅÄÖ]{3}\s?\d{2}[A-ZÅÄÖ0-9]$",
    "date":  r"^\d{4}-\d{2}-\d{2}$",
    "email": r"^[^@]+@[^@]+\.[^@]+$",
    "phone": r"^[\d\s\-\+\(\)]{7,}$",
}

_SAFE_FILENAME_RE: re.Pattern[str] = re.compile(r'[<>:"/\\|?*\x00-\x1F]')

# ══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ══════════════════════════════════════════════════════════════════════════════


class FieldType(Enum):
    TEXT = "text"
    DATE = "date"
    VIN = "vin"
    REG = "reg"
    NUMBER = "number"
    EMAIL = "email"
    PHONE = "phone"


class ValidationLevel(Enum):
    OK = auto()
    INFO = auto()
    WARNING = auto()
    ERROR = auto()

    @property
    def icon(self) -> str:
        # Identitetsjämförelse i stället för dict + self.name: undviker
        # RecursionError i Python 3.14:s enum/DynamicClassAttribute-hantering
        # (krasch fångad av centralloggen 2026-09-18) och är snabbare —
        # ingen dict byggs per anrop.
        if self is ValidationLevel.OK:
            return "✅"
        if self is ValidationLevel.INFO:
            return "ℹ️"
        if self is ValidationLevel.WARNING:
            return "⚠️"
        return "❌"


class TemplateStatus(Enum):
    VALID = auto()
    MISSING_DOCX = auto()
    INVALID = auto()
    UNKNOWN = auto()

    @property
    def icon(self) -> str:
        # Identitetsjämförelse — se ValidationLevel.icon: säker mot
        # Python 3.14-enumrekursion och snabbare än dict-per-anrop.
        if self is TemplateStatus.VALID:
            return "📄"
        if self is TemplateStatus.MISSING_DOCX:
            return "⚠️"
        if self is TemplateStatus.INVALID:
            return "❌"
        return "❓"


class UndoActionType(Enum):
    CREATE = auto()
    DELETE = auto()
    RENAME = auto()
    DUPLICATE = auto()
    MODIFY = auto()

# ══════════════════════════════════════════════════════════════════════════════
# DATAKLASSER
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class ValidationMessage:
    level: ValidationLevel
    message: str
    field: str = ""

    def __str__(self) -> str:
        return f"{self.level.icon} {self.message}"


@dataclass
class ValidationResult:
    """Samlad valideringsresultat för en mall."""

    messages: list[ValidationMessage] = field(default_factory=list)

    @property
    def level(self) -> ValidationLevel:
        if not self.messages:
            return ValidationLevel.OK
        levels = [m.level for m in self.messages]
        if ValidationLevel.ERROR in levels:
            return ValidationLevel.ERROR
        if ValidationLevel.WARNING in levels:
            return ValidationLevel.WARNING
        if ValidationLevel.INFO in levels:
            return ValidationLevel.INFO
        return ValidationLevel.OK

    @property
    def is_valid(self) -> bool:
        return self.level != ValidationLevel.ERROR

    def get_summary(self) -> str:
        if not self.messages:
            return "✅ Mall OK"
        errors = sum(1 for m in self.messages if m.level == ValidationLevel.ERROR)
        warnings = sum(
            1 for m in self.messages if m.level == ValidationLevel.WARNING
        )
        parts: list[str] = []
        if errors:
            parts.append(f"{errors} fel")
        if warnings:
            parts.append(f"{warnings} varningar")
        return f"{self.level.icon} {', '.join(parts)}" if parts else "✅ OK"

    def add(
        self, level: ValidationLevel, message: str, field_name: str = ""
    ) -> None:
        self.messages.append(ValidationMessage(level, message, field_name))


@dataclass
class FieldDefinition:
    """Definition av ett fält i en mall."""

    name: str
    label: str = ""
    field_type: FieldType = FieldType.TEXT
    default_value: str = ""
    hint: str = ""
    required: bool = False
    validator: str | None = None

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.name


@dataclass
class TemplateConfig:
    """Konfiguration för en mall (sparas i JSON)."""

    name: str
    description: str = ""
    category: str = "Övriga"
    author: str = ""
    version: str = "1.0"
    tags: list[str] = field(default_factory=list)
    created: str = ""
    modified: str = ""
    field_labels: dict[str, str] = field(default_factory=dict)
    field_types: dict[str, str] = field(default_factory=dict)
    field_hints: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    required_fields: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    attachments_order: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created:
            self.created = datetime.now().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "author": self.author,
            "version": self.version,
            "tags": self.tags,
            "created": self.created,
            "modified": self.modified,
            "field_labels": self.field_labels,
            "field_types": self.field_types,
            "field_hints": self.field_hints,
            "defaults": self.defaults,
            "required_fields": self.required_fields,
            "fields": self.fields,
            "attachments_order": self.attachments_order,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemplateConfig:
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", "Övriga"),
            author=data.get("author", ""),
            version=data.get("version", "1.0"),
            tags=data.get("tags", []),
            created=data.get("created", ""),
            modified=data.get("modified", ""),
            field_labels=data.get("field_labels", {}),
            field_types=data.get("field_types", {}),
            field_hints=data.get("field_hints", {}),
            defaults=data.get("defaults", {}),
            required_fields=data.get("required_fields", []),
            fields=data.get("fields", []),
            attachments_order=data.get("attachments_order", []),
            extra=data.get("extra", {}),
        )

    def save(self, path: Path) -> bool:
        """Sparar config till JSON-fil (atomic write)."""
        try:
            self.modified = datetime.now().isoformat()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
            return True
        except Exception:
            return False


@dataclass
class TemplateInfo:
    """Körbar representation av en mall (läst från disk)."""

    name: str
    folder: Path
    docx_file: Path | None = None
    attachments: list[Path] = field(default_factory=list)
    config: TemplateConfig | None = None
    placeholders: list[str] = field(default_factory=list)
    status: TemplateStatus = TemplateStatus.UNKNOWN
    last_modified: datetime = field(default_factory=datetime.now)
    file_hash: str = ""
    validation: ValidationResult = field(default_factory=ValidationResult)

    @property
    def category(self) -> str:
        return self.config.category if self.config else "Övriga"

    @property
    def icon(self) -> str:
        return self.status.icon

    @property
    def field_count(self) -> int:
        return len(self.placeholders)

    @property
    def attachment_count(self) -> int:
        return len(self.attachments)

    @property
    def is_valid(self) -> bool:
        return self.status == TemplateStatus.VALID

    def get_status_icon(self) -> str:
        return self.status.icon

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "folder": str(self.folder),
            "docx_file": str(self.docx_file) if self.docx_file else None,
            "attachments": [str(a) for a in self.attachments],
            "category": self.category,
            "placeholders": self.placeholders,
            "status": self.status.name,
            "last_modified": self.last_modified.isoformat(),
            "file_hash": self.file_hash,
        }


@dataclass
class UndoAction:
    """En åtgärd som kan ångras."""

    action_type: UndoActionType
    description: str
    timestamp: datetime = field(default_factory=datetime.now)
    undo_data: dict[str, Any] = field(default_factory=dict)
    redo_data: dict[str, Any] = field(default_factory=dict)

# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM INFO
# ══════════════════════════════════════════════════════════════════════════════


class PlatformInfo:
    """Plattformsinformation."""

    import platform as _pl

    system: str = _pl.system()
    release: str = _pl.release()
    version: str = _pl.version()
    machine: str = _pl.machine()
    processor: str = _pl.processor()

    @classmethod
    def is_windows(cls) -> bool:
        return cls.system == "Windows"

    @classmethod
    def is_macos(cls) -> bool:
        return cls.system == "Darwin"

    @classmethod
    def is_linux(cls) -> bool:
        return cls.system == "Linux"

# ══════════════════════════════════════════════════════════════════════════════
# PLACEHOLDER EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════


class PlaceholderExtractor:
    """Hittar och ersätter placeholders i DOCX-filer."""

    @classmethod
    def find_placeholders(cls, docx_path: Path) -> list[str]:
        """Returnerar unika placeholders i ordning (exkl. auto-fält)."""
        seen: dict[str, None] = {}
        result: list[str] = []
        for ph in cls._find_all(docx_path):
            key = ph.lower()
            if key not in seen:
                seen[key] = None
                if key not in AUTO_FIELDS:
                    result.append(ph)
        return result

    @classmethod
    def find_all_including_auto(cls, docx_path: Path) -> list[str]:
        """Returnerar ALLA placeholders inklusive auto-fält."""
        seen: dict[str, None] = {}
        result: list[str] = []
        for ph in cls._find_all(docx_path):
            key = ph.lower()
            if key not in seen:
                seen[key] = None
                result.append(ph)
        return result

    @classmethod
    def _find_all(cls, docx_path: Path) -> list[str]:
        """Hittar alla placeholders via python-docx + raw XML fallback."""
        try:
            import docx as _docx  # type: ignore[import]

            doc = _docx.Document(str(docx_path))
            found: list[str] = []
            for para in doc.paragraphs:
                for m in PLACEHOLDER_PATTERN.finditer(para.text):
                    found.append(m.group(1))
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            for m in PLACEHOLDER_PATTERN.finditer(para.text):
                                found.append(m.group(1))
            return found
        except ImportError:
            return cls._find_raw_xml(docx_path)
        except Exception:
            return cls._find_raw_xml(docx_path)

    @classmethod
    def _find_raw_xml(cls, docx_path: Path) -> list[str]:
        """Fallback: parsar raw XML utan python-docx."""
        found: list[str] = []
        try:
            with zipfile.ZipFile(str(docx_path), "r") as zf:
                if "word/document.xml" in zf.namelist():
                    xml = zf.read("word/document.xml").decode(
                        "utf-8", errors="replace"
                    )
                    clean = re.sub(r"<[^>]+>", "", xml)
                    for m in PLACEHOLDER_PATTERN.finditer(clean):
                        found.append(m.group(1))
        except Exception:
            pass
        return found

    @classmethod
    def get_all_text(cls, docx_path: Path) -> str:
        """Returnerar all text från DOCX (för förhandsvisning)."""
        try:
            import docx as _docx  # type: ignore[import]

            doc = _docx.Document(str(docx_path))
            lines: list[str] = []
            for para in doc.paragraphs:
                if para.text.strip():
                    lines.append(para.text)
            for table in doc.tables:
                for row in table.rows:
                    cells = [
                        c.text.strip() for c in row.cells if c.text.strip()
                    ]
                    if cells:
                        lines.append(" | ".join(cells))
            return "\n".join(lines)
        except ImportError:
            return cls._raw_text(docx_path)
        except Exception:
            return cls._raw_text(docx_path)

    @classmethod
    def _raw_text(cls, docx_path: Path) -> str:
        try:
            with zipfile.ZipFile(str(docx_path), "r") as zf:
                if "word/document.xml" in zf.namelist():
                    xml = zf.read("word/document.xml").decode(
                        "utf-8", errors="replace"
                    )
                    return re.sub(r"<[^>]+>", "", xml)
        except Exception:
            pass
        return ""

    @classmethod
    def is_auto_field(cls, field_name: str) -> bool:
        return field_name.lower() in AUTO_FIELDS

    @classmethod
    def replace_in_docx(
        cls,
        src: Path,
        dst: Path,
        replacements: dict[str, str],
        log: logging.Logger | None = None,
    ) -> bool:
        """
        Ersätter placeholders i DOCX och skriver till dst.
        Hanterar splitade runs (python-docx teknik).
        """
        try:
            import docx as _docx  # type: ignore[import]

            doc = _docx.Document(str(src))

            def _replace_para(para: Any) -> None:
                full = "".join(r.text for r in para.runs)
                new = full
                for k, v in replacements.items():
                    new = re.sub(
                        re.escape(f"::{k}::"), v, new, flags=re.IGNORECASE
                    )
                if new == full:
                    return
                if para.runs:
                    para.runs[0].text = new
                    for run in para.runs[1:]:
                        run.text = ""

            for para in doc.paragraphs:
                _replace_para(para)
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            _replace_para(para)

            tmp = dst.with_suffix(".tmp.docx")
            doc.save(str(tmp))
            tmp.replace(dst)
            return True
        except Exception as exc:
            if log:
                log.error("[PlaceholderExtractor] replace_in_docx: %s", exc)
            return False

# ══════════════════════════════════════════════════════════════════════════════
# FIELD VALIDATOR
# ══════════════════════════════════════════════════════════════════════════════


class FieldValidator:
    """Validerar fältvärden baserat på typ."""

    @classmethod
    def validate(
        cls, field_name: str, value: str, field_type: str = "text"
    ) -> tuple[bool, str]:
        """Validerar ett värde. Returnerar (är_giltigt, felmeddelande)."""
        if not value:
            return True, ""
        pattern = FIELD_VALIDATORS.get(field_type.lower())
        if not pattern:
            return True, ""
        if re.match(pattern, value, re.IGNORECASE):
            return True, ""
        messages: dict[str, str] = {
            "vin": (
                f"Ogiltigt VIN — måste vara {VIN_LENGTH} tecken "
                "(A-Z, 0-9, ej I/O/Q)"
            ),
            "reg": "Ogiltigt registreringsnummer (format: ABC123 eller ABC12D)",
            "date": "Ogiltigt datum (format: YYYY-MM-DD)",
            "email": "Ogiltig e-postadress",
            "phone": "Ogiltigt telefonnummer",
        }
        return False, messages.get(field_type.lower(), "Ogiltigt värde")

    @classmethod
    def validate_vin(cls, vin: str) -> tuple[bool, str]:
        vin = vin.upper().strip()
        if not vin:
            return False, "VIN saknas"
        if len(vin) != VIN_LENGTH:
            return False, (
                f"VIN måste vara exakt {VIN_LENGTH} tecken (du har {len(vin)})"
            )
        invalid = set(vin) & {"I", "O", "Q"}
        if invalid:
            return False, (
                f"VIN får inte innehålla: {', '.join(sorted(invalid))}"
            )
        if not VIN_PATTERN.match(vin):
            return False, "VIN innehåller ogiltiga tecken"
        return True, ""

    @classmethod
    def validate_reg(cls, reg: str) -> tuple[bool, str]:
        reg = reg.upper().strip().replace(" ", "")
        if not reg:
            return False, "Registreringsnummer saknas"
        if REG_PATTERN.match(reg):
            return True, ""
        return False, "Ogiltigt registreringsnummer"

# ══════════════════════════════════════════════════════════════════════════════
# UNDO MANAGER
# ══════════════════════════════════════════════════════════════════════════════


class UndoManager:
    """Hanterar ångra/gör-om-stack (max 100 åtgärder). Thread-safe."""

    MAX_HISTORY: int = 100

    def __init__(self) -> None:
        self._undo_stack: list[UndoAction] = []
        self._redo_stack: list[UndoAction] = []
        self._lock = threading.RLock()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def push(self, action: UndoAction) -> None:
        with self._lock:
            self._undo_stack.append(action)
            if len(self._undo_stack) > self.MAX_HISTORY:
                self._undo_stack.pop(0)
            self._redo_stack.clear()

    def pop_undo(self) -> UndoAction | None:
        with self._lock:
            if not self._undo_stack:
                return None
            action = self._undo_stack.pop()
            self._redo_stack.append(action)
            return action

    def pop_redo(self) -> UndoAction | None:
        with self._lock:
            if not self._redo_stack:
                return None
            action = self._redo_stack.pop()
            self._undo_stack.append(action)
            return action

    def clear(self) -> None:
        with self._lock:
            self._undo_stack.clear()
            self._redo_stack.clear()

# ══════════════════════════════════════════════════════════════════════════════
# INTERNA HJÄLPFUNKTIONER
# ══════════════════════════════════════════════════════════════════════════════


def _safe_filename(name: str) -> str:
    """Sanerar ett filnamn — behåller ÅÄÖ, tar bort ogiltiga tecken."""
    sanitized = _SAFE_FILENAME_RE.sub("_", name)
    return sanitized.strip(". ") or "mall"


def _file_hash(path: Path) -> str:
    """Beräknar MD5-hash av en fil (första 12 hex-tecken)."""
    try:
        md5 = hashlib.md5()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                md5.update(chunk)
        return md5.hexdigest()[:12]
    except Exception:
        return ""


def _atomic_write_json(path: Path, data: dict[str, Any]) -> bool:
    """Skriver JSON atomärt (.tmp → rename)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
        return True
    except Exception:
        return False

# ══════════════════════════════════════════════════════════════════════════════
# TEMPLATE MANAGER
# ══════════════════════════════════════════════════════════════════════════════


class TemplateManager:
    """
    Hanterar alla mall-operationer: skanning, skapande,
    radering, duplicering, import/export, konfiguration.

    Thread-safe via RLock.
    """

    def __init__(
        self,
        templates_path: Path,
        output_path: Path,
        log: logging.Logger | None = None,
    ) -> None:
        self._templates_path = Path(templates_path)
        self._output_path = Path(output_path)
        self._log = log or logging.getLogger("TemplateManager")
        self._lock = threading.RLock()
        self._templates: dict[str, TemplateInfo] = {}
        self._categories: list[str] = list(DEFAULT_CATEGORIES)
        self._undo_mgr: UndoManager = UndoManager()
        self._templates_path.mkdir(parents=True, exist_ok=True)
        self._output_path.mkdir(parents=True, exist_ok=True)

    # ── Egenskaper ────────────────────────────────────────────────────────

    @property
    def template_count(self) -> int:
        return len(self._templates)

    @property
    def valid_template_count(self) -> int:
        return sum(1 for t in self._templates.values() if t.is_valid)

    @property
    def can_undo(self) -> bool:
        return self._undo_mgr.can_undo

    @property
    def can_redo(self) -> bool:
        return self._undo_mgr.can_redo

    # ── Skanning ──────────────────────────────────────────────────────────

    def scan_templates(self) -> dict[str, TemplateInfo]:
        """
        Skannar templates_path.
        Varje undermapp med DOCX (ej ~$*) = en mall.
        Mappar utan DOCX ignoreras (MALL-006).

        4.3.0: tidsmätning + strukturerad tc.scan_done (DEBUG).
        """
        started = time.perf_counter()
        found: dict[str, TemplateInfo] = {}
        if not self._templates_path.exists():
            return found
        for folder in sorted(self._templates_path.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            try:
                info = self._scan_folder(folder)
                if info:
                    found[info.name] = info
                    if info.category not in self._categories:
                        self._categories.append(info.category)
            except Exception as exc:
                self._log.debug(
                    "[TM] Skanningsfel %s: %s", folder.name, exc
                )
        with self._lock:
            self._templates = found
        self._log.debug(
            "[TM] Skanning klar: %d mallar", len(found),
            extra={"event": "tc.scan_done",
                   "ctx": {"count": len(found),
                           "ms": round((time.perf_counter() - started) * 1000, 1)}},
        )
        return found

    def _scan_folder(self, folder: Path) -> TemplateInfo | None:
        """Skannar en mapp och returnerar TemplateInfo eller None."""
        name = folder.name
        docx_files = [
            f for f in folder.glob("*.docx")
            if not f.name.startswith("~$")
        ]
        if not docx_files:
            return None

        docx_file = docx_files[0]
        attachments = sorted(folder.glob("*.pdf"))
        config = self._load_config(folder, name)

        if config and config.attachments_order:
            order_map = {n: i for i, n in enumerate(config.attachments_order)}
            attachments = sorted(
                attachments, key=lambda p: order_map.get(p.name, 999)
            )

        placeholders: list[str] = []
        try:
            placeholders = PlaceholderExtractor.find_placeholders(docx_file)
        except Exception:
            pass

        validation = self._validate_template(name, docx_file, config)
        status = (
            TemplateStatus.VALID
            if validation.is_valid
            else TemplateStatus.INVALID
        )
        try:
            last_mod = datetime.fromtimestamp(docx_file.stat().st_mtime)
        except Exception:
            last_mod = datetime.now()

        return TemplateInfo(
            name=name,
            folder=folder,
            docx_file=docx_file,
            attachments=list(attachments),
            config=config,
            placeholders=placeholders,
            status=status,
            last_modified=last_mod,
            file_hash=_file_hash(docx_file),
            validation=validation,
        )

    def _load_config(self, folder: Path, name: str) -> TemplateConfig | None:
        """Laddar JSON-config om den finns."""
        json_path = folder / f"{name}.json"
        if not json_path.exists():
            json_files = list(folder.glob("*.json"))
            if not json_files:
                return None
            json_path = json_files[0]
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            return TemplateConfig.from_dict(data)
        except Exception:
            return None

    def _validate_template(
        self,
        name: str,
        docx_file: Path,
        config: TemplateConfig | None,
    ) -> ValidationResult:
        result = ValidationResult()
        if not docx_file.exists():
            result.add(ValidationLevel.ERROR, "DOCX-filen saknas", "docx")
        elif docx_file.suffix.lower() != ".docx":
            result.add(
                ValidationLevel.WARNING,
                "Filen är inte ett .docx-dokument",
                "docx",
            )
        if not config:
            result.add(
                ValidationLevel.INFO, "Ingen JSON-konfiguration hittad"
            )
        return result

    # ── Hämta mallar ──────────────────────────────────────────────────────

    def get_all_templates(self) -> dict[str, TemplateInfo]:
        with self._lock:
            return dict(self._templates)

    def get_template(self, name: str) -> TemplateInfo | None:
        with self._lock:
            return self._templates.get(name)

    def get_categories(self) -> list[str]:
        with self._lock:
            return list(self._categories)

    def add_category(self, name: str) -> None:
        with self._lock:
            if name and name not in self._categories:
                self._categories.append(name)

    def refresh_template(self, name: str) -> TemplateInfo | None:
        """Omskannar en enskild mall."""
        with self._lock:
            info = self._templates.get(name)
            if not info:
                return None
        try:
            new_info = self._scan_folder(info.folder)
            if new_info:
                with self._lock:
                    self._templates[name] = new_info
                return new_info
        except Exception as exc:
            self._log.error("[TM] refresh_template %s: %s", name, exc)
        return info

    # ── Skapa / Duplicera ─────────────────────────────────────────────────

    def create_template(
        self,
        name: str,
        docx_source: Path | None = None,
        category: str = "Övriga",
    ) -> TemplateInfo | None:
        """Skapar en ny mall-mapp med DOCX och JSON-config."""
        folder = self._templates_path / _safe_filename(name)
        if folder.exists():
            raise ValueError(f"Mall '{name}' finns redan")
        try:
            folder.mkdir(parents=True)
            if docx_source and docx_source.exists():
                target_docx = folder / f"{_safe_filename(name)}.docx"
                shutil.copy2(str(docx_source), str(target_docx))
            else:
                self._create_minimal_docx(folder, name)

            config = TemplateConfig(
                name=name,
                category=category,
                created=datetime.now().isoformat(),
            )
            config.save(folder / f"{_safe_filename(name)}.json")

            info = self._scan_folder(folder)
            if info:
                with self._lock:
                    self._templates[name] = info
                self._undo_mgr.push(
                    UndoAction(
                        action_type=UndoActionType.CREATE,
                        description=f"Skapade mall '{name}'",
                        undo_data={"folder": str(folder)},
                    )
                )
                return info
        except Exception as exc:
            self._log.error("[TM] create_template %s: %s", name, exc)
            with suppress(Exception):
                shutil.rmtree(str(folder))
        return None

    def _create_minimal_docx(self, folder: Path, name: str) -> Path:
        """Skapar en minimal DOCX med standard-placeholders."""
        target = folder / f"{_safe_filename(name)}.docx"
        try:
            import docx as _docx  # type: ignore[import]

            doc = _docx.Document()
            doc.add_heading(name, level=1)
            doc.add_paragraph(f"Mall: {name}")
            doc.add_paragraph("VIN: ::VIN::")
            doc.add_paragraph("Reg: ::Reg::")
            doc.add_paragraph("Datum: ::Datum::")
            doc.save(str(target))
        except ImportError:
            target.touch()
        return target

    def duplicate_template(
        self, source_name: str, new_name: str
    ) -> TemplateInfo | None:
        """Duplicerar en mall till ett nytt namn."""
        with self._lock:
            source_info = self._templates.get(source_name)
        if not source_info:
            raise ValueError(f"Mall '{source_name}' finns inte")

        new_folder = self._templates_path / _safe_filename(new_name)
        if new_folder.exists():
            raise ValueError(f"Mall '{new_name}' finns redan")

        try:
            shutil.copytree(str(source_info.folder), str(new_folder))

            for ext in (".docx", ".json"):
                old_f = new_folder / f"{_safe_filename(source_name)}{ext}"
                new_f = new_folder / f"{_safe_filename(new_name)}{ext}"
                if old_f.exists():
                    old_f.rename(new_f)

            new_json = new_folder / f"{_safe_filename(new_name)}.json"
            config = self._load_config(new_folder, new_name) or TemplateConfig(
                name=new_name
            )
            config.name = new_name
            config.created = datetime.now().isoformat()
            config.save(new_json)

            info = self._scan_folder(new_folder)
            if info:
                with self._lock:
                    self._templates[new_name] = info
                self._undo_mgr.push(
                    UndoAction(
                        action_type=UndoActionType.DUPLICATE,
                        description=(
                            f"Duplicerade '{source_name}' → '{new_name}'"
                        ),
                        undo_data={"folder": str(new_folder)},
                    )
                )
                return info
        except Exception as exc:
            self._log.error("[TM] duplicate_template: %s", exc)
            with suppress(Exception):
                shutil.rmtree(str(new_folder))
        return None

    # ── Ta bort / Byt namn ────────────────────────────────────────────────

    def delete_template(self, name: str) -> bool:
        """Flyttar mall till .trash/ (ångra möjlig)."""
        with self._lock:
            info = self._templates.get(name)
        if not info:
            return False
        try:
            trash = self._templates_path.parent / ".trash"
            trash.mkdir(exist_ok=True)
            dest = trash / (
                f"{_safe_filename(name)}_"
                f"{int(datetime.now().timestamp())}"
            )
            shutil.move(str(info.folder), str(dest))
            with self._lock:
                self._templates.pop(name, None)
            self._undo_mgr.push(
                UndoAction(
                    action_type=UndoActionType.DELETE,
                    description=f"Tog bort mall '{name}'",
                    undo_data={
                        "trash_path": str(dest),
                        "original_path": str(info.folder),
                        "name": name,
                    },
                )
            )
            return True
        except Exception as exc:
            self._log.error("[TM] delete_template %s: %s", name, exc)
            return False

    def rename_template(self, old_name: str, new_name: str) -> bool:
        """Byter namn på en mall (mapp + DOCX + JSON)."""
        with self._lock:
            info = self._templates.get(old_name)
        if not info:
            return False
        new_folder = self._templates_path / _safe_filename(new_name)
        if new_folder.exists():
            return False
        try:
            info.folder.rename(new_folder)
            for ext in (".docx", ".json"):
                old_f = new_folder / f"{_safe_filename(old_name)}{ext}"
                new_f = new_folder / f"{_safe_filename(new_name)}{ext}"
                if old_f.exists():
                    old_f.rename(new_f)

            new_json = new_folder / f"{_safe_filename(new_name)}.json"
            cfg = self._load_config(new_folder, new_name)
            if cfg:
                cfg.name = new_name
                cfg.save(new_json)

            new_info = self._scan_folder(new_folder)
            with self._lock:
                self._templates.pop(old_name, None)
                if new_info:
                    self._templates[new_name] = new_info

            self._undo_mgr.push(
                UndoAction(
                    action_type=UndoActionType.RENAME,
                    description=f"Bytte namn: '{old_name}' → '{new_name}'",
                    undo_data={
                        "old_name": old_name,
                        "new_name": new_name,
                        "old_folder": str(info.folder),
                        "new_folder": str(new_folder),
                    },
                )
            )
            return True
        except Exception as exc:
            self._log.error("[TM] rename_template: %s", exc)
            return False

    # ── Bilagor ───────────────────────────────────────────────────────────

    def add_attachment(self, template_name: str, pdf_path: Path) -> bool:
        """Kopierar en PDF till mall-mappen."""
        with self._lock:
            info = self._templates.get(template_name)
        if not info or not pdf_path.exists():
            return False
        try:
            dest = info.folder / pdf_path.name
            if not dest.exists():
                shutil.copy2(str(pdf_path), str(dest))
            self.refresh_template(template_name)
            return True
        except Exception as exc:
            self._log.error("[TM] add_attachment: %s", exc)
            return False

    def remove_attachment(self, template_name: str, filename: str) -> bool:
        """Tar bort en PDF-bilaga från mall-mappen."""
        with self._lock:
            info = self._templates.get(template_name)
        if not info:
            return False
        try:
            target = info.folder / filename
            if target.exists() and target.suffix.lower() == ".pdf":
                target.unlink()
                self.refresh_template(template_name)
                return True
        except Exception as exc:
            self._log.error("[TM] remove_attachment: %s", exc)
        return False

    def reorder_attachments(
        self, template_name: str, order: list[str]
    ) -> bool:
        """Sparar bilageordning i JSON-config."""
        with self._lock:
            info = self._templates.get(template_name)
        if not info:
            return False
        config = info.config or TemplateConfig(name=template_name)
        config.attachments_order = order
        return config.save(
            info.folder / f"{_safe_filename(template_name)}.json"
        )

    # ── Konfig ────────────────────────────────────────────────────────────

    def save_config(self, template_name: str, config: TemplateConfig) -> bool:
        """Sparar TemplateConfig till disk och omskannar mallen."""
        with self._lock:
            info = self._templates.get(template_name)
        if not info:
            return False
        json_path = info.folder / f"{_safe_filename(template_name)}.json"
        result = config.save(json_path)
        if result:
            self.refresh_template(template_name)
        return result

    # ── Import / Export ───────────────────────────────────────────────────

    def export_template(self, template_name: str, dest: Path) -> bool:
        """Exporterar en mall som ZIP-arkiv."""
        with self._lock:
            info = self._templates.get(template_name)
        if not info:
            return False
        try:
            with zipfile.ZipFile(
                str(dest), "w", zipfile.ZIP_DEFLATED
            ) as zf:
                for fp in info.folder.rglob("*"):
                    if fp.is_file() and not fp.name.startswith("~$"):
                        zf.write(fp, fp.relative_to(info.folder))
            self._log.debug(
                "[TM] Export klar: %s", template_name,
                extra={"event": "tc.export_done", "ctx": {"template": template_name}},
            )
            return True
        except Exception as exc:
            self._log.error(
                "[TM] export_template %s: %s", template_name, exc,
                extra={"event": "tc.export_failed",
                       "ctx": {"template": template_name, "error": str(exc)}},
            )
            return False

    def import_template(self, zip_path: Path) -> TemplateInfo | None:
        """Importerar en mall från ZIP-arkiv."""
        if not zip_path.exists():
            return None
        dest_folder: Path | None = None
        try:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                json_files = [n for n in zf.namelist() if n.endswith(".json")]
                name = zip_path.stem
                if json_files:
                    with zf.open(json_files[0]) as jf:
                        data = json.loads(jf.read().decode("utf-8"))
                        name = data.get("name", name)

                dest_folder = self._templates_path / _safe_filename(name)
                if dest_folder.exists():
                    name = f"{name}_import"
                    dest_folder = self._templates_path / _safe_filename(name)

                dest_folder.mkdir(parents=True)
                zf.extractall(str(dest_folder))

            info = self._scan_folder(dest_folder)
            if info:
                with self._lock:
                    self._templates[info.name] = info
                return info
        except Exception as exc:
            self._log.error("[TM] import_template: %s", exc)
            if dest_folder:
                with suppress(Exception):
                    shutil.rmtree(str(dest_folder))
        return None

    # ── Undo / Redo ───────────────────────────────────────────────────────

    def undo(self) -> str | None:
        """Ångrar senaste åtgärd. Returnerar beskrivning eller None."""
        action = self._undo_mgr.pop_undo()
        if not action:
            return None
        try:
            self._apply_undo(action)
        except Exception as exc:
            self._log.error("[TM] undo: %s", exc)
        return action.description

    def redo(self) -> str | None:
        """Gör om senaste ångrade åtgärd."""
        action = self._undo_mgr.pop_redo()
        if not action:
            return None
        return action.description

    def _apply_undo(self, action: UndoAction) -> None:
        """Applicerar ångra-åtgärd enligt typ."""
        if action.action_type in (
            UndoActionType.CREATE, UndoActionType.DUPLICATE
        ):
            folder = Path(action.undo_data.get("folder", ""))
            if folder.exists():
                shutil.rmtree(str(folder))
            with self._lock:
                self._templates.pop(folder.name, None)

        elif action.action_type == UndoActionType.DELETE:
            trash = Path(action.undo_data.get("trash_path", ""))
            orig = Path(action.undo_data.get("original_path", ""))
            name = action.undo_data.get("name", "")
            if trash.exists() and not orig.exists():
                shutil.move(str(trash), str(orig))
                info = self._scan_folder(orig)
                if info and name:
                    with self._lock:
                        self._templates[name] = info

        elif action.action_type == UndoActionType.RENAME:
            new_name = action.undo_data.get("new_name", "")
            old_name = action.undo_data.get("old_name", "")
            new_folder = Path(action.undo_data.get("new_folder", ""))
            if new_folder.exists() and new_name and old_name:
                self.rename_template(new_name, old_name)

# ══════════════════════════════════════════════════════════════════════════════
# PySide6-KOMPATIBLA STUB-KLASSER
# Exporteras av __init__.py — inga UI-beroenden här.
# ══════════════════════════════════════════════════════════════════════════════


class ToolTip:
    """Stub — ersatt av Qt ToolTips i plugin.py."""

    def __init__(self, widget: Any = None, text: str = "") -> None:
        pass


class ScrollableFrame:
    """Stub — ersatt av QScrollArea i plugin.py."""

    def __init__(self, parent: Any = None, **kwargs: Any) -> None:
        pass


class SearchableListbox:
    """Stub — ersatt av SearchableListbox i plugin.py."""

    def __init__(self, parent: Any = None, **kwargs: Any) -> None:
        pass


class StatusBar:
    """Stub — ersatt av DesignerStatusBar i plugin.py."""

    def __init__(self, parent: Any = None) -> None:
        pass


class ProgressDialog:
    """Stub — ersatt av QProgressDialog i plugin.py."""

    def __init__(self, parent: Any = None, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> ProgressDialog:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def close(self) -> None:
        pass
