from __future__ import annotations

from pathlib import Path

import yaml

from rearview.config import get_config, reload_config, AppConfig
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QCheckBox,
    QSpinBox,
    QComboBox,
    QLineEdit,
    QSlider,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

_BG = "#1e1e1e"
_SECTION_TEXT = "#666666"
_SECTION_BORDER = "#6366f1"
_BTN_BG = "#2a2a2a"
_BTN_HOVER = "#6366f1"
_INPUT_BG = "#242424"
_INPUT_BORDER = "#3a3a3a"
_TEXT = "#cccccc"

ROOT = Path(__file__).parent.parent.parent


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setFont(QFont("sans-serif", 10))
    lbl.setStyleSheet(
        f"color: {_SECTION_TEXT};"
        f"border-left: 3px solid {_SECTION_BORDER};"
        "padding-left: 6px;"
        "margin-bottom: 4px;"
    )
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #2a2a2a; background-color: #2a2a2a;")
    line.setFixedHeight(1)
    return line


_INPUT_STYLE = f"""
    background-color: {_INPUT_BG};
    color: {_TEXT};
    border: 1px solid {_INPUT_BORDER};
    border-radius: 3px;
    padding: 2px 6px;
    font-size: 12px;
"""

_COMBO_STYLE = f"""
    QComboBox {{
        background-color: {_INPUT_BG};
        color: {_TEXT};
        border: 1px solid {_INPUT_BORDER};
        border-radius: 3px;
        padding: 2px 6px;
        font-size: 12px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {_INPUT_BG};
        color: {_TEXT};
        selection-background-color: #6366f1;
    }}
"""

_SPINBOX_STYLE = f"""
    QSpinBox {{
        background-color: {_INPUT_BG};
        color: {_TEXT};
        border: 1px solid {_INPUT_BORDER};
        border-radius: 3px;
        padding: 2px 6px;
        font-size: 12px;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        background-color: #2a2a2a;
    }}
"""


def _form_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
    return lbl


class SettingsPanel(QWidget):
    settings_saved = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {_BG}; color: #ffffff;")
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        root.addWidget(self._build_workflow_section())
        root.addWidget(_divider())
        root.addWidget(self._build_teleprompter_section())
        root.addWidget(_divider())
        root.addWidget(self._build_toast_section())
        root.addWidget(_divider())
        root.addWidget(self._build_browser_section())
        root.addStretch(1)

        # Save button + status
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        self._save_btn = QPushButton("Save Settings")
        self._save_btn.setFixedHeight(32)
        self._save_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {_BTN_BG};
                color: #ffffff;
                border: none;
                border-radius: 4px;
                padding: 0 16px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {_BTN_HOVER};
            }}
            QPushButton:pressed {{
                background-color: #4f46e5;
            }}
            """
        )
        self._save_btn.clicked.connect(self._on_save)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: #22c55e; font-size: 12px;")

        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._status_label)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

    def _build_workflow_section(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background-color: {_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_section_label("Workflow"))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._auto_advance = QCheckBox()
        self._auto_advance.setStyleSheet("QCheckBox::indicator { width: 16px; height: 16px; }")
        form.addRow(_form_label("Auto-advance after disposition:"), self._auto_advance)

        self._auto_hide = QSpinBox()
        self._auto_hide.setRange(0, 30)
        self._auto_hide.setSuffix("s")
        self._auto_hide.setSpecialValueText("never")
        self._auto_hide.setFixedWidth(80)
        self._auto_hide.setStyleSheet(_SPINBOX_STYLE)
        form.addRow(_form_label("Auto-hide toast after (seconds):"), self._auto_hide)

        layout.addLayout(form)
        return container

    def _build_teleprompter_section(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background-color: {_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_section_label("Teleprompter"))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._stt_model = QComboBox()
        self._stt_model.addItems(["tiny", "base", "small", "medium"])
        self._stt_model.setFixedWidth(120)
        self._stt_model.setStyleSheet(_COMBO_STYLE)
        form.addRow(_form_label("STT Model:"), self._stt_model)

        self._agent_name = QLineEdit()
        self._agent_name.setFixedWidth(200)
        self._agent_name.setStyleSheet(_INPUT_STYLE)
        form.addRow(_form_label("Agent name:"), self._agent_name)

        self._company = QLineEdit()
        self._company.setFixedWidth(200)
        self._company.setStyleSheet(_INPUT_STYLE)
        form.addRow(_form_label("Company:"), self._company)

        layout.addLayout(form)
        return container

    def _build_toast_section(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background-color: {_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_section_label("Toast"))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        opacity_row = QHBoxLayout()
        opacity_row.setContentsMargins(0, 0, 0, 0)
        opacity_row.setSpacing(8)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(50, 100)
        self._opacity_slider.setFixedWidth(140)
        self._opacity_slider.setStyleSheet(
            """
            QSlider::groove:horizontal { height: 4px; background: #3a3a3a; border-radius: 2px; }
            QSlider::handle:horizontal { background: #6366f1; width: 12px; height: 12px;
                margin: -4px 0; border-radius: 6px; }
            QSlider::sub-page:horizontal { background: #6366f1; border-radius: 2px; }
            """
        )
        self._opacity_pct_label = QLabel("80%")
        self._opacity_pct_label.setStyleSheet(f"color: {_TEXT}; font-size: 12px; min-width: 36px;")
        self._opacity_slider.valueChanged.connect(
            lambda v: self._opacity_pct_label.setText(f"{v}%")
        )
        opacity_row.addWidget(self._opacity_slider)
        opacity_row.addWidget(self._opacity_pct_label)
        opacity_row.addStretch(1)

        opacity_widget = QWidget()
        opacity_widget.setLayout(opacity_row)
        opacity_widget.setStyleSheet(f"background-color: {_BG};")
        form.addRow(_form_label("Opacity:"), opacity_widget)

        self._toast_theme = QComboBox()
        self._toast_theme.addItems(["dark", "light"])
        self._toast_theme.setFixedWidth(100)
        self._toast_theme.setStyleSheet(_COMBO_STYLE)
        form.addRow(_form_label("Theme:"), self._toast_theme)

        layout.addLayout(form)
        return container

    def _build_browser_section(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background-color: {_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(_section_label("Browser"))

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self._debug_port = QSpinBox()
        self._debug_port.setRange(1024, 65535)
        self._debug_port.setFixedWidth(90)
        self._debug_port.setStyleSheet(_SPINBOX_STYLE)
        form.addRow(_form_label("Debug port:"), self._debug_port)

        self._executable = QLineEdit()
        self._executable.setFixedWidth(200)
        self._executable.setStyleSheet(_INPUT_STYLE)
        form.addRow(_form_label("Executable:"), self._executable)

        layout.addLayout(form)
        return container

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load_values(self) -> None:
        cfg = get_config()

        self._auto_advance.setChecked(cfg.workflow.auto_advance)
        self._auto_hide.setValue(cfg.workflow.auto_hide_after)

        stt = cfg.teleprompter.stt_model
        idx = self._stt_model.findText(stt)
        if idx >= 0:
            self._stt_model.setCurrentIndex(idx)
        self._agent_name.setText(cfg.teleprompter.agent_name)
        self._company.setText(cfg.teleprompter.company)

        opacity_pct = int(round(cfg.toast.opacity * 100))
        opacity_pct = max(50, min(100, opacity_pct))
        self._opacity_slider.setValue(opacity_pct)

        theme_idx = self._toast_theme.findText(cfg.toast.theme)
        if theme_idx >= 0:
            self._toast_theme.setCurrentIndex(theme_idx)

        self._debug_port.setValue(cfg.browser.debug_port)
        self._executable.setText(cfg.browser.executable)

    def _on_save(self) -> None:
        settings_path = ROOT / "config" / "settings.yaml"

        try:
            with open(settings_path) as f:
                raw = yaml.safe_load(f) or {}
        except OSError:
            raw = {}

        # Workflow
        raw.setdefault("workflow", {})
        raw["workflow"]["auto_advance"] = self._auto_advance.isChecked()
        raw["workflow"]["auto_hide_after"] = self._auto_hide.value()

        # Teleprompter
        raw.setdefault("teleprompter", {})
        raw["teleprompter"]["stt_model"] = self._stt_model.currentText()
        raw["teleprompter"]["agent_name"] = self._agent_name.text()
        raw["teleprompter"]["company"] = self._company.text()

        # Toast
        raw.setdefault("toast", {})
        raw["toast"]["opacity"] = round(self._opacity_slider.value() / 100, 2)
        raw["toast"]["theme"] = self._toast_theme.currentText()

        # Browser
        raw.setdefault("browser", {})
        raw["browser"]["debug_port"] = self._debug_port.value()
        raw["browser"]["executable"] = self._executable.text()

        with open(settings_path, "w") as f:
            yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)

        reload_config()
        self.settings_saved.emit()

        self._status_label.setText("Saved.")
        QTimer.singleShot(2000, lambda: self._status_label.setText(""))
