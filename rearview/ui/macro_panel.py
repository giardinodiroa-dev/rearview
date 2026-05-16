"""MacroPanel — PyQt6 widget for macro management.

Provides recording controls and a table of saved macros with run/edit/delete
actions. Designed to be embedded in a parent window or shown standalone.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QFrame,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont

from rearview.macro_store import list_macros, delete_macro, Macro


# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_BG_DARK = "#1a1a1a"
_BG_PANEL = "#242424"
_BG_ROW_ALT = "#2e2e2e"
_BG_RECORD_IDLE = "#2a2a2a"
_BG_RECORD_ACTIVE = "#ef4444"
_ACCENT_INDIGO = "#6366f1"
_TEXT_PRIMARY = "#e5e7eb"
_TEXT_MUTED = "#6b7280"
_BORDER_COLOR = "#374151"
_ROW_HEIGHT = 36

_COMMON_BTN = """
    QPushButton {{
        background: {bg};
        color: {fg};
        border: 1px solid {border};
        border-radius: 4px;
        padding: 3px 10px;
        font-size: 13px;
    }}
    QPushButton:hover {{
        background: {hover};
    }}
    QPushButton:disabled {{
        opacity: 0.4;
    }}
"""


def _action_button(text: str, bg: str, hover: str, fg: str = _TEXT_PRIMARY) -> QPushButton:
    btn = QPushButton(text)
    btn.setStyleSheet(
        _COMMON_BTN.format(bg=bg, fg=fg, border=_BORDER_COLOR, hover=hover)
    )
    btn.setFixedHeight(26)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


# ---------------------------------------------------------------------------
# Row action widget
# ---------------------------------------------------------------------------

class _ActionWidget(QWidget):
    """Three small buttons packed horizontally: run, edit, delete."""

    run_clicked = pyqtSignal()
    edit_clicked = pyqtSignal()
    delete_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        self._run_btn = _action_button("▶", "#1d4ed8", "#2563eb")
        self._run_btn.setToolTip("Run macro")
        self._run_btn.setFixedWidth(32)

        self._edit_btn = _action_button("✎", "#374151", "#4b5563")
        self._edit_btn.setToolTip("Edit macro")
        self._edit_btn.setFixedWidth(32)

        self._del_btn = _action_button("✕", "#7f1d1d", "#991b1b")
        self._del_btn.setToolTip("Delete macro")
        self._del_btn.setFixedWidth(32)

        layout.addWidget(self._run_btn)
        layout.addWidget(self._edit_btn)
        layout.addWidget(self._del_btn)
        layout.addStretch()

        self._run_btn.clicked.connect(self.run_clicked)
        self._edit_btn.clicked.connect(self.edit_clicked)
        self._del_btn.clicked.connect(self.delete_clicked)

    @property
    def run_btn(self) -> QPushButton:
        return self._run_btn

    def set_running(self, running: bool) -> None:
        """Visually indicate this row's macro is executing."""
        if running:
            self._run_btn.setStyleSheet(
                _COMMON_BTN.format(
                    bg="#ef4444", fg=_TEXT_PRIMARY, border="#b91c1c", hover="#dc2626"
                )
            )
            self._run_btn.setToolTip("Running…")
        else:
            self._run_btn.setStyleSheet(
                _COMMON_BTN.format(
                    bg="#1d4ed8", fg=_TEXT_PRIMARY, border=_BORDER_COLOR, hover="#2563eb"
                )
            )
            self._run_btn.setToolTip("Run macro")


# ---------------------------------------------------------------------------
# MacroPanel
# ---------------------------------------------------------------------------

class MacroPanel(QWidget):
    """Widget that shows recording controls and a list of saved macros."""

    # Public signals
    record_start_requested = pyqtSignal()
    record_stop_requested = pyqtSignal()
    macro_run_requested = pyqtSignal(str)
    macro_edit_requested = pyqtSignal(str)
    macro_deleted = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._recording = False
        self._running_name: str | None = None
        self._pulse_state = False  # for border alternation

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(800)
        self._pulse_timer.timeout.connect(self._pulse_tick)

        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setStyleSheet(f"background: {_BG_DARK}; color: {_TEXT_PRIMARY};")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # ---- Recording section ----------------------------------------
        rec_frame = QFrame()
        rec_frame.setStyleSheet(
            f"background: {_BG_PANEL}; border: 1px solid {_BORDER_COLOR}; border-radius: 6px;"
        )
        rec_layout = QVBoxLayout(rec_frame)
        rec_layout.setContentsMargins(12, 10, 12, 10)
        rec_layout.setSpacing(8)

        section_label = QLabel("RECORDING")
        section_label.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px;"
            " border: none;"
        )
        rec_layout.addWidget(section_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self._record_btn = QPushButton("● Start Recording")
        self._record_btn.setFixedHeight(34)
        self._record_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._record_btn.clicked.connect(self._on_record_clicked)
        self._apply_record_idle_style()

        self._status_label = QLabel("Status: idle")
        self._status_label.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 13px; border: none;"
        )

        btn_row.addWidget(self._record_btn)
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()
        rec_layout.addLayout(btn_row)

        hint = QLabel("Super+R also toggles recording")
        hint.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px; border: none;")
        rec_layout.addWidget(hint)

        root.addWidget(rec_frame)

        # ---- Saved macros section ------------------------------------
        macros_frame = QFrame()
        macros_frame.setStyleSheet(
            f"background: {_BG_PANEL}; border: 1px solid {_BORDER_COLOR}; border-radius: 6px;"
        )
        macros_layout = QVBoxLayout(macros_frame)
        macros_layout.setContentsMargins(12, 10, 12, 10)
        macros_layout.setSpacing(8)

        header_row = QHBoxLayout()
        macros_title = QLabel("SAVED MACROS")
        macros_title.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px;"
            " border: none;"
        )
        header_row.addWidget(macros_title)
        header_row.addStretch()

        refresh_btn = _action_button("Refresh", "#374151", "#4b5563")
        refresh_btn.setFixedWidth(72)
        refresh_btn.clicked.connect(self.refresh)
        header_row.addWidget(refresh_btn)
        macros_layout.addLayout(header_row)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["Name", "Target", "Steps", "Actions"])
        self._table.setStyleSheet(
            f"""
            QTableWidget {{
                background: {_BG_DARK};
                color: {_TEXT_PRIMARY};
                gridline-color: {_BORDER_COLOR};
                border: 1px solid {_BORDER_COLOR};
                border-radius: 4px;
                font-size: 13px;
                selection-background-color: #334155;
            }}
            QHeaderView::section {{
                background: {_BG_PANEL};
                color: {_TEXT_MUTED};
                border: none;
                border-bottom: 1px solid {_BORDER_COLOR};
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 600;
            }}
            QTableWidget::item {{
                padding: 4px 8px;
            }}
            QScrollBar:vertical {{
                background: {_BG_DARK};
                width: 8px;
            }}
            QScrollBar::handle:vertical {{
                background: {_BORDER_COLOR};
                border-radius: 4px;
            }}
            """
        )
        self._table.setAlternatingRowColors(True)
        self._table.setAlternatingRowColors(True)
        palette = self._table.palette()
        palette.setColor(palette.ColorRole.AlternateBase, QColor(_BG_ROW_ALT))
        palette.setColor(palette.ColorRole.Base, QColor(_BG_DARK))
        self._table.setPalette(palette)

        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(3, 116)
        self._table.setShowGrid(True)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setMinimumHeight(120)

        macros_layout.addWidget(self._table)

        # Empty-state label (shown when table has no rows)
        self._empty_label = QLabel("No macros yet — press ● Start Recording")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            f"color: {_TEXT_MUTED}; font-size: 13px; padding: 24px; border: none;"
        )
        self._empty_label.hide()
        macros_layout.addWidget(self._empty_label)

        root.addWidget(macros_frame)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_recording(self, active: bool) -> None:
        """Update button text + status label based on recording state."""
        self._recording = active
        if active:
            self._record_btn.setText("■ Stop Recording")
            self._apply_record_active_style()
            self._status_label.setText("Status: recording…")
            self._pulse_timer.start()
        else:
            self._record_btn.setText("● Start Recording")
            self._apply_record_idle_style()
            self._status_label.setText("Status: idle")
            self._pulse_timer.stop()
            self._pulse_state = False

    def refresh(self) -> None:
        """Reload macro list from disk and repopulate the table."""
        macros: list[Macro] = list_macros()

        self._table.setRowCount(0)

        if not macros:
            self._table.hide()
            self._empty_label.show()
            return

        self._empty_label.hide()
        self._table.show()
        self._table.setRowCount(len(macros))

        for row, macro in enumerate(macros):
            self._table.setRowHeight(row, _ROW_HEIGHT)

            name_item = QTableWidgetItem(macro.name)
            name_item.setData(Qt.ItemDataRole.UserRole, macro.name)
            self._table.setItem(row, 0, name_item)

            target_item = QTableWidgetItem(macro.target_hint or macro.target_type)
            self._table.setItem(row, 1, target_item)

            steps_item = QTableWidgetItem(str(len(macro.steps)))
            steps_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(row, 2, steps_item)

            actions = _ActionWidget()
            actions.run_clicked.connect(self._make_run_handler(macro.name))
            actions.edit_clicked.connect(self._make_edit_handler(macro.name))
            actions.delete_clicked.connect(self._make_delete_handler(macro.name))

            # Re-apply running highlight if this macro is currently running
            if self._running_name == macro.name:
                actions.set_running(True)
                for col in range(3):
                    item = self._table.item(row, col)
                    if item:
                        item.setBackground(QColor("#1e3a5f"))

            self._table.setCellWidget(row, 3, actions)

    def set_macro_running(self, name: str, running: bool) -> None:
        """Highlight the running macro row; disable all other run buttons."""
        self._running_name = name if running else None

        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            if name_item is None:
                continue
            row_name: str = name_item.data(Qt.ItemDataRole.UserRole)
            actions: _ActionWidget | None = self._table.cellWidget(row, 3)  # type: ignore[assignment]

            is_this_row = row_name == name

            if actions is not None:
                actions.set_running(is_this_row and running)
                # Disable run button on other rows while one is executing
                actions.run_btn.setEnabled(not running or is_this_row)

            # Highlight active row
            for col in range(3):
                item = self._table.item(row, col)
                if item is not None:
                    if is_this_row and running:
                        item.setBackground(QColor("#1e3a5f"))
                    else:
                        item.setBackground(QColor("transparent"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_record_clicked(self) -> None:
        if self._recording:
            self.record_stop_requested.emit()
        else:
            self.record_start_requested.emit()

    def _make_run_handler(self, name: str):
        def _handler() -> None:
            self.macro_run_requested.emit(name)
        return _handler

    def _make_edit_handler(self, name: str):
        def _handler() -> None:
            self.macro_edit_requested.emit(name)
        return _handler

    def _make_delete_handler(self, name: str):
        def _handler() -> None:
            reply = QMessageBox.question(
                self,
                "Delete macro",
                f"Delete macro <b>{name}</b>? This cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            delete_macro(name)
            self.macro_deleted.emit(name)
            self.refresh()
        return _handler

    def _apply_record_idle_style(self) -> None:
        self._record_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {_BG_RECORD_IDLE};
                color: {_TEXT_PRIMARY};
                border: 1px solid {_BORDER_COLOR};
                border-radius: 4px;
                padding: 4px 14px;
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: #333333;
                border-color: {_ACCENT_INDIGO};
            }}
            """
        )

    def _apply_record_active_style(self, pulse_border: bool = False) -> None:
        border_col = "#fbbf24" if pulse_border else "#b91c1c"
        self._record_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {_BG_RECORD_ACTIVE};
                color: #ffffff;
                border: 2px solid {border_col};
                border-radius: 4px;
                padding: 4px 14px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: #dc2626;
            }}
            """
        )

    def _pulse_tick(self) -> None:
        """Alternate the record button border color every 800 ms."""
        self._pulse_state = not self._pulse_state
        self._apply_record_active_style(pulse_border=self._pulse_state)
