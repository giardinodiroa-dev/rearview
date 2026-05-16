from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from pynput import keyboard
from pynput.keyboard import Key, KeyCode

from rearview.config import get_config

logger = logging.getLogger(__name__)


class HotkeyManager:
    """Global hotkey listener that runs pynput listeners in a daemon thread.

    Two listeners run concurrently:
      - GlobalHotKeys listener: fires on Super+S (configurable via settings)
        regardless of disposition mode.
      - A regular keyboard.Listener: fires on single-letter keys only when
        self._disposition_mode is True, mapping letters to disposition labels.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

        self._global_listener: Optional[keyboard.GlobalHotKeys] = None
        self._letter_listener: Optional[keyboard.Listener] = None
        self._thread: Optional[threading.Thread] = None

        self._disposition_mode: bool = False
        self._disposition_callback: Optional[callable] = None  # async fn(label: str)
        self._next_callback: Optional[callable] = None         # async fn()

        # Map of lowercase hotkey letter → disposition label, populated from config
        self._letter_map: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start both pynput listeners in a daemon thread."""
        cfg = get_config()

        # Build letter → label map from config dispositions
        self._letter_map = {}
        for disp in cfg.dispositions:
            if disp.hotkey and len(disp.hotkey) == 1:
                self._letter_map[disp.hotkey.lower()] = disp.label

        hotkey_next = cfg.workflow.hotkey_next  # e.g. "<super>s"

        def _run_listeners() -> None:
            # Build the GlobalHotKeys map for Super+S
            global_hotkeys = {
                hotkey_next: self._trigger_next,
            }

            self._global_listener = keyboard.GlobalHotKeys(global_hotkeys)
            self._global_listener.daemon = True

            # The regular listener handles per-key gating for disposition letters
            self._letter_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=None,
            )
            self._letter_listener.daemon = True

            self._global_listener.start()
            self._letter_listener.start()

            logger.info(
                "HotkeyManager: started — next=%r disposition_letters=%r",
                hotkey_next,
                list(self._letter_map.keys()),
            )

            # Block the thread until one of the listeners stops
            self._letter_listener.join()

        self._thread = threading.Thread(target=_run_listeners, daemon=True, name="hotkey-manager")
        self._thread.start()

    def stop(self) -> None:
        """Stop both pynput listeners."""
        if self._global_listener is not None:
            try:
                self._global_listener.stop()
            except Exception:
                pass
            self._global_listener = None

        if self._letter_listener is not None:
            try:
                self._letter_listener.stop()
            except Exception:
                pass
            self._letter_listener = None

        logger.info("HotkeyManager: stopped")

    def set_disposition_mode(self, active: bool) -> None:
        """Enable or disable single-letter disposition hotkeys.

        When active is True, pressing a hotkey letter triggers the
        disposition callback. When False, letter presses are ignored.
        """
        self._disposition_mode = active
        logger.debug("HotkeyManager: disposition_mode=%s", active)

    def set_disposition_callback(self, fn: callable) -> None:
        """Register an async callback: async def cb(label: str)."""
        self._disposition_callback = fn

    def set_next_callback(self, fn: callable) -> None:
        """Register an async callback: async def cb()."""
        self._next_callback = fn

    # ------------------------------------------------------------------
    # Internal trigger methods (called from pynput threads)
    # ------------------------------------------------------------------

    def _trigger_next(self) -> None:
        """Called by pynput GlobalHotKeys on Super+S.

        Thread-safe: schedules the async callback on the event loop.
        """
        logger.debug("HotkeyManager: Super+S pressed — triggering next")
        if self._next_callback is not None:
            asyncio.run_coroutine_threadsafe(self._next_callback(), self.loop)

    def _trigger_disposition(self, label: str) -> None:
        """Schedule the disposition callback with the given label on the event loop."""
        logger.debug("HotkeyManager: disposition hotkey → %r", label)
        if self._disposition_callback is not None:
            asyncio.run_coroutine_threadsafe(
                self._disposition_callback(label), self.loop
            )

    # ------------------------------------------------------------------
    # Key press handler (letter listener)
    # ------------------------------------------------------------------

    def _on_key_press(self, key) -> None:
        """Called by pynput Listener on every key press.

        Only processes single-character keys when disposition_mode is active.
        Ignores modifier keys and special keys silently.
        """
        if not self._disposition_mode:
            return

        # Extract the character from the key
        char: Optional[str] = None
        if isinstance(key, KeyCode):
            char = key.char
        elif isinstance(key, Key):
            # Special keys (shift, ctrl, etc.) — ignore
            return

        if char is None:
            return

        char_lower = char.lower()
        label = self._letter_map.get(char_lower)
        if label is not None:
            self._trigger_disposition(label)
