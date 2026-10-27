from __future__ import annotations

"""
Fil:    plugins/settings/view.py
Sökväg: Intygsgenerator/plugins/settings/view.py

Intygsgenerator — återanvändbara PySide6-vyer, dialoger och kontrollfabriker
för Settings-pluginets 12 flikar. Filen innehåller ingen fil-I/O eller
inställningslogik; det ägs av settings/plugin.py och settings/workers.py.
"""

from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

__version__: str = "9.0.0"
__all__: list[str] = [
    "SettingsScrollTab",
    "SettingsShell",
    "ShortcutEditDialog",
    "ProfileDialog",
    "make_separator",
    "make_label_row",
    "make_button_row",
    "make_action_button",
    "add_setting_control",
]

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)


def _sp(base: int) -> int:
    """9.0.0: skala pixelmått efter fontmetrik (DPI/teckenstorlek, §17/§30).

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

SettingChangedCallback = Callable[[str, Any], None]
ActionCallback = Callable[[], None]


class SettingsScrollTab(QScrollArea):
    """Responsiv, mus-hjulsvänlig container för en Settings-sektion."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._content = QWidget(self)
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(16, 16, 16, 16)
        self._layout.setSpacing(12)
        self._content.installEventFilter(self)
        self.setWidget(self._content)

    @property
    def content(self) -> QWidget:
        return self._content

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._layout

    def add_section(self, title: str, description: str = "") -> QGroupBox:
        box = QGroupBox(title, self._content)
        layout = QVBoxLayout(box)
        layout.setSpacing(8)
        if description:
            label = QLabel(description, box)
            label.setWordWrap(True)
            description_font = label.font()
            description_font.setPointSize(max(8, description_font.pointSize() - 1))
            label.setFont(description_font)
            layout.addWidget(label)
        self._layout.addWidget(box)
        return box

    def add_stretch(self) -> None:
        self._layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

    def eventFilter(self, watched: Any, event: QEvent) -> bool:
        if watched is self._content and event.type() == QEvent.Type.Wheel:
            bar = self.verticalScrollBar()
            if bar.maximum() > bar.minimum():
                steps = event.angleDelta().y() // 120
                if steps:
                    bar.setValue(bar.value() - steps * max(1, bar.singleStep()) * 3)
                event.accept()
                return True
        return super().eventFilter(watched, event)


class SettingsShell(QWidget):
    """Skal för Settings UI: toolbar, intern flikvy och statusrad."""

    export_requested = Signal()
    import_requested = Signal()
    search_changed = Signal(str)
    clear_search_requested = Signal()
    tab_changed = Signal(str)

    def __init__(self, copyright_text: str = COPYRIGHT, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsTab")
        self._tab_indices: dict[str, int] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        outer.addWidget(self._build_toolbar())

        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)
        self._tabs.setUsesScrollButtons(True)
        self._tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        outer.addWidget(self._tabs, stretch=1)
        outer.addWidget(self._build_statusbar(copyright_text))

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    @property
    def search_text(self) -> str:
        return self._search.text()

    def add_scroll_tab(self, tab_info: Any) -> SettingsScrollTab:
        tab = SettingsScrollTab(self._tabs)
        tab_id = str(getattr(tab_info, "id", ""))
        label = str(getattr(tab_info, "display_text", getattr(tab_info, "name", tab_id)))
        index = self._tabs.addTab(tab, label)
        self._tab_indices[tab_id] = index
        return tab

    def show_tab(self, tab_id: str) -> bool:
        index = self._tab_indices.get(tab_id)
        if index is None:
            return False
        self._tabs.setCurrentIndex(index)
        return True

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_restart_required(self, required: bool) -> None:
        self._restart.setVisible(required)

    def _build_toolbar(self) -> QWidget:
        frame = QFrame(self)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel("⚙️ Inställningar", frame))
        self._search = QLineEdit(frame)
        self._search.setPlaceholderText("Sök inställningar…")
        self._search.setMaximumWidth(_sp(260))
        self._search.setMinimumHeight(_sp(30))
        self._search.textChanged.connect(self.search_changed)
        layout.addWidget(self._search)
        clear = QPushButton("✕", frame)
        clear.setFixedSize(QSize(_sp(28), _sp(30)))
        clear.setToolTip("Rensa sökning")
        clear.clicked.connect(self._clear_search)
        layout.addWidget(clear)
        layout.addSpacing(10)
        export_button = make_action_button("📤 Exportera", self.export_requested.emit, parent=frame)
        export_button.setToolTip("Exportera inställningar till JSON")
        layout.addWidget(export_button)
        import_button = make_action_button("📥 Importera", self.import_requested.emit, parent=frame)
        import_button.setToolTip("Importera inställningar från JSON")
        layout.addWidget(import_button)
        layout.addStretch(1)
        return frame

    def _build_statusbar(self, copyright_text: str) -> QWidget:
        frame = QFrame(self)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(4, 0, 4, 0)
        self._status = QLabel("Redo", frame)
        layout.addWidget(self._status, stretch=1)
        self._restart = QLabel("⚠️ Omstart krävs för att ändringen ska träda i kraft", frame)
        self._restart.setVisible(False)
        layout.addWidget(self._restart)
        copyright_label = QLabel(copyright_text, frame)
        copyright_font = copyright_label.font()
        copyright_font.setPointSize(max(7, copyright_font.pointSize() - 2))
        copyright_label.setFont(copyright_font)
        layout.addWidget(copyright_label)
        return frame

    def _clear_search(self) -> None:
        self._search.clear()
        self.clear_search_requested.emit()

    def _on_tab_changed(self, index: int) -> None:
        for tab_id, tab_index in self._tab_indices.items():
            if tab_index == index:
                self.tab_changed.emit(tab_id)
                return


class ShortcutEditDialog(QDialog):
    """Dialog för att ändra en tangentbordsgenväg."""

    def __init__(
        self,
        label: str,
        current_key: str,
        default_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Redigera genväg: {label}")
        self.setMinimumWidth(_sp(390))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(QLabel(f"Åtgärd: {label}", self))
        layout.addWidget(QLabel(f"Standard: {default_key}", self))
        self._entry = QLineEdit(current_key, self)
        self._entry.setMinimumHeight(_sp(34))
        mono = QFont("Consolas" if __import__("sys").platform == "win32" else "Monospace", 11)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._entry.setFont(mono)
        self._entry.setPlaceholderText("Exempel: Ctrl+G eller F5")
        layout.addWidget(self._entry)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._entry.setFocus()
        self._entry.selectAll()

    @property
    def shortcut(self) -> str:
        return self._entry.text().strip()

    @property
    def key_sequence(self) -> QKeySequence:
        return QKeySequence(self.shortcut)


class ProfileDialog(QDialog):
    """Dialog för att skapa en namngiven inställningsprofil."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Skapa profil")
        self.setMinimumWidth(_sp(420))
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        layout.addWidget(QLabel("Spara nuvarande inställningar som en profil.", self))
        form = QFormLayout()
        self._name = QLineEdit(self)
        self._name.setPlaceholderText("Profilnamn")
        self._name.setMinimumHeight(_sp(30))
        form.addRow("Namn:", self._name)
        self._description = QLineEdit(self)
        self._description.setPlaceholderText("Valfri beskrivning")
        self._description.setMinimumHeight(_sp(30))
        form.addRow("Beskrivning:", self._description)
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._name.setFocus()

    @property
    def profile_name(self) -> str:
        return self._name.text().strip()

    @property
    def description(self) -> str:
        return self._description.text().strip()


def make_separator(parent: QWidget | None = None) -> QFrame:
    separator = QFrame(parent)
    separator.setFrameShape(QFrame.Shape.HLine)
    separator.setFrameShadow(QFrame.Shadow.Sunken)
    return separator


def make_label_row(label_text: str, widget: QWidget, parent: QWidget | None = None) -> QWidget:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel(label_text, row)
    label.setMinimumWidth(_sp(200))
    layout.addWidget(label)
    layout.addWidget(widget, stretch=1)
    return row


def make_button_row(*buttons: QPushButton, parent: QWidget | None = None) -> QWidget:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    for button in buttons:
        layout.addWidget(button)
    layout.addStretch(1)
    return row


def make_action_button(text: str, callback: ActionCallback, parent: QWidget | None = None) -> QPushButton:
    button = QPushButton(text, parent)
    button.setMinimumHeight(_sp(32))
    font = button.font()
    font.setBold(True)
    button.setFont(font)
    button.clicked.connect(callback)
    return button


def add_setting_control(
    layout: QVBoxLayout,
    definition: Any,
    value: Any,
    on_changed: SettingChangedCallback,
    controls: dict[str, Any],
) -> QWidget:
    """Skapa ett generiskt settings-kontrollobjekt från SettingDefinition."""
    key = str(getattr(definition, "key", ""))
    setting_type = str(getattr(definition, "type", "text"))
    label = str(getattr(definition, "label", key))
    icon = str(getattr(definition, "icon", ""))
    tooltip = str(getattr(definition, "tooltip", ""))
    prefix = f"{icon} " if icon else ""

    if setting_type == "bool":
        control = QCheckBox(f"{prefix}{label}")
        control.setChecked(bool(value))
        control.stateChanged.connect(lambda state, setting_key=key: on_changed(setting_key, bool(state)))
        if tooltip:
            control.setToolTip(tooltip)
        layout.addWidget(control)
    elif setting_type == "choice":
        control = QComboBox()
        control.setMinimumHeight(_sp(30))
        choices = list(getattr(definition, "choices", []) or [])
        choice_label = getattr(definition, "get_choice_label", lambda item: str(item))
        for choice in choices:
            control.addItem(str(choice_label(choice)), choice)
        selected = next((index for index in range(control.count()) if control.itemData(index) == value), 0)
        control.setCurrentIndex(selected)
        control.currentIndexChanged.connect(
            lambda index, setting_key=key, combo=control: on_changed(setting_key, combo.itemData(index))
        )
        if tooltip:
            control.setToolTip(tooltip)
        layout.addWidget(make_label_row(f"{prefix}{label}:", control))
    elif setting_type == "int":
        control = QSpinBox()
        control.setMinimumHeight(_sp(30))
        minimum = getattr(definition, "min_value", None)
        maximum = getattr(definition, "max_value", None)
        control.setRange(int(minimum) if minimum is not None else 0, int(maximum) if maximum is not None else 999_999)
        control.setValue(int(value) if str(value).lstrip("-").isdigit() else int(getattr(definition, "default", 0) or 0))
        control.valueChanged.connect(lambda number, setting_key=key: on_changed(setting_key, number))
        if tooltip:
            control.setToolTip(tooltip)
        layout.addWidget(make_label_row(f"{prefix}{label}:", control))
    else:
        control = QLineEdit(str(value or ""))
        control.setMinimumHeight(_sp(30))
        control.editingFinished.connect(lambda setting_key=key, field=control: on_changed(setting_key, field.text()))
        if tooltip:
            control.setToolTip(tooltip)
        layout.addWidget(make_label_row(f"{prefix}{label}:", control))

    controls[key] = control
    return control
