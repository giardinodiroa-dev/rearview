"""
region_mapper.py — PyQt6 fullscreen transparent overlay for defining named capture regions.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QRect, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QInputDialog, QMenu,
    QHBoxLayout, QVBoxLayout, QLineEdit,
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
# _ScriptNameBar
# ---------------------------------------------------------------------------

class _ScriptNameBar(QWidget):
    save_requested = pyqtSignal(str)   # emits the script name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setStyleSheet(
            "background: rgba(10,10,10,220); border-radius: 6px;"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        lbl = QLabel("Script name:")
        lbl.setStyleSheet("color: #888; font-size: 12px;")

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Untitled script")
        self._name_edit.setFixedWidth(180)
        self._name_edit.setStyleSheet(
            "QLineEdit { background: #1a1a1a; color: #e5e7eb; border: 1px solid #3a3a3a;"
            " border-radius: 4px; padding: 0 8px; height: 26px; font-size: 12px; }"
        )

        self._save_btn = QPushButton("Save")
        self._save_btn.setFixedSize(60, 26)
        self._save_btn.setStyleSheet(
            "QPushButton { background: #6366f1; color: white; border: none;"
            " border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background: #4f46e5; }"
        )
        self._save_btn.clicked.connect(self._on_save)

        self._feedback = QLabel("")
        self._feedback.setStyleSheet("color: #22c55e; font-size: 12px;")
        self._feedback.hide()

        layout.addWidget(lbl)
        layout.addWidget(self._name_edit)
        layout.addWidget(self._save_btn)
        layout.addWidget(self._feedback)
        layout.addStretch()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._feedback.hide)

    def _on_save(self):
        name = self._name_edit.text().strip() or "Untitled script"
        self.save_requested.emit(name)

    def show_feedback(self, msg: str = "Saved!") -> None:
        self._feedback.setText(msg)
        self._feedback.show()
        self._hide_timer.start(2000)

    def name(self) -> str:
        return self._name_edit.text().strip() or "Untitled script"


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

        # Connection editor state
        self._connections: list[tuple[str, str, float]] = []  # (from_dot_id, to_dot_id, delay_s)
        self._connecting_from: Optional[ClickDot] = None       # dot being dragged from
        self._connect_to_pos: QPoint = QPoint()                # current drag end position

        self._build_ui()

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

        self._script_name_bar = _ScriptNameBar(self)
        self._script_name_bar.hide()
        self._script_name_bar.save_requested.connect(self._on_save_script)

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

        # Connections — draw arrows between dots
        dot_map = {d.id: d for d in self._dots}
        conn_font = QFont()
        conn_font.setPointSize(9)
        painter.setFont(conn_font)

        for i, (from_id, to_id, delay) in enumerate(self._connections):
            fd = dot_map.get(from_id)
            td = dot_map.get(to_id)
            if fd is None or td is None:
                continue
            fx, fy = int(fd.rx), int(fd.ry)
            tx, ty = int(td.rx), int(td.ry)
            # Draw line
            painter.setPen(QPen(QColor("#6366f1"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(fx, fy, tx, ty)
            # Arrowhead at target end
            angle = math.atan2(ty - fy, tx - fx)
            arrow_len = 10
            arrow_angle = 0.4
            ax1 = tx - arrow_len * math.cos(angle - arrow_angle)
            ay1 = ty - arrow_len * math.sin(angle - arrow_angle)
            ax2 = tx - arrow_len * math.cos(angle + arrow_angle)
            ay2 = ty - arrow_len * math.sin(angle + arrow_angle)
            painter.drawLine(tx, ty, int(ax1), int(ay1))
            painter.drawLine(tx, ty, int(ax2), int(ay2))
            # Delay badge at midpoint
            mx, my = (fx + tx) // 2, (fy + ty) // 2
            badge_text = f"{delay:.1f}s"
            badge_rect = QRect(mx - 20, my - 10, 40, 20)
            badge_bg = QColor(30, 30, 50, 200)
            painter.setBrush(QBrush(badge_bg))
            painter.setPen(QPen(QColor("#6366f1"), 1))
            painter.drawRoundedRect(badge_rect, 4, 4)
            painter.setPen(QPen(QColor("#e5e7eb")))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)
            painter.setBrush(Qt.BrushStyle.NoBrush)

        # In-progress connection rubber-band
        if self._connecting_from is not None:
            fx = int(self._connecting_from.rx)
            fy = int(self._connecting_from.ry)
            painter.setPen(QPen(QColor("#a5b4fc"), 2, Qt.PenStyle.DashLine))
            painter.drawLine(fx, fy, self._connect_to_pos.x(), self._connect_to_pos.y())

        painter.end()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._tool == "dot":
                pos = event.pos()
                # If clicking on an existing dot → start connecting
                hit_dot = self._dot_at(pos)
                if hit_dot is not None:
                    self._connecting_from = hit_dot
                    self._connect_to_pos = pos
                else:
                    # Place new dot
                    label = f"Dot {len(self._dots) + 1}"
                    new_dot = ClickDot.new(label, float(pos.x()), float(pos.y()))
                    self._dots.append(new_dot)
                    self.update()
            else:
                hit = self._region_at(event.pos())
                if hit is None:
                    self._drawing = True
                    self._start = event.pos()
                    self._current_rect = QRect(self._start, self._start)
                    self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            conn_idx = self._connection_at(event.pos())
            if conn_idx >= 0:
                self._show_connection_menu(event.globalPosition().toPoint(), conn_idx)
            else:
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
        elif self._connecting_from is not None:
            self._connect_to_pos = event.pos()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._drawing:
                self._drawing = False
                rect = QRect(self._start, event.pos()).normalized()
                if rect.width() > 10 and rect.height() > 10:
                    self._prompt_name_and_add(rect)
                self._current_rect = None
                self.update()
            elif self._connecting_from is not None:
                target_dot = self._dot_at(event.pos())
                if target_dot is not None and target_dot.id != self._connecting_from.id:
                    self._connections.append((self._connecting_from.id, target_dot.id, 0.5))
                self._connecting_from = None
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
            self._script_name_bar.show()
            self._position_name_bar()
        else:
            self._script_name_bar.hide()

    def _position_name_bar(self) -> None:
        w = max(self._script_name_bar.sizeHint().width(), 420)
        self._script_name_bar.setFixedWidth(w)
        self._script_name_bar.adjustSize()
        x = (self.width() - w) // 2
        y = self._toolbar.y() + self._toolbar.height() + self._banner.height() + 10
        self._script_name_bar.move(x, y)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._script_name_bar.isVisible():
            self._position_name_bar()

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

    def _connection_at(self, pos: QPoint) -> int:
        """Return index of connection whose midpoint delay badge is near pos, or -1."""
        dot_map = {d.id: d for d in self._dots}
        for i, (from_id, to_id, _delay) in enumerate(self._connections):
            fd = dot_map.get(from_id)
            td = dot_map.get(to_id)
            if fd is None or td is None:
                continue
            mx = (int(fd.rx) + int(td.rx)) // 2
            my = (int(fd.ry) + int(td.ry)) // 2
            if abs(pos.x() - mx) < 24 and abs(pos.y() - my) < 14:
                return i
        return -1

    def _show_connection_menu(self, global_pos: QPoint, idx: int) -> None:
        menu = QMenu(self)
        edit_action = menu.addAction("Edit delay")
        delete_action = menu.addAction("Delete connection")
        chosen = menu.exec(global_pos)
        if chosen == edit_action:
            current_delay = self._connections[idx][2]
            new_delay, ok = QInputDialog.getDouble(
                self, "Edit delay", "Seconds to wait before this click:",
                value=current_delay, min=0.0, max=60.0, decimals=1,
            )
            if ok:
                from_id, to_id, _ = self._connections[idx]
                self._connections[idx] = (from_id, to_id, new_delay)
                self.update()
        elif chosen == delete_action:
            self._connections.pop(idx)
            self.update()

    def _on_save_script(self, name: str) -> None:
        if not self._dots or not self._connections:
            return
        # Build ordered steps from connections (chain: follow from first dot)
        dot_map = {d.id: d for d in self._dots}
        # Find start: dot that is never a "to" in any connection
        to_ids = {c[1] for c in self._connections}
        starts = [d for d in self._dots if d.id not in to_ids]
        start_id = starts[0].id if starts else self._connections[0][0]
        # Walk the chain
        conn_map: dict[str, tuple[str, float]] = {c[0]: (c[1], c[2]) for c in self._connections}
        steps = []
        visited: set[str] = set()
        cur = start_id
        first = True
        while cur and cur not in visited:
            visited.add(cur)
            nxt = conn_map.get(cur)
            delay = 0.0 if first else (
                next((c[2] for c in self._connections if c[1] == cur), 0.0)
            )
            steps.append(ChainStep(dot_id=cur, delay_before=delay))
            first = False
            cur = nxt[0] if nxt else None

        chain = ClickChain.new(name, "_overlay")
        chain.steps = steps

        store = get_click_store()
        existing = store.load_chains(self._target_key, "_overlay")
        # Upsert by name
        existing = [c for c in existing if c.name != name]
        existing.append(chain)
        store.save_chains(self._target_key, "_overlay", existing)
        store.save_dots(self._target_key, "_overlay", list(self._dots))

        self._script_name_bar.show_feedback("Saved!")

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
