"""Concentration rankings and dimensional breakdowns.

All measures use net current exposure (GBP). Every share column states its
denominator: ``share_of_total_net_exposure_pct`` = 100 x item exposure / total
net current exposure of all counterparties on the same date. When the total is
zero the share is blank (NaN) rather than a division by zero.

Parent-group exposure is descriptive only. No group limits are supplied, so no
group-limit test is performed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _share(numer: pd.Series, denom: pd.Series) -> pd.Series:
    d = denom.astype(float)
    return pd.Series(np.where(d > 0, 100.0 * numer.astype(float) / d.where(d > 0, 1.0), np.nan), index=numer.index)


def daily_totals(cp: pd.DataFrame) -> pd.Series:
    return cp.groupby("as_of_date")["net_current_exposure_gbp"].sum()


def top_n(cp: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Top ``n`` counterparties per date by net current exposure.

    Only counterparties with positive exposure are ranked. Ties are broken by
    counterparty_id ascending so the ranking is deterministic.
    """
    totals = daily_totals(cp).rename("total_net_exposure_gbp")
    pos = cp[cp["net_current_exposure_gbp"] > 0]
    ranked = pos.sort_values(["as_of_date", "net_current_exposure_gbp", "counterparty_id"],
                             ascending=[True, False, True])
    ranked = ranked.assign(rank=ranked.groupby("as_of_date").cumcount() + 1)
    top = ranked[ranked["rank"] <= n].merge(totals, left_on="as_of_date", right_index=True, how="left",
                                            validate="many_to_one")
    top["share_of_total_net_exposure_pct"] = _share(top["net_current_exposure_gbp"], top["total_net_exposure_gbp"])
    top["cumulative_share_pct"] = top.groupby("as_of_date")["share_of_total_net_exposure_pct"].cumsum()
    cols = ["as_of_date", "rank", "counterparty_id", "counterparty_name", "parent_group", "sector", "country",
            "rating", "net_current_exposure_gbp", "total_net_exposure_gbp", "share_of_total_net_exposure_pct",
            "cumulative_share_pct", "limit_status"]
    return top[cols].reset_index(drop=True)


def breakdown(cp: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """Exposure by ``dimension`` (rating, sector, country, parent_group) per date."""
    totals = daily_totals(cp).rename("total_net_exposure_gbp")
    g = cp.groupby(["as_of_date", dimension], sort=True).agg(
        counterparty_count=("counterparty_id", "size"),
        counterparties_with_exposure=("net_current_exposure_gbp", lambda s: int((s > 0).sum())),
        gross_positive_mtm_gbp=("gross_positive_mtm_gbp", "sum"),
        pre_collateral_exposure_gbp=("pre_collateral_exposure_gbp", "sum"),
        net_current_exposure_gbp=("net_current_exposure_gbp", "sum"),
    ).reset_index()
    g = g.merge(totals, left_on="as_of_date", right_index=True, how="left", validate="many_to_one")
    g["share_of_total_net_exposure_pct"] = _share(g["net_current_exposure_gbp"], g["total_net_exposure_gbp"])
    return g


def daily_summary(cp: pd.DataFrame, top: pd.DataFrame, n_included: pd.Series, n_excluded: pd.Series) -> pd.DataFrame:
    """Daily totals, counts by limit status, top-N share and day-on-day movement."""
    from .limits import AT_LIMIT, BREACH, MISSING, WARNING, WITHIN, ZERO_EXPOSURE, ZERO_NO_EXPOSURE

    g = cp.groupby("as_of_date", sort=True)
    s = pd.DataFrame({
        "counterparty_count": g.size(),
        "counterparties_with_exposure": g["net_current_exposure_gbp"].apply(lambda x: int((x > 0).sum())),
        "netting_set_count": g["netting_set_count"].sum(),
        "gross_positive_mtm_gbp": g["gross_positive_mtm_gbp"].sum(),
        "netting_benefit_gbp": g["netting_benefit_gbp"].sum(),
        "pre_collateral_exposure_gbp": g["pre_collateral_exposure_gbp"].sum(),
        "recognised_collateral_gbp": g["recognised_collateral_gbp"].sum(),
        "collateral_benefit_gbp": g["collateral_benefit_gbp"].sum(),
        "net_current_exposure_gbp": g["net_current_exposure_gbp"].sum(),
    })
    status_counts = cp.pivot_table(index="as_of_date", columns="limit_status", values="counterparty_id",
                                   aggfunc="count", fill_value=0)
    for col, status in (("breach_count", BREACH), ("at_limit_count", AT_LIMIT), ("warning_count", WARNING),
                        ("within_limit_count", WITHIN), ("missing_limit_count", MISSING),
                        ("zero_limit_with_exposure_count", ZERO_EXPOSURE),
                        ("zero_limit_no_exposure_count", ZERO_NO_EXPOSURE)):
        s[col] = status_counts[status] if status in status_counts else 0
        s[col] = s[col].fillna(0).astype(int)
    tsum = top.groupby("as_of_date")["net_current_exposure_gbp"].sum()
    s["top10_net_exposure_gbp"] = tsum.reindex(s.index).fillna(0.0)
    s["top10_share_of_total_pct"] = _share(s["top10_net_exposure_gbp"], s["net_current_exposure_gbp"])
    s["included_trade_count"] = n_included.reindex(s.index).fillna(0).astype(int)
    s["excluded_trade_count"] = n_excluded.reindex(s.index).fillna(0).astype(int)
    prev = s["net_current_exposure_gbp"].shift(1)
    s["dod_change_net_exposure_gbp"] = s["net_current_exposure_gbp"] - prev
    s["dod_change_net_exposure_pct"] = np.where(prev > 0, 100.0 * s["dod_change_net_exposure_gbp"] / prev.where(prev > 0, 1.0), np.nan)
    return s.reset_index()
