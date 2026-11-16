"""Configuration loading with explicit defaults and basic checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "run_name": "baseline",
    "reporting_entity": "Synthetic Reporting Entity (fictional)",
    "reporting_currency": "GBP",
    "paths": {"raw_dir": "data/raw", "results_dir": "results/baseline"},
    "generation": {
        "seed": 42,
        "start_date": "2026-01-05",
        "n_business_days": 60,
        "n_counterparties": 40,
        "target_n_trades": 400,
        "currencies": ["GBP", "USD", "EUR"],
        "fx_start": {"GBP": 1.0, "USD": 0.79, "EUR": 0.855},
        "fx_daily_vol": 0.003,
    },
    "limits": {"warning_threshold_pct": 80.0, "at_limit_tolerance_gbp": 0.01},
    "reconciliation": {"abs_tolerance": 0.01, "rel_tolerance": 1e-9},
    "concentration": {"top_n": 10},
}


class ConfigError(ValueError):
    """Raised for unusable configuration files."""


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a JSON object")
    cfg = _merge(DEFAULTS, raw)
    validate_config(cfg)
    return cfg


def validate_config(cfg: dict[str, Any]) -> None:
    if cfg["reporting_currency"] != "GBP":
        raise ConfigError("Only GBP reporting currency is supported")
    w = float(cfg["limits"]["warning_threshold_pct"])
    if not 0 < w < 100:
        raise ConfigError("warning_threshold_pct must be in (0, 100)")
    if float(cfg["limits"]["at_limit_tolerance_gbp"]) < 0:
        raise ConfigError("at_limit_tolerance_gbp must be non-negative")
    rec = cfg["reconciliation"]
    if float(rec["abs_tolerance"]) < 0 or float(rec["rel_tolerance"]) < 0:
        raise ConfigError("Reconciliation tolerances must be non-negative")
    if int(cfg["concentration"]["top_n"]) < 1:
        raise ConfigError("top_n must be >= 1")


def resolve(base_dir: str | Path, relative: str | Path) -> Path:
    """Resolve a config path against ``base_dir`` (the project root)."""
    p = Path(relative)
    return p if p.is_absolute() else Path(base_dir) / p
