from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QDialogButtonBox, QPushButton,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont

from rearview.window_discovery import WindowTarget

_BG = "#1a1a1a"
_BG_LIST = "#111111"
_ACCENT = "#6366f1"
_TEXT = "#e5e7eb"
_TEXT_DIM = "#6b7280"
_BORDER = "#2a2a2a"
_ROW_HOVER = "#222233"
_ROW_SELECTED = "#2e2e5e"


class TargetPickerDialog(QDialog):
    """Qt dialog for picking a target window — used when no terminal is available."""

    def __init__(self, targets: list[WindowTarget], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rearview — Select Target")
        self.setMinimumSize(QSize(460, 360))
        self.setStyleSheet(f"""
            QDialog {{ background-color: {_BG}; color: {_TEXT}; }}
            QLabel  {{ color: {_TEXT}; }}
        """)
        self._targets = targets
        self._selected: WindowTarget | None = None
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Select the window or browser tab to control")
        title.setFont(QFont("sans-serif", 12))
        title.setStyleSheet(f"color: {_TEXT}; font-weight: bold;")
        layout.addWidget(title)

        subtitle = QLabel("Double-click or press OK to confirm")
        subtitle.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px;")
        layout.addWidget(subtitle)

        self._list = QListWidget()
        self._list.setStyleSheet(f"""
            QListWidget {{
                background-color: {_BG_LIST};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 4px;
                font-size: 13px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 8px 12px;
                border-bottom: 1px solid {_BORDER};
            }}
            QListWidget::item:hover {{
                background-color: {_ROW_HOVER};
            }}
            QListWidget::item:selected {{
                background-color: {_ROW_SELECTED};
                color: #ffffff;
            }}
        """)
        self._list.setSpacing(1)

        for t in self._targets:
            self._list.addItem(self._make_item(t))

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

        self._list.doubleClicked.connect(self._on_accept)
        layout.addWidget(self._list, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setFixedHeight(30)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: #2a2a2a; color: {_TEXT};
                border: 1px solid {_BORDER}; border-radius: 4px;
                padding: 0 12px; font-size: 12px;
            }}
            QPushButton:hover {{ background: #333; }}
        """)
        refresh_btn.clicked.connect(self._on_refresh)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: #fff;
                border: none; border-radius: 4px;
                padding: 4px 16px; font-size: 12px; min-width: 64px;
            }}
            QPushButton:hover {{ background: #4f46e5; }}
            QPushButton[text="Cancel"] {{ background: #2a2a2a; }}
            QPushButton[text="Cancel"]:hover {{ background: #333; }}
        """)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)

        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._selected = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def _make_item(self, t: "WindowTarget") -> QListWidgetItem:
        if t.depth > 0:
            label = f"    {t.icon}  {t.tab_title or t.display_name}" if t.icon else f"    {t.tab_title or t.display_name}"
        else:
            label = f"{t.icon}  {t.display_name}" if t.icon else t.display_name
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, t)
        if t.depth > 0:
            item.setForeground(QColor("#93c5fd"))  # light blue for tabs
        elif t.type == "browser_window":
            item.setForeground(QColor("#34d399"))  # green for browser windows
        return item

    def _on_refresh(self) -> None:
        import asyncio
        from rearview.window_discovery import discover_all
        loop = asyncio.new_event_loop()
        self._targets = loop.run_until_complete(discover_all())
        loop.close()

        self._list.clear()
        for t in self._targets:
            self._list.addItem(self._make_item(t))
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    @property
    def selected(self) -> WindowTarget | None:
        return self._selected
