from __future__ import annotations

import asyncio
import threading
import time
import logging
from datetime import datetime, timezone
from typing import Any

from pynput import mouse, keyboard

from rearview.macro_store import MacroStep, Macro, save_macro
from rearview.controller import get_controller
from rearview.target_selector import get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# pynput Key → xdotool/Playwright key name map
# ---------------------------------------------------------------------------

_KEY_MAP: dict[keyboard.Key, str] = {
    keyboard.Key.enter: "Return",
    keyboard.Key.tab: "Tab",
    keyboard.Key.esc: "Escape",
    keyboard.Key.space: "space",
    keyboard.Key.backspace: "BackSpace",
    keyboard.Key.delete: "Delete",
    keyboard.Key.up: "Up",
    keyboard.Key.down: "Down",
    keyboard.Key.left: "Left",
    keyboard.Key.right: "Right",
    keyboard.Key.home: "Home",
    keyboard.Key.end: "End",
    keyboard.Key.page_up: "Page_Up",
    keyboard.Key.page_down: "Page_Down",
    keyboard.Key.insert: "Insert",
    keyboard.Key.f1: "F1",
    keyboard.Key.f2: "F2",
    keyboard.Key.f3: "F3",
    keyboard.Key.f4: "F4",
    keyboard.Key.f5: "F5",
    keyboard.Key.f6: "F6",
    keyboard.Key.f7: "F7",
    keyboard.Key.f8: "F8",
    keyboard.Key.f9: "F9",
    keyboard.Key.f10: "F10",
    keyboard.Key.f11: "F11",
    keyboard.Key.f12: "F12",
    keyboard.Key.ctrl_l: "ctrl",
    keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt_l: "alt",
    keyboard.Key.alt_r: "alt",
    keyboard.Key.shift_l: "shift",
    keyboard.Key.shift_r: "shift",
    keyboard.Key.caps_lock: "Caps_Lock",
    keyboard.Key.print_screen: "Print",
    keyboard.Key.scroll_lock: "Scroll_Lock",
    keyboard.Key.pause: "Pause",
    keyboard.Key.num_lock: "Num_Lock",
    keyboard.Key.media_play_pause: "XF86AudioPlay",
    keyboard.Key.media_volume_up: "XF86AudioRaiseVolume",
    keyboard.Key.media_volume_down: "XF86AudioLowerVolume",
    keyboard.Key.media_volume_mute: "XF86AudioMute",
}

_BUTTON_MAP: dict[Any, str] = {
    mouse.Button.left: "left",
    mouse.Button.right: "right",
    mouse.Button.middle: "middle",
}

# JavaScript injected into the browser page to capture semantic events.
_CDP_JS_INJECT = """
window.__st_recorded_events = [];
window.__st_record_handler = function(e) {
    var el = e.target;
    var info = {
        type: e.type,
        x: e.clientX || 0,
        y: e.clientY || 0,
        selector: el.id ? '#'+el.id : el.className ? '.'+el.className.trim().split(' ')[0] : el.tagName.toLowerCase(),
        text: (el.innerText || el.value || el.placeholder || '').trim().substring(0, 100),
        role: el.getAttribute('role') || el.tagName.toLowerCase(),
        url: window.location.href,
        input_value: el.value || '',
        focused: document.activeElement ? document.activeElement.tagName : '',
        t: Date.now()
    };
    window.__st_recorded_events.push(info);
};
['click','input','change','keydown'].forEach(function(ev) {
    document.addEventListener(ev, window.__st_record_handler, true);
});
"""

_CDP_JS_CLEANUP = """
['click','input','change','keydown'].forEach(function(ev) {
    document.removeEventListener(ev, window.__st_record_handler, true);
});
window.__st_recorded_events = [];
"""

_CDP_JS_RETRIEVE = "window.__st_recorded_events"


class Recorder:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self._recording = False
        self._steps: list[MacroStep] = []
        self._steps_lock = threading.Lock()
        self._start_time: float = 0.0
        self._mouse_listener: mouse.Listener | None = None
        self._keyboard_listener: keyboard.Listener | None = None
        self._last_pos: tuple[int, int] = (0, 0)
        # Wall-clock timestamp (epoch ms) at the moment recording started,
        # used for correlating with CDP event timestamps (Date.now() in JS).
        self._start_wall_ms: float = 0.0
        self._cdp_injected: bool = False

    # ── Public API ───────────────────────────────────────────────────

    def start(self) -> None:
        """Begin dual-track recording."""
        if self._recording:
            logger.warning("Recorder.start() called while already recording")
            return

        self._steps = []
        self._start_time = time.monotonic()
        self._start_wall_ms = time.time() * 1000.0
        self._recording = True
        self._cdp_injected = False

        # Start pynput listeners (run in their own daemon threads).
        self._mouse_listener = mouse.Listener(
            on_click=self._on_click,
            on_move=self._on_move,
            on_scroll=self._on_scroll,
        )
        self._mouse_listener.start()

        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._keyboard_listener.start()

        # Inject CDP JS listener if a browser page is connected.
        ctrl = get_controller()
        if ctrl.current_page is not None:
            future = asyncio.run_coroutine_threadsafe(
                self._inject_cdp_listener(), self.loop
            )
            # Fire-and-forget; log errors but don't block start().
            future.add_done_callback(self._cdp_inject_done)

        logger.debug("Recorder started")

    def stop(self) -> list[MacroStep]:
        """Stop recording and return a copy of captured steps (with semantic data merged)."""
        if not self._recording:
            logger.warning("Recorder.stop() called while not recording")
            return list(self._steps)

        self._recording = False

        # Stop pynput listeners.
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
            self._keyboard_listener = None

        # Retrieve and merge CDP events synchronously from the calling thread.
        semantic_events: list[dict] = []
        if self._cdp_injected:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._retrieve_cdp_events(), self.loop
                )
                semantic_events = future.result(timeout=5.0) or []
            except Exception:
                logger.exception("Failed to retrieve CDP events on stop")

        with self._steps_lock:
            steps_copy = list(self._steps)

        if semantic_events:
            steps_copy = self._merge_semantic(steps_copy, semantic_events)

        logger.debug("Recorder stopped: %d steps captured", len(steps_copy))
        return steps_copy

    def is_recording(self) -> bool:
        return self._recording

    # ── pynput callbacks (run in pynput threads) ─────────────────────

    def _on_click(
        self,
        x: int,
        y: int,
        button: mouse.Button,
        pressed: bool,
    ) -> None:
        if not self._recording or not pressed:
            return
        t = time.monotonic() - self._start_time
        btn_name = _BUTTON_MAP.get(button, "left")
        step = MacroStep(
            t=t,
            raw={"type": "click", "x": x, "y": y, "button": btn_name},
        )
        with self._steps_lock:
            self._steps.append(step)

    def _on_move(self, x: int, y: int) -> None:
        # Only update the tracked position; actual move steps are emitted
        # just-in-time before a click in playback if needed. Recording move
        # events verbatim would flood the log; we intentionally skip them.
        self._last_pos = (x, y)

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._recording:
            return
        t = time.monotonic() - self._start_time
        step = MacroStep(
            t=t,
            raw={"type": "scroll", "x": x, "y": y, "delta_x": dx, "delta_y": dy},
        )
        with self._steps_lock:
            self._steps.append(step)

    def _on_key_press(self, key: keyboard.Key | keyboard.KeyCode) -> None:
        if not self._recording:
            return
        t = time.monotonic() - self._start_time
        key_name = self._resolve_key(key)
        if key_name is None:
            return
        step = MacroStep(
            t=t,
            raw={"type": "key", "key": key_name},
        )
        with self._steps_lock:
            self._steps.append(step)

    def _on_key_release(
        self, key: keyboard.Key | keyboard.KeyCode
    ) -> None:
        # Key releases are intentionally not recorded (too noisy).
        pass

    # ── Key resolution ────────────────────────────────────────────────

    @staticmethod
    def _resolve_key(key: keyboard.Key | keyboard.KeyCode) -> str | None:
        """Convert a pynput key to an xdotool/Playwright key name.

        Returns None for modifier-only keys that should not be recorded as
        standalone steps (ctrl, alt, shift).
        """
        if isinstance(key, keyboard.KeyCode):
            # Printable character.
            if key.char is not None:
                return key.char
            # VK-only key with no char — skip.
            return None

        # Special key — look up in the map.
        mapped = _KEY_MAP.get(key)
        if mapped is None:
            # Unknown special key; fall back to the key's name attribute.
            try:
                name = key.name  # type: ignore[attr-defined]
                return str(name)
            except AttributeError:
                return None

        # Skip standalone modifier press events (they appear in combos instead).
        if mapped in ("ctrl", "alt", "shift"):
            return None

        return mapped

    # ── CDP helpers (run in asyncio loop) ────────────────────────────

    async def _inject_cdp_listener(self) -> None:
        ctrl = get_controller()
        if ctrl.current_page is None:
            return
        try:
            await ctrl.current_page.evaluate(_CDP_JS_INJECT)
            self._cdp_injected = True
            logger.debug("CDP JS listener injected")
        except Exception:
            logger.exception("Failed to inject CDP JS listener")

    async def _retrieve_cdp_events(self) -> list[dict]:
        ctrl = get_controller()
        if ctrl.current_page is None:
            return []
        try:
            events = await ctrl.current_page.evaluate(_CDP_JS_RETRIEVE)
            await ctrl.current_page.evaluate(_CDP_JS_CLEANUP)
            self._cdp_injected = False
            if isinstance(events, list):
                return events
            return []
        except Exception:
            logger.exception("Failed to retrieve/cleanup CDP events")
            return []

    def _cdp_inject_done(self, future: "asyncio.Future[None]") -> None:
        exc = future.exception()
        if exc is not None:
            logger.error("CDP injection future raised: %s", exc)

    # ── Merge raw + semantic ──────────────────────────────────────────

    def _merge_semantic(
        self,
        steps: list[MacroStep],
        semantic_events: list[dict],
    ) -> list[MacroStep]:
        """Attach the closest-in-time semantic event (within 200 ms) to each step."""
        if not semantic_events:
            return steps

        # Convert CDP Date.now() timestamps to the same monotonic offset used
        # by raw steps. CDP timestamps are absolute epoch ms; _start_wall_ms
        # is the epoch ms at recording start.
        def cdp_t(ev: dict) -> float:
            return (ev.get("t", 0) - self._start_wall_ms) / 1000.0

        for step in steps:
            raw_type = step.raw.get("type")
            if raw_type not in ("click", "key"):
                continue

            best: dict | None = None
            best_delta = float("inf")

            for ev in semantic_events:
                delta = abs(cdp_t(ev) - step.t)
                if delta < best_delta and delta <= 0.200:
                    best_delta = delta
                    best = ev

            if best is not None:
                step.semantic = {
                    "type": best.get("type"),
                    "selector": best.get("selector"),
                    "text": best.get("text"),
                    "role": best.get("role"),
                    "url": best.get("url"),
                    "input_value": best.get("input_value"),
                    "focused_element": best.get("focused"),
                }

        return steps


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def create_macro_from_steps(
    steps: list[MacroStep],
    name: str,
    target_hint: str = "",
) -> Macro:
    """Build a Macro from recorded steps."""
    session = get_session()
    target_type = "headless"
    if session.current is not None:
        target_type = session.current.type

    return Macro(
        name=name,
        target_type=target_type,
        target_hint=target_hint,
        created_at=datetime.now(timezone.utc).isoformat(),
        steps=steps,
    )


_recorder: Recorder | None = None


def get_recorder(loop: asyncio.AbstractEventLoop) -> Recorder:
    global _recorder
    if _recorder is None:
        _recorder = Recorder(loop)
    return _recorder
