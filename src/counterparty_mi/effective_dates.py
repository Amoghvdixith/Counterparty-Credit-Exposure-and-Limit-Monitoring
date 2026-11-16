"""Effective-dated record selection.

Rule: a record is effective on report date ``d`` when

    effective_from <= d < effective_to

A missing (NaT) ``effective_to`` means open-ended. Records whose
``effective_from`` lies after ``d`` are never used for ``d`` (no future
information).
"""

from __future__ import annotations

import pandas as pd


class AmbiguousEffectiveRecordError(ValueError):
    """More than one record is effective for the same key and date."""


def is_effective(effective_from: pd.Series, effective_to: pd.Series, as_of: pd.Series | pd.Timestamp) -> pd.Series:
    end_ok = effective_to.isna() | (as_of < effective_to)
    return (effective_from <= as_of) & end_ok


def find_overlaps(records: pd.DataFrame, key: str = "counterparty_id",
                  start: str = "effective_from", end: str = "effective_to") -> pd.DataFrame:
    """Return pairs of records for the same key whose intervals overlap.

    Intervals are half-open ``[start, end)``; NaT end is +infinity. Two intervals
    ``[a1, b1)`` and ``[a2, b2)`` with ``a1 <= a2`` overlap when ``a2 < b1``.
    All pairs are checked (not only neighbours) so nested intervals are found.
    """
    cols = [key, "first_index", "second_index", "first_from", "first_to", "second_from", "second_to"]
    out = []
    for k, grp in records.sort_values([key, start]).groupby(key, sort=True):
        g = grp.reset_index()
        for i in range(len(g)):
            b1 = g.loc[i, end]
            for j in range(i + 1, len(g)):
                a2 = g.loc[j, start]
                if pd.isna(b1) or a2 < b1:
                    out.append({
                        key: k,
                        "first_index": g.loc[i, "index"],
                        "second_index": g.loc[j, "index"],
                        "first_from": g.loc[i, start],
                        "first_to": b1,
                        "second_from": a2,
                        "second_to": g.loc[j, end],
                    })
    return pd.DataFrame(out, columns=cols)


def select_effective(records: pd.DataFrame, dates, key: str = "counterparty_id",
                     start: str = "effective_from", end: str = "effective_to") -> pd.DataFrame:
    """Return one row per (as_of_date, key) for every effective record.

    Keys with no effective record on a date are simply absent (callers decide
    how to report "missing"). Raises :class:`AmbiguousEffectiveRecordError` if
    any key has more than one effective record on the same date.
    """
    date_frame = pd.DataFrame({"as_of_date": pd.to_datetime(pd.Index(dates)).unique().sort_values()})
    if records.empty:
        return pd.DataFrame(columns=["as_of_date", *records.columns])
    cross = date_frame.merge(records, how="cross")
    mask = is_effective(cross[start], cross[end], cross["as_of_date"])
    selected = cross.loc[mask].reset_index(drop=True)
    dup = selected.duplicated(["as_of_date", key], keep=False)
    if dup.any():
        sample = selected.loc[dup, ["as_of_date", key]].drop_duplicates().head(5).to_dict("records")
        raise AmbiguousEffectiveRecordError(f"Multiple effective records for: {sample}")
    return selected.sort_values(["as_of_date", key]).reset_index(drop=True)
