from __future__ import annotations

import subprocess

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QPixmap

from rearview.region_mapper import Region


def _window_origin(wid: int) -> tuple[int, int]:
    try:
        out = subprocess.check_output(
            ["xdotool", "getwindowgeometry", "--shell", str(wid)],
            stderr=subprocess.DEVNULL, text=True,
        )
        x = y = 0
        for line in out.splitlines():
            if line.startswith("X="):
                x = int(line.split("=", 1)[1])
            elif line.startswith("Y="):
                y = int(line.split("=", 1)[1])
        return x, y
    except Exception:
        return 0, 0


class RegionStreamer(QObject):
    """Streams a mapped region at ~20fps using Qt's grabWindow (compositor-backed,
    works even when the target window is on a different virtual desktop)."""

    frame_ready = pyqtSignal(QPixmap)

    def __init__(self, region: Region, wid: int | None = None, fps: int = 20, parent=None):
        super().__init__(parent)
        self._region = region
        self._wid = wid
        self._rel_x = region.x
        self._rel_y = region.y
        self._timer = QTimer(self)
        self._timer.setInterval(1000 // fps)
        self._timer.timeout.connect(self._capture)

    def start(self) -> None:
        if self._wid:
            wx, wy = _window_origin(self._wid)
            self._rel_x = max(0, self._region.x - wx)
            self._rel_y = max(0, self._region.y - wy)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _capture(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        r = self._region
        if self._wid:
            px = screen.grabWindow(self._wid, self._rel_x, self._rel_y, r.w, r.h)
        else:
            px = screen.grabWindow(0, r.x, r.y, r.w, r.h)
        if not px.isNull():
            self.frame_ready.emit(px)
