"""End-to-end pipeline tests (all in temporary directories)."""

import json
import subprocess
import sys

import pandas as pd
import pytest

from conftest import D1, D2, write_case
from counterparty_mi.config import load_config
from counterparty_mi.pipeline import MANAGED_FILES, OUTPUT_CSVS, PipelineError, run_pipeline
from counterparty_mi.plotting import FIGURE_FILES
from counterparty_mi.reporting import write_report
from counterparty_mi.synthetic import generate_synthetic_data, write_generated_data


def _read(tmp_path, name):
    return pd.read_csv(tmp_path / "results" / name)


def test_synthetic_generation_is_deterministic(root):
    cfg = load_config(root / "configs" / "baseline.json")
    a = generate_synthetic_data(cfg)
    b = generate_synthetic_data(cfg)
    for name in a.tables:
        pd.testing.assert_frame_equal(a.tables[name], b.tables[name])
    m = a.metadata
    assert m["seed"] == 42 and m["distinct_trades"] == 400
    assert m["date_range"] == {"start": "2026-01-05", "end": "2026-03-27", "n_dates": 60}
    assert all(m["scenario_verification"].values())


def test_end_to_end_clean_run_matches_hand_calculation(raw, tmp_path):
    cfg = write_case(tmp_path, raw)
    res = run_pipeline(cfg, tmp_path)
    assert res.status == "SUCCESS" and res.exit_code == 0
    for name in OUTPUT_CSVS + ["run_metadata.json"]:
        assert (tmp_path / "results" / name).is_file(), name
    for fname in FIGURE_FILES.values():
        assert (tmp_path / "results" / "figures" / fname).stat().st_size > 10_000
    cp = _read(tmp_path, "counterparty_exposure.csv").set_index(["as_of_date", "counterparty_id"])
    assert cp.loc[(D1, "CPA"), "net_current_exposure_gbp"] == pytest.approx(62.0)
    assert cp.loc[(D1, "CPA"), "limit_status"] == "Within Limit"
    assert cp.loc[(D1, "CPB"), "net_current_exposure_gbp"] == pytest.approx(190.0)
    assert cp.loc[(D1, "CPB"), "limit_gbp"] == pytest.approx(180.0)
    assert cp.loc[(D1, "CPB"), "limit_status"] == "Breach"
    assert cp.loc[(D1, "CPB"), "rating"] == "BBB" and cp.loc[(D2, "CPB"), "rating"] == "BB"
    assert cp.loc[(D2, "CPB"), "net_current_exposure_gbp"] == pytest.approx(10.0)
    assert cp.loc[(D1, "CPC"), "limit_status"] == "Zero Limit — No Exposure"
    assert pd.isna(cp.loc[(D1, "CPC"), "utilisation_pct"])
    assert cp.loc[(D1, "CPC"), "rating"] == "Unrated"
    assert "INELIGIBLE_COLLATERAL" in cp.loc[(D1, "CPB"), "data_quality_flags"]
    daily = _read(tmp_path, "daily_summary.csv").set_index("as_of_date")
    assert daily.loc[D1, "net_current_exposure_gbp"] == pytest.approx(252.0)
    assert daily.loc[D2, "net_current_exposure_gbp"] == pytest.approx(72.0)
    assert daily.loc[D1, "breach_count"] == 1 and daily.loc[D2, "breach_count"] == 0
    assert daily.loc[D2, "excluded_trade_count"] == 1
    assert (daily["reconciliation_status"] == "PASS").all()
    recon = _read(tmp_path, "reconciliation.csv")
    assert (recon["result"] == "PASS").all()
    meta = json.loads((tmp_path / "results" / "run_metadata.json").read_text())
    assert meta["run_status"] == "SUCCESS" and meta["seed"] == 42
    assert str(tmp_path) not in json.dumps(meta)  # no absolute paths leak into metadata
    path, status = write_report(tmp_path / "results")
    text = path.read_text(encoding="utf-8")
    assert status == "SUCCESS" and "Management Report" in text and "SYNTHETIC DATA" in text
    assert "figures/exposure_trend.png" in text


def test_degraded_run_with_quarantined_record(raw, tmp_path):
    raw["trades"].loc[0, "mtm"] = "not-a-number"  # T1 on D1 quarantined
    cfg = write_case(tmp_path, raw)
    res = run_pipeline(cfg, tmp_path, make_figures=False)
    assert res.status == "DEGRADED" and res.exit_code == 1
    rej = _read(tmp_path, "rejected_records.csv")
    assert len(rej) == 1 and "mtm" in rej["reasons"].iloc[0]
    cp = _read(tmp_path, "counterparty_exposure.csv").set_index(["as_of_date", "counterparty_id"])
    assert "QUARANTINED_SOURCE_RECORDS" in cp.loc[(D1, "CPA"), "data_quality_flags"]
    # T1 (+100) removed: NS-A1 now -60 -> 0; CPA = 0 + 40 (NS-A2) = 40 (incomplete, flagged)
    assert cp.loc[(D1, "CPA"), "net_current_exposure_gbp"] == pytest.approx(40.0)
    assert (_read(tmp_path, "reconciliation.csv")["result"] == "PASS").all()
    path, status = write_report(tmp_path / "results")
    assert status == "DEGRADED" and "quarantined and excluded" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("case", ["duplicate", "missing_fx", "overlapping_limits"])
def test_fatal_errors_stop_publication(raw, tmp_path, case):
    if case == "duplicate":
        raw["trades"] = pd.concat([raw["trades"], raw["trades"].iloc[[3]]], ignore_index=True)
    elif case == "missing_fx":
        raw["fx_rates"] = raw["fx_rates"][raw["fx_rates"]["currency"] != "EUR"]
    else:
        extra = raw["limits"].iloc[[0]].copy()
        extra["limit_id"] = "L9"
        raw["limits"] = pd.concat([raw["limits"], extra], ignore_index=True)
    cfg = write_case(tmp_path, raw)
    res = run_pipeline(cfg, tmp_path)
    assert res.status == "FAILED_VALIDATION" and res.exit_code == 3
    out = tmp_path / "results"
    assert not (out / "counterparty_exposure.csv").exists()
    assert not (out / "figures" / "exposure_trend.png").exists()
    assert (_read(tmp_path, "data_quality_issues.csv")["severity"] == "FATAL").any()
    path, status = write_report(out)
    text = path.read_text(encoding="utf-8")
    assert "RUN ERROR SUMMARY" in text and "Management Report" not in text


def test_fatal_run_removes_stale_outputs_from_previous_success(raw, tmp_path):
    cfg = write_case(tmp_path, raw)
    assert run_pipeline(cfg, tmp_path, make_figures=False).status == "SUCCESS"
    assert (tmp_path / "results" / "counterparty_exposure.csv").exists()
    raw["fx_rates"] = raw["fx_rates"][raw["fx_rates"]["currency"] != "USD"]
    write_case(tmp_path, raw)
    assert run_pipeline(cfg, tmp_path, make_figures=False).status == "FAILED_VALIDATION"
    assert not (tmp_path / "results" / "counterparty_exposure.csv").exists()


def test_tampered_source_gives_reconciliation_failure(raw, tmp_path):
    from counterparty_mi.synthetic import build_control_totals
    raw["control_totals"] = build_control_totals(raw)
    raw["collateral"].loc[0, "market_value"] = "25.00"  # changed after controls were produced
    cfg = write_case(tmp_path, raw, recompute_controls=False)
    res = run_pipeline(cfg, tmp_path, make_figures=False)
    assert res.status == "FAILED_RECONCILIATION" and res.exit_code == 4
    daily = _read(tmp_path, "daily_summary.csv").set_index("as_of_date")
    assert daily.loc[D1, "reconciliation_status"] == "FAIL" and daily.loc[D2, "reconciliation_status"] == "PASS"
    path, status = write_report(tmp_path / "results")
    assert "Failed reconciliation controls" in path.read_text(encoding="utf-8")


def test_repeated_runs_are_identical_and_do_not_append(raw, tmp_path):
    cfg = write_case(tmp_path, raw)
    run_pipeline(cfg, tmp_path, make_figures=False)
    first = {n: (tmp_path / "results" / n).read_bytes() for n in OUTPUT_CSVS}
    run_pipeline(cfg, tmp_path, make_figures=False)
    second = {n: (tmp_path / "results" / n).read_bytes() for n in OUTPUT_CSVS}
    assert first == second
    assert len(_read(tmp_path, "counterparty_exposure.csv")) == 6  # 3 counterparties x 2 dates


def test_as_of_limits_history_and_uses_no_future_dates(raw, tmp_path):
    cfg = write_case(tmp_path, raw)
    res = run_pipeline(cfg, tmp_path, as_of=D1, output_dir=tmp_path / "asof", make_figures=False)
    assert res.latest_date == D1
    daily = pd.read_csv(tmp_path / "asof" / "daily_summary.csv")
    assert daily["as_of_date"].tolist() == [D1]
    with pytest.raises(PipelineError):
        run_pipeline(cfg, tmp_path, as_of="2026-02-30", output_dir=tmp_path / "bad")
    with pytest.raises(PipelineError):
        run_pipeline(cfg, tmp_path, as_of="2026-01-10", output_dir=tmp_path / "bad")


def test_managed_files_cover_required_outputs():
    assert "management_report.md" in MANAGED_FILES and "test_results.xml" not in MANAGED_FILES


def test_full_synthetic_baseline_in_temp_dir(root, tmp_path):
    cfg = load_config(root / "configs" / "baseline.json")
    cfg["paths"] = {"raw_dir": str(tmp_path / "raw"), "results_dir": str(tmp_path / "results")}
    write_generated_data(generate_synthetic_data(cfg), tmp_path / "raw")
    res = run_pipeline(cfg, tmp_path, make_figures=False)
    assert res.status == "SUCCESS"
    cp = _read(tmp_path, "counterparty_exposure.csv")
    last = cp[cp["as_of_date"] == "2026-03-27"].set_index("counterparty_id")["limit_status"]
    assert last["CP003"] == "Breach" and last["CP017"] == "At Limit" and last["CP006"] == "Missing Limit"
    assert last["CP004"] == "Zero Limit — Exposure" and last["CP005"] == "Zero Limit — No Exposure"


def test_cli_scripts_help_and_errors(root, tmp_path):
    for script in ("generate_data.py", "run_pipeline.py", "generate_report.py"):
        r = subprocess.run([sys.executable, str(root / "scripts" / script), "--help"], capture_output=True, text=True)
        assert r.returncode == 0 and "usage" in r.stdout.lower()
    r = subprocess.run([sys.executable, str(root / "scripts" / "run_pipeline.py"), "--config", str(tmp_path / "nope.json")],
                       capture_output=True, text=True)
    assert r.returncode == 5 and "not found" in r.stderr
    r = subprocess.run([sys.executable, str(root / "scripts" / "generate_report.py"), "--results-dir", str(tmp_path / "empty dir")],
                       capture_output=True, text=True)
    assert r.returncode == 5
