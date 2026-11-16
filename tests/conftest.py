"""Shared test fixtures: a tiny hand-built SYNTHETIC data set with hand-computed answers.

Fixture contents (two dates, 2026-01-05 and 2026-01-06; FX GBP 1, USD 0.8, EUR 0.9):

CPA  NS-A1 (netting):    T1 +100 GBP, T2 -60 GBP; collateral C1 20 GBP, haircut 10%, eligible, RECEIVED
     NS-A2 (no netting): T3 +50 USD, T4 -30 USD;  collateral C2 10 GBP eligible RECEIVED (unsupported)
     limit 100 GBP; rating A
CPB  NS-B1 (netting):    T5 +200 EUR maturing 2026-01-06, T6 +10 GBP; collateral C3 1000 GBP INELIGIBLE
     limit 200 EUR; rating BBB until 2026-01-06 (exclusive) then BB
CPC  NS-C1 (netting):    T7 -500 GBP; limit 0 GBP; no rating

Hand-computed results (independent of the implementation):
2026-01-05  NS-A1 gross 100, pre 40, collateral 18, net 22
            NS-A2 gross 40 (50 x 0.8), pre 40, net 40      -> CPA net 62, util 62% Within Limit
            NS-B1 gross 190 (180 + 10), pre 190, net 190   -> CPB limit 180 GBP, util 105.56% Breach
            CPC net 0, limit 0                             -> Zero Limit - No Exposure, Unrated
            total net 252
2026-01-06  T5 excluded (maturity <= as-of)                 -> CPB net 10, Within Limit; total net 72
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from counterparty_mi.config import DEFAULTS  # noqa: E402
from counterparty_mi.synthetic import build_control_totals, write_raw_tables  # noqa: E402

D1, D2 = "2026-01-05", "2026-01-06"


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).astype(str)


def make_raw() -> dict[str, pd.DataFrame]:
    raw: dict[str, pd.DataFrame] = {}
    raw["counterparties"] = _df([
        {"counterparty_id": "CPA", "counterparty_name": "Synthetic Test A", "parent_group": "Synthetic Group 1", "country": "GB", "sector": "Banks"},
        {"counterparty_id": "CPB", "counterparty_name": "Synthetic Test B", "parent_group": "Synthetic Group 1", "country": "US", "sector": "Insurance"},
        {"counterparty_id": "CPC", "counterparty_name": "Synthetic Test C", "parent_group": "Synthetic Group 2", "country": "DE", "sector": "Banks"},
    ])
    raw["netting_sets"] = _df([
        {"netting_set_id": "NS-A1", "counterparty_id": "CPA", "netting_permitted": "TRUE"},
        {"netting_set_id": "NS-A2", "counterparty_id": "CPA", "netting_permitted": "FALSE"},
        {"netting_set_id": "NS-B1", "counterparty_id": "CPB", "netting_permitted": "TRUE"},
        {"netting_set_id": "NS-C1", "counterparty_id": "CPC", "netting_permitted": "TRUE"},
    ])
    trades = []
    spec = [("T1", "CPA", "NS-A1", "IRS", "GBP", "100.00", "2030-01-01"),
            ("T2", "CPA", "NS-A1", "IRS", "GBP", "-60.00", "2030-01-01"),
            ("T3", "CPA", "NS-A2", "CCS", "USD", "50.00", "2030-01-01"),
            ("T4", "CPA", "NS-A2", "CCS", "USD", "-30.00", "2030-01-01"),
            ("T5", "CPB", "NS-B1", "FX_FORWARD", "EUR", "200.00", D2),
            ("T6", "CPB", "NS-B1", "IRS", "GBP", "10.00", "2030-01-01"),
            ("T7", "CPC", "NS-C1", "IRS", "GBP", "-500.00", "2030-01-01")]
    for d in (D1, D2):
        for tid, cp, ns, prod, ccy, mtm, mat in spec:
            trades.append({"as_of_date": d, "trade_id": tid, "counterparty_id": cp, "netting_set_id": ns,
                           "product_type": prod, "currency": ccy, "notional": "1000.00", "mtm": mtm,
                           "maturity_date": mat, "trade_status": "ACTIVE"})
    raw["trades"] = _df(trades)
    coll = []
    for d in (D1, D2):
        coll += [
            {"as_of_date": d, "collateral_id": "C1", "counterparty_id": "CPA", "netting_set_id": "NS-A1", "currency": "GBP",
             "market_value": "20.00", "haircut": "0.1000", "eligible": "TRUE", "direction": "RECEIVED"},
            {"as_of_date": d, "collateral_id": "C2", "counterparty_id": "CPA", "netting_set_id": "NS-A2", "currency": "GBP",
             "market_value": "10.00", "haircut": "0.0000", "eligible": "TRUE", "direction": "RECEIVED"},
            {"as_of_date": d, "collateral_id": "C3", "counterparty_id": "CPB", "netting_set_id": "NS-B1", "currency": "GBP",
             "market_value": "1000.00", "haircut": "0.0000", "eligible": "FALSE", "direction": "RECEIVED"},
        ]
    raw["collateral"] = _df(coll)
    raw["ratings"] = _df([
        {"counterparty_id": "CPA", "effective_from": "2025-01-01", "effective_to": "", "rating": "A"},
        {"counterparty_id": "CPB", "effective_from": "2025-01-01", "effective_to": D2, "rating": "BBB"},
        {"counterparty_id": "CPB", "effective_from": D2, "effective_to": "", "rating": "BB"},
    ])
    raw["limits"] = _df([
        {"limit_id": "L1", "counterparty_id": "CPA", "effective_from": "2025-01-01", "effective_to": "", "limit_currency": "GBP", "limit_amount": "100.00"},
        {"limit_id": "L2", "counterparty_id": "CPB", "effective_from": "2025-01-01", "effective_to": "", "limit_currency": "EUR", "limit_amount": "200.00"},
        {"limit_id": "L3", "counterparty_id": "CPC", "effective_from": "2025-01-01", "effective_to": "", "limit_currency": "GBP", "limit_amount": "0.00"},
    ])
    raw["fx_rates"] = _df([{"as_of_date": d, "currency": c, "gbp_per_unit": r}
                           for d in (D1, D2) for c, r in (("GBP", "1.000000"), ("USD", "0.800000"), ("EUR", "0.900000"))])
    raw["control_totals"] = build_control_totals(raw)
    return raw


def with_source_rows(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Mimic ingestion: add 1-based file line numbers."""
    out = {}
    for k, v in raw.items():
        df = v.copy()
        df.insert(0, "source_row", range(2, len(df) + 2))
        out[k] = df
    return out


def make_config(tmp_path: Path) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    cfg["run_name"] = "test"
    cfg["paths"] = {"raw_dir": str(tmp_path / "raw"), "results_dir": str(tmp_path / "results")}
    return cfg


def write_case(tmp_path: Path, raw: dict[str, pd.DataFrame], recompute_controls: bool = True) -> dict:
    """Write a raw case to tmp_path/raw and return its config. Controls are rebuilt from the
    (possibly modified) raw tables unless ``recompute_controls`` is False (tampering tests)."""
    raw = {k: v.copy() for k, v in raw.items()}
    if recompute_controls:
        raw["control_totals"] = build_control_totals(raw)
    write_raw_tables(raw, tmp_path / "raw")
    cfg = make_config(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


@pytest.fixture
def raw() -> dict[str, pd.DataFrame]:
    return make_raw()


@pytest.fixture
def root() -> Path:
    return ROOT
