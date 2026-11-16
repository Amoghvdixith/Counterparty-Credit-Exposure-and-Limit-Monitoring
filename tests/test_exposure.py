"""Exposure tests with hand-computed expectations."""

import pandas as pd
import pytest

from counterparty_mi.exposure import (
    COLL_INELIGIBLE, COLL_NON_NETTING, COLL_POSTED, COLL_RECOGNISED, classify_collateral, counterparty_exposure,
    netting_set_exposure, split_trades, trades_to_gbp,
)

D = pd.Timestamp("2026-01-05")
FX = pd.DataFrame({"as_of_date": [D] * 3, "currency": ["GBP", "USD", "EUR"], "gbp_per_unit": [1.0, 0.8, 0.9]})
FAR = pd.Timestamp("2030-01-01")


def trade(tid, ns, mtm, cp="X", ccy="GBP", maturity=FAR, status="ACTIVE", date=D):
    return {"as_of_date": date, "trade_id": tid, "counterparty_id": cp, "netting_set_id": ns, "currency": ccy,
            "mtm": float(mtm), "maturity_date": maturity, "trade_status": status}


def coll(cid, ns, mv, haircut=0.0, eligible=True, direction="RECEIVED", cp="X", ccy="GBP"):
    return {"as_of_date": D, "collateral_id": cid, "counterparty_id": cp, "netting_set_id": ns, "currency": ccy,
            "market_value": float(mv), "haircut": float(haircut), "eligible": eligible, "direction": direction}


def run(trades, collateral, sets):
    ns_df = pd.DataFrame(sets, columns=["netting_set_id", "counterparty_id", "netting_permitted"])
    t = pd.DataFrame(trades)
    inc, exc = split_trades(t)
    c = pd.DataFrame(collateral, columns=list(coll("c", "n", 0).keys()))
    cc = classify_collateral(c, ns_df, FX)
    ns = netting_set_exposure(trades_to_gbp(inc, FX), cc, ns_df, [D])
    cps = pd.DataFrame({"counterparty_id": sorted(ns_df["counterparty_id"].unique())})
    return ns.set_index("netting_set_id"), counterparty_exposure(ns, cps, [D]).set_index("counterparty_id"), cc, exc


def test_netting_permitted_plus100_minus60_gives_40():
    ns, _, _, _ = run([trade("a", "N", 100), trade("b", "N", -60)], [], [("N", "X", True)])
    assert ns.loc["N", "gross_positive_mtm_gbp"] == 100
    assert ns.loc["N", "pre_collateral_exposure_gbp"] == 40
    assert ns.loc["N", "netting_benefit_gbp"] == 60


def test_no_netting_plus100_minus60_gives_100():
    ns, _, _, _ = run([trade("a", "N", 100), trade("b", "N", -60)], [], [("N", "X", False)])
    assert ns.loc["N", "pre_collateral_exposure_gbp"] == 100
    assert ns.loc["N", "netting_benefit_gbp"] == 0


def test_eligible_collateral_deducted_once_with_haircut():
    # three trades, one collateral item of 20 with 10% haircut -> 18 deducted once (not per trade)
    ns, _, _, _ = run([trade("a", "N", 30), trade("b", "N", 20), trade("c", "N", 10)],
                      [coll("c1", "N", 20, haircut=0.10)], [("N", "X", True)])
    assert ns.loc["N", "pre_collateral_exposure_gbp"] == 60
    assert ns.loc["N", "recognised_collateral_gbp"] == pytest.approx(18.0)
    assert ns.loc["N", "net_current_exposure_gbp"] == pytest.approx(42.0)
    assert ns.loc["N", "collateral_benefit_gbp"] == pytest.approx(18.0)


def test_collateral_fx_and_haircut():
    # 100 USD at 0.8 with 25% haircut = 60 GBP
    ns, _, _, _ = run([trade("a", "N", 100)], [coll("c1", "N", 100, haircut=0.25, ccy="USD")], [("N", "X", True)])
    assert ns.loc["N", "net_current_exposure_gbp"] == pytest.approx(40.0)


def test_ineligible_and_posted_collateral_ignored():
    ns, _, cc, _ = run([trade("a", "N", 100)],
                       [coll("c1", "N", 50, eligible=False), coll("c2", "N", 50, direction="POSTED")],
                       [("N", "X", True)])
    assert ns.loc["N", "net_current_exposure_gbp"] == 100
    assert set(cc["collateral_class"]) == {COLL_INELIGIBLE, COLL_POSTED}


def test_collateral_on_non_netting_set_is_unsupported():
    ns, _, cc, _ = run([trade("a", "N", 100)], [coll("c1", "N", 50)], [("N", "X", False)])
    assert ns.loc["N", "net_current_exposure_gbp"] == 100
    assert cc["collateral_class"].iloc[0] == COLL_NON_NETTING


def test_excess_collateral_floors_at_zero():
    ns, _, cc, _ = run([trade("a", "N", 30)], [coll("c1", "N", 50)], [("N", "X", True)])
    assert ns.loc["N", "net_current_exposure_gbp"] == 0
    assert ns.loc["N", "collateral_benefit_gbp"] == 30
    assert ns.loc["N", "excess_collateral_gbp"] == 20
    assert cc["collateral_class"].iloc[0] == COLL_RECOGNISED


def test_no_cross_set_collateral_transfer():
    ns, cp, _, _ = run([trade("a", "N1", 20), trade("b", "N2", 40)], [coll("c1", "N1", 50)],
                       [("N1", "X", True), ("N2", "X", True)])
    assert ns.loc["N1", "net_current_exposure_gbp"] == 0
    assert ns.loc["N2", "net_current_exposure_gbp"] == 40
    assert cp.loc["X", "net_current_exposure_gbp"] == 40  # not 10


def test_no_cross_counterparty_netting():
    ns, cp, _, _ = run([trade("a", "N1", 100, cp="X"), trade("b", "N2", -100, cp="Y")], [],
                       [("N1", "X", True), ("N2", "Y", True)])
    assert cp.loc["X", "net_current_exposure_gbp"] == 100
    assert cp.loc["Y", "net_current_exposure_gbp"] == 0


def test_maturity_and_status_exclusion():
    trades = [trade("live", "N", 10, maturity=D + pd.Timedelta(days=1)),
              trade("mat_today", "N", 1000, maturity=D),
              trade("matured", "N", 0, status="MATURED", maturity=D - pd.Timedelta(days=1)),
              trade("terminated", "N", 0, status="TERMINATED")]
    ns, _, _, exc = run(trades, [], [("N", "X", True)])
    assert ns.loc["N", "included_trade_count"] == 1
    assert ns.loc["N", "pre_collateral_exposure_gbp"] == 10
    assert dict(zip(exc["trade_id"], exc["exclusion_reason"])) == {
        "mat_today": "MATURITY_ON_OR_BEFORE_AS_OF", "matured": "STATUS_MATURED", "terminated": "STATUS_TERMINATED"}


def test_mtm_converted_before_aggregation():
    # +100 USD (80 GBP) and -90 GBP net to -10 GBP -> exposure 0; would be +10 if netted in local units
    ns, _, _, _ = run([trade("a", "N", 100, ccy="USD"), trade("b", "N", -90)], [], [("N", "X", True)])
    assert ns.loc["N", "signed_net_mtm_gbp"] == pytest.approx(-10.0)
    assert ns.loc["N", "pre_collateral_exposure_gbp"] == 0


def test_notional_is_not_exposure():
    t = trade("a", "N", 5)
    t["notional"] = 1_000_000.0
    ns, _, _, _ = run([t], [], [("N", "X", True)])
    assert ns.loc["N", "net_current_exposure_gbp"] == 5
