import pandas as pd
import pytest

from counterparty_mi.effective_dates import AmbiguousEffectiveRecordError, find_overlaps, select_effective

T = pd.Timestamp


def recs(rows):
    return pd.DataFrame(rows, columns=["counterparty_id", "effective_from", "effective_to", "value"]).assign(
        effective_from=lambda d: pd.to_datetime(d["effective_from"]),
        effective_to=lambda d: pd.to_datetime(d["effective_to"]),
    )


def value_on(records, date, cp="X"):
    s = select_effective(records, [T(date)])
    s = s[s["counterparty_id"] == cp]
    return None if s.empty else s["value"].iloc[0]


def test_from_is_inclusive_to_is_exclusive():
    r = recs([("X", "2026-01-05", "2026-01-07", "old"), ("X", "2026-01-07", None, "new")])
    assert value_on(r, "2026-01-04") is None
    assert value_on(r, "2026-01-05") == "old"
    assert value_on(r, "2026-01-06") == "old"
    assert value_on(r, "2026-01-07") == "new"


def test_open_ended_interval_applies_indefinitely():
    r = recs([("X", "2020-01-01", None, "v")])
    assert value_on(r, "2099-12-31") == "v"


def test_gap_between_intervals_gives_no_record():
    r = recs([("X", "2026-01-01", "2026-01-10", "a"), ("X", "2026-01-20", None, "b")])
    assert value_on(r, "2026-01-15") is None


def test_future_record_is_not_used_for_past_date():
    r = recs([("X", "2026-02-01", None, "future")])
    assert value_on(r, "2026-01-15") is None
    assert value_on(r, "2026-02-01") == "future"


def test_ambiguous_effective_records_raise():
    r = recs([("X", "2026-01-01", None, "a"), ("X", "2026-01-05", None, "b")])
    with pytest.raises(AmbiguousEffectiveRecordError):
        select_effective(r, [T("2026-01-06")])


def test_find_overlaps_detects_nested_but_not_adjacent():
    adjacent = recs([("X", "2026-01-01", "2026-02-01", "a"), ("X", "2026-02-01", None, "b")])
    assert find_overlaps(adjacent).empty
    nested = recs([("X", "2026-01-01", None, "a"), ("X", "2026-02-01", "2026-03-01", "b"),
                   ("Y", "2026-01-01", None, "c")])
    ov = find_overlaps(nested)
    assert len(ov) == 1 and ov["counterparty_id"].iloc[0] == "X"
