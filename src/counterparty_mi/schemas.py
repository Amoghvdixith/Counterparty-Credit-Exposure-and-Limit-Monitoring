"""Source-table schemas, allowed values and key definitions.

Each table schema lists its columns with a logical type. Logical types are:

- ``str``      non-empty text
- ``date``     ISO date ``YYYY-MM-DD``
- ``date_opt`` ISO date or blank (blank = open-ended)
- ``float``    finite decimal number
- ``float_opt`` finite decimal number or blank
- ``int``      non-negative integer
- ``bool``     ``TRUE`` / ``FALSE``

All raw CSV values are read as strings and parsed in :mod:`counterparty_mi.validation`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CURRENCIES = ("GBP", "USD", "EUR")
TRADE_STATUSES = ("ACTIVE", "MATURED", "TERMINATED")
PRODUCT_TYPES = ("IRS", "CCS", "FX_FORWARD", "FX_SWAP", "EQUITY_SWAP")
COLLATERAL_DIRECTIONS = ("RECEIVED", "POSTED")
RATINGS = ("AAA", "AA", "A", "BBB", "BB", "B", "CCC")
RATING_ORDER = {r: i for i, r in enumerate(RATINGS)}
UNRATED = "Unrated"
BOOL_TRUE = "TRUE"
BOOL_FALSE = "FALSE"

SOURCE_TABLES_WITH_CONTROLS = (
    "counterparties",
    "netting_sets",
    "trades",
    "collateral",
    "ratings",
    "limits",
    "fx_rates",
)


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: dict[str, str]
    key: tuple[str, ...]
    allowed: dict[str, tuple[str, ...]] = field(default_factory=dict)
    nonnegative: tuple[str, ...] = ()
    strictly_positive: tuple[str, ...] = ()
    unit_interval: tuple[str, ...] = ()

    @property
    def required_columns(self) -> list[str]:
        return list(self.columns)


SCHEMAS: dict[str, TableSchema] = {
    "counterparties": TableSchema(
        name="counterparties",
        columns={
            "counterparty_id": "str",
            "counterparty_name": "str",
            "parent_group": "str",
            "country": "str",
            "sector": "str",
        },
        key=("counterparty_id",),
    ),
    "netting_sets": TableSchema(
        name="netting_sets",
        columns={
            "netting_set_id": "str",
            "counterparty_id": "str",
            "netting_permitted": "bool",
        },
        key=("netting_set_id",),
    ),
    "trades": TableSchema(
        name="trades",
        columns={
            "as_of_date": "date",
            "trade_id": "str",
            "counterparty_id": "str",
            "netting_set_id": "str",
            "product_type": "str",
            "currency": "str",
            "notional": "float",
            "mtm": "float",
            "maturity_date": "date",
            "trade_status": "str",
        },
        key=("as_of_date", "trade_id"),
        allowed={
            "product_type": PRODUCT_TYPES,
            "currency": CURRENCIES,
            "trade_status": TRADE_STATUSES,
        },
        strictly_positive=("notional",),
    ),
    "collateral": TableSchema(
        name="collateral",
        columns={
            "as_of_date": "date",
            "collateral_id": "str",
            "counterparty_id": "str",
            "netting_set_id": "str",
            "currency": "str",
            "market_value": "float",
            "haircut": "float",
            "eligible": "bool",
            "direction": "str",
        },
        key=("as_of_date", "collateral_id"),
        allowed={"currency": CURRENCIES, "direction": COLLATERAL_DIRECTIONS},
        nonnegative=("market_value",),
        unit_interval=("haircut",),
    ),
    "ratings": TableSchema(
        name="ratings",
        columns={
            "counterparty_id": "str",
            "effective_from": "date",
            "effective_to": "date_opt",
            "rating": "str",
        },
        key=("counterparty_id", "effective_from"),
        allowed={"rating": RATINGS},
    ),
    "limits": TableSchema(
        name="limits",
        columns={
            "limit_id": "str",
            "counterparty_id": "str",
            "effective_from": "date",
            "effective_to": "date_opt",
            "limit_currency": "str",
            "limit_amount": "float",
        },
        key=("limit_id",),
        allowed={"limit_currency": CURRENCIES},
        nonnegative=("limit_amount",),
    ),
    "fx_rates": TableSchema(
        name="fx_rates",
        columns={
            "as_of_date": "date",
            "currency": "str",
            "gbp_per_unit": "float",
        },
        key=("as_of_date", "currency"),
        allowed={"currency": CURRENCIES},
        strictly_positive=("gbp_per_unit",),
    ),
    "control_totals": TableSchema(
        name="control_totals",
        columns={
            "as_of_date": "date_opt",
            "source_table": "str",
            "currency": "str",
            "expected_row_count": "int",
            "expected_amount_total": "float_opt",
            "amount_field": "str_opt",
        },
        key=("as_of_date", "source_table", "currency"),
        allowed={"source_table": SOURCE_TABLES_WITH_CONTROLS},
    ),
}

#: Monetary amount controlled for each source table (``None`` = row count only).
CONTROL_AMOUNT_FIELDS: dict[str, str | None] = {
    "counterparties": None,
    "netting_sets": None,
    "trades": "mtm",
    "collateral": "market_value",
    "ratings": None,
    "limits": "limit_amount",
    "fx_rates": None,
}

#: Date column used to split controls by date (``None`` = static table, one control).
CONTROL_DATE_FIELDS: dict[str, str | None] = {
    "counterparties": None,
    "netting_sets": None,
    "trades": "as_of_date",
    "collateral": "as_of_date",
    "ratings": None,
    "limits": None,
    "fx_rates": "as_of_date",
}

#: Currency column used to split controls by currency (``None`` = "ALL").
CONTROL_CURRENCY_FIELDS: dict[str, str | None] = {
    "counterparties": None,
    "netting_sets": None,
    "trades": "currency",
    "collateral": "currency",
    "ratings": None,
    "limits": "limit_currency",
    "fx_rates": None,
}

ALL_CURRENCIES_LABEL = "ALL"

SOURCE_FILES = {name: f"{name}.csv" for name in SCHEMAS}
