from __future__ import annotations

from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QGuiApplication

_BG = "#1e1e1e"
_BG_EDITOR = "#111111"
_SECTION_TEXT = "#666666"
_SECTION_BORDER = "#6366f1"
_BTN_BG = "#2a2a2a"
_BTN_HOVER = "#6366f1"
_TS_COLOR = "#555555"
_DOT_COLOR = "#6366f1"
_MSG_COLOR = "#cccccc"

_MAX_LINES = 500


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


class LogPanel(QWidget):
    # Internal signal used as a thread-safety bridge.
    _sig_append = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {_BG}; color: #ffffff;")
        self._line_count = 0
        self._build_ui()
        # Connect signal to the actual UI updater on the main thread.
        self._sig_append.connect(self._append_on_main_thread)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # Header row
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        header_row.addWidget(_section_label("Activity Log"))
        header_row.addStretch(1)

        clear_btn = _make_button("Clear")
        copy_btn = _make_button("Copy")
        clear_btn.clicked.connect(self._on_clear)
        copy_btn.clicked.connect(self._on_copy)
        header_row.addWidget(clear_btn)
        header_row.addWidget(copy_btn)
        root.addLayout(header_row)

        # Log text area
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("monospace", 11))
        self._log.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {_BG_EDITOR};
                color: {_MSG_COLOR};
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                padding: 8px;
            }}
            """
        )
        root.addWidget(self._log, 1)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_clear(self) -> None:
        self._log.clear()
        self._line_count = 0

    def _on_copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._log.toPlainText())

    def _append_on_main_thread(self, html_line: str) -> None:
        # Enforce max line cap by trimming from the top.
        if self._line_count >= _MAX_LINES:
            cursor = self._log.textCursor()
            from PyQt6.QtGui import QTextCursor
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                1,
            )
            cursor.removeSelectedText()
            # Remove the trailing block separator left by removal
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Right,
                QTextCursor.MoveMode.KeepAnchor,
                1,
            )
            if cursor.selectedText() == "\n" or cursor.selectedText() == " ":
                cursor.removeSelectedText()
        else:
            self._line_count += 1

        self._log.append(html_line)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def append(self, msg: str) -> None:
        """Thread-safe. Adds a timestamped line to the log."""
        ts = datetime.now().strftime("%H:%M:%S")
        html_line = (
            f'<span style="color:{_TS_COLOR};">{ts}</span>'
            f'&nbsp;&nbsp;<span style="color:{_DOT_COLOR};">&#9679;</span>'
            f'&nbsp;&nbsp;<span style="color:{_MSG_COLOR};">{msg}</span>'
        )
        self._sig_append.emit(html_line)
