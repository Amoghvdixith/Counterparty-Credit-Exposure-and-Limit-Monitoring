import pandas as pd

from conftest import D1, with_source_rows
from counterparty_mi.validation import FATAL, QUARANTINE, validate_sources


def _checks(v, severity):
    return set(v.issues.loc[v.issues["severity"] == severity, "check"])


def test_clean_fixture_has_no_issues(raw):
    v = validate_sources(with_source_rows(raw))
    assert not v.fatal
    assert v.issues.empty
    assert v.accepted_counts["trades"] == 14
    assert v.tables["trades"]["mtm"].dtype == float


def test_missing_required_column_is_fatal(raw):
    raw["trades"] = raw["trades"].drop(columns=["mtm"])
    v = validate_sources(with_source_rows(raw))
    assert v.fatal and "SCHEMA_COLUMNS_MISSING" in _checks(v, FATAL)


def test_missing_file_is_fatal(raw):
    r = with_source_rows(raw)
    r["fx_rates"] = None
    v = validate_sources(r)
    assert "SCHEMA_FILE_MISSING" in _checks(v, FATAL)


def test_identical_duplicate_trade_key_is_fatal_not_dropped(raw):
    raw["trades"] = pd.concat([raw["trades"], raw["trades"].iloc[[0]]], ignore_index=True)
    v = validate_sources(with_source_rows(raw))
    dup = v.issues[v.issues["check"] == "DUPLICATE_KEY"]
    assert v.fatal and len(dup) == 1
    assert "identical" in dup["message"].iloc[0]


def test_conflicting_duplicate_is_fatal(raw):
    extra = raw["trades"].iloc[[0]].copy()
    extra["mtm"] = "999.00"
    raw["trades"] = pd.concat([raw["trades"], extra], ignore_index=True)
    v = validate_sources(with_source_rows(raw))
    assert "conflicting" in v.issues.loc[v.issues["check"] == "DUPLICATE_KEY", "message"].iloc[0]


def test_netting_set_ownership_mismatch_is_fatal(raw):
    t = raw["trades"]
    t.loc[t["trade_id"] == "T6", "counterparty_id"] = "CPA"  # NS-B1 belongs to CPB
    v = validate_sources(with_source_rows(raw))
    assert "NETTING_SET_OWNERSHIP_MISMATCH" in _checks(v, FATAL)


def test_netting_set_with_unknown_owner_is_fatal(raw):
    raw["netting_sets"].loc[3, "counterparty_id"] = "CPZ"
    v = validate_sources(with_source_rows(raw))
    assert "NETTING_SET_OWNER_UNKNOWN" in _checks(v, FATAL)


def test_orphan_trade_is_quarantined(raw):
    t = raw["trades"]
    t.loc[0, "netting_set_id"] = "NS-UNKNOWN"
    v = validate_sources(with_source_rows(raw))
    assert not v.fatal
    assert v.quarantined_counts["trades"] == 1
    assert "unknown or rejected netting set" in v.rejected["reasons"].iloc[0]


def test_malformed_values_are_quarantined_with_reasons(raw):
    t, c = raw["trades"], raw["collateral"]
    t.loc[0, "mtm"] = "abc"
    t.loc[1, "mtm"] = "inf"
    t.loc[2, "trade_status"] = "LIVE"
    t.loc[3, "maturity_date"] = "2030-13-45"
    c.loc[0, "haircut"] = "1.5"
    c.loc[1, "market_value"] = "-5"
    c.loc[2, "eligible"] = "YES"
    v = validate_sources(with_source_rows(raw))
    assert not v.fatal
    assert v.quarantined_counts == {**v.quarantined_counts, "trades": 4, "collateral": 3}
    reasons = " ".join(v.rejected["reasons"])
    for text in ("non-numeric or non-finite", "trade_status", "invalid date", "within [0, 1]", "non-negative", "TRUE or FALSE"):
        assert text in reasons
    # raw = accepted + quarantined
    assert v.raw_counts["trades"] == v.accepted_counts["trades"] + v.quarantined_counts["trades"]


def test_missing_mtm_is_not_replaced_with_zero(raw):
    raw["trades"].loc[0, "mtm"] = ""
    v = validate_sources(with_source_rows(raw))
    assert v.quarantined_counts["trades"] == 1
    assert "missing required number" in v.rejected["reasons"].iloc[0]
    assert not ((v.tables["trades"]["trade_id"] == "T1") & (v.tables["trades"]["as_of_date"] == pd.Timestamp(D1))).any()


def test_missing_fx_is_fatal(raw):
    fx = raw["fx_rates"]
    raw["fx_rates"] = fx[~((fx["as_of_date"] == D1) & (fx["currency"] == "USD"))]
    v = validate_sources(with_source_rows(raw))
    assert "MISSING_REQUIRED_FX" in _checks(v, FATAL)


def test_missing_fx_for_limit_currency_is_fatal(raw):
    raw["limits"].loc[0, "limit_currency"] = "USD"
    raw["trades"] = raw["trades"][raw["trades"]["currency"] != "USD"]
    fx = raw["fx_rates"]
    raw["fx_rates"] = fx[fx["currency"] != "USD"]
    v = validate_sources(with_source_rows(raw))
    msgs = v.issues.loc[v.issues["check"] == "MISSING_REQUIRED_FX", "message"]
    assert any("limits" in m for m in msgs)


def test_negative_fx_is_quarantined_then_missing_fx_fatal(raw):
    raw["fx_rates"].loc[1, "gbp_per_unit"] = "-0.8"
    v = validate_sources(with_source_rows(raw))
    assert v.quarantined_counts["fx_rates"] == 1
    assert "MISSING_REQUIRED_FX" in _checks(v, FATAL)


def test_gbp_rate_must_equal_one(raw):
    raw["fx_rates"].loc[0, "gbp_per_unit"] = "1.01"
    v = validate_sources(with_source_rows(raw))
    assert "GBP_RATE_NOT_ONE" in _checks(v, FATAL)


def test_overlapping_limits_are_fatal(raw):
    extra = pd.DataFrame([{"limit_id": "L9", "counterparty_id": "CPA", "effective_from": "2025-06-01",
                           "effective_to": "2026-06-01", "limit_currency": "GBP", "limit_amount": "50.00"}])
    raw["limits"] = pd.concat([raw["limits"], extra], ignore_index=True)
    v = validate_sources(with_source_rows(raw))
    assert "OVERLAPPING_LIMIT_INTERVALS" in _checks(v, FATAL)


def test_overlapping_ratings_are_fatal(raw):
    raw["ratings"].loc[1, "effective_to"] = "2026-02-01"  # overlaps the BB record from 2026-01-06
    v = validate_sources(with_source_rows(raw))
    assert "OVERLAPPING_RATING_INTERVALS" in _checks(v, FATAL)


def test_negative_limit_and_inverted_interval_are_quarantined(raw):
    raw["limits"].loc[0, "limit_amount"] = "-1"
    raw["limits"].loc[1, "effective_to"] = "2024-01-01"
    v = validate_sources(with_source_rows(raw))
    assert v.quarantined_counts["limits"] == 2
    assert QUARANTINE in set(v.issues["severity"])
