# Baseline results

All figures below come from the saved outputs in `results/baseline/`. They were produced by
running `generate_data.py`, `run_pipeline.py` and `generate_report.py` with
`configs/baseline.json`. **All data are synthetic**, so none of these observations
describes a real institution.

## Configuration

| Item | Value |
|---|---|
| Run ID | `baseline-7e6d46ebd870` (hash of the config and source checksums, so the same inputs give the same ID) |
| Run status | `SUCCESS` |
| Seed | 42 |
| Dates | 2026-01-05 to 2026-03-27, 60 business dates (Monday–Friday index) |
| Reporting currency | GBP |
| Warning threshold | 80% |
| At-limit tolerance | £0.01 |
| Reconciliation tolerances | 0.01 absolute; 1e-9 relative |
| Input rows | counterparties 40; netting sets 50; trades 22,485; collateral 1,140; ratings 40; limits 41; FX 180; control totals 426 |
| Accepted / quarantined rows | All accepted; 0 quarantined; 0 fatal errors |
| Trade population | 22,485 accepted snapshots → 22,359 included + 126 excluded (28 on/after maturity date, 84 `MATURED` status, 14 `TERMINATED` status) |
| Python and packages | 3.14.4; numpy 2.5.4, pandas 3.1.1, matplotlib 3.11.2 |

## Latest date (2026-03-27)

| Measure | 2026-03-27 | 2026-03-26 |
|---|---|---|
| Gross positive MTM | £148.12m | £148.24m |
| Netting benefit | £50.28m | £49.63m |
| Pre-collateral exposure | £97.84m | £98.61m |
| Recognised collateral (after haircuts) | £14.05m | £14.40m |
| Collateral benefit | £8.57m | £8.79m |
| Net current exposure | £89.26m | £89.82m |

Recognised collateral (£14.05m) is larger than the collateral benefit (£8.57m). The
difference is surplus collateral in overcollateralised netting sets. That surplus cannot
reduce exposure below zero and is not moved to other sets.

**Population:**

- 40 counterparties, of which 27 have positive net exposure.
- 50 netting sets.
- 365 included trades.

## Limit monitoring

On the latest date, **1 breach, 1 at-limit, 2 warnings, 1 zero limit with exposure, 1 zero
limit without exposure, 1 missing limit and 33 within limit**.

| Counterparty | Status | Exposure | Limit (GBP) | Utilisation |
|---|---|---|---|---|
| CP003 | Breach | £13,542,144 | £11,849,376 (EUR 14m) | 114.3%; headroom −£1,692,768 |
| CP017 | At Limit | £5,000,000 | £5,000,000 | 100.0% |
| CP009 | Warning | £8,000,000 | £9,000,000 | 88.9% |
| CP002 | Warning | £8,622,736 | £10,000,000 | 86.2% |
| CP004 | Zero Limit — Exposure | £2,000,000 | £0 | n/a |
| CP005 | Zero Limit — No Exposure | £0 | £0 | n/a |
| CP006 | Missing Limit | £3,000,000 | – | n/a |

How these statuses developed over the window:

- **CP003** moved from Within Limit (2026-01-05 to 02-05) to Warning (02-06 to 03-06) to
  Breach (03-09 to 03-27, 15 dates). Its peak utilisation was 114.3%.
- **CP009** went from Within Limit to Warning on 2026-02-23, when its limit was cut from
  15m to 9m GBP.
- **CP006** has had no effective limit since 2026-02-16, because its only limit was
  effective to 2026-02-16 (exclusive).
- **CP025** was in Warning on 10 dates (2026-01-14 to 2026-02-27) and **CP026** on 1 date
  (2026-03-17). Both are background counterparties whose generated limits were deliberately
  "tight".
- **Across the window:**
  - Breaches: CP003 is the only counterparty that breached, with 15 breach counterparty-days.
  - Warnings: 117 warning counterparty-days.
  - Limit statuses: 60 at-limit days, 60 days of zero limit with exposure, and 30
    missing-limit days.

## Concentration

- **Top 10:** the 10 largest counterparties hold 69.6% of total net current exposure on
  2026-03-27. Over the window the share ranged from 65.7% to 69.8%.
- **Largest single counterparty:** CP003, at 15.2%.
- **Rating:**
  - BBB is the largest bucket: £35.87m (40.2%) across 16 counterparties.
  - BB is next: £26.52m (29.7%).
  - Unrated: £1.50m (1.7%), which is CP007, flagged `MISSING_RATING`.
- **Sector:**
  - Hedge Funds: £22.56m (25.3%).
  - Corporates - Industrials: £22.07m (24.7%).
  - Banks: £19.46m (21.8%).
- **Country:**
  - US: £22.42m (25.1%).
  - GB: £12.55m (14.1%).
  - CA: £12.11m (13.6%).
- **Parent group:**
  - Synthetic Parent Group 10: £17.57m (19.7%, 9 counterparties).
  - Synthetic Parent Group 09: £17.34m (19.4%, 7 counterparties).
  - Parent-group figures are descriptive only; no group limits exist.

All shares use total net current exposure of all counterparties on the date as the
denominator.

## Exposure movement

- **Across the window:** net current exposure rose from £80.99m (2026-01-05) to £89.26m
  (2026-03-27). The window minimum was £80.35m and the maximum £91.87m.
- **Latest day:** exposure fell by £0.56m (−0.62%) from the previous day.
- **Largest single-name moves that day:** an increase of £281,392 at CP036 and a decrease
  of £284,798 at CP021.

## Collateral and netting scenarios (2026-03-27, netting-set level)

| Set | Netting? | Gross + MTM | Pre-collateral | Recognised collateral | Net | Notes |
|---|---|---|---|---|---|---|
| NS-CP010-01 | Yes | £3.0m | £3.0m | £5.0m | £0 | £2.0m excess, unused |
| NS-CP011-01 | Yes | £6.0m | £2.0m | £0 | £2.0m | Netting benefit £4.0m |
| NS-CP012-01 | No | £6.0m | £6.0m | £0 | £6.0m | £1.0m collateral unsupported (non-netting set) |
| NS-CP013-01 | Yes | £2.0m | £2.0m | £5.0m | £0 | £3.0m excess, not moved to set 02 |
| NS-CP013-02 | Yes | £4.0m | £4.0m | £0 | £4.0m | CP013 total £4.0m |

## Data-quality and business warnings

There are 9 summary warning rows (`data_quality_issues.csv`). There are no fatal or
quarantine issues.

| Check | Class | Counterparty |
|---|---|---|
| `MISSING_RATING` | Data quality | CP007 |
| `UNSUPPORTED_COLLATERAL_NON_NETTING_SET` | Data quality | CP012 |
| `MISSING_LIMIT` | Business | CP006 |
| `ZERO_LIMIT_WITH_EXPOSURE` | Business | CP004 |
| `ZERO_LIMIT_NO_EXPOSURE` | Business | CP005 |
| `LIMIT_BREACH` | Business | CP003 |
| `INELIGIBLE_COLLATERAL` | Business | CP015 |
| `POSTED_COLLATERAL` | Business | CP016, CP019 (background) |

`rejected_records.csv` contains only its header: no records were quarantined.

## Reconciliation

**2,777 of 2,777 controls passed** (C01 426, C02 363, C03 128, C04 60, C05 180, C06 180,
C07 60, C08 120, C09 300, C10 360, C11 600). Every date's `reconciliation_status` is
`PASS`.

The breach at CP003 did not cause any control to fail. Breaches are business outcomes, not
arithmetic failures.

## Caveats

- The data are synthetic. The scenario counterparties (CP001–CP017) have hand-built,
  mostly constant MTM paths so that their statuses are guaranteed; they do not behave like
  real portfolios.
- The exposure measure is current exposure only. It has no PFE or add-ons, and no
  thresholds or minimum transfer amounts.
- Passing reconciliation shows that the outputs are internally consistent with the
  synthetic extract and its control totals. It does not show that the data are true or
  that netting is enforceable.
- The baseline had no quarantined records, so its population is complete relative to the
  extract. Degraded and failed behaviour is demonstrated only with the test fixtures.
