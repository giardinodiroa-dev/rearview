from __future__ import annotations

import uuid

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QInputDialog, QMenu, QWidget

from rearview.click_store import ClickDot

DOT_RADIUS = 7
HIT_RADIUS = 10

_PALETTE = [
    "#6366f1",
    "#22c55e",
    "#f59e0b",
    "#ef4444",
    "#06b6d4",
    "#a855f7",
]


class DotOverlayWidget(QWidget):
    dot_added = pyqtSignal(ClickDot)
    dot_moved = pyqtSignal(ClickDot)
    dot_renamed = pyqtSignal(ClickDot)
    dot_removed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._dots: list[ClickDot] = []
        self._selected_id: str | None = None
        self._drag_id: str | None = None
        self._drag_offset = QPoint(0, 0)
        self.setMinimumSize(320, 200)

    def set_dots(self, dots: list[ClickDot]) -> None:
        self._dots = list(dots)
        self.update()

    def set_pixmap(self, px: QPixmap) -> None:
        self._pixmap = px
        self.update()

    def clear_pixmap(self) -> None:
        self._pixmap = None
        self.update()

    def dots(self) -> list[ClickDot]:
        return list(self._dots)

    def _dot_canvas_pos(self, dot: ClickDot) -> QPoint:
        return QPoint(int(dot.rx * self.width()), int(dot.ry * self.height()))

    def _canvas_to_rel(self, pos: QPoint) -> tuple[float, float]:
        rx = max(0.0, min(1.0, pos.x() / self.width()))
        ry = max(0.0, min(1.0, pos.y() / self.height()))
        return rx, ry

    def _dot_at(self, pos: QPoint) -> ClickDot | None:
        for dot in self._dots:
            cp = self._dot_canvas_pos(dot)
            dx = pos.x() - cp.x()
            dy = pos.y() - cp.y()
            if dx * dx + dy * dy <= HIT_RADIUS * HIT_RADIUS:
                return dot
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            hit = self._dot_at(pos)
            if hit:
                self._selected_id = hit.id
                self._drag_id = hit.id
                cp = self._dot_canvas_pos(hit)
                self._drag_offset = pos - cp
            else:
                self._selected_id = None
                self._drag_id = None
                rx, ry = self._canvas_to_rel(pos)
                label = f"Dot {len(self._dots) + 1}"
                dot = ClickDot(id=str(uuid.uuid4())[:8], label=label, rx=rx, ry=ry)
                self._dots.append(dot)
                self._selected_id = dot.id
                self.update()
                self.dot_added.emit(dot)

        elif event.button() == Qt.MouseButton.RightButton:
            pos = event.position().toPoint()
            hit = self._dot_at(pos)
            if hit:
                self._show_context_menu(hit, event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self._drag_id is None:
            return
        pos = event.position().toPoint() - self._drag_offset
        for dot in self._dots:
            if dot.id == self._drag_id:
                dot.rx, dot.ry = self._canvas_to_rel(pos)
                break
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_id is not None:
            for dot in self._dots:
                if dot.id == self._drag_id:
                    self.dot_moved.emit(dot)
                    break
            self._drag_id = None

    def _show_context_menu(self, dot: ClickDot, global_pos) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        delete_action = menu.addAction("Delete")
        chosen = menu.exec(global_pos)
        if chosen == rename_action:
            text, ok = QInputDialog.getText(
                self, "Rename Dot", "New label:", text=dot.label
            )
            if ok and text.strip():
                dot.label = text.strip()
                self.update()
                self.dot_renamed.emit(dot)
        elif chosen == delete_action:
            dot_id = dot.id
            self._dots = [d for d in self._dots if d.id != dot_id]
            if self._selected_id == dot_id:
                self._selected_id = None
            self.update()
            self.dot_removed.emit(dot_id)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._pixmap:
            scaled = self._pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(0, 0, scaled)
        else:
            painter.fillRect(self.rect(), QColor("#1a1a1a"))

        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)

        for idx, dot in enumerate(self._dots):
            color = QColor(_PALETTE[idx % len(_PALETTE)])
            cp = self._dot_canvas_pos(dot)

            if dot.id == self._selected_id:
                ring_pen = QPen(QColor("white"), 2)
                painter.setPen(ring_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                r = DOT_RADIUS + 3
                painter.drawEllipse(cp, r, r)

            painter.setBrush(color)
            border_pen = QPen(QColor("white"), 1)
            painter.setPen(border_pen)
            painter.drawEllipse(cp, DOT_RADIUS, DOT_RADIUS)

            painter.setPen(QColor("white"))
            label_rect = QRect(
                cp.x() - 40,
                cp.y() - DOT_RADIUS - 16,
                80,
                14,
            )
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, dot.label)

        painter.end()
