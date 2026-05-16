from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QApplication,
    QVBoxLayout, QHBoxLayout, QFrame, QSizePolicy,
    QLayout,
)
from PyQt6.QtCore import (
    Qt, QPoint, QRect, QTimer, QPropertyAnimation,
    QEasingCurve, pyqtSignal, QMetaObject, Q_ARG,
    QSize,
)
from PyQt6.QtGui import QColor

from rearview.toast.components import DispositionButton, ContactCard, SectionHeader
from rearview.toast.log_widget import LogWidget
from rearview.toast.teleprompter import TeleprompterWidget
from rearview.config import get_config, Disposition
from rearview.templates.base import Contact


# ---------------------------------------------------------------------------
# Flow layout — wraps children left-to-right, then next row
# ---------------------------------------------------------------------------

class _FlowLayout(QLayout):
    """Simple wrapping flow layout for disposition buttons."""

    def __init__(self, parent=None, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom(),
        )
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0
        right_bound = rect.right() - margins.right()

        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w
            if next_x > right_bound and line_height > 0:
                x = rect.x() + margins.left()
                y += line_height + self._v_spacing
                next_x = x + w
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x + self._h_spacing
            line_height = max(line_height, h)

        return y + line_height - rect.y() + margins.bottom()


# ---------------------------------------------------------------------------
# Header bar — supports drag
# ---------------------------------------------------------------------------

class _HeaderBar(QWidget):
    """Thin bar at the top of the toast that the user can drag."""

    drag_delta = pyqtSignal(QPoint)
    switch_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos: QPoint | None = None
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def set_target_name(self, name: str) -> None:
        """Update the target label text."""
        self._target_label.setText(name)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint()
            delta = new_pos - self._drag_pos
            self._drag_pos = new_pos
            self.drag_delta.emit(delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# Main ShadowToast window
# ---------------------------------------------------------------------------

TOAST_WIDTH = 420
PILL_HEIGHT = 32
BG_COLOR = "#1e1e1e"
BORDER_COLOR = "#333"
SECTION_SEP = "#2a2a2a"
INDIGO = "#4f46e5"


class ShadowToast(QWidget):
    disposition_selected = pyqtSignal(str)
    next_call_requested = pyqtSignal()
    closed = pyqtSignal()
    target_switch_requested = pyqtSignal()

    # Internal signals for thread-safe method calls
    _sig_update_contact = pyqtSignal(object)       # Contact
    _sig_set_disposition_mode = pyqtSignal(bool)
    _sig_load_script = pyqtSignal(str, object)     # text, variables dict
    _sig_push_transcript = pyqtSignal(str)
    _sig_flash = pyqtSignal()
    _sig_set_target = pyqtSignal(str)
    _sig_push_log = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._config = get_config()
        self._collapsed = False
        self._flash_count = 0
        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._on_flash_tick)
        self._border_flashing = False

        self._setup_window_flags()
        self._build_ui()
        self._apply_stylesheet()
        self.setWindowOpacity(self._config.toast.opacity)
        self._position_bottom_right()

        # Wire internal signals (thread-safety)
        self._sig_update_contact.connect(self._do_update_contact)
        self._sig_set_disposition_mode.connect(self._do_set_disposition_mode)
        self._sig_load_script.connect(self._do_load_script)
        self._sig_push_transcript.connect(self._teleprompter.update_position)
        self._sig_flash.connect(self._do_flash)
        self._sig_set_target.connect(self._header.set_target_name)
        self._sig_push_log.connect(self._log_widget.push)
        self._header.switch_requested.connect(self._on_switch_requested)

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window_flags(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedWidth(TOAST_WIDTH)

    def _position_bottom_right(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            self.move(0, 0)
            return
        geom = screen.availableGeometry()
        expanded_height = self.sizeHint().height()
        x = geom.right() - TOAST_WIDTH - 20
        y = geom.bottom() - expanded_height - 20
        self.move(x, y)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- Pill (collapsed state) ---
        self._pill = QLabel("📞 Rearview ▲")
        self._pill.setFixedHeight(PILL_HEIGHT)
        self._pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pill.setStyleSheet(f"""
            QLabel {{
                background-color: {BG_COLOR};
                color: #dddddd;
                border: 1px solid {BORDER_COLOR};
                border-radius: 16px;
                font-size: 13px;
                padding: 0 16px;
            }}
        """)
        self._pill.mousePressEvent = self._pill_clicked
        self._pill.hide()
        outer.addWidget(self._pill)

        # --- Main body container ---
        self._body = QFrame()
        self._body.setObjectName("toastBody")
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(12, 8, 12, 12)
        body_layout.setSpacing(8)

        # Header bar
        self._header = _HeaderBar()
        self._header.drag_delta.connect(self._on_drag)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(6, 0, 6, 0)
        header_layout.setSpacing(6)

        self._retract_btn = QPushButton("▼ hide")
        self._retract_btn.setFixedHeight(22)
        self._retract_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retract_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: #888;
                border: none;
                font-size: 11px;
            }}
            QPushButton:hover {{ color: #ccc; }}
        """)
        self._retract_btn.clicked.connect(self.collapse)

        self._header._target_label = QLabel("No target")
        self._header._target_label.setMaximumWidth(200)
        self._header._target_label.setStyleSheet("color: #888; font-size: 11px;")

        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setFixedSize(22, 22)
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: #888;
                border: none;
                font-size: 14px;
            }}
            QPushButton:hover {{ color: #ccc; }}
        """)
        self._settings_btn.clicked.connect(self._open_settings)

        self._switch_btn = QPushButton("⇄")
        self._switch_btn.setFixedSize(22, 22)
        self._switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._switch_btn.setToolTip("Switch target window")
        self._switch_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: #888;
                border: none;
                font-size: 14px;
            }}
            QPushButton:hover {{ color: #ccc; }}
        """)
        self._switch_btn.clicked.connect(self._header.switch_requested.emit)

        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(22, 22)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: #888;
                border: none;
                font-size: 14px;
            }}
            QPushButton:hover {{ color: #ff6666; }}
        """)
        self._close_btn.clicked.connect(self._on_close)

        header_layout.addWidget(self._retract_btn)
        header_layout.addWidget(self._header._target_label)
        header_layout.addStretch()
        header_layout.addWidget(self._switch_btn)
        header_layout.addWidget(self._settings_btn)
        header_layout.addWidget(self._close_btn)

        body_layout.addWidget(self._header)

        # Separator
        body_layout.addWidget(self._make_separator())

        # Script section
        body_layout.addWidget(SectionHeader("Script"))
        self._teleprompter = TeleprompterWidget()
        body_layout.addWidget(self._teleprompter)

        # Separator
        body_layout.addWidget(self._make_separator())

        # Contact section
        body_layout.addWidget(SectionHeader("Contact"))
        self._contact_card = ContactCard()
        body_layout.addWidget(self._contact_card)

        # Separator
        body_layout.addWidget(self._make_separator())

        # Disposition section
        self._disposition_section = QWidget()
        self._disposition_section.setObjectName("dispositionSection")
        disp_layout = QVBoxLayout(self._disposition_section)
        disp_layout.setContentsMargins(0, 0, 0, 0)
        disp_layout.setSpacing(6)
        disp_layout.addWidget(SectionHeader("Disposition"))

        self._disp_buttons_widget = QWidget()
        self._flow_layout = _FlowLayout(self._disp_buttons_widget, h_spacing=6, v_spacing=6)
        self._build_disposition_buttons()
        disp_layout.addWidget(self._disp_buttons_widget)

        # Next Call button row
        next_row = QHBoxLayout()
        next_row.setContentsMargins(0, 0, 0, 0)
        next_row.addStretch()
        self._next_btn = QPushButton("▶ Next Call")
        self._next_btn.setFixedHeight(28)
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {INDIGO};
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: #6366f1;
            }}
            QPushButton:pressed {{
                background-color: #3730a3;
            }}
        """)
        self._next_btn.clicked.connect(self.next_call_requested.emit)
        next_row.addWidget(self._next_btn)
        disp_layout.addLayout(next_row)

        body_layout.addWidget(self._disposition_section)

        # Log section
        self._log_widget = LogWidget(self)
        body_layout.addWidget(self._log_widget)

        outer.addWidget(self._body)

    def _make_separator(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {SECTION_SEP}; border: none;")
        return sep

    def _build_disposition_buttons(self):
        # Clear existing
        while self._flow_layout.count():
            item = self._flow_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        for disp in self._config.dispositions:
            btn = DispositionButton(label=disp.label, hotkey=disp.hotkey)
            btn.clicked_disposition.connect(self.disposition_selected.emit)
            self._flow_layout.addWidget(btn)

    # ------------------------------------------------------------------
    # Stylesheet
    # ------------------------------------------------------------------

    def _apply_stylesheet(self):
        self.setStyleSheet(f"""
            ShadowToast {{
                background-color: {BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
            }}
            QFrame#toastBody {{
                background-color: {BG_COLOR};
                border: 1px solid {BORDER_COLOR};
                border-radius: 8px;
            }}
            QWidget {{
                background-color: {BG_COLOR};
                color: #dddddd;
            }}
            QWidget#dispositionSection {{
                border: 1px solid transparent;
                border-radius: 4px;
            }}
        """)

    # ------------------------------------------------------------------
    # Collapse / expand
    # ------------------------------------------------------------------

    def collapse(self):
        if self._collapsed:
            return
        self._collapsed = True
        self._body.hide()
        self._pill.show()
        self._animate_to_height(PILL_HEIGHT)

    def expand(self):
        if not self._collapsed:
            return
        self._collapsed = False
        self._pill.hide()
        self._body.show()
        # Let Qt compute the proper height
        self.adjustSize()
        self._animate_to_height(self.sizeHint().height())

    def _pill_clicked(self, event):
        self.expand()

    def _animate_to_height(self, target_height: int):
        current_geo = self.geometry()
        target_geo = QRect(
            current_geo.x(),
            current_geo.y() + (current_geo.height() - target_height),
            current_geo.width(),
            target_height,
        )
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(150)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(current_geo)
        anim.setEndValue(target_geo)
        anim.start()
        # Keep reference so it isn't garbage-collected mid-flight
        self._current_anim = anim

    # ------------------------------------------------------------------
    # Dragging
    # ------------------------------------------------------------------

    def _on_drag(self, delta: QPoint):
        new_pos = self.pos() + delta
        self.move(new_pos)
        # Persist position to config
        self._config.toast.position = f"{new_pos.x()},{new_pos.y()}"

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def _open_settings(self):
        settings_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
        editor = os.environ.get("EDITOR", "xdg-open")
        subprocess.Popen([editor, str(settings_path)])

    def _on_close(self):
        self.closed.emit()
        self.hide()

    def _on_switch_requested(self):
        self.target_switch_requested.emit()

    # ------------------------------------------------------------------
    # Flash effect
    # ------------------------------------------------------------------

    def _do_flash(self):
        if self._border_flashing:
            return
        self._border_flashing = True
        self._flash_count = 0
        self._flash_timer.start(200)

    def _on_flash_tick(self):
        if self._flash_count >= 6:  # 3 on + 3 off = 6 ticks
            self._flash_timer.stop()
            self._border_flashing = False
            self._body.setStyleSheet("")
            self._apply_stylesheet()
            return
        if self._flash_count % 2 == 0:
            # Flash on — orange border
            self._body.setStyleSheet(f"""
                QFrame#toastBody {{
                    background-color: {BG_COLOR};
                    border: 2px solid #f97316;
                    border-radius: 8px;
                }}
            """)
        else:
            # Flash off — restore normal border
            self._body.setStyleSheet(f"""
                QFrame#toastBody {{
                    background-color: {BG_COLOR};
                    border: 1px solid {BORDER_COLOR};
                    border-radius: 8px;
                }}
            """)
        self._flash_count += 1

    # ------------------------------------------------------------------
    # Disposition section pulse border
    # ------------------------------------------------------------------

    def _do_set_disposition_mode(self, active: bool):
        if active:
            self._disposition_section.setStyleSheet(f"""
                QWidget#dispositionSection {{
                    border: 1px solid #6366f1;
                    border-radius: 4px;
                }}
            """)
        else:
            self._disposition_section.setStyleSheet(f"""
                QWidget#dispositionSection {{
                    border: 1px solid transparent;
                    border-radius: 4px;
                }}
            """)

    # ------------------------------------------------------------------
    # Internal slot implementations
    # ------------------------------------------------------------------

    def _do_update_contact(self, contact: Contact):
        self._contact_card.update_contact(
            name=contact.name,
            company=contact.company,
            description=contact.description,
            phone=contact.phone,
        )
        self._teleprompter.reset()

    def _do_load_script(self, text: str, variables: object):
        self._teleprompter.load_script(text, variables)

    # ------------------------------------------------------------------
    # Public API (thread-safe)
    # ------------------------------------------------------------------

    def update_contact(self, contact: Contact) -> None:
        """Thread-safe. Push contact data to ContactCard, reset teleprompter."""
        self._sig_update_contact.emit(contact)

    def set_disposition_mode(self, active: bool) -> None:
        """Thread-safe. Highlight the disposition section when active."""
        self._sig_set_disposition_mode.emit(active)

    def load_script(self, text: str, variables: dict) -> None:
        """Thread-safe. Forward to TeleprompterWidget."""
        self._sig_load_script.emit(text, variables)

    def push_transcript(self, chunk: str) -> None:
        """Thread-safe. Forward to TeleprompterWidget.update_position()."""
        self._sig_push_transcript.emit(chunk)

    def flash(self) -> None:
        """Thread-safe. Flash window border orange to grab attention."""
        self._sig_flash.emit()

    def set_target(self, name: str) -> None:
        """Thread-safe. Update the target name shown in the header."""
        self._sig_set_target.emit(name)

    def push_log(self, msg: str) -> None:
        """Thread-safe. Show a status log line in the toast."""
        self._sig_push_log.emit(msg)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_toast(app: QApplication) -> ShadowToast:
    """Create and position a ShadowToast window for the given QApplication."""
    toast = ShadowToast()
    toast.show()
    return toast
