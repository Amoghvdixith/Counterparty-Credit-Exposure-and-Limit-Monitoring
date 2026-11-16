# Data dictionary

All source data are **synthetic**. Every source file is UTF-8 CSV with a header row.
Values are read as text and then parsed by `counterparty_mi.validation`.

## Conventions

| Item | Rule |
|---|---|
| Dates | ISO `YYYY-MM-DD`. Any other format is invalid. |
| Booleans | `TRUE` / `FALSE` (case-sensitive) |
| Numbers | Decimal text. `inf`, `nan` and non-numeric text are invalid. |
| Blank values | Allowed only where marked *optional*. A blank required value causes the record to be quarantined; it is **never** replaced with zero. |
| Monetary units | Local currency of the record (`currency` or `limit_currency`). GBP figures are derived only in outputs. |
| FX | `gbp_per_unit`: GBP per 1 unit of the currency. GBP amount = local amount × `gbp_per_unit`. The GBP rate must equal 1. |
| Effective dating | Record applies on date *d* when `effective_from <= d < effective_to`. A blank `effective_to` means open-ended. `effective_to` must be later than `effective_from`. |

## Relationships

```
counterparties 1 ──< netting_sets 1 ──< trades (per as_of_date)
       │                    └────────< collateral (per as_of_date)
       ├──< ratings (effective-dated, non-overlapping)
       └──< limits  (effective-dated, non-overlapping)
fx_rates: one rate per (as_of_date, currency)
control_totals: one row per (as_of_date, source_table, currency)
```

- A netting set belongs to exactly one counterparty. Each trade's and collateral record's
  `counterparty_id` must equal the owner of its netting set; a mismatch is FATAL.
- Every join in the pipeline uses pandas `merge(validate="one_to_one" | "many_to_one")`, so
  a many-to-many join raises an error instead of silently multiplying financial rows.

## Tables

### counterparties.csv. Key: `counterparty_id`

| Column | Type | Allowed / notes |
|---|---|---|
| counterparty_id | text | Unique, e.g. `CP001` |
| counterparty_name | text | Synthetic name |
| parent_group | text | Synthetic parent group (descriptive only) |
| country | text | ISO-style 2-letter code (GB, US, DE, FR, NL, CH, JP, CA, IE in the baseline) |
| sector | text | Banks, Insurance, Asset Managers, Hedge Funds, Pension Funds, Corporates - Energy, Corporates - Industrials, Sovereign and Public Sector |

### netting_sets.csv. Key: `netting_set_id`

| Column | Type | Allowed / notes |
|---|---|---|
| netting_set_id | text | Unique, e.g. `NS-CP013-02` |
| counterparty_id | text | Must exist in counterparties (otherwise FATAL) |
| netting_permitted | bool | **Synthetic modelling assumption**, not a legal opinion |

### trades.csv. Key: `(as_of_date, trade_id)`

| Column | Type | Allowed / notes |
|---|---|---|
| as_of_date | date | Snapshot date |
| trade_id | text | Stable across snapshots |
| counterparty_id | text | Must equal the netting-set owner |
| netting_set_id | text | Must exist |
| product_type | text | IRS, CCS, FX_FORWARD, FX_SWAP, EQUITY_SWAP |
| currency | text | GBP, USD, EUR |
| notional | number | > 0. Context only; **not** exposure. |
| mtm | number | Finite, signed. + = receivable to the reporting entity, − = payable. |
| maturity_date | date | Included only if `maturity_date > as_of_date` |
| trade_status | text | ACTIVE, MATURED, TERMINATED. Only ACTIVE can be included. |

### collateral.csv. Key: `(as_of_date, collateral_id)`

| Column | Type | Allowed / notes |
|---|---|---|
| as_of_date | date | |
| collateral_id | text | One netting-set allocation per date |
| counterparty_id | text | Must equal the netting-set owner |
| netting_set_id | text | Must exist |
| currency | text | GBP, USD, EUR |
| market_value | number | ≥ 0 |
| haircut | number | Decimal in [0, 1] |
| eligible | bool | |
| direction | text | RECEIVED or POSTED. Only RECEIVED can be recognised; POSTED is flagged. Any other value is quarantined. |

### ratings.csv. Key: `(counterparty_id, effective_from)`

| Column | Type | Allowed / notes |
|---|---|---|
| counterparty_id | text | Must exist (otherwise the row is quarantined) |
| effective_from | date | Inclusive |
| effective_to | date, optional | Exclusive; blank = open-ended |
| rating | text | AAA, AA, A, BBB, BB, B, CCC (synthetic ratings). With no effective record, the output shows `Unrated` and the row is flagged. |

Overlapping intervals for one counterparty are FATAL.

### limits.csv. Key: `limit_id`

| Column | Type | Allowed / notes |
|---|---|---|
| limit_id | text | Unique |
| counterparty_id | text | Must exist |
| effective_from | date | Inclusive |
| effective_to | date, optional | Exclusive; blank = open-ended |
| limit_currency | text | GBP, USD, EUR. Converted at same-date FX. |
| limit_amount | number | ≥ 0. A limit of 0 is a valid zero limit; a missing record means Missing Limit. |

Overlapping intervals for one counterparty are FATAL.

### fx_rates.csv. Key: `(as_of_date, currency)`

| Column | Type | Allowed / notes |
|---|---|---|
| as_of_date | date | |
| currency | text | GBP, USD, EUR |
| gbp_per_unit | number | > 0. GBP must be exactly 1 (otherwise FATAL). |

A rate is required for every (date, currency) used by accepted trades, accepted collateral
and that date's effective limits. A missing rate is FATAL; rates are never filled or carried
forward.

### control_totals.csv. Key: `(as_of_date, source_table, currency)`

| Column | Type | Allowed / notes |
|---|---|---|
| as_of_date | date, optional | Blank for static tables (counterparties, netting_sets, ratings, limits) |
| source_table | text | One of the seven source tables |
| currency | text | Record currency, or `ALL` when the table has no currency split |
| expected_row_count | integer ≥ 0 | Raw row count in the group |
| expected_amount_total | number, optional | Signed sum of `amount_field` in local currency over **all raw rows** in the group, including rows later excluded or quarantined. Non-numeric raw amounts cannot be summed and are counted in the row count only. |
| amount_field | text, optional | `mtm`, `market_value`, `limit_amount`, or blank (count only) |

**Controlled population.** Every raw row in each source file is controlled, before any
filtering or validation. Malformed control rows are FATAL.

### generation_metadata.json

This file holds:

- the seed, generator version and parameters;
- the date range and calendar disclaimer;
- row counts and distinct trade and netting-set counts;
- scenario definitions and their verification results;
- the feed and control conventions.

## Missing-value policy (summary)

| Situation | Treatment |
|---|---|
| Blank or invalid required field | Record quarantined, reason kept, run `DEGRADED` |
| Missing FX for a required (date, currency) | FATAL |
| No effective limit | Business status `Missing Limit`; utilisation and headroom blank |
| No effective rating | `Unrated` plus `MISSING_RATING` flag |
| Netting set with no included trades on a date | Zero exposure (absence of trades, not a filled value) |
