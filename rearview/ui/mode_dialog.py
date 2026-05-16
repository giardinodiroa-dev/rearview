from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QCursor

from rearview.window_discovery import WindowTarget

_BG = "#141414"
_BG_CARD = "#1e1e1e"
_ACCENT = "#6366f1"
_TEXT = "#e5e7eb"
_TEXT_DIM = "#6b7280"
_BORDER = "#2a2a2a"


class _ModeCard(QFrame):
    """Clickable card representing a single mode option."""

    def __init__(self, icon: str, title: str, description: str, value: str, parent=None) -> None:
        super().__init__(parent)
        self._value = value
        self._selected = False

        self.setFixedSize(QSize(180, 110))
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._apply_style(hover=False, selected=False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_label = QLabel(icon)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet(f"font-size: 28px; background: transparent; border: none;")
        layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {_TEXT}; background: transparent; border: none;"
        )
        layout.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setStyleSheet(
            f"font-size: 11px; color: {_TEXT_DIM}; background: transparent; border: none;"
        )
        layout.addWidget(desc_label)

    # ------------------------------------------------------------------
    # Styling helpers
    # ------------------------------------------------------------------

    def _apply_style(self, *, hover: bool, selected: bool) -> None:
        if selected:
            bg = "#1e1e3a"
            border_width = 2
            border_color = _ACCENT
        elif hover:
            bg = "#1a1a2e"
            border_width = 1
            border_color = _ACCENT
        else:
            bg = _BG_CARD
            border_width = 1
            border_color = _BORDER

        self.setStyleSheet(
            f"QFrame {{"
            f"  background-color: {bg};"
            f"  border: {border_width}px solid {border_color};"
            f"  border-radius: 8px;"
            f"}}"
        )

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style(hover=False, selected=selected)

    @property
    def value(self) -> str:
        return self._value

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def enterEvent(self, event) -> None:
        if not self._selected:
            self._apply_style(hover=True, selected=False)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self._selected:
            self._apply_style(hover=False, selected=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Emit selection upward via parent dialog
            dialog = self._find_dialog()
            if dialog is not None:
                dialog._on_card_clicked(self._value)
        super().mousePressEvent(event)

    def _find_dialog(self) -> "ModePickerDialog | None":
        parent = self.parent()
        while parent is not None:
            if isinstance(parent, ModePickerDialog):
                return parent
            parent = parent.parent()  # type: ignore[assignment]
        return None


class ModePickerDialog(QDialog):
    """Dialog shown after the user picks a target window.

    Presents two mode cards ("app" and "viewer") and immediately accepts
    when the user clicks one.
    """

    def __init__(self, target: WindowTarget, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rearview")
        self.setMinimumSize(QSize(420, 280))
        self.setStyleSheet(f"QDialog {{ background-color: {_BG}; color: {_TEXT}; }}")

        self._mode = ""
        self._cards: list[_ModeCard] = []
        self._target = target
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(14)

        # Target line
        icon_prefix = f"{self._target.icon}  " if self._target.icon else ""
        target_label = QLabel(f"Target selected:  {icon_prefix}<b>{self._target.display_name}</b>")
        target_label.setTextFormat(Qt.TextFormat.RichText)
        target_label.setStyleSheet(f"color: {_TEXT}; font-size: 13px;")
        layout.addWidget(target_label)

        # Subtitle
        subtitle = QLabel("What do you want to do?")
        subtitle.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 12px;")
        layout.addWidget(subtitle)

        layout.addStretch(1)

        # Cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(16)
        cards_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        app_card = _ModeCard("🖥", "Open App", "Connect and control\nthis window or tab", "app", self)
        viewer_card = _ModeCard("👁", "Toast Viewer", "Scrape and display\npage content as toast", "viewer", self)

        self._cards = [app_card, viewer_card]
        for card in self._cards:
            cards_row.addWidget(card)

        layout.addLayout(cards_row)
        layout.addStretch(1)

        hint = QLabel("Click a card to confirm  ·  Esc to cancel")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 10px;")
        layout.addWidget(hint)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _on_card_clicked(self, value: str) -> None:
        self._mode = value
        for card in self._cards:
            card.set_selected(card.value == value)
        self.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Confirm whichever card is currently selected (first if none)
            if not self._mode and self._cards:
                self._on_card_clicked(self._cards[0].value)
            elif self._mode:
                self.accept()
        elif key == Qt.Key.Key_Escape:
            self._mode = ""
            self.reject()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Result
    # ------------------------------------------------------------------

    @property
    def selected_mode(self) -> str:
        """Return "app", "viewer", or "" if cancelled."""
        return self._mode
