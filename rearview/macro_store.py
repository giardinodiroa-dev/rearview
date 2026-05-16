from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

project_root = Path(__file__).parent.parent
_MACROS_DIR = project_root / "macros"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MacroStep:
    t: float
    label: str = ""
    raw: dict = field(default_factory=dict)
    # raw keys: type ("click"|"key"|"scroll"|"move"), x, y, button, key,
    #           text, delta_x, delta_y
    semantic: Optional[dict] = None
    # semantic keys: type, selector, text, role, url, input_value,
    #                focused_element
    # None when no browser was attached during recording


@dataclass
class Macro:
    name: str
    target_type: str    # "browser_tab" | "x11_app" | "headless"
    target_hint: str    # URL fragment or app name for display/matching
    created_at: str     # ISO 8601 timestamp
    steps: list[MacroStep] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _macro_path(name: str) -> Path:
    return _MACROS_DIR / f"{name}.json"


def _dict_to_macro(data: dict) -> Macro:
    steps = [
        MacroStep(
            t=s["t"],
            label=s.get("label", ""),
            raw=s.get("raw", {}),
            semantic=s.get("semantic"),
        )
        for s in data.get("steps", [])
    ]
    return Macro(
        name=data["name"],
        target_type=data["target_type"],
        target_hint=data["target_hint"],
        created_at=data["created_at"],
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_macros_dir() -> Path:
    """Return the macros/ directory path, creating it if it does not exist."""
    _MACROS_DIR.mkdir(parents=True, exist_ok=True)
    return _MACROS_DIR


def save_macro(macro: Macro) -> Path:
    """Serialize *macro* to JSON and write to macros/{macro.name}.json.

    Returns the path that was written.
    """
    dest = get_macros_dir() / f"{macro.name}.json"
    dest.write_text(json.dumps(asdict(macro), indent=2, ensure_ascii=False))
    return dest


def load_macro(name: str) -> Macro:
    """Load and deserialize macros/{name}.json.

    Raises FileNotFoundError if the file does not exist.
    """
    path = _macro_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Macro not found: {name!r} (expected {path})")
    data = json.loads(path.read_text())
    return _dict_to_macro(data)


def list_macros() -> list[Macro]:
    """Return all macros in macros/, sorted by created_at descending.

    Returns an empty list when the directory is empty or does not exist.
    """
    macros_dir = get_macros_dir()
    macros: list[Macro] = []
    for path in macros_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text())
            macros.append(_dict_to_macro(data))
        except (json.JSONDecodeError, KeyError):
            # Skip corrupted files rather than crashing the listing
            continue
    macros.sort(
        key=lambda m: m.created_at,
        reverse=True,
    )
    return macros


def delete_macro(name: str) -> bool:
    """Delete macros/{name}.json.

    Returns True if the file was deleted, False if it did not exist.
    """
    path = _macro_path(name)
    if not path.exists():
        return False
    path.unlink()
    return True


def macro_exists(name: str) -> bool:
    """Return True if macros/{name}.json exists."""
    return _macro_path(name).exists()
