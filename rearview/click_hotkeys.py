from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from pynput import keyboard

from rearview.click_executor import get_click_executor
from rearview.click_store import ClickChain, get_click_store
from rearview.target_selector import get_session

logger = logging.getLogger(__name__)


def _resolve_target_key() -> str:
    target = get_session().current
    if target is None:
        return ""
    if target.type == "browser_tab":
        return target.tab_url or ""
    return target.display_name


class ClickHotkeyManager:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self._listener: Optional[keyboard.GlobalHotKeys] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Build and start the GlobalHotKeys listener in a daemon thread."""
        with self._lock:
            self._start_locked()

    def stop(self) -> None:
        """Stop the listener."""
        with self._lock:
            self._stop_locked()

    def reload(self, chains: list[ClickChain] | None = None) -> None:
        """Stop current listener, rebuild from store (or provided chains), restart."""
        with self._lock:
            self._stop_locked()
            self._start_locked(chains=chains)

    # ------------------------------------------------------------------
    # Internal helpers (must be called with _lock held)
    # ------------------------------------------------------------------

    def _stop_locked(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
            logger.info("ClickHotkeyManager: stopped")

    def _start_locked(self, chains: list[ClickChain] | None = None) -> None:
        hotkey_map = self._build_hotkey_map(chains)
        if not hotkey_map:
            logger.debug("ClickHotkeyManager: no hotkeys to register")
            return

        listener = keyboard.GlobalHotKeys(hotkey_map)
        listener.daemon = True

        t = threading.Thread(target=listener.run, daemon=True, name="click-hotkey-manager")
        t.start()

        self._listener = listener
        logger.info("ClickHotkeyManager: started — hotkeys=%r", list(hotkey_map.keys()))

    def _build_hotkey_map(self, chains: list[ClickChain] | None) -> dict[str, callable]:
        store = get_click_store()
        executor = get_click_executor(self.loop)

        if chains is None:
            chains = store.all_chains()

        hotkey_map: dict[str, callable] = {}

        for chain in chains:
            if not chain.hotkey:
                continue

            def _make_trigger(c: ClickChain) -> callable:
                def _trigger() -> None:
                    target_key = _resolve_target_key()
                    dots = store.dots_for_chain(target_key, c)
                    logger.info("ClickHotkeyManager: hotkey %r → chain %r", c.hotkey, c.name)
                    asyncio.run_coroutine_threadsafe(
                        executor.run_chain(c, dots), self.loop
                    )

                return _trigger

            hotkey_map[chain.hotkey] = _make_trigger(chain)

        return hotkey_map


_manager: ClickHotkeyManager | None = None


def get_click_hotkey_manager(loop: asyncio.AbstractEventLoop) -> ClickHotkeyManager:
    global _manager
    if _manager is None:
        _manager = ClickHotkeyManager(loop)
    return _manager
