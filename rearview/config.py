from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

ROOT = Path(__file__).parent.parent
CONFIG_DIR = ROOT / "config"


@dataclass
class BrowserConfig:
    executable: str = "chromium"
    debug_port: int = 9222
    connect_existing: bool = True


@dataclass
class WorkflowConfig:
    auto_advance: bool = False
    auto_hide_after: int = 0
    hotkey_next: str = "<super>s"


@dataclass
class ToastConfig:
    position: str = "bottom-right"
    opacity: float = 0.92
    width: int = 420
    theme: str = "dark"


@dataclass
class TeleprompterConfig:
    enabled: bool = True
    script: str = "scripts/default.md"
    stt_model: str = "small"
    agent_name: str = "Agent"
    company: str = "MyCompany"


@dataclass
class RearviewConfig:
    display: str = ":99"
    real_display: str = ":0"
    template: str = "aloware"


@dataclass
class Disposition:
    label: str
    hotkey: str


@dataclass
class AppConfig:
    rearview: RearviewConfig = field(default_factory=RearviewConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    toast: ToastConfig = field(default_factory=ToastConfig)
    teleprompter: TeleprompterConfig = field(default_factory=TeleprompterConfig)
    agent: dict = field(default_factory=lambda: {"name": "Agent"})
    dispositions: list[Disposition] = field(default_factory=list)


def _from_dict(cls, data: dict):
    fields = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in fields}
    return cls(**filtered)


def load_config() -> AppConfig:
    settings_path = CONFIG_DIR / "settings.yaml"
    dispositions_path = CONFIG_DIR / "dispositions.yaml"

    cfg = AppConfig()

    if settings_path.exists():
        with open(settings_path) as f:
            raw = yaml.safe_load(f) or {}

        if "rearview" in raw:
            cfg.rearview = _from_dict(RearviewConfig, raw["rearview"])
        if "browser" in raw:
            cfg.browser = _from_dict(BrowserConfig, raw["browser"])
        if "workflow" in raw:
            cfg.workflow = _from_dict(WorkflowConfig, raw["workflow"])
        if "toast" in raw:
            cfg.toast = _from_dict(ToastConfig, raw["toast"])
        if "teleprompter" in raw:
            cfg.teleprompter = _from_dict(TeleprompterConfig, raw["teleprompter"])
        if "agent" in raw:
            cfg.agent = raw["agent"]

    if dispositions_path.exists():
        with open(dispositions_path) as f:
            raw = yaml.safe_load(f) or {}
        cfg.dispositions = [
            Disposition(label=d["label"], hotkey=d["hotkey"])
            for d in raw.get("dispositions", [])
        ]

    return cfg


_cached: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _cached
    if _cached is None:
        _cached = load_config()
    return _cached


def reload_config() -> AppConfig:
    global _cached
    _cached = load_config()
    return _cached
