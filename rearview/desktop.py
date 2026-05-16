from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from xvfbwrapper import Xvfb

from rearview.config import AppConfig, get_config

logger = logging.getLogger(__name__)

_instance: Optional["DesktopManager"] = None


def get_desktop_manager() -> "DesktopManager":
    global _instance
    if _instance is None:
        _instance = DesktopManager(get_config())
    return _instance


class DesktopManager:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._xvfb: Optional[Xvfb] = None
        self._chromium_proc: Optional[asyncio.subprocess.Process] = None
        self._vnc_proc: Optional[asyncio.subprocess.Process] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start Xvfb on :99, then launch Chromium inside it."""
        display = self._config.rearview.display  # e.g. ":99"
        display_num = int(display.lstrip(":"))

        # Start Xvfb
        self._xvfb = Xvfb(width=1280, height=900, colordepth=24, display=display_num)
        self._xvfb.start()
        logger.info("Xvfb started on display %s", display)

        # Build Chromium command
        debug_port = self._config.browser.debug_port
        executable = self._config.browser.executable or "chromium"

        cmd = [
            executable,
            f"--remote-debugging-port={debug_port}",
            f"--display={display}",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--window-size=1280,900",
        ]

        env = os.environ.copy()
        env["DISPLAY"] = display

        self._chromium_proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info(
            "Chromium launched (PID %d) with debug port %d",
            self._chromium_proc.pid,
            debug_port,
        )

        await self._wait_for_debug_port(debug_port, timeout=5.0)

    async def stop(self) -> None:
        """Gracefully terminate Chromium, then stop Xvfb."""
        await self.stop_vnc()

        if self._chromium_proc is not None and self._chromium_proc.returncode is None:
            self._chromium_proc.terminate()
            try:
                await asyncio.wait_for(self._chromium_proc.wait(), timeout=5.0)
                logger.info("Chromium terminated cleanly")
            except asyncio.TimeoutError:
                self._chromium_proc.kill()
                await self._chromium_proc.wait()
                logger.warning("Chromium killed after timeout")
            self._chromium_proc = None

        if self._xvfb is not None:
            self._xvfb.stop()
            self._xvfb = None
            logger.info("Xvfb stopped")

    async def start_vnc(self, vnc_port: int = 5900) -> None:
        """Launch x11vnc on the :99 display for remote inspection."""
        if self._vnc_proc is not None and self._vnc_proc.returncode is None:
            logger.debug("x11vnc already running (PID %d)", self._vnc_proc.pid)
            return

        display = self._config.rearview.display

        cmd = [
            "x11vnc",
            "-display", display,
            "-nopw",
            "-listen", "localhost",
            "-xkb",
            "-port", str(vnc_port),
        ]

        env = os.environ.copy()
        env["DISPLAY"] = display

        self._vnc_proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        logger.info(
            "x11vnc started (PID %d) on port %d", self._vnc_proc.pid, vnc_port
        )

    async def stop_vnc(self) -> None:
        """Kill the x11vnc process if running."""
        if self._vnc_proc is not None and self._vnc_proc.returncode is None:
            self._vnc_proc.terminate()
            try:
                await asyncio.wait_for(self._vnc_proc.wait(), timeout=3.0)
                logger.info("x11vnc terminated cleanly")
            except asyncio.TimeoutError:
                self._vnc_proc.kill()
                await self._vnc_proc.wait()
                logger.warning("x11vnc killed after timeout")
        self._vnc_proc = None

    def is_running(self) -> bool:
        """Return True if both Xvfb and Chromium are alive."""
        xvfb_alive = (
            self._xvfb is not None
            and self._xvfb.proc is not None
            and self._xvfb.proc.poll() is None
        )
        chromium_alive = (
            self._chromium_proc is not None
            and self._chromium_proc.returncode is None
        )
        return xvfb_alive and chromium_alive

    @property
    def debug_url(self) -> str:
        """Returns the Chrome DevTools remote debugging URL."""
        return f"http://localhost:{self._config.browser.debug_port}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _wait_for_debug_port(self, port: int, timeout: float) -> None:
        """Probe localhost:port until it accepts connections or timeout expires."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("localhost", port), timeout=0.5
                )
                writer.close()
                await writer.wait_closed()
                logger.info("Chromium debug port %d is ready", port)
                return
            except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.2)
        logger.warning(
            "Chromium debug port %d not available after %.1f seconds", port, timeout
        )
