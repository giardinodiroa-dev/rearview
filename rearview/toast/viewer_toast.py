from __future__ import annotations

import threading
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QScrollArea, QFrame, QSizeGrip, QMenu, QSizePolicy, QLayout,
    QStackedWidget, QLineEdit,
)
from PyQt6.QtCore import Qt, QPoint, QSize, QTimer, pyqtSignal, QMimeData, QThread
from PyQt6.QtGui import QGuiApplication, QPixmap, QDrag

from rearview.click_store import ClickChain, get_click_store

# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------

_BG = "#111111"
_BG_HEADER = "#1a1a1a"
_ACCENT = "#6366f1"
_TEXT = "#cccccc"
_TEXT_DIM = "#555555"
_BORDER = "#2a2a2a"

_BTN_INDIGO = f"""
    QPushButton {{
        background-color: {_ACCENT};
        color: #ffffff;
        border: none;
        border-radius: 3px;
        font-size: 11px;
        padding: 0 8px;
        height: 22px;
    }}
    QPushButton:hover {{ background-color: #7577f3; }}
    QPushButton:pressed {{ background-color: #4338ca; }}
"""

_BTN_INDIGO_CHECKED = f"""
    QPushButton {{
        background-color: {_ACCENT};
        color: #ffffff;
        border: none;
        border-radius: 3px;
        font-size: 11px;
        padding: 0 8px;
        height: 22px;
    }}
    QPushButton:hover {{ background-color: #7577f3; }}
    QPushButton:pressed {{ background-color: #4338ca; }}
    QPushButton:checked {{ background-color: {_ACCENT}; }}
"""

_BTN_GRAY = f"""
    QPushButton {{
        background-color: #2a2a2a;
        color: {_TEXT};
        border: none;
        border-radius: 3px;
        font-size: 11px;
        padding: 0 8px;
        height: 22px;
    }}
    QPushButton:hover {{ background-color: #3a3a3a; }}
    QPushButton:pressed {{ background-color: #222222; }}
"""

_BTN_PIN_UNPINNED = f"""
    QPushButton {{
        background-color: #2a2a2a;
        color: {_TEXT};
        border: none;
        border-radius: 3px;
        font-size: 11px;
        padding: 0 8px;
        height: 22px;
    }}
    QPushButton:hover {{ background-color: #3a3a3a; }}
"""

_BTN_CLOSE = """
    QPushButton {
        background-color: #3a1a1a;
        color: #cc4444;
        border: none;
        border-radius: 3px;
        font-size: 11px;
        padding: 0 8px;
        height: 22px;
    }
    QPushButton:hover { background-color: #552222; color: #ff6666; }
"""

_TAB_ACTIVE = (
    "QPushButton { background: #6366f1; color: white; border: none; border-radius: 3px;"
    " font-size: 11px; padding: 0 10px; height: 22px; }"
)
_TAB_INACTIVE = (
    "QPushButton { background: #2a2a2a; color: #aaa; border: none; border-radius: 3px;"
    " font-size: 11px; padding: 0 10px; height: 22px; }"
    " QPushButton:hover { background: #3a3a3a; color: #ccc; }"
)

_SCROLLBAR_STYLE = f"""
    QScrollBar:vertical {{
        background: {_BG};
        width: 6px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: #333333;
        border-radius: 3px;
        min-height: 20px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #444444;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
"""


# ---------------------------------------------------------------------------
# Drag-and-drop helpers
# ---------------------------------------------------------------------------

_REGION_MIME = "application/x-rearview-region"


class _DragHandle(QLabel):
    """Gripper that starts a QDrag when the user clicks and moves."""

    drag_requested = pyqtSignal(str)  # region name

    def __init__(self, region_name: str, parent=None):
        super().__init__("⠿", parent)
        self._region_name = region_name
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setStyleSheet(
            "color: #555; font-size: 13px; padding: 0 3px; background: transparent;"
        )
        self._press_pos: QPoint | None = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._press_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.pos() - self._press_pos).manhattanLength() > 4
        ):
            self.drag_requested.emit(self._region_name)
            self._press_pos = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# _ResizeHandle
# ---------------------------------------------------------------------------

class _ResizeHandle(QWidget):
    height_delta = pyqtSignal(int)
    reset = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(5)
        self.setMinimumWidth(0)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setStyleSheet("background: #1e1e1e; border-top: 1px solid #333;")
        self._press_y: int | None = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_y = event.globalPosition().toPoint().y()

    def mouseMoveEvent(self, event):
        if self._press_y is not None:
            y = event.globalPosition().toPoint().y()
            delta = y - self._press_y
            self._press_y = y
            self.height_delta.emit(delta)

    def mouseReleaseEvent(self, event):
        self._press_y = None

    def mouseDoubleClickEvent(self, event):
        self.reset.emit()


# ---------------------------------------------------------------------------
# _ClickableImageLabel
# ---------------------------------------------------------------------------

class _ClickableImageLabel(QLabel):
    """QLabel that emits the relative click position (0-1) within the label."""
    clicked_at = pyqtSignal(float, float)  # rel_x, rel_y

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            w, h = self.width(), self.height()
            if w > 0 and h > 0:
                rel_x = max(0.0, min(1.0, event.pos().x() / w))
                rel_y = max(0.0, min(1.0, event.pos().y() / h))
                self.clicked_at.emit(rel_x, rel_y)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# _RegionWidget
# ---------------------------------------------------------------------------

class _RegionWidget(QFrame):
    """Displays a single region: a name label + a screenshot pixmap."""

    rename_requested  = pyqtSignal(str)
    delete_requested  = pyqtSignal(str)
    remap_requested   = pyqtSignal(str)
    reorder_requested = pyqtSignal(str, str)   # source_name, target_name
    click_requested   = pyqtSignal(str, float, float)  # name, rel_x, rel_y

    def __init__(self, name: str, pixmap: QPixmap | None = None, parent=None):
        super().__init__(parent)
        self._name = name
        self.setObjectName("regionWidget")
        self.setStyleSheet(f"""
            QFrame#regionWidget {{
                background-color: {_BG_HEADER};
                border: 1px solid #252525;
                border-radius: 4px;
                margin: 4px;
            }}
        """)

        self.setAcceptDrops(True)
        self.setMinimumSize(0, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        # Header row: drag handle + name + action buttons
        self._header_widget = QWidget()
        self._header_widget.setMinimumSize(0, 0)
        header_layout = QHBoxLayout(self._header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        self._handle = _DragHandle(name, self)
        self._handle.setMinimumSize(0, 0)
        self._handle.drag_requested.connect(self._start_drag)

        self._name_label = QLabel(name)
        self._name_label.setMinimumWidth(0)
        self._name_label.setStyleSheet(
            f"color: {_ACCENT}; font-size: 11px; font-weight: bold;"
            f" background: transparent; border: none; margin: 0;"
        )

        _btn_style = (
            "QPushButton { background: transparent; color: #666; border: none;"
            " font-size: 11px; padding: 0 3px; }"
            "QPushButton:hover { color: #ccc; }"
        )

        rename_btn = QPushButton("✏")
        rename_btn.setFixedSize(18, 18)
        rename_btn.setMinimumSize(0, 0)
        rename_btn.setToolTip("Rename")
        rename_btn.setStyleSheet(_btn_style)
        rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rename_btn.clicked.connect(lambda: self.rename_requested.emit(self._name))

        remap_btn = QPushButton("⤢")
        remap_btn.setFixedSize(18, 18)
        remap_btn.setMinimumSize(0, 0)
        remap_btn.setToolTip("Resize / Remap")
        remap_btn.setStyleSheet(_btn_style)
        remap_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remap_btn.clicked.connect(lambda: self.remap_requested.emit(self._name))

        delete_btn = QPushButton("✕")
        delete_btn.setFixedSize(18, 18)
        delete_btn.setMinimumSize(0, 0)
        delete_btn.setToolTip("Delete")
        delete_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #666; border: none;"
            " font-size: 11px; padding: 0 3px; }"
            "QPushButton:hover { color: #ef4444; }"
        )
        delete_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(self._name))

        header_layout.addWidget(self._handle)
        header_layout.addWidget(self._name_label)
        header_layout.addStretch()
        header_layout.addWidget(rename_btn)
        header_layout.addWidget(remap_btn)
        header_layout.addWidget(delete_btn)

        # Image container — provides inner padding so screenshot never touches card border
        img_container = QWidget()
        img_container.setMinimumSize(0, 0)
        img_container.setStyleSheet(
            "QWidget { background: #0d0d0d; border-radius: 3px; }"
        )
        img_layout = QVBoxLayout(img_container)
        img_layout.setContentsMargins(8, 8, 8, 8)
        img_layout.setSpacing(0)

        self._img_label = _ClickableImageLabel()
        self._img_label.clicked_at.connect(
            lambda rx, ry: self.click_requested.emit(self._name, rx, ry)
        )
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.setStyleSheet("background: transparent; border: none;")
        # Ignored horizontal policy: label never pushes content wider than viewport
        self._img_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        img_layout.addWidget(self._img_label)

        layout.addWidget(self._header_widget)
        layout.addWidget(img_container)

        self._manual_height: int = 0
        self._resize_handle = _ResizeHandle(self)
        self._resize_handle.height_delta.connect(self._on_resize_drag)
        self._resize_handle.reset.connect(self._on_resize_reset)
        layout.addWidget(self._resize_handle)

        self._pixmap = pixmap if pixmap is not None else QPixmap()
        self._render_pixmap()

    def set_lean(self, lean: bool) -> None:
        self._header_widget.setVisible(not lean)
        self._resize_handle.setVisible(not lean)
        margins = (0, 0, 0, 0) if lean else (6, 4, 6, 6)
        self.layout().setContentsMargins(*margins)
        self.layout().setSpacing(0 if lean else 4)
        if lean:
            self.setStyleSheet("QFrame#regionWidget { background: transparent; border: none; margin: 0; }")
        else:
            self.setStyleSheet(f"""
                QFrame#regionWidget {{
                    background-color: {_BG_HEADER};
                    border: 1px solid #252525;
                    border-radius: 4px;
                    margin: 4px;
                }}
            """)

    def _on_resize_drag(self, delta: int) -> None:
        if self._manual_height == 0:
            self._manual_height = self.height()
        self._manual_height = max(1, self._manual_height + delta)
        self.setFixedHeight(self._manual_height)

    def _on_resize_reset(self) -> None:
        self._manual_height = 0
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)

    def _render_pixmap(self, smooth: bool = True):
        if self._pixmap.isNull():
            self._img_label.clear()
            self._img_label.setMinimumHeight(0)
            self._img_label.setMaximumHeight(0)
            return
        w = self._img_label.width()
        mode = (Qt.TransformationMode.SmoothTransformation if smooth
                else Qt.TransformationMode.FastTransformation)
        scaled = self._pixmap.scaledToWidth(w, mode)
        self._img_label.setMinimumHeight(0)
        self._img_label.setMaximumHeight(16777215)
        self._img_label.setPixmap(scaled)
        self._img_label.setFixedHeight(scaled.height())

    def update_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        self._render_pixmap()

    def connect_streamer(self, streamer) -> None:
        """Wire a RegionStreamer so each frame updates this widget."""
        streamer.frame_ready.connect(self._on_stream_frame)

    def _on_stream_frame(self, img) -> None:
        self._pixmap = QPixmap.fromImage(img)
        self._render_pixmap(smooth=False)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render_pixmap(smooth=False)

    # ------------------------------------------------------------------
    # Drag source
    # ------------------------------------------------------------------

    def _start_drag(self, name: str) -> None:
        # Walk up to ViewerToast and lock it so auto-refresh can't deleteLater us
        toast = self.parent()
        while toast is not None and not isinstance(toast, ViewerToast):
            toast = toast.parent()
        if toast is not None:
            toast._card_dragging = True
        try:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(_REGION_MIME, name.encode())
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            if toast is not None:
                toast._card_dragging = False
                toast.drag_ended.emit()

    # ------------------------------------------------------------------
    # Drop target
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_REGION_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(_REGION_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasFormat(_REGION_MIME):
            source = event.mimeData().data(_REGION_MIME).data().decode()
            if source != self._name:
                self.reorder_requested.emit(source, self._name)
            event.acceptProposedAction()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: #1e1e1e; color: #cccccc; border: 1px solid #333; border-radius: 4px; }"
            "QMenu::item { padding: 6px 18px; }"
            "QMenu::item:selected { background: #6366f1; color: #fff; }"
            "QMenu::separator { height: 1px; background: #333; margin: 2px 0; }"
        )
        rename_act = menu.addAction("✏  Rename")
        remap_act  = menu.addAction("⤢  Resize / Remap")
        menu.addSeparator()
        delete_act = menu.addAction("✕  Delete")
        chosen = menu.exec(event.globalPosition().toPoint())
        if chosen == rename_act:
            self.rename_requested.emit(self._name)
        elif chosen == remap_act:
            self.remap_requested.emit(self._name)
        elif chosen == delete_act:
            self.delete_requested.emit(self._name)


# ---------------------------------------------------------------------------
# _HotkeyCapture
# ---------------------------------------------------------------------------

class _HotkeyCapture(QThread):
    captured = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()

    def run(self):
        from pynput import keyboard as _kb
        pressed: set[str] = set()
        MOD_NAMES = {"ctrl", "alt", "shift", "cmd", "super"}

        def on_press(key):
            try:
                name = key.name if hasattr(key, "name") else key.char
            except Exception:
                name = str(key)
            if name:
                pressed.add(name)

        def on_release(key):
            mods = [k for k in pressed if k in MOD_NAMES]
            chars = [k for k in pressed if k not in MOD_NAMES]
            if chars:
                parts = [f"<{m}>" for m in sorted(mods)] + chars
                self.captured.emit("".join(parts))
                self._stop.set()

        listener = _kb.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        self._stop.wait(timeout=15)
        listener.stop()

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
# ViewerToast
# ---------------------------------------------------------------------------

class ViewerToast(QWidget):
    """Always-on-top floating toast showing per-region screenshot tiles."""

    refresh_requested  = pyqtSignal()
    map_requested      = pyqtSignal()
    drag_ended         = pyqtSignal()   # emitted after any card drag completes/cancels
    region_renamed          = pyqtSignal(str, str)  # old_name, new_name
    region_deleted          = pyqtSignal(str)        # name
    region_remap_requested  = pyqtSignal(str)        # name
    region_reordered        = pyqtSignal(str, str)   # source_name, target_name
    region_click_requested  = pyqtSignal(str, float, float)  # name, rel_x, rel_y
    run_chain_requested     = pyqtSignal(str)         # chain_id

    # Internal signals for thread-safe calls from background threads
    _sig_update  = pyqtSignal(object)   # list[tuple[str, QPixmap]]
    _sig_loading = pyqtSignal(bool)
    _sig_stream  = pyqtSignal(object)   # list[Region]

    def __init__(self, target_name: str = "", parent=None):
        super().__init__(parent)

        self._drag_pos: QPoint | None = None
        self._pinned = True
        self._region_widgets: list[_RegionWidget] = []
        self._streamers: list = []
        self._card_dragging = False  # True while a card QDrag is in exec()
        self._layout_horizontal = False
        self._scripts_target_key: str = ""

        self._setup_window()
        self._build_ui()
        self._apply_stylesheet()
        self._position_top_right()

        if target_name:
            self.set_target_name(target_name)

        # Wire internal signals to main-thread handlers
        self._sig_update.connect(self._do_update_regions)
        self._sig_loading.connect(self._do_set_loading)
        self._sig_stream.connect(self._do_start_streaming)

        # Debounce timer for resize re-render
        self._resize_debounce = QTimer()
        self._resize_debounce.setSingleShot(True)
        self._resize_debounce.timeout.connect(self._rerender_all)

    # ------------------------------------------------------------------
    # Window setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setMinimumSize(0, 0)
        self.resize(380, 560)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def _position_top_right(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.move(0, 0)
            return
        geom = screen.availableGeometry()
        x = geom.right() - self.width() - 20
        y = geom.top() + 20
        self.move(x, y)

    # ------------------------------------------------------------------
    # All-desktops pinning (KDE Plasma / EWMH)
    # ------------------------------------------------------------------

    def show_on_all_desktops(self) -> None:
        """Pin window to all virtual desktops using EWMH (xprop) + wmctrl sticky."""
        import subprocess
        wid = hex(int(self.winId()))
        subprocess.Popen(
            ["xprop", "-id", wid, "-f", "_NET_WM_DESKTOP", "32c",
             "-set", "_NET_WM_DESKTOP", "0xFFFFFFFF"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        subprocess.Popen(
            ["wmctrl", "-i", "-r", wid, "-b", "add,sticky"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(250, self.show_on_all_desktops)

    # ------------------------------------------------------------------
    # Resize grip placement
    # ------------------------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        grip_size = self._grip.sizeHint()
        self._grip.move(
            self.width() - grip_size.width(),
            self.height() - grip_size.height()
        )
        self._grip.raise_()
        # Debounce: re-render images 80ms after resize stops to avoid thrashing
        self._resize_debounce.start(80)

    def _rerender_all(self):
        for rw in self._region_widgets:
            rw._render_pixmap(smooth=True)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Do not let the layout enforce a minimum window size — user can resize freely
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        self._header_bar   = self._build_header()
        self._status_bar   = self._build_status_bar()
        self._mini_bar     = self._build_mini_bar()
        self._footer_bar   = self._build_footer()
        self._mini_bar.hide()

        self._content_stack = QStackedWidget()
        self._content_stack.addWidget(self._build_content_area())   # index 0 = regions
        self._content_stack.addWidget(self._build_scripts_panel())  # index 1 = scripts

        outer.addWidget(self._header_bar)
        outer.addWidget(self._status_bar)
        outer.addWidget(self._mini_bar)
        outer.addWidget(self._content_stack, stretch=1)
        outer.addWidget(self._footer_bar)

        # Resize grip (must exist before resizeEvent fires)
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)
        self._grip.raise_()

        self._header_visible = True

    def _build_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setObjectName("headerBar")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        # Colored dot
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {_ACCENT}; font-size: 10px;")

        # Target name
        self._target_label = QLabel("—")
        self._target_label.setMaximumWidth(160)
        self._target_label.setStyleSheet("color: #ffffff; font-size: 12px;")

        layout.addWidget(dot)
        layout.addWidget(self._target_label)
        layout.addStretch()

        # Tab toggles
        self._tab_regions_btn = QPushButton("Regions")
        self._tab_regions_btn.setFixedHeight(22)
        self._tab_regions_btn.setCheckable(True)
        self._tab_regions_btn.setChecked(True)
        self._tab_regions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tab_regions_btn.setStyleSheet(_TAB_ACTIVE)
        self._tab_regions_btn.clicked.connect(lambda: self._switch_tab("regions"))

        self._tab_scripts_btn = QPushButton("Scripts")
        self._tab_scripts_btn.setFixedHeight(22)
        self._tab_scripts_btn.setCheckable(True)
        self._tab_scripts_btn.setChecked(False)
        self._tab_scripts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tab_scripts_btn.setStyleSheet(_TAB_INACTIVE)
        self._tab_scripts_btn.clicked.connect(lambda: self._switch_tab("scripts"))

        layout.addWidget(self._tab_regions_btn)
        layout.addWidget(self._tab_scripts_btn)

        # Map button
        self._map_btn = QPushButton("⬜ Map")
        self._map_btn.setFixedHeight(22)
        self._map_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._map_btn.setStyleSheet(_BTN_INDIGO)
        self._map_btn.clicked.connect(self.map_requested.emit)

        # Refresh button
        self._refresh_btn = QPushButton("⟳ Refresh")
        self._refresh_btn.setFixedHeight(22)
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.setStyleSheet(_BTN_GRAY)
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)

        # Close button
        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedHeight(22)
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(_BTN_CLOSE)
        self._close_btn.clicked.connect(self.hide)

        self._collapse_btn = QPushButton("▲")
        self._collapse_btn.setFixedSize(22, 22)
        self._collapse_btn.setToolTip("Hide header")
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.setStyleSheet(
            f"QPushButton {{ background: #2a2a2a; color: {_ACCENT}; border: none;"
            " border-radius: 3px; font-size: 10px; padding: 0; }"
            "QPushButton:hover { background: #3a3a3a; }"
        )
        self._collapse_btn.clicked.connect(self._toggle_header)

        layout.addWidget(self._map_btn)
        layout.addWidget(self._refresh_btn)
        layout.addWidget(self._close_btn)
        layout.addWidget(self._collapse_btn)

        return bar

    def _build_mini_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(20)
        bar.setObjectName("miniBar")
        bar.setStyleSheet(
            f"QWidget#miniBar {{ background: {_BG_HEADER};"
            f" border-bottom: 1px solid {_BORDER}; }}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)

        self._mini_label = QLabel("—")
        self._mini_label.setStyleSheet(
            f"color: {_TEXT_DIM}; font-size: 10px; background: transparent;"
        )

        expand_btn = QPushButton("▼")
        expand_btn.setFixedSize(22, 16)
        expand_btn.setToolTip("Show header")
        expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        expand_btn.setStyleSheet(
            f"QPushButton {{ background: #2a2a2a; color: {_ACCENT}; border: none;"
            " border-radius: 3px; font-size: 9px; padding: 0; }"
            "QPushButton:hover { background: #3a3a3a; }"
        )
        expand_btn.clicked.connect(self._toggle_header)

        layout.addWidget(expand_btn)
        layout.addWidget(self._mini_label)
        layout.addStretch()
        return bar

    def _toggle_header(self) -> None:
        self._header_visible = not self._header_visible
        lean = not self._header_visible
        self._header_bar.setVisible(self._header_visible)
        self._status_bar.setVisible(self._header_visible)
        self._footer_bar.setVisible(self._header_visible)
        self._mini_bar.setVisible(lean)
        if lean:
            self._mini_label.setText(self._target_label.text())
        self._content_layout.setSpacing(0 if lean else 4)
        self._content_layout.setContentsMargins(*((0, 0, 0, 0) if lean else (4, 4, 4, 4)))
        for rw in self._region_widgets:
            rw.set_lean(lean)

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(22)
        bar.setObjectName("statusBar")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(8)

        self._status_label = QLabel("—")
        self._status_label.setStyleSheet(
            f"color: {_TEXT_DIM}; font-size: 11px;"
        )

        layout.addWidget(self._status_label)
        layout.addStretch()

        return bar

    def _build_content_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG}; border: none; }}"
            + _SCROLLBAR_STYLE
        )

        scroll.setMinimumSize(0, 0)

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet(f"background: {_BG};")
        self._content_widget.setMinimumSize(0, 0)
        self._content_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(4, 4, 4, 4)
        self._content_layout.setSpacing(4)
        self._content_layout.addStretch()

        scroll.setWidget(self._content_widget)
        self._scroll_area = scroll
        return scroll

    def _build_footer(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(28)
        bar.setObjectName("footerBar")

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(6)

        self._count_label = QLabel("0 regions")
        self._count_label.setStyleSheet(
            f"color: {_TEXT_DIM}; font-size: 11px;"
        )

        self._pin_btn = QPushButton("📌 Pin")
        self._pin_btn.setFixedHeight(22)
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(True)
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.setStyleSheet(_BTN_INDIGO_CHECKED)
        self._pin_btn.toggled.connect(self._on_pin_toggled)

        self._layout_btn = QPushButton("⇄")
        self._layout_btn.setFixedHeight(22)
        self._layout_btn.setCheckable(True)
        self._layout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._layout_btn.setStyleSheet(_BTN_GRAY)
        self._layout_btn.setToolTip("Toggle side-by-side layout")
        self._layout_btn.toggled.connect(self._on_layout_toggled)

        layout.addWidget(self._count_label)
        layout.addStretch()
        layout.addWidget(self._layout_btn)
        layout.addWidget(self._pin_btn)

        return bar

    def _build_scripts_panel(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG}; border: none; }}"
            + _SCROLLBAR_STYLE
        )

        container = QWidget()
        container.setStyleSheet(f"background: {_BG};")
        self._scripts_layout = QVBoxLayout(container)
        self._scripts_layout.setContentsMargins(4, 4, 4, 4)
        self._scripts_layout.setSpacing(4)
        self._scripts_layout.addStretch()

        self._scripts_list_widget = container
        scroll.setWidget(container)
        self._scripts_scroll = scroll
        return scroll

    def _switch_tab(self, tab: str) -> None:
        if tab == "regions":
            self._content_stack.setCurrentIndex(0)
            self._tab_regions_btn.setStyleSheet(_TAB_ACTIVE)
            self._tab_scripts_btn.setStyleSheet(_TAB_INACTIVE)
        else:
            self._content_stack.setCurrentIndex(1)
            self._tab_regions_btn.setStyleSheet(_TAB_INACTIVE)
            self._tab_scripts_btn.setStyleSheet(_TAB_ACTIVE)
            self._reload_scripts()

    def _reload_scripts(self) -> None:
        # Clear existing rows (keep the trailing stretch at the end)
        while self._scripts_layout.count() > 1:
            item = self._scripts_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        chains = get_click_store().load_chains(self._scripts_target_key, "_overlay")

        if not chains:
            empty = QLabel(
                "No scripts yet.\nOpen the mapping overlay,\nswitch to Dot mode, and save a script."
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {_TEXT_DIM}; font-size: 11px; padding: 20px;")
            self._scripts_layout.insertWidget(0, empty)
            return

        for chain in chains:
            row = self._build_chain_row(chain)
            self._scripts_layout.insertWidget(self._scripts_layout.count() - 1, row)

    def _build_chain_row(self, chain: ClickChain) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{ background: {_BG_HEADER}; border: 1px solid {_BORDER};"
            " border-radius: 4px; margin: 2px 4px; }}"
        )

        outer = QVBoxLayout(frame)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(4)

        # Name row
        name_lbl = QLabel(chain.name)
        name_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 11px; font-weight: bold;"
            " background: transparent; border: none;"
        )
        outer.addWidget(name_lbl)

        # Hotkey row
        hotkey_row = QWidget()
        hotkey_row.setStyleSheet("background: transparent;")
        hk_layout = QHBoxLayout(hotkey_row)
        hk_layout.setContentsMargins(0, 0, 0, 0)
        hk_layout.setSpacing(4)

        hk_lbl = QLabel("Hotkey:")
        hk_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 11px; background: transparent; border: none;")

        hk_field = QLineEdit(chain.hotkey)
        hk_field.setFixedHeight(20)
        hk_field.setPlaceholderText("e.g. <ctrl>1")
        hk_field.setStyleSheet(
            f"QLineEdit {{ background: #222; color: {_TEXT}; border: 1px solid {_BORDER};"
            " border-radius: 3px; font-size: 11px; padding: 0 4px; }}"
        )

        rec_btn = QPushButton("Rec")
        rec_btn.setFixedHeight(20)
        rec_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rec_btn.setStyleSheet(_BTN_GRAY)

        hk_layout.addWidget(hk_lbl)
        hk_layout.addWidget(hk_field, stretch=1)
        hk_layout.addWidget(rec_btn)
        outer.addWidget(hotkey_row)

        # Action row
        action_row = QWidget()
        action_row.setStyleSheet("background: transparent;")
        act_layout = QHBoxLayout(action_row)
        act_layout.setContentsMargins(0, 0, 0, 0)
        act_layout.setSpacing(4)

        run_btn = QPushButton("▶ Run")
        run_btn.setFixedHeight(22)
        run_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        run_btn.setStyleSheet(_BTN_INDIGO)

        del_btn = QPushButton("🗑")
        del_btn.setFixedHeight(22)
        del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        del_btn.setStyleSheet(_BTN_GRAY)

        act_layout.addStretch()
        act_layout.addWidget(run_btn)
        act_layout.addWidget(del_btn)
        outer.addWidget(action_row)

        # Wire hotkey save on editingFinished
        chain_id = chain.id
        chain_region = chain.region_name

        def _save_hotkey():
            new_hk = hk_field.text().strip()
            store = get_click_store()
            chains_list = store.load_chains(self._scripts_target_key, chain_region)
            for c in chains_list:
                if c.id == chain_id:
                    c.hotkey = new_hk
                    break
            store.save_chains(self._scripts_target_key, chain_region, chains_list)

        hk_field.editingFinished.connect(_save_hotkey)

        # Wire Rec button
        self._hotkey_capture: _HotkeyCapture | None = None

        def _start_rec():
            cap = _HotkeyCapture()
            cap.captured.connect(lambda combo: (hk_field.setText(combo), cap.stop()))
            cap.start()
            self._hotkey_capture = cap

        rec_btn.clicked.connect(_start_rec)

        # Wire Run button
        run_btn.clicked.connect(lambda: self.run_chain_requested.emit(chain_id))

        # Wire Delete button
        def _delete_chain():
            store = get_click_store()
            chains_list = store.load_chains(self._scripts_target_key, chain_region)
            chains_list = [c for c in chains_list if c.id != chain_id]
            store.save_chains(self._scripts_target_key, chain_region, chains_list)
            self._reload_scripts()

        del_btn.clicked.connect(_delete_chain)

        return frame

    def _on_layout_toggled(self, horizontal: bool) -> None:
        self._layout_horizontal = horizontal
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        QWidget().setLayout(self._content_layout)

        if horizontal:
            new_layout = QHBoxLayout(self._content_widget)
            self._layout_btn.setStyleSheet(_BTN_INDIGO)
        else:
            new_layout = QVBoxLayout(self._content_widget)
            self._layout_btn.setStyleSheet(_BTN_GRAY)

        new_layout.setContentsMargins(4, 4, 4, 4)
        new_layout.setSpacing(4)
        new_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self._content_layout = new_layout

        for rw in self._region_widgets:
            rw.setParent(self._content_widget)
            self._content_layout.addWidget(rw)

        if not horizontal:
            self._content_layout.addStretch()

    # ------------------------------------------------------------------
    # Stylesheet
    # ------------------------------------------------------------------

    def _apply_stylesheet(self):
        self.setStyleSheet(f"""
            ViewerToast {{
                background-color: {_BG};
                border: 1px solid {_BORDER};
                border-radius: 6px;
            }}
            QWidget#headerBar {{
                background-color: {_BG_HEADER};
                border-bottom: 1px solid {_BORDER};
            }}
            QWidget#statusBar {{
                background-color: #141414;
            }}
            QWidget#footerBar {{
                background-color: {_BG_HEADER};
                border-top: 1px solid {_BORDER};
            }}
            QCheckBox::indicator {{
                width: 12px;
                height: 12px;
                border: 1px solid #444;
                border-radius: 2px;
                background: #222;
            }}
            QCheckBox::indicator:checked {{
                background: {_ACCENT};
                border-color: {_ACCENT};
            }}
        """)

    # ------------------------------------------------------------------
    # Edge detection helpers for resize
    # ------------------------------------------------------------------

    _EDGE = 7  # px from border that activates resize cursor

    def _edge_at(self, pos):
        x, y, w, h, e = pos.x(), pos.y(), self.width(), self.height(), self._EDGE
        top    = y < e
        bottom = y > h - e
        left   = x < e
        right  = x > w - e
        if top    and left:  return Qt.Edge.TopEdge    | Qt.Edge.LeftEdge
        if top    and right: return Qt.Edge.TopEdge    | Qt.Edge.RightEdge
        if bottom and left:  return Qt.Edge.BottomEdge | Qt.Edge.LeftEdge
        if bottom and right: return Qt.Edge.BottomEdge | Qt.Edge.RightEdge
        if top:    return Qt.Edge.TopEdge
        if bottom: return Qt.Edge.BottomEdge
        if left:   return Qt.Edge.LeftEdge
        if right:  return Qt.Edge.RightEdge
        return None

    _EDGE_CURSORS = {
        Qt.Edge.TopEdge:                                   Qt.CursorShape.SizeVerCursor,
        Qt.Edge.BottomEdge:                                Qt.CursorShape.SizeVerCursor,
        Qt.Edge.LeftEdge:                                  Qt.CursorShape.SizeHorCursor,
        Qt.Edge.RightEdge:                                 Qt.CursorShape.SizeHorCursor,
        Qt.Edge.TopEdge    | Qt.Edge.LeftEdge:             Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.BottomEdge | Qt.Edge.RightEdge:            Qt.CursorShape.SizeFDiagCursor,
        Qt.Edge.TopEdge    | Qt.Edge.RightEdge:            Qt.CursorShape.SizeBDiagCursor,
        Qt.Edge.BottomEdge | Qt.Edge.LeftEdge:             Qt.CursorShape.SizeBDiagCursor,
    }

    # ------------------------------------------------------------------
    # Drag to move (header) + edge resize
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge is not None:
                win = self.windowHandle()
                if win:
                    win.startSystemResize(edge)
                return
            drag_zone = self._header_bar.height() if self._header_visible else self._mini_bar.height()
            if event.position().y() < drag_zone:
                self._drag_pos = event.globalPosition().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not event.buttons():
            edge = self._edge_at(event.position().toPoint())
            cursor = self._EDGE_CURSORS.get(edge, Qt.CursorShape.ArrowCursor)
            self.setCursor(cursor)
        elif (self._drag_pos is not None
              and event.buttons() & Qt.MouseButton.LeftButton):
            new_global = event.globalPosition().toPoint()
            delta = new_global - self._drag_pos
            self._drag_pos = new_global
            self.move(self.pos() + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_pin_toggled(self, checked: bool):
        self._pinned = checked
        was_visible = self.isVisible()
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
            self._pin_btn.setStyleSheet(_BTN_INDIGO_CHECKED)
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
            self._pin_btn.setStyleSheet(_BTN_PIN_UNPINNED)
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def _on_reorder(self, source: str, target: str) -> None:
        self.region_reordered.emit(source, target)

    def _on_rename_region(self, name: str) -> None:
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self, "Rename Region", "New name:", text=name
        )
        if ok and new_name.strip() and new_name.strip() != name:
            self.region_renamed.emit(name, new_name.strip())

    def _on_delete_region(self, name: str) -> None:
        self.region_deleted.emit(name)

    def _on_remap_region(self, name: str) -> None:
        self.region_remap_requested.emit(name)

    def _do_update_regions(self, region_tiles: list[tuple[str, QPixmap]]) -> None:
        """Main-thread slot: rebuild all region widgets."""
        if self._card_dragging:
            return  # never delete widgets while a QDrag.exec() is running
        # Remove existing widgets (all but the trailing stretch)
        for rw in self._region_widgets:
            self._content_layout.removeWidget(rw)
            rw.deleteLater()
        self._region_widgets.clear()

        for name, pixmap in region_tiles:
            rw = _RegionWidget(name, pixmap, self._content_widget)
            if self._layout_horizontal:
                self._content_layout.addWidget(rw)
            else:
                # Insert before the trailing stretch (last item)
                insert_idx = self._content_layout.count() - 1
                self._content_layout.insertWidget(insert_idx, rw)
            self._region_widgets.append(rw)
            rw.rename_requested.connect(self._on_rename_region)
            rw.delete_requested.connect(self._on_delete_region)
            rw.remap_requested.connect(self._on_remap_region)
            rw.reorder_requested.connect(self._on_reorder)
            rw.click_requested.connect(self.region_click_requested)

        count = len(region_tiles)
        self._count_label.setText(
            f"{count} region{'s' if count != 1 else ''}"
        )
        now = datetime.now().strftime("%H:%M:%S")
        self._status_label.setText(f"Updated {now}")
        if not self._header_visible:
            for rw in self._region_widgets:
                rw.set_lean(True)

    def _do_set_loading(self, loading: bool) -> None:
        if loading:
            self._loading_label.show()
        else:
            self._loading_label.hide()

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def start_streaming(self, regions: list, target=None) -> None:
        """Thread-safe. Stop existing streams, create widgets, start one streamer per region."""
        self._sig_stream.emit((list(regions), target))

    def _stop_streamers(self) -> None:
        for s in self._streamers:
            s.stop()
        self._streamers.clear()

    def _do_start_streaming(self, payload: tuple) -> None:
        """Main-thread slot: rebuild widgets and launch a RegionStreamer per region."""
        from rearview.region_streamer import RegionStreamer

        regions, target = payload
        self._stop_streamers()

        if self._card_dragging:
            return

        for rw in self._region_widgets:
            self._content_layout.removeWidget(rw)
            rw.deleteLater()
        self._region_widgets.clear()

        for region in regions:
            rw = _RegionWidget(region.name, parent=self._content_widget)
            if self._layout_horizontal:
                self._content_layout.addWidget(rw)
            else:
                self._content_layout.insertWidget(self._content_layout.count() - 1, rw)
            self._region_widgets.append(rw)
            rw.rename_requested.connect(self._on_rename_region)
            rw.delete_requested.connect(self._on_delete_region)
            rw.remap_requested.connect(self._on_remap_region)
            rw.reorder_requested.connect(self._on_reorder)
            rw.click_requested.connect(self.region_click_requested)

            streamer = RegionStreamer(region, target=target, parent=self)
            rw.connect_streamer(streamer)
            streamer.start()
            self._streamers.append(streamer)

        if not self._header_visible:
            for rw in self._region_widgets:
                rw.set_lean(True)

        count = len(regions)
        self._count_label.setText(f"{count} region{'s' if count != 1 else ''}")
        self._status_label.setText("Streaming")

    def closeEvent(self, event) -> None:
        self._stop_streamers()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_regions(self, region_tiles: list[tuple[str, QPixmap]]) -> None:
        """Thread-safe via _sig_update. Replace all region widgets."""
        self._sig_update.emit(region_tiles)

    def set_loading(self, loading: bool) -> None:
        """Show/hide loading label. Thread-safe via _sig_loading."""
        self._sig_loading.emit(loading)

    def set_target_name(self, name: str) -> None:
        self._target_label.setText(name)
        self._mini_label.setText(name)

    def set_scripts_target(self, target_key: str) -> None:
        self._scripts_target_key = target_key
