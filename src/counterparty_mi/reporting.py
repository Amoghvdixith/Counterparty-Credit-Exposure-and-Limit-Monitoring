"""Generate ``management_report.md`` from saved pipeline outputs only.

The report never recalculates exposure and never regenerates source data. If the
saved run status is FAILED_VALIDATION or FAILED_RECONCILIATION an error summary
is written instead of the normal management report.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .plotting import FIGURE_FILES
from .schemas import RATINGS, UNRATED

STATUS_MEANING = {
    "SUCCESS": "All source and arithmetic reconciliation controls passed and no source records were quarantined.",
    "DEGRADED": "The run completed, but some source records were quarantined. Exposure for affected counterparties may be incomplete.",
    "FAILED_VALIDATION": "Fatal validation errors were found. No exposure, limit or concentration figures were produced.",
    "FAILED_RECONCILIATION": "At least one reconciliation control failed. Figures were written for investigation only and are not approved for use.",
}
DISCLAIMER = ("> **SYNTHETIC DATA.** All counterparties, trades, valuations, collateral, ratings, limits and results in this "
              "report are fictional and generated for demonstration. They do not describe any real institution. The "
              "exposure measure is a simplified current-exposure view (no potential future exposure, SA-CCR, regulatory "
              "EAD, CVA, expected credit loss or capital).")


class ReportInputError(FileNotFoundError):
    pass


def _m(x) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{'-' if x < 0 else ''}£{abs(x) / 1e6:,.2f}m"


def _gbp(x) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{'-' if round(x) < 0 else ''}£{abs(x):,.0f}"


def _pct(x, dp: int = 1) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:.{dp}f}%"


def _table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_None._\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines) + "\n"


def _blank(df: pd.DataFrame) -> pd.DataFrame:
    return df.astype(object).where(df.notna(), "")


def _read(results_dir: Path, name: str, required: bool = True) -> pd.DataFrame | None:
    p = results_dir / name
    if not p.is_file():
        if required:
            raise ReportInputError(f"Required output missing: {p}")
        return None
    return pd.read_csv(p, keep_default_na=True, encoding="utf-8")


def _error_report(meta: dict, results_dir: Path, generated: str) -> str:
    status = meta.get("run_status")
    lines = [
        "# Counterparty Credit Exposure and Limit Monitoring: RUN ERROR SUMMARY",
        "",
        DISCLAIMER,
        "",
        f"- **Run status:** `{status}`: {STATUS_MEANING.get(status, '')}",
        f"- **Run ID:** `{meta.get('run_id')}`",
        f"- **Pipeline execution (UTC):** {meta.get('execution_timestamp_utc')}",
        f"- **Report generated (UTC):** {generated}",
        "",
        "**This is not a management report.** Do not use any figures from this run for decision-making.",
        "",
    ]
    issues = _read(results_dir, "data_quality_issues.csv", required=False)
    rejected = _read(results_dir, "rejected_records.csv", required=False)
    if status == "FAILED_VALIDATION" and issues is not None:
        fatal = issues[issues["severity"] == "FATAL"]
        lines += [f"## Fatal validation errors ({len(fatal)})", "",
                  _table(fatal[["table", "check", "as_of_date", "record_key", "source_row", "message"]].pipe(_blank).head(50))]
        if len(fatal) > 50:
            lines.append(f"_{len(fatal) - 50} further fatal errors are listed in `data_quality_issues.csv`._\n")
    if status == "FAILED_RECONCILIATION":
        recon = _read(results_dir, "reconciliation.csv", required=False)
        if recon is not None:
            failed = recon[recon["result"] == "FAIL"]
            lines += [f"## Failed reconciliation controls ({len(failed)} of {len(recon)})", "",
                      _table(failed[["as_of_date", "control_id", "control_name", "scope", "expected_value",
                                     "actual_value", "difference", "explanation"]].pipe(_blank).head(50))]
    if rejected is not None:
        lines += [f"## Quarantined records: {len(rejected)}", ""]
        if len(rejected):
            lines.append(_table(rejected.groupby("table").size().rename("records").reset_index()))
    lines += ["## Next steps", "",
              "1. Correct the source data or source controls listed above.",
              "2. Re-run `python scripts/run_pipeline.py --config configs/baseline.json`.",
              "3. Re-generate this report with `python scripts/generate_report.py --results-dir results/baseline`.", ""]
    return "\n".join(lines)


def build_report(results_dir: str | Path) -> tuple[str, str]:
    """Return (markdown, run_status)."""
    results_dir = Path(results_dir)
    meta_path = results_dir / "run_metadata.json"
    if not meta_path.is_file():
        raise ReportInputError(f"run_metadata.json not found in {results_dir}; run the pipeline first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    status = meta.get("run_status")
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if status in ("FAILED_VALIDATION", "FAILED_RECONCILIATION"):
        return _error_report(meta, results_dir, generated), status

    daily = _read(results_dir, "daily_summary.csv")
    cp = _read(results_dir, "counterparty_exposure.csv")
    top = _read(results_dir, "top10_concentrations.csv")
    by_rating = _read(results_dir, "exposure_by_rating.csv")
    by_sector = _read(results_dir, "exposure_by_sector.csv")
    recon = _read(results_dir, "reconciliation.csv")
    issues = _read(results_dir, "data_quality_issues.csv")
    rejected = _read(results_dir, "rejected_records.csv")

    if daily.empty:
        return "# Counterparty Credit Exposure and Limit Monitoring\n\n" + DISCLAIMER + "\n\n**No valid data: the daily summary is empty.**\n", status

    dates = sorted(daily["as_of_date"].unique())
    latest = dates[-1]
    prev = dates[-2] if len(dates) > 1 else None
    d_last = daily[daily["as_of_date"] == latest].iloc[0]
    d_prev = daily[daily["as_of_date"] == prev].iloc[0] if prev else None
    c_last = cp[cp["as_of_date"] == latest]

    L: list[str] = []
    L += ["# Counterparty Credit Exposure and Limit Monitoring: Management Report", "", DISCLAIMER, ""]
    L += [f"- **Latest as-of date:** {latest}",
          f"- **History covered:** {dates[0]} to {latest} ({len(dates)} business dates; Monday-Friday index, not a holiday calendar)",
          f"- **Reporting currency:** {meta.get('reporting_currency', 'GBP')}",
          f"- **Run ID:** `{meta.get('run_id')}`",
          f"- **Run status:** `{status}`: {STATUS_MEANING.get(status, '')}",
          f"- **Pipeline execution (UTC):** {meta.get('execution_timestamp_utc')}",
          f"- **Report generated (UTC):** {generated}", ""]

    # Population completeness
    n_rej = len(rejected)
    tp = meta.get("trade_population", {})
    L += ["## 1. Population completeness", ""]
    if n_rej == 0:
        L.append("No source records were quarantined. The exposure population is complete relative to the supplied (synthetic) source extract.")
    else:
        L.append(f"**{n_rej} source record(s) were quarantined and excluded.** Exposure for the affected counterparties "
                 "may be understated or incomplete. See `rejected_records.csv`.")
        L.append("")
        L.append(_table(rejected.groupby("table").size().rename("quarantined_records").reset_index()))
    exc = tp.get("excluded_by_reason", {})
    L += ["", f"Across the reporting window, {tp.get('accepted_snapshots_in_scope', 'n/a'):,} accepted trade snapshots were "
          f"split into {tp.get('included', 'n/a'):,} included and {tp.get('excluded', 'n/a'):,} excluded snapshots "
          f"(by documented rule: {', '.join(f'{k}: {v}' for k, v in exc.items()) or 'none'}). Excluded snapshots are "
          "matured/terminated trades or trades on/after their maturity date; they are not data errors.", ""]

    # Key metrics
    L += ["## 2. Key metrics", ""]
    rows = []
    for label, col in (("Gross positive MTM", "gross_positive_mtm_gbp"), ("Netting benefit", "netting_benefit_gbp"),
                       ("Pre-collateral exposure", "pre_collateral_exposure_gbp"),
                       ("Recognised collateral (after haircuts)", "recognised_collateral_gbp"),
                       ("Collateral benefit", "collateral_benefit_gbp"),
                       ("**Net current exposure**", "net_current_exposure_gbp")):
        cur = d_last[col]
        pv = d_prev[col] if d_prev is not None else np.nan
        rows.append({"Measure": label, f"{latest}": _m(cur), f"{prev or 'previous'}": _m(pv),
                     "Change": _m(cur - pv) if d_prev is not None else "n/a"})
    L.append(_table(pd.DataFrame(rows)))
    L += ["", f"- Counterparties in scope: {int(d_last['counterparty_count'])}; with positive net exposure: "
          f"{int(d_last['counterparties_with_exposure'])}; netting sets: {int(d_last['netting_set_count'])}.",
          f"- Included trade snapshots on {latest}: {int(d_last['included_trade_count'])}.",
          f"- Top-10 share of total net current exposure: {_pct(d_last['top10_share_of_total_pct'])} "
          "(denominator: total net current exposure of all counterparties on the date).",
          f"- Day-on-day change in net current exposure: {_m(d_last['dod_change_net_exposure_gbp'])} "
          f"({_pct(d_last['dod_change_net_exposure_pct'], 2)}).", ""]

    # Limits
    L += ["## 3. Limit monitoring (latest date)", ""]
    counts = c_last["limit_status"].value_counts()
    order = ["Breach", "At Limit", "Warning", "Within Limit", "Zero Limit — Exposure", "Zero Limit — No Exposure", "Missing Limit"]
    L.append(_table(pd.DataFrame({"Limit status": order, "Counterparties": [int(counts.get(s, 0)) for s in order]})))
    L.append("")
    show_cols = ["counterparty_id", "counterparty_name", "net_current_exposure_gbp", "limit_currency", "limit_amount",
                 "limit_gbp", "utilisation_pct", "headroom_gbp"]

    def fmt(df):
        out = df[show_cols].copy()
        for c in ("net_current_exposure_gbp", "limit_gbp", "headroom_gbp"):
            out[c] = out[c].map(_gbp)
        out["limit_amount"] = out["limit_amount"].map(lambda x: "n/a" if pd.isna(x) else f"{x:,.0f}")
        out["utilisation_pct"] = out["utilisation_pct"].map(_pct)
        return out.fillna("n/a")

    for title, status_name, none_text in (
            ("Breaches", "Breach", "**No breaches** on the latest date (among counterparties with an effective positive limit)."),
            ("At limit", "At Limit", "No counterparty is exactly at its limit (within tolerance)."),
            ("Warnings (utilisation at or above the warning threshold)", "Warning", "No warnings on the latest date."),
            ("Zero limit with exposure", "Zero Limit — Exposure", "No positive exposure against a zero limit."),
            ("Zero limit, no exposure", "Zero Limit — No Exposure", "None."),
            ("Missing effective limit", "Missing Limit", "Every counterparty has an effective limit on the latest date.")):
        sub = c_last[c_last["limit_status"] == status_name].sort_values("net_current_exposure_gbp", ascending=False)
        L += [f"### {title}", ""]
        L.append(none_text if sub.empty else _table(fmt(sub)))
        L.append("")
    L.append("Utilisation is shown only for positive limits; for missing or zero limits it is unavailable (n/a), not 0%. "
             "Negative headroom means exposure exceeds the limit. A missing limit is a monitoring gap, not evidence of "
             "compliance.")
    L.append("")
    hist = cp[cp["limit_status"] == "Breach"].groupby("counterparty_id")["as_of_date"].agg(["min", "max", "count"])
    L += ["### Breach history over the window", ""]
    if hist.empty:
        L.append("No breaches on any date in the window.")
    else:
        hist = hist.reset_index().rename(columns={"min": "first_breach_date", "max": "last_breach_date", "count": "breach_dates"})
        L.append(_table(hist))
    dd = daily[["as_of_date", "breach_count", "warning_count", "at_limit_count", "missing_limit_count",
                "zero_limit_with_exposure_count"]]
    L += ["", f"Over {len(dates)} dates: breach counterparty-days {int(dd['breach_count'].sum())}, warning counterparty-days "
          f"{int(dd['warning_count'].sum())}, at-limit counterparty-days {int(dd['at_limit_count'].sum())}, missing-limit "
          f"counterparty-days {int(dd['missing_limit_count'].sum())}, zero-limit-with-exposure counterparty-days "
          f"{int(dd['zero_limit_with_exposure_count'].sum())}.", ""]

    # Concentration
    t_last = top[top["as_of_date"] == latest].sort_values("rank")
    L += ["## 4. Top-10 counterparty concentration (latest date)", ""]
    if t_last.empty:
        L.append("No counterparty has positive net current exposure on the latest date.")
    else:
        tt = t_last[["rank", "counterparty_id", "counterparty_name", "rating", "sector", "net_current_exposure_gbp",
                     "share_of_total_net_exposure_pct", "cumulative_share_pct", "limit_status"]].copy()
        tt["net_current_exposure_gbp"] = tt["net_current_exposure_gbp"].map(_gbp)
        tt["share_of_total_net_exposure_pct"] = tt["share_of_total_net_exposure_pct"].map(_pct)
        tt["cumulative_share_pct"] = tt["cumulative_share_pct"].map(_pct)
        L.append(_table(tt))
        L.append("Shares use total net current exposure across all counterparties on the date as the denominator.")
    L.append("")

    # Breakdowns
    def bd(frame, dim, order=None):
        f = frame[frame["as_of_date"] == latest].copy()
        if order:
            f["_o"] = f[dim].map({v: i for i, v in enumerate(order)})
            f = f.sort_values("_o")
        else:
            f = f.sort_values("net_current_exposure_gbp", ascending=False)
        out = f[[dim, "counterparty_count", "counterparties_with_exposure", "net_current_exposure_gbp",
                 "share_of_total_net_exposure_pct"]].copy()
        out["net_current_exposure_gbp"] = out["net_current_exposure_gbp"].map(_gbp)
        out["share_of_total_net_exposure_pct"] = out["share_of_total_net_exposure_pct"].map(_pct)
        return out

    L += ["## 5. Exposure by rating and sector (latest date)", "", "### By rating", "",
          _table(bd(by_rating, "rating", list(RATINGS) + [UNRATED])),
          f"`{UNRATED}` = no effective rating record on the date (flagged as a data-quality warning).", "",
          "### By sector", "", _table(bd(by_sector, "sector")),
          "Country and parent-group breakdowns are in `exposure_by_country.csv` and `exposure_by_parent.csv`. "
          "Parent-group exposure is descriptive only; no group limits were supplied, so no group-limit test is performed.", ""]

    # Movement
    L += ["## 6. Exposure movement", ""]
    first = daily.iloc[0]
    L.append(f"Net current exposure moved from {_m(first['net_current_exposure_gbp'])} on {dates[0]} to "
             f"{_m(d_last['net_current_exposure_gbp'])} on {latest}. Window minimum "
             f"{_m(daily['net_current_exposure_gbp'].min())}, maximum {_m(daily['net_current_exposure_gbp'].max())}.")
    L.append("")
    if prev:
        a = cp[cp["as_of_date"] == prev].set_index("counterparty_id")["net_current_exposure_gbp"]
        b = c_last.set_index("counterparty_id")["net_current_exposure_gbp"]
        mv = (b - a.reindex(b.index).fillna(0.0)).rename("change_gbp").reset_index()
        mv = mv[mv["change_gbp"].abs() > 0.005]
        up = mv.sort_values("change_gbp", ascending=False).head(5)
        down = mv.sort_values("change_gbp").head(5)
        for title, frame in ((f"Largest increases, {prev} to {latest}", up[up["change_gbp"] > 0]),
                             (f"Largest decreases, {prev} to {latest}", down[down["change_gbp"] < 0])):
            L += [f"### {title}", ""]
            f2 = frame.copy()
            f2["change_gbp"] = f2["change_gbp"].map(_gbp)
            L.append(_table(f2))
            L.append("")

    # Reconciliation
    L += ["## 7. Reconciliation", ""]
    rs = recon.groupby(["control_id", "control_name"])["result"].agg(
        controls="size", passed=lambda s: int((s == "PASS").sum()), failed=lambda s: int((s == "FAIL").sum())).reset_index()
    L.append(f"{int((recon['result'] == 'PASS').sum())} of {len(recon)} controls passed; "
             f"{int((recon['result'] == 'FAIL').sum())} failed.")
    L.append("")
    L.append(_table(rs))
    L.append("Passing arithmetic controls shows that the reported totals are internally consistent with the source "
             "extract and its control totals. It does not prove that the source data are true, that valuations are "
             "economically correct, or that netting is legally enforceable. Limit breaches are business outcomes, not "
             "reconciliation failures.")
    L.append("")

    # Data quality
    L += ["## 8. Data-quality and business warnings", ""]
    if issues.empty:
        L.append("No issues recorded.")
    else:
        iq = issues.groupby(["severity", "issue_class", "check"]).size().rename("rows").reset_index()
        L.append(_table(iq))
        L.append("Each warning row summarises one counterparty or collateral item over the dates affected; see "
                 "`data_quality_issues.csv`.")
    L.append("")

    # Charts
    L += ["## 9. Charts", ""]
    titles = {
        "exposure_trend": "Daily exposure trend", "top10_counterparties": "Top-10 counterparties (latest date)",
        "limit_utilisation": "Limit utilisation (positive limits only)", "exposure_by_rating": "Exposure by rating",
        "exposure_by_sector": "Exposure by sector", "collateral_comparison": "Pre-collateral vs net exposure",
        "limit_status_counts": "Counts by limit status",
    }
    for key, fname in FIGURE_FILES.items():
        if (results_dir / "figures" / fname).is_file():
            L += [f"### {titles[key]}", "", f"![{titles[key]}](figures/{fname})", ""]
        else:
            L += [f"- {titles[key]}: figure not found (`figures/{fname}`).", ""]

    # Assumptions
    L += ["## 10. Assumptions and limitations", "",
          "- All data are synthetic; MTM values are supplied inputs, not priced.",
          "- Current exposure only: no potential future exposure, SA-CCR, regulatory EAD, CVA, expected credit loss or capital.",
          "- `netting_permitted` is a synthetic modelling flag, not a legal opinion on enforceability.",
          "- Collateral is recognised only if received, eligible and held against a netting-permitted set, at market value x FX x (1 - haircut). No thresholds, minimum transfer amounts, margin period of risk or wrong-way risk.",
          "- Surplus collateral in one netting set is not transferred to another set or counterparty.",
          "- Trades are included only if ACTIVE with maturity date strictly after the as-of date.",
          "- FX: same-date `gbp_per_unit` rates; GBP = 1.",
          "- Limits and ratings are selected with effective_from <= as-of date < effective_to; no future records are used.",
          "- Business dates are a Monday-Friday index, not an exchange holiday calendar.",
          "- This is a repeatable pipeline run on demand, not a scheduled production service.", ""]
    return "\n".join(L), status


def write_report(results_dir: str | Path) -> tuple[Path, str]:
    md, status = build_report(results_dir)
    path = Path(results_dir) / "management_report.md"
    path.write_text(md, encoding="utf-8", newline="\n")
    return path, status
