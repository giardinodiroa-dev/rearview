from __future__ import annotations

import asyncio

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from rearview.click_executor import ClickExecutor
from rearview.click_store import ClickChain, ClickDot, get_click_store
from rearview.region_mapper import Region, RegionStore
from rearview.target_selector import get_session
from rearview.ui.chain_editor import ChainEditor
from rearview.ui.dot_overlay import DotOverlayWidget

_BG = "#141414"
_PANEL = "#1a1a1a"
_ACCENT = "#6366f1"
_TEXT = "#e5e7eb"
_BORDER = "#2a2a2a"
_DIM = "#6b7280"

_COMBO_STYLE = f"""
    QComboBox {{
        background: {_PANEL};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 0 8px;
        height: 28px;
        font-size: 13px;
    }}
    QComboBox:focus {{
        border-color: {_ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background: #242424;
        color: {_TEXT};
        selection-background-color: #2e2e5e;
    }}
"""

_BTN_STYLE = f"""
    QPushButton {{
        background: {_PANEL};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 0 10px;
        height: 28px;
        font-size: 13px;
    }}
    QPushButton:hover {{
        background: #2a2a2a;
    }}
"""


def _target_key(target) -> str:
    if target.type == "browser_tab" and target.tab_url:
        return target.tab_url
    return target.display_name


class ClicksPanel(QWidget):
    chains_changed = pyqtSignal()

    def __init__(self, parent=None, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__(parent)
        self._loop = loop
        self._target_key: str | None = None
        self._dots: list[ClickDot] = []
        self._chains: list[ClickChain] = []
        self._regions: list[Region] = []
        self._store = get_click_store()
        self._region_store = RegionStore()
        self._executor: ClickExecutor | None = None

        self._build_ui()
        self.setStyleSheet(f"background: {_BG}; color: {_TEXT};")

        session = get_session()
        if session.current is not None:
            self._apply_target(session.current)
        session.on_change(self._on_target_change)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Top bar: region selector + refresh
        top = QHBoxLayout()
        top.setSpacing(8)
        top.setContentsMargins(0, 0, 0, 0)

        region_lbl = QLabel("Region:")
        region_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 13px;")
        top.addWidget(region_lbl)

        self._region_combo = QComboBox()
        self._region_combo.setStyleSheet(_COMBO_STYLE)
        self._region_combo.setFixedHeight(28)
        self._region_combo.currentIndexChanged.connect(self._on_region_changed)
        top.addWidget(self._region_combo)

        top.addStretch()

        refresh_btn = QPushButton("↺ Refresh")
        refresh_btn.setStyleSheet(_BTN_STYLE)
        refresh_btn.setFixedHeight(28)
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh)
        top.addWidget(refresh_btn)

        root.addLayout(top)

        # Main splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background: {_BORDER}; width: 1px; }}"
        )
        splitter.setHandleWidth(1)

        self._overlay = DotOverlayWidget()
        self._overlay.setMinimumSize(320, 200)
        self._overlay.setStyleSheet(f"background: {_PANEL}; border: 1px solid {_BORDER};")
        self._overlay.dot_added.connect(self._on_dot_added)
        self._overlay.dot_moved.connect(self._on_dot_moved)
        self._overlay.dot_renamed.connect(self._on_dot_renamed)
        self._overlay.dot_removed.connect(self._on_dot_removed)

        self._chain_editor = ChainEditor()
        self._chain_editor.chain_saved.connect(self._on_chain_saved)
        self._chain_editor.chain_deleted.connect(self._on_chain_deleted)
        self._chain_editor.chain_run.connect(self._on_chain_run)

        splitter.addWidget(self._overlay)
        splitter.addWidget(self._chain_editor)
        splitter.setSizes([1, 1])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        root.addWidget(splitter, stretch=1)

        # Status bar
        self._status = QLabel("No target selected")
        self._status.setStyleSheet(
            f"color: {_DIM}; font-size: 11px; padding: 2px 0;"
        )
        root.addWidget(self._status)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        if self._target_key is None:
            return
        self._regions = self._region_store.load(self._target_key)
        self._repopulate_combo()
        self._load_current_region()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _current_region(self) -> Region | None:
        idx = self._region_combo.currentIndex()
        if idx < 0 or idx >= len(self._regions):
            return None
        return self._regions[idx]

    def _repopulate_combo(self) -> None:
        self._region_combo.blockSignals(True)
        prev = self._region_combo.currentText()
        self._region_combo.clear()
        for r in self._regions:
            self._region_combo.addItem(r.name)
        if prev:
            idx = self._region_combo.findText(prev)
            if idx >= 0:
                self._region_combo.setCurrentIndex(idx)
        self._region_combo.blockSignals(False)

    def _load_current_region(self) -> None:
        region = self._current_region()
        if region is None:
            self._dots = []
            self._chains = []
            self._overlay.set_dots([])
            self._overlay.clear_pixmap()
            self._chain_editor.load([], [])
            self._update_status()
            if not self._regions:
                self._overlay.set_dots([])
            return

        self._dots = self._store.load_dots(self._target_key, region.name)
        self._chains = self._store.load_chains(self._target_key, region.name)
        self._overlay.set_dots(self._dots)
        self._chain_editor.load(self._chains, self._dots)
        self._update_status()

    def _update_status(self) -> None:
        region = self._current_region()
        if self._target_key is None:
            self._status.setText("No target selected")
        elif region is None:
            self._status.setText("Map regions first (use the overlay tool)")
        else:
            n_dots = len(self._dots)
            n_chains = len(self._chains)
            self._status.setText(
                f"Region: {region.name} — {n_dots} dot{'s' if n_dots != 1 else ''}, "
                f"{n_chains} chain{'s' if n_chains != 1 else ''}"
            )

    def _apply_target(self, target) -> None:
        self._target_key = _target_key(target)
        self._regions = self._region_store.load(self._target_key)
        self._repopulate_combo()
        self._load_current_region()

    # ------------------------------------------------------------------
    # Session / region signals
    # ------------------------------------------------------------------

    def _on_target_change(self, target) -> None:
        self._apply_target(target)

    def _on_region_changed(self, _index: int) -> None:
        self._load_current_region()

    # ------------------------------------------------------------------
    # Dot signals
    # ------------------------------------------------------------------

    def _on_dot_added(self, dot: ClickDot) -> None:
        self._dots.append(dot)
        region = self._current_region()
        if region:
            self._store.save_dots(self._target_key, region.name, self._dots)
        self._chain_editor.load(self._chains, self._dots)
        self._update_status()

    def _on_dot_moved(self, dot: ClickDot) -> None:
        for i, d in enumerate(self._dots):
            if d.id == dot.id:
                self._dots[i] = dot
                break
        region = self._current_region()
        if region:
            self._store.save_dots(self._target_key, region.name, self._dots)

    def _on_dot_renamed(self, dot: ClickDot) -> None:
        for i, d in enumerate(self._dots):
            if d.id == dot.id:
                self._dots[i] = dot
                break
        region = self._current_region()
        if region:
            self._store.save_dots(self._target_key, region.name, self._dots)
        self._chain_editor.load(self._chains, self._dots)

    def _on_dot_removed(self, dot_id: str) -> None:
        self._dots = [d for d in self._dots if d.id != dot_id]
        region = self._current_region()
        if region:
            self._store.save_dots(self._target_key, region.name, self._dots)
        self._chain_editor.load(self._chains, self._dots)
        self._update_status()

    # ------------------------------------------------------------------
    # Chain signals
    # ------------------------------------------------------------------

    def _on_chain_saved(self, chain: ClickChain) -> None:
        for i, c in enumerate(self._chains):
            if c.id == chain.id:
                self._chains[i] = chain
                break
        else:
            self._chains.append(chain)
        region = self._current_region()
        if region:
            self._store.save_chains(self._target_key, region.name, self._chains)
        self.chains_changed.emit()
        self._update_status()

    def _on_chain_deleted(self, chain_id: str) -> None:
        self._chains = [c for c in self._chains if c.id != chain_id]
        region = self._current_region()
        if region:
            self._store.save_chains(self._target_key, region.name, self._chains)
        self.chains_changed.emit()
        self._update_status()

    def _on_chain_run(self, chain_id: str) -> None:
        chain = next((c for c in self._chains if c.id == chain_id), None)
        if chain is None:
            return
        dot_map = {d.id: d for d in self._dots}

        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

        executor = ClickExecutor(loop)
        asyncio.run_coroutine_threadsafe(
            executor.run_chain(chain, dot_map), loop
        )
