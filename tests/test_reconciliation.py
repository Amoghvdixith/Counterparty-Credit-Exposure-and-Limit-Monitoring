import dataclasses

import pandas as pd
import pytest

from conftest import D1, make_config, with_source_rows
from counterparty_mi.exposure import netting_set_exposure
from counterparty_mi.pipeline import compute
from counterparty_mi.reconciliation import FAIL, PASS, run_controls
from counterparty_mi.synthetic import build_control_totals
from counterparty_mi.validation import validate_sources


def computed(raw, tmp_path):
    src = with_source_rows(raw)
    v = validate_sources(src)
    assert not v.fatal
    dates = [pd.Timestamp(d) for d in sorted(v.tables["trades"]["as_of_date"].unique())]
    return compute(v, src, dates, make_config(tmp_path))


def controls(comp, ri=None):
    return run_controls(ri or comp.recon_inputs, "test", 0.01, 1e-9)


def failed(recon):
    return recon[recon["result"] == FAIL]


def test_clean_fixture_passes_every_control(raw, tmp_path):
    recon = controls(computed(raw, tmp_path))
    assert len(recon) > 50
    assert (recon["result"] == PASS).all()
    assert {f"C{i:02d}" for i in range(1, 12)} <= set(recon["control_id"])


def test_business_breach_does_not_fail_arithmetic_controls(raw, tmp_path):
    comp = computed(raw, tmp_path)
    cp = comp.cp
    assert (cp["limit_status"] == "Breach").any()  # CPB on 2026-01-05 (190 vs 180)
    assert failed(controls(comp)).empty


def test_tampered_raw_source_fails_source_controls(raw, tmp_path):
    controls_before = build_control_totals(raw)
    raw["trades"].loc[0, "mtm"] = "101.00"  # change after the source controls were produced
    raw["control_totals"] = controls_before
    recon = controls(computed(raw, tmp_path))
    f = failed(recon)
    assert set(f["control_id"]) == {"C02"}
    row = f.iloc[0]
    assert row["as_of_date"] == D1 and row["difference"] == pytest.approx(1.0)


def test_extra_raw_row_fails_row_count_control(raw, tmp_path):
    controls_before = build_control_totals(raw)
    extra = raw["trades"].iloc[[0]].copy()
    extra["trade_id"] = "T99"
    extra["mtm"] = "0.00"
    raw["trades"] = pd.concat([raw["trades"], extra], ignore_index=True)
    raw["control_totals"] = controls_before
    f = failed(controls(computed(raw, tmp_path)))
    assert "SOURCE_ROW_COUNT" in set(f["control_name"])


def test_dropped_trade_is_detected(raw, tmp_path):
    comp = computed(raw, tmp_path)
    ri = comp.recon_inputs
    dropped = ri.included_gbp[ri.included_gbp["trade_id"] != "T6"].reset_index(drop=True)
    ns = netting_set_exposure(dropped, ri.collateral_classified, _netting_sets(comp), ri.report_dates)
    f = failed(controls(comp, dataclasses.replace(ri, ns_exposure=ns)))
    assert {"TRADE_TO_NETTING_SET_COUNT", "TRADE_TO_NETTING_SET_SIGNED_MTM"} <= set(f["control_name"])


def test_join_duplication_is_detected(raw, tmp_path):
    comp = computed(raw, tmp_path)
    ri = comp.recon_inputs
    duplicated = pd.concat([ri.included_gbp, ri.included_gbp.iloc[[0]]], ignore_index=True)
    ns = netting_set_exposure(duplicated, ri.collateral_classified, _netting_sets(comp), ri.report_dates)
    f = failed(controls(comp, dataclasses.replace(ri, ns_exposure=ns)))
    assert "TRADE_TO_NETTING_SET_COUNT" in set(f["control_name"])


def test_quarantine_bridge_reconciles(raw, tmp_path):
    raw["collateral"].loc[0, "haircut"] = "2.0"  # malformed -> quarantined
    raw["control_totals"] = build_control_totals(raw)
    comp = computed(raw, tmp_path)
    recon = controls(comp)
    bridge = recon[(recon["control_id"] == "C03") & (recon["scope"] == "collateral")]
    assert (bridge["result"] == PASS).all()
    table_row = bridge[bridge["as_of_date"] == "ALL"].iloc[0]
    assert table_row["expected_value"] == 6 and "quarantined (1)" in table_row["explanation"]
    assert failed(recon).empty


def _netting_sets(comp):
    ns = comp.ns
    return ns[["netting_set_id", "counterparty_id", "netting_permitted"]].drop_duplicates().reset_index(drop=True)
