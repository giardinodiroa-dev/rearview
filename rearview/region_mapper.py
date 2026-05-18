"""
region_mapper.py — PyQt6 fullscreen transparent overlay for defining named capture regions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QGuiApplication
from PyQt6.QtWidgets import QWidget, QLabel, QPushButton, QInputDialog, QMenu, QHBoxLayout

from rearview.click_store import ClickDot, get_click_store


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
