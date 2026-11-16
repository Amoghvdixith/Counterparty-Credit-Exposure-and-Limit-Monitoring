# Methodology

This project calculates a **simplified current exposure** for one reporting entity, in
GBP, from synthetic daily snapshots.

**What it does not calculate:**

- trade pricing;
- potential future exposure;
- SA-CCR;
- regulatory exposure at default;
- CVA;
- expected credit loss;
- regulatory capital.

It also does not test whether netting is legally enforceable.

## 1. Trade population

On each as-of date *d*, an accepted trade snapshot is **included** only if:

```
trade_status == "ACTIVE"  and  maturity_date > d
```

All other snapshots are **excluded**, each with a documented reason:

| Reason | Rule |
|---|---|
| `STATUS_TERMINATED` | `trade_status = TERMINATED` |
| `STATUS_MATURED` | `trade_status = MATURED` |
| `MATURITY_ON_OR_BEFORE_AS_OF` | `ACTIVE` but `maturity_date <= d` |

**Simplified maturity convention.** A trade that matures on *d* is treated as settled, so it
contributes no exposure on *d*.

Excluded snapshots are counted and reconciled (control C04); they are not data errors.

## 2. FX conversion

Every MTM, collateral value and limit is converted to GBP **before** aggregation, using
the same-date rate:

```
amount_gbp = amount_local × gbp_per_unit(d, currency)
```

The GBP rate must equal 1. Missing rates are fatal. FX joins are validated many-to-one.

## 3. Netting-set exposure

For each date *d* and netting set *s*, using only the included trades *t* in *s*:

```
gross_positive_mtm      = Σ max(mtm_gbp_t, 0)
signed_net_mtm          = Σ mtm_gbp_t
pre_collateral_exposure = max(signed_net_mtm, 0)      if netting_permitted(s)
                        = gross_positive_mtm          otherwise
netting_benefit         = gross_positive_mtm − pre_collateral_exposure
```

**Netting assumption.** `netting_permitted` is a synthetic flag. Netting happens only
inside one netting set, never across sets or across counterparties.

The grid is every (report date × netting set). A set with no included trades has zero MTM
and zero exposure.

## 4. Collateral

Each accepted collateral record falls into exactly one class. The rules are checked in this
order, and the first match wins:

| Order | Class | Condition | Recognised? |
|---|---|---|---|
| 1 | `POSTED_NOT_RECOGNISED` | `direction = POSTED` | No; flagged as a business warning |
| 2 | `NON_NETTING_SET_UNSUPPORTED` | Netting set not netting-permitted | No; flagged as a data-quality warning |
| 3 | `INELIGIBLE` | `eligible = FALSE` | No; flagged as a business warning |
| 4 | `RECOGNISED` | Otherwise | Yes |

The recognised value is calculated, and applied, as follows:

```
recognised_collateral(s) = Σ market_value × gbp_per_unit × (1 − haircut)   over RECOGNISED items in s
net_current_exposure     = max(pre_collateral_exposure − recognised_collateral, 0)
collateral_benefit       = pre_collateral_exposure − net_current_exposure
excess_collateral        = max(recognised_collateral − pre_collateral_exposure, 0)   (reported, unused)
```

- Collateral is deducted once per netting set, not once per trade.
- Surplus collateral in one set is **not** moved to another set or counterparty.
- Net exposure can never be negative.

**Not modelled:** thresholds, minimum transfer amounts, independent amounts, margin period
of risk and wrong-way risk.

## 5. Counterparty exposure

Each counterparty measure is the sum of the netting-set measures over that counterparty's
own sets. There is no netting across counterparties. Notional never enters exposure.

## 6. Limits

**Selecting the limit.** On date *d*, the pipeline selects the single limit for which
`effective_from <= d < effective_to`, with a blank end meaning open-ended. Overlapping
limits are fatal, so at most one can match. The limit is converted to GBP at the
same-date FX rate.

**Calculations for positive limits:**

```
utilisation_pct = 100 × net_current_exposure / limit_gbp
headroom_gbp    = limit_gbp − net_current_exposure      (negative = exposure above limit)
```

**Status rules.** The rules are checked in this order and exactly one applies. The
tolerance is £0.01 (`at_limit_tolerance_gbp`) and the warning threshold is 80%
(`warning_threshold_pct`).

| Order | Status | Condition |
|---|---|---|
| 1 | Missing Limit | No effective limit |
| 2 | Zero Limit — Exposure | limit = 0 and exposure > tolerance |
| 3 | Zero Limit — No Exposure | limit = 0 and exposure ≤ tolerance |
| 4 | At Limit | \|exposure − limit\| ≤ tolerance |
| 5 | Breach | exposure > limit + tolerance |
| 6 | Warning | utilisation ≥ 80% |
| 7 | Within Limit | otherwise |

**Missing and zero limits:**

- Utilisation is blank (NaN). It is never shown as 0% and never as infinity.
- Headroom is blank for a missing limit. For a zero limit it equals −exposure.

**Reporting:**

- Utilisation percentages are never summed.
- Business limit status is kept separate from data-quality flags (`data_quality_flags`) and
  from reconciliation status.

## 7. Concentrations and history

- **Top 10 per date.** Only counterparties with positive net current exposure are ranked:
  highest exposure first, ties broken by `counterparty_id` in ascending order.
- **Shares.** `share_of_total_net_exposure_pct` = 100 × item exposure / total net current
  exposure of all counterparties on that date. If the total is zero, the share is blank
  and the top-10 table is empty for that date.
- **Breakdowns:** by rating (missing ratings shown as `Unrated` and flagged), sector,
  country and parent group.
- **Parent groups** are descriptive only. No group limits are supplied, so no group-limit
  test is made.
- **Daily summary.** Totals, counts by limit status, top-10 share, and day-on-day change.
  The percentage change uses the previous date's total as its denominator and is blank
  when that total is zero.

## 8. Reconciliation bridges

A control passes when `|actual − expected| ≤ max(abs_tol, rel_tol × |expected|)`, with
`abs_tol = 0.01` and `rel_tol = 1e-9` (configurable). Counts must match exactly.

| ID | Control | Expected vs actual |
|---|---|---|
| C01 | SOURCE_ROW_COUNT | Source control row count vs raw rows, by table, date and currency |
| C02 | SOURCE_AMOUNT_TOTAL | Source control signed total vs raw signed total (local currency) |
| C03 | QUARANTINE_BRIDGE | Raw rows = accepted + quarantined, per table and per date |
| C04 | TRADE_INCLUSION_BRIDGE | Accepted snapshots = included + excluded (with reasons) |
| C05 | TRADE_TO_NETTING_SET_* | Included trade count, signed MTM and positive MTM vs netting-set aggregates. Catches dropped trades and join duplication. |
| C06 | COLLATERAL_* | Accepted vs classified records; one allocation per collateral item; recognised value vs netting-set recognised value |
| C07 | NETTING_BENEFIT_BRIDGE | gross_positive − netting_benefit = pre_collateral |
| C08 | COLLATERAL_BENEFIT_BRIDGE, NON_NEGATIVE_EXPOSURE | pre_collateral − collateral_benefit = net; no negative exposure |
| C09 | NETTING_SET_TO_COUNTERPARTY | Five exposure measures, netting sets vs counterparties |
| C10 | COUNTERPARTY_TO_DAILY_SUMMARY | Five measures and counterparty count, counterparties vs daily summary |
| C11 | BREAKDOWN_*, TOP_N_* | Rating, sector, country and parent totals (shares sum to 100%); top-10 vs an independent `nlargest`; top-10 vs daily summary |

- Signed MTM is never expected to equal floored exposure; C07 and C08 bridge the two
  explicitly.
- A limit breach is a business outcome, not a reconciliation failure.
- Passing the controls does **not** prove that the source data are true, that the economics
  are correct, or that netting is legally enforceable.

## 9. Error policy and run status

**Issue severities:**

| Severity | Examples | Effect |
|---|---|---|
| FATAL | Missing file or column; duplicate key (identical or conflicting); overlapping limit or rating intervals; missing FX; GBP ≠ 1; netting set with an unknown owner; trade or collateral counterparty ≠ netting-set owner; malformed control row | No exposure outputs. Run `FAILED_VALIDATION` (exit code 3). Stale outputs removed. The report becomes an error summary. |
| QUARANTINE | Unparseable or blank required field; non-finite number; value not allowed; negative market value or limit; haircut outside [0, 1]; non-positive FX or notional; inverted interval; orphan record | Record rejected, reason and raw record kept in `rejected_records.csv`. Run `DEGRADED` (exit code 1); exposure may be incomplete. Affected counterparties are flagged `QUARANTINED_SOURCE_RECORDS`. |
| WARNING | Missing rating; collateral on a non-netting set (data quality); missing limit, zero limit, breach, ineligible or posted collateral (business) | Reported and flagged. They are not validation or arithmetic failures. |

**Reconciliation failure.** If any reconciliation control fails, the run is
`FAILED_RECONCILIATION` (exit code 4). Outputs are written so the failure can be
investigated, and the management report is replaced by an error summary.

**Duplicates.** Duplicate keys are never silently dropped. Even exact duplicates are
fatal, because a duplicate could be a double booking or a duplicated feed.
