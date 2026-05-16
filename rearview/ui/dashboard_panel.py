from __future__ import annotations

from rearview.target_selector import get_session, TargetSession
from rearview.config import get_config
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFrame,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor


_BG = "#1e1e1e"
_BG_ALT = "#242424"
_SECTION_BORDER = "#6366f1"
_SECTION_TEXT = "#666666"
_DOT_CONNECTED = "#22c55e"
_DOT_DISCONNECTED = "#666666"
_BTN_BG = "#2a2a2a"
_BTN_HOVER = "#6366f1"
_NOTE_COLOR = "#555555"
_DIVIDER = "#2a2a2a"


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
    line.setStyleSheet(f"color: {_DIVIDER}; background-color: {_DIVIDER};")
    line.setFixedHeight(1)
    return line


class DashboardPanel(QWidget):
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    record_requested = pyqtSignal()
    switch_target_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {_BG}; color: #ffffff;")
        self._build_ui()
        self._apply_initial_status()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        root.addWidget(self._build_status_section())
        root.addWidget(_divider())
        root.addWidget(self._build_quick_actions_section())
        root.addWidget(_divider())
        root.addWidget(self._build_hotkeys_section())
        root.addStretch(1)

    # --- Status section ---------------------------------------------------

    def _build_status_section(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background-color: {_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_section_label("Status"))

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._dot_label = QLabel("●")
        dot_font = QFont("monospace", 16)
        dot_font.setBold(True)
        self._dot_label.setFont(dot_font)
        self._dot_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._status_label = QLabel("Disconnected")
        self._status_label.setStyleSheet("color: #cccccc; font-size: 13px;")

        row.addWidget(self._dot_label)
        row.addWidget(self._status_label)
        row.addStretch(1)

        self._switch_btn = self._make_button("⇄ Switch", small=True)
        self._switch_btn.clicked.connect(self.switch_target_requested)
        row.addWidget(self._switch_btn)

        layout.addLayout(row)
        return container

    # --- Quick actions section -------------------------------------------

    def _build_quick_actions_section(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background-color: {_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_section_label("Quick Actions"))

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        start_btn = self._make_button("▶ Start")
        start_btn.clicked.connect(self.start_requested)

        stop_btn = self._make_button("■ Stop")
        stop_btn.clicked.connect(self.stop_requested)

        record_btn = self._make_button("● Record")
        record_btn.clicked.connect(self.record_requested)

        target_btn = self._make_button("⇄ Target")
        target_btn.clicked.connect(self.switch_target_requested)

        for btn in (start_btn, stop_btn, record_btn, target_btn):
            btn_row.addWidget(btn)

        layout.addLayout(btn_row)
        return container

    # --- Hotkeys section -------------------------------------------------

    def _build_hotkeys_section(self) -> QWidget:
        container = QWidget()
        container.setStyleSheet(f"background-color: {_BG};")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_section_label("Hotkeys"))

        self._hotkeys_table = QTableWidget()
        self._hotkeys_table.setColumnCount(2)
        self._hotkeys_table.setHorizontalHeaderLabels(["Key", "Action"])
        self._hotkeys_table.horizontalHeader().setVisible(False)
        self._hotkeys_table.verticalHeader().setVisible(False)
        self._hotkeys_table.setShowGrid(False)
        self._hotkeys_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._hotkeys_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hotkeys_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._hotkeys_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._hotkeys_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._hotkeys_table.setStyleSheet(
            f"""
            QTableWidget {{
                background-color: {_BG};
                color: #cccccc;
                border: none;
                font-size: 12px;
            }}
            QTableWidget::item {{
                padding: 3px 6px;
                border: none;
            }}
            """
        )

        layout.addWidget(self._hotkeys_table)
        self._populate_hotkeys_table()

        note = QLabel("(active even when window is minimized)")
        note_font = QFont("sans-serif", 11)
        note_font.setItalic(True)
        note.setFont(note_font)
        note.setStyleSheet(f"color: {_NOTE_COLOR};")
        layout.addWidget(note)

        return container

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_button(self, text: str, small: bool = False) -> QPushButton:
        btn = QPushButton(text)
        height = 26 if small else 32
        btn.setFixedHeight(height)
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

    def _static_hotkeys(self) -> list[tuple[str, str]]:
        return [
            ("Super+S", "Next call / advance"),
            ("Super+R", "Toggle recording"),
        ]

    def _populate_hotkeys_table(self) -> None:
        cfg = get_config()
        rows: list[tuple[str, str]] = list(self._static_hotkeys())
        for disp in cfg.dispositions:
            rows.append((disp.hotkey.upper(), disp.label))

        self._hotkeys_table.setRowCount(len(rows))

        mono_font = QFont("monospace", 11)

        for i, (key, action) in enumerate(rows):
            bg_color = QColor(_BG if i % 2 == 0 else _BG_ALT)

            key_item = QTableWidgetItem(key)
            key_item.setFont(mono_font)
            key_item.setForeground(QColor("#a5b4fc"))
            key_item.setBackground(bg_color)

            action_item = QTableWidgetItem(action)
            action_item.setBackground(bg_color)
            action_item.setForeground(QColor("#cccccc"))

            self._hotkeys_table.setItem(i, 0, key_item)
            self._hotkeys_table.setItem(i, 1, action_item)

        self._hotkeys_table.resizeRowsToContents()
        total_height = sum(
            self._hotkeys_table.rowHeight(r) for r in range(self._hotkeys_table.rowCount())
        )
        self._hotkeys_table.setFixedHeight(total_height + 2)

    def _apply_initial_status(self) -> None:
        session = get_session()
        target = session.current
        if target is not None:
            self.set_status(connected=True, target_name=target.display_name)
        else:
            self.set_status(connected=False, target_name="—")

        session.on_change(lambda t: self.set_status(True, t.display_name))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_status(self, connected: bool, target_name: str) -> None:
        dot_color = _DOT_CONNECTED if connected else _DOT_DISCONNECTED
        dot_font = QFont("monospace", 16)
        dot_font.setBold(True)
        self._dot_label.setFont(dot_font)
        self._dot_label.setStyleSheet(f"color: {dot_color};")

        state_text = "Connected" if connected else "Disconnected"
        self._status_label.setText(f"{state_text}    {target_name}")

    def refresh_hotkeys(self) -> None:
        self._populate_hotkeys_table()
