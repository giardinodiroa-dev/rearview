from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared data structure
# ---------------------------------------------------------------------------

@dataclass
class WindowTarget:
    id: str                        # unique stable identifier
    type: str                      # "browser_tab" | "x11_app" | "headless"
    display_name: str              # shown to user e.g. "Chrome — Aloware"
    app_name: str                  # "Chrome", "VS Code", "Terminal", etc.
    window_id: Optional[int]       # X11 window ID (xdotool wid)
    cdp_url: Optional[str]         # e.g. "http://localhost:9222" if browser
    tab_url: Optional[str]         # current tab URL if browser_tab
    tab_title: Optional[str]       # tab page title if browser_tab
    tab_id: Optional[str]          # CDP target ID if browser_tab
    pid: Optional[int]             # process PID
    icon: str = ""                 # emoji icon for display: 🌐 browser, 🖥 app


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SYSTEM_WINDOW_NAMES: frozenset[str] = frozenset(
    {"", "Desktop", "xfwm4", "plank", "polybar", "xfdesktop", "panel", "Xfce Panel"}
)

_CDP_PORTS = range(9222, 9231)


def _read_proc_comm(pid: int) -> Optional[str]:
    """Read /proc/{pid}/comm for the process name."""
    try:
        with open(f"/proc/{pid}/comm") as fh:
            return fh.read().strip()
    except OSError:
        return None


def _xdotool(*args: str) -> Optional[str]:
    """Run xdotool with the given args; return stdout or None on failure."""
    try:
        result = subprocess.run(
            ["xdotool", *args],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def _find_window_id_by_name(title: str) -> Optional[int]:
    """Best-effort: find an X11 window whose name contains title."""
    if not title:
        return None
    raw = _xdotool("search", "--name", title)
    if not raw:
        return None
    # xdotool may return multiple; take the first
    first = raw.splitlines()[0].strip()
    try:
        return int(first)
    except ValueError:
        return None


def _get_window_name(wid: int) -> Optional[str]:
    return _xdotool("getwindowname", str(wid))


def _get_window_pid(wid: int) -> Optional[int]:
    raw = _xdotool("getwindowpid", str(wid))
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return None


async def _port_open(port: int, timeout: float = 0.3) -> bool:
    """Check if localhost:port accepts a TCP connection."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
        return False


def _http_get_json(url: str, timeout: float = 2.0) -> Optional[object]:
    """Fetch URL and parse JSON; returns None on any error."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def _browser_name_from_version(version_data: Optional[object]) -> str:
    """Extract a friendly browser name from /json/version payload."""
    if not isinstance(version_data, dict):
        return "Browser"
    browser_raw: str = version_data.get("Browser", "")
    # "Chrome/124.0.6367.60" → "Chrome"
    # "Brave/1.65.114" → "Brave"
    # "Firefox/..." is not CDP-standard but handle gracefully
    name_part = browser_raw.split("/")[0].strip()
    if not name_part:
        return "Browser"
    # Clean up known prefixes like "HeadlessChrome"
    if name_part.lower().startswith("headless"):
        name_part = name_part[8:].strip() or "Browser"
    return name_part


def _app_name_from_pid(pid: Optional[int], window_title: str) -> str:
    """Derive human-friendly app_name from PID or window title."""
    if pid is not None:
        comm = _read_proc_comm(pid)
        if comm:
            # Map common process names to friendlier labels
            _COMM_MAP: dict[str, str] = {
                "code": "VS Code",
                "code-oss": "VS Code",
                "codium": "VS Codium",
                "bash": "Terminal",
                "zsh": "Terminal",
                "fish": "Terminal",
                "sh": "Terminal",
                "xterm": "XTerm",
                "gnome-terminal": "Terminal",
                "konsole": "Konsole",
                "alacritty": "Alacritty",
                "kitty": "Kitty",
                "gedit": "Gedit",
                "mousepad": "Mousepad",
                "thunar": "Thunar",
                "nautilus": "Files",
                "dolphin": "Dolphin",
                "vlc": "VLC",
                "gimp": "GIMP",
                "inkscape": "Inkscape",
                "libreoffice": "LibreOffice",
                "soffice": "LibreOffice",
            }
            for key, label in _COMM_MAP.items():
                if comm.lower().startswith(key):
                    return label
            # Capitalize first letter of comm as fallback
            return comm.capitalize()
    # Last resort: extract from title "App — document" pattern
    if " — " in window_title:
        return window_title.split(" — ")[0].strip()
    if " - " in window_title:
        return window_title.split(" - ")[0].strip()
    return window_title[:24] if window_title else "Unknown"


# ---------------------------------------------------------------------------
# Discovery methods
# ---------------------------------------------------------------------------

async def discover_browser_tabs() -> list[WindowTarget]:
    """Scan CDP debug ports 9222–9230 and return one WindowTarget per page tab."""
    targets: list[WindowTarget] = []

    open_ports = await asyncio.gather(
        *(_port_open(p) for p in _CDP_PORTS)
    )

    for port, is_open in zip(_CDP_PORTS, open_ports):
        if not is_open:
            continue

        base_url = f"http://localhost:{port}"

        tabs_data = _http_get_json(f"{base_url}/json")
        version_data = _http_get_json(f"{base_url}/json/version")

        if not isinstance(tabs_data, list):
            continue

        browser_name = _browser_name_from_version(version_data)

        for tab in tabs_data:
            if not isinstance(tab, dict):
                continue
            if tab.get("type") != "page":
                continue

            tab_id: str = tab.get("id", "")
            tab_title: str = tab.get("title", "")
            tab_url: str = tab.get("url", "")

            wid: Optional[int] = None

            # Derive a short URL label for display
            url_label = _short_url(tab_url)
            display_name = f"{browser_name} — {tab_title}" if tab_title else browser_name

            targets.append(
                WindowTarget(
                    id=f"browser_{port}_{tab_id}",
                    type="browser_tab",
                    display_name=display_name,
                    app_name=browser_name,
                    window_id=wid,
                    cdp_url=base_url,
                    tab_url=tab_url or None,
                    tab_title=tab_title or None,
                    tab_id=tab_id or None,
                    pid=None,
                    icon="\U0001f310",  # 🌐
                )
            )

    return targets


def _short_url(url: str) -> str:
    """Extract hostname from URL for display."""
    if not url:
        return ""
    try:
        # Simple extraction without urllib.parse to keep it lightweight
        stripped = url.split("//")[-1]
        host = stripped.split("/")[0]
        # Remove www. prefix
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return url[:40]


def discover_x11_windows(browser_pids: Optional[set[int]] = None) -> list[WindowTarget]:
    """Discover visible X11 windows using wmctrl (one call instead of per-window)."""
    targets: list[WindowTarget] = []
    browser_pids = browser_pids or set()

    # wmctrl -lp: one call gives window_id (hex), desktop, pid, host, title
    try:
        result = subprocess.run(
            ["wmctrl", "-lp"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return targets
        lines = result.stdout.splitlines()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return targets

    for line in lines:
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        wid_hex, _desktop, pid_str, _host, name = parts
        name = name.strip()

        if not name or name in _SYSTEM_WINDOW_NAMES:
            continue

        name_lower = name.lower()
        if any(frag in name_lower for frag in ("xfwm4", "desktop", "panel", "plank", "polybar", "tray")):
            continue

        try:
            wid = int(wid_hex, 16)
            pid: Optional[int] = int(pid_str) if pid_str != "0" else None
        except ValueError:
            continue

        if pid is not None and pid in browser_pids:
            continue

        app_name = _app_name_from_pid(pid, name)

        if name.lower().startswith(app_name.lower()):
            doc_part = name[len(app_name):].lstrip(" —-").strip()
            display_name = f"{app_name} — {doc_part}" if doc_part else app_name
        elif " — " in name:
            parts2 = name.split(" — ", 1)
            display_name = f"{app_name} — {parts2[-1].strip()}"
        elif " - " in name:
            parts2 = name.split(" - ", 1)
            display_name = f"{app_name} — {parts2[-1].strip()}"
        else:
            display_name = f"{app_name} — {name}" if name != app_name else app_name

        targets.append(
            WindowTarget(
                id=f"x11_{wid}",
                type="x11_app",
                display_name=display_name,
                app_name=app_name,
                window_id=wid,
                cdp_url=None,
                tab_url=None,
                tab_title=None,
                tab_id=None,
                pid=pid,
                icon="\U0001f5a5️",  # 🖥️
            )
        )

    return targets


def discover_headless_option() -> WindowTarget:
    """Return a placeholder target for launching a new headless browser."""
    return WindowTarget(
        id="headless",
        type="headless",
        display_name="Launch new hidden browser (Xvfb)",
        app_name="Xvfb",
        window_id=None,
        cdp_url=None,
        tab_url=None,
        tab_title=None,
        tab_id=None,
        pid=None,
        icon="\U0001f47b",  # 👻
    )


async def discover_all() -> list[WindowTarget]:
    """Discover all window targets: browser tabs, X11 apps, headless option.

    Returns targets in order:
      1. Browser tabs (most useful first)
      2. X11 applications
      3. Headless option (always last)
    """
    browser_tabs = await discover_browser_tabs()

    # Collect PIDs from browser CDP results so x11 discovery can skip them
    browser_pids: set[int] = set()
    for tab in browser_tabs:
        if tab.pid is not None:
            browser_pids.add(tab.pid)

    x11_windows = discover_x11_windows(browser_pids=browser_pids)
    headless = discover_headless_option()

    return [*browser_tabs, *x11_windows, headless]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_target_list(targets: list[WindowTarget]) -> str:
    """Return a rich-formatted string for terminal display of all targets."""
    lines: list[str] = []
    counter = 1

    browser_targets = [t for t in targets if t.type == "browser_tab"]
    app_targets = [t for t in targets if t.type == "x11_app"]
    headless_targets = [t for t in targets if t.type == "headless"]

    if browser_targets:
        lines.append("[bold cyan]BROWSERS[/bold cyan]")
        for t in browser_targets:
            url_label = _short_url(t.tab_url or "")
            if url_label:
                entry = f"  [bold white][[{counter}]][/bold white] {t.icon} [green]{t.display_name}[/green] [dim]({url_label})[/dim]"
            else:
                entry = f"  [bold white][[{counter}]][/bold white] {t.icon} [green]{t.display_name}[/green]"
            lines.append(entry)
            counter += 1
        lines.append("")

    if app_targets:
        lines.append("[bold cyan]APPLICATIONS[/bold cyan]")
        for t in app_targets:
            entry = f"  [bold white][[{counter}]][/bold white] {t.icon} [yellow]{t.display_name}[/yellow]"
            lines.append(entry)
            counter += 1
        lines.append("")

    for t in headless_targets:
        entry = f"  [bold white][[{counter}]][/bold white] {t.icon} [dim]{t.display_name}[/dim]"
        lines.append(entry)
        counter += 1

    return "\n".join(lines)
