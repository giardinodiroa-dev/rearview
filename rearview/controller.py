from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
from typing import Any

from playwright.async_api import async_playwright, Browser, Page, Playwright

from rearview.window_discovery import WindowTarget
from rearview.target_selector import TargetSession, get_session
from rearview.config import get_config
from rearview.macro_store import Macro, MacroStep

logger = logging.getLogger(__name__)


def _xdotool(*args: str) -> subprocess.CompletedProcess[str]:
    """Run xdotool with the given args and return the CompletedProcess."""
    return subprocess.run(
        ["xdotool", *args],
        capture_output=True,
        text=True,
        timeout=5,
    )


def _x11_find_child_at(win, x: int, y: int):
    """Walk the X11 window tree to find the deepest child containing (x, y).

    Returns (child_window, child_x, child_y) where coords are relative to the
    returned child. Sending events to a deep child avoids the top-level window
    calling _NET_ACTIVE_WINDOW which would cause KWin to switch virtual desktops.
    """
    try:
        children = win.query_tree().children
    except Exception:
        return win, x, y
    for child in reversed(children):  # reversed = topmost z-order first
        try:
            geom = child.get_geometry()
            if geom.x <= x < geom.x + geom.width and geom.y <= y < geom.y + geom.height:
                return _x11_find_child_at(child, x - geom.x, y - geom.y)
        except Exception:
            continue
    return win, x, y


def _x11_send_click(window_id: int, abs_x: int, abs_y: int) -> None:
    """Inject a left-click into a window via XSendEvent — no mouse movement, no focus change,
    no virtual desktop switch. Targets the deepest child widget to avoid top-level WM activation.
    """
    try:
        from Xlib import X, display as xdisplay
        from Xlib.protocol import event as xevent

        d = xdisplay.Display()
        root = d.screen().root
        top_win = d.create_resource_object("window", window_id)

        # Get top-level window's screen position
        coords = root.translate_coords(top_win, 0, 0)
        wx, wy = coords.x, coords.y
        win_x = abs_x - wx
        win_y = abs_y - wy

        # Walk the tree to find the deepest child at these coords — child widgets
        # don't call _NET_ACTIVE_WINDOW so KWin won't switch virtual desktops
        target_win, evt_x, evt_y = _x11_find_child_at(top_win, win_x, win_y)

        common = dict(
            time=X.CurrentTime,
            root=root,
            window=target_win,
            child=X.NONE,
            root_x=abs_x,
            root_y=abs_y,
            event_x=evt_x,
            event_y=evt_y,
            same_screen=True,
        )
        # event_mask=0 sends directly to the creating client, bypassing WM event selection
        press = xevent.ButtonPress(detail=1, state=0, **common)
        release = xevent.ButtonRelease(detail=1, state=X.Button1Mask, **common)
        target_win.send_event(press, event_mask=0, propagate=False)
        target_win.send_event(release, event_mask=0, propagate=False)
        d.sync()
    except Exception:
        logger.exception("_x11_send_click failed for window %d", window_id)


class Controller:
    def __init__(self, session: TargetSession) -> None:
        self._session = session
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._target: WindowTarget | None = None
        # Capture the running loop at init time; fall back to None if no loop yet.
        try:
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None
        self._session.on_change(self._on_target_changed)

    # ── Connection ──────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to the current session target. Returns True on success."""
        target = self._session.current
        if target is None:
            logger.warning("connect() called but no target set in session")
            return False

        # Tear down any existing CDP connection before re-connecting.
        await self.disconnect()
        self._target = target

        if target.type == "browser_tab":
            return await self._connect_browser_tab(target)
        elif target.type == "headless":
            return await self._connect_headless(target)
        elif target.type == "x11_app":
            return await self._connect_x11(target)
        else:
            logger.error("Unknown target type: %s", target.type)
            return False

    async def _connect_browser_tab(self, target: WindowTarget) -> bool:
        """Attach to an existing browser via CDP."""
        if not target.cdp_url:
            logger.error("browser_tab target has no cdp_url")
            return False
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(
                target.cdp_url
            )
            # Find the matching page by tab_id or tab_url.
            self._page = self._find_page(target)
            if self._page is None:
                logger.warning(
                    "Could not find matching page for tab_id=%s tab_url=%s; "
                    "using first available page",
                    target.tab_id,
                    target.tab_url,
                )
                contexts = self._browser.contexts
                if contexts and contexts[0].pages:
                    self._page = contexts[0].pages[0]
            if self._page is None:
                logger.error("No pages found in connected browser")
                await self.disconnect()
                return False
            logger.info("Connected via CDP to %s", target.cdp_url)
            return True
        except Exception:
            logger.exception("Failed to connect via CDP to %s", target.cdp_url)
            await self.disconnect()
            return False

    def _find_page(self, target: WindowTarget) -> Page | None:
        """Search connected browser contexts for the page matching target."""
        if self._browser is None:
            return None
        for ctx in self._browser.contexts:
            for page in ctx.pages:
                # Match by CDP target ID if available.
                if target.tab_id and hasattr(page, "_impl_obj"):
                    try:
                        if page._impl_obj._guid == target.tab_id:
                            return page
                    except AttributeError:
                        pass
                # Match by URL substring.
                if target.tab_url and target.tab_url in page.url:
                    return page
        return None

    async def _connect_headless(self, target: WindowTarget) -> bool:
        """Launch Xvfb + Chromium via DesktopManager, then attach via CDP."""
        try:
            from rearview.desktop import get_desktop_manager

            dm = get_desktop_manager()
            if not dm.is_running():
                await dm.start()
            cdp_url = dm.debug_url
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            contexts = self._browser.contexts
            if contexts and contexts[0].pages:
                self._page = contexts[0].pages[0]
            else:
                # Open a blank page if none exists yet.
                ctx = contexts[0] if contexts else await self._browser.new_context()
                self._page = await ctx.new_page()
            logger.info("Connected to headless browser at %s", cdp_url)
            return True
        except Exception:
            logger.exception("Failed to start/connect headless browser")
            await self.disconnect()
            return False

    async def _connect_x11(self, target: WindowTarget) -> bool:
        """Verify the X11 window exists; no CDP needed."""
        if target.window_id is None:
            logger.error("x11_app target has no window_id")
            return False
        try:
            result = _xdotool("getwindowname", str(target.window_id))
            if result.returncode == 0:
                logger.info(
                    "Connected to X11 window %d (%s)",
                    target.window_id,
                    result.stdout.strip(),
                )
                return True
            logger.error(
                "Window %d not found (xdotool exit %d)",
                target.window_id,
                result.returncode,
            )
            return False
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            logger.exception("xdotool not available or window check failed")
            return False

    async def disconnect(self) -> None:
        """Close CDP connection if open."""
        self._page = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                logger.debug("Error closing browser", exc_info=True)
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("Error stopping playwright", exc_info=True)
            self._playwright = None

    def _on_target_changed(self, new_target: WindowTarget) -> None:
        """Session target switched — schedule reconnect."""
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_event_loop()
                self._loop = loop
            except RuntimeError:
                logger.warning("_on_target_changed called but no event loop available")
                return
        if loop.is_running():
            asyncio.ensure_future(self.connect(), loop=loop)
        else:
            logger.debug(
                "Event loop not running during target change; reconnect deferred"
            )

    @property
    def is_connected(self) -> bool:
        """True if ready to accept commands."""
        if self._target is None:
            return False
        if self._target.type in ("browser_tab", "headless"):
            return self._page is not None
        if self._target.type == "x11_app":
            return self._target.window_id is not None
        return False

    @property
    def current_page(self) -> Page | None:
        """The active Playwright page, if browser target."""
        return self._page

    # ── Unified control API ─────────────────────────────────────────

    async def click(self, x: int, y: int) -> None:
        """Click at screen coordinates."""
        if self._is_browser_target():
            assert self._page is not None
            await self._page.mouse.click(x, y)
        else:
            await self.focus_window()
            _xdotool("mousemove", str(x), str(y), "click", "1")

    async def background_click(self, x: int, y: int) -> None:
        """Click at absolute screen coordinates without focus steal or desktop switch.

        For browser_tab targets with an active Playwright page, dispatches the click
        via CDP (page.mouse.click) using computed viewport coordinates.
        For all other targets, falls back to XSendEvent via _x11_send_click.
        """
        if self._target is None:
            return

        if self._target.type == "browser_tab" and self._page is not None:
            # Resolve the browser window position so we can convert abs → viewport coords.
            wx, wy = 0, 0
            try:
                port = int(self._target.cdp_url.split(":")[-1])
                ss_result = subprocess.run(
                    ["ss", "-tlnp", f"sport = :{port}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                pid_match = re.search(r"pid=(\d+)", ss_result.stdout)
                if pid_match:
                    pid = pid_match.group(1)
                    xdo_result = _xdotool("search", "--pid", pid)
                    if xdo_result.returncode == 0:
                        lines = [ln.strip() for ln in xdo_result.stdout.splitlines() if ln.strip()]
                        if lines:
                            try:
                                wid = int(lines[0])
                                geom_result = _xdotool("getwindowgeometry", "--shell", str(wid))
                                if geom_result.returncode == 0:
                                    for line in geom_result.stdout.splitlines():
                                        if line.startswith("X="):
                                            wx = int(line.split("=", 1)[1])
                                        elif line.startswith("Y="):
                                            wy = int(line.split("=", 1)[1])
                            except (ValueError, IndexError):
                                pass
            except Exception:
                logger.debug("background_click: could not resolve browser window position", exc_info=True)

            try:
                info = await self._page.evaluate(
                    "() => ({dpr: window.devicePixelRatio, chromeH: window.outerHeight - window.innerHeight})"
                )
            except Exception:
                info = {}

            dpr = info.get("dpr", 1.0)
            chrome_h = info.get("chromeH", 0)
            vx = (x - wx) / dpr
            vy = (y - wy) / dpr - chrome_h
            await self._page.mouse.click(max(0.0, vx), max(0.0, vy))
            return

        # XSendEvent path for x11_app targets (and browser_tab with no active page).
        wid = self._target.window_id
        if wid is None and self._target.tab_title:
            # browser_tab targets don't store window_id — resolve via title
            raw = _xdotool("search", "--name", self._target.tab_title)
            if raw:
                try:
                    wid = int(raw.splitlines()[0].strip())
                except ValueError:
                    pass
        if wid is None:
            logger.warning("background_click: no window_id for target %s", self._target.display_name)
            return
        _x11_send_click(wid, x, y)

    async def type(self, text: str) -> None:
        """Type text into the focused element."""
        if self._is_browser_target():
            assert self._page is not None
            await self._page.keyboard.type(text)
        else:
            await self.focus_window()
            _xdotool("type", "--clearmodifiers", text)

    async def key(self, key: str) -> None:
        """Press a key or key combo e.g. 'Return', 'ctrl+c'."""
        if self._is_browser_target():
            assert self._page is not None
            await self._page.keyboard.press(key)
        else:
            await self.focus_window()
            _xdotool("key", key)

    async def focus_window(self) -> None:
        """Bring the target window to front."""
        if self._is_browser_target():
            # Already focused via CDP — nothing to do.
            return
        if self._target is not None and self._target.window_id is not None:
            wid = str(self._target.window_id)
            _xdotool("windowfocus", "--sync", wid)
            _xdotool("windowraise", wid)

    async def screenshot(self) -> bytes | None:
        """Take a screenshot of the target. Returns PNG bytes."""
        if self._is_browser_target():
            if self._page is None:
                return None
            return await self._page.screenshot()
        else:
            # Use scrot to capture the focused window, fall back to import.
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = tmp.name
                result = subprocess.run(
                    ["scrot", "-u", tmp_path],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    with open(tmp_path, "rb") as fh:
                        data = fh.read()
                    os.unlink(tmp_path)
                    return data
                os.unlink(tmp_path)
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            # Fallback: ImageMagick import
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp_path = tmp.name
                result = subprocess.run(
                    ["import", "-window", "root", tmp_path],
                    capture_output=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    with open(tmp_path, "rb") as fh:
                        data = fh.read()
                    os.unlink(tmp_path)
                    return data
                os.unlink(tmp_path)
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
                pass
            logger.error("screenshot: neither scrot nor import (ImageMagick) succeeded")
            return None

    async def run_js(self, script: str) -> Any:
        """Run JavaScript. Only works on browser targets. Raises RuntimeError on X11."""
        if not self._is_browser_target():
            raise RuntimeError("run_js not available for X11 app targets")
        assert self._page is not None
        return await self._page.evaluate(script)

    async def get_dom(self) -> str | None:
        """Get full page HTML. Browser only, returns None for X11."""
        if not self._is_browser_target():
            return None
        if self._page is None:
            return None
        return await self._page.content()

    # ── Browser-specific helpers ────────────────────────────────────

    async def navigate(self, url: str) -> None:
        """Navigate browser to URL. No-op for X11."""
        if not self._is_browser_target():
            return
        if self._page is None:
            return
        await self._page.goto(url)

    async def wait_for_selector(self, selector: str, timeout: int = 5000) -> bool:
        """Wait for a CSS selector to appear. Browser only. Returns False for X11."""
        if not self._is_browser_target():
            return False
        if self._page is None:
            return False
        try:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception:
            return False

    async def evaluate_handle(self, script: str) -> Any:
        """page.evaluate_handle wrapper. Browser only."""
        if not self._is_browser_target():
            raise RuntimeError("evaluate_handle not available for X11 app targets")
        if self._page is None:
            raise RuntimeError("evaluate_handle: no active page")
        return await self._page.evaluate_handle(script)

    # ── Internal helpers ────────────────────────────────────────────

    def _is_browser_target(self) -> bool:
        """Return True if the current target uses CDP (browser_tab or headless)."""
        return (
            self._target is not None
            and self._target.type in ("browser_tab", "headless")
        )

    # ── Macro playback ──────────────────────────────────────────────

    async def play_macro(
        self,
        macro: Macro,
        log_callback: callable = None,
        speed: float = 1.0,
    ) -> None:
        """
        Play back a recorded macro against the current target.

        - Tries semantic action first (more reliable), falls back to raw coords.
        - Calls log_callback(label) for each step that has a non-empty label.
        - speed: 1.0 = real time, 2.0 = double speed, 0.5 = half speed.
        - Never raises on individual step failure — logs warning and continues.
        """
        if not macro.steps:
            return

        prev_t = 0.0
        for step in macro.steps:
            # Wait for the inter-step delay (scaled by speed)
            delay = (step.t - prev_t) / speed
            if delay > 0:
                await asyncio.sleep(delay)
            prev_t = step.t

            # Emit log if step is labeled
            if step.label and log_callback:
                try:
                    log_callback(step.label)
                except Exception:
                    pass

            # Execute the step
            try:
                await self._execute_step(step)
            except Exception as exc:
                logger.warning("play_macro: step %s failed: %s", step.t, exc)

    async def _execute_step(self, step: MacroStep) -> None:
        """Try semantic execution first, fall back to raw."""
        raw = step.raw
        semantic = step.semantic
        action_type = raw.get("type", "")

        if action_type == "click":
            # Semantic path: find element by selector and click via JS
            if semantic and semantic.get("selector") and self._page:
                clicked = await self._try_semantic_click(semantic)
                if clicked:
                    return
            # Raw fallback
            await self.click(raw.get("x", 0), raw.get("y", 0))

        elif action_type == "key":
            key = raw.get("key", "")
            if key:
                await self.key(key)

        elif action_type == "scroll":
            # Playwright: page.mouse.wheel(delta_x, delta_y)
            # xdotool: xdotool click --window {id} 4 or 5 (scroll up/down)
            dx = raw.get("delta_x", 0)
            dy = raw.get("delta_y", 0)
            if self._page:
                x = raw.get("x", 0)
                y = raw.get("y", 0)
                await self._page.mouse.move(x, y)
                await self._page.mouse.wheel(dx, dy)
            else:
                # xdotool scroll: button 4=up, 5=down
                btn = "5" if dy < 0 else "4"
                wid = self._session.current.window_id if self._session.current else None
                cmd = ["xdotool", "click"]
                if wid:
                    cmd += ["--window", str(wid)]
                cmd.append(btn)
                subprocess.run(cmd, capture_output=True)

    async def _try_semantic_click(self, semantic: dict) -> bool:
        """Try to click an element by selector. Returns True if successful."""
        selector = semantic.get("selector", "")
        text = semantic.get("text", "")
        if not selector or not self._page:
            return False
        try:
            # Try exact selector first
            el = await self._page.query_selector(selector)
            if el is None and text:
                # Fallback: find by text content
                el = await self._page.query_selector(f"text={text}")
            if el is None:
                return False
            await el.click(timeout=2000)
            return True
        except Exception:
            return False


# ── Module-level singleton ──────────────────────────────────────────

_controller: Controller | None = None


def get_controller() -> Controller:
    global _controller
    if _controller is None:
        _controller = Controller(get_session())
    return _controller
