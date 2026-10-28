from __future__ import annotations

"""
Fil:    plugins/settings/plugin.py
Sökväg: Intygsgenerator/plugins/settings/plugin.py

IntygsDrive — centrala PySide6-inställningar: 12 flikar, teman, genvägar,
profiler, backup/restore, diagnostik samt import/export av JSON-inställningar.
Fil-I/O för backup körs av workers.py och återanvändbar UI ligger i view.py.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import Qt, QThreadPool, QTimer, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core import (
    __version__ as CORE_VERSION,
    AutostartManager,
    HealthCheck,
    HealthStatus,
    PlatformHelper,
    Profile,
    SettingDefinition,
    ShortcutManager,
    TabInfo,
    Theme,
    ThemeManager,
)
from .view import (
    COPYRIGHT,
    ProfileDialog,
    SettingsScrollTab,
    SettingsShell,
    ShortcutEditDialog,
    add_setting_control,
    make_action_button,
    make_button_row,
    make_label_row,
)
from .workers import BackupOptions, BackupWorker, RestoreWorker

if TYPE_CHECKING:
    from core import AppContext

__version__: str = "9.0.0"
__all__: list[str] = ["Plugin"]

PLUGIN_NAME: str = "Inställningar"
PLUGIN_PRIORITY: int = 90
_SETTINGS_FILE_NAME: str = "settings.json"
_PROFILE_FILE_PATTERN: re.Pattern[str] = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_logger: logging.Logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# BEROENDE-RÅDGIVARE (v8.9.0) — multi-OS, multi-office, erbjud installation
# ══════════════════════════════════════════════════════════════════════════════


class DependencyAdvisor:
    """Inventerar saknade tillägg (Python-paket + office-sviter) per OS och kan
    installera Python-paket i bakgrundstråd. Allt loggas med ``dep.*``-events."""

    SUITE_URLS: dict[str, str] = {
        "LibreOffice": "https://www.libreoffice.org/download/download-libreoffice/",
        "OpenOffice": "https://www.openoffice.org/download/",
        "Microsoft Word": "https://www.microsoft.com/microsoft-365/word",
    }

    @staticmethod
    def _can_import(module: str) -> bool:
        try:
            __import__(module)
            return True
        except Exception:
            return False

    @classmethod
    def missing_python_packages(cls) -> list[dict[str, str]]:
        """Python-paket som saknas men som appen har nytta av."""
        missing: list[dict[str, str]] = []
        if sys.platform == "win32" and not cls._can_import("win32com.client"):
            missing.append({
                "name": "pywin32",
                "why": "MS Word som PDF-konverterare (Word COM)",
            })
        if not cls._can_import("docx2pdf"):
            missing.append({
                "name": "docx2pdf",
                "why": "Alternativ konverterare (Word på Windows/macOS)",
            })
        if not (cls._can_import("pypdf") or cls._can_import("PyPDF2")):
            missing.append({
                "name": "pypdf",
                "why": "PDF-sammanslagning (primär motor, §7)",
            })
        if not cls._can_import("psutil"):
            missing.append({
                "name": "psutil",
                "why": "Minneskontroll i diagnostik",
            })
        return missing

    @classmethod
    def office_suite_status(cls) -> list[dict[str, Any]]:
        """Status + installationskommando per office-svit för aktuellt OS."""
        import glob as _glob

        win = sys.platform == "win32"
        mac = sys.platform == "darwin"

        def _roots() -> list[str]:
            return [
                os.environ.get(key, "")
                for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432")
            ]

        def lo_found() -> bool:
            if win:
                if any(
                    root and _glob.glob(os.path.join(root, "LibreOffice*", "program", "soffice.exe"))
                    for root in _roots()
                ):
                    return True
                return bool(shutil.which("soffice.exe") or shutil.which("libreoffice.exe"))
            if mac:
                return bool(_glob.glob("/Applications/LibreOffice*.app/Contents/MacOS/soffice"))
            return bool(shutil.which("libreoffice") or shutil.which("soffice"))

        def oo_found() -> bool:
            if win:
                return any(
                    root and _glob.glob(os.path.join(root, "OpenOffice*", "program", "soffice.exe"))
                    for root in _roots()
                )
            if mac:
                return bool(_glob.glob("/Applications/OpenOffice.app/Contents/MacOS/soffice"))
            return bool(shutil.which("openoffice"))

        def hint(suite: str) -> str:
            if suite == "LibreOffice":
                if win:
                    return "winget install TheDocumentFoundation.LibreOffice"
                if mac:
                    return "brew install --cask libreoffice"
                return "sudo apt install libreoffice"
            if suite == "OpenOffice":
                if win:
                    return "winget install Apache.OpenOffice"
                if mac:
                    return "brew install --cask openoffice"
                return "sudo apt install openoffice"
            return ""

        word_usable = (win and cls._can_import("win32com.client")) or cls._can_import("docx2pdf")
        return [
            {
                "name": "Microsoft Word",
                "found": word_usable,
                "note": "används via COM/docx2pdf" if win else "via docx2pdf på macOS",
                "hint": "",
                "url": cls.SUITE_URLS["Microsoft Word"],
            },
            {
                "name": "LibreOffice",
                "found": lo_found(),
                "note": "fungerar på alla OS, headless-capabel",
                "hint": hint("LibreOffice"),
                "url": cls.SUITE_URLS["LibreOffice"],
            },
            {
                "name": "OpenOffice",
                "found": oo_found(),
                "note": "samma soffice-CLI som LibreOffice",
                "hint": hint("OpenOffice"),
                "url": cls.SUITE_URLS["OpenOffice"],
            },
        ]

    @staticmethod
    def pip_install(package: str, on_done: "Callable[[bool, str], None] | None" = None) -> threading.Thread:
        """Installera Python-paket via pip i bakgrundstråd (blockerar aldrig UI)."""

        def run() -> None:
            _logger.info("[DEP] Installerar %s …", package,
                         extra={"event": "dep.install_started", "ctx": {"package": package}})
            ok = False
            tail = ""
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", package],
                    capture_output=True, text=True, timeout=600,
                )
                ok = proc.returncode == 0
                tail = ((proc.stdout or "") + (proc.stderr or ""))[-800:]
            except Exception as exc:
                tail = f"{type(exc).__name__}: {exc}"
            if ok:
                _logger.info("[DEP] %s installerat", package,
                             extra={"event": "dep.install_done", "ctx": {"package": package}})
            else:
                _logger.error("[DEP] Installation av %s misslyckades: %s", package, tail,
                              extra={"event": "dep.install_failed",
                                     "ctx": {"package": package, "detail": tail[-300:]}})
            if callable(on_done):
                on_done(ok, tail)

        thread = threading.Thread(target=run, name=f"pip-{package}", daemon=True)
        thread.start()
        return thread


class _DependencyDialog(QDialog):
    """Erbjuder installation av saknade tillägg (pip i bakgrunden) och
    länkar/kommandon för office-sviter — multi-OS."""

    def __init__(
        self,
        missing: list[dict[str, str]],
        suites: list[dict[str, Any]],
        on_changed: "Callable[[], None] | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📦 Saknade tillägg")
        self.setMinimumWidth(560)
        self._on_changed = on_changed
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "<b>Saknade tillägg</b> — installera direkt här (bakgrundstråd), "
            "eller hämta office-sviter nedan. Allt loggas."
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QVBoxLayout(inner)

        form.addWidget(QLabel("<b>Python-paket</b>"))
        if not missing:
            form.addWidget(QLabel("✅ Inga saknade Python-paket."))
        for item in missing:
            row = QHBoxLayout()
            label = QLabel(f"• <b>{item['name']}</b> — {item['why']}")
            label.setWordWrap(True)
            row.addWidget(label, 1)
            btn = QPushButton("⬇ Installera")
            btn.setFixedWidth(110)
            btn.clicked.connect(lambda _c=False, name=item["name"], b=btn: self._install(name, b))
            row.addWidget(btn)
            form.addLayout(row)

        form.addWidget(QLabel("<b>Office-sviter</b> (minst en behövs för PDF-konvertering)"))
        for suite in suites:
            row = QHBoxLayout()
            icon = "✅" if suite.get("found") else "❌"
            note = suite.get("note", "")
            row.addWidget(QLabel(f"{icon} <b>{suite['name']}</b> — {note}"), 1)
            url = suite.get("url", "")
            if url:
                web_btn = QPushButton("🌐 Webbplats")
                web_btn.setFixedWidth(100)
                web_btn.clicked.connect(lambda _c=False, u=url: webbrowser.open(u))
                row.addWidget(web_btn)
            hint = suite.get("hint", "")
            if hint:
                cmd_btn = QPushButton("📋 Kommando")
                cmd_btn.setFixedWidth(100)
                cmd_btn.setToolTip(hint)
                cmd_btn.clicked.connect(lambda _c=False, h=hint: self._copy(h))
                row.addWidget(cmd_btn)
            form.addLayout(row)
        form.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        self._status = QLabel("Redo.")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        close_btn = QPushButton("Stäng")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _copy(self, text: str) -> None:
        with suppress(Exception):
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
        self._status.setText(f"Kopierade: {text}")
        _logger.info("[DEP] Installeringskommando kopierat: %s", text,
                     extra={"event": "dep.command_copied", "ctx": {"command": text}})

    def _install(self, package: str, btn: QPushButton) -> None:
        btn.setEnabled(False)
        btn.setText("⏳ …")
        self._status.setText(f"Installerar {package} …")

        def on_done(ok: bool, tail: str) -> None:
            def apply() -> None:
                if ok:
                    btn.setText("✔ Klart")
                    self._status.setText(f"✅ {package} installerat — detektering uppdateras.")
                else:
                    btn.setEnabled(True)
                    btn.setText("⬇ Försök igen")
                    self._status.setText(f"❌ {package} misslyckades: {tail[-200:]}")
                if ok and callable(self._on_changed):
                    with suppress(Exception):
                        self._on_changed()
            QTimer.singleShot(0, apply)  # tillbaka till UI-tråden

        DependencyAdvisor.pip_install(package, on_done)


def _safe_profile_filename(name: str) -> str:
    cleaned = _PROFILE_FILE_PATTERN.sub("_", name).strip(". ")
    return cleaned or "Namnlös"


def _safe_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now()


class Plugin:
    """IPlugin-controller för alla Settings-funktioner."""

    name: str = PLUGIN_NAME
    version: str = __version__
    description: str = "Centrala inställningar med profiler, teman och diagnostik"
    priority: int = PLUGIN_PRIORITY

    SETTING_DEFINITIONS: list[SettingDefinition] = [
        SettingDefinition("app.start_with_os", "Starta med operativsystemet", "Startar programmet automatiskt vid datorstart", "bool", False, section="startup", tooltip="Registrerar programmet i operativsystemets autostart."),
        SettingDefinition("app.tray.minimize_to_tray", "Minimera till systemfältet", "Dölj fönstret vid minimering och visa det bara som ikon i systemfältet.", "bool", True, section="startup", icon="📥"),
        SettingDefinition("app.tray.start_minimized", "Starta minimerad", "Starta programmet i systemfältet.", "bool", True, section="startup", icon="🔽"),
        SettingDefinition("app.tray.show_notifications", "Visa tray-notifieringar", "Visa notifieringar från systemfältet.", "bool", True, section="startup", icon="🔔"),
        SettingDefinition("app.tray.single_click_open", "Enkelklick öppnar fönstret", "Öppna fönstret med enkelklick på tray-ikonen.", "bool", True, section="startup"),
        SettingDefinition("app.confirm_exit", "Bekräfta avslut", "Fråga innan programmet avslutas via meny.", "bool", True, section="startup"),
        SettingDefinition("general.theme", "Tema", "Välj appens utseende.", "choice", "system", choices=["system", "light", "dark"], choice_labels={"system": "Följ systemet", "light": "Ljust", "dark": "Mörkt"}, section="general", icon="🎨"),
        SettingDefinition("general.language", "Språk", "Välj språk för gränssnittet.", "choice", "sv", choices=["sv", "en"], choice_labels={"sv": "Svenska 🇸🇪", "en": "English 🇬🇧"}, section="general", requires_restart=True, icon="🌐"),
        SettingDefinition("general.font_size", "Textstorlek", "Grundläggande textstorlek i punkter.", "int", 10, min_value=8, max_value=18, section="general"),
        SettingDefinition("general.show_tooltips", "Visa hjälptexter", "Visa tooltips vid hovring.", "bool", True, section="general", icon="💡"),
        SettingDefinition("general.remember_window", "Kom ihåg fönsterposition", "Spara fönsterstorlek och position.", "bool", True, section="general", icon="📐"),
        SettingDefinition("general.animation_enabled", "Aktivera animationer", "Visa diskreta animationer i gränssnittet.", "bool", True, section="general", icon="✨"),
        SettingDefinition("pdf.converter", "PDF-konverterare", "Välj DOCX→PDF-metod.", "choice", "auto", choices=["auto", "libreoffice", "word"], choice_labels={"auto": "Automatisk", "libreoffice": "LibreOffice", "word": "Microsoft Word"}, section="pdf"),
        SettingDefinition("pdf.open_after_generate", "Öppna efter generering", "Öppna PDF automatiskt efter skapande.", "bool", True, section="pdf", icon="👁️"),
        SettingDefinition("pdf.compress_output", "Komprimera PDF", "Minska filstorlek när det är möjligt.", "bool", False, section="pdf", icon="📦"),
        SettingDefinition("pdf.dpi", "DPI för bilder", "Kvalitet för inbäddade bilder.", "int", 150, min_value=72, max_value=600, section="pdf", icon="🖼️"),
        SettingDefinition("pdf.embed_fonts", "Bädda in typsnitt", "Inkludera typsnitt i PDF när motorn stöder det.", "bool", True, section="pdf"),
        SettingDefinition("backup.auto_backup", "Automatisk backup", "Skapa backup enligt angivet intervall.", "bool", True, section="backup", icon="💾"),
        SettingDefinition("backup.backup_interval_days", "Backup-intervall (dagar)", "Antal dagar mellan automatiska backups.", "int", 7, min_value=1, max_value=365, section="backup", icon="📅"),
        SettingDefinition("backup.max_backups", "Max antal backups", "Antal backups som behålls.", "int", 10, min_value=1, max_value=100, section="backup"),
        SettingDefinition("backup.include_logs", "Inkludera loggar", "Ta med loggfiler i backup.", "bool", False, section="backup", icon="📝"),
        SettingDefinition("backup.include_output", "Inkludera genererade filer", "Ta med PDF-filer från Out i backup.", "bool", False, section="backup", icon="📁"),
        SettingDefinition("backup.include_templates", "Inkludera mallar", "Ta med Mallar-mappen i backup.", "bool", True, section="backup", icon="📄"),
        SettingDefinition("backup.include_statistics", "Inkludera intygsstatistik", "Ta med statistikfilen med intygsposter.", "bool", True, section="backup", icon="📊"),
        SettingDefinition("notifications.enabled", "Aktivera notifikationer", "Visa appnotifikationer.", "bool", True, section="notifications", icon="🔔"),
        SettingDefinition("notifications.on_complete", "Vid färdigt intyg", "Notifiera när intyg skapats.", "bool", True, section="notifications", icon="✅", depends_on="notifications.enabled"),
        SettingDefinition("notifications.on_error", "Vid fel", "Notifiera vid fel och varningar.", "bool", True, section="notifications", icon="❌", depends_on="notifications.enabled"),
        SettingDefinition("notifications.duration_seconds", "Visningstid (sekunder)", "Hur länge notifieringar visas.", "int", 5, min_value=1, max_value=30, section="notifications", icon="⏱️", depends_on="notifications.enabled"),
        SettingDefinition("advanced.debug_mode", "Debug-läge", "Visa extra felsökningsinformation.", "bool", False, section="advanced", icon="🐛"),
        SettingDefinition("advanced.log_level", "Loggnivå", "Detaljnivå för loggning.", "choice", "INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], section="advanced"),
        SettingDefinition("advanced.max_log_size_mb", "Max loggstorlek (MB)", "Storlek innan loggfil roteras.", "int", 10, min_value=1, max_value=100, section="advanced"),
        SettingDefinition("advanced.log_retention_days", "Behåll loggar (dagar)", "Antal dagar att behålla gamla loggar.", "int", 30, min_value=1, max_value=365, section="advanced", icon="📅"),
        SettingDefinition("advanced.parallel_jobs", "Parallella jobb", "Max antal samtidiga bakgrundsjobb.", "int", 2, min_value=1, max_value=8, section="advanced", icon="⚡"),
        SettingDefinition("advanced.cache_enabled", "Aktivera cache", "Cacha data för snabbare åtkomst.", "bool", True, section="advanced", icon="💨"),
    ]

    TAB_DEFINITIONS: list[TabInfo] = [
        TabInfo("startup", "Start", "🚀", True, 0),
        TabInfo("general", "Allmänt", "⚙️", True, 1),
        TabInfo("appearance", "Utseende", "🎨", True, 2),
        TabInfo("shortcuts", "Genvägar", "⌨️", True, 3),
        TabInfo("pdf", "PDF", "📄", True, 4),
        TabInfo("backup", "Backup", "💾", True, 5),
        TabInfo("profiles", "Profiler", "👤", True, 6),
        TabInfo("notifications", "Notiser", "🔔", True, 7),
        TabInfo("plugins", "Plugins", "🧩", True, 8),
        TabInfo("advanced", "Avancerat", "🔧", True, 9),
        TabInfo("diagnostics", "Diagnostik", "🏥", True, 10),
        TabInfo("about", "Om", "ℹ️", True, 11),
    ]

    def __init__(self) -> None:
        self._context: AppContext | Any | None = None
        self._event_bus: Any | None = None
        self._log_fn: Any | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._lock = threading.RLock()
        self._loaded = False
        self._thread_pool = QThreadPool.globalInstance()
        self._definitions = {definition.key: definition for definition in self.SETTING_DEFINITIONS}
        self._profiles: dict[str, Profile] = {}
        self._current_profile: str | None = None
        self._requires_restart = False
        self.backup_path: Path | None = None
        self.profiles_path: Path | None = None
        self._theme_manager: ThemeManager | None = None
        self._shortcut_manager: ShortcutManager | None = None
        self._autostart_manager = AutostartManager()
        self._shell: SettingsShell | None = None
        self._setting_widgets: dict[str, Any] = {}
        self._shortcut_tree: QTreeWidget | None = None
        self._backup_tree: QTreeWidget | None = None
        self._profiles_tree: QTreeWidget | None = None
        self._plugins_tree: QTreeWidget | None = None
        self._health_tree: QTreeWidget | None = None
        self._autostart_status: QLabel | None = None
        self._system_info_layout: QVBoxLayout | None = None
        self._theme_buttons: dict[str, QPushButton] = {}
        # Fix 8.8.0: konverterardetektering i bakgrund (UI-tråden får aldrig
        # blockeras av shutil.which:s PATH×PATHEXT-skanning).
        self._converter_cache: list[str] | None = None
        self._converter_started: bool = False
        self._converter_lock = threading.Lock()

    # ── IPlugin ───────────────────────────────────────────────────────────

    def on_load(self, context: Any) -> None:
        if self._loaded:
            return
        self._context = context
        self._event_bus = getattr(context, "event_bus", None)
        get_logger = getattr(context, "get_logger", None)
        if callable(get_logger):
            try:
                self._log_fn = get_logger("Settings")
            except Exception:
                _logger.exception("[SETTINGS] Kunde inte hämta applogger")
        self._setup_paths()
        self._theme_manager = ThemeManager()
        self._shortcut_manager = ShortcutManager(self._settings_provider())
        self._ensure_defaults()
        self._load_profiles()
        self._subscribe_events()
        self._apply_saved_theme()
        self._sync_autostart()
        self._sync_tray()
        self._loaded = True
        self._start_converter_detection()  # 8.8.0: varm cache innan flikar öppnas
        if self.get_setting("backup.auto_backup", True):
            QTimer.singleShot(0, self._check_auto_backup)
        self._log("info", "[SETTINGS] v%s initierad (core=%s)", self.version, CORE_VERSION)

    def setup(self, context: Any) -> None:
        self.on_load(context)

    def on_unload(self) -> None:
        self._loaded = False
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte avregistrera event")
        self._unsubscribers.clear()
        self._save_settings()
        self._context = None
        self._event_bus = None

    def on_close(self) -> None:
        self.on_unload()

    def get_tab(self) -> QWidget | None:
        if self._shell is not None:
            return self._shell
        try:
            self._shell = SettingsShell(COPYRIGHT)
            self._shell.export_requested.connect(self._export_settings_dialog)
            self._shell.import_requested.connect(self._import_settings_dialog)
            self._shell.search_changed.connect(self._on_search_changed)
            self._shell.clear_search_requested.connect(lambda: self._set_status("Redo"))
            self._build_tabs()
            self._refresh_all_lists()
            return self._shell
        except Exception as exc:
            self._log("error", "[SETTINGS] get_tab: %s", exc)
            widget = QWidget()
            layout = QVBoxLayout(widget)
            message = QLabel(f"⚠️ Settings UI kunde inte laddas:\n{exc}", widget)
            message.setWordWrap(True)
            layout.addWidget(message)
            return widget

    # ── Context, config och EventBus ──────────────────────────────────────

    def _settings_provider(self) -> Any | None:
        context = self._context
        if context is None:
            return None
        return (
            getattr(context, "settings", None)
            or getattr(context, "config_manager", None)
            or getattr(context, "config", None)
        )

    def _setup_paths(self) -> None:
        base = Path(getattr(self._context, "base_path", Path(".")))
        backup = getattr(self._context, "get_path", None)
        config_path: Path | None = None
        if callable(backup):
            try:
                config_path = Path(backup("config"))
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte hämta config-sökväg")
        self.backup_path = base / "Backup"
        self.backup_path.mkdir(parents=True, exist_ok=True)
        # Profiles skapas aldrig vid startup. Mappen skapas enbart vid explicit
        # profilskapande, profilimport eller användarinitierad återställning.
        self.profiles_path = (config_path or base / "js") / "profiles"

    def _ensure_defaults(self) -> None:
        settings = self._settings_provider()
        if settings is None:
            return
        defaults = {definition.key: definition.default for definition in self.SETTING_DEFINITIONS}
        setter = getattr(settings, "set_defaults", None) or getattr(settings, "ensure_defaults", None)
        if callable(setter):
            try:
                setter(defaults)
                return
            except Exception:
                self._log("error", "[SETTINGS] set_defaults misslyckades")
        for key, value in defaults.items():
            if self._setting_get(key, None) is None:
                self._setting_set(key, value, auto_save=False)
        self._save_settings()

    def _setting_get(self, key: str, default: Any = None) -> Any:
        settings = self._settings_provider()
        getter = getattr(settings, "get", None) if settings is not None else None
        if not callable(getter):
            return default
        try:
            return getter(key, default)
        except TypeError:
            try:
                value = getter(key)
                return default if value is None else value
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte läsa %s", key)
                return default
        except Exception:
            self._log("error", "[SETTINGS] Kunde inte läsa %s", key)
            return default

    def _setting_set(self, key: str, value: Any, *, auto_save: bool = True) -> bool:
        settings = self._settings_provider()
        setter = getattr(settings, "set", None) if settings is not None else None
        if not callable(setter):
            return False
        try:
            setter(key, value, notify=False, auto_save=auto_save)
            return True
        except TypeError:
            try:
                setter(key, value)
                return True
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte spara %s", key)
                return False
        except Exception:
            self._log("error", "[SETTINGS] Kunde inte spara %s", key)
            return False

    def _save_settings(self) -> None:
        settings = self._settings_provider()
        saver = getattr(settings, "save", None) if settings is not None else None
        if callable(saver):
            try:
                saver()
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte spara inställningar")

    def _subscribe_events(self) -> None:
        if self._event_bus is None:
            return
        callbacks: tuple[tuple[str, Callable[[Any], None]], ...] = (
            ("show_settings", self._on_show_settings),
            ("refresh_ui", self._on_refresh_ui),
            ("plugin.loaded", self._on_plugin_change),
            ("plugin.unloaded", self._on_plugin_change),
            ("settings.theme_changed", self._on_theme_event),
        )
        for event_name, callback in callbacks:
            try:
                unsubscribe = self._event_bus.subscribe(event_name, callback)
                if callable(unsubscribe):
                    self._unsubscribers.append(unsubscribe)
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte prenumerera på %s", event_name)

    def _emit(self, event_name: str, data: dict[str, Any]) -> None:
        context = self._context
        emitter = getattr(context, "emit_event", None) if context is not None else None
        if callable(emitter):
            try:
                emitter(event_name, data)
                return
            except Exception:
                self._log("error", "[SETTINGS] Event %s misslyckades", event_name)
        bus = self._event_bus
        emit = getattr(bus, "emit", None) if bus is not None else None
        if callable(emit):
            try:
                emit(event_name, data)
            except Exception:
                self._log("error", "[SETTINGS] EventBus %s misslyckades", event_name)

    # ── Settings-API ──────────────────────────────────────────────────────

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self._setting_get(key, default)

    def set_setting(self, key: str, value: Any, validate: bool = True) -> tuple[bool, str]:
        if key == "app.autostart":
            key = "app.start_with_os"
        definition = self._definitions.get(key)
        if validate and definition is not None:
            valid, error = definition.validate(value)
            if not valid:
                return False, error
            value = definition.get_typed_value(value)
        old_value = self._setting_get(key)
        if not self._setting_set(key, value):
            return False, "Inställningslagringen är inte tillgänglig"
        if definition is not None and definition.requires_restart and old_value != value:
            self._requires_restart = True
            if self._shell is not None:
                self._shell.set_restart_required(True)
        self._handle_special_setting(key, value, old_value)
        self._emit("settings.changed", {"key": key, "value": value, "old_value": old_value})
        return True, ""

    def _handle_special_setting(self, key: str, value: Any, _old_value: Any) -> None:
        if key in {"app.start_with_os", "app.autostart"}:
            actual = self._autostart_manager.set_enabled(bool(value))
            self._setting_set("app.start_with_os", actual)
            # Keep the old key synchronized for configs saved by prior versions.
            self._setting_set("app.autostart", actual)
            self._update_autostart_status()
        elif key.startswith("app.tray."):
            self._sync_tray()
        elif key == "general.theme":
            self._apply_saved_theme()
            self._emit("settings.theme_changed", {"theme": str(value)})
        elif key == "general.font_size":
            self._apply_font_size(int(value))

    def _apply_saved_theme(self) -> None:
        if self._theme_manager is not None:
            self._theme_manager.apply_theme(Theme.from_string(str(self.get_setting("general.theme", "system"))))

    def _apply_font_size(self, size: int) -> None:
        app = QApplication.instance()
        if app is not None:
            font = app.font()
            font.setPointSize(max(8, min(18, size)))
            app.setFont(font)

    def _sync_autostart(self) -> None:
        actual = self._autostart_manager.sync()
        saved = bool(self.get_setting("app.start_with_os", False))
        if actual != saved:
            self._setting_set("app.start_with_os", actual)
            self._setting_set("app.autostart", actual)

    def _sync_tray(self) -> None:
        tray = getattr(self._context, "tray_config", None) if self._context is not None else None
        if tray is None:
            return
        for attribute, key, default in (
            ("minimize_to_tray", "app.tray.minimize_to_tray", True),
            ("start_minimized", "app.tray.start_minimized", True),
            ("show_notifications", "app.tray.show_notifications", True),
            ("single_click_open", "app.tray.single_click_open", True),
        ):
            try:
                setattr(tray, attribute, bool(self.get_setting(key, default)))
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte synka tray-inställning %s", key)

    # ── Profiler ──────────────────────────────────────────────────────────

    def _load_profiles(self) -> None:
        if self.profiles_path is None or not self.profiles_path.is_dir():
            return
        for file_path in self.profiles_path.glob("*.json"):
            try:
                profile = Profile.from_dict(json.loads(file_path.read_text(encoding="utf-8")))
                self._profiles[profile.name] = profile
                if profile.is_default:
                    self._current_profile = profile.name
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte läsa profil %s", file_path.name)

    def _ensure_profiles_path(self) -> Path:
        if self.profiles_path is None:
            raise RuntimeError("Profilsökväg saknas")
        self.profiles_path.mkdir(parents=True, exist_ok=True)
        return self.profiles_path

    def create_profile(self, name: str, description: str = "") -> Profile | None:
        clean_name = name.strip()
        if not clean_name or clean_name in self._profiles:
            return None
        profile = Profile(
            name=clean_name,
            description=description.strip(),
            settings={definition.key: self.get_setting(definition.key, definition.default) for definition in self.SETTING_DEFINITIONS},
        )
        self._profiles[profile.name] = profile
        return profile if self._save_profile(profile) else None

    def _save_profile(self, profile: Profile) -> bool:
        try:
            folder = self._ensure_profiles_path()
            profile.update_modified()
            destination = folder / f"{_safe_profile_filename(profile.name)}.json"
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(destination)
            return True
        except Exception:
            self._log("error", "[SETTINGS] Kunde inte spara profil %s", profile.name)
            return False

    def load_profile(self, name: str) -> bool:
        profile = self._profiles.get(name)
        if profile is None:
            return False
        for key, value in profile.settings.items():
            self._setting_set(key, value, auto_save=False)
        self._save_settings()
        self._current_profile = name
        self._sync_autostart()
        self._sync_tray()
        self._apply_saved_theme()
        self._emit("settings.profile_loaded", {"profile": name})
        return True

    def delete_profile(self, name: str) -> bool:
        profile = self._profiles.pop(name, None)
        if profile is None:
            return False
        if self.profiles_path is not None:
            with suppress(OSError):
                (self.profiles_path / f"{_safe_profile_filename(name)}.json").unlink()
        if self._current_profile == name:
            self._current_profile = None
        return True

    def get_profiles(self) -> list[Profile]:
        return sorted(self._profiles.values(), key=lambda profile: profile.created, reverse=True)

    # ── Backup och restore ─────────────────────────────────────────────────

    def create_backup(
        self,
        include_logs: bool | None = None,
        include_output: bool | None = None,
        callback: Callable[[bool, str], None] | None = None,
    ) -> None:
        if self.backup_path is None or self._context is None:
            return
        options = BackupOptions(
            include_logs=bool(self.get_setting("backup.include_logs", False) if include_logs is None else include_logs),
            include_output=bool(self.get_setting("backup.include_output", False) if include_output is None else include_output),
            include_templates=bool(self.get_setting("backup.include_templates", True)),
            include_statistics=bool(self.get_setting("backup.include_statistics", True)),
            app_version="0.0.1",
        )
        worker = BackupWorker(self.backup_path, self._context, options, self.profiles_path if self.profiles_path and self.profiles_path.is_dir() else None)
        worker.signals.progress.connect(self._on_backup_progress, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(lambda success, message: self._on_backup_finished(success, message, callback), Qt.ConnectionType.QueuedConnection)
        self._thread_pool.start(worker)

    @Slot(str, int)
    def _on_backup_progress(self, message: str, percent: int) -> None:
        self._set_status(f"💾 {message} ({percent}%)")

    def _on_backup_finished(self, success: bool, message: str, callback: Callable[[bool, str], None] | None) -> None:
        if success:
            self._setting_set("backup.last_auto_backup", datetime.now().isoformat(timespec="seconds"))
            self._cleanup_old_backups()
            self._refresh_backup_list()
            self._set_status(f"✅ {message}")
        else:
            self._set_status(f"❌ Backup misslyckades: {message}")
        if callback is not None:
            try:
                callback(success, message)
            except Exception:
                self._log("error", "[SETTINGS] Backup-callback misslyckades")

    def restore_backup(self, backup_file: Path, callback: Callable[[bool, str], None] | None = None) -> None:
        if self._context is None:
            return
        # Användaren har uttryckligen begärt restore; profiler får därför skapas.
        profiles_destination = self._ensure_profiles_path()
        worker = RestoreWorker(backup_file, self._context, profiles_destination)
        worker.signals.progress.connect(lambda message, percent: self._set_status(f"📥 {message} ({percent}%)"), Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(lambda success, message: self._on_restore_finished(success, message, callback), Qt.ConnectionType.QueuedConnection)
        self._thread_pool.start(worker)

    def _on_restore_finished(self, success: bool, message: str, callback: Callable[[bool, str], None] | None) -> None:
        if success:
            self._profiles.clear()
            self._load_profiles()
            self._refresh_profiles_list()
            self._set_status(f"✅ {message}")
        else:
            self._set_status(f"❌ Återställning misslyckades: {message}")
        if callback is not None:
            try:
                callback(success, message)
            except Exception:
                self._log("error", "[SETTINGS] Restore-callback misslyckades")

    def get_backups(self) -> list[dict[str, Any]]:
        if self.backup_path is None or not self.backup_path.is_dir():
            return []
        entries: list[tuple[float, Path]] = []
        for file_path in self.backup_path.glob("backup_*.zip"):
            try:
                stat = file_path.stat()
            except OSError:
                self._log("error", "[SETTINGS] Kunde inte läsa backup %s", file_path)
                continue
            entries.append((stat.st_mtime, file_path))
        backups: list[dict[str, Any]] = []
        for mtime, file_path in sorted(entries, reverse=True):
            try:
                stat = file_path.stat()
                backups.append({
                    "file": file_path,
                    "name": file_path.name,
                    "size": stat.st_size,
                    "date": datetime.fromtimestamp(stat.st_mtime),
                    "version": self._backup_version(file_path),
                })
            except OSError:
                self._log("error", "[SETTINGS] Kunde inte läsa backup %s", file_path)
        return backups

    @staticmethod
    def _backup_version(path: Path) -> str:
        try:
            with zipfile.ZipFile(path) as archive:
                return str(json.loads(archive.read("_metadata.json").decode("utf-8")).get("version", "?"))
        except Exception:
            return "?"

    def _cleanup_old_backups(self) -> None:
        limit = max(1, int(self.get_setting("backup.max_backups", 10)))
        for backup in self.get_backups()[limit:]:
            try:
                Path(backup["file"]).unlink()
            except OSError as exc:
                self._log("error", "[SETTINGS] Kunde inte ta bort gammal backup %s: %s", backup["name"], exc)

    def _check_auto_backup(self) -> None:
        last = _safe_datetime(self.get_setting("backup.last_auto_backup", ""))
        interval = max(1, int(self.get_setting("backup.backup_interval_days", 7)))
        if not self.get_setting("backup.last_auto_backup", None) or (datetime.now() - last).days >= interval:
            self.create_backup()

    # ── Diagnostik ────────────────────────────────────────────────────────

    def run_health_checks(self) -> list[HealthCheck]:
        checks: list[HealthCheck] = []
        templates = self._context_path("templates")
        valid_templates = self._count_valid_template_folders(templates)
        checks.append(HealthCheck("Mallar", HealthStatus.OK if valid_templates else HealthStatus.WARNING, f"{valid_templates} giltiga mallar", category="Mallar"))
        converters = self._available_converters()
        checks.append(HealthCheck("PDF", HealthStatus.OK if converters else HealthStatus.ERROR, ", ".join(converters) if converters else "Ingen PDF-konverterare hittades", category="PDF"))
        backups = self.get_backups()
        checks.append(HealthCheck("Backup", HealthStatus.OK if backups else HealthStatus.WARNING, backups[0]["name"] if backups else "Ingen backup hittades", category="Backup"))
        checks.append(HealthCheck("Autostart", HealthStatus.OK, "Aktiverad" if self._autostart_manager.is_enabled() else "Inaktiverad", category="System"))
        py = sys.version_info
        checks.append(HealthCheck("Python", HealthStatus.OK if py >= (3, 12) else HealthStatus.ERROR, f"{py.major}.{py.minor}.{py.micro}", category="System"))
        logs = self._context_path("logs")
        checks.append(HealthCheck("Loggar", HealthStatus.OK if logs and logs.exists() else HealthStatus.WARNING, str(logs) if logs else "Loggmapp saknas", category="System"))
        try:
            import psutil

            memory = psutil.Process().memory_info().rss / (1024 * 1024)
            checks.append(HealthCheck("Minne", HealthStatus.WARNING if memory > 500 else HealthStatus.OK, f"{memory:.0f} MB", category="System"))
        except ImportError:
            checks.append(HealthCheck("Minne", HealthStatus.UNKNOWN, "psutil saknas", category="System"))
        return checks

    def _context_path(self, key: str) -> Path | None:
        getter = getattr(self._context, "get_path", None) if self._context is not None else None
        if callable(getter):
            try:
                value = getter(key)
                return Path(value) if value else None
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte hämta %s", key)
        return None

    @staticmethod
    def _count_valid_template_folders(root: Path | None) -> int:
        if root is None or not root.is_dir():
            return 0
        count = 0
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name.startswith((".", "_", "~")):
                continue
            try:
                if any(item.is_file() and item.suffix.casefold() == ".docx" and not item.name.startswith(("~$", ".")) for item in folder.iterdir()):
                    count += 1
            except OSError:
                continue
        return count

    def _available_converters(self) -> list[str]:
        """Icke-blockerande PDF-konverterardetektering (fix 8.8.0).

        Tidigare kördes ``shutil.which`` (PATH × PATHEXT, 12 exists-anrop per
        PATH-mapp — inklusive sega nätverks-/WindowsApps-poster) synkront i
        UI-tråden och låste appen. Nu: cachat resultat; den dyra skanningen
        körs i bakgrundstråd; UI-tråden får omedelbart ett snabbt glob-svar
        mot kända LibreOffice-platser (eller tidigare cachat resultat).
        """
        with self._converter_lock:
            if self._converter_cache is not None:
                return list(self._converter_cache)
            if not self._converter_started:
                self._converter_started = True
                threading.Thread(
                    target=self._detect_converters,
                    name="ConverterDetect",
                    daemon=True,
                ).start()
        return self._fast_libreoffice_guess()

    def _start_converter_detection(self) -> None:
        """Starta bakgrundsdetektering tidigt så cachen är varm när UI öppnas."""
        with suppress(Exception):
            self._available_converters()

    def _fast_libreoffice_guess(self) -> list[str]:
        """Snabb glob mot vanliga installationsplatser — ingen PATH-probing."""
        guess: list[str] = []
        if sys.platform == "win32":
            import glob as _glob

            roots = [
                os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                os.environ.get("LOCALAPPDATA", ""),
            ]
            for root in roots:
                if not root:
                    continue
                with suppress(Exception):
                    pattern = str(Path(root) / "LibreOffice*" / "program" / "soffice.exe")
                    if _glob.glob(pattern):
                        guess.append("LibreOffice")
                        break
        return guess

    def _detect_converters(self) -> None:
        """Bakgrundstråd: den dyra detekteringen, med loggning av tid."""
        started = time.monotonic()
        result: list[str] = []
        try:
            if "LibreOffice" in self._fast_libreoffice_guess():
                result.append("LibreOffice")
            elif any(shutil.which(cmd) for cmd in ("libreoffice", "soffice")):
                result.append("LibreOffice")
            try:
                import docx2pdf  # noqa: F401

                result.append("docx2pdf")
            except ImportError:
                pass
            if sys.platform == "win32":
                try:
                    import win32com.client  # noqa: F401

                    result.append("Microsoft Word")
                except ImportError:
                    pass
        except Exception:
            _logger.exception("[SETTINGS] Konverterardetektering misslyckades")
        finally:
            with self._converter_lock:
                self._converter_cache = result
            self._log(
                "info",
                "[SETTINGS] PDF-konverterare detekterade på %.0f ms: %s",
                (time.monotonic() - started) * 1000,
                ", ".join(result) or "inga",
            )

    def get_system_info(self) -> dict[str, str]:
        return {
            "version": self.version,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": PlatformHelper.get_platform_name(),
            "app_path": str(getattr(self._context, "base_path", Path("."))),
            "pdf_converters": ", ".join(self._available_converters()) or "Ingen",
            "autostart": "Aktiverad" if self._autostart_manager.is_enabled() else "Inaktiverad",
        }

    # ── UI-tabbyggare ─────────────────────────────────────────────────────

    def _build_tabs(self) -> None:
        if self._shell is None:
            return
        builders: dict[str, Callable[[SettingsScrollTab], None]] = {
            "startup": self._build_startup_tab,
            "general": self._build_general_tab,
            "appearance": self._build_appearance_tab,
            "shortcuts": self._build_shortcuts_tab,
            "pdf": self._build_pdf_tab,
            "backup": self._build_backup_tab,
            "profiles": self._build_profiles_tab,
            "notifications": self._build_notifications_tab,
            "plugins": self._build_plugins_tab,
            "advanced": self._build_advanced_tab,
            "diagnostics": self._build_diagnostics_tab,
            "about": self._build_about_tab,
        }
        for info in sorted(self.TAB_DEFINITIONS, key=lambda tab: tab.order):
            tab = self._shell.add_scroll_tab(info)
            builder = builders.get(info.id)
            if builder is not None:
                try:
                    builder(tab)
                except Exception as exc:
                    self._log("error", "[SETTINGS] Byggfel %s: %s", info.id, exc)
                    tab.content_layout.addWidget(QLabel(f"❌ Kunde inte bygga fliken:\n{exc}", tab.content))
            tab.add_stretch()

    def _build_startup_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("🚀 Autostart", "Konfigurera programstart med operativsystemet.")
        layout = box.layout()
        autostart = QCheckBox("Starta med operativsystemet", box)
        autostart.setChecked(self._autostart_manager.is_enabled())
        autostart.stateChanged.connect(lambda state: self._on_autostart_changed(bool(state), autostart))
        layout.addWidget(autostart)
        self._autostart_status = QLabel(box)
        layout.addWidget(self._autostart_status)
        self._update_autostart_status()
        tray_box = tab.add_section("📥 Systemfält", "Bestäm hur huvudfönstret beter sig tillsammans med tray-pluginet.")
        for definition in self._definitions_for_section("startup"):
            if definition.key != "app.start_with_os":
                self._add_setting_widget(tray_box.layout(), definition)
        quit_box = tab.add_section("🚪 Avsluta program", "Avslut sker alltid från meny eller denna knapp.")
        quit_box.layout().addWidget(make_button_row(make_action_button("🚪 Avsluta programmet helt", self._handle_quit_request, quit_box), parent=quit_box))

    def _build_general_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("⚙️ Allmänna inställningar")
        for definition in self._definitions_for_section("general"):
            if definition.key not in {"general.theme", "general.font_size"}:
                self._add_setting_widget(box.layout(), definition)

    def _build_appearance_tab(self, tab: SettingsScrollTab) -> None:
        theme_box = tab.add_section("🎨 Tema", "Exakt tre teman: system, ljust och mörkt.")
        row = QHBoxLayout()
        current = str(self.get_setting("general.theme", "system"))
        self._theme_buttons: dict[str, QPushButton] = {}
        for theme in Theme:
            button = make_action_button(f"{theme.icon} {theme.display_name}", lambda selected=theme: self._select_theme(selected), parent=theme_box)
            button.setCheckable(True)
            button.setChecked(theme.value == current)
            row.addWidget(button)
            self._theme_buttons[theme.value] = button
        row.addStretch(1)
        theme_box.layout().addLayout(row)
        size_box = tab.add_section("🔤 Textstorlek")
        definition = self._definitions["general.font_size"]
        self._add_setting_widget(size_box.layout(), definition)

    def _build_shortcuts_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("⌨️ Tangentbordsgenvägar", "Dubbelklicka på en genväg för att redigera den.")
        self._shortcut_tree = QTreeWidget(box)
        self._shortcut_tree.setHeaderLabels(["", "Åtgärd", "Genväg", "Standard", "Kategori"])
        self._shortcut_tree.setAlternatingRowColors(True)
        self._shortcut_tree.setMinimumHeight(300)
        self._shortcut_tree.itemDoubleClicked.connect(lambda _item, _column: self._edit_selected_shortcut())
        box.layout().addWidget(self._shortcut_tree)
        buttons = make_button_row(
            make_action_button("✏️ Redigera", self._edit_selected_shortcut, box),
            make_action_button("↩️ Återställ vald", self._reset_selected_shortcut, box),
            make_action_button("🔄 Återställ alla", self._reset_all_shortcuts, box),
            parent=box,
        )
        box.layout().addWidget(buttons)

    def _build_pdf_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("📄 Tillgängliga PDF-konverterare")
        available = set(self._available_converters())
        for name in ("LibreOffice", "Microsoft Word", "docx2pdf"):
            box.layout().addWidget(QLabel(f"{'✅' if name in available else '❌'} {name}: {'Tillgänglig' if name in available else 'Ej installerad'}", box))
        settings_box = tab.add_section("PDF-inställningar")
        for definition in self._definitions_for_section("pdf"):
            self._add_setting_widget(settings_box.layout(), definition)

    def _build_backup_tab(self, tab: SettingsScrollTab) -> None:
        actions = tab.add_section("💾 Backup-åtgärder")
        actions.layout().addWidget(make_button_row(
            make_action_button("💾 Skapa backup nu", self._create_backup_now, actions),
            make_action_button("📥 Återställ", self._restore_backup_dialog, actions),
            make_action_button("📂 Öppna mapp", lambda: PlatformHelper.open_folder(self.backup_path) if self.backup_path else None, actions),
            parent=actions,
        ))
        options = tab.add_section("Backupinnehåll och automatisk backup")
        for definition in self._definitions_for_section("backup"):
            self._add_setting_widget(options.layout(), definition)
        list_box = tab.add_section("Tillgängliga backups")
        self._backup_tree = QTreeWidget(list_box)
        self._backup_tree.setHeaderLabels(["Namn", "Datum", "Storlek", "Version"])
        self._backup_tree.setAlternatingRowColors(True)
        self._backup_tree.setMinimumHeight(180)
        list_box.layout().addWidget(self._backup_tree)

    def _build_profiles_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("👤 Konfigurationsprofiler", "Profiler skapas endast när du uttryckligen väljer att skapa eller importera en.")
        self._profiles_tree = QTreeWidget(box)
        self._profiles_tree.setHeaderLabels(["", "Namn", "Beskrivning", "Skapad"])
        self._profiles_tree.setAlternatingRowColors(True)
        self._profiles_tree.setMinimumHeight(220)
        self._profiles_tree.itemDoubleClicked.connect(lambda _item, _column: self._load_selected_profile())
        box.layout().addWidget(self._profiles_tree)
        box.layout().addWidget(make_button_row(
            make_action_button("➕ Skapa profil", self._create_profile_dialog, box),
            make_action_button("📂 Ladda", self._load_selected_profile, box),
            make_action_button("🗑️ Ta bort", self._delete_selected_profile, box),
            make_action_button("📤 Exportera", self._export_profile_dialog, box),
            make_action_button("📥 Importera", self._import_profile_dialog, box),
            parent=box,
        ))

    def _build_notifications_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("🔔 Notifikationer")
        for definition in self._definitions_for_section("notifications"):
            self._add_setting_widget(box.layout(), definition)

    def _build_plugins_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("🧩 Installerade plugins")
        self._plugins_tree = QTreeWidget(box)
        self._plugins_tree.setHeaderLabels(["", "Namn", "Version", "Beskrivning", "Prioritet"])
        self._plugins_tree.setAlternatingRowColors(True)
        self._plugins_tree.setMinimumHeight(250)
        box.layout().addWidget(self._plugins_tree)
        box.layout().addWidget(make_button_row(make_action_button("🔄 Uppdatera", self._refresh_plugins_list, box), parent=box))

    def _build_advanced_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("🔧 Avancerade inställningar", "Ändra bara avancerade inställningar om du förstår följderna.")
        for definition in self._definitions_for_section("advanced"):
            self._add_setting_widget(box.layout(), definition)
        actions = tab.add_section("⚠️ Åtgärder")
        actions.layout().addWidget(make_button_row(
            make_action_button("🔄 Återställ alla", self._reset_all_settings, actions),
            make_action_button("🗑️ Rensa cache", self._clear_cache, actions),
            make_action_button("🧹 Rensa loggar", self._clear_logs, actions),
            parent=actions,
        ))

    def _build_diagnostics_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("🏥 Systemhälsa")
        self._health_tree = QTreeWidget(box)
        self._health_tree.setHeaderLabels(["", "Kontroll", "Status", "Kategori"])
        self._health_tree.setAlternatingRowColors(True)
        self._health_tree.setMinimumHeight(250)
        box.layout().addWidget(self._health_tree)
        box.layout().addWidget(make_button_row(
            make_action_button("🔄 Kör diagnostik", self._run_diagnostics, box),
            make_action_button("📋 Kopiera rapport", self._copy_diagnostics_report, box),
            make_action_button("📦 Installera saknade tillägg", self._open_dependency_dialog, box),
            parent=box,
        ))
        info_box = tab.add_section("Systeminformation")
        self._system_info_layout = QVBoxLayout()
        info_box.layout().addLayout(self._system_info_layout)
        info_box.layout().addWidget(make_button_row(make_action_button("📋 Kopiera info", self._copy_system_info, info_box), parent=info_box))

    def _build_about_tab(self, tab: SettingsScrollTab) -> None:
        box = tab.add_section("ℹ️ Om IntygsDrive")
        title = QLabel("IntygsDrive", box)
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 6)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.layout().addWidget(title)
        for text in (f"Settings Plugin v{self.version}", COPYRIGHT, "Pluginbaserat intygssystem för Windows, macOS och Linux."):
            label = QLabel(text, box)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            box.layout().addWidget(label)
        box.layout().addWidget(make_button_row(make_action_button("📂 Öppna app-mapp", lambda: PlatformHelper.open_folder(Path(getattr(self._context, "base_path", Path(".")))), box), make_action_button("📋 Kopiera info", self._copy_system_info, box), parent=box))

    def _definitions_for_section(self, section: str) -> list[SettingDefinition]:
        return [definition for definition in self.SETTING_DEFINITIONS if definition.section == section]

    def _add_setting_widget(self, layout: QVBoxLayout, definition: SettingDefinition) -> None:
        control = add_setting_control(layout, definition, self.get_setting(definition.key, definition.default), lambda key, value: self.set_setting(key, value), self._setting_widgets)
        dependency = definition.depends_on
        if dependency:
            control.setEnabled(bool(self.get_setting(dependency, False)))

    # ── UI uppdateringar och actions ──────────────────────────────────────

    def _refresh_all_lists(self) -> None:
        self._refresh_shortcuts_list()
        self._refresh_backup_list()
        self._refresh_profiles_list()
        self._refresh_plugins_list()
        self._run_diagnostics()
        self._refresh_system_info()

    def _refresh_shortcuts_list(self) -> None:
        if self._shortcut_tree is None or self._shortcut_manager is None:
            return
        self._shortcut_tree.clear()
        for category, shortcuts in self._shortcut_manager.get_by_category().items():
            category_item = QTreeWidgetItem([f"📂 {category}", "", "", "", ""])
            self._shortcut_tree.addTopLevelItem(category_item)
            for shortcut in shortcuts:
                item = QTreeWidgetItem([shortcut.icon, shortcut.label, shortcut.current_key, shortcut.default_key, shortcut.category])
                item.setData(0, Qt.ItemDataRole.UserRole, shortcut.action)
                category_item.addChild(item)
            category_item.setExpanded(True)

    def _refresh_backup_list(self) -> None:
        if self._backup_tree is None:
            return
        self._backup_tree.clear()
        for backup in self.get_backups():
            item = QTreeWidgetItem([backup["name"], backup["date"].strftime("%Y-%m-%d %H:%M"), self._format_size(backup["size"]), backup["version"]])
            item.setData(0, Qt.ItemDataRole.UserRole, str(backup["file"]))
            self._backup_tree.addTopLevelItem(item)

    def _refresh_profiles_list(self) -> None:
        if self._profiles_tree is None:
            return
        self._profiles_tree.clear()
        for profile in self.get_profiles():
            marker = "▶" if profile.name == self._current_profile else ""
            item = QTreeWidgetItem([profile.icon, f"{marker} {profile.name}".strip(), profile.description[:60], profile.created.strftime("%Y-%m-%d")])
            item.setData(0, Qt.ItemDataRole.UserRole, profile.name)
            self._profiles_tree.addTopLevelItem(item)

    def _refresh_plugins_list(self) -> None:
        if self._plugins_tree is None:
            return
        self._plugins_tree.clear()
        manager = getattr(self._context, "_plugin_manager", None) or getattr(self._context, "plugin_manager", None)
        info_getter = getattr(manager, "get_plugin_info", None)
        if callable(info_getter):
            try:
                for info in info_getter():
                    enabled = bool(info.get("enabled", True)) and not info.get("error")
                    self._plugins_tree.addTopLevelItem(QTreeWidgetItem(["✅" if enabled else "❌", str(info.get("name", "–")), str(info.get("version", "–")), str(info.get("description", ""))[:60], str(info.get("priority", "–"))]))
                return
            except Exception:
                self._log("error", "[SETTINGS] Kunde inte läsa plugininfo")
        self._plugins_tree.addTopLevelItem(QTreeWidgetItem(["✅", self.name, self.version, self.description[:60], str(self.priority)]))

    def _run_diagnostics(self) -> None:
        if self._health_tree is None:
            return
        self._health_tree.clear()
        for check in self.run_health_checks():
            self._health_tree.addTopLevelItem(QTreeWidgetItem([check.status.icon, check.name, check.message, check.category]))

    def _open_dependency_dialog(self) -> None:
        """v8.9.0: erbjud installation när tillägg/office-sviter saknas."""
        try:
            dialog = _DependencyDialog(
                DependencyAdvisor.missing_python_packages(),
                DependencyAdvisor.office_suite_status(),
                on_changed=self._after_dependency_change,
                parent=self._shell,
            )
            self._log("info", "[SETTINGS] Beroendedialog öppnad")
            dialog.exec()
        except Exception:
            _logger.exception("[SETTINGS] Kunde inte öppna beroendedialog")

    def _after_dependency_change(self) -> None:
        """Efter lyckad installation: uppdatera detektering + diagnostik."""
        with self._converter_lock:
            self._converter_cache = None
            self._converter_started = False
        with suppress(Exception):
            self._start_converter_detection()
        with suppress(Exception):
            self._run_diagnostics()
        with suppress(Exception):
            self._refresh_system_info()
        self._log("info", "[SETTINGS] Detektering uppdaterad efter installation")

    def _refresh_system_info(self) -> None:
        if self._system_info_layout is None:
            return
        while self._system_info_layout.count():
            item = self._system_info_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for key, value in self.get_system_info().items():
            self._system_info_layout.addWidget(make_label_row(f"{key}:", QLabel(value)))

    def _select_theme(self, theme: Theme) -> None:
        for value, button in self._theme_buttons.items():
            button.setChecked(value == theme.value)
        self.set_setting("general.theme", theme.value)

    def _on_autostart_changed(self, enabled: bool, checkbox: QCheckBox) -> None:
        actual = self._autostart_manager.set_enabled(enabled)
        checkbox.blockSignals(True)
        checkbox.setChecked(actual)
        checkbox.blockSignals(False)
        self.set_setting("app.start_with_os", actual)
        self._update_autostart_status()

    def _update_autostart_status(self) -> None:
        if self._autostart_status is not None:
            actual = self._autostart_manager.is_enabled()
            self._autostart_status.setText(f"{'✅ Aktiverad' if actual else '❌ Inaktiverad'} i systemet")

    @Slot()
    def _create_backup_now(self) -> None:
        answer = QMessageBox.question(self._shell, "Skapa backup", "Vill du inkludera loggfiler i backupen?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        self._set_status("💾 Skapar backup…")
        self.create_backup(include_logs=answer == QMessageBox.StandardButton.Yes)

    @Slot()
    def _restore_backup_dialog(self) -> None:
        backup_file: Path | None = None
        if self._backup_tree is not None and self._backup_tree.selectedItems():
            text = self._backup_tree.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole)
            backup_file = Path(str(text)) if text else None
        if backup_file is None:
            selected, _ = QFileDialog.getOpenFileName(self._shell, "Välj backup", str(self.backup_path or Path(".")), "ZIP-backup (*.zip);;Alla filer (*.*)")
            backup_file = Path(selected) if selected else None
        if backup_file is None or not backup_file.is_file():
            return
        answer = QMessageBox.question(self._shell, "Bekräfta återställning", f"Återställa från:\n{backup_file.name}?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.restore_backup(backup_file)

    @Slot()
    def _edit_selected_shortcut(self) -> None:
        if self._shortcut_tree is None or self._shortcut_manager is None or not self._shortcut_tree.selectedItems():
            return
        action = self._shortcut_tree.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole)
        shortcut = self._shortcut_manager.get_shortcut(str(action)) if action else None
        if shortcut is None:
            return
        dialog = ShortcutEditDialog(shortcut.label, shortcut.current_key, shortcut.default_key, self._shell)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.shortcut:
            success, error = self._shortcut_manager.set_shortcut(shortcut.action, dialog.shortcut)
            if success:
                self._refresh_shortcuts_list()
                self._set_status(f"✅ Genväg sparad: {shortcut.label}")
            else:
                QMessageBox.warning(self._shell, "Genvägskonflikt", error)

    @Slot()
    def _reset_selected_shortcut(self) -> None:
        if self._shortcut_tree is None or self._shortcut_manager is None or not self._shortcut_tree.selectedItems():
            return
        action = self._shortcut_tree.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole)
        if action:
            self._shortcut_manager.reset_shortcut(str(action))
            self._refresh_shortcuts_list()

    @Slot()
    def _reset_all_shortcuts(self) -> None:
        if self._shortcut_manager is None:
            return
        answer = QMessageBox.question(self._shell, "Återställ genvägar", "Återställa alla genvägar till standardvärden?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._shortcut_manager.reset_all()
            self._refresh_shortcuts_list()

    @Slot()
    def _create_profile_dialog(self) -> None:
        dialog = ProfileDialog(self._shell)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        if not dialog.profile_name:
            QMessageBox.warning(self._shell, "Profil", "Ange ett profilnamn.")
            return
        profile = self.create_profile(dialog.profile_name, dialog.description)
        if profile is None:
            QMessageBox.warning(self._shell, "Profil", "Profilen finns redan eller kunde inte sparas.")
            return
        self._refresh_profiles_list()
        self._set_status(f"✅ Profil skapad: {profile.name}")

    @Slot()
    def _load_selected_profile(self) -> None:
        if self._profiles_tree is None or not self._profiles_tree.selectedItems():
            return
        name = self._profiles_tree.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole)
        if name and self.load_profile(str(name)):
            self._refresh_profiles_list()
            self._set_status(f"✅ Profil laddad: {name}")

    @Slot()
    def _delete_selected_profile(self) -> None:
        if self._profiles_tree is None or not self._profiles_tree.selectedItems():
            return
        name = str(self._profiles_tree.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole) or "")
        if not name:
            return
        answer = QMessageBox.question(self._shell, "Ta bort profil", f"Ta bort profilen '{name}'?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes and self.delete_profile(name):
            self._refresh_profiles_list()

    @Slot()
    def _export_profile_dialog(self) -> None:
        if self._profiles_tree is None or not self._profiles_tree.selectedItems():
            return
        name = str(self._profiles_tree.selectedItems()[0].data(0, Qt.ItemDataRole.UserRole) or "")
        profile = self._profiles.get(name)
        if profile is None:
            return
        selected, _ = QFileDialog.getSaveFileName(self._shell, "Exportera profil", f"profil_{_safe_profile_filename(name)}.json", "JSON (*.json)")
        if selected:
            try:
                Path(selected).write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                self._set_status(f"✅ Profil exporterad: {Path(selected).name}")
            except OSError as exc:
                QMessageBox.critical(self._shell, "Exportfel", str(exc))

    @Slot()
    def _import_profile_dialog(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self._shell, "Importera profil", "", "JSON (*.json);;Alla filer (*.*)")
        if not selected:
            return
        try:
            profile = Profile.from_dict(json.loads(Path(selected).read_text(encoding="utf-8")))
            base_name = profile.name
            counter = 1
            while profile.name in self._profiles:
                profile.name = f"{base_name}_{counter}"
                counter += 1
            self._profiles[profile.name] = profile
            if not self._save_profile(profile):
                raise OSError("Kunde inte spara importerad profil")
            self._refresh_profiles_list()
            self._set_status(f"✅ Profil importerad: {profile.name}")
        except Exception as exc:
            QMessageBox.critical(self._shell, "Importfel", str(exc))

    @Slot()
    def _reset_all_settings(self) -> None:
        answer = QMessageBox.question(self._shell, "Återställ inställningar", "Återställa alla inställningar till standardvärden?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        for definition in self.SETTING_DEFINITIONS:
            self._setting_set(definition.key, definition.default, auto_save=False)
        self._save_settings()
        self._sync_autostart()
        self._sync_tray()
        self._apply_saved_theme()
        self._set_status("✅ Alla inställningar återställda")

    @Slot()
    def _clear_cache(self) -> None:
        config = self._context_path("config")
        if config is None:
            return
        cache = config / "cache"
        try:
            if cache.is_dir():
                shutil.rmtree(cache)
            self._set_status("✅ Cache rensad")
        except OSError as exc:
            QMessageBox.critical(self._shell, "Cachefel", str(exc))

        # R6/B1: rensa även teknisk Python-cache (__pycache__/.pyc/.pyo) manuellt.
        # Användardata är alltid fredad (Del I §26) — clear_pycache garanterar det.
        try:
            from core import clear_pycache

            stats = clear_pycache(getattr(self._context, "base_path", Path(".")))
            self._set_status(
                "✅ Cache rensad ({} __pycache__-mappar, {} filer)".format(
                    stats.get("dirs", 0), stats.get("files", 0)
                )
            )
            self._log.info(
                "[SETTINGS] Teknisk cache rensad: %s", stats,
                extra={"event": "st.cache_cleared", "ctx": stats},
            )
        except Exception as exc:
            self._log.warning(
                "[SETTINGS] Kunde inte rensa teknisk cache: %s", exc,
                extra={"event": "st.cache_clear_failed",
                       "ctx": {"error": str(exc)}},
            )

    @Slot()
    def _clear_logs(self) -> None:
        logs = self._context_path("logs")
        if logs is None or not logs.is_dir():
            return
        answer = QMessageBox.question(self._shell, "Rensa loggar", "Ta bort vanliga loggfiler? Crash-loggen behålls.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = 0
        for file_path in logs.glob("*.log*"):
            if file_path.name == "crash.log":
                continue
            with suppress(OSError):
                file_path.unlink()
                removed += 1
        self._set_status(f"✅ Raderade {removed} loggfil(er)")

    @Slot()
    def _handle_quit_request(self) -> None:
        if self.get_setting("app.confirm_exit", True):
            answer = QMessageBox.question(self._shell, "Avsluta IntygsDrive", "Vill du avsluta programmet helt?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._emit("app.quit_confirmed", {})

    def _copy_to_clipboard(self, text: str, status: str) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            self._set_status("⚠️ Urklipp är inte tillgängligt")
            return
        clipboard.setText(text)
        self._set_status(status)

    @Slot()
    def _copy_diagnostics_report(self) -> None:
        lines = [
            "INTYGSDRIVE DIAGNOSTIK",
            "=" * 48,
            COPYRIGHT,
            f"Settings Plugin: v{self.version}",
            "",
            "SYSTEM:",
        ]
        lines.extend(f"  {key}: {value}" for key, value in self.get_system_info().items())
        lines.append("\nHÄLSA:")
        lines.extend(f"  {check.status.icon} {check.name}: {check.message}" for check in self.run_health_checks())
        self._copy_to_clipboard("\n".join(lines), "✅ Diagnostik kopierad till urklipp")

    @Slot()
    def _copy_system_info(self) -> None:
        text = "\n".join(
            [f"INTYGSDRIVE SETTINGS v{self.version}", COPYRIGHT, ""]
            + [f"{key}: {value}" for key, value in self.get_system_info().items()]
        )
        self._copy_to_clipboard(text, "✅ Systeminformation kopierad till urklipp")

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(max(0, int(size)))
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return "0.0 B"

    # ── Export/import av inställningar ────────────────────────────────────

    @Slot()
    def _export_settings_dialog(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(self._shell, "Exportera inställningar", f"intyg_settings_{datetime.now():%Y%m%d}.json", "JSON (*.json);;Alla filer (*.*)")
        if selected and self._export_settings(Path(selected)):
            self._set_status(f"✅ Exporterade: {Path(selected).name}")

    @Slot()
    def _import_settings_dialog(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(self._shell, "Importera inställningar", "", "JSON (*.json);;Alla filer (*.*)")
        if not selected:
            return
        success, message = self._import_settings(Path(selected))
        if success:
            self._set_status(f"✅ {message}")
            self._refresh_all_lists()
        else:
            QMessageBox.critical(self._shell, "Importfel", message)

    def _export_settings(self, destination: Path) -> bool:
        temporary = destination.with_suffix(".json.tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "exported": datetime.now().isoformat(timespec="seconds"),
                "version": self.version,
                "settings": {definition.key: self.get_setting(definition.key, definition.default) for definition in self.SETTING_DEFINITIONS},
                "shortcuts": {shortcut.action: shortcut.current_key for shortcut in self._shortcut_manager.get_all() if shortcut.is_modified} if self._shortcut_manager else {},
            }
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(destination)
            return True
        except OSError:
            self._log("error", "[SETTINGS] Export misslyckades")
            return False
        finally:
            if temporary.exists():
                with suppress(OSError):
                    temporary.unlink()

    def _import_settings(self, source: Path) -> tuple[bool, str]:
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return False, "Filen innehåller inte ett JSON-objekt"
            count = 0
            for key, value in dict(data.get("settings", {}) or {}).items():
                if key in self._definitions:
                    success, _ = self.set_setting(key, value)
                    count += int(success)
            if self._shortcut_manager is not None:
                for action, key in dict(data.get("shortcuts", {}) or {}).items():
                    self._shortcut_manager.set_shortcut(str(action), str(key))
            self._save_settings()
            self._sync_autostart()
            self._sync_tray()
            self._apply_saved_theme()
            return True, f"Importerade {count} inställningar"
        except (OSError, json.JSONDecodeError) as exc:
            return False, str(exc)

    # ── Event och UI-hjälp ────────────────────────────────────────────────

    def _on_show_settings(self, data: Any = None) -> None:
        if self._shell is not None and isinstance(data, dict) and data.get("tab"):
            self._shell.show_tab(str(data["tab"]))

    def _on_refresh_ui(self, _data: Any = None) -> None:
        QTimer.singleShot(0, self._refresh_all_lists)

    def _on_plugin_change(self, _data: Any = None) -> None:
        QTimer.singleShot(0, self._refresh_plugins_list)

    def _on_theme_event(self, _data: Any = None) -> None:
        if self._shell is not None:
            self._shell.update()

    def _on_search_changed(self, text: str) -> None:
        query = text.casefold().strip()
        for key, widget in self._setting_widgets.items():
            definition = self._definitions.get(key)
            haystack = f"{key} {getattr(definition, 'label', '')} {getattr(definition, 'description', '')}".casefold()
            widget.setVisible(not query or query in haystack)
        if query:
            self._set_status(f"🔍 Söker: {text}")

    def _set_status(self, text: str) -> None:
        if self._shell is not None:
            self._shell.set_status(text)

    def _log(self, level: str, message: str, *args: Any) -> None:
        active_logger = self._log_fn or _logger
        method = getattr(active_logger, level, active_logger.info)
        try:
            method(message, *args)
        except Exception:
            _logger.exception("[SETTINGS] Loggning misslyckades")

    # ── Publikt API ───────────────────────────────────────────────────────

    def get_theme_manager(self) -> ThemeManager | None:
        return self._theme_manager

    def get_shortcut_manager(self) -> ShortcutManager | None:
        return self._shortcut_manager

    def get_autostart_manager(self) -> AutostartManager:
        return self._autostart_manager

    def is_autostart_enabled(self) -> bool:
        return self._autostart_manager.is_enabled()

    def get_current_theme(self) -> Theme:
        return self._theme_manager.current_theme if self._theme_manager is not None else Theme.from_string(str(self.get_setting("general.theme", "system")))

    def is_dark_mode(self) -> bool:
        return bool(self._theme_manager and self._theme_manager.is_dark)

    def get_color(self, key: str) -> str:
        return self._theme_manager.get_color(key) if self._theme_manager is not None else ""

    def register_setting(self, definition: SettingDefinition) -> None:
        if definition.key not in self._definitions:
            self._definitions[definition.key] = definition
            self.SETTING_DEFINITIONS.append(definition)
            if self._setting_get(definition.key, None) is None:
                self._setting_set(definition.key, definition.default)

    def unregister_setting(self, key: str) -> None:
        if key in self._definitions:
            del self._definitions[key]
            self.SETTING_DEFINITIONS[:] = [definition for definition in self.SETTING_DEFINITIONS if definition.key != key]

    def show_tab(self, tab_id: str) -> None:
        if self._shell is not None:
            self._shell.show_tab(tab_id)

    def get_statistics(self) -> dict[str, Any]:
        return {
            "profiles": len(self._profiles),
            "backups": len(self.get_backups()),
            "theme": self.get_current_theme().value,
            "autostart": self.is_autostart_enabled(),
            "requires_restart": self._requires_restart,
            "version": self.version,
            "copyright": COPYRIGHT,
        }
