from __future__ import annotations

import asyncio
import re
import subprocess
import threading

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap

from rearview.region_mapper import Region
from rearview.window_discovery import WindowTarget


def _find_browser_wid(cdp_url: str) -> int | None:
    """Parse port from cdp_url, find PID via ss, then find wid via xdotool."""
    try:
        m = re.search(r":(\d+)", cdp_url)
        if not m:
            return None
        port = m.group(1)

        ss_out = subprocess.check_output(
            ["ss", "-tlnp", f"sport = :{port}"],
            stderr=subprocess.DEVNULL, text=True,
        )
        pid_m = re.search(r"pid=(\d+)", ss_out)
        if not pid_m:
            return None
        pid = pid_m.group(1)

        xdot_out = subprocess.check_output(
            ["xdotool", "search", "--pid", pid],
            stderr=subprocess.DEVNULL, text=True,
        )
        for line in xdot_out.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
        return None
    except Exception:
        return None


class RegionStreamer(QObject):
    """Streams a mapped region at ~fps using either X11 grabWindow or CDP screenshot."""

    frame_ready = pyqtSignal(QImage)

    def __init__(self, region: Region, target: WindowTarget, fps: int = 20, parent=None):
        super().__init__(parent)
        self._region = region
        self._target = target
        self._fps = fps

        self._rel_x = region.x
        self._rel_y = region.y

        self._running = False

        self._timer = QTimer(self)
        self._timer.setInterval(1000 // fps)
        self._timer.timeout.connect(self._capture)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._is_browser_path():
            self._running = True
            t = threading.Thread(target=self._cdp_loop, daemon=True)
            t.start()
        else:
            wid = self._target.window_id
            if wid is not None:
                try:
                    out = subprocess.check_output(
                        ["xdotool", "getwindowgeometry", "--shell", str(wid)],
                        stderr=subprocess.DEVNULL, text=True,
                    )
                    wx = wy = 0
                    for line in out.splitlines():
                        if line.startswith("X="):
                            wx = int(line.split("=", 1)[1])
                        elif line.startswith("Y="):
                            wy = int(line.split("=", 1)[1])
                    self._rel_x = max(0, self._region.x - wx)
                    self._rel_y = max(0, self._region.y - wy)
                except Exception:
                    self._rel_x = self._region.x
                    self._rel_y = self._region.y
            self._running = True
            self._timer.start()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()

    # ------------------------------------------------------------------
    # X11 path
    # ------------------------------------------------------------------

    def _capture(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        r = self._region
        wid = self._target.window_id
        if wid is not None:
            px = screen.grabWindow(wid, self._rel_x, self._rel_y, r.w, r.h)
        else:
            px = screen.grabWindow(0, r.x, r.y, r.w, r.h)
        if not px.isNull():
            self.frame_ready.emit(px.toImage())

    # ------------------------------------------------------------------
    # Browser/CDP path
    # ------------------------------------------------------------------

    def _is_browser_path(self) -> bool:
        return (
            self._target.type == "browser_tab"
            and bool(getattr(self._target, "cdp_url", None))
            and bool(getattr(self._target, "tab_id", None))
        )

    def _cdp_loop(self) -> None:
        asyncio.run(self._cdp_stream())

    async def _compute_clip(self, page) -> dict:
        wid = _find_browser_wid(self._target.cdp_url)
        wx = wy = 0
        if wid is not None:
            try:
                out = subprocess.check_output(
                    ["xdotool", "getwindowgeometry", "--shell", str(wid)],
                    stderr=subprocess.DEVNULL, text=True,
                )
                for line in out.splitlines():
                    if line.startswith("X="):
                        wx = int(line.split("=", 1)[1])
                    elif line.startswith("Y="):
                        wy = int(line.split("=", 1)[1])
            except Exception:
                pass

        info = await page.evaluate(
            "() => ({dpr: window.devicePixelRatio, chromeH: window.outerHeight - window.innerHeight})"
        )
        dpr = info.get("dpr", 1.0)
        chrome_h_css = info.get("chromeH", 0)

        r = self._region
        clip_x = max(0, (r.x - wx) / dpr)
        clip_y = max(0, (r.y - wy) / dpr - chrome_h_css)
        clip_w = r.w / dpr
        clip_h = r.h / dpr
        return {"x": clip_x, "y": clip_y, "width": clip_w, "height": clip_h}

    async def _cdp_stream(self) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(self._target.cdp_url)

            page = None
            tab_url = getattr(self._target, "tab_url", None)
            for ctx in browser.contexts:
                for p in ctx.pages:
                    try:
                        if p._impl_obj._guid == self._target.tab_id:
                            page = p
                            break
                    except Exception:
                        pass
                    if tab_url and tab_url in p.url:
                        page = p
                if page:
                    break

            if page is None and browser.contexts and browser.contexts[0].pages:
                page = browser.contexts[0].pages[0]

            if page is None:
                return

            clip = await self._compute_clip(page)
            interval = 1.0 / self._fps

            while self._running:
                try:
                    png = await page.screenshot(clip=clip, type="png")
                    img = QImage.fromData(png)
                    if not img.isNull() and self._running:
                        self.frame_ready.emit(img.copy())
                except Exception:
                    break
                await asyncio.sleep(interval)
