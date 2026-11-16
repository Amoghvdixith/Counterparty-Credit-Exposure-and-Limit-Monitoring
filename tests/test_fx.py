import pandas as pd
import pytest

from counterparty_mi.fx import MissingFXError, to_gbp

D = pd.Timestamp("2026-01-05")
FX = pd.DataFrame({"as_of_date": [D, D, D], "currency": ["GBP", "USD", "EUR"], "gbp_per_unit": [1.0, 0.8, 0.9]})


def test_conversion_multiplies_by_gbp_per_unit():
    df = pd.DataFrame({"as_of_date": [D, D, D], "currency": ["GBP", "USD", "EUR"], "mtm": [100.0, 100.0, -50.0]})
    out = to_gbp(df, "mtm", FX)
    assert out["mtm_gbp"].tolist() == pytest.approx([100.0, 80.0, -45.0])


def test_missing_rate_raises_rather_than_filling():
    df = pd.DataFrame({"as_of_date": [D], "currency": ["JPY"], "mtm": [1.0]})
    with pytest.raises(MissingFXError):
        to_gbp(df, "mtm", FX)


def test_wrong_date_rate_is_not_used():
    df = pd.DataFrame({"as_of_date": [pd.Timestamp("2026-01-06")], "currency": ["USD"], "mtm": [1.0]})
    with pytest.raises(MissingFXError):
        to_gbp(df, "mtm", FX)


def test_duplicate_fx_rows_cannot_multiply_records():
    dup = pd.concat([FX, FX.iloc[[1]]], ignore_index=True)
    df = pd.DataFrame({"as_of_date": [D], "currency": ["USD"], "mtm": [1.0]})
    with pytest.raises(pd.errors.MergeError):
        to_gbp(df, "mtm", dup)
