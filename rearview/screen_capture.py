from __future__ import annotations
import subprocess
from PyQt6.QtGui import QPixmap, QGuiApplication, QScreen


def _get_window_origin(wid: int) -> tuple[int, int]:
    """Return (x, y) screen position of the window using xdotool."""
    try:
        out = subprocess.check_output(
            ["xdotool", "getwindowgeometry", "--shell", str(wid)],
            stderr=subprocess.DEVNULL, text=True
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


def capture_region(x: int, y: int, w: int, h: int, wid: int | None = None) -> QPixmap:
    """Capture a screen region. If wid given, captures from that window using window-relative coords."""
    screen: QScreen | None = QGuiApplication.primaryScreen()
    if screen is None:
        return QPixmap()
    try:
        if wid:
            wx, wy = _get_window_origin(wid)
            rx, ry = max(0, x - wx), max(0, y - wy)
            return screen.grabWindow(wid, rx, ry, w, h)
        return screen.grabWindow(0, x, y, w, h)
    except Exception:
        return QPixmap()


def capture_all_regions(regions: list, wid: int | None = None) -> list[tuple[str, QPixmap]]:
    """Capture all regions. Pass wid to capture from a specific window only."""
    results = []
    for region in regions:
        if region.w <= 0 or region.h <= 0:
            continue
        px = capture_region(region.x, region.y, region.w, region.h, wid=wid)
        results.append((region.name, px))
    return results
