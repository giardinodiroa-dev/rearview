from __future__ import annotations

import re
import shutil
import subprocess
import os
from pathlib import Path

from rearview.config import get_config, reload_config
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QFileDialog,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

_BG = "#1e1e1e"
_BG_EDITOR = "#111111"
_SECTION_TEXT = "#666666"
_SECTION_BORDER = "#6366f1"
_BTN_BG = "#2a2a2a"
_BTN_HOVER = "#6366f1"
_VAR_DIM = "#555555"
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


def _make_button(text: str) -> QPushButton:
    btn = QPushButton(text)
    btn.setFixedHeight(26)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background-color: {_BTN_BG};
            color: #ffffff;
            border: none;
            border-radius: 4px;
            padding: 0 10px;
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
    return btn


class ScriptPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {_BG}; color: #ffffff;")
        self._current_path: Path | None = None
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Header row: section label + buttons
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        header_row.addWidget(_section_label("Script"))
        header_row.addStretch(1)

        self._load_btn = _make_button("Load")
        self._edit_btn = _make_button("Edit")
        self._load_btn.clicked.connect(self._on_load)
        self._edit_btn.clicked.connect(self._on_edit)
        header_row.addWidget(self._load_btn)
        header_row.addWidget(self._edit_btn)
        root.addLayout(header_row)

        # Current path label
        self._path_label = QLabel("Current: —")
        self._path_label.setStyleSheet(f"color: {_SECTION_TEXT}; font-size: 11px;")
        root.addWidget(self._path_label)

        # Script text display
        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        mono = QFont("monospace", 12)
        self._text_edit.setFont(mono)
        self._text_edit.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {_BG_EDITOR};
                color: {_TEXT};
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                padding: 8px;
            }}
            """
        )
        root.addWidget(self._text_edit, 1)

        # Variables row
        self._vars_label = QLabel("Variables: —")
        self._vars_label.setWordWrap(True)
        self._vars_label.setStyleSheet(f"color: {_VAR_DIM}; font-size: 11px;")
        root.addWidget(self._vars_label)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_load(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Load Script",
            str(ROOT),
            "Script files (*.md *.txt);;All files (*)",
        )
        if not path_str:
            return

        src = Path(path_str)
        scripts_dir = ROOT / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        dest = scripts_dir / src.name

        if src != dest:
            shutil.copy2(src, dest)

        # Update settings.yaml — round-trip preserving existing keys
        settings_path = ROOT / "config" / "settings.yaml"
        try:
            import yaml  # local import to avoid top-level optional dep

            with open(settings_path) as f:
                raw = yaml.safe_load(f) or {}

            if "teleprompter" not in raw:
                raw["teleprompter"] = {}
            raw["teleprompter"]["script"] = str(dest.relative_to(ROOT))

            with open(settings_path, "w") as f:
                yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)

            reload_config()
        except Exception:
            pass  # settings write failure is non-fatal

        self.load_script_file(dest)

    def _on_edit(self) -> None:
        if self._current_path is None:
            return
        editor = os.environ.get("EDITOR", "xdg-open")
        subprocess.Popen([editor, str(self._current_path)])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_script_file(self, path: Path) -> None:
        self._current_path = path
        rel = path.relative_to(ROOT) if path.is_absolute() else path
        self._path_label.setText(f"Current: {rel}")

        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            content = f"(could not read {path})"

        self._text_edit.setPlainText(content)

        variables = sorted(set(re.findall(r"\{(\w+)\}", content)))
        if variables:
            tags = "  ".join(f"{{{v}}}" for v in variables)
            self._vars_label.setText(f"Variables: {tags}")
        else:
            self._vars_label.setText("Variables: (none)")

    def refresh(self) -> None:
        cfg = get_config()
        script_rel = cfg.teleprompter.script
        script_path = ROOT / script_rel
        if script_path.exists():
            self.load_script_file(script_path)
        else:
            self._path_label.setText(f"Current: {script_rel}  (not found)")
            self._text_edit.setPlainText("")
            self._vars_label.setText("Variables: —")
            self._current_path = None
