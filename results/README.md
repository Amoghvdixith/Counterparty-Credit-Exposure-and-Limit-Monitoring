# Results

Every file in `results/baseline/` was produced by actually running the commands below on
the **synthetic** baseline data. None of the numbers were typed in by hand.

## Current execution status

| Item | Status |
|---|---|
| Pipeline run | `SUCCESS`, run ID `baseline-7e6d46ebd870`, latest as-of date 2026-03-27, 60 dates from 2026-01-05 |
| Reconciliation | 2,777 of 2,777 controls passed |
| Quarantined records | 0 |
| Fatal errors | 0 |
| Warnings | 9 summary rows (business and data-quality) |
| Tests | 73 passed, 0 failed, 0 errors, 0 skipped (`test_results.xml`) |
| Management report | Generated (`management_report.md`) |

## Generation commands (from the project root)

```bash
python scripts/generate_data.py --config configs/baseline.json
python scripts/run_pipeline.py --config configs/baseline.json
python scripts/generate_report.py --results-dir results/baseline
python -m pytest -q --junitxml=results/baseline/test_results.xml
```

`run_pipeline.py` replaces its own output files on every run. A run that fails validation
deletes them, so no stale results are left behind.

## Units and conventions

- **Amounts:** every amount ending `_gbp` is in GBP. Output CSVs are rounded to 2 decimal
  places; calculations use full precision.
- **Native-currency amounts:** `limit_amount` is in `limit_currency`.
- **Percentages:** `_pct` columns are percentages (25.0 means 25%), rounded to 4 decimal
  places.
- **Dates:** ISO `YYYY-MM-DD`.
- **Blank cells:** not applicable or unavailable, for example utilisation when the limit is
  missing or zero. A blank is never zero.
- **reconciliation.csv:** keeps full floating-point precision.

## Output definitions

| File | Grain | Content |
|---|---|---|
| `netting_set_exposure.csv` | date × netting set | Included trade count, collateral record count, gross positive MTM, signed net MTM, netting benefit, pre-collateral exposure, recognised collateral, collateral benefit, excess (unused) collateral, net current exposure, unrecognised collateral after haircut |
| `counterparty_exposure.csv` | date × counterparty | Attributes (name, parent, country, sector, rating, `rating_missing`), exposure measures summed over the counterparty's own sets, effective limit (id, currency, amount, FX, GBP), utilisation, headroom, `limit_status`, `data_quality_flags` |
| `daily_summary.csv` | date | Exposure totals, netting and collateral benefit, counts of counterparties, netting sets and trades, counts by limit status, top-10 exposure and share, day-on-day change, `reconciliation_status`, `run_quality_status` |
| `limit_alerts.csv` | date × counterparty | Every row whose status is not Within Limit, with severity (HIGH, MEDIUM or INFO) |
| `top10_concentrations.csv` | date × rank | Top 10 by net current exposure (positive exposure only), share of total and cumulative share |
| `exposure_by_rating.csv`, `exposure_by_sector.csv`, `exposure_by_country.csv`, `exposure_by_parent.csv` | date × group | Counterparty count, counterparties with exposure, gross, pre-collateral and net exposure, share of total. Parent groups are descriptive only. |
| `data_quality_issues.csv` | issue | Severity (`FATAL`, `QUARANTINE`, `WARNING`), class (`DATA_QUALITY` or `BUSINESS`), table, check, date, counterparty, key, source row, message |
| `rejected_records.csv` | rejected source row | Table, source line number, key, reasons, raw record as JSON. Empty in the baseline (header only). |
| `reconciliation.csv` | control | Run ID, date, control ID and name, scope, expected, actual, difference, tolerance, PASS/FAIL, explanation |
| `run_metadata.json` | run | Config, seed, SHA-256 of each source file, input, accepted and quarantined row counts, date coverage, Python and package versions, UTC timestamp, issue counts, run status. Contains no absolute paths. |
| `test_results.xml` | – | pytest JUnit XML |
| `management_report.md` | – | Generated management report with links to `figures/` |
| `figures/*.png` | – | Seven static charts (see below) |

Share columns always use **total net current exposure of all counterparties on the same
date** as the denominator.

## Figures

| File | Content |
|---|---|
| `exposure_trend.png` | Daily gross positive MTM, pre-collateral exposure and net current exposure |
| `top10_counterparties.png` | Top 10 counterparties by net current exposure on the latest date |
| `limit_utilisation.png` | Utilisation for counterparties with a positive limit on the latest date, with 80% and 100% reference lines. Missing and zero-limit counterparties are listed in a footnote. |
| `exposure_by_rating.png` | Exposure by rating on the latest date |
| `exposure_by_sector.png` | Exposure by sector on the latest date |
| `collateral_comparison.png` | Pre-collateral versus net exposure for the top 15 counterparties by pre-collateral exposure |
| `limit_status_counts.png` | Counterparty counts by limit status on the latest date |
