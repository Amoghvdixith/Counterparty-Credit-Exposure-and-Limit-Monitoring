# Source data (synthetic)

All files in `data/raw/` are **synthetic**. They are produced deterministically by
`src/counterparty_mi/synthetic.py` and describe no real institution, trade or valuation.
Counterparty names are built as "Synthetic" + a plant or bird word + a sector suffix.

## Reproduce

```bash
python scripts/generate_data.py --config configs/baseline.json
```

Regenerating overwrites the files in `data/raw/`. With the same config and package
versions, the output is byte-identical: this was confirmed by an unchanged SHA-256 of
`trades.csv` across two runs, and by the test `test_synthetic_generation_is_deterministic`.

## Generation settings (configs/baseline.json)

| Setting | Value |
|---|---|
| Seed | 42 (`numpy.random.default_rng`) |
| Dates | 60 business dates, 2026-01-05 to 2026-03-27 (`pandas.bdate_range`, Monday–Friday). **Not** an exchange or settlement holiday calendar. |
| Counterparties | 40 (`CP001`–`CP040`): 17 scenario counterparties plus 23 seeded background counterparties |
| Netting sets | 50; 6 are not netting-permitted, and 8 counterparties have more than one set |
| Trades | 400 distinct trade IDs, 22,485 daily snapshots |
| Currencies | GBP, USD, EUR |
| FX | Seeded log random walk with 0.3% daily volatility; starts at USD 0.79 and EUR 0.855 GBP per unit; GBP = 1 |
| Other rows | collateral 1,140; ratings 40; limits 41; FX 180; control totals 426 |

**Background counterparties:**

- **Netting sets:** 1–3 each; 85% are netting-permitted.
- **Trade MTM:** starts from N(0, 2% of notional) and follows a daily random walk with
  steps of N(0, 0.2% of notional).
- **Trade lifecycle:**
  - about 10% of trades mature inside the window;
  - about 2% are terminated;
  - about 8% start mid-window.
- **Collateral:** about half of the netting-permitted sets hold collateral.
  - Type: cash (no haircut) or bonds (2–15% haircut).
  - Eligibility: 90% eligible; 5% is posted rather than received.
  - Value: 30–110% of the set's exposure on the previous day.
- **Limits:** set at the counterparty's simulated peak exposure × U(1.3, 3.0). Three "tight"
  counterparties use × U(0.95, 1.2) instead. Limits are converted at first-date FX and
  rounded up to the nearest 100,000.

## Feed conventions

- **MTM sign:** positive means a receivable to the reporting entity; negative means a
  payable by it. Notional is context only; it is not exposure.
- **Maturity:** a trade is reported `ACTIVE` up to and including its maturity date. It is
  then reported `MATURED` with MTM 0 for three business days and dropped from the feed.
  The pipeline excludes it from the maturity date onward.
- **Termination:** a terminated trade is reported `TERMINATED` with MTM 0 for two business
  days, then dropped.
- **Collateral:** one record per collateral item per date.
- **Control totals:** `control_totals.csv` is built from the raw string tables **before**
  any downstream processing. It holds:
  - row counts per table, split by date and currency where those columns exist;
  - signed local-currency totals of `trades.mtm`, `collateral.market_value` and
    `limits.limit_amount`.

  Totals include every raw row, including trades that are later excluded, and are written
  to 2 decimal places.

## Guaranteed business scenarios

The generator recomputes these scenarios with a separate, simple calculation and refuses
to write data if any of them fails (`scenario_verification` in `generation_metadata.json`;
all passed). The Pipeline result column is from the actual baseline run.

| Counterparty | Scenario | Pipeline result (actual baseline) |
|---|---|---|
| CP001 | Within limit | Within Limit on all 60 dates (≈40%) |
| CP002 | Warning (USD trades, GBP limit) | Warning on all 60 dates (86.2% on 2026-03-27) |
| CP003 | Breach (EUR limit, rising exposure) | Within 2026-01-05..02-05, Warning 02-06..03-06, Breach 03-09..03-27 |
| CP004 | Positive exposure with zero limit | Zero Limit — Exposure |
| CP005 | Zero exposure with zero limit | Zero Limit — No Exposure |
| CP006 | Missing effective limit | Limit ends 2026-02-16 (exclusive); Missing Limit from 2026-02-16 |
| CP007 | Missing effective rating | Unrated, flagged `MISSING_RATING` |
| CP008 | Rating downgrade | A until 2026-02-06, BBB from 2026-02-09 |
| CP009 | Limit change | 15m → 9m GBP on 2026-02-23; Within Limit → Warning |
| CP010 | Overcollateralisation | 3m exposure, 5m cash collateral → net 0 |
| CP011 | Offset trades, netting permitted | +6m / −4m → 2m |
| CP012 | Offset trades, netting not permitted | +6m / −4m → 6m; its 1m collateral is flagged unsupported |
| CP013 | Two sets, no transfer of surplus collateral | Set 1 net 0 (3m surplus), set 2 net 4m → counterparty 4m |
| CP014 | Maturing trade | +4m trade matures 2026-02-13; exposure 5m → 1m |
| CP015 | Ineligible collateral | Not recognised; exposure stays 3m |
| CP016 | Posted collateral | Flagged, not recognised |
| CP017 | At limit | 5,000,000.00 vs 5,000,000 → At Limit |

The main baseline is structurally clean: 0 fatal errors and 0 quarantined records. The
malformed, duplicate, missing-FX, overlapping-limit and tampered-control cases live only
in the small test fixtures in `tests/`.

## Schemas

The full data dictionary is in [../docs/data_dictionary.md](../docs/data_dictionary.md).

| File | Key | Columns |
|---|---|---|
| counterparties.csv | counterparty_id | counterparty_name, parent_group, country, sector |
| netting_sets.csv | netting_set_id | counterparty_id, netting_permitted |
| trades.csv | (as_of_date, trade_id) | counterparty_id, netting_set_id, product_type, currency, notional, mtm, maturity_date, trade_status |
| collateral.csv | (as_of_date, collateral_id) | counterparty_id, netting_set_id, currency, market_value, haircut, eligible, direction |
| ratings.csv | (counterparty_id, effective_from) | effective_to, rating |
| limits.csv | limit_id | counterparty_id, effective_from, effective_to, limit_currency, limit_amount |
| fx_rates.csv | (as_of_date, currency) | gbp_per_unit |
| control_totals.csv | (as_of_date, source_table, currency) | expected_row_count, expected_amount_total, amount_field |
| generation_metadata.json | – | Seed, parameters, scenario definitions and verification, conventions |
