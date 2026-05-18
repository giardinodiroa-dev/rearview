from __future__ import annotations

import asyncio
import urllib.request
import urllib.error
import json
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from rearview.window_discovery import (
    WindowTarget,
    discover_all,
    format_target_list,
)
from rearview.config import get_config

_CDP_AUTO_PORTS = range(9222, 9231)


def _is_aloware_url(url: str | None) -> bool:
    """Return True if the URL belongs to an Aloware domain."""
    if not url:
        return False
    return "aloware.com" in url.lower()


async def _port_open(port: int, timeout: float = 0.3) -> bool:
    """Check whether localhost:port accepts a TCP connection."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
        return False


def _http_get_json(url: str, timeout: float = 2.0) -> object | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Interactive selection
# ---------------------------------------------------------------------------

async def select_target_interactive(console: Console) -> WindowTarget:
    """Present a full interactive terminal selection flow and return the chosen target."""
    while True:
        console.print("[bold blue]Scanning for open windows...[/bold blue]")
        targets = await discover_all()

        if not targets:
            console.print("[red]No windows found. Rescanning...[/red]")
            await asyncio.sleep(0.5)
            continue

        # Auto-select single Aloware browser tab
        browser_tabs = [t for t in targets if t.type == "browser_tab"]
        aloware_tabs = [t for t in browser_tabs if _is_aloware_url(t.tab_url)]
        if len(browser_tabs) == 1 and len(aloware_tabs) == 1:
            target = aloware_tabs[0]
            console.print(f"[green]Auto-selected:[/green] {target.display_name}")
            return target

        formatted = format_target_list(targets)
        console.print(Panel(Text.from_markup(formatted), title="Select Agent Target", border_style="cyan"))

        raw = Prompt.ask("Enter number (or press Enter to rescan)", default="", show_default=False)

        if raw.strip() == "":
            continue

        try:
            choice = int(raw.strip())
        except ValueError:
            console.print(f"[red]Invalid input:[/red] {raw!r} — enter a number or press Enter to rescan.")
            continue

        # Build an ordered list matching the displayed numbers (counter starts at 1)
        ordered = (
            [t for t in targets if t.type in ("browser_window", "browser_tab")]
            + [t for t in targets if t.type == "x11_app"]
            + [t for t in targets if t.type == "headless"]
        )

        if 1 <= choice <= len(ordered):
            return ordered[choice - 1]

        console.print(f"[red]Number out of range.[/red] Enter 1–{len(ordered)} or press Enter to rescan.")


# ---------------------------------------------------------------------------
# Auto-detection (non-interactive)
# ---------------------------------------------------------------------------

async def select_target_auto(console: Console) -> WindowTarget | None:
    """Non-interactive auto-detection. Returns a target only when unambiguous."""
    open_ports = await asyncio.gather(*(_port_open(p) for p in _CDP_AUTO_PORTS))

    aloware_targets: list[WindowTarget] = []

    for port, is_open in zip(_CDP_AUTO_PORTS, open_ports):
        if not is_open:
            continue

        base_url = f"http://localhost:{port}"
        tabs_data = _http_get_json(f"{base_url}/json")
        if not isinstance(tabs_data, list):
            continue

        for tab in tabs_data:
            if not isinstance(tab, dict):
                continue
            if tab.get("type") != "page":
                continue
            tab_url: str = tab.get("url", "")
            if _is_aloware_url(tab_url):
                aloware_targets.append(
                    WindowTarget(
                        id=f"browser_{port}_{tab.get('id', '')}",
                        type="browser_tab",
                        display_name=f"Browser — {tab.get('title', tab_url)}",
                        app_name="Browser",
                        window_id=None,
                        cdp_url=base_url,
                        tab_url=tab_url or None,
                        tab_title=tab.get("title") or None,
                        tab_id=tab.get("id") or None,
                        pid=None,
                        icon="\U0001f310",
                    )
                )

    if len(aloware_targets) == 1:
        return aloware_targets[0]

    # Zero or multiple: cannot auto-select
    return None


# ---------------------------------------------------------------------------
# Session container
# ---------------------------------------------------------------------------

class TargetSession:
    """Holds the currently selected target and notifies registered listeners on change."""

    def __init__(self) -> None:
        self._current: WindowTarget | None = None
        self._listeners: list[Callable[[WindowTarget], None]] = []

    @property
    def current(self) -> WindowTarget | None:
        return self._current

    def set(self, target: WindowTarget) -> None:
        """Update the current target and notify all registered listeners."""
        self._current = target
        for fn in self._listeners:
            fn(target)

    def on_change(self, fn: Callable[[WindowTarget], None]) -> None:
        """Register a listener called with the new target whenever it changes."""
        self._listeners.append(fn)

    def is_browser(self) -> bool:
        return self._current is not None and self._current.type in ("browser_tab", "browser_window", "headless")

    def is_x11(self) -> bool:
        return self._current is not None and self._current.type == "x11_app"


# ---------------------------------------------------------------------------
# Mid-session switch helper
# ---------------------------------------------------------------------------

async def prompt_switch(console: Console, session: TargetSession) -> None:
    """Re-run interactive selection and update the session. Used by CLI and toast Switch button."""
    new_target = await select_target_interactive(console)
    session.set(new_target)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_session = TargetSession()


def get_session() -> TargetSession:
    """Return the process-wide TargetSession singleton."""
    return _session
