"""Settings, from the add-on options file or environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Home Assistant add-ons get their options here and may persist data alongside.
OPTIONS_PATH = Path("/data/options.json")
DEFAULT_DATA_DIR = Path("/data")


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path = DEFAULT_DATA_DIR
    scan_interval_minutes: int = 60
    max_pages: int = 3
    min_delay: float = 6.0
    max_delay: float = 12.0
    # Item pages re-checked at most once per this many hours.
    recheck_hours: int = 24
    max_checks_per_cycle: int = 25
    min_margin: float = 8.0
    min_margin_pct: float = 25.0
    max_days_to_sell: float | None = 45.0
    notify_service: str = ""
    notify_min_margin: float = 15.0
    log_level: str = "INFO"
    port: int = 8099
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vinted.db"


def load_settings() -> Settings:
    """Read /data/options.json when running as an add-on, env vars otherwise."""
    options: dict = {}
    if OPTIONS_PATH.is_file():
        try:
            options = json.loads(OPTIONS_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            options = {}

    data_dir = Path(_env("DATA_DIR") or options.get("data_dir") or DEFAULT_DATA_DIR)

    def opt(name: str, default):
        value = options.get(name, _env(name.upper()))
        if value is None or value == "":
            return default
        if isinstance(default, bool):
            return str(value).lower() in ("1", "true", "yes", "on")
        if isinstance(default, int) and not isinstance(default, bool):
            return int(value)
        if isinstance(default, float):
            return float(value)
        return value

    max_days = opt("max_days_to_sell", 45.0)
    return Settings(
        data_dir=data_dir,
        scan_interval_minutes=opt("scan_interval_minutes", 60),
        max_pages=opt("max_pages", 3),
        min_delay=opt("min_delay", 6.0),
        max_delay=opt("max_delay", 12.0),
        recheck_hours=opt("recheck_hours", 24),
        max_checks_per_cycle=opt("max_checks_per_cycle", 25),
        min_margin=opt("min_margin", 8.0),
        min_margin_pct=opt("min_margin_pct", 25.0),
        max_days_to_sell=None if max_days in (0, "0") else float(max_days),
        notify_service=opt("notify_service", "") or "",
        notify_min_margin=opt("notify_min_margin", 15.0),
        log_level=str(opt("log_level", "INFO")).upper(),
        port=opt("port", 8099),
        extra=options,
    )
