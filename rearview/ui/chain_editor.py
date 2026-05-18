from __future__ import annotations

import threading
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from rearview.click_store import ClickChain, ClickDot, ChainStep

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_BG = "#1a1a1a"
_ACCENT = "#6366f1"
_TEXT = "#e5e7eb"
_BORDER = "#2a2a2a"
_BTN_NEUTRAL = "#2a2a2a"
_BTN_NEUTRAL_HOVER = "#3a3a3a"
_BTN_ACCENT_HOVER = "#4f52d4"
_SELECTED_BG = "#2e2e5e"

_LIST_STYLE = f"""
    QListWidget {{
        background: {_BG};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        outline: none;
    }}
    QListWidget::item {{
        padding: 8px 12px;
        border-bottom: 1px solid {_BORDER};
    }}
    QListWidget::item:selected {{
        background: {_SELECTED_BG};
        color: white;
    }}
    QScrollBar:vertical {{
        background: {_BG};
        width: 8px;
    }}
    QScrollBar::handle:vertical {{
        background: {_BORDER};
        border-radius: 4px;
    }}
"""

_INPUT_STYLE = f"""
    QLineEdit, QDoubleSpinBox, QComboBox {{
        background: {_BORDER};
        color: {_TEXT};
        border: 1px solid #3a3a3a;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 13px;
    }}
    QLineEdit:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {_ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background: #242424;
        color: {_TEXT};
        selection-background-color: {_SELECTED_BG};
    }}
"""


def _btn(text: str, accent: bool = False, height: int = 28) -> QPushButton:
    bg = _ACCENT if accent else _BTN_NEUTRAL
    hover = _BTN_ACCENT_HOVER if accent else _BTN_NEUTRAL_HOVER
    b = QPushButton(text)
    b.setFixedHeight(height)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(f"""
        QPushButton {{
            background: {bg};
            color: {_TEXT};
            border: 1px solid {_BORDER};
            border-radius: 4px;
            padding: 0 10px;
            font-size: 13px;
        }}
        QPushButton:hover {{
            background: {hover};
        }}
        QPushButton:disabled {{
            opacity: 0.4;
        }}
    """)
    return b


# ---------------------------------------------------------------------------
# Hotkey capture worker
# ---------------------------------------------------------------------------

class _HotkeyCapture(QThread):
    """Captures one key combo via pynput and emits it as a formatted string."""

    captured = pyqtSignal(str)

    # pynput Key → pynput string fragment
    _MOD_ORDER = ["ctrl", "alt", "shift", "cmd"]

    def run(self) -> None:
        try:
            from pynput import keyboard as _kb
        except ImportError:
            self.captured.emit("")
            return

        held: set[str] = set()
        done = threading.Event()
        result: list[str] = []

        def _fmt(mods: list[str], key_name: str) -> str:
            parts = [f"<{m}>" for m in self._MOD_ORDER if m in mods]
            if key_name and key_name not in self._MOD_ORDER:
                parts.append(f"<{key_name}>" if len(key_name) > 1 else key_name)
            return "".join(parts)

        def _key_name(key) -> str:
            try:
                # Regular printable character
                return key.char or ""
            except AttributeError:
                name = str(key).replace("Key.", "")
                # Normalise common modifier names
                for alias, canonical in (
                    ("ctrl_l", "ctrl"), ("ctrl_r", "ctrl"),
                    ("alt_l", "alt"), ("alt_r", "alt"),
                    ("shift", "shift"), ("shift_l", "shift"), ("shift_r", "shift"),
                    ("cmd", "cmd"), ("cmd_l", "cmd"), ("cmd_r", "cmd"),
                    ("super_l", "cmd"), ("super_r", "cmd"),
                ):
                    if name == alias:
                        return canonical
                return name

        _MODIFIERS = {"ctrl", "alt", "shift", "cmd"}

        def on_press(key):
            held.add(_key_name(key))

        def on_release(key):
            name = _key_name(key)
            mods = [m for m in held if m in _MODIFIERS]
            non_mods = [k for k in held if k not in _MODIFIERS]

            # Emit when all modifier keys are being held and at least one
            # non-modifier key was pressed, or when any key is released
            # after something was held.
            if not done.is_set() and held:
                key_part = non_mods[0] if non_mods else (mods[0] if mods else name)
                mods_only = [m for m in mods if m != key_part]
                result.append(_fmt(mods_only, key_part))
                done.set()

            held.discard(name)

            if done.is_set():
                return False  # stop listener

        with _kb.Listener(on_press=on_press, on_release=on_release) as lst:
            done.wait(timeout=10)
            lst.stop()

        self.captured.emit(result[0] if result else "")


# ---------------------------------------------------------------------------
# "Add Step" dialog
# ---------------------------------------------------------------------------

class _AddStepDialog(QDialog):
    def __init__(self, dots: list[ClickDot], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Step")
        self.setModal(True)
        self.setStyleSheet(f"background: #242424; color: {_TEXT}; {_INPUT_STYLE}")
        self.setMinimumWidth(300)

        self._dots = dots

        form = QFormLayout(self)
        form.setContentsMargins(16, 16, 16, 16)
        form.setSpacing(12)

        self._dot_combo = QComboBox()
        self._dot_combo.setStyleSheet(_INPUT_STYLE)
        for dot in dots:
            self._dot_combo.addItem(dot.label, dot.id)
        form.addRow("Click target:", self._dot_combo)

        self._delay_spin = QDoubleSpinBox()
        self._delay_spin.setStyleSheet(_INPUT_STYLE)
        self._delay_spin.setRange(0.0, 60.0)
        self._delay_spin.setSingleStep(0.1)
        self._delay_spin.setDecimals(2)
        self._delay_spin.setSuffix(" s")
        form.addRow("Delay before:", self._delay_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.setStyleSheet(f"""
            QPushButton {{
                background: {_BTN_NEUTRAL};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                padding: 4px 16px;
                min-width: 60px;
            }}
            QPushButton:hover {{ background: {_BTN_NEUTRAL_HOVER}; }}
        """)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def result_step(self) -> ChainStep | None:
        if self._dot_combo.count() == 0:
            return None
        dot_id = self._dot_combo.currentData()
        delay = self._delay_spin.value()
        return ChainStep(dot_id=dot_id, delay_before=delay)


# ---------------------------------------------------------------------------
# ChainEditor
# ---------------------------------------------------------------------------

class ChainEditor(QWidget):
    """Two-column widget for creating and editing click chains."""

    chain_saved = pyqtSignal(ClickChain)
    chain_deleted = pyqtSignal(str)
    chain_run = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chains: list[ClickChain] = []
        self._dots: list[ClickDot] = []
        self._capture_worker: _HotkeyCapture | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, chains: list[ClickChain], dots: list[ClickDot]) -> None:
        self._chains = list(chains)
        self._dots = list(dots)
        self._populate_chain_list()
        self._set_right_enabled(False)

    def current_chain(self) -> ClickChain | None:
        row = self._chain_list.currentRow()
        if row < 0 or row >= len(self._chains):
            return None
        return self._chains[row]

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background: {_BG}; color: {_TEXT};")

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {_BORDER}; width: 1px; }}")
        splitter.setHandleWidth(1)

        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setSizes([180, 500])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        root.addWidget(splitter)

    def _build_left(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(180)
        w.setStyleSheet(f"background: {_BG}; border-right: 1px solid {_BORDER};")

        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        header = QLabel("CHAINS")
        header.setStyleSheet(
            f"color: #6b7280; font-size: 11px; font-weight: 600; letter-spacing: 1px;"
        )
        layout.addWidget(header)

        self._chain_list = QListWidget()
        self._chain_list.setStyleSheet(_LIST_STYLE)
        self._chain_list.currentRowChanged.connect(self._on_chain_selected)
        layout.addWidget(self._chain_list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self._new_btn = _btn("+ New")
        self._new_btn.clicked.connect(self._on_new_chain)
        btn_row.addWidget(self._new_btn)

        self._del_chain_btn = _btn("Delete")
        self._del_chain_btn.clicked.connect(self._on_delete_chain)
        btn_row.addWidget(self._del_chain_btn)

        layout.addLayout(btn_row)
        return w

    def _build_right(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: {_BG};")

        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(8)

        header = QLabel("STEPS")
        header.setStyleSheet(
            f"color: #6b7280; font-size: 11px; font-weight: 600; letter-spacing: 1px;"
        )
        layout.addWidget(header)

        # Step list + side controls
        step_row = QHBoxLayout()
        step_row.setSpacing(6)

        self._step_list = QListWidget()
        self._step_list.setStyleSheet(_LIST_STYLE)
        step_row.addWidget(self._step_list)

        side_btns = QVBoxLayout()
        side_btns.setSpacing(4)
        side_btns.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._up_btn = _btn("↑")
        self._up_btn.setFixedWidth(32)
        self._up_btn.clicked.connect(self._on_step_up)

        self._down_btn = _btn("↓")
        self._down_btn.setFixedWidth(32)
        self._down_btn.clicked.connect(self._on_step_down)

        self._add_step_btn = _btn("Add")
        self._add_step_btn.setFixedWidth(52)
        self._add_step_btn.clicked.connect(self._on_add_step)

        self._remove_step_btn = _btn("×")
        self._remove_step_btn.setFixedWidth(32)
        self._remove_step_btn.clicked.connect(self._on_remove_step)

        for b in (self._up_btn, self._down_btn, self._add_step_btn, self._remove_step_btn):
            side_btns.addWidget(b)

        step_row.addLayout(side_btns)
        layout.addLayout(step_row)

        # Fields
        form = QFormLayout()
        form.setSpacing(8)
        form.setContentsMargins(0, 4, 0, 4)

        self._name_edit = QLineEdit()
        self._name_edit.setStyleSheet(_INPUT_STYLE)
        self._name_edit.setPlaceholderText("Chain name")
        self._name_edit.textEdited.connect(self._on_name_edited)
        form.addRow("Name:", self._name_edit)

        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(6)
        self._hotkey_edit = QLineEdit()
        self._hotkey_edit.setStyleSheet(_INPUT_STYLE)
        self._hotkey_edit.setPlaceholderText("<ctrl><alt>1")
        hotkey_row.addWidget(self._hotkey_edit)

        self._rec_btn = _btn("Rec")
        self._rec_btn.setFixedWidth(44)
        self._rec_btn.setCheckable(False)
        self._rec_btn.clicked.connect(self._on_rec_clicked)
        hotkey_row.addWidget(self._rec_btn)

        form.addRow("Hotkey:", hotkey_row)
        layout.addLayout(form)

        # Action buttons
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addStretch()

        self._run_btn = _btn("▶ Run", accent=False)
        self._run_btn.setFixedWidth(80)
        self._run_btn.clicked.connect(self._on_run)

        self._save_btn = _btn("Save", accent=True)
        self._save_btn.setFixedWidth(80)
        self._save_btn.clicked.connect(self._on_save)

        action_row.addWidget(self._run_btn)
        action_row.addWidget(self._save_btn)
        layout.addLayout(action_row)

        self._right_widgets = [
            self._step_list, self._up_btn, self._down_btn,
            self._add_step_btn, self._remove_step_btn,
            self._name_edit, self._hotkey_edit, self._rec_btn,
            self._run_btn, self._save_btn,
        ]

        return w

    # ------------------------------------------------------------------
    # Chain list helpers
    # ------------------------------------------------------------------

    def _populate_chain_list(self) -> None:
        self._chain_list.blockSignals(True)
        self._chain_list.clear()
        for chain in self._chains:
            self._chain_list.addItem(chain.name)
        self._chain_list.blockSignals(False)

    def _set_right_enabled(self, enabled: bool) -> None:
        for w in self._right_widgets:
            w.setEnabled(enabled)

    def _on_chain_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._chains):
            self._set_right_enabled(False)
            return
        self._set_right_enabled(True)
        chain = self._chains[row]
        self._name_edit.setText(chain.name)
        self._hotkey_edit.setText(chain.hotkey)
        self._reload_step_list(chain)

    def _reload_step_list(self, chain: ClickChain) -> None:
        dot_map = {d.id: d for d in self._dots}
        self._step_list.clear()
        for step in chain.steps:
            dot = dot_map.get(step.dot_id)
            label = dot.label if dot else f"[{step.dot_id}]"
            item = QListWidgetItem(f"{label} — wait {step.delay_before:.2f}s")
            self._step_list.addItem(item)

    # ------------------------------------------------------------------
    # Chain actions
    # ------------------------------------------------------------------

    def _on_new_chain(self) -> None:
        name, ok = QInputDialog.getText(self, "New Chain", "Chain name:")
        if not ok or not name.strip():
            return
        chain = ClickChain.new(name.strip(), "")
        self._chains.append(chain)
        self._chain_list.addItem(chain.name)
        self._chain_list.setCurrentRow(len(self._chains) - 1)

    def _on_delete_chain(self) -> None:
        row = self._chain_list.currentRow()
        if row < 0 or row >= len(self._chains):
            return
        chain = self._chains[row]
        reply = QMessageBox.question(
            self,
            "Delete chain",
            f"Delete chain <b>{chain.name}</b>? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        chain_id = chain.id
        self._chains.pop(row)
        self._chain_list.takeItem(row)
        self._set_right_enabled(False)
        self.chain_deleted.emit(chain_id)

    # ------------------------------------------------------------------
    # Step actions
    # ------------------------------------------------------------------

    def _on_add_step(self) -> None:
        chain = self.current_chain()
        if chain is None:
            return
        if not self._dots:
            QMessageBox.information(self, "No dots", "No click targets available for this region.")
            return
        dlg = _AddStepDialog(self._dots, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        step = dlg.result_step()
        if step is None:
            return
        chain.steps.append(step)
        self._reload_step_list(chain)
        self._step_list.setCurrentRow(len(chain.steps) - 1)

    def _on_remove_step(self) -> None:
        chain = self.current_chain()
        if chain is None:
            return
        row = self._step_list.currentRow()
        if row < 0 or row >= len(chain.steps):
            return
        chain.steps.pop(row)
        self._reload_step_list(chain)
        new_row = min(row, len(chain.steps) - 1)
        if new_row >= 0:
            self._step_list.setCurrentRow(new_row)

    def _on_step_up(self) -> None:
        chain = self.current_chain()
        if chain is None:
            return
        row = self._step_list.currentRow()
        if row <= 0:
            return
        chain.steps[row - 1], chain.steps[row] = chain.steps[row], chain.steps[row - 1]
        self._reload_step_list(chain)
        self._step_list.setCurrentRow(row - 1)

    def _on_step_down(self) -> None:
        chain = self.current_chain()
        if chain is None:
            return
        row = self._step_list.currentRow()
        if row < 0 or row >= len(chain.steps) - 1:
            return
        chain.steps[row], chain.steps[row + 1] = chain.steps[row + 1], chain.steps[row]
        self._reload_step_list(chain)
        self._step_list.setCurrentRow(row + 1)

    # ------------------------------------------------------------------
    # Field bindings
    # ------------------------------------------------------------------

    def _on_name_edited(self, text: str) -> None:
        chain = self.current_chain()
        if chain is None:
            return
        chain.name = text
        row = self._chain_list.currentRow()
        item = self._chain_list.item(row)
        if item is not None:
            item.setText(text)

    # ------------------------------------------------------------------
    # Hotkey capture
    # ------------------------------------------------------------------

    def _on_rec_clicked(self) -> None:
        if self._capture_worker and self._capture_worker.isRunning():
            return
        self._rec_btn.setText("…")
        self._rec_btn.setEnabled(False)
        self._hotkey_edit.setPlaceholderText("press keys…")

        self._capture_worker = _HotkeyCapture(parent=self)
        self._capture_worker.captured.connect(self._on_hotkey_captured)
        self._capture_worker.start()

    def _on_hotkey_captured(self, combo: str) -> None:
        self._rec_btn.setText("Rec")
        self._rec_btn.setEnabled(True)
        self._hotkey_edit.setPlaceholderText("<ctrl><alt>1")
        if combo:
            self._hotkey_edit.setText(combo)
            chain = self.current_chain()
            if chain is not None:
                chain.hotkey = combo

    # ------------------------------------------------------------------
    # Run / Save
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        chain = self.current_chain()
        if chain is not None:
            self.chain_run.emit(chain.id)

    def _on_save(self) -> None:
        chain = self.current_chain()
        if chain is None:
            return
        chain.hotkey = self._hotkey_edit.text()
        chain.name = self._name_edit.text() or chain.name
        self.chain_saved.emit(chain)
