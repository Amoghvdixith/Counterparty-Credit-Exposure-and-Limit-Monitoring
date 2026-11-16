"""End-to-end orchestration: ingest -> validate -> exposure -> limits -> concentrations
-> reconciliation -> saved CSV/JSON/PNG outputs.

Run statuses and exit codes
---------------------------
SUCCESS                0  all controls pass, no quarantined records
DEGRADED               1  completed, but some source records were quarantined;
                          exposure may be incomplete
FAILED_VALIDATION      3  fatal validation errors; no exposure outputs are published
FAILED_RECONCILIATION  4  outputs written for investigation, but at least one
                          arithmetic/source control failed; not approved for use
(2 is argparse usage error; 5 is configuration / input-output error.)
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import __version__
from .concentrations import breakdown, daily_summary, top_n
from .config import resolve
from .effective_dates import select_effective
from .exposure import (
    COLL_INELIGIBLE, COLL_NON_NETTING, COLL_POSTED, classify_collateral, counterparty_exposure,
    netting_set_exposure, split_trades, trades_to_gbp,
)
from .fx import MissingFXError
from .ingestion import read_sources
from .limits import BREACH, MISSING, ZERO_EXPOSURE, ZERO_NO_EXPOSURE, apply_limits, effective_limits_gbp, limit_alerts
from .plotting import FIGURE_FILES, make_all
from .reconciliation import FAIL, ReconInputs, date_status, run_controls
from .schemas import UNRATED
from .validation import FATAL, ISSUE_COLUMNS, REJECT_COLUMNS, WARNING, validate_sources

SUCCESS = "SUCCESS"
DEGRADED = "DEGRADED"
FAILED_VALIDATION = "FAILED_VALIDATION"
FAILED_RECONCILIATION = "FAILED_RECONCILIATION"
EXIT_CODES = {SUCCESS: 0, DEGRADED: 1, FAILED_VALIDATION: 3, FAILED_RECONCILIATION: 4}

OUTPUT_CSVS = [
    "netting_set_exposure.csv", "counterparty_exposure.csv", "daily_summary.csv", "limit_alerts.csv",
    "top10_concentrations.csv", "exposure_by_rating.csv", "exposure_by_sector.csv", "exposure_by_country.csv",
    "exposure_by_parent.csv", "data_quality_issues.csv", "rejected_records.csv", "reconciliation.csv",
]
MANAGED_FILES = OUTPUT_CSVS + ["run_metadata.json", "management_report.md"]


class PipelineError(RuntimeError):
    """Configuration or input/output problem (exit code 5)."""


@dataclass
class PipelineResult:
    status: str
    exit_code: int
    output_dir: Path
    run_id: str
    latest_date: str | None = None
    messages: list[str] = field(default_factory=list)


def _clear_outputs(out_dir: Path) -> None:
    """Remove previous pipeline-managed outputs so runs never append or leave stale files.
    test_results.xml (written by pytest) and unrelated files are left untouched."""
    for name in MANAGED_FILES:
        p = out_dir / name
        if p.is_file():
            p.unlink()
    fig_dir = out_dir / "figures"
    for name in FIGURE_FILES.values():
        p = fig_dir / name
        if p.is_file():
            p.unlink()


def _fmt_frame(df: pd.DataFrame, full_precision: bool = False) -> pd.DataFrame:
    out = df.copy()
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            out[c] = s.dt.strftime("%Y-%m-%d").fillna("")
        elif pd.api.types.is_bool_dtype(s):
            out[c] = s.map({True: "TRUE", False: "FALSE"})
        elif pd.api.types.is_float_dtype(s) and not full_precision:
            decimals = 6 if c.endswith("fx_rate") or c == "gbp_per_unit" else 4 if c.endswith("_pct") else 2
            out[c] = s.round(decimals)
    return out


def _write_csv(df: pd.DataFrame, path: Path, full_precision: bool = False) -> None:
    _fmt_frame(df, full_precision).to_csv(path, index=False, lineterminator="\n", na_rep="", encoding="utf-8")


def _package_versions() -> dict[str, str]:
    out = {}
    for pkg in ("numpy", "pandas", "matplotlib", "pytest"):
        try:
            out[pkg] = importlib_metadata.version(pkg)
        except importlib_metadata.PackageNotFoundError:
            out[pkg] = "not installed"
    return out


def _run_id(config: dict, checksums: dict, as_of: str | None) -> str:
    payload = json.dumps({"config": config, "checksums": checksums, "as_of": as_of}, sort_keys=True, default=str)
    return f"{config.get('run_name', 'run')}-{hashlib.sha256(payload.encode()).hexdigest()[:12]}"


def _dq_flags(cp: pd.DataFrame, coll: pd.DataFrame, rejected: pd.DataFrame) -> pd.Series:
    flags: dict[tuple, list[str]] = {}
    keys = list(zip(cp["as_of_date"], cp["counterparty_id"]))
    for k, missing in zip(keys, cp["rating_missing"]):
        flags[k] = ["MISSING_RATING"] if missing else []
    labels = {COLL_INELIGIBLE: "INELIGIBLE_COLLATERAL", COLL_POSTED: "POSTED_COLLATERAL_NOT_RECOGNISED",
              COLL_NON_NETTING: "COLLATERAL_ON_NON_NETTING_SET"}
    for (d, c, cls), _ in coll[coll["collateral_class"].isin(labels)].groupby(
            ["as_of_date", "counterparty_id", "collateral_class"]):
        if (d, c) in flags:
            flags[(d, c)].append(labels[cls])
    if len(rejected):
        dates = sorted(set(cp["as_of_date"]))
        date_str = {pd.Timestamp(d).strftime("%Y-%m-%d"): d for d in dates}
        for r in rejected.itertuples():
            if not r.counterparty_id:
                continue
            targets = [date_str[r.as_of_date]] if r.as_of_date in date_str else (dates if not r.as_of_date else [])
            for d in targets:
                k = (d, r.counterparty_id)
                if k in flags and "QUARANTINED_SOURCE_RECORDS" not in flags[k]:
                    flags[k].append("QUARANTINED_SOURCE_RECORDS")
    return pd.Series([";".join(sorted(flags[k])) or "NONE" for k in keys], index=cp.index)


def _business_issues(cp: pd.DataFrame, coll: pd.DataFrame) -> list[dict]:
    """Summarised business/data warnings (one row per counterparty or collateral item and type)."""
    rows: list[dict] = []

    def span(dates) -> str:
        ds = sorted(dates)
        return f"{len(ds)} report date(s) from {ds[0]:%Y-%m-%d} to {ds[-1]:%Y-%m-%d}"

    def add(check, issue_class, cid, dates, msg, key=""):
        rows.append({"severity": WARNING, "issue_class": issue_class, "table": "derived", "check": check,
                     "as_of_date": f"{min(dates):%Y-%m-%d}", "counterparty_id": cid, "record_key": key,
                     "source_row": "", "message": f"{msg}; {span(dates)}"})

    for cid, g in cp[cp["rating_missing"]].groupby("counterparty_id"):
        add("MISSING_RATING", "DATA_QUALITY", cid, g["as_of_date"], "No effective rating; reported as Unrated")
    for status, check in ((MISSING, "MISSING_LIMIT"), (ZERO_EXPOSURE, "ZERO_LIMIT_WITH_EXPOSURE"),
                          (ZERO_NO_EXPOSURE, "ZERO_LIMIT_NO_EXPOSURE"), (BREACH, "LIMIT_BREACH")):
        for cid, g in cp[cp["limit_status"] == status].groupby("counterparty_id"):
            add(check, "BUSINESS", cid, g["as_of_date"], f"Limit status '{status}'")
    texts = {COLL_INELIGIBLE: ("INELIGIBLE_COLLATERAL", "BUSINESS", "Collateral marked ineligible; not recognised"),
             COLL_POSTED: ("POSTED_COLLATERAL", "BUSINESS", "Collateral posted by the reporting entity; not recognised as an offset"),
             COLL_NON_NETTING: ("UNSUPPORTED_COLLATERAL_NON_NETTING_SET", "DATA_QUALITY",
                                "Collateral assigned to a non-netting-permitted set; unsupported and not recognised")}
    for (cls, cid, coll_id), g in coll[coll["collateral_class"].isin(texts)].groupby(
            ["collateral_class", "counterparty_id", "collateral_id"]):
        check, ic, msg = texts[cls]
        add(check, ic, cid, g["as_of_date"], msg, key=coll_id)
    return rows


def _write_failure(out_dir: Path, run_id: str, issues: pd.DataFrame, rejected: pd.DataFrame, meta: dict) -> None:
    _write_csv(issues[ISSUE_COLUMNS].assign(run_id=run_id)[["run_id", *ISSUE_COLUMNS]], out_dir / "data_quality_issues.csv")
    _write_csv(rejected[REJECT_COLUMNS].assign(run_id=run_id)[["run_id", *REJECT_COLUMNS]], out_dir / "rejected_records.csv")
    (out_dir / "run_metadata.json").write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")


CP_COLUMNS = [
    "as_of_date", "counterparty_id", "counterparty_name", "parent_group", "country", "sector", "rating",
    "rating_missing", "netting_set_count", "included_trade_count", "gross_positive_mtm_gbp",
    "signed_net_mtm_gbp", "netting_benefit_gbp", "pre_collateral_exposure_gbp", "recognised_collateral_gbp",
    "collateral_benefit_gbp", "excess_collateral_gbp", "net_current_exposure_gbp", "limit_id",
    "limit_currency", "limit_amount", "limit_fx_rate", "limit_gbp", "utilisation_pct", "headroom_gbp",
    "limit_status", "data_quality_flags",
]


@dataclass
class Computed:
    trades: pd.DataFrame
    collateral: pd.DataFrame
    included: pd.DataFrame
    excluded: pd.DataFrame
    included_gbp: pd.DataFrame
    collateral_classified: pd.DataFrame
    ns: pd.DataFrame
    cp: pd.DataFrame
    top: pd.DataFrame
    breakdowns: dict[str, pd.DataFrame]
    daily: pd.DataFrame
    recon_inputs: ReconInputs


def compute(v, raw_tables: dict[str, pd.DataFrame], dates: list[pd.Timestamp], config: dict[str, Any]) -> Computed:
    """All calculations for validated (non-fatal) inputs over ``dates``."""
    t = v.tables
    date_set = set(dates)
    trades = t["trades"][t["trades"]["as_of_date"].isin(date_set)].reset_index(drop=True)
    coll = t["collateral"][t["collateral"]["as_of_date"].isin(date_set)].reset_index(drop=True)
    fx = t["fx_rates"]
    cfg_l = config["limits"]
    included, excluded = split_trades(trades)
    included_gbp = trades_to_gbp(included, fx)
    coll_c = classify_collateral(coll, t["netting_sets"], fx)
    ns = netting_set_exposure(included_gbp, coll_c, t["netting_sets"], dates)
    cp = counterparty_exposure(ns, t["counterparties"], dates)
    eff_lim = effective_limits_gbp(t["limits"], fx, dates)

    attrs = t["counterparties"][["counterparty_id", "counterparty_name", "parent_group", "country", "sector"]]
    cp = cp.merge(attrs, on="counterparty_id", how="left", validate="many_to_one")
    ratings = select_effective(t["ratings"], dates)
    ratings = ratings[["as_of_date", "counterparty_id", "rating"]] if len(ratings) else \
        pd.DataFrame({"as_of_date": pd.Series(dtype=cp["as_of_date"].dtype), "counterparty_id": pd.Series(dtype=str),
                      "rating": pd.Series(dtype=str)})
    cp = cp.merge(ratings, on=["as_of_date", "counterparty_id"], how="left", validate="one_to_one")
    cp["rating_missing"] = cp["rating"].isna()
    cp["rating"] = cp["rating"].fillna(UNRATED).astype(str)
    cp = apply_limits(cp, eff_lim, float(cfg_l["warning_threshold_pct"]), float(cfg_l["at_limit_tolerance_gbp"]))
    cp["data_quality_flags"] = _dq_flags(cp, coll_c, v.rejected)
    cp = cp[CP_COLUMNS].sort_values(["as_of_date", "counterparty_id"]).reset_index(drop=True)

    n_top = int(config["concentration"]["top_n"])
    top = top_n(cp, n_top)
    bds = {dim: breakdown(cp, dim) for dim in ("rating", "sector", "country", "parent_group")}
    daily = daily_summary(cp, top, included.groupby("as_of_date").size(), excluded.groupby("as_of_date").size())
    ri = ReconInputs(
        raw_tables=raw_tables, control_totals=t["control_totals"], raw_counts=v.raw_counts,
        accepted_counts=v.accepted_counts, quarantined_counts=v.quarantined_counts, rejected=v.rejected,
        accepted_trades=trades, accepted_collateral=coll, included=included, included_gbp=included_gbp,
        excluded=excluded, collateral_classified=coll_c, ns_exposure=ns, cp_exposure=cp, daily=daily,
        breakdowns=bds, top=top, report_dates=dates, top_n=n_top,
    )
    return Computed(trades, coll, included, excluded, included_gbp, coll_c, ns, cp, top, bds, daily, ri)


def run_pipeline(config: dict[str, Any], base_dir: str | Path, as_of: str | None = None,
                 output_dir: str | Path | None = None, make_figures: bool = True) -> PipelineResult:
    base_dir = Path(base_dir)
    raw_dir = resolve(base_dir, config["paths"]["raw_dir"])
    if output_dir is not None:
        out_dir = Path(output_dir)
    elif as_of:
        out_dir = resolve(base_dir, f"results/as_of_{as_of}")
    else:
        out_dir = resolve(base_dir, config["paths"]["results_dir"])
    if not raw_dir.is_dir():
        raise PipelineError(f"Raw data directory not found: {raw_dir}")
    as_of_ts = None
    if as_of:
        try:
            as_of_ts = pd.Timestamp(datetime.strptime(as_of, "%Y-%m-%d"))
        except ValueError as exc:
            raise PipelineError(f"--as-of must be YYYY-MM-DD, got {as_of!r}") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_outputs(out_dir)
    src = read_sources(raw_dir)
    run_id = _run_id(config, src.checksums, as_of)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    gen_meta_path = raw_dir / "generation_metadata.json"
    gen_meta = json.loads(gen_meta_path.read_text(encoding="utf-8")) if gen_meta_path.is_file() else {}
    meta: dict[str, Any] = {
        "run_id": run_id,
        "run_status": None,
        "synthetic_data_notice": "All inputs and results are SYNTHETIC and fictional.",
        "execution_timestamp_utc": started,
        "package_version": __version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.system(),
        "dependency_versions": _package_versions(),
        "config": config,
        "seed": gen_meta.get("seed", config.get("generation", {}).get("seed")),
        "reporting_currency": config["reporting_currency"],
        "requested_as_of": as_of,
        "raw_dir": (str(config["paths"]["raw_dir"]) if not Path(config["paths"]["raw_dir"]).is_absolute()
                    else "<absolute path omitted>"),
        "source_files": src.files,
        "source_checksums_sha256": src.checksums,
    }

    v = validate_sources(src.tables)
    meta["input_row_counts"] = v.raw_counts
    meta["accepted_row_counts"] = v.accepted_counts
    meta["quarantined_row_counts"] = v.quarantined_counts

    dates: list[pd.Timestamp] = []
    issues = v.issues.copy()
    if not v.fatal:
        dates = [pd.Timestamp(d) for d in sorted(v.tables["trades"]["as_of_date"].dropna().unique())]
        if as_of_ts is not None:
            if as_of_ts not in set(dates):
                raise PipelineError(f"--as-of {as_of} is not a date present in the accepted trade snapshots "
                                    f"({dates[0]:%Y-%m-%d} to {dates[-1]:%Y-%m-%d})" if dates else
                                    f"--as-of {as_of}: no accepted trade snapshots")
            dates = [d for d in dates if d <= as_of_ts]
        if not dates:
            issues = pd.concat([issues, pd.DataFrame([{
                "severity": FATAL, "issue_class": "DATA_QUALITY", "table": "trades", "check": "NO_REPORT_DATES",
                "as_of_date": "", "counterparty_id": "", "record_key": "", "source_row": "",
                "message": "No accepted trade snapshots; no valid data to report"}])], ignore_index=True)

    if v.fatal or not dates:
        meta.update({"run_status": FAILED_VALIDATION,
                     "issue_counts": {s: int((issues["severity"] == s).sum()) for s in (FATAL, "QUARANTINE", WARNING)},
                     "date_coverage": None,
                     "message": "Fatal validation errors: exposure, limit and concentration outputs were NOT produced."})
        _write_failure(out_dir, run_id, issues, v.rejected, meta)
        return PipelineResult(FAILED_VALIDATION, EXIT_CODES[FAILED_VALIDATION], out_dir, run_id,
                              messages=issues.loc[issues["severity"] == FATAL, "message"].head(20).tolist())

    try:
        comp = compute(v, src.tables, dates, config)
    except MissingFXError as exc:  # pragma: no cover - guarded by validation
        raise PipelineError(str(exc)) from exc
    cfg_l = config["limits"]
    trades, coll, included, excluded = comp.trades, comp.collateral, comp.included, comp.excluded
    coll_c, ns, cp, top, bds, daily = comp.collateral_classified, comp.ns, comp.cp, comp.top, comp.breakdowns, comp.daily
    ri = comp.recon_inputs
    recon = run_controls(ri, run_id, float(config["reconciliation"]["abs_tolerance"]),
                         float(config["reconciliation"]["rel_tolerance"]))
    rstat = date_status(recon, dates)
    n_fail = int((recon["result"] == FAIL).sum())
    status = FAILED_RECONCILIATION if n_fail else (DEGRADED if v.n_quarantined else SUCCESS)
    daily["reconciliation_status"] = daily["as_of_date"].map(rstat)
    daily["run_quality_status"] = status
    daily.insert(0, "run_id", run_id)

    issues = pd.concat([issues, pd.DataFrame(_business_issues(cp, coll_c), columns=ISSUE_COLUMNS)], ignore_index=True)
    alerts = limit_alerts(cp)

    _write_csv(ns.assign(run_id=run_id), out_dir / "netting_set_exposure.csv")
    _write_csv(cp.assign(run_id=run_id), out_dir / "counterparty_exposure.csv")
    _write_csv(daily, out_dir / "daily_summary.csv")
    _write_csv(alerts.assign(run_id=run_id), out_dir / "limit_alerts.csv")
    _write_csv(top.assign(run_id=run_id), out_dir / "top10_concentrations.csv")
    for dim, fname in (("rating", "exposure_by_rating.csv"), ("sector", "exposure_by_sector.csv"),
                       ("country", "exposure_by_country.csv"), ("parent_group", "exposure_by_parent.csv")):
        frame = bds[dim].assign(run_id=run_id)
        if dim == "parent_group":
            frame["note"] = "Descriptive only; no group limits supplied, no group-limit test performed"
        _write_csv(frame, out_dir / fname)
    _write_csv(issues.assign(run_id=run_id)[["run_id", *ISSUE_COLUMNS]], out_dir / "data_quality_issues.csv")
    _write_csv(v.rejected.assign(run_id=run_id)[["run_id", *REJECT_COLUMNS]], out_dir / "rejected_records.csv")
    _write_csv(recon, out_dir / "reconciliation.csv", full_precision=True)

    latest = dates[-1]
    if make_figures:
        figs = make_all(cp, daily, top, bds, latest, float(cfg_l["warning_threshold_pct"]), out_dir / "figures")
        meta["figures"] = [f"figures/{p.name}" for p in figs.values()]

    meta.update({
        "run_status": status,
        "date_coverage": {"first": f"{dates[0]:%Y-%m-%d}", "latest": f"{latest:%Y-%m-%d}", "n_dates": len(dates)},
        "issue_counts": {s: int((issues["severity"] == s).sum()) for s in (FATAL, "QUARANTINE", WARNING)},
        "fatal_count": int((issues["severity"] == FATAL).sum()),
        "warning_count": int((issues["severity"] == WARNING).sum()),
        "rejected_record_count": int(len(v.rejected)),
        "reconciliation": {"controls": int(len(recon)), "passed": int((recon["result"] == "PASS").sum()),
                           "failed": n_fail},
        "trade_population": {"accepted_snapshots_in_scope": int(len(trades)), "included": int(len(included)),
                             "excluded": int(len(excluded)),
                             "excluded_by_reason": {k: int(n) for k, n in excluded["exclusion_reason"].value_counts().sort_index().items()}},
        "outputs": OUTPUT_CSVS,
    })
    (out_dir / "run_metadata.json").write_text(json.dumps(meta, indent=2, default=str) + "\n", encoding="utf-8")
    return PipelineResult(status, EXIT_CODES[status], out_dir, run_id, latest_date=f"{latest:%Y-%m-%d}")
