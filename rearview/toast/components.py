from PyQt6.QtWidgets import (
    QPushButton, QWidget, QLabel, QVBoxLayout, QSizePolicy
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QFont


class DispositionButton(QPushButton):
    clicked_disposition = pyqtSignal(str)

    def __init__(self, label: str, hotkey: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._hotkey = hotkey
        self.setText(f"{label} [{hotkey}]")
        self.setFixedHeight(32)
        self._apply_style(hovered=False, pressed=False)
        self.clicked.connect(lambda: self.clicked_disposition.emit(self._label))

    def _apply_style(self, hovered: bool, pressed: bool) -> None:
        if pressed:
            bg = "#4f46e5"
            border = "#6366f1"
        elif hovered:
            bg = "#3a3a3a"
            border = "#6366f1"
        else:
            bg = "#2a2a2a"
            border = "#444"

        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg};
                border: 1px solid {border};
                color: #ffffff;
                border-radius: 4px;
                font-size: 12px;
                padding: 0 10px;
            }}
        """)

    def enterEvent(self, event):
        self._apply_style(hovered=True, pressed=False)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_style(hovered=False, pressed=False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._apply_style(hovered=False, pressed=True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._apply_style(hovered=True, pressed=False)
        super().mouseReleaseEvent(event)


class ContactCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._name_label = QLabel()
        name_font = QFont()
        name_font.setBold(True)
        name_font.setPointSize(14)
        self._name_label.setFont(name_font)
        self._name_label.setStyleSheet("color: #ffffff; background: transparent;")

        self._company_label = QLabel()
        self._company_label.setStyleSheet(
            "color: #aaaaaa; font-size: 12px; background: transparent;"
        )

        self._desc_label = QLabel()
        self._desc_label.setStyleSheet(
            "color: #888888; font-size: 11px; font-style: italic; background: transparent;"
        )
        self._desc_label.setWordWrap(True)
        self._desc_label.setMaximumHeight(34)  # approx 2 lines at 11px

        self._phone_label = QLabel()
        phone_font = QFont("monospace")
        phone_font.setPointSize(11)
        self._phone_label.setFont(phone_font)
        self._phone_label.setStyleSheet(
            "color: #aaaaaa; background: transparent;"
        )

        layout.addWidget(self._name_label)
        layout.addWidget(self._company_label)
        layout.addWidget(self._desc_label)
        layout.addWidget(self._phone_label)

    def update_contact(
        self,
        name: str,
        company: str,
        description: str,
        phone: str,
    ) -> None:
        self._name_label.setText(name)
        self._company_label.setText(company)
        self._desc_label.setText(description)
        self._phone_label.setText(phone)


class SectionHeader(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        self.setStyleSheet("""
            QLabel {
                color: #666666;
                font-size: 10px;
                letter-spacing: 1px;
                padding-left: 6px;
                border-left: 3px solid #6366f1;
                background: transparent;
            }
        """)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
