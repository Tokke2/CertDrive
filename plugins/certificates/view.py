# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil:    plugins/certificates/view.py
Sökväg: Intygsgenerator/plugins/certificates/view.py

Intygsgenerator — återanvändbara PySide6-widgets för Certificates-pluginet.

UI-komponenterna är medvetet frikopplade från engine.py. Validering och
auto-fält skickas in som callbacks från plugin-controllern.

Version 10.3.0 (uppgraderad från 10.1.0, 2026-09-21)
====================================================
1. ADAPTIV UI ("anpassat i text och fönster"): alla fasta pixelmått skalas med
   fontmetrik via `_sp()` (DPI/teckenstorlek, §17/§30) — fail-safe till basmått.
2. MINNE + SNABBHET: PlaceholderPreviewWidget renderar om endast när innehållet
   faktiskt ändrades (skippar setPlainText → ingen onödig document-reparse).
3. All funktionalitet och alla publika API:er oförändrade (MUST-005).
"""

from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

__version__: str = "10.3.0"
__all__: list[str] = [
    "FieldsScrollArea",
    "ProgressDialog",
    "FieldWidget",
    "PlaceholderPreviewWidget",
    "EngineErrorWidget",
]

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

ValidationCallable = Callable[[str], Any]
AutoFieldCallable = Callable[[str], bool]


def _sp(base: int) -> int:
    """Skala pixelmått efter fontmetrik (DPI/teckenstorlek) — §17/§30.

    Fail-safe: utan QApplication/fontmetrik returneras basmåttet.
    """
    try:
        app = QApplication.instance()
        if app is not None:
            height = app.fontMetrics().height()
            if height > 0:
                return max(base, int(round(base * height / 16.0)))
    except Exception:
        pass
    return base


def _validation_state(result: Any) -> tuple[bool, bool, str, str]:
    """Normalisera engine- och degraded-mode-valideringsresultat."""
    if result is None:
        return False, False, "", ""
    formatted = str(getattr(result, "formatted_value", "") or "")
    message = str(getattr(result, "message", "") or "")
    is_valid = bool(getattr(result, "is_valid", False))
    is_warning = bool(getattr(result, "is_warning", False))
    if not is_warning:
        result_name = str(getattr(getattr(result, "result", None), "name", "")).upper()
        is_warning = result_name == "WARNING"
    return is_valid, is_warning, formatted, message


class FieldsScrollArea(QScrollArea):
    """Responsiv och mus-hjulsvänlig yta för dynamiska inmatningsfält."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content = QWidget(self)
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(_sp(4), _sp(4), _sp(4), _sp(4))
        self._layout.setSpacing(_sp(6))
        self._content.installEventFilter(self)
        self.setWidget(self._content)

    @property
    def content(self) -> QWidget:
        return self._content

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._layout

    def clear_widgets(self) -> None:
        """Ta bort tidigare fält utan att lämna gamla widgetar i layouten."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def add_stretch(self) -> None:
        self._layout.addSpacerItem(
            QSpacerItem(
                0,
                0,
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Expanding,
            )
        )

    def scroll_to_top(self) -> None:
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        """Säkerställ att mushjulet alltid fortsätter att scrolla formuläret."""
        if watched is self._content and event.type() == QEvent.Type.Wheel:
            scrollbar = self.verticalScrollBar()
            if scrollbar.maximum() > scrollbar.minimum():
                steps = event.angleDelta().y() // 120
                if steps:
                    scrollbar.setValue(
                        scrollbar.value() - steps * max(1, scrollbar.singleStep()) * 3
                    )
                event.accept()
                return True
        return super().eventFilter(watched, event)


class ProgressDialog(QDialog):
    """Liten modal progressdialog för batcharbete utanför UI-tråden."""

    cancelled = Signal()

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        cancellable: bool = False,
    ) -> None:
        super().__init__(parent)
        self._cancelled = False
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(_sp(440))
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(_sp(20), _sp(20), _sp(20), _sp(20))
        layout.setSpacing(_sp(12))
        self._message_label = QLabel(message, self)
        self._message_label.setWordWrap(True)
        layout.addWidget(self._message_label)

        self._progress_bar = QProgressBar(self)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setMinimumHeight(_sp(24))
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel(self)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        if cancellable:
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Cancel,
                parent=self,
            )
            buttons.rejected.connect(self._cancel)
            layout.addWidget(buttons)

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def update_progress(self, percent: int, message: str = "") -> None:
        self._progress_bar.setValue(max(0, min(100, int(percent))))
        if message:
            self._status_label.setText(message)

    def set_message(self, message: str) -> None:
        self._message_label.setText(message)

    def _cancel(self) -> None:
        self._cancelled = True
        self.cancelled.emit()
        self.reject()


class FieldWidget(QWidget):
    """Ett tillgängligt formulärfält med valfri VIN-/REG-validering."""

    value_changed = Signal(str, str)

    def __init__(
        self,
        field_name: str,
        display_name: str,
        *,
        default_value: str = "",
        validator: ValidationCallable | None = None,
        field_kind: str = "",
        tooltip: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._field_name = field_name
        self._validator = validator
        self._field_kind = field_kind.casefold()
        self._base_tooltip = tooltip

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, _sp(2), 0, _sp(2))
        layout.setSpacing(_sp(8))

        prefix = "🚗 " if self._field_kind == "vin" else "🔢 " if self._field_kind == "reg" else ""
        label = QLabel(f"{prefix}{display_name}:", self)
        # 10.3.0: adaptiva etikettmått — text klippas aldrig (§30).
        label.setMinimumWidth(_sp(150))
        label.setMaximumWidth(_sp(230))
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(label)

        self._entry = QLineEdit(self)
        self._entry.setText(default_value)
        self._entry.setMinimumHeight(_sp(32))
        self._entry.setAccessibleName(display_name)
        self._entry.setToolTip(tooltip)
        self._entry.textChanged.connect(self._on_text_changed)
        self._entry.editingFinished.connect(self.validate)
        layout.addWidget(self._entry, stretch=1)

        self._status_label = QLabel(self)
        self._status_label.setFixedWidth(_sp(30))
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

    @property
    def field_name(self) -> str:
        return self._field_name

    def get_value(self) -> str:
        return self._entry.text().strip()

    def set_value(self, value: str) -> None:
        self._entry.setText(str(value))

    def clear(self) -> None:
        self._entry.clear()
        self._status_label.clear()
        self._entry.setToolTip(self._base_tooltip)

    def focus_entry(self) -> None:
        self._entry.setFocus(Qt.FocusReason.OtherFocusReason)

    def validate(self) -> bool:
        """Validera synkront vid avslutad inmatning, aldrig vid varje tangent."""
        value = self.get_value()
        if not value or self._validator is None:
            self._status_label.clear()
            return True
        try:
            result = self._validator(value)
        except Exception as exc:
            self._status_label.setText("⚠️")
            self._status_label.setToolTip(str(exc))
            return False

        is_valid, is_warning, formatted, message = _validation_state(result)
        if formatted and formatted != value:
            self._entry.blockSignals(True)
            self._entry.setText(formatted)
            self._entry.blockSignals(False)
            self.value_changed.emit(self._field_name, formatted)

        if is_valid and not is_warning:
            self._status_label.setText("✅")
        elif is_warning:
            self._status_label.setText("⚠️")
        else:
            self._status_label.setText("❌")
        self._status_label.setToolTip(message)
        return is_valid

    def _on_text_changed(self, value: str) -> None:
        if not value.strip():
            self._status_label.clear()
        self.value_changed.emit(self._field_name, value)


class PlaceholderPreviewWidget(QFrame):
    """Debounce-vänlig textförhandsvisning av aktuella placeholder-värden.

    10.3.0: renderar om endast när texten faktiskt ändrades — sparar både
    minne och CPU (ingen onödig QTextEdit-documentreparse per tangent).
    """

    def __init__(
        self,
        is_auto_field: AutoFieldCallable,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_auto_field = is_auto_field
        self._last_text: str | None = None
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(_sp(8), _sp(8), _sp(8), _sp(8))
        layout.setSpacing(_sp(4))

        title = QLabel("👁️ Förhandsgranskning av placeholders", self)
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        self._editor = QTextEdit(self)
        self._editor.setReadOnly(True)
        self._editor.setMinimumHeight(_sp(100))
        self._editor.setMaximumHeight(_sp(180))
        self._editor.setPlaceholderText("Värden visas när fälten fylls i…")
        layout.addWidget(self._editor)

    def update_preview(
        self,
        placeholders: list[str],
        values: dict[str, str],
    ) -> None:
        lines: list[str] = []
        for field_name in sorted(dict.fromkeys(placeholders), key=str.casefold):
            value = values.get(field_name, "")
            if self._is_auto_field(field_name):
                marker, display = "🤖", "(automatiskt)"
            elif value:
                marker, display = "✅", value
            else:
                marker, display = "", "(tomt)"
            lines.append(f"{marker} ::{field_name}:: → {display}")
        text = "\n".join(lines) or "Inga placeholders att visa."
        if text == self._last_text:
            return  # 10.3.0: ingen onödig re-render
        self._last_text = text
        self._editor.setPlainText(text)


class EngineErrorWidget(QWidget):
    """Felpanel som förklarar varför intygsfliken inte kan initieras."""

    def __init__(self, error_message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(_sp(40), _sp(40), _sp(40), _sp(40))
        layout.setSpacing(_sp(14))
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("⚠️", self)
        icon_font = icon.font()
        icon_font.setPointSize(44)
        icon.setFont(icon_font)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)

        title = QLabel("Intygsmodulen kunde inte laddas", self)
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(max(14, title_font.pointSize() + 3))
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        detail = QLabel(f"Fel: {error_message}", self)
        detail.setWordWrap(True)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(detail)

        hint = QLabel(
            "Kontrollera engine.py, python-docx, pypdf och en DOCX→PDF-konverterare som LibreOffice.",
            self,
        )
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        layout.addStretch(1)
