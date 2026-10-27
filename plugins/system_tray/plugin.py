from __future__ import annotations

# plugins/system_tray/plugin.py
# Sökväg: Intygsgenerator/plugins/system_tray/plugin.py
# Intygsgenerator — System tray-plugin: QSystemTrayIcon, kontextmeny,
# fönstersynlighet, notifikationer och senaste intyg.
#
# Version: 9.0.0
# © Rickard Längkvist 2025–2026
#
# Nytt i v9.0.0:
#   • Notification debouncing (500ms, max 3 queued)
#   • Lazy animation icons (genereras först vid behov)
#   • _logger PEP8-konvention
#   • Ta bort encoding-kommentar
#   • Moderniserad docstring
#
# Ansvar:
#   • Äger den ENDA QSystemTrayIcon-instansen (TRAY-001)
#   • Enkelklick togglar huvudfönster (TRAY-004)
#   • X = minimize to tray (TRAY-005)
#   • Avsluta ENBART via meny (TRAY-006)
#   • QPainter-fallback ikon (TRAY-008/009)
#
# Beroenden: PySide6, core/__init__.py, .models

import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QObject,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .models import (
    ANIMATION_SPEED_MS,
    COPYRIGHT,
    ICON_SIZE,
    MAX_RECENT_CERTIFICATES,
    NOTIFICATION_DURATION_MS,
    PLUGIN_DESCRIPTION,
    PLUGIN_NAME,
    PLUGIN_VERSION,
    CloseAction,
    IconGenerator,
    IconState,
    NotificationType,
    Platform,
    RecentCertificate,
    TrayConfigHelper,
    get_tooltip_busy,
    get_tooltip_minimized,
    get_tooltip_normal,
)

__version__: str = PLUGIN_VERSION
__all__: list[str] = ["Plugin"]

_logger: logging.Logger = logging.getLogger(__name__)

_GLOBAL_TRAY_LOCK = threading.RLock()
_GLOBAL_TRAY_ICON: QSystemTrayIcon | None = None

# Notification debouncing
_NOTIFY_DEBOUNCE_MS: int = 500
_NOTIFY_MAX_QUEUE: int = 3

# ─── Qt-signaler ─────────────────────────────────────────────────────────────


class _TraySignals(QObject):
    """Thread-safe signaler för UI-uppdateringar."""

    show_window = Signal()
    hide_window = Signal()
    toggle_window = Signal()
    quit_app = Signal()
    notify = Signal(str, str, str, int)
    set_icon = Signal(str)


class _AnimationTimer(QObject):
    """Busy-animation av tray-ikonen i UI-tråden."""

    tick = Signal(int)

    def __init__(
        self,
        frame_count: int = 8,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._frame_count = max(1, int(frame_count))
        self._current = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)

    def start(self) -> None:
        self._current = 0
        self._timer.start(ANIMATION_SPEED_MS)

    def stop(self) -> None:
        self._timer.stop()
        self._current = 0

    @property
    def is_active(self) -> bool:
        return self._timer.isActive()

    @Slot()
    def _on_tick(self) -> None:
        self.tick.emit(self._current)
        self._current = (self._current + 1) % self._frame_count

# ─── Ikon-hjälpfunktioner ────────────────────────────────────────────────────


def _pil_to_qicon(pil_image: Any) -> QIcon:
    """Konvertera PIL Image till QIcon."""
    try:
        if pil_image is None:
            return QIcon()
        if pil_image.mode != "RGBA":
            pil_image = pil_image.convert("RGBA")
        data = pil_image.tobytes("raw", "RGBA")
        qimage = QImage(
            data,
            pil_image.width,
            pil_image.height,
            QImage.Format.Format_RGBA8888,
        ).copy()
        return QIcon(QPixmap.fromImage(qimage))
    except Exception:
        _logger.exception("Kunde inte konvertera PIL till QIcon")
        return QIcon()


def _generate_fallback_qicon(
    state: IconState = IconState.NORMAL,
    size: int = ICON_SIZE,
) -> QIcon:
    """QPainter-fallback — kräver inte Pillow (COLORS-001 undantag)."""
    try:
        qicon = IconGenerator.create_qicon(state, size)
        if isinstance(qicon, QIcon) and not qicon.isNull():
            return qicon
    except Exception:
        _logger.exception("IconGenerator.create_qicon misslyckades")

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    r, g, b = state.color
    dr, dg, db = state.color_dark
    margin = max(size // 16, 2)
    border_width = max(size // 24, 1)

    painter.setBrush(QColor(r, g, b, 255))
    painter.setPen(QPen(QColor(dr, dg, db, 255), border_width))
    painter.drawEllipse(
        margin, margin,
        size - 2 * margin, size - 2 * margin,
    )

    # Vit "I"-bokstav
    painter.setBrush(QColor(255, 255, 255, 255))
    painter.setPen(QColor(255, 255, 255, 255))
    center_x = size // 2
    stem_w = max(int(size * 0.12), 4)
    serif_h = max(int(size * 0.08), 3)
    top = int(size * 0.23)
    bottom = int(size * 0.77)
    painter.drawRect(
        center_x - stem_w // 2,
        top + serif_h,
        stem_w,
        bottom - top - 2 * serif_h,
    )
    painter.drawRect(
        int(size * 0.31), top,
        int(size * 0.38), serif_h,
    )
    painter.drawRect(
        int(size * 0.31), bottom - serif_h,
        int(size * 0.38), serif_h,
    )
    painter.end()
    return QIcon(pixmap)


def _load_icon(
    icon_folder: Path,
    state: IconState = IconState.NORMAL,
) -> QIcon:
    """Ladda ikon: fil → PIL → QPainter-fallback."""
    for filename in (
        "icon.png", "icon.ico", "app.png",
        "app.ico", "tray.png", "icon.icns",
    ):
        icon_path = icon_folder / filename
        if icon_path.exists() and icon_path.is_file():
            icon = QIcon(str(icon_path))
            if not icon.isNull():
                return icon

    pil_image = IconGenerator.create_icon(
        state,
        IconGenerator.get_platform_icon_size(),
    )
    if pil_image is not None:
        icon = _pil_to_qicon(pil_image)
        if not icon.isNull():
            return icon

    return _generate_fallback_qicon(state)

# ─── Inställningsflik ────────────────────────────────────────────────────────


class _SettingsTab(QScrollArea):
    """Inställningsflik för system tray."""

    def __init__(self, plugin: Plugin) -> None:
        super().__init__()
        self._plugin = plugin
        self._recent_list: QListWidget | None = None
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        self.setWidget(inner)
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(10)
        self._build()

    def _build(self) -> None:
        self._build_behavior_group()
        self._build_notification_group()
        self._build_recent_group()
        self._build_info_group()
        self._layout.addStretch()

    def _build_behavior_group(self) -> None:
        group = QGroupBox("Fönsterbeteende")
        form = QFormLayout(group)
        form.setSpacing(8)
        config = self._plugin._tray_config

        cb_start = QCheckBox()
        cb_start.setChecked(
            config.start_minimized if config else True
        )
        cb_start.toggled.connect(
            lambda v: self._set(
                "start_minimized", v, "app.start_minimized",
            )
        )
        form.addRow("Starta minimerat:", cb_start)

        cb_close = QCheckBox()
        cb_close.setChecked(
            config.close_to_tray if config else True
        )
        cb_close.toggled.connect(
            lambda v: self._set(
                "close_to_tray", v, "app.close_to_tray",
            )
        )
        form.addRow("X stänger till tray:", cb_close)

        cb_minimize = QCheckBox()
        cb_minimize.setChecked(
            config.minimize_to_tray if config else True
        )
        cb_minimize.toggled.connect(
            lambda v: self._set(
                "minimize_to_tray", v,
                "app.minimize_to_tray",
            )
        )
        form.addRow("Minimera till tray:", cb_minimize)

        cb_click = QCheckBox()
        cb_click.setChecked(
            config.single_click_open if config else True
        )
        cb_click.toggled.connect(
            lambda v: self._set("single_click_open", v)
        )
        form.addRow("Enkelklick öppnar:", cb_click)

        self._layout.addWidget(group)

    def _build_notification_group(self) -> None:
        group = QGroupBox("Notifikationer")
        form = QFormLayout(group)
        form.setSpacing(8)
        config = self._plugin._tray_config

        cb_notif = QCheckBox()
        cb_notif.setChecked(
            config.show_notifications if config else True
        )
        cb_notif.toggled.connect(
            lambda v: self._set("show_notifications", v)
        )
        form.addRow("Visa notifikationer:", cb_notif)

        cb_min_notif = QCheckBox()
        cb_min_notif.setChecked(
            config.show_minimize_notification if config else True
        )
        cb_min_notif.toggled.connect(
            lambda v: self._set("show_minimize_notification", v)
        )
        form.addRow("Notis vid minimering:", cb_min_notif)

        self._layout.addWidget(group)

    def _build_recent_group(self) -> None:
        group = QGroupBox(
            f"Senaste intyg (max {MAX_RECENT_CERTIFICATES})"
        )
        layout = QVBoxLayout(group)
        self._recent_list = QListWidget()
        self._recent_list.setMaximumHeight(220)
        self._recent_list.itemDoubleClicked.connect(
            self._open_recent
        )
        layout.addWidget(self._recent_list)
        button_row = QHBoxLayout()
        clear_btn = QPushButton("Rensa lista")
        clear_btn.clicked.connect(self._clear_recent)
        button_row.addWidget(clear_btn)
        button_row.addStretch()
        layout.addLayout(button_row)
        self._layout.addWidget(group)
        self._refresh_recent()

    def _build_info_group(self) -> None:
        group = QGroupBox("Information")
        layout = QVBoxLayout(group)
        platform = Platform.current()
        rows = [
            ("Plugin:", f"{PLUGIN_NAME} v{PLUGIN_VERSION}"),
            (
                "Plattform:",
                f"{platform.emoji} {platform.display_name}",
            ),
            (
                "Tray tillgänglig:",
                "Ja"
                if QSystemTrayIcon.isSystemTrayAvailable()
                else "Nej",
            ),
            ("Copyright:", COPYRIGHT),
        ]
        for label_text, value_text in rows:
            row = QHBoxLayout()
            label = QLabel(f"<b>{label_text}</b>")
            label.setFixedWidth(120)
            row.addWidget(label)
            value = QLabel(value_text)
            value.setWordWrap(True)
            row.addWidget(value)
            row.addStretch()
            layout.addLayout(row)
        self._layout.addWidget(group)

    def _set(
        self,
        tray_key: str,
        value: Any,
        config_key: str | None = None,
    ) -> None:
        config = self._plugin._tray_config
        if config is not None:
            with suppress(Exception):
                setattr(config, tray_key, value)
        if config_key and self._plugin._context:
            cm = getattr(
                self._plugin._context, "config_manager", None,
            )
            if cm is not None:
                with suppress(Exception):
                    cm.set(config_key, value)
        self._plugin._emit_event(
            "settings.changed",
            {
                "key": config_key or f"tray.{tray_key}",
                "value": value,
                "old_value": None,
            },
        )

    def _refresh_recent(self) -> None:
        if self._recent_list is None:
            return
        self._recent_list.clear()
        config = self._plugin._tray_config
        if config is None:
            return
        for cert in config.get_recent_certificates():
            text = f"{cert.short_name} — {cert.age_text}"
            item = QListWidgetItem(text)
            item.setToolTip(str(cert.path))
            if cert.exists:
                item.setData(
                    Qt.ItemDataRole.UserRole, str(cert.path),
                )
            else:
                item.setToolTip("Filen finns inte längre")
                item.setFlags(
                    item.flags() & ~Qt.ItemFlag.ItemIsEnabled
                )
            self._recent_list.addItem(item)

    def _open_recent(self, item: QListWidgetItem) -> None:
        path_text = item.data(Qt.ItemDataRole.UserRole)
        if not path_text:
            return
        self._plugin._open_path(Path(str(path_text)))

    def _clear_recent(self) -> None:
        config = self._plugin._tray_config
        if config is not None:
            config.recent_certificates = []
        if self._recent_list is not None:
            self._recent_list.clear()
        self._plugin._refresh_recent_menu()

    def refresh(self) -> None:
        self._refresh_recent()

# ─── Plugin ──────────────────────────────────────────────────────────────────


class Plugin:
    """System tray-plugin (TRAY-001→009).

    Äger exakt en QSystemTrayIcon. Enkelklick togglar
    fönster. Avsluta sker via tray-menyn.
    """

    name: str = PLUGIN_NAME
    version: str = PLUGIN_VERSION
    description: str = PLUGIN_DESCRIPTION
    priority: int = 5

    def __init__(self) -> None:
        self._context: Any = None
        self._log = logging.getLogger("SystemTray")
        self._tray: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tab_widget: _SettingsTab | None = None
        self._tray_config: TrayConfigHelper | None = None
        self._signals: _TraySignals | None = None
        self._animation: _AnimationTimer | None = None
        self._icon_cache: dict[IconState, QIcon] = {}
        self._animation_icons: list[QIcon] = []
        self._icon_folder: Path = Path("Icon")
        self._recent_menu: QMenu | None = None
        self._unsub_fns: list[Callable[[], None]] = []
        # 9.1.0 (OBS-1): loop-guard som bryter window.show/hide-pingpong.
        self._winloop_guard_until: float = 0.0
        self._lock = threading.RLock()
        self._loaded = False
        # Notification debouncing
        self._notify_queue: list[tuple[str, str, str, int]] = []
        self._notify_timer: QTimer | None = None
        self._notify_lock = threading.Lock()

    # ── Livscykel ────────────────────────────────────────────────────────

    def on_load(self, context: Any) -> None:
        with self._lock:
            if self._loaded:
                return
            self._context = context
            with suppress(Exception):
                self._log = context.get_logger("SystemTray")
            steps: tuple[tuple[str, Callable[[], None]], ...] = (
                ("_setup_paths", self._setup_paths),
                ("_setup_config", self._setup_config),
                ("_setup_signals", self._setup_signals),
                ("_setup_tray", self._setup_tray),
                ("_subscribe_events", self._subscribe_events),
                ("_setup_close_handler", self._setup_close_handler),
            )
            for step_name, step_fn in steps:
                try:
                    step_fn()
                except Exception:
                    self._log.exception(
                        "[TRAY] Setup fel: %s", step_name,
                    )
            self._loaded = True
            self._log.info("[TRAY] v%s laddad", PLUGIN_VERSION)

    def setup(self, context: Any) -> None:
        self.on_load(context)

    def on_unload(self) -> None:
        global _GLOBAL_TRAY_ICON
        with self._lock:
            for unsub in self._unsub_fns:
                with suppress(Exception):
                    unsub()
            self._unsub_fns.clear()
            if self._animation is not None:
                with suppress(Exception):
                    self._animation.stop()
            if self._notify_timer is not None:
                with suppress(Exception):
                    self._notify_timer.stop()
            if self._tray is not None:
                with suppress(Exception):
                    self._tray.hide()
                    self._tray.activated.disconnect()
            with _GLOBAL_TRAY_LOCK:
                if _GLOBAL_TRAY_ICON is self._tray:
                    _GLOBAL_TRAY_ICON = None
            self._tray = None
            self._tray_menu = None
            self._recent_menu = None
            self._loaded = False
            self._log.info("[TRAY] Avlastat")

    def on_close(self) -> None:
        self.on_unload()

    def get_tab(self) -> QWidget | None:
        if self._tab_widget is None:
            try:
                self._tab_widget = _SettingsTab(self)
            except Exception:
                self._log.exception("[TRAY] Kunde inte bygga tab")
                return None
        return self._tab_widget

    # ── Setup ─────────────────────────────────────────────────────────────

    def _setup_paths(self) -> None:
        if self._context:
            base = getattr(self._context, "base_path", None)
            if base is None:
                base = getattr(
                    self._context, "root_path", Path("."),
                )
            self._icon_folder = Path(base) / "Icon"
        self._icon_folder.mkdir(parents=True, exist_ok=True)

    def _setup_config(self) -> None:
        if not self._context:
            return
        cm = (
            getattr(self._context, "config_manager", None)
            or getattr(self._context, "settings", None)
            or getattr(self._context, "config", None)
        )
        if cm is not None:
            self._tray_config = TrayConfigHelper(cm)
        else:
            self._log.warning("[TRAY] Ingen ConfigManager")

    def _setup_signals(self) -> None:
        self._signals = _TraySignals()
        self._signals.show_window.connect(self._on_show_window)
        self._signals.hide_window.connect(self._on_hide_window)
        self._signals.toggle_window.connect(self._on_toggle_window)
        self._signals.quit_app.connect(self._on_quit)
        self._signals.notify.connect(self._on_notify_queued)
        self._signals.set_icon.connect(self._on_set_icon_by_name)
        # Debounce-timer för notifikationer
        self._notify_timer = QTimer()
        self._notify_timer.setSingleShot(True)
        self._notify_timer.setInterval(_NOTIFY_DEBOUNCE_MS)
        self._notify_timer.timeout.connect(self._flush_notifications)

    def _setup_tray(self) -> None:
        global _GLOBAL_TRAY_ICON
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._log.warning(
                "[TRAY] Ej tillgänglig på detta system"
            )
            return
        qapp = QApplication.instance()
        if qapp is None:
            self._log.error("[TRAY] QApplication saknas")
            return
        with _GLOBAL_TRAY_LOCK:
            if _GLOBAL_TRAY_ICON is not None:
                self._tray = _GLOBAL_TRAY_ICON
                self._tray_menu = self._build_context_menu()
                self._tray.setContextMenu(self._tray_menu)
                return
            icon = self._get_icon(IconState.NORMAL)
            self._tray = QSystemTrayIcon(icon, qapp)
            _GLOBAL_TRAY_ICON = self._tray
        self._tray.setToolTip(get_tooltip_normal())
        self._tray_menu = self._build_context_menu()
        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._animation = _AnimationTimer(frame_count=8)
        self._animation.tick.connect(self._on_animation_tick)
        self._tray.show()
        self._log.info("[TRAY] QSystemTrayIcon visas")

        # 9.2.0: watchdog — om ikonen försvinner (t.ex. Explorer-krasch) och
        # fönstret är dolt, återställ synligheten så att appen aldrig blir en
        # osynlig "zombie" som håller single-instance-låset.
        self._tray_watchdog = QTimer()
        self._tray_watchdog.setInterval(60_000)
        self._tray_watchdog.timeout.connect(self._tray_watchdog_tick)
        self._tray_watchdog.start()

    def is_icon_visible(self) -> bool:
        """9.3.0: exponerar ikonens synlighet för startup-självdiagnosen."""
        return bool(self._tray is not None and self._tray.isVisible())

    def _tray_watchdog_tick(self) -> None:
        """9.2.0: kontrollera att ikonen syns; återställ vid behov."""
        if self._tray is None:
            return
        try:
            if self._tray.isVisible():
                return
            self._log.warning(
                "[TRAY] Ikon ej synlig — försöker återställa",
                extra={"event": "tray.watchdog", "ctx": {"action": "reshow"}},
            )
            self._tray.show()
            root = self._get_root()
            window_hidden = bool(
                root is not None
                and hasattr(root, "isVisible")
                and not root.isVisible()
            )
            if not self._tray.isVisible() and window_hidden:
                self._emit_event("window.show", {})
        except Exception:
            self._log.debug("[TRAY] watchdog-fel", exc_info=True)

    def _build_context_menu(self) -> QMenu:
        menu = QMenu()
        open_action = QAction("Öppna Intygsgenerator", menu)
        open_action.triggered.connect(self._on_show_window)
        menu.addAction(open_action)
        settings_action = QAction("Inställningar", menu)
        settings_action.triggered.connect(self._on_open_settings)
        menu.addAction(settings_action)
        menu.addSeparator()
        self._recent_menu = QMenu("Senaste intyg", menu)
        menu.addMenu(self._recent_menu)
        self._refresh_recent_menu()
        menu.addSeparator()
        quit_action = QAction("Avsluta", menu)
        quit_action.triggered.connect(self._on_quit)
        menu.addAction(quit_action)
        return menu

    def _setup_close_handler(self) -> None:
        root = self._get_root()
        if root is None:
            return
        handler = getattr(root, "set_close_handler", None)
        if callable(handler):
            with suppress(Exception):
                handler(self._on_window_close_button)

    def _subscribe_events(self) -> None:
        if not self._context:
            return
        event_bus = getattr(self._context, "event_bus", None)
        if event_bus is None:
            return
        event_map: dict[str, Callable[[Any], None]] = {
            "certificate.generated": self._on_certificate_generated,
            "certificate.generation_failed": self._on_generation_failed,
            "certificates.batch.started": self._on_batch_started,
            "certificates.batch.completed": self._on_batch_completed,
            "tray.status_update": self._on_status_update,
            "tray.minimize": self._on_tray_minimize_event,
            "tray.restore": self._on_tray_restore_event,
            "window.hide": self._on_tray_minimize_event,
            "window.show": self._on_tray_restore_event,
            "app.shutdown": self._on_app_shutdown,
            "show_settings": self._on_show_settings_event,
            "notification": self._on_notification_event,
        }
        for name, handler in event_map.items():
            try:
                unsub = event_bus.subscribe(name, handler)
                if callable(unsub):
                    self._unsub_fns.append(unsub)
            except Exception:
                pass

    def _emit_event(
        self,
        event_name: str,
        data: dict[str, Any],
    ) -> None:
        if not self._context:
            return
        with suppress(Exception):
            emit = getattr(self._context, "emit_event", None)
            if callable(emit):
                emit(event_name, data)
                return
            bus = getattr(self._context, "event_bus", None)
            bus_emit = getattr(bus, "emit", None)
            if callable(bus_emit):
                bus_emit(event_name, data)

    # ── Ikonhantering ─────────────────────────────────────────────────────

    def _get_icon(
        self, state: IconState = IconState.NORMAL
    ) -> QIcon:
        with self._lock:
            if state not in self._icon_cache:
                self._icon_cache[state] = _load_icon(
                    self._icon_folder, state
                )
            return self._icon_cache[state]

    def _ensure_animation_icons(self) -> None:
        """Lazy: generera animationsikoner vid behov."""
        with self._lock:
            if self._animation_icons:
                return
        try:
            frames = IconGenerator.create_animation_frames(
                IconState.BUSY,
                frame_count=8,
                size=IconGenerator.get_platform_icon_size(),
            )
            icons = [_pil_to_qicon(f) for f in frames]
            icons = [i for i in icons if not i.isNull()]
            with self._lock:
                self._animation_icons = icons
        except Exception:
            with self._lock:
                self._animation_icons = []

    def _set_tray_icon(self, state: IconState) -> None:
        if self._tray is None:
            return
        self._tray.setIcon(self._get_icon(state))

    # ── Notification debouncing ──────────────────────────────────────────

    @Slot(str, str, str, int)
    def _on_notify_queued(
        self,
        title: str,
        message: str,
        level: str,
        duration_ms: int,
    ) -> None:
        """Köa notifikation med debouncing."""
        with self._notify_lock:
            self._notify_queue.append(
                (title, message, level, duration_ms)
            )
            # Begränsa kö
            if len(self._notify_queue) > _NOTIFY_MAX_QUEUE:
                self._notify_queue = (
                    self._notify_queue[-_NOTIFY_MAX_QUEUE:]
                )
        if self._notify_timer:
            self._notify_timer.start()

    def _flush_notifications(self) -> None:
        """Visa köade notifikationer (senaste vinner)."""
        with self._notify_lock:
            queue = list(self._notify_queue)
            self._notify_queue.clear()
        if not queue:
            return
        # Visa bara den senaste (undvik spam)
        title, message, level, duration = queue[-1]
        config = self._tray_config
        if (
            config is not None
            and not config.show_notifications
        ):
            return
        if self._tray is None:
            return
        icon = self._message_icon_from_level(level)
        with suppress(Exception):
            self._tray.showMessage(
                title, message, icon, max(0, int(duration)),
            )

    # ── Fönsterhantering ─────────────────────────────────────────────────

    @Slot()
    def _on_show_window(self) -> None:
        now = time.monotonic()
        if now < self._winloop_guard_until:  # 9.1.0: bryt pingpong
            return
        root = self._get_root()
        if root is None:
            return
        try:
            show = (
                getattr(self._context, "show_window", None)
                if self._context
                else None
            )
            if callable(show):
                show()
            elif hasattr(root, "showNormal"):
                root.showNormal()
                root.raise_()
                root.activateWindow()
            elif hasattr(root, "show"):
                root.show()
            if self._tray:
                self._tray.setToolTip(get_tooltip_normal())
            self._winloop_guard_until = time.monotonic() + 0.5
            self._emit_event("window.show", {})
        except Exception:
            self._log.exception("[TRAY] Kunde inte visa fönster")

    @Slot()
    def _on_hide_window(self) -> None:
        now = time.monotonic()
        if now < self._winloop_guard_until:  # 9.1.0: bryt pingpong
            return
        root = self._get_root()
        if root is None:
            return
        try:
            hide = (
                getattr(self._context, "hide_window", None)
                if self._context
                else None
            )
            if callable(hide):
                hide()
            elif hasattr(root, "hide"):
                root.hide()
            if self._tray:
                self._tray.setToolTip(get_tooltip_minimized())
            config = self._tray_config
            if (
                config
                and config.show_minimize_notification
                and self._tray
            ):
                self._tray.showMessage(
                    PLUGIN_NAME,
                    "Körs i bakgrunden. Klicka på "
                    "ikonen för att öppna.",
                    QSystemTrayIcon.MessageIcon.Information,
                    NOTIFICATION_DURATION_MS,
                )
            self._winloop_guard_until = time.monotonic() + 0.5
            self._emit_event("window.hide", {})
        except Exception:
            self._log.exception("[TRAY] Kunde inte dölja fönster")

    @Slot()
    def _on_toggle_window(self) -> None:
        root = self._get_root()
        if root is None:
            return
        try:
            toggle = (
                getattr(self._context, "toggle_window", None)
                if self._context
                else None
            )
            if callable(toggle):
                toggle()
                return
            visible = (
                bool(root.isVisible())
                if hasattr(root, "isVisible")
                else False
            )
            if visible:
                self._on_hide_window()
            else:
                self._on_show_window()
        except Exception:
            self._on_show_window()

    def _on_window_close_button(self) -> None:
        config = self._tray_config
        close_to_tray = config.close_to_tray if config else True
        if close_to_tray:
            self._on_hide_window()
        else:
            self._on_quit()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_tray_activated(
        self,
        reason: QSystemTrayIcon.ActivationReason,
    ) -> None:
        config = self._tray_config
        single = config.single_click_open if config else True
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if single:
                self._on_toggle_window()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if not single:
                self._on_toggle_window()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self._on_toggle_window()

    @Slot()
    def _on_open_settings(self) -> None:
        self._on_show_window()
        self._emit_event("show_settings", {"tab": "startup"})

    @Slot()
    def _on_quit(self) -> None:
        self._log.info("[TRAY] Avslutar via meny")
        self._emit_event("app.quit_confirmed", {})
        self._emit_event("app.shutdown", {})
        qapp = QApplication.instance()
        if qapp is not None:
            qapp.quit()

    # ── Animation ─────────────────────────────────────────────────────────

    @Slot(int)
    def _on_animation_tick(self, frame: int) -> None:
        if self._tray is None:
            return
        with self._lock:
            icons = list(self._animation_icons)
        if icons and 0 <= frame < len(icons):
            self._tray.setIcon(icons[frame])
        else:
            self._tray.setIcon(self._get_icon(IconState.BUSY))

    @Slot(str)
    def _on_set_icon_by_name(self, state_name: str) -> None:
        with suppress(Exception):
            state = IconState.from_string(state_name)
            self._set_tray_icon(state)

    # ── Event-handlers ────────────────────────────────────────────────────

    def _on_certificate_generated(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        template = str(data.get("template", "") or "")
        number_raw = data.get("number", "")
        path_text = str(data.get("path", "") or "")
        filename = str(data.get("filename", "") or "")
        if path_text and self._tray_config:
            with suppress(Exception):
                number = int(number_raw) if number_raw else 0
                cert = RecentCertificate(
                    name=filename or Path(path_text).name,
                    path=Path(path_text),
                    template=template,
                    number=number,
                )
                self._tray_config.add_recent_certificate(cert)
                self._refresh_recent_menu()
                if self._tab_widget:
                    self._tab_widget.refresh()
        title = "✅ Intyg skapat"
        msg = (
            f"#{number_raw} — {template}" if number_raw else template
        )
        if self._signals:
            self._signals.notify.emit(
                title,
                msg,
                NotificationType.SUCCESS.value,
                NOTIFICATION_DURATION_MS,
            )
            self._signals.set_icon.emit(IconState.SUCCESS.value)
            QTimer.singleShot(3000, self._restore_normal_icon)

    def _on_generation_failed(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        error = str(data.get("error", "Okänt fel"))
        if self._signals:
            self._signals.notify.emit(
                "❌ Generering misslyckades",
                error,
                NotificationType.ERROR.value,
                NOTIFICATION_DURATION_MS,
            )
            self._signals.set_icon.emit(IconState.ERROR.value)
            QTimer.singleShot(5000, self._restore_normal_icon)

    def _on_batch_started(self, data: Any) -> None:
        self._ensure_animation_icons()
        if self._animation:
            self._animation.start()
        if self._tray:
            self._tray.setToolTip(get_tooltip_busy())

    def _on_batch_completed(self, data: Any) -> None:
        if self._animation:
            self._animation.stop()
        if self._tray:
            self._tray.setToolTip(get_tooltip_normal())
        if isinstance(data, dict):
            total = int(data.get("total", 0) or 0)
            succeeded = int(data.get("succeeded", 0) or 0)
            failed = int(data.get("failed", 0) or 0)
            msg = f"{succeeded}/{total} skapade"
            if failed:
                msg += f" ({failed} fel)"
            notif_type = (
                NotificationType.SUCCESS
                if failed == 0
                else NotificationType.WARNING
            )
            icon_state = (
                IconState.SUCCESS
                if failed == 0
                else IconState.WARNING
            )
            if self._signals:
                self._signals.notify.emit(
                    "Batch klar",
                    msg,
                    notif_type.value,
                    NOTIFICATION_DURATION_MS,
                )
                self._signals.set_icon.emit(icon_state.value)
                QTimer.singleShot(
                    4000, self._restore_normal_icon,
                )
        self._set_tray_icon(IconState.NORMAL)

    def _on_status_update(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        title = str(data.get("title", PLUGIN_NAME) or PLUGIN_NAME)
        message = str(data.get("message", "") or "")
        level = str(
            data.get("level", NotificationType.INFO.value)
            or NotificationType.INFO.value
        )
        if message and self._signals:
            self._signals.notify.emit(
                title,
                message,
                level,
                NOTIFICATION_DURATION_MS,
            )

    def _on_notification_event(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        message = str(data.get("message", "") or "")
        level = str(data.get("level", "info") or "info").lower()
        title = str(data.get("title", "") or "")
        if not message:
            return
        if not title:
            title_map = {
                "error": "❌ Fel",
                "warning": "⚠️ Varning",
                "success": "✅ Klart",
                "info": "ℹ️ Information",
            }
            title = title_map.get(level, "ℹ️ Information")
        if self._signals:
            self._signals.notify.emit(
                title,
                message,
                level,
                NOTIFICATION_DURATION_MS,
            )

    def _on_tray_minimize_event(self, data: Any) -> None:
        if self._signals:
            self._signals.hide_window.emit()

    def _on_tray_restore_event(self, data: Any) -> None:
        if self._signals:
            self._signals.show_window.emit()

    def _on_app_shutdown(self, data: Any) -> None:
        if self._animation:
            self._animation.stop()

    def _on_show_settings_event(self, data: Any) -> None:
        self._on_show_window()

    # ── Senaste intyg ─────────────────────────────────────────────────────

    def _refresh_recent_menu(self) -> None:
        if self._recent_menu is None:
            return
        try:
            self._recent_menu.clear()
            config = self._tray_config
            if config is None:
                self._recent_menu.addAction(
                    "(Inga intyg)"
                ).setEnabled(False)
                return
            certs = config.get_recent_certificates()
            if not certs:
                self._recent_menu.addAction(
                    "(Inga intyg)"
                ).setEnabled(False)
                return
            for cert in certs:
                text = f"{cert.short_name} ({cert.age_text})"
                action = QAction(text, self._recent_menu)
                action.setEnabled(cert.exists)
                if cert.exists:
                    action.setToolTip(str(cert.path))
                    path_copy = cert.path
                    action.triggered.connect(
                        lambda _c=False, p=path_copy: (
                            self._open_path(p)
                        )
                    )
                self._recent_menu.addAction(action)
            self._recent_menu.addSeparator()
            clear = QAction("Rensa lista", self._recent_menu)
            clear.triggered.connect(self._clear_recent_certs)
            self._recent_menu.addAction(clear)
        except Exception:
            self._log.exception("[TRAY] Recent menu fel")

    def _clear_recent_certs(self) -> None:
        config = self._tray_config
        if config is not None:
            config.recent_certificates = []
        self._refresh_recent_menu()
        if self._tab_widget:
            self._tab_widget.refresh()

    # ── Hjälp ─────────────────────────────────────────────────────────────

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            if Platform.is_windows():
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif Platform.is_macos():
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception:
            self._log.exception("[TRAY] Kunde inte öppna: %s", path)

    def _get_root(self) -> Any | None:
        if not self._context:
            return None
        return getattr(self._context, "root", None)

    def _restore_normal_icon(self) -> None:
        if self._signals:
            self._signals.set_icon.emit(IconState.NORMAL.value)

    @staticmethod
    def _message_icon_from_level(
        level: str,
    ) -> QSystemTrayIcon.MessageIcon:
        notif = NotificationType.from_string(level)
        if notif == NotificationType.ERROR:
            return QSystemTrayIcon.MessageIcon.Critical
        if notif == NotificationType.WARNING:
            return QSystemTrayIcon.MessageIcon.Warning
        return QSystemTrayIcon.MessageIcon.Information

    # ── Publikt API ───────────────────────────────────────────────────────

    def show_notification(
        self,
        title: str,
        message: str,
        notification_type: NotificationType = NotificationType.INFO,
        duration_ms: int = NOTIFICATION_DURATION_MS,
    ) -> None:
        if self._signals:
            self._signals.notify.emit(
                title,
                message,
                notification_type.value,
                duration_ms,
            )

    def set_icon_state(self, state: IconState) -> None:
        if self._signals:
            self._signals.set_icon.emit(state.value)

    def get_tray_icon(self) -> QSystemTrayIcon | None:
        return self._tray

    def is_tray_available(self) -> bool:
        return (
            self._tray is not None
            and QSystemTrayIcon.isSystemTrayAvailable()
        )

    def get_statistics(self) -> dict[str, Any]:
        return {
            "available": self.is_tray_available(),
            "has_tray": self._tray is not None,
            "has_menu": self._tray_menu is not None,
            "recent_count": (
                len(self._tray_config.get_recent_certificates())
                if self._tray_config
                else 0
            ),
            "platform": Platform.current().to_dict(),
            "version": self.version,
            "copyright": COPYRIGHT,
        }
    def is_tray_active(self) -> bool:
        """True om tray-ikon finns och systemfältet är tillgängligt."""
        return (
            self._tray is not None
            and QSystemTrayIcon.isSystemTrayAvailable()
        )

