from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PyQt6.QtCore import pyqtSignal, QObject, Qt
from PyQt6.QtGui import QIcon, QPixmap, QColor, QPainter, QPen, QPainterPath

# vb theme: teal on transparent
_TEAL       = QColor(0, 255, 212)    # #00FFD4
_TEAL_DIM   = QColor(0, 180, 150)    # dimmed teal for disconnected
_BLACK      = QColor(0, 0, 0)
_RED        = QColor(239, 68, 68)    # recording red
_SZ         = 64                     # internal render size

_STATUS_COLORS = {
    "connected":    _TEAL,
    "disconnected": _TEAL_DIM,
    "recording":    _RED,
}


def _make_rearview_icon(color: QColor, active: bool = False) -> QIcon:
    """
    Rearview mirror icon — vb theme.
    Idle:   teal outline mirror + stem
    Active: solid filled mirror + black interior reflection line
    """
    px = QPixmap(_SZ, _SZ)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Mount at top: narrow bracket that clips to windshield
    cx = _SZ // 2
    sx, sy, sw, sh = cx - 4, 4, 8, 10
    if active:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRect(sx, sy, sw, sh)
        # Mounting bar across top
        p.drawRect(sx - 8, sy, sw + 16, 4)
    else:
        pen2 = QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(sx - 8, sy + 2, sx + sw + 8, sy + 2)  # top bar
        p.drawLine(cx, sy + 2, cx, sy + sh)               # stem down

    # Mirror glass: wide rounded rectangle hanging below the mount
    mx, my, mw, mh = 6, sy + sh, 52, 30
    if active:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(mx, my, mw, mh, 8, 8)
        # Reflection line inside (black) — diagonal slash
        pen = QPen(_BLACK, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(mx + 12, my + 6, mx + 30, my + mh - 6)
    else:
        pen = QPen(color, 3, Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(mx, my, mw, mh, 8, 8)

    p.end()
    return QIcon(px.scaled(22, 22,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation))


class RearviewTrayIcon(QSystemTrayIcon):
    show_hide_requested = pyqtSignal()
    record_start_requested = pyqtSignal()
    record_stop_requested = pyqtSignal()
    switch_target_requested = pyqtSignal()
    quit_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target_name = "No target"
        self._status = "disconnected"
        self.setIcon(_make_rearview_icon(_TEAL_DIM, active=False))
        self.setToolTip("Rearview — disconnected")
        self._build_menu()
        self.activated.connect(self._on_activated)
        self.setVisible(True)

    @staticmethod
    def _make_icon(color: QColor, active: bool = False) -> QIcon:
        return _make_rearview_icon(color, active)

    def _build_menu(self) -> None:
        menu = QMenu()

        self._action_show_hide = menu.addAction("Show / Hide Window")
        self._action_show_hide.triggered.connect(self.show_hide_requested)

        menu.addSeparator()

        self._action_start = menu.addAction("● Start Recording")
        self._action_start.triggered.connect(self.record_start_requested)

        self._action_stop = menu.addAction("■ Stop Recording")
        self._action_stop.triggered.connect(self.record_stop_requested)
        self._action_stop.setEnabled(False)

        menu.addSeparator()

        action_switch = menu.addAction("⇄ Switch Target")
        action_switch.triggered.connect(self.switch_target_requested)

        menu.addSeparator()

        action_quit = menu.addAction("Quit")
        action_quit.triggered.connect(self.quit_requested)

        self.setContextMenu(menu)

    def _update_tooltip(self) -> None:
        self.setToolTip(f"Rearview — {self._status} — {self._target_name}")

    def set_status(self, status: str) -> None:
        self._status = status
        color = _STATUS_COLORS.get(status, _TEAL_DIM)
        self.setIcon(self._make_icon(color, active=(status == "connected")))
        self._update_tooltip()

    def set_target_name(self, name: str) -> None:
        self._target_name = name
        self._update_tooltip()

    def set_recording(self, active: bool) -> None:
        self._action_start.setEnabled(not active)
        self._action_stop.setEnabled(active)
        if active:
            self.setIcon(self._make_icon(_RED, active=True))
        else:
            color = _STATUS_COLORS.get(self._status, _TEAL_DIM)
            self.setIcon(self._make_icon(color, active=(self._status == "connected")))

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_hide_requested.emit()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_hide_requested.emit()
