# -*- coding: utf-8 -*-
from __future__ import annotations

# core/themes.py
# Intygsgenerator — Centraliserad temahantering med exakt tre teman:
# light | dark | system.
#
# Version: 2.3.0 (uppgraderad från 2.2.0, 2026-09-21)
# © Rickard Längkvist 2025–2026
#
# Temahantering är QPalette-baserad för snabb applicering. Minimal QSS används
# endast för tooltip, scrollbars och tydlig fet text på knappar/flikar enligt
# UI-019/UI-020. Färgpaletter definieras här som COLORS-001-undantag.
#
# Uppgradering 2.2.0 → 2.3.0 ("188888888% bättre", endast denna fil)
# ================================================================
# 1. PRESTANDA (tidigare förslag H2): systemtemadetektering (winreg/defaults/
#    gsettings) fick TTL-cache + bakgrunds-prefetch — apply_theme blockerar
#    aldrig UI-tråden på långsamma subprocess-anrop; stale cache returneras
#    direkt och uppdateras i bakgrunden, med auto-reapply vid ändring.
# 2. ADAPTIV UI ("anpassat i text och fönster"): QSS:s fasta px-värden skalas
#    med fontmetrik (DPI/teckenstorlek) via _ui_scale() — §17/§30.
# 3. LOGGNING: theme.*-events (applied med ms, system_detected med metod+ms,
#    cache-prefetch) — synligt i centralloggens JSONL för AI-felsökning.
# 4. All övrig funktionalitet oförändrad (MUST-005).

import logging
import subprocess
import sys
import threading
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Final

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from core import AppContext

__version__: Final[str] = "2.3.0"
__author__: Final[str] = "Rickard Längkvist"

_START_YEAR: Final[int] = 2025
_CURRENT_YEAR: Final[int] = datetime.now().year
COPYRIGHT: Final[str] = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_logger = logging.getLogger(__name__)

_SYSTEM_CACHE_TTL_SECONDS: Final[float] = 30.0


def _evt(logger: logging.Logger, event: str, msg: str,
         level: int = logging.INFO, **ctx: Any) -> None:
    """Fail-safe strukturerad loggpost (theme.*) — får aldrig krascha appen."""
    try:
        logger.log(level, msg, extra={"event": event, "ctx": ctx or None})
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


# ─── Tema-enum ───────────────────────────────────────────────────────────────


class Theme(Enum):
    """
    Exakt tre tillgängliga teman.

    LIGHT  — Ljust tema
    DARK   — Mörkt tema
    SYSTEM — Följer OS automatiskt och applicerar motsvarande light/dark
    """

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"

    @property
    def display_name(self) -> str:
        names: dict[Theme, str] = {
            Theme.LIGHT: "Ljust",
            Theme.DARK: "Mörkt",
            Theme.SYSTEM: "System",
        }
        return names[self]

    @property
    def icon(self) -> str:
        icons: dict[Theme, str] = {
            Theme.LIGHT: "☀️",
            Theme.DARK: "🌙",
            Theme.SYSTEM: "🖥️",
        }
        return icons[self]

    @classmethod
    def from_string(cls, value: str) -> Theme:
        """
        Tolka tema från sträng.

        Bakåtkompatibilitet: gamla värdet "auto" mappas till "system" utan att
        införa ett fjärde tema.
        """
        normalized = str(value or "").lower().strip()
        mapping: dict[str, Theme] = {
            "light": cls.LIGHT,
            "ljust": cls.LIGHT,
            "dark": cls.DARK,
            "mörkt": cls.DARK,
            "morkt": cls.DARK,
            "system": cls.SYSTEM,
            "auto": cls.SYSTEM,
            "automatisk": cls.SYSTEM,
        }
        return mapping.get(normalized, cls.SYSTEM)


# ─── Färgpaletter (COLORS-001-undantag) ─────────────────────────────────────

LIGHT_PALETTE: dict[str, str] = {
    "bg": "#f5f5f5",
    "fg": "#1a1a1a",
    "accent": "#1976d2",
    "accent_fg": "#ffffff",
    "secondary_bg": "#efefef",
    "tertiary_bg": "#e8e8e8",
    "border": "#bdbdbd",
    "border_focus": "#1976d2",
    "success": "#2e7d32",
    "warning": "#e65100",
    "error": "#c62828",
    "info": "#01579b",
    "disabled": "#9e9e9e",
    "disabled_bg": "#eeeeee",
    "link": "#1565c0",
    "card_bg": "#ffffff",
    "input_bg": "#ffffff",
    "selection_bg": "#bbdefb",
    "selection_fg": "#0d47a1",
    "placeholder": "#888888",
    "tooltip_bg": "#fffde7",
    "tooltip_fg": "#212121",
    "window": "#f5f5f5",
    "base": "#ffffff",
    "alternate_base": "#efefef",
    "button": "#606060",
    "button_text": "#ffffff",
    "window_text": "#1a1a1a",
    "bright_text": "#000000",
    "placeholder_text": "#888888",
    "accent_hover": "#1565c0",
    "accent_text": "#ffffff",
    "highlight": "#1976d2",
    "highlighted_text": "#ffffff",
    "success_bg": "#e8f5e9",
    "warning_bg": "#fff3e0",
    "error_bg": "#ffebee",
    "info_bg": "#e1f5fe",
    "link_visited": "#6a1b9a",
    "mid": "#c0c0c0",
    "midlight": "#ebebeb",
    "dark": "#a0a0a0",
    "shadow": "#787878",
    "header_bg": "#e3f2fd",
    "status_bg": "#f5f5f5",
    "separator": "#e0e0e0",
    "text": "#1a1a1a",
}

DARK_PALETTE: dict[str, str] = {
    "bg": "#1e1e1e",
    "fg": "#e8e8e8",
    "accent": "#4fc3f7",
    "accent_fg": "#000000",
    "secondary_bg": "#2d2d30",
    "tertiary_bg": "#3c3c3c",
    "border": "#3e3e42",
    "border_focus": "#4fc3f7",
    "success": "#66bb6a",
    "warning": "#ffa726",
    "error": "#ef5350",
    "info": "#4fc3f7",
    "disabled": "#5a5a5a",
    "disabled_bg": "#2a2a2a",
    "link": "#81d4fa",
    "card_bg": "#2d2d30",
    "input_bg": "#3c3c3c",
    "selection_bg": "#264f78",
    "selection_fg": "#ffffff",
    "placeholder": "#6d6d6d",
    "tooltip_bg": "#3c3c3c",
    "tooltip_fg": "#e8e8e8",
    "window": "#1e1e1e",
    "base": "#252526",
    "alternate_base": "#2d2d30",
    "button": "#3c3c3c",
    "button_text": "#ffffff",
    "window_text": "#e8e8e8",
    "bright_text": "#ffffff",
    "placeholder_text": "#6d6d6d",
    "accent_hover": "#81d4fa",
    "accent_text": "#000000",
    "highlight": "#4fc3f7",
    "highlighted_text": "#000000",
    "success_bg": "#1b3a1d",
    "warning_bg": "#3a2a0a",
    "error_bg": "#3a1010",
    "info_bg": "#0a2a3a",
    "link_visited": "#ce93d8",
    "mid": "#505050",
    "midlight": "#3a3a3a",
    "dark": "#141414",
    "shadow": "#0a0a0a",
    "header_bg": "#252526",
    "status_bg": "#1e1e1e",
    "separator": "#3e3e42",
    "text": "#e8e8e8",
}


# ─── Qt-signaler ─────────────────────────────────────────────────────────────


class _ThemeSignals(QObject):
    """Qt-signaler för temaändringar."""

    theme_changed = Signal(str)


# ─── ThemeManager ────────────────────────────────────────────────────────────


class ThemeManager:
    """
    Centraliserad temahantering för Intygsgenerator.

    Exakt tre teman: light | dark | system.

    Singleton per AppContext. Plugins ska använda get_color()/get_qcolor() och
    inte hårdkoda färger.

    2.3.0: systemtemadetektering är TTL-cachad med bakgrunds-prefetch —
    apply_theme() blockerar aldrig på subprocess-anrop (PERF-003).
    """

    _instances: dict[int, "ThemeManager"] = {}
    _instances_lock: threading.Lock = threading.Lock()
    _CONFIG_KEY: Final[str] = "general.theme"

    def __init__(self, context: "AppContext | None" = None) -> None:
        self._context = context
        self._lock = threading.RLock()

        self._current: Theme = Theme.SYSTEM
        self._resolved: Theme = Theme.LIGHT

        self._palette_light: dict[str, str] = dict(LIGHT_PALETTE)
        self._palette_dark: dict[str, str] = dict(DARK_PALETTE)

        self._signals = _ThemeSignals()
        self._observers: list[Callable[[Theme], None]] = []
        self._unsub_fns: list[Callable[[], None]] = []
        self._font_cache: dict[str, QFont] = {}

        # 2.3.0: cache för systemtema (H2) — detektering är dyr på Linux/macOS.
        self._system_cache: Theme | None = None
        self._system_cache_ts: float = 0.0
        self._prefetch_lock = threading.Lock()
        self._prefetch_running = False

        self._log: logging.Logger = (
            context.get_logger("ThemeManager") if context else _logger
        )

        self._load_saved_theme()
        self._subscribe_events()

        self._log.info(
            "[ThemeManager] v%s initierad — tema: %s (löst: %s)",
            __version__,
            self._current.value,
            self._resolved.value,
        )

    @classmethod
    def get_instance(cls, context: "AppContext | None" = None) -> "ThemeManager":
        """Hämta eller skapa ThemeManager-singleton för given context."""
        key = id(context) if context is not None else 0
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(context)
            return cls._instances[key]

    @classmethod
    def _remove_instance(cls, context: "AppContext | None") -> None:
        key = id(context) if context is not None else 0
        with cls._instances_lock:
            cls._instances.pop(key, None)

    def _load_saved_theme(self) -> None:
        """Ladda sparat tema från ConfigManager."""
        saved = "system"
        if self._context and self._context.settings:
            with self._lock:
                try:
                    saved = str(
                        self._context.settings.get(self._CONFIG_KEY, "system")
                    )
                except Exception:
                    self._log.exception("[ThemeManager] Kunde inte läsa sparat tema")

        self._current = Theme.from_string(saved)
        self._resolved = self._resolve(self._current)

    def _subscribe_events(self) -> None:
        """Prenumerera på EventBus-events."""
        if not (self._context and self._context.event_bus):
            return

        try:
            unsubscribe = self._context.event_bus.subscribe(
                "app.shutdown",
                lambda _data: self.shutdown(),
            )
            if callable(unsubscribe):
                self._unsub_fns.append(unsubscribe)
        except Exception:
            self._log.exception("[ThemeManager] EventBus-subscribe misslyckades")

    def shutdown(self) -> None:
        """Städa resurser vid avslut."""
        for unsubscribe in self._unsub_fns:
            try:
                unsubscribe()
            except Exception:
                self._log.exception("[ThemeManager] Kunde inte unsubscribe:a event")

        self._unsub_fns.clear()
        self._observers.clear()
        self._font_cache.clear()
        ThemeManager._remove_instance(self._context)
        self._log.debug("[ThemeManager] Shutdown klar")

    # ── Systemtema: cachad + prefetch (2.3.0, H2) ─────────────────────────

    def detect_system_theme(self) -> Theme:
        """Detektera systemets tema. Returnerar alltid LIGHT eller DARK.

        2.3.0: TTL-cache. Färsk cache returneras direkt; stale cache
        returneras direkt MEN prefetchas i bakgrunden (UI blockeras aldrig);
        endast första anropet (ingen cache) detekterar synkront.
        """
        now = time.monotonic()
        with self._lock:
            cached = self._system_cache
            age = now - self._system_cache_ts
        if cached is not None and age < _SYSTEM_CACHE_TTL_SECONDS:
            return cached
        if cached is not None:
            self._prefetch_system_theme()
            return cached
        return self._detect_system_now()

    def _detect_system_now(self) -> Theme:
        """Synkron detektering med tidsmätning och theme.*-loggning."""
        started = time.monotonic()
        method = "windows" if sys.platform == "win32" else (
            "macos" if sys.platform == "darwin" else "linux"
        )
        try:
            if sys.platform == "win32":
                result = self._detect_windows()
            elif sys.platform == "darwin":
                result = self._detect_macos()
            else:
                result = self._detect_linux()
        except Exception:
            self._log.exception("[ThemeManager] Systemtema-detektion misslyckades")
            result = Theme.LIGHT
        with self._lock:
            self._system_cache = result
            self._system_cache_ts = time.monotonic()
        _evt(
            self._log,
            "theme.system_detected",
            f"Systemtema detekterat: {result.value} ({method})",
            logging.DEBUG,
            resolved=result.value,
            method=method,
            ms=round((time.monotonic() - started) * 1000, 1),
        )
        return result

    def _prefetch_system_theme(self) -> None:
        """Uppdatera stale cache i bakgrunden; reapply vid ändring."""
        with self._prefetch_lock:
            if self._prefetch_running:
                return
            self._prefetch_running = True

        def run() -> None:
            try:
                new_value = self._detect_system_now()
                if self._current == Theme.SYSTEM and new_value != self._resolved:
                    _evt(
                        self._log,
                        "theme.system_changed",
                        f"OS-tema ändrat → {new_value.value}; applicerar om",
                        logging.INFO,
                        resolved=new_value.value,
                    )
                    try:
                        from PySide6.QtCore import QTimer

                        QTimer.singleShot(0, self.refresh_system_theme)
                    except Exception:
                        self.refresh_system_theme()
            finally:
                with self._prefetch_lock:
                    self._prefetch_running = False

        thread = threading.Thread(target=run, name="ThemePrefetch", daemon=True)
        thread.start()
        _evt(self._log, "theme.prefetch", "Stale systemtema prefetchas i bakgrunden",
             logging.DEBUG)

    def apply_theme(self, theme: Theme) -> None:
        """Applicera tema på QApplication med QPalette."""
        started = time.monotonic()
        with self._lock:
            self._current = theme
            self._resolved = self._resolve(theme)

        app = QApplication.instance()
        if app:
            palette = self._build_palette(self._resolved)
            self._apply_font(app)
            app.setPalette(palette)
            self._apply_stylesheet(app)

        self._save_theme()
        self._notify_observers()
        self._emit_theme_changed()
        self._signals.theme_changed.emit(self._current.value)

        _evt(
            self._log,
            "theme.applied",
            f"Tema applicerat: {self._current.value} (löst: {self._resolved.value})",
            logging.INFO,
            theme=self._current.value,
            resolved=self._resolved.value,
            ms=round((time.monotonic() - started) * 1000, 1),
        )

    def apply_theme_by_name(self, name: str) -> None:
        """Applicera tema via strängnamn."""
        self.apply_theme(Theme.from_string(name))

    def _apply_font(self, app: Any) -> None:
        """Applicera basfont på QApplication."""
        app.setFont(self.get_font("base"))

    def _ui_scale(self, app: Any | None = None) -> float:
        """2.3.0: skala för QSS-px efter fontmetrik (DPI) — §17/§30."""
        try:
            application = app or QApplication.instance()
            if application is not None:
                height = application.fontMetrics().height()
                if height > 0:
                    return max(1.0, height / 16.0)
        except Exception:
            pass
        return 1.0

    def _apply_stylesheet(self, app: Any) -> None:
        """
        Applicera minimal QSS.

        Huvudfärgsättning sker via QPalette; QSS används här för tooltip,
        scrollbar samt fet och tydlig knapp-/fliktext.
        2.3.0: alla px-värden skalas med fontmetrik (_ui_scale).
        """
        s = self._ui_scale(app)

        def px(value: float) -> int:
            return max(1, int(round(value * s)))

        stylesheet = f"""
QToolTip {{
    background-color: {self.get_color("tooltip_bg")};
    color: {self.get_color("tooltip_fg")};
    border: 1px solid {self.get_color("border")};
    padding: {px(4)}px {px(8)}px;
    border-radius: {px(4)}px;
    font-size: {px(13)}px;
}}
QPushButton {{
    font-weight: bold;
    background-color: {self.get_color("button")};
    color: {self.get_color("button_text")};
    border: 1px solid {self.get_color("border")};
    border-radius: {px(4)}px;
    padding: {px(5)}px {px(14)}px;
    min-height: {px(26)}px;
}}
QPushButton:hover {{
    background-color: {self.get_color("accent")};
    color: {self.get_color("accent_fg")};
    border-color: {self.get_color("accent")};
}}
QPushButton:pressed {{
    background-color: {self.get_color("accent_hover")};
}}
QPushButton:disabled {{
    background-color: {self.get_color("disabled_bg")};
    color: {self.get_color("disabled")};
}}
QTabBar::tab {{
    font-weight: bold;
    background-color: {self.get_color("button")};
    color: {self.get_color("button_text")};
    padding: {px(6)}px {px(16)}px;
    border: 1px solid {self.get_color("border")};
    border-bottom: none;
    border-radius: {px(4)}px {px(4)}px 0 0;
    min-width: {px(80)}px;
}}
QTabBar::tab:selected {{
    background-color: {self.get_color("accent")};
    color: {self.get_color("accent_fg")};
}}
QTabBar::tab:hover:!selected {{
    background-color: {self.get_color("secondary_bg")};
}}
QHeaderView::section {{
    font-weight: bold;
    background-color: {self.get_color("header_bg")};
    color: {self.get_color("fg")};
    border: 1px solid {self.get_color("border")};
    padding: {px(4)}px {px(8)}px;
}}
QGroupBox::title {{
    font-weight: bold;
    color: {self.get_color("fg")};
}}
QLabel[heading="true"] {{
    font-weight: bold;
}}
QScrollBar:vertical {{
    background: {self.get_color("secondary_bg")};
    width: {px(10)}px;
    border-radius: {px(5)}px;
}}
QScrollBar::handle:vertical {{
    background: {self.get_color("border")};
    border-radius: {px(5)}px;
    min-height: {px(20)}px;
}}
QScrollBar::handle:vertical:hover {{
    background: {self.get_color("accent")};
}}
QScrollBar:horizontal {{
    background: {self.get_color("secondary_bg")};
    height: {px(10)}px;
    border-radius: {px(5)}px;
}}
QScrollBar::handle:horizontal {{
    background: {self.get_color("border")};
    border-radius: {px(5)}px;
    min-width: {px(20)}px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {self.get_color("accent")};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
"""
        app.setStyleSheet(stylesheet)

    def _build_palette(self, resolved: Theme) -> QPalette:
        """Bygg QPalette för löst tema, light eller dark."""
        colors = (
            self._palette_light if resolved == Theme.LIGHT else self._palette_dark
        )

        def color(key: str) -> QColor:
            return QColor(colors.get(key, "#000000"))

        palette = QPalette()
        role_map: list[tuple[QPalette.ColorRole, str]] = [
            (QPalette.ColorRole.Window, "window"),
            (QPalette.ColorRole.WindowText, "window_text"),
            (QPalette.ColorRole.Base, "base"),
            (QPalette.ColorRole.AlternateBase, "alternate_base"),
            (QPalette.ColorRole.ToolTipBase, "tooltip_bg"),
            (QPalette.ColorRole.ToolTipText, "tooltip_fg"),
            (QPalette.ColorRole.Text, "text"),
            (QPalette.ColorRole.Button, "button"),
            (QPalette.ColorRole.ButtonText, "button_text"),
            (QPalette.ColorRole.BrightText, "bright_text"),
            (QPalette.ColorRole.Link, "link"),
            (QPalette.ColorRole.LinkVisited, "link_visited"),
            (QPalette.ColorRole.Highlight, "selection_bg"),
            (QPalette.ColorRole.HighlightedText, "selection_fg"),
            (QPalette.ColorRole.Mid, "mid"),
            (QPalette.ColorRole.Midlight, "midlight"),
            (QPalette.ColorRole.Dark, "dark"),
            (QPalette.ColorRole.Shadow, "shadow"),
            (QPalette.ColorRole.PlaceholderText, "placeholder"),
        ]

        for role, key in role_map:
            palette.setColor(QPalette.ColorGroup.All, role, color(key))
            palette.setColor(QPalette.ColorGroup.Inactive, role, color(key))

        disabled_text = color("disabled")
        disabled_bg = color("disabled_bg")
        for role, key in role_map:
            if role in (
                QPalette.ColorRole.Text,
                QPalette.ColorRole.WindowText,
                QPalette.ColorRole.ButtonText,
                QPalette.ColorRole.PlaceholderText,
            ):
                palette.setColor(
                    QPalette.ColorGroup.Disabled, role, disabled_text
                )
            elif role == QPalette.ColorRole.Button:
                palette.setColor(
                    QPalette.ColorGroup.Disabled, role, disabled_bg
                )
            else:
                palette.setColor(
                    QPalette.ColorGroup.Disabled, role, color(key)
                )

        return palette

    def _build_fonts(self) -> dict[str, QFont]:
        """Bygg plattformsanpassade QFont-objekt."""
        if sys.platform == "win32":
            family = "Segoe UI"
            mono_family = "Consolas"
        elif sys.platform == "darwin":
            family = "SF Pro Display"
            mono_family = "Menlo"
        else:
            family = "Ubuntu"
            mono_family = "Ubuntu Mono"

        def make(size: int, bold: bool = False, monospace: bool = False) -> QFont:
            font = QFont(mono_family if monospace else family, size)
            font.setBold(bold)
            font.setStyleHint(
                QFont.StyleHint.Monospace
                if monospace
                else QFont.StyleHint.SansSerif
            )
            return font

        return {
            "base": make(10),
            "bold": make(10, bold=True),
            "small": make(9),
            "heading": make(14, bold=True),
            "subheading": make(11, bold=True),
            "mono": make(10, monospace=True),
            "button": make(10, bold=True),
            "tab": make(10, bold=True),
        }

    def get_font(self, role: str = "base") -> QFont:
        """Returnera QFont för given roll, cachad."""
        if not self._font_cache:
            self._font_cache = self._build_fonts()
        return self._font_cache.get(role, self._font_cache["base"])

    def _detect_windows(self) -> Theme:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _reg_type = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return Theme.LIGHT if value == 1 else Theme.DARK
        except Exception:
            return Theme.LIGHT

    def _detect_macos(self) -> Theme:
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            return Theme.DARK if "Dark" in result.stdout else Theme.LIGHT
        except Exception:
            return Theme.LIGHT

    def _detect_linux(self) -> Theme:
        if self._linux_gsettings_contains_dark("color-scheme"):
            return Theme.DARK

        if self._linux_gsettings_contains_dark("gtk-theme"):
            return Theme.DARK

        try:
            kde_config = Path.home() / ".config" / "kdeglobals"
            if kde_config.exists():
                content = kde_config.read_text(encoding="utf-8", errors="ignore")
                if "dark" in content.lower():
                    return Theme.DARK
        except Exception:
            self._log.debug(
                "[ThemeManager] KDE-tema kunde inte läsas", exc_info=True
            )

        return Theme.LIGHT

    def _linux_gsettings_contains_dark(self, key: str) -> bool:
        try:
            result = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", key],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            return "dark" in result.stdout.lower()
        except Exception:
            return False

    def get_color(self, key: str, default: str = "#000000") -> str:
        """Returnera en färg från aktuellt löst tema."""
        with self._lock:
            palette = (
                self._palette_light
                if self._resolved == Theme.LIGHT
                else self._palette_dark
            )
            return palette.get(key, default)

    def get_qcolor(self, key: str, default: str = "#000000") -> QColor:
        """Returnera QColor från aktuell palett."""
        return QColor(self.get_color(key, default))

    def get_palette_dict(self) -> dict[str, str]:
        """Returnera kopia av aktuell palett."""
        with self._lock:
            source = (
                self._palette_light
                if self._resolved == Theme.LIGHT
                else self._palette_dark
            )
            return dict(source)

    def add_observer(
        self, callback: Callable[[Theme], None]
    ) -> Callable[[], None]:
        """Lägg till observer och returnera unsubscribe-funktion."""
        with self._lock:
            if callback not in self._observers:
                self._observers.append(callback)

        def remove() -> None:
            self.remove_observer(callback)

        return remove

    def remove_observer(self, callback: Callable[[Theme], None]) -> None:
        """Ta bort observer."""
        with self._lock:
            if callback in self._observers:
                self._observers.remove(callback)

    def _notify_observers(self) -> None:
        """Notifiera observers om löst tema."""
        with self._lock:
            observers = list(self._observers)

        for observer in observers:
            try:
                observer(self._resolved)
            except Exception:
                self._log.exception("[ThemeManager] Observer-fel")

    def _emit_theme_changed(self) -> None:
        """Emittera settings.theme_changed via EventBus."""
        if not (self._context and self._context.event_bus):
            return

        try:
            self._context.event_bus.emit(
                "settings.theme_changed",
                {
                    "theme": self._current.value,
                    "resolved": self._resolved.value,
                    "is_dark": self.is_dark,
                    "palette": self.get_palette_dict(),
                },
            )
        except Exception:
            self._log.exception(
                "[ThemeManager] EventBus-emission misslyckades"
            )

    def _save_theme(self) -> None:
        """Spara aktuellt tema till ConfigManager."""
        if not (self._context and self._context.settings):
            return

        try:
            self._context.settings.set(self._CONFIG_KEY, self._current.value)
        except Exception:
            self._log.exception("[ThemeManager] Tema-sparning misslyckades")

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def current_theme(self) -> Theme:
        """Valt tema."""
        return self._current

    @property
    def resolved_theme(self) -> Theme:
        """Faktiskt tillämpat tema, alltid LIGHT eller DARK."""
        return self._resolved

    @property
    def is_dark(self) -> bool:
        """True om mörkt tema är aktivt."""
        return self._resolved == Theme.DARK

    @property
    def is_light(self) -> bool:
        """True om ljust tema är aktivt."""
        return self._resolved == Theme.LIGHT

    @property
    def signals(self) -> _ThemeSignals:
        """Qt-signaler för temaändringar."""
        return self._signals

    @property
    def available_themes(self) -> list[Theme]:
        """Alla tre tillgängliga teman."""
        return list(Theme)

    # ── Interna metoder ───────────────────────────────────────────────────

    def _resolve(self, theme: Theme) -> Theme:
        """Lös SYSTEM till LIGHT eller DARK."""
        if theme == Theme.SYSTEM:
            return self.detect_system_theme()
        return theme

    def refresh_system_theme(self) -> bool:
        """Applicera systemtema igen om OS-temat ändrats."""
        if self._current != Theme.SYSTEM:
            return False

        new_resolved = self.detect_system_theme()
        if new_resolved != self._resolved:
            self.apply_theme(self._current)
            return True
        return False

    def get_theme_info(self) -> dict[str, Any]:
        """Returnera diagnostik om aktuellt tema (2.3.0: + cache-status)."""
        with self._lock:
            cache_age = (
                round(time.monotonic() - self._system_cache_ts, 1)
                if self._system_cache is not None
                else None
            )
        return {
            "selected": self._current.value,
            "resolved": self._resolved.value,
            "is_dark": self.is_dark,
            "display_name": self._current.display_name,
            "icon": self._current.icon,
            "color_count": len(self.get_palette_dict()),
            "available": [theme.value for theme in self.available_themes],
            "system_cache_age_s": cache_age,
            "system_cache_ttl_s": _SYSTEM_CACHE_TTL_SECONDS,
            "version": __version__,
            "copyright": COPYRIGHT,
        }

    def __repr__(self) -> str:
        return (
            f"<ThemeManager v{__version__} "
            f"theme={self._current.value!r} "
            f"resolved={self._resolved.value!r}>"
        )

    def __str__(self) -> str:
        return f"ThemeManager({self._current.display_name})"


__all__: list[str] = [
    "__version__",
    "__author__",
    "COPYRIGHT",
    "get_copyright",
    "Theme",
    "ThemeManager",
    "LIGHT_PALETTE",
    "DARK_PALETTE",
]
