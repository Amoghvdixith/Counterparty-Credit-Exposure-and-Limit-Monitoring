import numpy as np
import pandas as pd
import pytest

from counterparty_mi import limits as L

D = pd.Timestamp("2026-01-05")


def status(e, lim, w=80.0, tol=0.01):
    return L.classify_status([e], [lim], w, tol)[0]


def test_within_limit():
    assert status(50, 100) == L.WITHIN


def test_warning_boundary_is_inclusive():
    assert status(79.99, 100) == L.WITHIN
    assert status(80.0, 100) == L.WARNING
    assert status(99.0, 100) == L.WARNING


def test_at_limit_tolerance():
    assert status(100.0, 100) == L.AT_LIMIT
    assert status(100.009, 100) == L.AT_LIMIT
    assert status(99.991, 100) == L.AT_LIMIT
    assert status(100.02, 100) == L.BREACH


def test_breach():
    assert status(150, 100) == L.BREACH


def test_missing_and_zero_limits():
    assert status(10, np.nan) == L.MISSING
    assert status(0, np.nan) == L.MISSING
    assert status(5, 0.0) == L.ZERO_EXPOSURE
    assert status(0, 0.0) == L.ZERO_NO_EXPOSURE


def test_utilisation_unavailable_for_missing_and_zero_limits():
    u = L.utilisation_pct([10, 10, 10], [np.nan, 0.0, 40.0])
    assert np.isnan(u[0]) and np.isnan(u[1])
    assert u[2] == pytest.approx(25.0)
    assert np.all(np.isfinite(u[2:]))


def test_statuses_are_mutually_exclusive_over_grid():
    es = np.linspace(0, 200, 81)
    for lim in (np.nan, 0.0, 100.0):
        out = L.classify_status(es, np.full_like(es, lim))
        assert all(s in L.STATUSES for s in out)


def _cp(rows):
    return pd.DataFrame(rows, columns=["as_of_date", "counterparty_id", "net_current_exposure_gbp"])


def test_limit_fx_conversion_and_headroom():
    limits = pd.DataFrame({"limit_id": ["L1"], "counterparty_id": ["X"], "effective_from": [pd.Timestamp("2025-01-01")],
                           "effective_to": [pd.NaT], "limit_currency": ["EUR"], "limit_amount": [200.0]})
    fx = pd.DataFrame({"as_of_date": [D], "currency": ["EUR"], "gbp_per_unit": [0.9]})
    eff = L.effective_limits_gbp(limits, fx, [D])
    assert eff["limit_gbp"].iloc[0] == pytest.approx(180.0)
    out = L.apply_limits(_cp([(D, "X", 190.0)]), eff, 80.0, 0.01)
    assert out["headroom_gbp"].iloc[0] == pytest.approx(-10.0)  # negative = exposure above limit
    assert out["utilisation_pct"].iloc[0] == pytest.approx(105.5555556)
    assert out["limit_status"].iloc[0] == L.BREACH


def test_missing_limit_has_blank_utilisation_and_headroom():
    eff = pd.DataFrame(columns=["as_of_date", "counterparty_id", "limit_id", "limit_currency", "limit_amount",
                                "limit_fx_rate", "limit_gbp"]).astype({"as_of_date": "datetime64[ns]", "limit_gbp": float})
    out = L.apply_limits(_cp([(D, "X", 5.0)]), eff, 80.0, 0.01)
    assert out["limit_status"].iloc[0] == L.MISSING
    assert np.isnan(out["utilisation_pct"].iloc[0]) and np.isnan(out["headroom_gbp"].iloc[0])
