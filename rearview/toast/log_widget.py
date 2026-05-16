from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGraphicsOpacityEffect
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QFont


class LogWidget(QWidget):
    _sig_push = pyqtSignal(str)  # thread-safe bridge

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lines: list[QLabel] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 2, 4, 2)
        self._layout.setSpacing(2)
        self.setMaximumHeight(72)  # fits ~3 lines
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._sig_push.connect(self._do_push)

    def push(self, msg: str) -> None:
        """Thread-safe. Show a new log line."""
        self._sig_push.emit(msg)

    def _do_push(self, msg: str) -> None:
        # Remove oldest if already at 3
        if len(self._lines) >= 3:
            self._remove_line(self._lines[0])

        # Create label
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setText(f'<span style="color:#6366f1">●</span> {msg}')
        label.setStyleSheet("""
            QLabel {
                background: #111111;
                color: #aaaaaa;
                font-family: monospace;
                font-size: 11px;
                font-style: italic;
                padding: 2px 6px;
                border-radius: 3px;
            }
        """)

        self._layout.addWidget(label)
        self._lines.append(label)

        # Fade in
        effect = QGraphicsOpacityEffect(label)
        label.setGraphicsEffect(effect)
        anim_in = QPropertyAnimation(effect, b"opacity", label)
        anim_in.setDuration(200)
        anim_in.setStartValue(0.0)
        anim_in.setEndValue(1.0)
        anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim_in.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

        # Schedule fade-out after 3s
        QTimer.singleShot(3000, lambda lbl=label: self._fade_out(lbl))

    def _fade_out(self, label: QLabel) -> None:
        if label not in self._lines:
            return
        effect = label.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(label)
            label.setGraphicsEffect(effect)
        anim_out = QPropertyAnimation(effect, b"opacity", label)
        anim_out.setDuration(400)
        anim_out.setStartValue(1.0)
        anim_out.setEndValue(0.0)
        anim_out.setEasingCurve(QEasingCurve.Type.InCubic)
        anim_out.finished.connect(lambda lbl=label: self._remove_line(lbl))
        anim_out.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _remove_line(self, label: QLabel) -> None:
        if label in self._lines:
            self._lines.remove(label)
        self._layout.removeWidget(label)
        label.deleteLater()
