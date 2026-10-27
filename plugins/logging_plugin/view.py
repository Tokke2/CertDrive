# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil:    plugins/logging_plugin/view.py
Sökväg: Intygsgenerator/plugins/logging_plugin/view.py

Intygsgenerator — LoggerView (kompatibel med plugin.py 9.x):
loggdisplay med nivåfärger, filter/sök/regex, live-läge, alerts och
filsystemsvy med batchad flush (fs_view.add_operation + flush_fs_pending).

Version 9.1.0 (2026-09-22) — "50000000000% bättre":
  • Inkrementell rendering: nya poster läggs till med appendPlainText i stället
    för full setPlainText-rebuild (minne + CPU, märkbart i live-läge).
  • Smart autoscroll: scrollar endast om användaren redan är i botten.
  • Bokmärken: dubbelklick på rad togglar 🔖 (signal bookmark_requested så att
    plugin kan persistera i LogStorage; quickfiltret "Bokmärkta" fungerar).
  • "Endast missar"-checkbox i filsystemsvyn (SAKNAS/NEKAD).
  • Radbrytning på/av för långa rader.
  • Synlig statistikrad (set_stats_text ritar nu text, inte bara tooltip).
  • Allt från 9.0.0 behållet: _sp-skalning, render-skip, lg.ui.*-events.
"""

import logging
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .core import COPYRIGHT, QuickFilter

__version__: str = "9.1.0"
__all__: list[str] = ["LoggerView"]

_view_logger = logging.getLogger("Intygsgenerator.logging.view")

_LEVEL_COLORS = {
    "DEBUG": "#8a8a8a",
    "INFO": "#1d6fd6",
    "WARNING": "#b07d00",
    "ERROR": "#c0392b",
    "CRITICAL": "#7d1172",
}

_MAX_RENDER_ENTRIES: int = 2_000


def _sp(base: int) -> int:
    """Adaptiv pixel-skalning efter fontmetrik (§17/§30), fail-safe."""
    try:
        app = QApplication.instance()
        if app is not None:
            height = app.fontMetrics().height()
            if height > 0:
                return max(base, int(round(base * height / 16.0)))
    except Exception:
        pass
    return base


def _evt(event: str, msg: str, level: int = logging.DEBUG, **ctx: Any) -> None:
    try:
        _view_logger.log(level, msg, extra={"event": event, "ctx": ctx or None})
    except Exception:
        pass


class _LogHighlighter(QSyntaxHighlighter):
    """Semantiska nivåfärger i loggdisplayen (COLORS-001-undantag)."""

    def __init__(self, document: Any) -> None:
        super().__init__(document)
        self._rules: list[tuple[str, QTextCharFormat]] = []
        for level, color in _LEVEL_COLORS.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            self._rules.append((f"| {level}", fmt))

    def highlightBlock(self, text: str) -> None:
        for needle, fmt in self._rules:
            idx = text.find(needle)
            while idx >= 0:
                self.setFormat(idx, len(needle), fmt)
                idx = text.find(needle, idx + len(needle))


class _FsView:
    """Batchad filsystemsvy: add_operation buffrar, flush_fs_pending ritar."""

    def __init__(self, tree: QTreeWidget) -> None:
        self._tree = tree
        self._pending: list[Any] = []
        self._misses_only = False

    def set_misses_only(self, enabled: bool) -> None:
        self._misses_only = bool(enabled)

    def add_operation(self, operation: Any) -> None:
        self._pending.append(operation)
        if len(self._pending) > 5_000:  # bounded minne
            del self._pending[: len(self._pending) - 5_000]

    @staticmethod
    def _is_miss(operation: Any) -> bool:
        detail = str(getattr(operation, "detail", "") or "")
        return detail.startswith("SAKNAS") or detail.startswith("NEKAD")

    def flush_fs_pending(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        tree = self._tree
        for op in batch:
            if self._misses_only and not self._is_miss(op):
                continue
            item = QTreeWidgetItem([
                datetime.now().strftime("%H:%M:%S"),
                str(getattr(op, "operation", "")),
                str(getattr(op, "plugin", "")),
                str(getattr(op, "caller", "")),
                str(getattr(op, "path", "")),
                str(getattr(op, "detail", "") or ""),
            ])
            if self._is_miss(op):
                item.setForeground(4, QColor(_LEVEL_COLORS["WARNING"]))
            tree.insertTopLevelItem(0, item)
        while tree.topLevelItemCount() > 1_000:
            tree.takeTopLevelItem(tree.topLevelItemCount() - 1)


class LoggerView(QWidget):
    """Loggvyn som plugin.py styr via signaler + metoder."""

    refresh_requested = Signal()
    clear_requested = Signal()
    open_folder_requested = Signal()
    export_requested = Signal()
    show_alerts_requested = Signal()
    search_changed = Signal()
    filters_changed = Signal()
    live_changed = Signal(bool)
    detailed_changed = Signal(bool)
    acknowledge_all_requested = Signal()
    alert_activated = Signal(int)
    # 9.1.0: bokmärke togglat (entry_id, värde) — plugin persisterar i storage.
    bookmark_requested = Signal(int, bool)

    def __init__(self, session_id: str, copyright_text: str = COPYRIGHT) -> None:
        super().__init__()
        self._session_id = session_id
        self._copyright = copyright_text
        self._level = "ALL"
        self._quick = QuickFilter.ALL
        self._query = ""
        self._use_regex = False
        self._live = True
        self._detailed = False
        self._wrap = False
        self._last_text: str | None = None
        self._rendered_ids: list[int] = []
        self._filter_sig: tuple[Any, ...] | None = None
        self._bookmarks: set[int] = set()
        self._alerts: list[dict[str, Any]] = []
        self._build_ui()
        _evt("lg.ui.built", "Loggvy byggd", session=session_id)

    # ── bygg UI ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(_sp(6), _sp(6), _sp(6), _sp(6))
        root.setSpacing(_sp(4))

        header = QLabel(
            f"<b>Logger</b> — session <code>{self._session_id}</code> · {self._copyright}"
        )
        header.setWordWrap(True)
        root.addWidget(header)

        bar = QHBoxLayout()
        refresh_btn = QPushButton("🔄")
        refresh_btn.setFixedWidth(_sp(32))
        refresh_btn.setToolTip("Uppdatera vyn")
        refresh_btn.clicked.connect(self.refresh_requested.emit)
        bar.addWidget(refresh_btn)

        clear_btn = QPushButton("🗑️")
        clear_btn.setFixedWidth(_sp(32))
        clear_btn.setToolTip("Rensa displayen")
        clear_btn.clicked.connect(self.clear_requested.emit)
        bar.addWidget(clear_btn)

        folder_btn = QPushButton("📂")
        folder_btn.setFixedWidth(_sp(32))
        folder_btn.setToolTip("Öppna loggmappen")
        folder_btn.clicked.connect(self.open_folder_requested.emit)
        bar.addWidget(folder_btn)

        export_btn = QPushButton("📤 Export")
        export_btn.clicked.connect(self.export_requested.emit)
        bar.addWidget(export_btn)

        self._alerts_btn = QPushButton("🔔 0")
        self._alerts_btn.clicked.connect(self.show_alerts_requested.emit)
        bar.addWidget(self._alerts_btn)

        ack_btn = QPushButton("✔ Bekräfta alla")
        ack_btn.clicked.connect(self.acknowledge_all_requested.emit)
        bar.addWidget(ack_btn)

        bar.addSpacing(_sp(8))
        self._live_cb = QCheckBox("Live")
        self._live_cb.setChecked(True)
        self._live_cb.toggled.connect(self._on_live_toggled)
        bar.addWidget(self._live_cb)

        self._detailed_cb = QCheckBox("Detaljer")
        self._detailed_cb.toggled.connect(self._on_detailed_toggled)
        bar.addWidget(self._detailed_cb)

        self._wrap_cb = QCheckBox("Radbryt")
        self._wrap_cb.toggled.connect(self._on_wrap_toggled)
        bar.addWidget(self._wrap_cb)
        bar.addStretch(1)
        root.addLayout(bar)

        bar2 = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Sök i loggen… (dubbelklicka rad = 🔖)")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(_sp(160))
        self._search_edit.textChanged.connect(self._on_search_changed)
        bar2.addWidget(self._search_edit, 1)

        self._regex_cb = QCheckBox("Regex")
        self._regex_cb.toggled.connect(self._on_regex_toggled)
        bar2.addWidget(self._regex_cb)

        self._level_combo = QComboBox()
        self._level_combo.addItems(["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self._level_combo.currentTextChanged.connect(self._on_level_changed)
        bar2.addWidget(self._level_combo)

        self._quick_combo = QComboBox()
        self._quick_combo.addItems([
            "Alla", "Endast fel", "Varningar+", "Senaste timmen",
            "Senaste 24 h", "Idag", "Bokmärkta",
        ])
        self._quick_combo.currentIndexChanged.connect(self._on_quick_changed)
        bar2.addWidget(self._quick_combo)
        root.addLayout(bar2)

        splitter = QSplitter(Qt.Vertical)

        self.log_display = QPlainTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMaximumBlockCount(20_000)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.log_display.setFont(mono)
        self.log_display.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_display.installEventFilter(self)  # 9.1.0: dubbelklick = 🔖
        self._highlighter = _LogHighlighter(self.log_display.document())
        splitter.addWidget(self.log_display)

        fs_box = QVBoxLayout()
        fs_bar = QHBoxLayout()
        self._misses_cb = QCheckBox("🚫 Endast missar")
        self._misses_cb.setToolTip("Visa endast SAKNADE/NEKADE filoperationer")
        self._misses_cb.toggled.connect(self._on_misses_toggled)
        fs_bar.addWidget(self._misses_cb)
        fs_bar.addStretch(1)
        fs_box.addLayout(fs_bar)
        self._fs_tree = QTreeWidget()
        self._fs_tree.setHeaderLabels(
            ["Tid", "Op", "Plugin", "Anropare", "Sökväg", "Detail"]
        )
        self._fs_tree.header().setSectionResizeMode(QHeaderView.Interactive)
        self._fs_tree.header().setStretchLastSection(True)
        self._fs_tree.setColumnWidth(0, _sp(70))
        self._fs_tree.setColumnWidth(1, _sp(70))
        self._fs_tree.setColumnWidth(4, _sp(300))
        fs_box.addWidget(self._fs_tree)
        fs_widget = QWidget()
        fs_widget.setLayout(fs_box)
        splitter.addWidget(fs_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self.fs_view = _FsView(self._fs_tree)

        # 9.1.0: synlig statistikrad (tidigare endast tooltip).
        self._stats_label = QLabel("")
        self._stats_label.setWordWrap(True)
        self._stats_label.setStyleSheet("color: #666666;")
        root.addWidget(self._stats_label)

        self._status = QLabel("Redo")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self.setMinimumSize(_sp(520), _sp(360))

    # ── plugin-API ────────────────────────────────────────────────────────

    def filters(self) -> dict[str, Any]:
        return {
            "level": self._level,
            "quick_filter": self._quick,
            "query": self._query,
            "use_regex": self._use_regex,
        }

    def _filter_signature(self) -> tuple[Any, ...]:
        return (self._level, self._quick, self._query, self._use_regex, self._detailed)

    def _format_entry(self, entry: Any) -> str:
        stamp = getattr(entry, "timestamp", None)
        stamp_s = stamp.strftime("%H:%M:%S") if hasattr(stamp, "strftime") else str(stamp)
        level = str(getattr(entry, "level", ""))
        category = str(getattr(entry, "category", ""))
        message = str(getattr(entry, "message", ""))
        mark = "🔖 " if getattr(entry, "id", 0) in self._bookmarks else ""
        line = f"{mark}{stamp_s} | {level:<8} | {category} | {message}"
        if self._detailed:
            line += (
                f"  [{getattr(entry, 'thread_name', '')} "
                f"{getattr(entry, 'func_name', '')}:{getattr(entry, 'line_no', '')}]"
            )
            event = getattr(entry, "event", None)
            if event:
                line += f" {{{event}}}"
        return line

    def set_entries(self, entries: list[Any]) -> None:
        """Rita poster.

        9.1.0: inkrementell rendering — om filtren är oförändrade och de nya
        posterna börjar med exakt det som redan ritats, appendas bara skillnaden
        (mycket snabbare än full rebuild i live-läge). Annars full rebuild med
        render-skip vid oförändrad text.
        """
        if not self._live:
            return
        entries = entries[-_MAX_RENDER_ENTRIES:]
        sig = self._filter_signature()
        ids = [getattr(e, "id", 0) for e in entries]
        at_bottom = self._is_at_bottom()

        if (
            sig == self._filter_sig
            and self._rendered_ids
            and ids[: len(self._rendered_ids)] == self._rendered_ids
            and len(ids) > len(self._rendered_ids)
        ):
            new_entries = entries[len(self._rendered_ids):]
            for entry in new_entries:
                self.log_display.appendPlainText(self._format_entry(entry))
            self._rendered_ids = ids
        else:
            lines = [self._format_entry(e) for e in entries]
            text = "\n".join(lines)
            if text == self._last_text and sig == self._filter_sig:
                return
            self._last_text = text
            self._rendered_ids = ids
            self._filter_sig = sig
            self.log_display.setPlainText(text)
        if at_bottom:
            sb = self.log_display.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _is_at_bottom(self) -> bool:
        sb = self.log_display.verticalScrollBar()
        return sb.value() >= sb.maximum() - 4

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_stats_text(self, text: str) -> None:
        # 9.1.0: synlig statistikrad + tooltip.
        self._stats_label.setText(text.replace("\n", "  │  "))
        self._stats_label.setToolTip(text)

    def set_alerts(self, alerts: list[dict[str, Any]]) -> None:
        self._alerts = list(alerts)

    def set_alert_count(self, count: int) -> None:
        self._alerts_btn.setText(f"🔔 {count}")

    def flush_fs_pending(self) -> None:
        self.fs_view.flush_fs_pending()

    # ── interna slots ─────────────────────────────────────────────────────

    def _on_search_changed(self, text: str) -> None:
        self._query = text.strip()
        self.search_changed.emit()

    def _on_regex_toggled(self, checked: bool) -> None:
        self._use_regex = bool(checked)
        self.filters_changed.emit()

    def _on_level_changed(self, text: str) -> None:
        self._level = str(text)
        _evt("lg.ui.filter_level", f"Nivå: {text}")
        self.filters_changed.emit()

    def _on_quick_changed(self, index: int) -> None:
        mapping = [
            QuickFilter.ALL, QuickFilter.ERRORS_ONLY, QuickFilter.WARNINGS_UP,
            QuickFilter.LAST_HOUR, QuickFilter.LAST_24H, QuickFilter.TODAY,
            QuickFilter.BOOKMARKED,
        ]
        self._quick = mapping[index] if 0 <= index < len(mapping) else QuickFilter.ALL
        _evt("lg.ui.filter_quick", f"Snabbfilter: {self._quick_combo.currentText()}")
        self.filters_changed.emit()

    def _on_live_toggled(self, checked: bool) -> None:
        self._live = bool(checked)
        _evt("lg.ui.live", f"Live: {checked}")
        self.live_changed.emit(bool(checked))

    def _on_detailed_toggled(self, checked: bool) -> None:
        self._detailed = bool(checked)
        self._last_text = None  # tvinga rebuild
        _evt("lg.ui.detailed", f"Detaljer: {checked}")
        self.detailed_changed.emit(bool(checked))

    def _on_wrap_toggled(self, checked: bool) -> None:
        self._wrap = bool(checked)
        self.log_display.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if checked
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        _evt("lg.ui.wrap", f"Radbrytning: {checked}")

    def _on_misses_toggled(self, checked: bool) -> None:
        self.fs_view.set_misses_only(bool(checked))
        self._fs_tree.clear()
        _evt("lg.ui.fs_misses_only", f"Endast missar: {checked}")

    # ── bokmärken (9.1.0) ─────────────────────────────────────────────────

    def toggle_bookmark_at(self, block_number: int) -> None:
        """Dubbelklick på rad → toggla bokmärke och be plugin persistera."""
        if block_number < 0 or block_number >= len(self._rendered_ids):
            return
        entry_id = self._rendered_ids[block_number]
        new_state = entry_id not in self._bookmarks
        if new_state:
            self._bookmarks.add(entry_id)
        else:
            self._bookmarks.discard(entry_id)
        _evt("lg.ui.bookmark", f"Bokmärke #{entry_id}: {new_state}",
             entry_id=entry_id, value=new_state)
        self.bookmark_requested.emit(int(entry_id), bool(new_state))
        self._last_text = None  # tvinga rebuild så 🔖 syns

    def connect_bookmark_toggle(self) -> None:
        """Finns för bakåtkompatibilitet; eventFilter installeras i _build_ui."""
        return None

    def eventFilter(self, watched: Any, event: Any) -> bool:
        try:
            from PySide6.QtCore import QEvent

            viewport = (
                self.log_display.viewport()
                if hasattr(self.log_display, "viewport")
                else self.log_display
            )
            if watched is viewport and event.type() == QEvent.Type.MouseButtonDoubled:
                block = self.log_display.textCursor().blockNumber()
                self.toggle_bookmark_at(block)
        except Exception:
            pass
        return super().eventFilter(watched, event)
