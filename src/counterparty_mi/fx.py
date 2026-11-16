"""FX conversion to the GBP reporting currency.

GBP amount = local-currency amount x gbp_per_unit, using the rate for the same
as-of date. Missing rates raise :class:`MissingFXError`; they are never filled.
"""

from __future__ import annotations

import pandas as pd


class MissingFXError(ValueError):
    """A required (date, currency) FX rate is not available."""


def to_gbp(
    df: pd.DataFrame,
    amount_col: str,
    fx: pd.DataFrame,
    *,
    currency_col: str = "currency",
    date_col: str = "as_of_date",
    out_col: str | None = None,
    rate_col: str = "gbp_per_unit",
) -> pd.DataFrame:
    """Return ``df`` with ``rate_col`` and ``out_col`` (= amount x rate) added.

    The join is validated many-to-one so FX rows can never multiply records.
    """
    out_col = out_col or f"{amount_col}_gbp"
    rates = fx[["as_of_date", "currency", "gbp_per_unit"]].rename(
        columns={"as_of_date": date_col, "currency": currency_col, "gbp_per_unit": rate_col}
    )
    merged = df.merge(rates, on=[date_col, currency_col], how="left", validate="many_to_one")
    if len(merged) != len(df):  # pragma: no cover - guarded by validate=
        raise RuntimeError("FX join changed the row count")
    missing = merged[rate_col].isna()
    if missing.any():
        pairs = merged.loc[missing, [date_col, currency_col]].drop_duplicates().head(10)
        raise MissingFXError(
            "Missing FX rate(s) for: "
            + ", ".join(f"{pd.Timestamp(d).date()} {c}" for d, c in pairs.itertuples(index=False))
        )
    merged[out_col] = merged[amount_col] * merged[rate_col]
    return merged
