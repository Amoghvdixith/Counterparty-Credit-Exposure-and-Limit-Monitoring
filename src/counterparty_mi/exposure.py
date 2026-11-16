"""Simplified current-exposure calculation by netting set and counterparty.

This is NOT a regulatory exposure calculation (no PFE, SA-CCR, EAD, CVA, margin
period of risk, thresholds or minimum transfer amounts). Netting eligibility is a
synthetic modelling flag, not a legal opinion.

Per (as_of_date, netting set), with all amounts converted to GBP first:

    gross_positive_mtm      = sum(max(mtm_gbp, 0))
    signed_net_mtm          = sum(mtm_gbp)
    pre_collateral_exposure = max(signed_net_mtm, 0)   if netting permitted
                            = gross_positive_mtm       otherwise
    recognised_collateral   = sum(market_value x FX x (1 - haircut))
                              over RECEIVED, eligible collateral on netting-permitted sets
    net_current_exposure    = max(pre_collateral_exposure - recognised_collateral, 0)
    collateral_benefit      = pre_collateral_exposure - net_current_exposure
    netting_benefit         = gross_positive_mtm - pre_collateral_exposure
    excess_collateral       = max(recognised_collateral - pre_collateral_exposure, 0)  (unused, not transferable)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .fx import to_gbp

# Trade inclusion -----------------------------------------------------------
EXCL_TERMINATED = "STATUS_TERMINATED"
EXCL_MATURED_STATUS = "STATUS_MATURED"
EXCL_MATURITY = "MATURITY_ON_OR_BEFORE_AS_OF"

# Collateral classes (exactly one per collateral record) ---------------------
COLL_RECOGNISED = "RECOGNISED"
COLL_POSTED = "POSTED_NOT_RECOGNISED"
COLL_NON_NETTING = "NON_NETTING_SET_UNSUPPORTED"
COLL_INELIGIBLE = "INELIGIBLE"
COLLATERAL_CLASSES = (COLL_RECOGNISED, COLL_POSTED, COLL_NON_NETTING, COLL_INELIGIBLE)

NS_KEYS = ["as_of_date", "netting_set_id"]
CP_KEYS = ["as_of_date", "counterparty_id"]

EXPOSURE_COLUMNS = [
    "gross_positive_mtm_gbp",
    "signed_net_mtm_gbp",
    "netting_benefit_gbp",
    "pre_collateral_exposure_gbp",
    "recognised_collateral_gbp",
    "collateral_benefit_gbp",
    "excess_collateral_gbp",
    "net_current_exposure_gbp",
]


def split_trades(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split accepted trade snapshots into included and excluded populations.

    Included: trade_status == ACTIVE and maturity_date > as_of_date.
    A trade maturing on the as-of date is excluded on that date (simplified
    convention: final cash flows are assumed settled at the end of maturity day).
    """
    reason = pd.Series("", index=trades.index, dtype=object)
    reason[trades["trade_status"] == "TERMINATED"] = EXCL_TERMINATED
    reason[trades["trade_status"] == "MATURED"] = EXCL_MATURED_STATUS
    active = trades["trade_status"] == "ACTIVE"
    reason[active & (trades["maturity_date"] <= trades["as_of_date"])] = EXCL_MATURITY
    included = trades[reason == ""].copy()
    excluded = trades[reason != ""].copy()
    excluded["exclusion_reason"] = reason[reason != ""].astype(str)
    return included.reset_index(drop=True), excluded.reset_index(drop=True)


def trades_to_gbp(included: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    return to_gbp(included, "mtm", fx, out_col="mtm_gbp")


def classify_collateral(collateral: pd.DataFrame, netting_sets: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    """Assign each collateral record exactly one class and compute GBP values.

    Precedence: POSTED -> non-netting set -> ineligible -> recognised.
    """
    c = collateral.merge(
        netting_sets[["netting_set_id", "netting_permitted"]], on="netting_set_id", how="left", validate="many_to_one"
    )
    c = to_gbp(c, "market_value", fx, out_col="market_value_gbp")
    c["haircut_adjusted_gbp"] = c["market_value_gbp"] * (1.0 - c["haircut"])
    cls = np.select(
        [c["direction"] != "RECEIVED", ~c["netting_permitted"].astype(bool), ~c["eligible"].astype(bool)],
        [COLL_POSTED, COLL_NON_NETTING, COLL_INELIGIBLE],
        default=COLL_RECOGNISED,
    )
    c["collateral_class"] = cls
    c["recognised_value_gbp"] = np.where(c["collateral_class"] == COLL_RECOGNISED, c["haircut_adjusted_gbp"], 0.0)
    return c


def netting_set_exposure(
    included_gbp: pd.DataFrame,
    collateral_classified: pd.DataFrame,
    netting_sets: pd.DataFrame,
    dates,
) -> pd.DataFrame:
    """Exposure for every (report date x netting set).

    Netting sets with no included trades on a date have zero MTM and zero exposure
    (absence of trades, not a filled missing value).
    """
    grid = pd.DataFrame({"as_of_date": pd.Index(dates).unique().sort_values()}).merge(
        netting_sets[["netting_set_id", "counterparty_id", "netting_permitted"]], how="cross"
    )
    t = included_gbp.assign(_pos=included_gbp["mtm_gbp"].clip(lower=0.0))
    tagg = t.groupby(NS_KEYS, sort=True).agg(
        included_trade_count=("trade_id", "size"),
        gross_positive_mtm_gbp=("_pos", "sum"),
        signed_net_mtm_gbp=("mtm_gbp", "sum"),
    ).reset_index()
    out = grid.merge(tagg, on=NS_KEYS, how="left", validate="one_to_one")
    unknown = set(map(tuple, tagg[NS_KEYS].itertuples(index=False))) - set(map(tuple, grid[NS_KEYS].itertuples(index=False)))
    if unknown:
        raise ValueError(f"Trades reference netting sets/dates outside the reporting grid: {sorted(unknown)[:5]}")

    c = collateral_classified
    c = c.assign(_unrec=np.where(c["collateral_class"] == COLL_RECOGNISED, 0.0, c["haircut_adjusted_gbp"]))
    cagg = c.groupby(NS_KEYS, sort=True).agg(
        collateral_record_count=("collateral_id", "size"),
        recognised_collateral_gbp=("recognised_value_gbp", "sum"),
        unrecognised_collateral_gbp=("_unrec", "sum"),
    ).reset_index()
    out = out.merge(cagg, on=NS_KEYS, how="left", validate="one_to_one")

    for col in ("included_trade_count", "collateral_record_count"):
        out[col] = out[col].fillna(0).astype(int)
    for col in ("gross_positive_mtm_gbp", "signed_net_mtm_gbp", "recognised_collateral_gbp",
                "unrecognised_collateral_gbp"):
        out[col] = out[col].fillna(0.0).astype(float)

    permitted = out["netting_permitted"].astype(bool)
    out["pre_collateral_exposure_gbp"] = np.where(
        permitted, out["signed_net_mtm_gbp"].clip(lower=0.0), out["gross_positive_mtm_gbp"]
    )
    out["netting_benefit_gbp"] = out["gross_positive_mtm_gbp"] - out["pre_collateral_exposure_gbp"]
    out["net_current_exposure_gbp"] = (out["pre_collateral_exposure_gbp"] - out["recognised_collateral_gbp"]).clip(lower=0.0)
    out["collateral_benefit_gbp"] = out["pre_collateral_exposure_gbp"] - out["net_current_exposure_gbp"]
    out["excess_collateral_gbp"] = (out["recognised_collateral_gbp"] - out["pre_collateral_exposure_gbp"]).clip(lower=0.0)
    cols = ["as_of_date", "counterparty_id", "netting_set_id", "netting_permitted",
            "included_trade_count", "collateral_record_count", *EXPOSURE_COLUMNS, "unrecognised_collateral_gbp"]
    return out[cols].sort_values(["as_of_date", "counterparty_id", "netting_set_id"]).reset_index(drop=True)


def counterparty_exposure(ns_exposure: pd.DataFrame, counterparties: pd.DataFrame, dates) -> pd.DataFrame:
    """Sum netting-set results within each counterparty (no cross-counterparty netting,
    no transfer of collateral between netting sets)."""
    sums = [c for c in EXPOSURE_COLUMNS if c != "signed_net_mtm_gbp"] + ["signed_net_mtm_gbp"]
    agg = ns_exposure.groupby(CP_KEYS, sort=True).agg(
        netting_set_count=("netting_set_id", "size"),
        included_trade_count=("included_trade_count", "sum"),
        **{c: (c, "sum") for c in sums},
    ).reset_index()
    grid = pd.DataFrame({"as_of_date": pd.Index(dates).unique().sort_values()}).merge(
        counterparties[["counterparty_id"]], how="cross"
    )
    out = grid.merge(agg, on=CP_KEYS, how="left", validate="one_to_one")
    for col in ("netting_set_count", "included_trade_count"):
        out[col] = out[col].fillna(0).astype(int)
    for col in sums:
        out[col] = out[col].fillna(0.0)
    return out.sort_values(CP_KEYS).reset_index(drop=True)
