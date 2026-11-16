"""Counterparty limit selection, utilisation, headroom and business status.

Status rules (evaluated in this order; exactly one applies):

1. Missing Limit               no effective limit on the report date
2. Zero Limit — Exposure       limit = 0 and exposure > tolerance
   Zero Limit — No Exposure    limit = 0 and exposure <= tolerance
3. At Limit                    |exposure - limit| <= tolerance
4. Breach                      exposure > limit + tolerance
5. Warning                     utilisation >= warning threshold (default 80%)
6. Within Limit                otherwise

Utilisation (%) = 100 x exposure / limit for positive limits only; it is left
blank (NaN) for missing and zero limits - never 0% and never infinity.
Headroom = limit - exposure (negative means exposure exceeds the limit); blank
when the limit is missing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .effective_dates import select_effective
from .fx import to_gbp

MISSING = "Missing Limit"
ZERO_EXPOSURE = "Zero Limit — Exposure"
ZERO_NO_EXPOSURE = "Zero Limit — No Exposure"
AT_LIMIT = "At Limit"
BREACH = "Breach"
WARNING = "Warning"
WITHIN = "Within Limit"
STATUSES = (MISSING, ZERO_EXPOSURE, ZERO_NO_EXPOSURE, BREACH, AT_LIMIT, WARNING, WITHIN)

ALERT_SEVERITY = {
    BREACH: "HIGH",
    ZERO_EXPOSURE: "HIGH",
    MISSING: "HIGH",
    AT_LIMIT: "MEDIUM",
    WARNING: "MEDIUM",
    ZERO_NO_EXPOSURE: "INFO",
}


def effective_limits_gbp(limits: pd.DataFrame, fx: pd.DataFrame, dates) -> pd.DataFrame:
    """One effective limit per (date, counterparty), converted at same-date FX."""
    eff = select_effective(limits, dates)
    if eff.empty:
        return pd.DataFrame(columns=["as_of_date", "counterparty_id", "limit_id", "limit_currency",
                                     "limit_amount", "limit_fx_rate", "limit_gbp"])
    eff = to_gbp(eff, "limit_amount", fx, currency_col="limit_currency", out_col="limit_gbp", rate_col="limit_fx_rate")
    return eff[["as_of_date", "counterparty_id", "limit_id", "limit_currency", "limit_amount",
                "limit_fx_rate", "limit_gbp"]].reset_index(drop=True)


def classify_status(exposure, limit_gbp, warning_pct: float = 80.0, tolerance: float = 0.01) -> np.ndarray:
    e = np.asarray(exposure, dtype=float)
    lim = np.asarray(limit_gbp, dtype=float)
    missing = np.isnan(lim)
    zero = ~missing & (lim == 0.0)
    positive = ~missing & (lim > 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        util = np.where(positive, 100.0 * e / np.where(positive, lim, 1.0), np.nan)
    conditions = [
        missing,
        zero & (e > tolerance),
        zero,
        positive & (np.abs(e - lim) <= tolerance),
        positive & (e > lim + tolerance),
        positive & (util >= warning_pct),
    ]
    choices = [MISSING, ZERO_EXPOSURE, ZERO_NO_EXPOSURE, AT_LIMIT, BREACH, WARNING]
    return np.select(conditions, choices, default=WITHIN).astype(object)


def utilisation_pct(exposure, limit_gbp) -> np.ndarray:
    e = np.asarray(exposure, dtype=float)
    lim = np.asarray(limit_gbp, dtype=float)
    positive = ~np.isnan(lim) & (lim > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(positive, 100.0 * e / np.where(positive, lim, 1.0), np.nan)


def apply_limits(cp_exposure: pd.DataFrame, eff_limits: pd.DataFrame, warning_pct: float, tolerance: float) -> pd.DataFrame:
    out = cp_exposure.merge(eff_limits, on=["as_of_date", "counterparty_id"], how="left", validate="one_to_one")
    e = out["net_current_exposure_gbp"].to_numpy(dtype=float)
    lim = out["limit_gbp"].to_numpy(dtype=float)
    out["utilisation_pct"] = utilisation_pct(e, lim)
    out["headroom_gbp"] = np.where(np.isnan(lim), np.nan, lim - e)
    out["limit_status"] = classify_status(e, lim, warning_pct, tolerance)
    return out


def limit_alerts(cp: pd.DataFrame) -> pd.DataFrame:
    """Every counterparty-date whose status is not Within Limit."""
    a = cp[cp["limit_status"] != WITHIN].copy()
    a["alert_severity"] = a["limit_status"].map(ALERT_SEVERITY)
    cols = ["as_of_date", "counterparty_id", "counterparty_name", "limit_status", "alert_severity",
            "net_current_exposure_gbp", "limit_id", "limit_currency", "limit_amount", "limit_gbp",
            "utilisation_pct", "headroom_gbp"]
    order = {s: i for i, s in enumerate(STATUSES)}
    a["_o"] = a["limit_status"].map(order)
    a = a.sort_values(["as_of_date", "_o", "counterparty_id"])
    return a[cols].reset_index(drop=True)
