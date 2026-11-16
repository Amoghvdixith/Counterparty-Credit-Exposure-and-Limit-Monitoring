"""Source-to-report reconciliation controls.

Each control compares an expected value with an actual value obtained by a
different route and passes when

    |actual - expected| <= max(abs_tolerance, rel_tolerance x |expected|)

Counts use a tolerance of zero. Signed MTM is never expected to equal floored
exposure; explicit bridges (netting benefit, collateral benefit) connect them.

Passing arithmetic controls does not prove the source data are true, that the
economics are right, or that netting is legally enforceable. A limit breach is a
business outcome, not a reconciliation failure.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .synthetic import summarise_raw_for_controls

RECON_COLUMNS = [
    "run_id", "as_of_date", "control_id", "control_name", "scope", "expected_value", "actual_value",
    "difference", "tolerance", "result", "explanation",
]
PASS = "PASS"
FAIL = "FAIL"
ALL_DATES = "ALL"


@dataclass
class ReconInputs:
    raw_tables: dict[str, pd.DataFrame]
    control_totals: pd.DataFrame          # typed, accepted control rows
    raw_counts: dict[str, int]
    accepted_counts: dict[str, int]
    quarantined_counts: dict[str, int]
    rejected: pd.DataFrame
    accepted_trades: pd.DataFrame         # in-scope dates
    accepted_collateral: pd.DataFrame     # in-scope dates
    included: pd.DataFrame                # included trades before FX join
    included_gbp: pd.DataFrame
    excluded: pd.DataFrame
    collateral_classified: pd.DataFrame
    ns_exposure: pd.DataFrame
    cp_exposure: pd.DataFrame
    daily: pd.DataFrame
    breakdowns: dict[str, pd.DataFrame]
    top: pd.DataFrame
    report_dates: list
    top_n: int = 10


def _ds(d) -> str:
    return pd.Timestamp(d).strftime("%Y-%m-%d")


class _Rows:
    def __init__(self, run_id: str, abs_tol: float, rel_tol: float) -> None:
        self.run_id, self.abs_tol, self.rel_tol = run_id, abs_tol, rel_tol
        self.rows: list[dict] = []

    def add(self, as_of: str, cid: str, name: str, scope: str, expected, actual, explanation: str,
            *, count: bool = False) -> None:
        exp = float(expected) if expected is not None and not pd.isna(expected) else np.nan
        act = float(actual) if actual is not None and not pd.isna(actual) else np.nan
        tol = 0.0 if count else max(self.abs_tol, self.rel_tol * abs(exp) if np.isfinite(exp) else 0.0)
        diff = act - exp
        ok = np.isfinite(diff) and abs(diff) <= tol
        self.rows.append({
            "run_id": self.run_id, "as_of_date": as_of, "control_id": cid, "control_name": name, "scope": scope,
            "expected_value": exp, "actual_value": act, "difference": diff, "tolerance": tol,
            "result": PASS if ok else FAIL, "explanation": explanation,
        })


def run_controls(ri: ReconInputs, run_id: str, abs_tol: float, rel_tol: float) -> pd.DataFrame:
    r = _Rows(run_id, abs_tol, rel_tol)
    date_strs = [_ds(d) for d in ri.report_dates]
    in_scope = set(date_strs)

    # C01/C02 raw source vs source controls ---------------------------------
    actual = summarise_raw_for_controls(ri.raw_tables)
    ct = ri.control_totals.copy()
    ct["as_of_date"] = ct["as_of_date"].map(lambda d: "" if pd.isna(d) else _ds(d))
    ct = ct.rename(columns={"expected_row_count": "exp_rows", "expected_amount_total": "exp_amount"})
    m = ct.merge(actual, on=["source_table", "as_of_date", "currency"], how="outer", indicator="merge_status",
                 suffixes=("_ctl", ""))
    m = m[(m["as_of_date"] == "") | m["as_of_date"].isin(in_scope)].sort_values(
        ["source_table", "as_of_date", "currency"])
    for row in m.itertuples(index=False):
        d = row.as_of_date or ALL_DATES
        scope = f"{row.source_table}/{row.currency}"
        if row.merge_status == "left_only":
            r.add(d, "C01", "SOURCE_ROW_COUNT", scope, row.exp_rows, 0,
                  "Control row has no matching raw source rows", count=True)
            continue
        if row.merge_status == "right_only":
            r.add(d, "C01", "SOURCE_ROW_COUNT", scope, np.nan, row.row_count,
                  "Raw source rows have no matching source control row", count=True)
            continue
        r.add(d, "C01", "SOURCE_ROW_COUNT", scope, row.exp_rows, row.row_count,
              "Raw source row count vs source-system control count", count=True)
        field = row.amount_field if isinstance(row.amount_field, str) else ""
        if field:
            r.add(d, "C02", "SOURCE_AMOUNT_TOTAL", f"{scope}/{field}", row.exp_amount, row.amount_total,
                  f"Signed local-currency sum of raw {field} vs source control total")

    # C03 raw = accepted + quarantined ---------------------------------------
    for table in sorted(ri.raw_counts):
        r.add(ALL_DATES, "C03", "QUARANTINE_BRIDGE", table, ri.raw_counts[table],
              ri.accepted_counts.get(table, 0) + ri.quarantined_counts.get(table, 0),
              f"Raw rows = accepted ({ri.accepted_counts.get(table, 0)}) + quarantined "
              f"({ri.quarantined_counts.get(table, 0)})", count=True)
    rej = ri.rejected
    for table, acc in (("trades", ri.accepted_trades), ("collateral", ri.accepted_collateral)):
        raw_d = ri.raw_tables[table]["as_of_date"].astype(str).str.strip().value_counts()
        acc_d = acc["as_of_date"].map(_ds).value_counts()
        rej_d = rej.loc[rej["table"] == table, "as_of_date"].astype(str).value_counts()
        for d in date_strs:
            a, q = int(acc_d.get(d, 0)), int(rej_d.get(d, 0))
            r.add(d, "C03", "QUARANTINE_BRIDGE", table, int(raw_d.get(d, 0)), a + q,
                  f"Raw rows on date = accepted ({a}) + quarantined ({q})", count=True)

    # C04 accepted trades = included + excluded --------------------------------
    acc_d = ri.accepted_trades.groupby("as_of_date").size()
    inc_d = ri.included.groupby("as_of_date").size()
    exc = ri.excluded.groupby(["as_of_date", "exclusion_reason"]).size()
    for d in ri.report_dates:
        reasons = exc.loc[d].to_dict() if d in exc.index.get_level_values(0) else {}
        n_exc = int(sum(reasons.values()))
        r.add(_ds(d), "C04", "TRADE_INCLUSION_BRIDGE", "trades", int(acc_d.get(d, 0)), int(inc_d.get(d, 0)) + n_exc,
              f"Accepted snapshots = included ({int(inc_d.get(d, 0))}) + excluded {reasons or '{}'}", count=True)

    # C05 included trades aggregate to netting sets ----------------------------
    ig = ri.included_gbp
    ns = ri.ns_exposure
    ig_net = ig.groupby("as_of_date")["mtm_gbp"].sum()
    ig_pos = ig.groupby("as_of_date")["mtm_gbp"].apply(lambda s: s.clip(lower=0).sum())
    ns_d = ns.groupby("as_of_date").agg(n=("included_trade_count", "sum"), net=("signed_net_mtm_gbp", "sum"),
                                         gross=("gross_positive_mtm_gbp", "sum"))
    for d in ri.report_dates:
        ds = _ds(d)
        r.add(ds, "C05", "TRADE_TO_NETTING_SET_COUNT", "netting_sets", int(inc_d.get(d, 0)), ns_d["n"].get(d, 0),
              "Included trade snapshots vs trades counted in netting-set aggregation (detects join duplication/drops)",
              count=True)
        r.add(ds, "C05", "TRADE_TO_NETTING_SET_SIGNED_MTM", "netting_sets", ig_net.get(d, 0.0), ns_d["net"].get(d, 0.0),
              "Sum of included trade MTM (GBP) vs sum of netting-set signed net MTM")
        r.add(ds, "C05", "TRADE_TO_NETTING_SET_GROSS_POSITIVE", "netting_sets", ig_pos.get(d, 0.0),
              ns_d["gross"].get(d, 0.0), "Sum of positive trade MTM (GBP) vs netting-set gross positive MTM")

    # C06 collateral classified once -------------------------------------------
    cc = ri.collateral_classified
    acc_c = ri.accepted_collateral.groupby("as_of_date").size()
    cls_n = cc.groupby("as_of_date").size()
    cls_u = cc.groupby("as_of_date")["collateral_id"].nunique()
    rec = cc.groupby("as_of_date")["recognised_value_gbp"].sum()
    ns_rec = ns.groupby("as_of_date")["recognised_collateral_gbp"].sum()
    cls_counts = cc.groupby(["as_of_date", "collateral_class"]).size()
    for d in ri.report_dates:
        ds = _ds(d)
        breakdown = cls_counts.loc[d].to_dict() if d in cls_counts.index.get_level_values(0) else {}
        r.add(ds, "C06", "COLLATERAL_CLASSIFICATION_COUNT", "collateral", int(acc_c.get(d, 0)), int(cls_n.get(d, 0)),
              f"Accepted collateral records vs classified records {breakdown or '{}'}", count=True)
        r.add(ds, "C06", "COLLATERAL_SINGLE_ALLOCATION", "collateral", int(cls_n.get(d, 0)), int(cls_u.get(d, 0)),
              "Classified records vs distinct collateral_id (each item allocated to one netting set once)", count=True)
        r.add(ds, "C06", "RECOGNISED_COLLATERAL_TO_NETTING_SET", "collateral", rec.get(d, 0.0), ns_rec.get(d, 0.0),
              "Recognised haircut-adjusted collateral (GBP) vs netting-set recognised collateral")

    # C07/C08 exposure bridges and floor ----------------------------------------
    nsd = ns.groupby("as_of_date")[["gross_positive_mtm_gbp", "netting_benefit_gbp", "pre_collateral_exposure_gbp",
                                     "collateral_benefit_gbp", "net_current_exposure_gbp"]].sum()
    neg = ns.groupby("as_of_date")["net_current_exposure_gbp"].apply(lambda s: int((s < 0).sum()))
    for d in ri.report_dates:
        ds = _ds(d)
        x = nsd.loc[d]
        r.add(ds, "C07", "NETTING_BENEFIT_BRIDGE", "netting_sets", x["pre_collateral_exposure_gbp"],
              x["gross_positive_mtm_gbp"] - x["netting_benefit_gbp"],
              "gross positive MTM - netting benefit = pre-collateral exposure")
        r.add(ds, "C08", "COLLATERAL_BENEFIT_BRIDGE", "netting_sets", x["net_current_exposure_gbp"],
              x["pre_collateral_exposure_gbp"] - x["collateral_benefit_gbp"],
              "pre-collateral exposure - collateral benefit = net current exposure")
        r.add(ds, "C08", "NON_NEGATIVE_EXPOSURE", "netting_sets", 0, neg.get(d, 0),
              "Number of netting sets with negative net current exposure", count=True)

    # C09 netting sets -> counterparties ------------------------------------------
    cp = ri.cp_exposure
    measures = ["gross_positive_mtm_gbp", "netting_benefit_gbp", "pre_collateral_exposure_gbp",
                "collateral_benefit_gbp", "net_current_exposure_gbp"]
    cpd = cp.groupby("as_of_date")[measures].sum()
    for d in ri.report_dates:
        for mname in measures:
            r.add(_ds(d), "C09", "NETTING_SET_TO_COUNTERPARTY", mname, nsd.loc[d, mname], cpd.loc[d, mname],
                  f"Sum over netting sets vs sum over counterparties ({mname})")

    # C10 counterparties -> daily summary -------------------------------------------
    daily = ri.daily.set_index("as_of_date")
    cpn = cp.groupby("as_of_date").size()
    for d in ri.report_dates:
        ds = _ds(d)
        for mname in measures:
            r.add(ds, "C10", "COUNTERPARTY_TO_DAILY_SUMMARY", mname, cpd.loc[d, mname], daily.loc[d, mname],
                  f"Sum over counterparties vs daily summary ({mname})")
        r.add(ds, "C10", "COUNTERPARTY_TO_DAILY_SUMMARY", "counterparty_count", int(cpn.get(d, 0)),
              daily.loc[d, "counterparty_count"], "Counterparty rows vs daily summary count", count=True)

    # C11 breakdowns and concentration ----------------------------------------------
    for dim, frame in sorted(ri.breakdowns.items()):
        bd = frame.groupby("as_of_date")["net_current_exposure_gbp"].sum()
        sh = frame.groupby("as_of_date")["share_of_total_net_exposure_pct"].sum(min_count=1)
        for d in ri.report_dates:
            ds = _ds(d)
            total = cpd.loc[d, "net_current_exposure_gbp"]
            r.add(ds, "C11", "BREAKDOWN_TOTAL", dim, total, bd.get(d, 0.0),
                  f"Sum of exposure by {dim} vs counterparty total")
            if total > 0:
                r.add(ds, "C11", "BREAKDOWN_SHARE_TOTAL", dim, 100.0, sh.get(d, np.nan),
                      f"Shares by {dim} sum to 100% of total net current exposure")
    top_sum = ri.top.groupby("as_of_date")["net_current_exposure_gbp"].sum()
    for d in ri.report_dates:
        ds = _ds(d)
        day = cp[(cp["as_of_date"] == d) & (cp["net_current_exposure_gbp"] > 0)]
        independent = day["net_current_exposure_gbp"].nlargest(ri.top_n).sum()
        r.add(ds, "C11", "TOP_N_CONCENTRATION", f"top{ri.top_n}", independent, top_sum.get(d, 0.0),
              f"Independent nlargest({ri.top_n}) exposure sum vs saved top-{ri.top_n} table")
        r.add(ds, "C11", "TOP_N_TO_DAILY_SUMMARY", f"top{ri.top_n}", top_sum.get(d, 0.0),
              daily.loc[d, "top10_net_exposure_gbp"], "Top-N table sum vs daily summary top-N exposure")

    return pd.DataFrame(r.rows, columns=RECON_COLUMNS)


def date_status(recon: pd.DataFrame, dates) -> pd.Series:
    """PASS for a date when all its controls and all ALL-date controls pass."""
    global_fail = (recon.loc[recon["as_of_date"] == ALL_DATES, "result"] == FAIL).any()
    out = {}
    for d in dates:
        ds = _ds(d)
        fail = global_fail or (recon.loc[recon["as_of_date"] == ds, "result"] == FAIL).any()
        out[pd.Timestamp(d)] = FAIL if fail else PASS
    return pd.Series(out, name="reconciliation_status")
