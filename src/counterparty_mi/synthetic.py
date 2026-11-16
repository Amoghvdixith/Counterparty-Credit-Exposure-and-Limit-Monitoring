"""Deterministic synthetic source-data generator.

Everything produced here is fictional. Counterparty names are prefixed with
"Synthetic" and do not represent real institutions. MTM values are externally
"supplied" synthetic numbers (a seeded random walk or hand-built scenario paths);
no trade pricing is performed.

The business-date index is ``pandas.bdate_range`` (Monday-Friday). It is NOT an
exchange or settlement holiday calendar.

Source control totals are built by :func:`build_control_totals` from the raw
string tables *before* any downstream processing, mirroring a source system that
sends independent row-count and amount controls with its extract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .schemas import (
    ALL_CURRENCIES_LABEL,
    CONTROL_AMOUNT_FIELDS,
    CONTROL_CURRENCY_FIELDS,
    CONTROL_DATE_FIELDS,
    SCHEMAS,
    SOURCE_FILES,
    SOURCE_TABLES_WITH_CONTROLS,
)

GENERATOR_VERSION = "1.0.0"

_NAME_WORDS = [
    "Alder", "Birch", "Cedar", "Damson", "Elm", "Fir", "Gorse", "Hazel", "Ivy",
    "Juniper", "Kestrel", "Larch", "Maple", "Nettle", "Oakmoss", "Poplar", "Quince",
    "Rowan", "Sorrel", "Teasel", "Umber", "Vetch", "Willow", "Yarrow", "Aster",
    "Bracken", "Clover", "Dogwood", "Fennel", "Heather", "Iris", "Laurel", "Myrtle",
    "Osier", "Primrose", "Rush", "Sedge", "Thistle", "Violet", "Wren",
]
_SECTORS = {
    "Banks": "Bank",
    "Insurance": "Assurance",
    "Asset Managers": "Asset Management",
    "Hedge Funds": "Capital Partners",
    "Pension Funds": "Pension Scheme",
    "Corporates - Energy": "Energy",
    "Corporates - Industrials": "Industries",
    "Sovereign and Public Sector": "Public Authority",
}
_COUNTRIES = ["GB", "US", "DE", "FR", "NL", "CH", "JP", "CA", "IE"]
_N_PARENTS = 12
_BACKGROUND_RATINGS = (["AAA", "AA", "A", "BBB", "BB", "B"], [0.03, 0.12, 0.30, 0.35, 0.15, 0.05])

LIMIT_DEFAULT_FROM = "2025-07-01"
RATING_DEFAULT_FROM = "2025-01-01"


def business_dates(start: str, n: int) -> pd.DatetimeIndex:
    """Fixed Monday-Friday date index (not a holiday calendar)."""
    return pd.bdate_range(start=start, periods=n)


# ---------------------------------------------------------------------------
# Raw-table formatting helpers
# ---------------------------------------------------------------------------

def _fmt_date(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x):
        return ""
    return pd.Timestamp(x).strftime("%Y-%m-%d")


def _fmt_money(x: float) -> str:
    return f"{float(x):.2f}"


def _fmt_bool(x: bool) -> str:
    return "TRUE" if bool(x) else "FALSE"


# ---------------------------------------------------------------------------
# Source control totals (computed from raw strings only)
# ---------------------------------------------------------------------------

def summarise_raw_for_controls(raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Row counts and signed amount totals by (source_table, as_of_date, currency).

    Convention:
    - Static tables have a blank ``as_of_date``.
    - Tables without a currency split use currency ``ALL``.
    - Amount totals are the signed sum of the raw amount field in its own
      (local) currency over all raw rows in the group, including rows that the
      pipeline later excludes or quarantines. Non-numeric raw amounts cannot be
      summed and are ignored in the amount total (but counted in the row count).
    """
    rows: list[dict[str, Any]] = []
    for table in SOURCE_TABLES_WITH_CONTROLS:
        df = raw[table]
        date_col = CONTROL_DATE_FIELDS[table]
        ccy_col = CONTROL_CURRENCY_FIELDS[table]
        amt_col = CONTROL_AMOUNT_FIELDS[table]
        work = pd.DataFrame(index=df.index)
        work["as_of_date"] = (
            df[date_col].astype(str).str.strip() if date_col and date_col in df else ""
        )
        work["currency"] = (
            df[ccy_col].astype(str).str.strip() if ccy_col and ccy_col in df else ALL_CURRENCIES_LABEL
        )
        if amt_col and amt_col in df:
            work["amount"] = pd.to_numeric(df[amt_col], errors="coerce").astype(float)
        else:
            work["amount"] = np.nan
        if work.empty and date_col is None:
            rows.append(
                {
                    "source_table": table,
                    "as_of_date": "",
                    "currency": ALL_CURRENCIES_LABEL,
                    "row_count": 0,
                    "amount_total": 0.0 if amt_col else np.nan,
                    "amount_field": amt_col or "",
                }
            )
            continue
        grouped = work.groupby(["as_of_date", "currency"], sort=True, dropna=False)
        for (d, c), g in grouped:
            rows.append(
                {
                    "source_table": table,
                    "as_of_date": d,
                    "currency": c,
                    "row_count": int(len(g)),
                    "amount_total": float(g["amount"].sum(min_count=0)) if amt_col else np.nan,
                    "amount_field": amt_col or "",
                }
            )
    return pd.DataFrame(
        rows,
        columns=["source_table", "as_of_date", "currency", "row_count", "amount_total", "amount_field"],
    )


def build_control_totals(raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Source control file (strings) built from raw source tables."""
    s = summarise_raw_for_controls(raw)
    out = pd.DataFrame(
        {
            "as_of_date": s["as_of_date"].astype(str),
            "source_table": s["source_table"].astype(str),
            "currency": s["currency"].astype(str),
            "expected_row_count": s["row_count"].astype(int).astype(str),
            "expected_amount_total": [
                "" if (f == "" or pd.isna(a)) else f"{a:.2f}"
                for a, f in zip(s["amount_total"], s["amount_field"])
            ],
            "amount_field": s["amount_field"].astype(str),
        }
    )
    return out[list(SCHEMAS["control_totals"].columns)].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

@dataclass
class GeneratedData:
    tables: dict[str, pd.DataFrame]
    metadata: dict[str, Any]


@dataclass
class _State:
    dates: pd.DatetimeIndex
    rng: np.random.Generator
    counterparties: list[dict] = field(default_factory=list)
    netting_sets: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    collateral: list[dict] = field(default_factory=list)
    ratings: list[dict] = field(default_factory=list)
    limits: list[dict] = field(default_factory=list)
    next_trade: int = 1
    next_limit: int = 1

    def trade_id(self) -> str:
        tid = f"TRD-{self.next_trade:06d}"
        self.next_trade += 1
        return tid

    def limit_id(self) -> str:
        lid = f"LIM-{self.next_limit:04d}"
        self.next_limit += 1
        return lid


def _add_trade(
    st: _State,
    cp: str,
    ns: str,
    product: str,
    ccy: str,
    notional: float,
    mtm_path: np.ndarray,
    maturity_date: pd.Timestamp,
    *,
    start_idx: int = 0,
    maturity_idx: int | None = None,
    terminate_idx: int | None = None,
) -> str:
    """Append daily snapshots for one trade.

    - Up to and including the maturity date the feed reports ``ACTIVE`` (on the
      maturity date itself the pipeline excludes it via maturity_date > as_of_date).
    - For three business days after maturity the feed reports ``MATURED`` with MTM 0,
      after which the trade drops out of the snapshot.
    - Terminated trades report ``TERMINATED`` with MTM 0 for two days, then drop out.
    """
    tid = st.trade_id()
    for t in range(start_idx, len(st.dates)):
        if terminate_idx is not None and t >= terminate_idx:
            if t < terminate_idx + 2:
                status, mtm = "TERMINATED", 0.0
            else:
                break
        elif maturity_idx is not None and t > maturity_idx:
            if t <= maturity_idx + 3:
                status, mtm = "MATURED", 0.0
            else:
                break
        else:
            status, mtm = "ACTIVE", float(mtm_path[t])
        st.trades.append(
            {
                "as_of_date": st.dates[t],
                "trade_id": tid,
                "counterparty_id": cp,
                "netting_set_id": ns,
                "product_type": product,
                "currency": ccy,
                "notional": round(float(notional), 2),
                "mtm": round(mtm, 2),
                "maturity_date": pd.Timestamp(maturity_date),
                "trade_status": status,
            }
        )
    return tid


def _add_collateral(
    st: _State, coll_id: str, cp: str, ns: str, ccy: str, mv_path: np.ndarray,
    haircut: float, eligible: bool, direction: str,
) -> None:
    for t, d in enumerate(st.dates):
        st.collateral.append(
            {
                "as_of_date": d,
                "collateral_id": coll_id,
                "counterparty_id": cp,
                "netting_set_id": ns,
                "currency": ccy,
                "market_value": round(max(float(mv_path[t]), 0.0), 2),
                "haircut": float(haircut),
                "eligible": bool(eligible),
                "direction": direction,
            }
        )


def _fx_table(dates: pd.DatetimeIndex, cfg_gen: dict, rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    vol = float(cfg_gen["fx_daily_vol"])
    paths = {}
    for ccy in cfg_gen["currencies"]:
        start = float(cfg_gen["fx_start"][ccy])
        if ccy == "GBP":
            paths[ccy] = np.ones(len(dates))
        else:
            shocks = rng.normal(0.0, vol, len(dates))
            shocks[0] = 0.0
            paths[ccy] = start * np.exp(np.cumsum(shocks))
    for t, d in enumerate(dates):
        for ccy in cfg_gen["currencies"]:
            rows.append({"as_of_date": d, "currency": ccy, "gbp_per_unit": round(float(paths[ccy][t]), 6)})
    return pd.DataFrame(rows)


def _const(n: int, value: float) -> np.ndarray:
    return np.full(n, float(value))


# Scenario definitions: counterparty -> (scenario name, description)
SCENARIOS: list[dict[str, str]] = [
    {"counterparty_id": "CP001", "scenario": "within_limit",
     "description": "Netting set with +5.0m (gently oscillating) and -1.0m GBP trades against a 10m GBP limit; utilisation about 40%."},
    {"counterparty_id": "CP002", "scenario": "warning_utilisation",
     "description": "USD trades netting to about 11.3m USD (about 8.9m GBP) against a 10m GBP limit; utilisation between 80% and 100% on every date."},
    {"counterparty_id": "CP003", "scenario": "breach",
     "description": "EUR exposure ramps linearly from 8m to 16m EUR against a 14m EUR limit; status moves Within Limit -> Warning -> Breach."},
    {"counterparty_id": "CP004", "scenario": "positive_exposure_zero_limit",
     "description": "+2.0m GBP exposure with an approved limit of 0."},
    {"counterparty_id": "CP005", "scenario": "zero_exposure_zero_limit",
     "description": "Only negative MTM (payable) trades in a netting set; exposure 0 with a limit of 0."},
    {"counterparty_id": "CP006", "scenario": "missing_effective_limit",
     "description": "Only limit expires (effective_to exclusive) on 2026-02-16; no effective limit from that date."},
    {"counterparty_id": "CP007", "scenario": "missing_effective_rating",
     "description": "No rating record at all; reported as Unrated and flagged."},
    {"counterparty_id": "CP008", "scenario": "rating_downgrade",
     "description": "Rating A until 2026-02-09 (exclusive), BBB from 2026-02-09."},
    {"counterparty_id": "CP009", "scenario": "limit_change",
     "description": "Limit cut from 15m to 9m GBP effective 2026-02-23 against stable 8m GBP exposure; Within Limit -> Warning."},
    {"counterparty_id": "CP010", "scenario": "overcollateralisation",
     "description": "+3.0m GBP exposure with 5.0m GBP eligible cash collateral received; net exposure floored at 0, 2.0m surplus unused."},
    {"counterparty_id": "CP011", "scenario": "offset_netting_permitted",
     "description": "+6.0m and -4.0m GBP trades in a netting-permitted set; pre-collateral exposure 2.0m."},
    {"counterparty_id": "CP012", "scenario": "offset_netting_not_permitted",
     "description": "+6.0m and -4.0m GBP trades in a non-netting set; pre-collateral exposure 6.0m; 1.0m collateral on the set is unsupported and not recognised."},
    {"counterparty_id": "CP013", "scenario": "no_cross_set_collateral_transfer",
     "description": "Set 01: +2.0m exposure with 5.0m collateral (3.0m surplus). Set 02: +4.0m with no collateral. Counterparty exposure is 4.0m, not 1.0m."},
    {"counterparty_id": "CP014", "scenario": "maturing_trade",
     "description": "+4.0m GBP trade matures 2026-02-13 (excluded from that date; reported MATURED for three days, then dropped); +1.0m long-dated trade remains."},
    {"counterparty_id": "CP015", "scenario": "ineligible_collateral",
     "description": "2.0m EUR bond collateral marked ineligible; not recognised; exposure stays 3.0m GBP."},
    {"counterparty_id": "CP016", "scenario": "posted_collateral",
     "description": "Collateral posted by the reporting entity (direction POSTED) is flagged and not recognised as an offset."},
    {"counterparty_id": "CP017", "scenario": "at_limit",
     "description": "Constant +5,000,000.00 GBP exposure against a 5,000,000 GBP limit; At Limit within tolerance."},
]


def _scenario_counterparties(st: _State) -> None:
    n = len(st.dates)
    dates = st.dates
    far = pd.Timestamp("2031-06-30")
    idx = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(dates)}

    def ns(cp: str, k: int, netting: bool) -> str:
        nid = f"NS-{cp}-{k:02d}"
        st.netting_sets.append({"netting_set_id": nid, "counterparty_id": cp, "netting_permitted": netting})
        return nid

    def lim(cp: str, ccy: str, amount: float, start: str = LIMIT_DEFAULT_FROM, end: str | None = None) -> None:
        st.limits.append({"limit_id": st.limit_id(), "counterparty_id": cp, "effective_from": pd.Timestamp(start),
                          "effective_to": pd.Timestamp(end) if end else pd.NaT,
                          "limit_currency": ccy, "limit_amount": float(amount)})

    t = np.arange(n)
    # CP001 within limit
    s = ns("CP001", 1, True)
    _add_trade(st, "CP001", s, "IRS", "GBP", 60e6, 5e6 + 2e5 * np.sin(t / 6.0), far)
    _add_trade(st, "CP001", s, "IRS", "GBP", 25e6, _const(n, -1e6), far)
    lim("CP001", "GBP", 10e6)
    # CP002 warning (USD)
    s = ns("CP002", 1, True)
    _add_trade(st, "CP002", s, "CCS", "USD", 90e6, _const(n, 11.8e6), far)
    _add_trade(st, "CP002", s, "FX_FORWARD", "USD", 20e6, _const(n, -0.5e6), pd.Timestamp("2027-03-31"))
    lim("CP002", "GBP", 10e6)
    # CP003 breach ramp (EUR)
    s = ns("CP003", 1, True)
    _add_trade(st, "CP003", s, "CCS", "EUR", 120e6, np.linspace(9e6, 17e6, n), far)
    _add_trade(st, "CP003", s, "IRS", "EUR", 40e6, _const(n, -1e6), far)
    lim("CP003", "EUR", 14e6)
    # CP004 zero limit with exposure
    s = ns("CP004", 1, True)
    _add_trade(st, "CP004", s, "IRS", "GBP", 30e6, _const(n, 2e6), far)
    lim("CP004", "GBP", 0.0)
    # CP005 zero limit, zero exposure
    s = ns("CP005", 1, True)
    _add_trade(st, "CP005", s, "IRS", "GBP", 50e6, _const(n, -3e6), far)
    _add_trade(st, "CP005", s, "FX_SWAP", "GBP", 20e6, _const(n, -1.5e6), pd.Timestamp("2027-01-29"))
    lim("CP005", "GBP", 0.0)
    # CP006 limit expires
    s = ns("CP006", 1, True)
    _add_trade(st, "CP006", s, "IRS", "GBP", 40e6, _const(n, 3e6), far)
    lim("CP006", "GBP", 10e6, end="2026-02-16")
    # CP007 no rating
    s = ns("CP007", 1, True)
    _add_trade(st, "CP007", s, "EQUITY_SWAP", "GBP", 15e6, _const(n, 1.5e6), pd.Timestamp("2028-12-29"))
    lim("CP007", "GBP", 5e6)
    # CP008 downgrade
    s = ns("CP008", 1, True)
    _add_trade(st, "CP008", s, "CCS", "EUR", 35e6, _const(n, 2.5e6), far)
    lim("CP008", "GBP", 6e6)
    # CP009 limit change
    s = ns("CP009", 1, True)
    _add_trade(st, "CP009", s, "IRS", "GBP", 80e6, _const(n, 8e6), far)
    lim("CP009", "GBP", 15e6, end="2026-02-23")
    lim("CP009", "GBP", 9e6, start="2026-02-23")
    # CP010 overcollateralised
    s = ns("CP010", 1, True)
    _add_trade(st, "CP010", s, "IRS", "GBP", 30e6, _const(n, 3e6), far)
    _add_collateral(st, f"COL-{s}-01", "CP010", s, "GBP", _const(n, 5e6), 0.0, True, "RECEIVED")
    lim("CP010", "GBP", 5e6)
    # CP011 netting permitted offset
    s = ns("CP011", 1, True)
    _add_trade(st, "CP011", s, "IRS", "GBP", 60e6, _const(n, 6e6), far)
    _add_trade(st, "CP011", s, "IRS", "GBP", 45e6, _const(n, -4e6), far)
    lim("CP011", "GBP", 10e6)
    # CP012 non-netting offset + unsupported collateral
    s = ns("CP012", 1, False)
    _add_trade(st, "CP012", s, "IRS", "GBP", 60e6, _const(n, 6e6), far)
    _add_trade(st, "CP012", s, "IRS", "GBP", 45e6, _const(n, -4e6), far)
    _add_collateral(st, f"COL-{s}-01", "CP012", s, "GBP", _const(n, 1e6), 0.0, True, "RECEIVED")
    lim("CP012", "GBP", 10e6)
    # CP013 two sets, surplus collateral cannot transfer
    s1 = ns("CP013", 1, True)
    s2 = ns("CP013", 2, True)
    _add_trade(st, "CP013", s1, "IRS", "GBP", 25e6, _const(n, 2e6), far)
    _add_collateral(st, f"COL-{s1}-01", "CP013", s1, "GBP", _const(n, 5e6), 0.0, True, "RECEIVED")
    _add_trade(st, "CP013", s2, "IRS", "GBP", 45e6, _const(n, 4e6), far)
    lim("CP013", "GBP", 10e6)
    # CP014 maturing trade
    s = ns("CP014", 1, True)
    m = idx["2026-02-13"]
    _add_trade(st, "CP014", s, "FX_FORWARD", "GBP", 50e6, _const(n, 4e6), dates[m], maturity_idx=m)
    _add_trade(st, "CP014", s, "IRS", "GBP", 20e6, _const(n, 1e6), far)
    lim("CP014", "GBP", 8e6)
    # CP015 ineligible collateral
    s = ns("CP015", 1, True)
    _add_trade(st, "CP015", s, "IRS", "GBP", 35e6, _const(n, 3e6), far)
    _add_collateral(st, f"COL-{s}-01", "CP015", s, "EUR", _const(n, 2e6), 0.05, False, "RECEIVED")
    lim("CP015", "GBP", 10e6)
    # CP016 posted collateral
    s = ns("CP016", 1, True)
    _add_trade(st, "CP016", s, "IRS", "GBP", 40e6, _const(n, -2e6), far)
    _add_trade(st, "CP016", s, "FX_FORWARD", "GBP", 10e6, _const(n, 0.5e6), pd.Timestamp("2026-09-30"))
    _add_collateral(st, f"COL-{s}-01", "CP016", s, "GBP", _const(n, 2e6), 0.0, True, "POSTED")
    lim("CP016", "GBP", 5e6)
    # CP017 exactly at limit
    s = ns("CP017", 1, True)
    _add_trade(st, "CP017", s, "IRS", "GBP", 50e6, _const(n, 5_000_000.00), far)
    lim("CP017", "GBP", 5_000_000.00)


def _quick_counterparty_exposure(
    trades: pd.DataFrame, collateral: pd.DataFrame, netting_sets: pd.DataFrame, fx: pd.DataFrame
) -> pd.DataFrame:
    """Small independent exposure calculation used only to calibrate limits and
    verify scenarios during generation (the pipeline has its own implementation)."""
    tr = trades[(trades["trade_status"] == "ACTIVE") & (trades["maturity_date"] > trades["as_of_date"])]
    tr = tr.merge(fx, on=["as_of_date", "currency"], how="left", validate="many_to_one")
    tr = tr.assign(v=tr["mtm"] * tr["gbp_per_unit"])
    g = tr.groupby(["as_of_date", "netting_set_id"]).agg(
        gross=("v", lambda x: x.clip(lower=0).sum()), net=("v", "sum")
    ).reset_index()
    g = g.merge(netting_sets, on="netting_set_id", how="left", validate="many_to_one")
    g["pre"] = np.where(g["netting_permitted"], g["net"].clip(lower=0), g["gross"])
    if len(collateral):
        c = collateral.merge(netting_sets[["netting_set_id", "netting_permitted"]], on="netting_set_id")
        c = c[(c["direction"] == "RECEIVED") & c["eligible"] & c["netting_permitted"]]
        c = c.merge(fx, on=["as_of_date", "currency"], how="left", validate="many_to_one")
        c = c.assign(adj=c["market_value"] * c["gbp_per_unit"] * (1 - c["haircut"]))
        c = c.groupby(["as_of_date", "netting_set_id"])["adj"].sum().reset_index()
        g = g.merge(c, on=["as_of_date", "netting_set_id"], how="left")
    else:
        g["adj"] = 0.0
    g["adj"] = g["adj"].fillna(0.0)
    g["exp"] = (g["pre"] - g["adj"]).clip(lower=0)
    return g.groupby(["as_of_date", "counterparty_id"])["exp"].sum().reset_index()


def _background(st: _State, cfg_gen: dict, fx: pd.DataFrame, n_background_trades: int) -> list[str]:
    rng = st.rng
    n = len(st.dates)
    first_bg = len(st.counterparties) - (int(cfg_gen["n_counterparties"]) - len(SCENARIOS))
    bg_cps = [c["counterparty_id"] for c in st.counterparties[first_bg:]]
    sets: list[tuple[str, str, bool]] = []
    for cp in bg_cps:
        k = int(rng.choice([1, 1, 1, 2, 2, 3]))
        for j in range(1, k + 1):
            netting = bool(rng.random() < 0.85)
            nid = f"NS-{cp}-{j:02d}"
            st.netting_sets.append({"netting_set_id": nid, "counterparty_id": cp, "netting_permitted": netting})
            sets.append((cp, nid, netting))
    base = 2
    extra = max(n_background_trades - base * len(sets), 0)
    counts = rng.multinomial(extra, np.full(len(sets), 1.0 / len(sets))) + base
    fx_lookup = {(r.as_of_date, r.currency): r.gbp_per_unit for r in fx.itertuples()}
    products = ["IRS", "CCS", "FX_FORWARD", "FX_SWAP", "EQUITY_SWAP"]
    set_net_gbp: dict[str, np.ndarray] = {}
    for (cp, nid, netting), k in zip(sets, counts):
        net_gbp = np.zeros(n)
        for _ in range(int(k)):
            ccy = str(rng.choice(["GBP", "USD", "EUR"], p=[0.45, 0.35, 0.20]))
            product = str(rng.choice(products))
            notional = float(np.clip(round(np.exp(rng.normal(np.log(20e6), 0.8)) / 1e5) * 1e5, 1e6, 150e6))
            mtm0 = rng.normal(0.0, 0.02 * notional)
            steps = rng.normal(0.0, 0.002 * notional, n)
            steps[0] = 0.0
            path = mtm0 + np.cumsum(steps)
            start_idx = 0 if rng.random() < 0.92 else int(rng.integers(5, 41))
            maturity_idx = None
            terminate_idx = None
            u = rng.random()
            if u < 0.10:
                maturity_idx = int(rng.integers(max(start_idx + 3, 5), 56))
                maturity = st.dates[maturity_idx]
            else:
                maturity = pd.Timestamp("2026-06-01") + pd.Timedelta(days=int(rng.integers(0, 3650)))
                if u < 0.12:
                    terminate_idx = int(rng.integers(max(start_idx + 3, 10), 51))
            _add_trade(st, cp, nid, product, ccy, notional, path, maturity,
                       start_idx=start_idx, maturity_idx=maturity_idx, terminate_idx=terminate_idx)
            for t in range(n):
                alive = t >= start_idx
                if maturity_idx is not None and t >= maturity_idx:
                    alive = False
                if terminate_idx is not None and t >= terminate_idx:
                    alive = False
                if alive:
                    net_gbp[t] += path[t] * fx_lookup[(st.dates[t], ccy)]
        set_net_gbp[nid] = net_gbp
    # Collateral on about half of the netting-permitted sets
    for cp, nid, netting in sets:
        if not netting or rng.random() >= 0.5:
            continue
        is_cash = rng.random() < 0.5
        haircut = 0.0 if is_cash else float(rng.choice([0.02, 0.04, 0.08, 0.15]))
        ccy = str(rng.choice(["GBP", "USD", "EUR"], p=[0.5, 0.3, 0.2]))
        eligible = bool(rng.random() < 0.9)
        direction = "RECEIVED" if rng.random() < 0.95 else "POSTED"
        coverage = float(rng.uniform(0.3, 1.1))
        exposure = np.clip(set_net_gbp[nid], 0, None)
        lagged = np.concatenate([[exposure[0]], exposure[:-1]])
        mv = np.array([coverage * lagged[t] / fx_lookup[(st.dates[t], ccy)] for t in range(n)])
        _add_collateral(st, f"COL-{nid}-01", cp, nid, ccy, mv, haircut, eligible, direction)
    return bg_cps


def generate_synthetic_data(config: dict[str, Any]) -> GeneratedData:
    gen = config["generation"]
    seed = int(gen["seed"])
    rng = np.random.default_rng(seed)
    dates = business_dates(gen["start_date"], int(gen["n_business_days"]))
    st = _State(dates=dates, rng=rng)
    n_cp = int(gen["n_counterparties"])
    if n_cp < len(SCENARIOS) + 5:
        raise ValueError(f"n_counterparties must be at least {len(SCENARIOS) + 5}")

    # Counterparty attributes
    sectors = list(_SECTORS)
    for i in range(1, n_cp + 1):
        cp = f"CP{i:03d}"
        sector = sectors[int(rng.integers(0, len(sectors)))]
        word = _NAME_WORDS[(i - 1) % len(_NAME_WORDS)]
        st.counterparties.append(
            {
                "counterparty_id": cp,
                "counterparty_name": f"Synthetic {word} {_SECTORS[sector]}",
                "parent_group": f"Synthetic Parent Group {int(rng.integers(1, _N_PARENTS + 1)):02d}",
                "country": _COUNTRIES[int(rng.integers(0, len(_COUNTRIES)))],
                "sector": sector,
            }
        )

    fx = _fx_table(dates, gen, rng)
    _scenario_counterparties(st)
    n_scenario_trades = len({t["trade_id"] for t in st.trades})
    n_bg_trades = max(int(gen["target_n_trades"]) - n_scenario_trades, 0)
    bg_cps = _background(st, gen, fx, n_bg_trades)

    # Ratings
    for c in st.counterparties:
        cp = c["counterparty_id"]
        if cp == "CP007":
            continue
        if cp == "CP008":
            st.ratings.append({"counterparty_id": cp, "effective_from": pd.Timestamp(RATING_DEFAULT_FROM),
                               "effective_to": pd.Timestamp("2026-02-09"), "rating": "A"})
            st.ratings.append({"counterparty_id": cp, "effective_from": pd.Timestamp("2026-02-09"),
                               "effective_to": pd.NaT, "rating": "BBB"})
            continue
        r = str(rng.choice(_BACKGROUND_RATINGS[0], p=_BACKGROUND_RATINGS[1]))
        st.ratings.append({"counterparty_id": cp, "effective_from": pd.Timestamp(RATING_DEFAULT_FROM),
                           "effective_to": pd.NaT, "rating": r})

    trades = pd.DataFrame(st.trades)
    collateral = pd.DataFrame(st.collateral)
    netting_sets = pd.DataFrame(st.netting_sets)

    # Background limits calibrated against simulated peak exposure
    quick = _quick_counterparty_exposure(trades, collateral, netting_sets, fx)
    peak = quick.groupby("counterparty_id")["exp"].max()
    tight = set(rng.choice(bg_cps, size=3, replace=False).tolist())
    fx0 = {r.currency: r.gbp_per_unit for r in fx[fx["as_of_date"] == dates[0]].itertuples()}
    for cp in bg_cps:
        p = float(peak.get(cp, 0.0))
        mult = float(rng.uniform(0.95, 1.2)) if cp in tight else float(rng.uniform(1.3, 3.0))
        ccy = str(rng.choice(["GBP", "USD", "EUR"], p=[0.7, 0.2, 0.1]))
        amount = 5e6 if p <= 0 else np.ceil(p * mult / fx0[ccy] / 1e5) * 1e5
        st.limits.append({"limit_id": st.limit_id(), "counterparty_id": cp,
                          "effective_from": pd.Timestamp(LIMIT_DEFAULT_FROM), "effective_to": pd.NaT,
                          "limit_currency": ccy, "limit_amount": float(amount)})
    limits = pd.DataFrame(st.limits)
    ratings = pd.DataFrame(st.ratings)
    counterparties = pd.DataFrame(st.counterparties)

    verification = _verify_scenarios(quick, limits, ratings, fx, trades, collateral, netting_sets, dates)
    failed = [k for k, v in verification.items() if not v]
    if failed:
        raise RuntimeError(f"Synthetic scenario guarantees not met: {failed}")

    raw = _to_raw_strings(counterparties, netting_sets, trades, collateral, ratings, limits, fx)
    raw["control_totals"] = build_control_totals(raw)

    metadata = {
        "description": "SYNTHETIC data for the Counterparty Credit Exposure and Limit Monitoring project. "
                       "All counterparties, trades, valuations, collateral, ratings and limits are fictional.",
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "parameters": gen,
        "business_calendar": "pandas.bdate_range Monday-Friday index; NOT an exchange or settlement holiday calendar.",
        "date_range": {"start": _fmt_date(dates[0]), "end": _fmt_date(dates[-1]), "n_dates": len(dates)},
        "row_counts": {k: int(len(v)) for k, v in raw.items()},
        "distinct_trades": int(trades["trade_id"].nunique()),
        "distinct_netting_sets": int(len(netting_sets)),
        "scenario_counterparties": SCENARIOS,
        "scenario_verification": verification,
        "conventions": {
            "mtm_sign": "Positive = receivable to the reporting entity; negative = payable by the reporting entity.",
            "fx": "GBP amount = local amount x gbp_per_unit; GBP rate = 1.",
            "maturity_feed": "Trades are reported ACTIVE up to and including maturity date, MATURED with MTM 0 for three business days afterwards, then dropped.",
            "termination_feed": "Terminated trades are reported TERMINATED with MTM 0 for two business days, then dropped.",
            "background_limits": "Background counterparty limits are set to simulated peak exposure x U(1.3, 3.0), except three 'tight' counterparties at x U(0.95, 1.2), converted at first-date FX and rounded up to 100,000.",
            "control_totals": "Built from the raw string tables before downstream processing: row counts and signed local-currency amount totals by table/date/currency.",
        },
    }
    return GeneratedData(tables=raw, metadata=metadata)


def _effective(records: pd.DataFrame, cp: str, d: pd.Timestamp) -> pd.DataFrame:
    r = records[records["counterparty_id"] == cp]
    end_ok = r["effective_to"].isna() | (d < r["effective_to"])
    return r[(r["effective_from"] <= d) & end_ok]


def _verify_scenarios(quick, limits, ratings, fx, trades, collateral, netting_sets, dates) -> dict[str, bool]:
    fxl = {(r.as_of_date, r.currency): r.gbp_per_unit for r in fx.itertuples()}
    exp = {(r.as_of_date, r.counterparty_id): r.exp for r in quick.itertuples()}

    def util_series(cp: str) -> list[tuple[float, float | None]]:
        out = []
        for d in dates:
            e = float(exp.get((d, cp), 0.0))
            lim = _effective(limits, cp, d)
            if len(lim) != 1:
                out.append((e, None))
            else:
                out.append((e, float(lim["limit_amount"].iloc[0]) * fxl[(d, lim["limit_currency"].iloc[0])]))
        return out

    def status(e: float, l: float | None) -> str:
        if l is None:
            return "Missing Limit"
        if l == 0:
            return "Zero Limit — Exposure" if e > 0.01 else "Zero Limit — No Exposure"
        if abs(e - l) <= 0.01:
            return "At Limit"
        if e > l:
            return "Breach"
        if 100 * e / l >= 80:
            return "Warning"
        return "Within Limit"

    st = {cp: [status(e, l) for e, l in util_series(cp)] for cp in [s["counterparty_id"] for s in SCENARIOS]}
    feb16 = list(dates).index(pd.Timestamp("2026-02-16"))
    feb23 = list(dates).index(pd.Timestamp("2026-02-23"))
    feb13 = list(dates).index(pd.Timestamp("2026-02-13"))
    e = lambda cp, i: float(exp.get((dates[i], cp), 0.0))  # noqa: E731
    return {
        "within_limit": all(s == "Within Limit" for s in st["CP001"]),
        "warning_utilisation": all(s == "Warning" for s in st["CP002"]),
        "breach": {"Within Limit", "Warning", "Breach"} <= set(st["CP003"]) and st["CP003"][-1] == "Breach",
        "positive_exposure_zero_limit": all(s == "Zero Limit — Exposure" for s in st["CP004"]),
        "zero_exposure_zero_limit": all(s == "Zero Limit — No Exposure" for s in st["CP005"]),
        "missing_effective_limit": all(s != "Missing Limit" for s in st["CP006"][:feb16])
        and all(s == "Missing Limit" for s in st["CP006"][feb16:]),
        "missing_effective_rating": (ratings["counterparty_id"] == "CP007").sum() == 0,
        "rating_downgrade": (ratings["counterparty_id"] == "CP008").sum() == 2,
        "limit_change": all(s == "Within Limit" for s in st["CP009"][:feb23])
        and all(s == "Warning" for s in st["CP009"][feb23:]),
        "overcollateralisation": all(e("CP010", i) == 0 for i in range(len(dates))),
        "offset_netting_permitted": all(abs(e("CP011", i) - 2e6) < 1e-6 for i in range(len(dates))),
        "offset_netting_not_permitted": all(abs(e("CP012", i) - 6e6) < 1e-6 for i in range(len(dates))),
        "no_cross_set_collateral_transfer": all(abs(e("CP013", i) - 4e6) < 1e-6 for i in range(len(dates))),
        "maturing_trade": abs(e("CP014", feb13 - 1) - 5e6) < 1e-6 and abs(e("CP014", feb13) - 1e6) < 1e-6,
        "ineligible_collateral": all(abs(e("CP015", i) - 3e6) < 1e-6 for i in range(len(dates))),
        "posted_collateral": bool(((collateral["counterparty_id"] == "CP016") & (collateral["direction"] == "POSTED")).any()),
        "at_limit": all(s == "At Limit" for s in st["CP017"]),
        "background_trades_mature_in_period": bool(
            ((trades["trade_status"] == "MATURED") & (~trades["counterparty_id"].isin([s["counterparty_id"] for s in SCENARIOS]))).any()
        ),
        "multiple_netting_sets_present": bool(netting_sets["counterparty_id"].value_counts().max() >= 2),
    }


def _to_raw_strings(counterparties, netting_sets, trades, collateral, ratings, limits, fx) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    out["counterparties"] = counterparties.sort_values("counterparty_id").astype(str)
    out["netting_sets"] = pd.DataFrame({
        "netting_set_id": netting_sets["netting_set_id"],
        "counterparty_id": netting_sets["counterparty_id"],
        "netting_permitted": netting_sets["netting_permitted"].map(_fmt_bool),
    }).sort_values("netting_set_id")
    tr = trades.sort_values(["as_of_date", "trade_id"])
    out["trades"] = pd.DataFrame({
        "as_of_date": tr["as_of_date"].map(_fmt_date),
        "trade_id": tr["trade_id"],
        "counterparty_id": tr["counterparty_id"],
        "netting_set_id": tr["netting_set_id"],
        "product_type": tr["product_type"],
        "currency": tr["currency"],
        "notional": tr["notional"].map(_fmt_money),
        "mtm": tr["mtm"].map(_fmt_money),
        "maturity_date": tr["maturity_date"].map(_fmt_date),
        "trade_status": tr["trade_status"],
    })
    co = collateral.sort_values(["as_of_date", "collateral_id"])
    out["collateral"] = pd.DataFrame({
        "as_of_date": co["as_of_date"].map(_fmt_date),
        "collateral_id": co["collateral_id"],
        "counterparty_id": co["counterparty_id"],
        "netting_set_id": co["netting_set_id"],
        "currency": co["currency"],
        "market_value": co["market_value"].map(_fmt_money),
        "haircut": co["haircut"].map(lambda x: f"{x:.4f}"),
        "eligible": co["eligible"].map(_fmt_bool),
        "direction": co["direction"],
    })
    ra = ratings.sort_values(["counterparty_id", "effective_from"])
    out["ratings"] = pd.DataFrame({
        "counterparty_id": ra["counterparty_id"],
        "effective_from": ra["effective_from"].map(_fmt_date),
        "effective_to": ra["effective_to"].map(_fmt_date),
        "rating": ra["rating"],
    })
    li = limits.sort_values("limit_id")
    out["limits"] = pd.DataFrame({
        "limit_id": li["limit_id"],
        "counterparty_id": li["counterparty_id"],
        "effective_from": li["effective_from"].map(_fmt_date),
        "effective_to": li["effective_to"].map(_fmt_date),
        "limit_currency": li["limit_currency"],
        "limit_amount": li["limit_amount"].map(_fmt_money),
    })
    fxs = fx.sort_values(["as_of_date", "currency"])
    out["fx_rates"] = pd.DataFrame({
        "as_of_date": fxs["as_of_date"].map(_fmt_date),
        "currency": fxs["currency"],
        "gbp_per_unit": fxs["gbp_per_unit"].map(lambda x: f"{x:.6f}"),
    })
    for k in out:
        out[k] = out[k][list(SCHEMAS[k].columns)].reset_index(drop=True)
    return out


def write_raw_tables(tables: dict[str, pd.DataFrame], raw_dir: str | Path) -> dict[str, Path]:
    """Write raw string tables to CSV with LF line endings (deterministic bytes)."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, df in tables.items():
        path = raw_dir / SOURCE_FILES[name]
        df.to_csv(path, index=False, lineterminator="\n", encoding="utf-8")
        written[name] = path
    return written


def write_generated_data(data: GeneratedData, raw_dir: str | Path) -> dict[str, Path]:
    written = write_raw_tables(data.tables, raw_dir)
    meta_path = Path(raw_dir) / "generation_metadata.json"
    meta_path.write_text(json.dumps(data.metadata, indent=2, default=str) + "\n", encoding="utf-8")
    written["generation_metadata"] = meta_path
    return written
