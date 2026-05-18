"""
region_mapper.py — PyQt6 fullscreen transparent overlay for defining named capture regions.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QRect, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QInputDialog, QMenu,
    QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QScrollArea, QDoubleSpinBox, QSplitter,
)

from rearview.click_store import ClickDot, ClickChain, ChainStep, get_click_store


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class Region:
    name: str
    x: int
    y: int
    w: int
    h: int
    color: str = "#6366f1"


# ---------------------------------------------------------------------------
# RegionStore
# ---------------------------------------------------------------------------

class RegionStore:
    STORE_PATH = Path(__file__).parent.parent / "config" / "regions.json"

    def _load_all(self) -> dict:
        if not self.STORE_PATH.exists():
            return {}
        with open(self.STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_all(self, data: dict) -> None:
        self.STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(self.STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load(self, target_key: str) -> list[Region]:
        data = self._load_all()
        raw = data.get(target_key, [])
        return [Region(**{k: v for k, v in r.items() if k in Region.__dataclass_fields__}) for r in raw]

    def save(self, target_key: str, regions: list[Region]) -> None:
        data = self._load_all()
        data[target_key] = [asdict(r) for r in regions]
        self._save_all(data)

    def clear(self, target_key: str) -> None:
        data = self._load_all()
        data.pop(target_key, None)
        self._save_all(data)


# ---------------------------------------------------------------------------
# _ClickRecorderThread
# ---------------------------------------------------------------------------

class _ClickRecorderThread(QThread):
    """Records left-clicks and maps them to nearest ClickDot within 20px."""

    recorded = pyqtSignal(list)  # list of (dot_label, delay_before) tuples

    def __init__(self, dots: list[ClickDot], parent=None) -> None:
        super().__init__(parent)
        self._dots = dots
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            from pynput import mouse as _mouse
        except ImportError:
            self.recorded.emit([])
            return

        clicks: list[tuple[float, int, int]] = []

        def on_click(x, y, button, pressed):
            if self._stop_event.is_set():
                return False
            if pressed and button.name == "left":
                clicks.append((time.time(), int(x), int(y)))

        with _mouse.Listener(on_click=on_click) as lst:
            self._stop_event.wait(timeout=60)
            lst.stop()

        steps: list[tuple[str, float]] = []
        prev_t: Optional[float] = None
        for t, x, y in clicks:
            delay = 0.0 if prev_t is None else t - prev_t
            prev_t = t
            best_dot: Optional[ClickDot] = None
            best_dist = 20 * 20 + 1
            for dot in self._dots:
                dx = x - int(dot.rx)
                dy = y - int(dot.ry)
                dist = dx * dx + dy * dy
                if dist <= 20 * 20 and dist < best_dist:
                    best_dist = dist
                    best_dot = dot
            if best_dot is not None:
                steps.append((best_dot.label, delay))

        self.recorded.emit(steps)


# ---------------------------------------------------------------------------
# _HotkeyCapture
# ---------------------------------------------------------------------------

class _HotkeyCapture(QThread):
    """Captures one key combo via pynput and emits it as a formatted string."""

    captured = pyqtSignal(str)

    _MOD_ORDER = ["ctrl", "alt", "shift", "cmd"]

    def run(self) -> None:
        try:
            from pynput import keyboard as _kb
        except ImportError:
            self.captured.emit("")
            return

        held: set[str] = set()
        done = threading.Event()
        result: list[str] = []

        def _fmt(mods: list[str], key_name: str) -> str:
            parts = [f"<{m}>" for m in self._MOD_ORDER if m in mods]
            if key_name and key_name not in self._MOD_ORDER:
                parts.append(f"<{key_name}>" if len(key_name) > 1 else key_name)
            return "".join(parts)

        def _key_name(key) -> str:
            try:
                return key.char or ""
            except AttributeError:
                name = str(key).replace("Key.", "")
                for alias, canonical in (
                    ("ctrl_l", "ctrl"), ("ctrl_r", "ctrl"),
                    ("alt_l", "alt"), ("alt_r", "alt"),
                    ("shift", "shift"), ("shift_l", "shift"), ("shift_r", "shift"),
                    ("cmd", "cmd"), ("cmd_l", "cmd"), ("cmd_r", "cmd"),
                    ("super_l", "cmd"), ("super_r", "cmd"),
                ):
                    if name == alias:
                        return canonical
                return name

        _MODIFIERS = {"ctrl", "alt", "shift", "cmd"}

        def on_press(key):
            held.add(_key_name(key))

        def on_release(key):
            name = _key_name(key)
            mods = [m for m in held if m in _MODIFIERS]
            non_mods = [k for k in held if k not in _MODIFIERS]
            if not done.is_set() and held:
                key_part = non_mods[0] if non_mods else (mods[0] if mods else name)
                mods_only = [m for m in mods if m != key_part]
                result.append(_fmt(mods_only, key_part))
                done.set()
            held.discard(name)
            if done.is_set():
                return False

        with _kb.Listener(on_press=on_press, on_release=on_release) as lst:
            done.wait(timeout=10)
            lst.stop()

        self.captured.emit(result[0] if result else "")


# ---------------------------------------------------------------------------
# _DotScriptPanel
# ---------------------------------------------------------------------------

class _DotScriptPanel(QWidget):
    """Floating dark panel for building click-chain scripts from overlay dots."""

    chain_saved = pyqtSignal(ClickChain)

    _ACCENT = "#6366f1"
    _BG = "rgba(15,15,15,230)"
    _BORDER = "#2a2a2a"
    _TEXT = "#e5e7eb"
    _MUTED = "#6b7280"
    _INPUT_BG = "#1a1a1a"

    def __init__(self, target_key: str, parent=None) -> None:
        super().__init__(parent)
        self._target_key = target_key
        self._steps: list[tuple[ClickDot, float]] = []
        self._dots: list[ClickDot] = []
        self._recorder: Optional[_ClickRecorderThread] = None
        self._capture_worker: Optional[_HotkeyCapture] = None

        self.setFixedWidth(290)
        self.setStyleSheet(
            f"background: {self._BG};"
            f"border: 1px solid {self._BORDER};"
            "border-radius: 8px;"
        )

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # Title
        title = QLabel("Scripts")
        title.setStyleSheet(
            f"color: {self._TEXT}; font-size: 13px; font-weight: bold;"
            "border: none; background: transparent;"
        )
        root.addWidget(title)

        _sep_style = f"color: {self._MUTED}; font-size: 11px; border: none; background: transparent;"

        # Available dots label
        dots_lbl = QLabel("Available dots:")
        dots_lbl.setStyleSheet(_sep_style)
        root.addWidget(dots_lbl)

        # Chip row (scrollable)
        self._chip_scroll = QScrollArea()
        self._chip_scroll.setFixedHeight(36)
        self._chip_scroll.setWidgetResizable(True)
        self._chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._chip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._chip_scroll.setStyleSheet(
            f"background: transparent; border: 1px solid {self._BORDER}; border-radius: 4px;"
        )
        self._chip_container = QWidget()
        self._chip_layout = QHBoxLayout(self._chip_container)
        self._chip_layout.setContentsMargins(4, 2, 4, 2)
        self._chip_layout.setSpacing(4)
        self._chip_layout.addStretch()
        self._chip_scroll.setWidget(self._chip_container)
        root.addWidget(self._chip_scroll)

        # Sequence label
        seq_lbl = QLabel("Sequence:")
        seq_lbl.setStyleSheet(_sep_style)
        root.addWidget(seq_lbl)

        # Sequence list
        self._seq_list = QListWidget()
        self._seq_list.setFixedHeight(120)
        self._seq_list.setStyleSheet(
            "QListWidget {"
            f"  background: #111; border: 1px solid {self._BORDER};"
            f"  color: {self._TEXT}; font-size: 12px; border-radius: 4px;"
            "}"
            "QListWidget::item:selected {"
            f"  background: {self._ACCENT}; color: white;"
            "}"
        )
        self._seq_list.itemDoubleClicked.connect(self._edit_step_delay)
        root.addWidget(self._seq_list)

        # Reorder / delete buttons
        order_row = QHBoxLayout()
        order_row.setSpacing(4)
        for label, slot in (("↑", self._move_up), ("↓", self._move_down), ("×", self._remove_step)):
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setStyleSheet(self._btn_style())
            btn.clicked.connect(slot)
            order_row.addWidget(btn)
        order_row.addStretch()
        root.addLayout(order_row)

        # Name field
        name_lbl = QLabel("Name:")
        name_lbl.setStyleSheet(_sep_style)
        root.addWidget(name_lbl)
        self._name_edit = QLineEdit()
        self._name_edit.setFixedHeight(26)
        self._name_edit.setPlaceholderText("Script name…")
        self._name_edit.setStyleSheet(
            f"background: {self._INPUT_BG}; border: 1px solid {self._BORDER};"
            f"color: {self._TEXT}; border-radius: 4px; padding: 0 6px; font-size: 12px;"
        )
        root.addWidget(self._name_edit)

        # Hotkey field
        hotkey_lbl = QLabel("Hotkey:")
        hotkey_lbl.setStyleSheet(_sep_style)
        root.addWidget(hotkey_lbl)
        hk_row = QHBoxLayout()
        hk_row.setSpacing(4)
        self._hotkey_edit = QLineEdit()
        self._hotkey_edit.setFixedHeight(26)
        self._hotkey_edit.setReadOnly(True)
        self._hotkey_edit.setPlaceholderText("None")
        self._hotkey_edit.setStyleSheet(
            f"background: {self._INPUT_BG}; border: 1px solid {self._BORDER};"
            f"color: {self._TEXT}; border-radius: 4px; padding: 0 6px; font-size: 12px;"
        )
        self._rec_btn = QPushButton("Rec")
        self._rec_btn.setFixedHeight(26)
        self._rec_btn.setStyleSheet(self._btn_style())
        self._rec_btn.clicked.connect(self._start_hotkey_capture)
        hk_row.addWidget(self._hotkey_edit)
        hk_row.addWidget(self._rec_btn)
        root.addLayout(hk_row)

        # Record / Save buttons
        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        self._record_btn = QPushButton("⏺ Record")
        self._record_btn.setFixedHeight(26)
        self._record_btn.setStyleSheet(self._btn_style())
        self._record_btn.clicked.connect(self._toggle_record)
        self._save_btn = QPushButton("💾 Save")
        self._save_btn.setFixedHeight(26)
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background: {self._ACCENT}; color: white; border: none;"
            "  border-radius: 4px; font-size: 12px; height: 26px; }}"
            f"QPushButton:hover {{ background: #4f46e5; }}"
        )
        self._save_btn.clicked.connect(self._save_chain)
        action_row.addWidget(self._record_btn)
        action_row.addWidget(self._save_btn)
        root.addLayout(action_row)

        self.adjustSize()

    def _btn_style(self) -> str:
        return (
            f"QPushButton {{ background: #1e1e1e; color: {self._TEXT}; border: 1px solid {self._BORDER};"
            "  border-radius: 4px; font-size: 12px; height: 26px; padding: 0 8px; }}"
            "QPushButton:hover { background: #2a2a2a; }"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_dots(self, dots: list[ClickDot]) -> None:
        """Repopulate the available-dots chip row."""
        self._dots = list(dots)
        # Clear existing chips (leave stretch at end)
        while self._chip_layout.count() > 1:
            item = self._chip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for dot in self._dots:
            chip = QPushButton(dot.label)
            chip.setFixedHeight(24)
            chip.setStyleSheet(
                f"QPushButton {{ background: #1e1e1e; color: {self._TEXT};"
                f"  border: 1px solid {self._BORDER}; border-radius: 4px;"
                "  font-size: 11px; padding: 0 8px; }}"
                "QPushButton:hover { background: #2a2a2a; }"
            )
            # Capture dot by value via default arg
            chip.clicked.connect(lambda _checked, d=dot: self._append_step(d))
            self._chip_layout.insertWidget(self._chip_layout.count() - 1, chip)

    def add_recorded_steps(self, steps: list[tuple[str, float]]) -> None:
        """Append recorded steps: list of (dot_label, delay_before_seconds)."""
        label_to_dot = {d.label: d for d in self._dots}
        for label, delay in steps:
            dot = label_to_dot.get(label)
            if dot is not None:
                self._append_step(dot, delay)

    # ------------------------------------------------------------------
    # Sequence helpers
    # ------------------------------------------------------------------

    def _append_step(self, dot: ClickDot, delay: float = 0.5) -> None:
        self._steps.append((dot, delay))
        item = QListWidgetItem(f"{dot.label}  →  {delay:.1f}s")
        self._seq_list.addItem(item)

    def _edit_step_delay(self, item: QListWidgetItem) -> None:
        row = self._seq_list.row(item)
        if row < 0 or row >= len(self._steps):
            return
        dot, current_delay = self._steps[row]
        new_delay, ok = QInputDialog.getDouble(
            self, "Edit delay", f"Delay before '{dot.label}' (seconds):",
            value=current_delay, min=0.0, max=60.0, decimals=2,
        )
        if ok:
            self._steps[row] = (dot, new_delay)
            item.setText(f"{dot.label}  →  {new_delay:.1f}s")

    def _move_up(self) -> None:
        row = self._seq_list.currentRow()
        if row <= 0:
            return
        self._steps[row - 1], self._steps[row] = self._steps[row], self._steps[row - 1]
        self._refresh_list(row - 1)

    def _move_down(self) -> None:
        row = self._seq_list.currentRow()
        if row < 0 or row >= len(self._steps) - 1:
            return
        self._steps[row], self._steps[row + 1] = self._steps[row + 1], self._steps[row]
        self._refresh_list(row + 1)

    def _remove_step(self) -> None:
        row = self._seq_list.currentRow()
        if row < 0 or row >= len(self._steps):
            return
        self._steps.pop(row)
        self._seq_list.takeItem(row)

    def _refresh_list(self, select_row: int) -> None:
        self._seq_list.clear()
        for dot, delay in self._steps:
            self._seq_list.addItem(f"{dot.label}  →  {delay:.1f}s")
        self._seq_list.setCurrentRow(select_row)

    # ------------------------------------------------------------------
    # Hotkey capture
    # ------------------------------------------------------------------

    def _start_hotkey_capture(self) -> None:
        self._rec_btn.setEnabled(False)
        self._hotkey_edit.setPlaceholderText("Press combo…")
        self._capture_worker = _HotkeyCapture(parent=self)
        self._capture_worker.captured.connect(self._on_hotkey_captured)
        self._capture_worker.start()

    def _on_hotkey_captured(self, combo: str) -> None:
        self._hotkey_edit.setText(combo)
        self._rec_btn.setEnabled(True)
        self._hotkey_edit.setPlaceholderText("None")

    # ------------------------------------------------------------------
    # Record clicks
    # ------------------------------------------------------------------

    def _toggle_record(self) -> None:
        if self._recorder is not None and self._recorder.isRunning():
            self._recorder.stop()
            self._recorder.wait()
            self._recorder = None
            self._record_btn.setText("⏺ Record")
        else:
            self._recorder = _ClickRecorderThread(self._dots, parent=self)
            self._recorder.recorded.connect(self._on_recorded)
            self._recorder.start()
            self._record_btn.setText("⏹ Stop")

    def _on_recorded(self, steps: list[tuple[str, float]]) -> None:
        self._record_btn.setText("⏺ Record")
        self._recorder = None
        self.add_recorded_steps(steps)

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_chain(self) -> None:
        name = self._name_edit.text().strip() or "Unnamed"
        chain = ClickChain.new(name, "_overlay")
        chain.hotkey = self._hotkey_edit.text().strip()
        for dot, delay in self._steps:
            chain.steps.append(ChainStep(dot_id=dot.id, delay_before=delay))
        store = get_click_store()
        existing = store.load_chains(self._target_key, "_overlay")
        # Upsert by id
        updated = [c for c in existing if c.id != chain.id]
        updated.append(chain)
        store.save_chains(self._target_key, "_overlay", updated)
        self.chain_saved.emit(chain)


# ---------------------------------------------------------------------------
# RegionMapperOverlay
# ---------------------------------------------------------------------------

class RegionMapperOverlay(QWidget):
    regions_updated = pyqtSignal(list)
    cancelled = pyqtSignal()

    _COLORS = [
        "#6366f1",
        "#22c55e",
        "#f59e0b",
        "#ef4444",
        "#06b6d4",
        "#a855f7",
    ]

    def __init__(
        self,
        target_key: str,
        existing_regions: Optional[list[Region]] = None,
        existing_dots: Optional[list[ClickDot]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._target_key = target_key
        self._regions: list[Region] = list(existing_regions) if existing_regions else []
        self._dots: list[ClickDot] = list(existing_dots) if existing_dots else []
        self._tool: str = "rect"   # "rect" | "dot"
        self._drawing = False
        self._start: QPoint = QPoint()
        self._current_rect: Optional[QRect] = None
        self._color_index = len(self._regions) % len(self._COLORS)

        # Window flags
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        geom = QGuiApplication.primaryScreen().geometry()
        self.setGeometry(geom)

        self._build_ui()

        self._script_panel = _DotScriptPanel(target_key, parent=self)
        self._script_panel.hide()
        self._script_panel.chain_saved.connect(self._on_chain_saved)

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Tool bar — floating container at top-center
        self._toolbar = QWidget(self)
        toolbar_layout = QHBoxLayout(self._toolbar)
        toolbar_layout.setContentsMargins(8, 6, 8, 6)
        toolbar_layout.setSpacing(6)
        self._toolbar.setStyleSheet(
            "background: rgba(0,0,0,200);"
            "border-radius: 6px;"
        )

        _btn_base = (
            "QPushButton { color: #aaa; background: transparent; border: none;"
            " border-radius: 4px; padding: 4px 14px; font-size: 13px; }"
            "QPushButton:hover { background: rgba(255,255,255,15); color: white; }"
        )
        _btn_active = (
            "QPushButton { color: white; background: #6366f1; border: none;"
            " border-radius: 4px; padding: 4px 14px; font-size: 13px; }"
        )

        self._btn_rect = QPushButton("▭  Region", self._toolbar)
        self._btn_rect.setStyleSheet(_btn_active)
        self._btn_rect.clicked.connect(lambda: self._set_tool("rect"))

        self._btn_dot = QPushButton("•  Dot", self._toolbar)
        self._btn_dot.setStyleSheet(_btn_base)
        self._btn_dot.clicked.connect(lambda: self._set_tool("dot"))

        toolbar_layout.addWidget(self._btn_rect)
        toolbar_layout.addWidget(self._btn_dot)
        self._toolbar.adjustSize()
        self._toolbar.move(
            (self.width() - self._toolbar.width()) // 2, 12
        )

        # Instruction banner (below toolbar)
        self._banner = QLabel(
            "Draw rectangles over content areas  •  Right-click to rename/delete  •  ESC when done",
            self,
        )
        self._banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._banner.setStyleSheet(
            "color: white;"
            "background: rgba(0,0,0,160);"
            "padding: 5px 14px;"
            "font-size: 12px;"
            "border-radius: 4px;"
        )
        self._banner.adjustSize()
        self._banner.move(
            (self.width() - self._banner.width()) // 2,
            self._toolbar.y() + self._toolbar.height() + 6,
        )

        # Done button
        self._done_btn = QPushButton("Done", self)
        self._done_btn.setFixedHeight(32)
        self._done_btn.setMinimumWidth(120)
        self._done_btn.setStyleSheet(
            "QPushButton {"
            "  background: #6366f1;"
            "  color: white;"
            "  border: none;"
            "  border-radius: 6px;"
            "  font-size: 14px;"
            "  padding: 0 24px;"
            "}"
            "QPushButton:hover {"
            "  background: #4f46e5;"
            "}"
        )
        self._done_btn.adjustSize()
        self._done_btn.move(
            (self.width() - self._done_btn.width()) // 2,
            self.height() - self._done_btn.height() - 20,
        )
        self._done_btn.clicked.connect(self._on_done)

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dark semi-transparent overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)

        # Saved regions
        for region in self._regions:
            base_color = QColor(region.color)
            border_pen = QPen(base_color, 2)
            painter.setPen(border_pen)

            fill_color = QColor(base_color)
            fill_color.setAlpha(30)
            painter.setBrush(QBrush(fill_color))

            rect = QRect(region.x, region.y, region.w, region.h)
            painter.drawRect(rect)

            # Label
            painter.setPen(QPen(QColor("white")))
            painter.drawText(region.x + 4, region.y + 14, region.name)

        # Current rubber-band rect
        if self._current_rect is not None and not self._current_rect.isNull():
            current_color = QColor(self._COLORS[self._color_index % len(self._COLORS)])
            dash_pen = QPen(current_color, 2, Qt.PenStyle.DashLine)
            painter.setPen(dash_pen)
            fill = QColor(current_color)
            fill.setAlpha(30)
            painter.setBrush(QBrush(fill))
            painter.drawRect(self._current_rect.normalized())

        # Click dots (rx/ry stored as absolute screen pixel coords)
        dot_font = QFont()
        dot_font.setPointSize(9)
        dot_font.setBold(True)
        painter.setFont(dot_font)
        for i, dot in enumerate(self._dots):
            color = QColor(self._COLORS[i % len(self._COLORS)])
            cx, cy = int(dot.rx), int(dot.ry)
            painter.setPen(QPen(QColor("white"), 1))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(QPoint(cx, cy), 8, 8)
            painter.setPen(QPen(QColor("white")))
            painter.drawText(cx + 12, cy + 4, dot.label)

        painter.end()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._tool == "dot":
                pos = event.pos()
                label = f"Dot {len(self._dots) + 1}"
                self._dots.append(ClickDot.new(label, float(pos.x()), float(pos.y())))
                self._script_panel.refresh_dots(self._dots)
                self.update()
            else:
                hit = self._region_at(event.pos())
                if hit is None:
                    self._drawing = True
                    self._start = event.pos()
                    self._current_rect = QRect(self._start, self._start)
                    self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            dot_hit = self._dot_at(event.pos())
            if dot_hit is not None:
                self._show_dot_context_menu(event.globalPosition().toPoint(), dot_hit)
            else:
                hit = self._region_at(event.pos())
                if hit is not None:
                    self._show_context_menu(event.globalPosition().toPoint(), hit)

    def mouseMoveEvent(self, event) -> None:
        if self._drawing:
            self._current_rect = QRect(self._start, event.pos()).normalized()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            self._drawing = False
            rect = QRect(self._start, event.pos()).normalized()
            if rect.width() > 10 and rect.height() > 10:
                self._prompt_name_and_add(rect)
            self._current_rect = None
            self.update()

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_done()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_tool(self, tool: str) -> None:
        self._tool = tool
        _active = (
            "QPushButton { color: white; background: #6366f1; border: none;"
            " border-radius: 4px; padding: 4px 14px; font-size: 13px; }"
        )
        _inactive = (
            "QPushButton { color: #aaa; background: transparent; border: none;"
            " border-radius: 4px; padding: 4px 14px; font-size: 13px; }"
            "QPushButton:hover { background: rgba(255,255,255,15); color: white; }"
        )
        self._btn_rect.setStyleSheet(_active if tool == "rect" else _inactive)
        self._btn_dot.setStyleSheet(_active if tool == "dot" else _inactive)
        hint = (
            "Draw rectangles over content areas  •  Right-click to rename/delete  •  ESC when done"
            if tool == "rect" else
            "Click anywhere to place a dot  •  Right-click dot to rename/delete  •  ESC when done"
        )
        self._banner.setText(hint)
        self._banner.adjustSize()
        self._banner.move(
            (self.width() - self._banner.width()) // 2,
            self._toolbar.y() + self._toolbar.height() + 6,
        )
        if tool == "dot":
            self._script_panel.show()
            self._position_script_panel()
            self._script_panel.refresh_dots(self._dots)
        else:
            self._script_panel.hide()

    def _position_script_panel(self) -> None:
        """Pin the script panel to the right side, vertically centered."""
        sw = self._script_panel.sizeHint().width() or 290
        sh = self._script_panel.sizeHint().height() or 480
        x = self.width() - sw - 20
        y = (self.height() - sh) // 2
        self._script_panel.setGeometry(x, y, sw, sh)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._script_panel.isVisible():
            self._position_script_panel()

    def _on_chain_saved(self, chain: ClickChain) -> None:
        pass  # chain is already saved by _DotScriptPanel; overlay just acknowledges

    def _dot_at(self, pos: QPoint) -> Optional[ClickDot]:
        for dot in self._dots:
            dx = pos.x() - int(dot.rx)
            dy = pos.y() - int(dot.ry)
            if dx * dx + dy * dy <= 12 * 12:
                return dot
        return None

    def _show_dot_context_menu(self, global_pos: QPoint, dot: ClickDot) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(global_pos)
        if chosen == rename_action:
            new_name, ok = QInputDialog.getText(self, "Rename dot", "Name:", text=dot.label)
            if ok and new_name.strip():
                dot.label = new_name.strip()
                self.update()
        elif chosen == delete_action:
            self._dots.remove(dot)
            self.update()

    def _region_at(self, pos: QPoint) -> Optional[Region]:
        for region in self._regions:
            rect = QRect(region.x, region.y, region.w, region.h)
            if rect.contains(pos):
                return region
        return None

    def _next_color(self) -> str:
        color = self._COLORS[self._color_index % len(self._COLORS)]
        self._color_index += 1
        return color

    def _prompt_name_and_add(self, rect: QRect) -> None:
        name, ok = QInputDialog.getText(
            self,
            "Name this region",
            "Region name:",
        )
        if ok and name.strip():
            color = self._next_color()
            region = Region(
                name=name.strip(),
                x=rect.x(),
                y=rect.y(),
                w=rect.width(),
                h=rect.height(),
                color=color,
            )
            self._regions.append(region)
            self.update()

    def _show_context_menu(self, global_pos: QPoint, region: Region) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(global_pos)
        if chosen == rename_action:
            new_name, ok = QInputDialog.getText(
                self,
                "Rename region",
                "New name:",
                text=region.name,
            )
            if ok and new_name.strip():
                region.name = new_name.strip()
                self.update()
        elif chosen == delete_action:
            self._regions.remove(region)
            self.update()

    def _on_done(self) -> None:
        store = RegionStore()
        store.save(self._target_key, self._regions)
        # Save dots keyed to target; use "_overlay" as region_name since dots are screen-absolute
        get_click_store().save_dots(self._target_key, "_overlay", list(self._dots))
        self.regions_updated.emit(list(self._regions))
        self.close()
