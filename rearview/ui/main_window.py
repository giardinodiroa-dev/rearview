from __future__ import annotations

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QStackedWidget,
    QApplication,
    QSizePolicy,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QCloseEvent, QIcon, QPixmap, QColor, QPainter

from rearview.ui.dashboard_panel import DashboardPanel
from rearview.ui.macro_panel import MacroPanel
from rearview.ui.script_panel import ScriptPanel
from rearview.ui.settings_panel import SettingsPanel
from rearview.ui.log_panel import LogPanel
from rearview.ui.clicks_panel import ClicksPanel
from rearview.ui.system_tray import RearviewTrayIcon


_BG_WINDOW = "#141414"
_BG_SIDEBAR = "#1a1a1a"
_SIDEBAR_WIDTH = 116
_ACCENT = "#6366f1"
_TEXT_ACTIVE = "#ffffff"
_TEXT_INACTIVE = "#6b7280"
_BORDER_SIDEBAR = "#252525"


# ---------------------------------------------------------------------------
# Sidebar nav button
# ---------------------------------------------------------------------------

class _NavButton(QPushButton):
    def __init__(self, icon_text: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self._icon_text = icon_text
        self._label = label
        self._active = False
        self.setFixedHeight(52)
        self.setCheckable(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._apply_style()

    def set_active(self, active: bool) -> None:
        self._active = active
        self._apply_style()

    def _apply_style(self) -> None:
        if self._active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: #222233;
                    color: {_TEXT_ACTIVE};
                    border: none;
                    border-left: 3px solid {_ACCENT};
                    border-radius: 0px;
                    text-align: left;
                    padding-left: 10px;
                    font-size: 12px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {_TEXT_INACTIVE};
                    border: none;
                    border-left: 3px solid transparent;
                    border-radius: 0px;
                    text-align: left;
                    padding-left: 10px;
                    font-size: 12px;
                }}
                QPushButton:hover {{
                    background-color: #202020;
                    color: #cccccc;
                }}
            """)
        self.setText(f"{self._icon_text}  {self._label}")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

_NAV_ITEMS = [
    ("⬛", "Dashboard"),
    ("⏺", "Macros"),
    ("📄", "Script"),
    ("⚙", "Settings"),
    ("📋", "Logs"),
    ("🖱", "Clicks"),
]


class _Sidebar(QWidget):
    nav_changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_SIDEBAR_WIDTH)
        self.setStyleSheet(
            f"background-color: {_BG_SIDEBAR};"
            f"border-right: 1px solid {_BORDER_SIDEBAR};"
        )
        self._buttons: list[_NavButton] = []
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Logo area
        logo = QLabel("Shadow\nTasker")
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedHeight(60)
        logo.setStyleSheet(
            f"color: {_ACCENT}; font-size: 11px; font-weight: bold;"
            f" letter-spacing: 1px; border-bottom: 1px solid {_BORDER_SIDEBAR};"
            " padding: 0px;"
        )
        layout.addWidget(logo)

        # Nav buttons
        for i, (icon, label) in enumerate(_NAV_ITEMS):
            btn = _NavButton(icon, label)
            btn.clicked.connect(lambda checked, idx=i: self._on_nav(idx))
            self._buttons.append(btn)
            layout.addWidget(btn)

        layout.addStretch(1)

        # Version footer
        ver = QLabel("v0.1")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setFixedHeight(28)
        ver.setStyleSheet(f"color: #333; font-size: 10px;")
        layout.addWidget(ver)

        # Activate first item
        self._buttons[0].set_active(True)

    def _on_nav(self, idx: int) -> None:
        for i, btn in enumerate(self._buttons):
            btn.set_active(i == idx)
        self.nav_changed.emit(idx)

    def select(self, idx: int) -> None:
        self._on_nav(idx)


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    # Signals the CLI orchestrator wires to its async handlers
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    record_start_requested = pyqtSignal()
    record_stop_requested = pyqtSignal()
    switch_target_requested = pyqtSignal()
    clicks_changed = pyqtSignal()   # re-emitted from ClicksPanel.chains_changed

    def __init__(self, parent=None, loop=None) -> None:
        super().__init__(parent)
        self._loop = loop
        self.setWindowTitle("Rearview")
        self.setMinimumSize(QSize(780, 560))
        self.resize(900, 640)
        self.setStyleSheet(f"background-color: {_BG_WINDOW};")

        self._build_ui()
        self._wire_signals()

        self._tray = RearviewTrayIcon(self)
        self._wire_tray()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet(f"background-color: {_BG_WINDOW};")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self._sidebar = _Sidebar()
        self._sidebar.nav_changed.connect(self._on_nav_changed)
        root.addWidget(self._sidebar)

        # Content stack
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background-color: {_BG_WINDOW};")
        root.addWidget(self._stack, 1)

        # Create and register panels (order must match _NAV_ITEMS)
        self._dashboard = DashboardPanel()
        self._macros = MacroPanel()
        self._script = ScriptPanel()
        self._settings = SettingsPanel()
        self._log_panel = LogPanel()
        self._clicks = ClicksPanel(loop=self._loop)

        for panel in (self._dashboard, self._macros, self._script, self._settings, self._log_panel, self._clicks):
            self._stack.addWidget(panel)

    def _wire_signals(self) -> None:
        # Dashboard quick actions → main signals
        self._dashboard.start_requested.connect(self.start_requested)
        self._dashboard.stop_requested.connect(self.stop_requested)
        self._dashboard.record_requested.connect(self.record_start_requested)
        self._dashboard.switch_target_requested.connect(self.switch_target_requested)

        # Macro panel recording controls → main signals
        self._macros.record_start_requested.connect(self._on_record_start)
        self._macros.record_stop_requested.connect(self._on_record_stop)
        self._macros.macro_run_requested.connect(self._on_macro_run)
        self._macros.macro_edit_requested.connect(self._on_macro_edit)

        # Settings saved → refresh script panel (script path may have changed)
        self._settings.settings_saved.connect(self._script.refresh)
        self._settings.settings_saved.connect(self._dashboard.refresh_hotkeys)

        # Clicks panel → propagate chain changes to CLI for hotkey reload
        self._clicks.chains_changed.connect(self.clicks_changed)

    def _wire_tray(self) -> None:
        self._tray.show_hide_requested.connect(self._toggle_visible)
        self._tray.record_start_requested.connect(self._on_record_start)
        self._tray.record_stop_requested.connect(self._on_record_stop)
        self._tray.switch_target_requested.connect(self.switch_target_requested)
        self._tray.quit_requested.connect(self._on_quit)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_nav_changed(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)

    def _on_record_start(self) -> None:
        self._macros.set_recording(True)
        self._tray.set_recording(True)
        self.record_start_requested.emit()

    def _on_record_stop(self) -> None:
        self._macros.set_recording(False)
        self._tray.set_recording(False)
        self.record_stop_requested.emit()

    def _on_macro_run(self, name: str) -> None:
        self._log_panel.append(f"Running macro: {name}")

    def _on_macro_edit(self, name: str) -> None:
        # Switch to the script/log panel is not needed; macro editor is a CLI operation.
        # Just navigate to Logs so user can see output.
        self._sidebar.select(4)
        self._log_panel.append(f"Edit macro '{name}' — use: rearview macro edit {name}")

    def _toggle_visible(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def _on_quit(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ------------------------------------------------------------------
    # Public API (called by CLI orchestrator)
    # ------------------------------------------------------------------

    def log(self, msg: str) -> None:
        """Thread-safe. Push a message to the activity log panel."""
        self._log_panel.append(msg)

    def set_status(self, connected: bool, target_name: str) -> None:
        """Update dashboard status dot + tray tooltip."""
        self._dashboard.set_status(connected, target_name)
        self._tray.set_status("connected" if connected else "disconnected")
        self._tray.set_target_name(target_name)

    def set_recording(self, active: bool) -> None:
        """Sync recording state across macro panel and tray icon."""
        self._macros.set_recording(active)
        self._tray.set_recording(active)

    def set_macro_running(self, name: str, running: bool) -> None:
        """Highlight the currently-executing macro row in the macro table."""
        self._macros.set_macro_running(name, running)

    def refresh_macros(self) -> None:
        """Refresh the macro table from disk."""
        self._macros.refresh()

    def show_panel(self, name: str) -> None:
        """Navigate to a named panel. name: dashboard|macros|script|settings|logs"""
        panel_map = {
            "dashboard": 0,
            "macros": 1,
            "script": 2,
            "settings": 3,
            "logs": 4,
            "clicks": 5,
        }
        idx = panel_map.get(name.lower())
        if idx is not None:
            self._sidebar.select(idx)

    # ------------------------------------------------------------------
    # Qt overrides
    # ------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Intercept window close — minimize to tray instead of quitting."""
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Rearview",
            "Running in the background. Click tray icon to restore.",
            QIcon(),
            2000,
        )
