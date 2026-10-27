from __future__ import annotations
# -*- coding: utf-8 -*-

"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    TEMPLATE DESIGNER PLUGIN v4.2.0                           ║
║                                                                              ║
║  Designa och redigera intygsmallar direkt i appen.                           ║
║  Stöd för Windows, macOS och Linux.                                          ║
║                                                                              ║
║  Funktioner:                                                                 ║
║  • Skapa/redigera/ta bort mallar                                             ║
║  • Undo/Redo-stöd                                                            ║
║  • Import/Export av mallar                                                   ║
║  • Testgenerering av intyg                                                   ║
║  • Bilagor och dokumenthantering                                             ║
║  • Integration med certificates, statistics och generator                    ║
║  • Dynamisk fönsterstorlek med DPI-skalning                                  ║
║  • Scrollbart innehåll med mushjul-stöd                                      ║
║  • Tab-visibility via Visa-menyn och högerklick                              ║
║                                                                              ║
║  Fil: plugins/template_designer/__init__.py                                  ║
║  Sökväg: Intygsgenerator/plugins/template_designer/__init__.py               ║
║                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  © Rickard Längkvist 2025                                                    ║
║  Multi-OS: Windows / macOS / Linux                                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import logging
import platform
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plugin import Plugin as PluginClass
    from .core import (
        TemplateManager as TemplateManagerClass,
        TemplateConfig as TemplateConfigClass,
        TemplateInfo as TemplateInfoClass,
        ValidationResult as ValidationResultClass,
        UndoAction as UndoActionClass,
        FieldDefinition as FieldDefinitionClass,
    )

# ══════════════════════════════════════════════════════════════════════════════
# VERSION & METADATA
# ══════════════════════════════════════════════════════════════════════════════

__version__: str = "4.3.0"
__version_info__: tuple[int, int, int] = (4, 2, 0)
__author__: str = "Rickard Längkvist"
__plugin_name__: str = "Malldesigner"
__plugin_id__: str = "template_designer"
__min_python__: tuple[int, int] = (3, 12)
__min_app_version__: str = "9.0.0"

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# PYTHON VERSION CHECK
# ══════════════════════════════════════════════════════════════════════════════

if sys.version_info < __min_python__:
    raise RuntimeError(
        f"{__plugin_name__} kräver Python "
        f"{__min_python__[0]}.{__min_python__[1]}+, "
        f"du har {sys.version_info.major}.{sys.version_info.minor}"
    )

# ══════════════════════════════════════════════════════════════════════════════
# COPYRIGHT
# ══════════════════════════════════════════════════════════════════════════════

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)
BASE_YEAR: int = _START_YEAR
CURRENT_YEAR: int = _CURRENT_YEAR


def get_dynamic_year() -> int:
    """Returnerar aktuellt år."""
    return datetime.now().year


def get_copyright() -> str:
    """Genererar dynamisk copyright-sträng."""
    year = get_dynamic_year()
    if year > _START_YEAR:
        return f"© Rickard Längkvist {_START_YEAR}–{year}"
    return f"© Rickard Längkvist {_START_YEAR}"

# ══════════════════════════════════════════════════════════════════════════════
# OS-STÖD
# ══════════════════════════════════════════════════════════════════════════════


class OSType(Enum):
    """Operativsystemtyper som stöds."""

    WINDOWS = auto()
    MACOS = auto()
    LINUX = auto()
    UNKNOWN = auto()

    @property
    def display_name(self) -> str:
        names: dict[OSType, str] = {
            OSType.WINDOWS: "Windows",
            OSType.MACOS: "macOS",
            OSType.LINUX: "Linux",
            OSType.UNKNOWN: "Okänt",
        }
        return names.get(self, "Okänt")

    @property
    def modifier_key(self) -> str:
        return "Command" if self == OSType.MACOS else "Control"

    @property
    def modifier_symbol(self) -> str:
        return "⌘" if self == OSType.MACOS else "Ctrl"


def get_os_type() -> OSType:
    """Detekterar aktuellt operativsystem."""
    os_map: dict[str, OSType] = {
        "windows": OSType.WINDOWS,
        "darwin": OSType.MACOS,
        "linux": OSType.LINUX,
    }
    return os_map.get(platform.system().lower(), OSType.UNKNOWN)


def is_windows() -> bool:
    return get_os_type() == OSType.WINDOWS


def is_macos() -> bool:
    return get_os_type() == OSType.MACOS


def is_linux() -> bool:
    return get_os_type() == OSType.LINUX


def get_platform_info() -> dict[str, str]:
    """Returnerar detaljerad plattformsinformation."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "os_type": get_os_type().name,
    }


CURRENT_OS: OSType = get_os_type()
OS_NAME: str = platform.system()
OS_VERSION: str = platform.version()
OS_RELEASE: str = platform.release()
OS_MACHINE: str = platform.machine()

# ══════════════════════════════════════════════════════════════════════════════
# PLUGIN CAPABILITIES
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PluginCapability:
    """Definierar en plugin-kapabilitet."""

    id: str
    name: str
    description: str
    enabled: bool = True
    requires: tuple[str, ...] = ()

    def __str__(self) -> str:
        status = "✓" if self.enabled else "✗"
        return f"[{status}] {self.name}"


@dataclass(frozen=True)
class PluginDependency:
    """Definierar ett plugin-beroende."""

    plugin_id: str
    min_version: str | None = None
    optional: bool = False
    description: str = ""

    def __str__(self) -> str:
        opt = "(valfritt)" if self.optional else "(krävs)"
        ver = f" v{self.min_version}+" if self.min_version else ""
        return f"{self.plugin_id}{ver} {opt}"


CAPABILITIES: dict[str, PluginCapability] = {
    "create_templates": PluginCapability(
        id="create_templates",
        name="Skapa mallar",
        description="Skapa nya intygsmallar från scratch eller från DOCX",
    ),
    "edit_templates": PluginCapability(
        id="edit_templates",
        name="Redigera mallar",
        description="Redigera befintliga mallar och fältkonfiguration",
    ),
    "delete_templates": PluginCapability(
        id="delete_templates",
        name="Ta bort mallar",
        description="Ta bort mallar permanent (med undo-stöd)",
    ),
    "import_export": PluginCapability(
        id="import_export",
        name="Import/Export",
        description="Importera och exportera mallar som ZIP-arkiv",
    ),
    "undo_redo": PluginCapability(
        id="undo_redo",
        name="Ångra/Gör om",
        description="Ångra och gör om ändringar med Ctrl+Z/Y",
    ),
    "test_generation": PluginCapability(
        id="test_generation",
        name="Testgenerering",
        description="Generera testintyg med smarta defaultvärden",
        requires=("generator",),
    ),
    "attachments": PluginCapability(
        id="attachments",
        name="Bilagor",
        description="Hantera PDF-bilagor i mallar",
    ),
    "validation": PluginCapability(
        id="validation",
        name="Validering",
        description="Validera mallar, fält och VIN/REG",
    ),
    "preview": PluginCapability(
        id="preview",
        name="Förhandsgranskning",
        description="Förhandsgranska mall",
    ),
    "search": PluginCapability(
        id="search",
        name="Sökfunktion",
        description="Sök och filtrera mallar med kategori",
    ),
}

DEPENDENCIES: tuple[PluginDependency, ...] = (
    PluginDependency(
        plugin_id="generator",
        min_version="5.0.0",
        optional=False,
        description="Krävs för PDF-generering och testning",
    ),
    PluginDependency(
        plugin_id="certificates",
        min_version="8.7.0",
        optional=True,
        description="Samarbetar med intygsgenerering",
    ),
    PluginDependency(
        plugin_id="statistics",
        min_version="4.2.0",
        optional=True,
        description="Statistik över mallar och generering",
    ),
    PluginDependency(
        plugin_id="settings",
        min_version="8.6.0",
        optional=True,
        description="Inställningar och konfiguration",
    ),
    PluginDependency(
        plugin_id="system_tray",
        min_version="8.4.0",
        optional=True,
        description="System tray-integration",
    ),
)

EVENTS: dict[str, list[str]] = {
    "emit": [
        "template.created",
        "template.updated",
        "template.deleted",
        "templates.scanned",
        "certificate.before_generate",
        "plugin.reload",
    ],
    "listen": [
        "app.ready",
        "app.closing",
        "settings.changed",
        "settings.theme_changed",
        "refresh_templates",
        "template.created",
        "template.deleted",
        "template.updated",
        "generator.ready",
    ],
}

# ══════════════════════════════════════════════════════════════════════════════
# IMPORTS FRÅN SUBMODULER
# ══════════════════════════════════════════════════════════════════════════════

_core_available: bool = False
_plugin_available: bool = False
_import_errors: list[str] = []

# Enums
FieldType = None
ValidationLevel = None
TemplateStatus = None
UndoActionType = None

# Dataklasser
ValidationMessage = None
ValidationResult = None
FieldDefinition = None
TemplateConfig = None
TemplateInfo = None
UndoAction = None

# Core-klasser
PlaceholderExtractor = None
FieldValidator = None
UndoManager = None
TemplateManager = None
PlatformInfo = None

# UI-stubs från core
ToolTip = None
ScrollableFrame = None
CoreSearchableListbox = None
CoreStatusBar = None
CoreProgressDialog = None

# Konstanter (fallback-värden)
RESERVED_NAMES: frozenset[str] = frozenset()
DEFAULT_CATEGORIES: list[str] = []
AUTO_FIELDS: frozenset[str] = frozenset()
PLACEHOLDER_PATTERNS: list[tuple[Any, str]] = []
FIELD_VALIDATORS: dict[str, str] = {}
VIN_PATTERN = None
VIN_LENGTH: int = 17
FOLDER_TEMPLATES: str = "Mallar"
FOLDER_OUTPUT: str = "Out"
FOLDER_CONFIG: str = "js"  # BUG-007: ej ".js"

try:
    from .core import (
        __version__ as core_version,
        COPYRIGHT as core_copyright,
        FieldType,
        ValidationLevel,
        TemplateStatus,
        UndoActionType,
        ValidationMessage,
        ValidationResult,
        FieldDefinition,
        TemplateConfig,
        TemplateInfo,
        UndoAction,
        PlaceholderExtractor,
        FieldValidator,
        UndoManager,
        TemplateManager,
        PlatformInfo,
        ToolTip,
        ScrollableFrame,
        SearchableListbox as CoreSearchableListbox,
        StatusBar as CoreStatusBar,
        ProgressDialog as CoreProgressDialog,
        RESERVED_NAMES,
        DEFAULT_CATEGORIES,
        AUTO_FIELDS,
        PLACEHOLDER_PATTERNS,
        FIELD_VALIDATORS,
        VIN_PATTERN,
        VIN_LENGTH,
        FOLDER_TEMPLATES,
        FOLDER_OUTPUT,
    )
    _core_available = True
    logger.debug("template_designer.core v%s importerad", core_version)
except ImportError as exc:
    _import_errors.append(f"core: {exc}")
    logger.warning("Kunde inte importera template_designer.core: %s", exc)
    RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "SYSTEM"})
    DEFAULT_CATEGORIES = ["25kW", "35kW", "Passagerare", "Demontering", "Övriga"]
    AUTO_FIELDS = frozenset({"DATUM", "NUMMER", "DATE", "NUMBER"})
except Exception as exc:
    _import_errors.append(f"core (unexpected): {exc}")
    logger.error("Oväntat fel vid import av core: %s", exc)

# Plugin-imports
Plugin = None
SearchableListbox = None
DesignerStatusBar = None
ProgressDialog = None
ScrollableText = None
OSPlatform = None
UIConstants = None
UI = None

try:
    from .plugin import (
        Plugin,
        SearchableListbox,
        DesignerStatusBar,
        ProgressDialog,
        ScrollableText,
        OSPlatform,
        UIConstants,
        UI,
        __version__ as plugin_version,
        COPYRIGHT as plugin_copyright,
    )
    _plugin_available = True
    logger.debug("template_designer.plugin v%s importerad", plugin_version)
except ImportError as exc:
    _import_errors.append(f"plugin: {exc}")
    logger.warning("Kunde inte importera template_designer.plugin: %s", exc)
except Exception as exc:
    _import_errors.append(f"plugin (unexpected): {exc}")
    logger.error("Oväntat fel vid import av plugin: %s", exc)

# ══════════════════════════════════════════════════════════════════════════════
# LADDNINGSSTATUS
# ══════════════════════════════════════════════════════════════════════════════


def is_fully_loaded() -> bool:
    """Kontrollerar om alla komponenter laddades korrekt."""
    return _core_available and _plugin_available


def is_core_available() -> bool:
    """Kontrollerar om core-modulen är tillgänglig."""
    return _core_available


def is_plugin_available() -> bool:
    """Kontrollerar om plugin-modulen är tillgänglig."""
    return _plugin_available


def get_import_errors() -> list[str]:
    """Returnerar lista med importfel."""
    return _import_errors.copy()


def get_load_status() -> dict[str, Any]:
    """Returnerar detaljerad laddningsstatus."""
    return {
        "fully_loaded": is_fully_loaded(),
        "core_available": _core_available,
        "plugin_available": _plugin_available,
        "import_errors": _import_errors.copy(),
        "version": __version__,
        "os": CURRENT_OS.name,
    }

# ══════════════════════════════════════════════════════════════════════════════
# PLUGIN DISCOVERY & FACTORY
# ══════════════════════════════════════════════════════════════════════════════


def get_plugin_class() -> type | None:
    """Returnerar plugin-klassen för discovery."""
    return Plugin


def get_plugin_info() -> dict[str, Any]:
    """Returnerar komplett plugin-information för discovery."""
    return {
        "id": __plugin_id__,
        "name": __plugin_name__,
        "version": __version__,
        "version_info": __version_info__,
        "author": __author__,
        "copyright": COPYRIGHT,
        "description": "Designa och redigera intygsmallar med avancerade funktioner",
        "plugin_class": Plugin,
        "is_available": is_fully_loaded(),
        "priority": 50,
        "min_python": __min_python__,
        "min_app_version": __min_app_version__,
        "capabilities": CAPABILITIES,
        "dependencies": DEPENDENCIES,
        "events": EVENTS,
        "os_support": {"windows": True, "macos": True, "linux": True},
        "current_os": CURRENT_OS.name,
        "platform_info": get_platform_info(),
        "has_tab": True,
        "tab_title": "Malldesigner",
        "tab_icon": "📐",
        "supports_hide": True,
        "supports_scroll": True,
        "supports_resize": True,
        "supports_dpi_scaling": True,
        "shortcuts": {
            "new": f"{CURRENT_OS.modifier_symbol}+N",
            "import": f"{CURRENT_OS.modifier_symbol}+Shift+I",
            "export": f"{CURRENT_OS.modifier_symbol}+Shift+E",
            "undo": f"{CURRENT_OS.modifier_symbol}+Z",
            "redo": f"{CURRENT_OS.modifier_symbol}+Y",
            "search": f"{CURRENT_OS.modifier_symbol}+F",
            "refresh": "F5",
            "rename": "F2",
        },
        "load_status": get_load_status(),
        "import_errors": _import_errors,
        "fully_loaded": is_fully_loaded(),
        "load_errors": _import_errors,
    }


def create_plugin(*args: Any, **kwargs: Any) -> Any | None:
    """Factory-funktion för att skapa plugin-instans."""
    if Plugin is None:
        logger.error("Kan inte skapa plugin — Plugin-klass ej laddad")
        return None
    try:
        instance = Plugin(*args, **kwargs)
        logger.debug(
            "Skapade plugin-instans: %s v%s", __plugin_name__, __version__
        )
        return instance
    except Exception as exc:
        logger.error("Fel vid skapande av plugin: %s", exc)
        return None


def check_dependencies(available_plugins: dict[str, Any]) -> dict[str, bool]:
    """Kontrollerar om beroenden är uppfyllda."""
    status: dict[str, bool] = {}
    for dep in DEPENDENCIES:
        if dep.plugin_id in available_plugins:
            info = available_plugins[dep.plugin_id]
            if dep.min_version:
                status[dep.plugin_id] = (
                    info.get("version", "0.0.0") >= dep.min_version
                )
            else:
                status[dep.plugin_id] = True
        else:
            status[dep.plugin_id] = dep.optional
            if not dep.optional:
                logger.warning(
                    "Obligatoriskt beroende saknas: %s (%s)",
                    dep.plugin_id,
                    dep.description,
                )
    return status


def get_missing_dependencies(
    available_plugins: dict[str, Any],
) -> list[PluginDependency]:
    """Returnerar lista med saknade obligatoriska beroenden."""
    status = check_dependencies(available_plugins)
    return [
        dep
        for dep in DEPENDENCIES
        if not status.get(dep.plugin_id, False) and not dep.optional
    ]

# ══════════════════════════════════════════════════════════════════════════════
# TAB & MENY-HJÄLPARE
# ══════════════════════════════════════════════════════════════════════════════


def get_tab_config() -> dict[str, Any]:
    """Returnerar tab-konfiguration för Visa-menyn."""
    return {
        "id": __plugin_id__,
        "title": "Malldesigner",
        "icon": "📐",
        "visible_by_default": True,
        "can_hide": True,
        "can_close": True,
        "shortcut": f"{CURRENT_OS.modifier_symbol}+M",
        "tooltip": "Designa och redigera intygsmallar",
        "category": "Verktyg",
        "priority": 50,
    }


def get_menu_items() -> list[dict[str, Any]]:
    """Returnerar menyalternativ för plugin."""
    mod = CURRENT_OS.modifier_symbol
    return [
        {"menu": "Verktyg", "label": "Ny mall...", "command": "template:new",
         "shortcut": f"{mod}+Shift+N", "icon": "📄", "enabled": True},
        {"menu": "Verktyg", "label": "Importera mall...", "command": "template:import",
         "shortcut": f"{mod}+Shift+I", "icon": "📥", "enabled": True},
        {"menu": "Verktyg", "label": "Exportera mall...", "command": "template:export",
         "shortcut": f"{mod}+Shift+E", "icon": "📤", "enabled": True},
        {"menu": "Verktyg", "label": "-"},
        {"menu": "Verktyg", "label": "Validera alla mallar",
         "command": "template:validate_all", "icon": "✅", "enabled": True},
        {"menu": "Verktyg", "label": "Uppdatera mall-lista",
         "command": "template:refresh", "shortcut": "F5", "icon": "🔄", "enabled": True},
    ]


def get_context_menu_items() -> list[dict[str, Any]]:
    """Returnerar högerklick-menyalternativ."""
    return [
        {"label": "Redigera DOCX", "command": "template:edit_docx", "icon": "📝"},
        {"label": "Öppna mapp", "command": "template:open_folder", "icon": "📂"},
        {"label": "-"},
        {"label": "Duplicera", "command": "template:duplicate", "icon": "📋"},
        {"label": "Byt namn", "command": "template:rename",
         "shortcut": "F2", "icon": "✏️"},
        {"label": "-"},
        {"label": "Exportera", "command": "template:export", "icon": "📤"},
        {"label": "Ta bort", "command": "template:delete", "icon": "🗑️"},
    ]

# ══════════════════════════════════════════════════════════════════════════════
# UTILITY-FUNKTIONER
# ══════════════════════════════════════════════════════════════════════════════


def get_templates_path(base_path: Path | None = None) -> Path:
    """Returnerar sökväg till Mallar-mappen."""
    return (base_path or Path.cwd()) / FOLDER_TEMPLATES


def get_output_path(base_path: Path | None = None) -> Path:
    """Returnerar sökväg till Out-mappen."""
    return (base_path or Path.cwd()) / FOLDER_OUTPUT


def get_config_path(base_path: Path | None = None) -> Path:
    """Returnerar sökväg till js/-mappen (config)."""
    return (base_path or Path.cwd()) / FOLDER_CONFIG


def list_available_templates(base_path: Path | None = None) -> list[str]:
    """Listar tillgängliga mallar (mappar med DOCX)."""
    templates_path = get_templates_path(base_path)
    if not templates_path.exists():
        return []
    result: list[str] = []
    for item in templates_path.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            if any(item.glob("*.docx")):
                result.append(item.name)
    return sorted(result)


def validate_template_name(name: str) -> tuple[bool, str]:
    """Validerar ett mallnamn."""
    if not name:
        return False, "Mallnamn får inte vara tomt"
    if len(name) > 100:
        return False, "Mallnamn får max vara 100 tecken"
    if len(name) < 2:
        return False, "Mallnamn måste vara minst 2 tecken"
    if RESERVED_NAMES and name.upper() in {n.upper() for n in RESERVED_NAMES}:
        return False, f"'{name}' är ett reserverat namn"
    invalid_chars = '<>:"/\\|?*\x00'
    for char in invalid_chars:
        if char in name:
            return False, f"Ogiltigt tecken i namn: '{char}'"
    if name.startswith((".", " ")):
        return False, "Mallnamn får inte börja med punkt eller mellanslag"
    if name.endswith((".", " ")):
        return False, "Mallnamn får inte sluta med punkt eller mellanslag"
    return True, ""


def format_template_size(size_bytes: int) -> str:
    """Formaterar filstorlek till läsbar sträng."""
    val = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} TB"


def get_file_modified_date(path: Path) -> datetime | None:
    """Hämtar filens senaste ändringsdatum."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

__all__: list[str] = [
    # Metadata
    "__version__", "__version_info__", "__author__",
    "__plugin_name__", "__plugin_id__", "__min_python__",
    "__min_app_version__", "COPYRIGHT", "BASE_YEAR", "CURRENT_YEAR",
    "get_copyright", "get_dynamic_year",
    # OS
    "OSType", "CURRENT_OS", "OS_NAME", "OS_VERSION", "OS_RELEASE",
    "OS_MACHINE", "get_os_type", "is_windows", "is_macos", "is_linux",
    "get_platform_info",
    # Plugin discovery
    "Plugin", "get_plugin_class", "get_plugin_info", "create_plugin",
    "check_dependencies", "get_missing_dependencies",
    "is_fully_loaded", "is_core_available", "is_plugin_available",
    "get_import_errors", "get_load_status",
    # Capabilities
    "PluginCapability", "PluginDependency", "CAPABILITIES",
    "DEPENDENCIES", "EVENTS",
    # Tab & meny
    "get_tab_config", "get_menu_items", "get_context_menu_items",
    # Enums
    "FieldType", "ValidationLevel", "TemplateStatus", "UndoActionType",
    # Dataklasser
    "ValidationMessage", "ValidationResult", "FieldDefinition",
    "TemplateConfig", "TemplateInfo", "UndoAction",
    # Core-klasser
    "PlaceholderExtractor", "FieldValidator", "UndoManager",
    "TemplateManager", "PlatformInfo",
    # UI (core stubs)
    "ToolTip", "ScrollableFrame", "CoreSearchableListbox",
    "CoreStatusBar", "CoreProgressDialog",
    # UI (plugin)
    "SearchableListbox", "DesignerStatusBar", "ProgressDialog",
    "ScrollableText", "OSPlatform", "UIConstants", "UI",
    # Konstanter
    "RESERVED_NAMES", "DEFAULT_CATEGORIES", "AUTO_FIELDS",
    "PLACEHOLDER_PATTERNS", "FIELD_VALIDATORS", "VIN_PATTERN",
    "VIN_LENGTH", "FOLDER_TEMPLATES", "FOLDER_OUTPUT", "FOLDER_CONFIG",
    # Utilities
    "get_templates_path", "get_output_path", "get_config_path",
    "list_available_templates", "validate_template_name",
    "format_template_size", "get_file_modified_date",
]

# ══════════════════════════════════════════════════════════════════════════════
# MODUL-INITIERING
# ══════════════════════════════════════════════════════════════════════════════


def _init_module() -> None:
    """Initierar modulen vid import."""
    if _import_errors:
        logger.warning(
            "%s v%s laddades med %d varning(ar)",
            __plugin_name__, __version__, len(_import_errors),
        )
        for err in _import_errors:
            logger.debug("  - %s", err)
    else:
        logger.debug(
            "%s v%s laddad (%s)",
            __plugin_name__, __version__, CURRENT_OS.display_name,
        )


_init_module()
