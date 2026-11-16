"""Source validation: schema, typing, keys, relationships, effective dates, FX coverage.

Severity classes
----------------
FATAL       The run cannot produce trustworthy figures; publication stops.
            - required file/column missing (unusable schema)
            - duplicate key in any table (ambiguous; never silently de-duplicated)
            - overlapping effective-dated limits or ratings
            - missing required FX rate, or GBP rate != 1
            - netting set owned by an unknown counterparty
            - trade/collateral counterparty disagrees with its netting set's owner
            - malformed source control-total rows
QUARANTINE  An individual record can be rejected unambiguously; the run continues
            as DEGRADED and the rejection reason is preserved.
            - unparseable/blank required field, non-finite number
            - value outside allowed list, negative amount, haircut outside [0, 1],
              non-positive FX or notional, effective_to <= effective_from
            - orphan record (unknown counterparty / netting set)

Business warnings (missing limit, zero limit, missing rating, breach, ineligible or
unsupported collateral) are raised downstream and are not validation failures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .effective_dates import AmbiguousEffectiveRecordError, find_overlaps, select_effective
from .schemas import BOOL_FALSE, BOOL_TRUE, SCHEMAS

FATAL = "FATAL"
QUARANTINE = "QUARANTINE"
WARNING = "WARNING"

ISSUE_COLUMNS = [
    "severity", "issue_class", "table", "check", "as_of_date", "counterparty_id",
    "record_key", "source_row", "message",
]
REJECT_COLUMNS = ["table", "source_row", "record_key", "as_of_date", "counterparty_id", "reasons", "raw_record"]

DATE_FORMAT = "%Y-%m-%d"


@dataclass
class ValidationResult:
    tables: dict[str, pd.DataFrame]
    issues: pd.DataFrame
    rejected: pd.DataFrame
    raw_counts: dict[str, int] = field(default_factory=dict)
    accepted_counts: dict[str, int] = field(default_factory=dict)
    quarantined_counts: dict[str, int] = field(default_factory=dict)

    @property
    def fatal(self) -> bool:
        return bool((self.issues["severity"] == FATAL).any())

    @property
    def n_quarantined(self) -> int:
        return int(sum(self.quarantined_counts.values()))

    def counts_by_severity(self) -> dict[str, int]:
        return {s: int((self.issues["severity"] == s).sum()) for s in (FATAL, QUARANTINE, WARNING)}


class _Collector:
    def __init__(self) -> None:
        self.issues: list[dict] = []
        self.rejects: list[dict] = []

    def issue(self, severity: str, table: str, check: str, message: str, *, as_of_date="",
              counterparty_id="", record_key="", source_row="", issue_class="DATA_QUALITY") -> None:
        self.issues.append({
            "severity": severity, "issue_class": issue_class, "table": table, "check": check,
            "as_of_date": as_of_date, "counterparty_id": counterparty_id, "record_key": record_key,
            "source_row": source_row, "message": message,
        })


def _key_str(row: pd.Series, key: tuple[str, ...]) -> str:
    return "|".join(str(row[k]) for k in key)


def _parse_table(name: str, raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Parse a raw string table. Returns (typed frame, per-row reason strings)."""
    schema = SCHEMAS[name]
    reasons = pd.Series([[] for _ in range(len(raw))], index=raw.index, dtype=object)
    typed = pd.DataFrame(index=raw.index)
    typed["source_row"] = raw["source_row"].astype(int)

    def add(mask: pd.Series, text: str) -> None:
        for i in mask[mask].index:
            reasons.at[i].append(text)

    for col, kind in schema.columns.items():
        s = raw[col].astype(str).str.strip()
        blank = s == ""
        if kind in ("str", "str_opt"):
            typed[col] = s
            if kind == "str":
                add(blank, f"{col}: missing required value")
        elif kind in ("date", "date_opt"):
            parsed = pd.to_datetime(s.where(~blank, None), format=DATE_FORMAT, errors="coerce")
            typed[col] = parsed
            bad = parsed.isna() & ~blank
            add(bad, f"{col}: invalid date (expected YYYY-MM-DD)")
            if kind == "date":
                add(blank, f"{col}: missing required date")
        elif kind in ("float", "float_opt"):
            num = pd.to_numeric(s.where(~blank, None), errors="coerce").astype(float)
            typed[col] = num
            nonfinite = ~np.isfinite(num) & ~blank
            add(nonfinite, f"{col}: non-numeric or non-finite value")
            if kind == "float":
                add(blank, f"{col}: missing required number")
        elif kind == "int":
            num = pd.to_numeric(s.where(~blank, None), errors="coerce").astype(float)
            bad = num.isna() | (num < 0) | (num != np.floor(num))
            typed[col] = num.where(~bad).astype("Int64")
            add(bad, f"{col}: not a non-negative integer")
        elif kind == "bool":
            ok = s.isin([BOOL_TRUE, BOOL_FALSE])
            typed[col] = s == BOOL_TRUE
            add(~ok, f"{col}: expected TRUE or FALSE")
        else:  # pragma: no cover - schema definition error
            raise ValueError(f"Unknown column kind {kind}")

    for col, allowed in schema.allowed.items():
        s = typed[col]
        add(~s.isin(allowed) & (s != ""), f"{col}: value not in allowed set {list(allowed)}")
    for col in schema.nonnegative:
        add(typed[col] < 0, f"{col}: must be non-negative")
    for col in schema.strictly_positive:
        add(typed[col] <= 0, f"{col}: must be strictly positive")
    for col in schema.unit_interval:
        add((typed[col] < 0) | (typed[col] > 1), f"{col}: must be within [0, 1]")
    if "effective_from" in typed and "effective_to" in typed:
        add(typed["effective_to"].notna() & typed["effective_from"].notna()
            & (typed["effective_to"] <= typed["effective_from"]),
            "effective_to must be after effective_from")
    return typed, reasons


def validate_sources(raw: dict[str, pd.DataFrame | None]) -> ValidationResult:
    col = _Collector()
    raw_counts = {k: (0 if v is None else int(len(v))) for k, v in raw.items()}

    # 1. Schema usability ---------------------------------------------------
    for name, schema in SCHEMAS.items():
        df = raw.get(name)
        if df is None:
            col.issue(FATAL, name, "SCHEMA_FILE_MISSING", f"Required source file for '{name}' is missing")
            continue
        missing = [c for c in schema.required_columns if c not in df.columns]
        if missing:
            col.issue(FATAL, name, "SCHEMA_COLUMNS_MISSING", f"Missing required columns: {missing}")
    if col.issues:
        return _finish(col, {}, raw_counts, {}, {})

    # 2. Duplicate keys (on raw stripped text, before anything is dropped) ---
    for name, schema in SCHEMAS.items():
        df = raw[name]
        keys = df[list(schema.key)].apply(lambda s: s.astype(str).str.strip())
        dup = keys.duplicated(keep=False)
        if dup.any():
            for key_vals, grp in df[dup].groupby([keys[k][dup] for k in schema.key], sort=True):
                key_vals = key_vals if isinstance(key_vals, tuple) else (key_vals,)
                payload = grp[list(schema.columns)].apply(lambda s: s.astype(str).str.strip())
                identical = len(payload.drop_duplicates()) == 1
                col.issue(
                    FATAL, name, "DUPLICATE_KEY",
                    f"{len(grp)} rows share key {dict(zip(schema.key, key_vals))} "
                    f"({'identical' if identical else 'conflicting'} content); duplicates are never silently discarded",
                    record_key="|".join(map(str, key_vals)),
                    source_row=";".join(map(str, grp["source_row"])),
                    as_of_date=str(dict(zip(schema.key, key_vals)).get("as_of_date", "")),
                )

    # 3. Row-level parsing -> quarantine ------------------------------------
    typed: dict[str, pd.DataFrame] = {}
    bad_rows: dict[str, pd.Series] = {}
    reasons_all: dict[str, pd.Series] = {}
    for name in SCHEMAS:
        t, reasons = _parse_table(name, raw[name])
        typed[name] = t
        reasons_all[name] = reasons
        bad_rows[name] = reasons.map(len) > 0

    # control-total rows must all be usable
    ct_bad = bad_rows["control_totals"]
    for i in ct_bad[ct_bad].index:
        col.issue(FATAL, "control_totals", "CONTROL_ROW_MALFORMED", "; ".join(reasons_all["control_totals"][i]),
                  source_row=int(raw["control_totals"].at[i, "source_row"]))
    bad_rows["control_totals"] = pd.Series(False, index=ct_bad.index)

    # 4. Referential integrity ---------------------------------------------
    cps_ok = set(typed["counterparties"].loc[~bad_rows["counterparties"], "counterparty_id"])
    ns_frame = typed["netting_sets"].loc[~bad_rows["netting_sets"]]
    unknown_owner = ~ns_frame["counterparty_id"].isin(cps_ok)
    for _, r in ns_frame[unknown_owner].iterrows():
        col.issue(FATAL, "netting_sets", "NETTING_SET_OWNER_UNKNOWN",
                  f"Netting set {r['netting_set_id']} references unknown or rejected counterparty {r['counterparty_id']}",
                  counterparty_id=r["counterparty_id"], record_key=r["netting_set_id"], source_row=int(r["source_row"]))
    ns_owner = dict(zip(ns_frame["netting_set_id"], ns_frame["counterparty_id"]))

    for name in ("trades", "collateral"):
        t = typed[name]
        ok = ~bad_rows[name]
        orphan_cp = ok & ~t["counterparty_id"].isin(cps_ok)
        orphan_ns = ok & ~t["netting_set_id"].isin(ns_owner.keys())
        for i in orphan_cp[orphan_cp].index:
            reasons_all[name][i].append(f"counterparty_id: unknown or rejected counterparty {t.at[i, 'counterparty_id']}")
        for i in orphan_ns[orphan_ns].index:
            reasons_all[name][i].append(f"netting_set_id: unknown or rejected netting set {t.at[i, 'netting_set_id']}")
        bad_rows[name] = bad_rows[name] | orphan_cp | orphan_ns
        ok = ~bad_rows[name]
        owner = t["netting_set_id"].map(ns_owner)
        mismatch = ok & (owner != t["counterparty_id"])
        if mismatch.any():
            m = t[mismatch]
            for (ns, cp), grp in m.groupby(["netting_set_id", "counterparty_id"], sort=True):
                col.issue(FATAL, name, "NETTING_SET_OWNERSHIP_MISMATCH",
                          f"{len(grp)} {name} rows assign netting set {ns} (owner {ns_owner[ns]}) to counterparty {cp}",
                          counterparty_id=cp, record_key=ns,
                          source_row=";".join(map(str, grp["source_row"].head(20))))

    for name in ("ratings", "limits"):
        t = typed[name]
        orphan = ~bad_rows[name] & ~t["counterparty_id"].isin(cps_ok)
        for i in orphan[orphan].index:
            reasons_all[name][i].append(f"counterparty_id: unknown or rejected counterparty {t.at[i, 'counterparty_id']}")
        bad_rows[name] = bad_rows[name] | orphan

    # Record quarantines
    quarantined_counts: dict[str, int] = {}
    accepted: dict[str, pd.DataFrame] = {}
    for name, schema in SCHEMAS.items():
        bad = bad_rows[name]
        quarantined_counts[name] = int(bad.sum())
        r = raw[name]
        for i in bad[bad].index:
            row = r.loc[i]
            key = _key_str(row.map(lambda v: str(v).strip()), schema.key)
            text = "; ".join(reasons_all[name][i])
            as_of = str(row.get("as_of_date", "")).strip()
            cp = str(row.get("counterparty_id", "")).strip()
            col.rejects.append({
                "table": name, "source_row": int(row["source_row"]), "record_key": key,
                "as_of_date": as_of, "counterparty_id": cp, "reasons": text,
                "raw_record": json.dumps({c: str(row[c]) for c in schema.columns}, sort_keys=False),
            })
            col.issue(QUARANTINE, name, "RECORD_QUARANTINED", text, as_of_date=as_of, counterparty_id=cp,
                      record_key=key, source_row=int(row["source_row"]))
        accepted[name] = typed[name].loc[~bad].reset_index(drop=True)

    # 5. Effective-date overlaps --------------------------------------------
    for name, label in (("limits", "LIMIT"), ("ratings", "RATING")):
        ov = find_overlaps(accepted[name])
        for _, r in ov.iterrows():
            a = accepted[name].loc[r["first_index"]]
            b = accepted[name].loc[r["second_index"]]
            col.issue(FATAL, name, f"OVERLAPPING_{label}_INTERVALS",
                      f"Overlapping effective intervals for {r['counterparty_id']}: "
                      f"[{_d(a['effective_from'])}, {_d(a['effective_to']) or 'open'}) and "
                      f"[{_d(b['effective_from'])}, {_d(b['effective_to']) or 'open'})",
                      counterparty_id=r["counterparty_id"],
                      source_row=f"{int(a['source_row'])};{int(b['source_row'])}")

    # 6. FX checks -----------------------------------------------------------
    fx = accepted["fx_rates"]
    gbp_bad = fx[(fx["currency"] == "GBP") & ~np.isclose(fx["gbp_per_unit"], 1.0, rtol=0, atol=1e-12)]
    for _, r in gbp_bad.iterrows():
        col.issue(FATAL, "fx_rates", "GBP_RATE_NOT_ONE", f"GBP rate on {_d(r['as_of_date'])} is {r['gbp_per_unit']}, must be 1",
                  as_of_date=_d(r["as_of_date"]), source_row=int(r["source_row"]))
    available = set(zip(fx["as_of_date"], fx["currency"]))
    required: dict[tuple, str] = {}
    for name in ("trades", "collateral"):
        t = accepted[name]
        for d, c in t[["as_of_date", "currency"]].drop_duplicates().itertuples(index=False):
            required.setdefault((d, c), name)
    report_dates = sorted(accepted["trades"]["as_of_date"].dropna().unique())
    if report_dates and not any(c["check"].startswith("OVERLAPPING_LIMIT") for c in col.issues):
        try:
            eff = select_effective(accepted["limits"], report_dates)
            for d, c in eff[["as_of_date", "limit_currency"]].drop_duplicates().itertuples(index=False):
                required.setdefault((d, c), "limits")
        except AmbiguousEffectiveRecordError:  # pragma: no cover - overlaps already fatal
            pass
    missing = sorted(k for k in required if k not in available)
    for d, c in missing:
        col.issue(FATAL, "fx_rates", "MISSING_REQUIRED_FX",
                  f"No accepted FX rate for {c} on {_d(d)} (required by {required[(d, c)]})", as_of_date=_d(d))

    accepted_counts = {k: int(len(v)) for k, v in accepted.items()}
    return _finish(col, accepted, raw_counts, accepted_counts, quarantined_counts)


def _d(x) -> str:
    return "" if x is None or pd.isna(x) else pd.Timestamp(x).strftime(DATE_FORMAT)


def _finish(col: _Collector, accepted, raw_counts, accepted_counts, quarantined_counts) -> ValidationResult:
    issues = pd.DataFrame(col.issues, columns=ISSUE_COLUMNS)
    rejected = pd.DataFrame(col.rejects, columns=REJECT_COLUMNS)
    return ValidationResult(
        tables=accepted, issues=issues, rejected=rejected, raw_counts=raw_counts,
        accepted_counts=accepted_counts, quarantined_counts=quarantined_counts,
    )
