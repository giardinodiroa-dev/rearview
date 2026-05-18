from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

_STORE_PATH = Path(__file__).parent.parent / "config" / "click_dots.json"


@dataclass
class ClickDot:
    id: str
    label: str
    rx: float   # 0.0–1.0 relative to region width
    ry: float   # 0.0–1.0 relative to region height

    @staticmethod
    def new(label: str, rx: float, ry: float) -> "ClickDot":
        return ClickDot(id=str(uuid.uuid4())[:8], label=label, rx=rx, ry=ry)


@dataclass
class ChainStep:
    dot_id: str
    delay_before: float = 0.0   # seconds to wait before this click


@dataclass
class ClickChain:
    id: str
    name: str
    hotkey: str                          # pynput format e.g. "<ctrl><alt>1"
    region_name: str                     # which region's dots to use
    steps: list[ChainStep] = field(default_factory=list)

    @staticmethod
    def new(name: str, region_name: str) -> "ClickChain":
        return ClickChain(id=str(uuid.uuid4())[:8], name=name, hotkey="", region_name=region_name)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_raw() -> dict:
    if _STORE_PATH.exists():
        try:
            return json.loads(_STORE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_raw(data: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(json.dumps(data, indent=2))


def _entry_key(target_key: str, region_name: str) -> str:
    return f"{target_key}::{region_name}"


class ClickStore:
    """Load and save ClickDots and ClickChains per (target_key, region_name)."""

    def load_dots(self, target_key: str, region_name: str) -> list[ClickDot]:
        raw = _load_raw()
        key = _entry_key(target_key, region_name)
        entry = raw.get(key, {})
        return [ClickDot(**d) for d in entry.get("dots", [])]

    def load_chains(self, target_key: str, region_name: str) -> list[ClickChain]:
        raw = _load_raw()
        key = _entry_key(target_key, region_name)
        entry = raw.get(key, {})
        chains = []
        for c in entry.get("chains", []):
            steps = [ChainStep(**s) for s in c.get("steps", [])]
            chains.append(ClickChain(
                id=c["id"], name=c["name"],
                hotkey=c.get("hotkey", ""),
                region_name=c.get("region_name", region_name),
                steps=steps,
            ))
        return chains

    def save_dots(self, target_key: str, region_name: str, dots: list[ClickDot]) -> None:
        raw = _load_raw()
        key = _entry_key(target_key, region_name)
        entry = raw.setdefault(key, {})
        entry["dots"] = [asdict(d) for d in dots]
        _save_raw(raw)

    def save_chains(self, target_key: str, region_name: str, chains: list[ClickChain]) -> None:
        raw = _load_raw()
        key = _entry_key(target_key, region_name)
        entry = raw.setdefault(key, {})
        entry["chains"] = [asdict(c) for c in chains]
        _save_raw(raw)

    def all_chains(self) -> list[ClickChain]:
        """Return every chain across all targets/regions (for hotkey registration)."""
        raw = _load_raw()
        chains: list[ClickChain] = []
        for entry in raw.values():
            for c in entry.get("chains", []):
                steps = [ChainStep(**s) for s in c.get("steps", [])]
                chains.append(ClickChain(
                    id=c["id"], name=c["name"],
                    hotkey=c.get("hotkey", ""),
                    region_name=c.get("region_name", ""),
                    steps=steps,
                ))
        return chains

    def dots_for_chain(self, target_key: str, chain: ClickChain) -> dict[str, ClickDot]:
        """Return id→ClickDot mapping for the region this chain targets."""
        dots = self.load_dots(target_key, chain.region_name)
        return {d.id: d for d in dots}


_store = ClickStore()


def get_click_store() -> ClickStore:
    return _store
