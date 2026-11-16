# Validation

## Test results (actual)

Command: `python -m pytest -q --junitxml=results/baseline/test_results.xml`

Environment: CPython 3.14.4 on Windows, in the project `.venv`.

| Tests | Passed | Failed | Errors | Skipped |
|---|---|---|---|---|
| 73 | 73 | 0 | 0 | 0 |

The suite was also run with `-W error::Warning`, which turns every warning into a failure.
All 73 tests passed in that mode as well.

| File | Tests | What is covered |
|---|---|---|
| `test_validation.py` | 17 | Clean fixture; missing column or file; identical and conflicting duplicate keys; netting-set ownership mismatch; unknown owner; orphan trade; malformed values (text, `inf`, bad status, bad date, haircut > 1, negative market value, bad boolean); missing MTM not set to zero; missing FX for a trade and for a limit currency; negative FX; GBP ≠ 1; overlapping limits and ratings; negative limit; inverted interval |
| `test_effective_dates.py` | 6 | Inclusive start and exclusive end; open-ended interval; gaps; no future information; ambiguous records raise an error; nested overlap found, adjacent intervals not flagged |
| `test_fx.py` | 4 | Conversion; missing rate raises; a different date's rate is never used; duplicate FX rows cannot multiply records |
| `test_exposure.py` | 12 | Netting +100/−60 → 40; no netting → 100; collateral deducted once with haircut; collateral FX and haircut; ineligible and posted collateral ignored; collateral on a non-netting set unsupported; excess collateral floored at 0; no transfer between sets; no netting across counterparties; maturity and status exclusion; FX applied before netting; notional is not exposure |
| `test_limits.py` | 9 | Within limit; warning boundary (79.99 vs 80.00); at-limit tolerance (±0.009 vs +0.02); breach; missing and zero limits; utilisation blank or finite; statuses mutually exclusive; EUR limit FX and negative headroom; missing limit gives blank utilisation and headroom |
| `test_concentrations.py` | 5 | Ranking and deterministic ties; share denominator; positive-only top-N cap; zero total exposure; breakdown totals and shares |
| `test_reconciliation.py` | 7 | Clean data passes all 11 control families; a business breach does not fail any control; a tampered raw amount fails C02; an extra raw row fails C01; a dropped trade fails C05; join duplication fails C05; the quarantine bridge reconciles |
| `test_pipeline.py` | 13 (11 test functions; one parametrised over 3 fatal cases) | Deterministic generation (400 trades, 60 dates, all scenarios verified); end-to-end clean run against hand calculation; degraded quarantine run; fatal duplicate, missing FX and overlap (no exposure outputs, error-summary report); stale outputs removed after a fatal run; tampered source gives `FAILED_RECONCILIATION`; repeated runs byte-identical and not appended; `--as-of` history cut-off and invalid dates; managed-file list; full synthetic baseline in a temporary folder; CLI `--help` and error exit codes |

Collected-test totals by file (from `pytest --collect-only`):
17 + 6 + 4 + 12 + 9 + 5 + 7 + 13 = 73.

## Independent checks

- **Hand-computed fixture** (`tests/conftest.py`). Expected values are written out in the
  docstring and checked against pipeline outputs. Examples:
  - CPA = 22 (NS-A1: 40 − 18 collateral) + 40 (NS-A2, no netting, 50 USD × 0.8) = 62.
  - CPB = 200 EUR × 0.9 + 10 = 190 against a 180 GBP limit, giving 105.56%, a Breach.
  - On the next date, T5 matures, so CPB falls to 10.
  - Total net exposure is 252, then 72.
- **Generator scenario check.** `synthetic._verify_scenarios` recomputes each scenario with
  a separate, deliberately simple calculation (`_quick_counterparty_exposure`). If any
  scenario is not met, nothing is written.
- **Independent top-N.** Reconciliation control C11 recomputes the top-10 total with
  `Series.nlargest` and does not use the saved ranking.
- **Source controls.** These are generated from the raw text before any processing and are
  compared with a separate summary of the raw files. The tests tamper with the raw data
  after the controls are built and confirm that the controls fail.
- **Join safety.** Every merge uses `validate=`. The tests show that duplicated FX rows
  raise `MergeError` and that duplicated trade rows are caught by C05.

## Tolerances

| Purpose | Value | Where |
|---|---|---|
| Reconciliation, amounts | pass if \|diff\| ≤ max(0.01, 1e-9 × \|expected\|) | `configs/baseline.json` → `reconciliation` |
| Reconciliation, counts | exact (0) | `reconciliation.py` |
| At-limit band and zero-limit exposure test | £0.01 | `limits.at_limit_tolerance_gbp` |
| Warning threshold | 80% (inclusive) | `limits.warning_threshold_pct` |
| GBP FX rate | exactly 1 (1e-12) | `validation.py` |

## What validation does not establish

- **Source truth.** That the synthetic MTMs, collateral values, ratings or limits are
  correct. Controls prove consistency with the extract, not truth.
- **Economics.** That current exposure is an adequate risk measure. It ignores PFE, margin
  period of risk, thresholds and similar features.
- **Netting.** That netting or collateral agreements are legally enforceable.
- **Regulatory compliance.** Compliance with any rule, including BCBS 239, SA-CCR and
  IFRS 9.
- **Performance.** How the pipeline behaves at production volumes or with real feeds.
- **Other environments.** Behaviour on Python versions or operating systems other than
  those tested (CPython 3.14.4, Windows).
