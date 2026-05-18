from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import logging
import typer
from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)

app = typer.Typer(name="rearview", help="Browser automation assistant for sales workflows")
console = Console()

ROOT = Path(__file__).parent.parent
PID_FILE = Path("/tmp/rearview.pid")
STOP_FILE = Path("/tmp/rearview.stop")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _read_pid() -> Optional[int]:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


def _write_pid() -> None:
    PID_FILE.write_text(str(os.getpid()))


def _check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


def _pgrep(name: str) -> bool:
    result = subprocess.run(
        ["pgrep", "-x", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _open_in_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(path)])


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

@app.command()
def start(
    no_toast: bool = typer.Option(False, "--no-toast", help="Skip the overlay toast window"),
    no_speech: bool = typer.Option(False, "--no-speech", help="Skip speech recognition"),
) -> None:
    """Launch virtual desktop + browser, start watcher and toast overlay."""
    # ------------------------------------------------------------------
    # Hot-reload wrapper — top-level process watches for .py changes and
    # restarts the child. The child skips this block via env var.
    # ------------------------------------------------------------------
    if not os.environ.get("_REARVIEW_CHILD"):
        import signal as _sig
        import time as _time
        from watchfiles import watch as _watch

        _pkg = Path(__file__).parent
        _cmd = [sys.executable] + sys.argv
        _env = {**os.environ, "_REARVIEW_CHILD": "1"}

        _proc: list = []

        def _launch():
            _proc.clear()
            _proc.append(subprocess.Popen(_cmd, env=_env))

        def _kill():
            if _proc and _proc[0].poll() is None:
                _proc[0].send_signal(_sig.SIGTERM)
                try:
                    _proc[0].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    _proc[0].kill()
                    _proc[0].wait()

        def _on_exit(sig, frame):
            _kill()
            raise SystemExit(0)

        _sig.signal(_sig.SIGINT, _on_exit)
        _sig.signal(_sig.SIGTERM, _on_exit)

        _launch()
        for _changes in _watch(_pkg, watch_filter=lambda _c, p: p.endswith(".py")):
            _names = ", ".join(Path(p).name for _, p in _changes)
            console.print(f"[yellow]↺ {_names}[/yellow] — reloading...")
            _kill()
            _time.sleep(0.3)
            _launch()
        return

    from rearview.config import get_config
    from rearview.desktop import DesktopManager

    # Remove any leftover stop signal from a previous run
    STOP_FILE.unlink(missing_ok=True)

    _write_pid()
    console.print("[bold cyan]rearview[/bold cyan] starting up...")

    cfg = get_config()

    # Target selection — terminal prompt if tty, Qt dialog if detached
    from rearview.target_selector import select_target_auto, select_target_interactive, get_session
    from rich.console import Console as RichConsole

    _rich = RichConsole()
    loop_pre = asyncio.new_event_loop()
    target = loop_pre.run_until_complete(select_target_auto(_rich))

    if target is None:
        if sys.stdin.isatty():
            target = loop_pre.run_until_complete(select_target_interactive(_rich))
        else:
            # No terminal — show Qt picker dialog before the main window
            from PyQt6.QtWidgets import QApplication, QDialog
            from rearview.ui.target_dialog import TargetPickerDialog
            from rearview.window_discovery import discover_all
            _qt_pre = QApplication.instance() or QApplication(sys.argv)
            targets = loop_pre.run_until_complete(discover_all())
            dlg = TargetPickerDialog(targets)
            if dlg.exec() != QDialog.DialogCode.Accepted or dlg.selected is None:
                loop_pre.close()
                raise typer.Exit(0)
            target = dlg.selected

    loop_pre.close()

    get_session().set(target)
    console.print(f"  [green]+[/green] Target: [bold]{target.display_name}[/bold]")

    # ------------------------------------------------------------------
    # Mode selection — "Open App" or "Toast Viewer"
    # ------------------------------------------------------------------
    _mode = "app"
    try:
        from PyQt6.QtWidgets import QApplication, QDialog
        _qt_now = QApplication.instance()
        if _qt_now is not None:
            from rearview.ui.mode_dialog import ModePickerDialog
            _dlg = ModePickerDialog(target)
            if _dlg.exec() != QDialog.DialogCode.Accepted or not _dlg.selected_mode:
                raise typer.Exit(0)
            _mode = _dlg.selected_mode
        elif sys.stdin.isatty():
            console.print("\n  [dim]Mode:[/dim]  [bold]1[/bold] Open App   [bold]2[/bold] Toast Viewer")
            _choice = typer.prompt("  Select", default="1").strip()
            _mode = "viewer" if _choice == "2" else "app"
    except ImportError:
        pass
    console.print(f"  [green]+[/green] Mode: [bold]{_mode}[/bold]")

    # ------------------------------------------------------------------
    # Toast Viewer mode — region screenshots
    # ------------------------------------------------------------------
    if _mode == "viewer":
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer
        from rearview.toast.viewer_toast import ViewerToast
        from rearview.ui.system_tray import RearviewTrayIcon
        from rearview.region_mapper import RegionStore, RegionMapperOverlay

        qt_app_v = QApplication.instance() or QApplication(sys.argv)
        loop_v = asyncio.new_event_loop()

        target_key = target.tab_url or target.display_name
        store_v = RegionStore()
        _regions: list = store_v.load(target_key)
        _viewer_target = target

        tray_v = RearviewTrayIcon()
        tray_v.set_status("connected")
        tray_v.set_target_name(target.display_name)
        tray_v.quit_requested.connect(qt_app_v.quit)
        tray_v.show()

        viewer = ViewerToast(target_name=target.display_name)
        viewer.set_scripts_target(target_key)
        tray_v.show_hide_requested.connect(
            lambda: viewer.hide() if viewer.isVisible() else viewer.show()
        )
        viewer.show()

        async def _viewer_main() -> None:
            from rearview.controller import get_controller
            controller = get_controller()
            await controller.connect()
            while True:
                await asyncio.sleep(1)

        # Holds a strong Python reference to the overlay so GC doesn't destroy it
        _overlay_ref: list = []

        # Map button → fullscreen overlay to draw/edit regions
        def _on_map_requested() -> None:
            _overlay_ref.clear()
            overlay = RegionMapperOverlay(target_key, existing_regions=list(_regions))
            _overlay_ref.append(overlay)

            def _on_regions_updated(new_regions: list) -> None:
                _regions.clear()
                _regions.extend(new_regions)
                _overlay_ref.clear()
                viewer.show()
                viewer.raise_()
                # 400ms for compositor to fully remove the overlay before streaming starts
                QTimer.singleShot(400, lambda: viewer.start_streaming(_regions, target=_viewer_target))

            overlay.regions_updated.connect(_on_regions_updated)
            overlay.cancelled.connect(lambda: _overlay_ref.clear())
            overlay.script_saved.connect(lambda _key: viewer._reload_scripts())
            overlay.show()

        viewer.map_requested.connect(_on_map_requested)
        viewer.refresh_requested.connect(lambda: viewer.start_streaming(_regions, target=_viewer_target))

        def _on_region_renamed(old_name: str, new_name: str) -> None:
            for r in _regions:
                if r.name == old_name:
                    r.name = new_name
                    break
            store_v.save(target_key, _regions)
            viewer.start_streaming(_regions, target=_viewer_target)

        def _on_region_deleted(name: str) -> None:
            for r in list(_regions):
                if r.name == name:
                    _regions.remove(r)
                    break
            store_v.save(target_key, _regions)
            viewer.start_streaming(_regions, target=_viewer_target)

        def _on_region_remap_requested(_name: str) -> None:
            _on_map_requested()

        def _on_region_reordered(source: str, target: str) -> None:
            src_idx = next((i for i, r in enumerate(_regions) if r.name == source), -1)
            tgt_idx = next((i for i, r in enumerate(_regions) if r.name == target), -1)
            if src_idx == -1 or tgt_idx == -1 or src_idx == tgt_idx:
                return
            item = _regions.pop(src_idx)
            _regions.insert(tgt_idx, item)
            store_v.save(target_key, _regions)
            viewer.start_streaming(_regions, target=_viewer_target)

        def _on_region_click(name: str, rel_x: float, rel_y: float) -> None:
            region = next((r for r in _regions if r.name == name), None)
            if region is None:
                return
            abs_x = int(region.x + rel_x * region.w)
            abs_y = int(region.y + rel_y * region.h)
            from rearview.controller import get_controller
            asyncio.run_coroutine_threadsafe(
                get_controller().background_click(abs_x, abs_y),
                loop_v,
            )

        viewer.region_renamed.connect(_on_region_renamed)
        viewer.region_deleted.connect(_on_region_deleted)
        viewer.region_remap_requested.connect(_on_region_remap_requested)
        viewer.region_reordered.connect(_on_region_reordered)
        viewer.region_click_requested.connect(_on_region_click)

        def _on_run_chain(chain_id: str) -> None:
            from rearview.click_store import get_click_store as _gcs
            from rearview.click_executor import get_click_executor
            store_c = _gcs()
            chains = store_c.load_chains(target_key, "_overlay")
            chain = next((c for c in chains if c.id == chain_id), None)
            if chain is None:
                return
            dots = store_c.dots_for_chain(target_key, chain)
            asyncio.run_coroutine_threadsafe(
                get_click_executor(loop_v).run_chain(chain, dots), loop_v
            )

        viewer.run_chain_requested.connect(_on_run_chain)

        if not _regions:
            QTimer.singleShot(500, _on_map_requested)
        else:
            viewer.start_streaming(_regions, target=_viewer_target)

        def _viewer_thread() -> None:
            asyncio.set_event_loop(loop_v)
            loop_v.run_until_complete(_viewer_main())

        threading.Thread(target=_viewer_thread, daemon=True, name="viewer-async").start()

        qt_app_v.exec()
        loop_v.call_soon_threadsafe(loop_v.stop)
        return

    # If user picked headless → need virtual desktop. Otherwise skip it.
    _needs_desktop = (target.type == "headless")

    # ------------------------------------------------------------------
    # 1. Start virtual desktop + Chromium (async)
    # ------------------------------------------------------------------
    loop = asyncio.new_event_loop()

    if _needs_desktop:
        desktop = DesktopManager(cfg)

        async def _boot_desktop() -> None:
            console.print("  [green]+[/green] Starting virtual desktop (Xvfb :99) and Chromium...")
            await desktop.start()
            console.print("  [green]+[/green] Browser ready on port [bold]%d[/bold]" % cfg.browser.debug_port)

        loop.run_until_complete(_boot_desktop())
    else:
        desktop = None
        console.print("  [cyan]~[/cyan] Using existing browser — skipping virtual desktop")

    # ------------------------------------------------------------------
    # 2. Start CDP watcher in the background asyncio thread
    # ------------------------------------------------------------------
    watcher_task: Optional[asyncio.Task] = None

    # Shared state between async thread and Qt thread
    _toast_ref: list = []       # populated once Qt thread creates the toast
    _win_ref: list = []         # populated once Qt thread creates the main window
    _shared: dict = {}          # "on_disposition" key set by async thread

    async def _run_watcher() -> None:
        from rearview.watcher import CallWatcher
        from rearview.disposition import complete_call, advance_to_next
        from rearview.hotkeys import HotkeyManager
        from rearview.controller import get_controller

        watcher = CallWatcher()
        hotkeys = HotkeyManager(loop)
        from rearview.click_hotkeys import get_click_hotkey_manager
        click_hotkeys = get_click_hotkey_manager(loop)

        async def _on_call_end(contact) -> None:
            if _toast_ref:
                _toast_ref[0].update_contact(contact)
                _toast_ref[0].set_disposition_mode(True)
                _toast_ref[0].flash()
            hotkeys.set_disposition_mode(True)

        async def _on_disposition(label: str) -> None:
            hotkeys.set_disposition_mode(False)
            if _toast_ref:
                _toast_ref[0].set_disposition_mode(False)
            if get_controller().is_connected:
                await complete_call(get_controller().current_page, label, cfg.workflow.auto_advance)

        async def _on_next() -> None:
            if get_controller().is_connected:
                await advance_to_next(get_controller().current_page)

        # Expose disposition handler so the Qt thread can schedule it
        _shared["on_disposition"] = _on_disposition

        # Connect controller to the selected target, hand page to watcher
        controller = get_controller()
        connected = await controller.connect()
        if connected:
            watcher.set_page(controller.current_page)
        else:
            logger.warning("Controller could not connect to target — watcher will retry")

        # When target switches, reconnect controller and update watcher page
        async def _on_target_switch(new_target) -> None:
            await controller.connect()
            watcher.set_page(controller.current_page)
        get_session().on_change(lambda t: asyncio.ensure_future(_on_target_switch(t)))

        watcher.on_call_end = _on_call_end
        hotkeys.set_disposition_callback(_on_disposition)
        hotkeys.set_next_callback(_on_next)
        hotkeys.start()
        click_hotkeys.start()

        # Super+R: toggle macro recording
        recorder = None
        async def _toggle_record() -> None:
            nonlocal recorder
            from rearview.recorder import get_recorder, create_macro_from_steps
            from rearview.macro_store import save_macro
            rec = get_recorder(loop)
            if not rec.is_recording():
                rec.start()
                if _toast_ref:
                    _toast_ref[0].push_log("● Recording...")
                if _win_ref:
                    _win_ref[0].set_recording(True)
                    _win_ref[0].log("● Recording started")
            else:
                steps = rec.stop()
                if _win_ref:
                    _win_ref[0].set_recording(False)
                if steps:
                    from datetime import datetime
                    name = f"macro-{datetime.now().strftime('%H%M%S')}"
                    macro = create_macro_from_steps(steps, name,
                        get_session().current.tab_url or "" if get_session().current else "")
                    save_macro(macro)
                    if _toast_ref:
                        _toast_ref[0].push_log(f"Saved: {name} ({len(steps)} steps)")
                    if _win_ref:
                        _win_ref[0].log(f"Saved macro: {name} ({len(steps)} steps)")
                        _win_ref[0].refresh_macros()

        # Wire Super+R for recording toggle
        import pynput.keyboard as _kb
        _record_hotkeys = _kb.GlobalHotKeys({"<super>r": lambda: asyncio.run_coroutine_threadsafe(_toggle_record(), loop)})
        _record_hotkeys.start()

        # Speech listener — feeds transcript chunks to the teleprompter
        speech = None
        if not no_speech and cfg.teleprompter.enabled:
            try:
                from rearview.speech.listener import SpeechListener
                transcript_queue: asyncio.Queue = asyncio.Queue()
                speech = SpeechListener(loop, transcript_queue)
                speech.start()

                async def _forward_transcript() -> None:
                    while True:
                        chunk = await transcript_queue.get()
                        if chunk is None:
                            break
                        if _toast_ref:
                            _toast_ref[0].push_transcript(chunk)

                loop.create_task(_forward_transcript())
            except Exception as exc:
                logger.warning("Speech listener failed to start: %s", exc)

        try:
            await watcher.watch()
        finally:
            hotkeys.stop()
            click_hotkeys.stop()
            if '_record_hotkeys' in dir():
                _record_hotkeys.stop()
            if speech is not None:
                speech.stop()

    async def _main_loop() -> None:
        nonlocal watcher_task
        watcher_task = loop.create_task(_run_watcher())

        # Poll for the stop signal; give the watcher time to do its work
        while not STOP_FILE.exists():
            await asyncio.sleep(0.5)

        console.print("\n[yellow]Stop signal received — shutting down...[/yellow]")
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

        if desktop is not None:
            await desktop.stop()
        PID_FILE.unlink(missing_ok=True)

    if no_toast:
        # No Qt needed — run purely in the main thread's asyncio loop
        console.print("  [green]+[/green] CDP watcher running (no toast)")
        try:
            loop.run_until_complete(_main_loop())
        finally:
            loop.close()
        return

    # ------------------------------------------------------------------
    # 3. Run asyncio loop in a background thread; Qt in the main thread
    # ------------------------------------------------------------------
    console.print("  [green]+[/green] CDP watcher running")

    async_ready = threading.Event()
    async_done = threading.Event()

    def _asyncio_thread() -> None:
        asyncio.set_event_loop(loop)
        async_ready.set()
        try:
            loop.run_until_complete(_main_loop())
        finally:
            async_done.set()
            loop.close()

    bg_thread = threading.Thread(target=_asyncio_thread, daemon=True, name="rearview-async")
    bg_thread.start()
    async_ready.wait()

    # ------------------------------------------------------------------
    # 4. Qt: main window + floating call overlay toast
    # ------------------------------------------------------------------
    try:
        from PyQt6.QtWidgets import QApplication
        from rearview.ui.main_window import MainWindow
        from rearview.toast.window import ShadowToast

        console.print("  [green]+[/green] Launching main window and overlay toast")
        qt_app = QApplication.instance() or QApplication(sys.argv)

        # Main management window
        main_win = MainWindow(loop=loop)
        _win_ref.append(main_win)
        main_win.set_status(True, target.display_name)

        # Reload click hotkeys whenever chains are saved/deleted in the UI
        from rearview.click_hotkeys import get_click_hotkey_manager as _get_chm
        main_win.clicks_changed.connect(lambda: _get_chm(loop).reload())

        # Floating call overlay
        toast = ShadowToast()
        _toast_ref.append(toast)

        # Load teleprompter script
        script_path = ROOT / cfg.teleprompter.script
        if script_path.exists():
            toast.load_script(
                script_path.read_text(),
                {"agent_name": cfg.teleprompter.agent_name, "company": cfg.teleprompter.company},
            )

        # Show initial target name in toast header
        toast.set_target(target.display_name)

        # When session target changes → update toast header + main window status
        def _on_session_change(new_target) -> None:
            toast.set_target(new_target.display_name)
            main_win.set_status(True, new_target.display_name)
        get_session().on_change(_on_session_change)

        # Switch target from main window or toast
        def _on_switch_requested() -> None:
            async def _do_switch() -> None:
                from rearview.target_selector import select_target_interactive
                from rich.console import Console as RichConsole
                new_target = await select_target_interactive(RichConsole())
                get_session().set(new_target)
            asyncio.run_coroutine_threadsafe(_do_switch(), loop)

        toast.target_switch_requested.connect(_on_switch_requested)
        main_win.switch_target_requested.connect(_on_switch_requested)

        # Bridge: disposition button clicks → async handler
        def _disposition_from_qt(label: str) -> None:
            handler = _shared.get("on_disposition")
            if handler is not None:
                asyncio.run_coroutine_threadsafe(handler(label), loop)

        toast.disposition_selected.connect(_disposition_from_qt)

        # Main window stop button → signal async thread
        main_win.stop_requested.connect(lambda: STOP_FILE.touch())

        toast.show()
        main_win.show()

        # When Qt quits signal the async thread
        def _on_qt_quit() -> None:
            STOP_FILE.touch()

        qt_app.aboutToQuit.connect(_on_qt_quit)

        qt_app.exec()

    except ImportError as exc:
        console.print(f"[yellow]Toast unavailable ({exc}); running headless.[/yellow]")

    # Wait for the asyncio thread to finish its teardown
    async_done.wait(timeout=10.0)
    console.print("[bold green]rearview stopped.[/bold green]")


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

@app.command()
def stop() -> None:
    """Stop the running rearview process."""
    import signal as _signal

    pid = _read_pid()
    if pid is None or not _is_process_alive(pid):
        STOP_FILE.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)
        console.print("[yellow]rearview is not running.[/yellow]")
        raise typer.Exit(0)

    STOP_FILE.touch()
    try:
        os.kill(pid, _signal.SIGTERM)
        console.print(f"[green]Stopped (PID {pid}).[/green]")
    except ProcessLookupError:
        console.print("[yellow]Process already gone.[/yellow]")
    PID_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status() -> None:
    """Show current component statuses."""
    from rearview.config import get_config

    cfg = get_config()

    table = Table(title="rearview status", show_header=True, header_style="bold magenta")
    table.add_column("Component", style="cyan", width=24)
    table.add_column("Status", width=12)
    table.add_column("Detail", style="dim")

    # Xvfb
    xvfb_alive = _pgrep("Xvfb")
    table.add_row(
        "Xvfb (:99)",
        "[green]running[/green]" if xvfb_alive else "[red]stopped[/red]",
        "Virtual display for headless browser" if xvfb_alive else "Not found via pgrep",
    )

    # Chromium / CDP
    port = cfg.browser.debug_port
    cdp_reachable = _check_port("localhost", port)
    table.add_row(
        f"Chromium (:{port})",
        "[green]reachable[/green]" if cdp_reachable else "[red]unreachable[/red]",
        f"localhost:{port}" if cdp_reachable else f"Cannot connect to localhost:{port}",
    )

    # Main process
    pid = _read_pid()
    if pid is not None and _is_process_alive(pid):
        proc_status = "[green]alive[/green]"
        proc_detail = f"PID {pid}"
    elif pid is not None:
        proc_status = "[red]dead[/red]"
        proc_detail = f"PID {pid} (stale pidfile)"
    else:
        proc_status = "[red]not running[/red]"
        proc_detail = "No pidfile found"

    table.add_row("rearview process", proc_status, proc_detail)

    console.print(table)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@app.command("config")
def config_cmd(
    dispositions: bool = typer.Option(False, "--dispositions", help="Open dispositions.yaml instead"),
) -> None:
    """Open settings.yaml (or dispositions.yaml) in $EDITOR."""
    config_dir = ROOT / "config"
    target = config_dir / ("dispositions.yaml" if dispositions else "settings.yaml")

    if not target.exists():
        console.print(f"[red]File not found:[/red] {target}")
        raise typer.Exit(1)

    console.print(f"Opening [bold]{target}[/bold] in editor...")
    _open_in_editor(target)


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------

@app.command()
def connect(
    port: int = typer.Option(9222, "--port", "-p", help="CDP debug port"),
) -> None:
    """(Re)attach CDP connection to a running browser."""
    console.print(f"Connecting to CDP on [bold]localhost:{port}[/bold]...")

    if not _check_port("localhost", port):
        console.print(f"[red]Cannot reach localhost:{port}[/red] — is the browser running?")
        raise typer.Exit(1)

    # Attempt a real Playwright CDP connection to validate the endpoint
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://localhost:{port}")
            contexts = browser.contexts
            pages = sum(len(c.pages) for c in contexts)
            browser.close()

        console.print(f"[green]Connected.[/green] Found {len(contexts)} context(s), {pages} page(s).")
    except Exception as exc:
        console.print(f"[red]CDP connection failed:[/red] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# target
# ---------------------------------------------------------------------------

@app.command("target")
def target_cmd() -> None:
    """Switch the agent's active target window or browser tab."""
    import asyncio
    from rich.console import Console as RichConsole
    from rearview.target_selector import select_target_interactive, get_session
    from rearview.controller import get_controller

    _rich = RichConsole()
    loop = asyncio.new_event_loop()
    new_target = loop.run_until_complete(select_target_interactive(_rich))
    loop.close()

    get_session().set(new_target)
    # Controller._on_target_changed will fire automatically via session listener
    console.print(f"[green]Target switched to:[/green] [bold]{new_target.display_name}[/bold]")


# ---------------------------------------------------------------------------
# vnc
# ---------------------------------------------------------------------------

@app.command()
def vnc(
    port: int = typer.Option(5900, "--port", "-p", help="VNC port to listen on"),
) -> None:
    """Start x11vnc for visual inspection of the :99 display."""
    console.print(f"Starting x11vnc on display :99, port [bold]{port}[/bold]...")

    cmd = [
        "x11vnc",
        "-display", ":99",
        "-nopw",
        "-listen", "localhost",
        "-xkb",
        "-port", str(port),
        "-bg",          # daemonise
        "-quiet",
    ]

    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        console.print("[red]x11vnc not found.[/red] Install with: sudo apt install x11vnc")
        raise typer.Exit(1)
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]x11vnc failed (exit {exc.returncode}).[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold green]VNC server running.[/bold green]")
    console.print(f"  Connect your VNC viewer to: [cyan]localhost:{port}[/cyan]")
    console.print("  Example: vncviewer localhost::" + str(port))
    console.print("  TigerVNC:  xtigervncviewer localhost:" + str(port))


# ---------------------------------------------------------------------------
# script
# ---------------------------------------------------------------------------

script_app = typer.Typer(help="Manage the teleprompter script")
app.add_typer(script_app, name="script")


@script_app.command("load")
def script_load(
    path: Path = typer.Argument(..., help="Path to the script file to load", exists=True, readable=True),
) -> None:
    """Copy <path> into scripts/ and update settings.yaml."""
    import shutil
    import yaml

    scripts_dir = ROOT / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)

    dest = scripts_dir / path.name
    shutil.copy2(path, dest)
    console.print(f"[green]Copied[/green] {path.name} → {dest}")

    # Update teleprompter.script in settings.yaml
    settings_path = ROOT / "config" / "settings.yaml"
    if settings_path.exists():
        with open(settings_path) as f:
            raw = yaml.safe_load(f) or {}
    else:
        raw = {}

    raw.setdefault("teleprompter", {})
    raw["teleprompter"]["script"] = f"scripts/{path.name}"

    with open(settings_path, "w") as f:
        yaml.dump(raw, f, default_flow_style=False, allow_unicode=True)

    console.print(f"[green]settings.yaml[/green] teleprompter.script → [bold]scripts/{path.name}[/bold]")


@script_app.command("edit")
def script_edit() -> None:
    """Open the current teleprompter script in $EDITOR."""
    from rearview.config import get_config

    cfg = get_config()
    script_rel = cfg.teleprompter.script
    script_path = ROOT / script_rel

    if not script_path.exists():
        console.print(f"[red]Script not found:[/red] {script_path}")
        console.print("  Run [bold]rearview script load <path>[/bold] to set one first.")
        raise typer.Exit(1)

    console.print(f"Opening [bold]{script_path}[/bold] in editor...")
    _open_in_editor(script_path)


@script_app.command("show")
def script_show() -> None:
    """Print the current teleprompter script to the terminal."""
    from rearview.config import get_config
    from rich.markdown import Markdown

    cfg = get_config()
    script_rel = cfg.teleprompter.script
    script_path = ROOT / script_rel

    if not script_path.exists():
        console.print(f"[red]Script not found:[/red] {script_path}")
        raise typer.Exit(1)

    content = script_path.read_text()
    console.print(Markdown(content))


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------

@app.command()
def record(
    name: str = typer.Argument(..., help="Name for this macro"),
) -> None:
    """Record a macro. Press Super+R to start, Super+R again to stop."""
    import asyncio
    from rich.console import Console as RichConsole
    from rearview.recorder import get_recorder, create_macro_from_steps
    from rearview.macro_store import save_macro
    from rearview.target_selector import get_session
    from pynput import keyboard as kb

    _rich = RichConsole()
    session = get_session()

    if session.current is None:
        console.print("[red]No target selected. Run 'rearview start' first.[/red]")
        raise typer.Exit(1)

    loop = asyncio.new_event_loop()
    recorder = get_recorder(loop)

    _rich.print(f"[bold cyan]Recording:[/bold cyan] [bold]{name}[/bold]")
    _rich.print(f"  Target: {session.current.display_name}")
    _rich.print("  Press [bold]Super+R[/bold] to start recording...")

    started = threading.Event()
    stopped = threading.Event()

    def _on_activate_record():
        if not recorder.is_recording():
            recorder.start()
            started.set()
            _rich.print("  [green]● Recording...[/green] (press Super+R again to stop)")
        else:
            recorder.stop()
            stopped.set()

    hotkeys = kb.GlobalHotKeys({"<super>r": _on_activate_record})
    hotkeys.start()

    stopped.wait()
    hotkeys.stop()

    steps = recorder.stop() if recorder.is_recording() else recorder._steps.copy()
    _rich.print(f"  [green]Recorded {len(steps)} steps.[/green]")

    if not steps:
        _rich.print("[yellow]No steps recorded.[/yellow]")
        raise typer.Exit(0)

    macro = create_macro_from_steps(steps, name, session.current.tab_url or session.current.app_name or "")
    path = save_macro(macro)
    _rich.print(f"  Saved to [bold]{path}[/bold]")
    _rich.print(f"\nRun [bold]rearview macro edit {name}[/bold] to label your steps.")


# ---------------------------------------------------------------------------
# macro
# ---------------------------------------------------------------------------

macro_app = typer.Typer(help="Manage recorded macros")
app.add_typer(macro_app, name="macro")


@macro_app.command("list")
def macro_list() -> None:
    """List all saved macros."""
    from rearview.macro_store import list_macros
    from rich.table import Table
    macros = list_macros()
    if not macros:
        console.print("[dim]No macros saved yet.[/dim]")
        return
    t = Table(show_header=True, header_style="bold magenta")
    t.add_column("Name", style="cyan")
    t.add_column("Target", style="dim")
    t.add_column("Steps")
    t.add_column("Created", style="dim")
    for m in macros:
        labeled = sum(1 for s in m.steps if s.label)
        t.add_row(m.name, m.target_hint, f"{len(m.steps)} ({labeled} labeled)", m.created_at[:16])
    console.print(t)


@macro_app.command("edit")
def macro_edit(name: str = typer.Argument(...)) -> None:
    """Interactively label steps in a macro."""
    from rearview.macro_store import load_macro
    from rearview.macro_editor import edit_macro
    from rich.console import Console as RichConsole
    try:
        macro = load_macro(name)
    except FileNotFoundError:
        console.print(f"[red]Macro '{name}' not found.[/red]")
        raise typer.Exit(1)
    edit_macro(macro, RichConsole())


@macro_app.command("run")
def macro_run(
    name: str = typer.Argument(...),
    speed: float = typer.Option(1.0, "--speed", "-s", help="Playback speed multiplier"),
) -> None:
    """Run a macro against the current target."""
    import asyncio
    from rearview.macro_store import load_macro
    from rearview.controller import get_controller
    try:
        macro = load_macro(name)
    except FileNotFoundError:
        console.print(f"[red]Macro '{name}' not found.[/red]")
        raise typer.Exit(1)

    loop = asyncio.new_event_loop()
    controller = get_controller()

    def _log(msg):
        console.print(f"  [bold #6366f1]●[/bold #6366f1] {msg}")

    async def _run():
        if not controller.is_connected:
            await controller.connect()
        await controller.play_macro(macro, log_callback=_log, speed=speed)

    console.print(f"[cyan]Running macro:[/cyan] [bold]{name}[/bold] (speed {speed}x)")
    loop.run_until_complete(_run())
    loop.close()
    console.print("[green]Done.[/green]")


@macro_app.command("delete")
def macro_delete(name: str = typer.Argument(...)) -> None:
    """Delete a macro."""
    from rearview.macro_store import delete_macro
    if not typer.confirm(f"Delete macro '{name}'?"):
        raise typer.Exit(0)
    if delete_macro(name):
        console.print(f"[green]Deleted '{name}'.[/green]")
    else:
        console.print(f"[red]Macro '{name}' not found.[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
