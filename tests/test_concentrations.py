import numpy as np
import pandas as pd
import pytest

from counterparty_mi.concentrations import breakdown, top_n

D = pd.Timestamp("2026-01-05")


def cp_frame(exposures, sectors=None):
    n = len(exposures)
    return pd.DataFrame({
        "as_of_date": [D] * n,
        "counterparty_id": [f"C{i:02d}" for i in range(n)],
        "counterparty_name": [f"Synthetic {i}" for i in range(n)],
        "parent_group": ["P"] * n, "country": ["GB"] * n,
        "sector": sectors or ["S"] * n, "rating": ["A"] * n,
        "gross_positive_mtm_gbp": exposures, "pre_collateral_exposure_gbp": exposures,
        "net_current_exposure_gbp": [float(x) for x in exposures], "limit_status": ["Within Limit"] * n,
    })


def test_ranking_descending_with_deterministic_ties():
    cp = cp_frame([10, 30, 30, 0, 5])
    t = top_n(cp, 3)
    assert t["counterparty_id"].tolist() == ["C01", "C02", "C00"]
    assert t["rank"].tolist() == [1, 2, 3]


def test_share_denominator_is_total_net_exposure_of_all_counterparties():
    cp = cp_frame([50, 30, 20])  # total 100
    t = top_n(cp, 2)
    assert t["total_net_exposure_gbp"].iloc[0] == 100
    assert t["share_of_total_net_exposure_pct"].tolist() == pytest.approx([50.0, 30.0])
    assert t["cumulative_share_pct"].iloc[-1] == pytest.approx(80.0)


def test_top_n_only_ranks_positive_exposure_and_caps_at_n():
    cp = cp_frame(list(range(1, 16)) + [0, 0])
    t = top_n(cp, 10)
    assert len(t) == 10
    assert t["net_current_exposure_gbp"].min() == 6


def test_zero_total_exposure_gives_no_ranking_and_blank_shares():
    cp = cp_frame([0, 0, 0])
    assert top_n(cp, 10).empty
    b = breakdown(cp, "sector")
    assert b["net_current_exposure_gbp"].iloc[0] == 0
    assert np.isnan(b["share_of_total_net_exposure_pct"].iloc[0])


def test_breakdown_totals_and_shares():
    cp = cp_frame([10, 20, 70], sectors=["Banks", "Insurance", "Banks"])
    b = breakdown(cp, "sector").set_index("sector")
    assert b.loc["Banks", "net_current_exposure_gbp"] == 80
    assert b.loc["Banks", "counterparty_count"] == 2
    assert b["net_current_exposure_gbp"].sum() == cp["net_current_exposure_gbp"].sum()
    assert b["share_of_total_net_exposure_pct"].sum() == pytest.approx(100.0)
