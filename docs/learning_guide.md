# Learning guide

This guide explains the concepts behind the project, using its own **synthetic** baseline
results as examples.

## 1. MTM vs notional vs exposure

- **Notional** is the reference amount of a contract, for example a £50m swap. It sets the
  size of the cash flows but is **not** the amount at risk.
- **Mark-to-market (MTM)** is the contract's current value to us. If it is positive, the
  counterparty owes us value (a receivable). If it is negative, we owe them.
- **Current exposure** is what we would lose today if the counterparty defaulted. That is
  roughly the positive part of the MTM, after netting and collateral.

Example: CP017's trade has a notional of 50m but an MTM of 5m, so its exposure is 5m.

## 2. Gross vs net exposure

- **Gross positive MTM** adds up only the positive trades. It is the exposure if no
  offsets were allowed.
- **Net exposure** lets negative trades offset positive ones where a netting agreement
  permits it.

In the baseline on 2026-03-27, gross positive MTM was £148.12m. Netting reduced this to
£97.84m, a netting benefit of £50.28m. Collateral then reduced it to £89.26m.

## 3. Netting sets

A **netting set** is a group of trades with one counterparty that can be closed out as a
single net amount, usually under a master agreement. Netting applies only **within** a set:

- Netting permitted (CP011): +6m and −4m give 2m.
- Netting not permitted (CP012): the same trades give 6m, because the −4m cannot be used.

Netting never crosses counterparties. In this project `netting_permitted` is a synthetic
flag, whereas in practice it depends on legal opinions.

## 4. Collateral and haircuts

Collateral is an asset received to cover exposure. A **haircut** reduces its value to allow
for price falls and FX risk. For example, 100 with a 10% haircut counts as 90.

In this project collateral counts only if it is received, eligible and held against a
netting-permitted set. It reduces that set's exposure only down to zero.

Example: CP013 has 5m of collateral against 2m of exposure in set 01, a surplus of 3m. That
surplus does **not** reduce the 4m exposure in set 02, so CP013's exposure is 4m.

## 5. Limits, utilisation and headroom

- A **limit** is the maximum exposure that has been approved for a counterparty.
- **Utilisation** = exposure / limit.
- **Headroom** = limit − exposure. Negative headroom means a breach.

Example: CP003 has £13.54m of exposure against an EUR 14m limit, which converts to
£11.85m. That is 114.3% utilisation and −£1.69m headroom.

Special cases:

- **Zero limit** (CP004, CP005): no exposure is allowed, so any exposure is an exception.
  Utilisation cannot be defined.
- **Missing limit** (CP006): a monitoring gap. It is not the same as being within limit.
- **Warning** (80%): an early-warning trigger (CP002, CP009).

## 6. Concentration risk

**Concentration risk** is the risk that losses come from a few large names or from
correlated groups such as one sector, country, rating band or parent group. On 2026-03-27
the top 10 counterparties held 69.6% of net exposure.

Always state the denominator. Here it is total net current exposure on that date. Parent
groups are reported for information only; testing them would need group limits.

## 7. Effective-dated data

Ratings and limits change over time. Each record has an
`[effective_from, effective_to)` interval. To report a past date correctly, use the record
that was effective **on that date**, never a later one:

- CP008 was rated A up to 2026-02-06 and BBB from 2026-02-09.
- CP009's limit was cut to 9m on 2026-02-23, so its earlier dates still show 15m.

If two records overlap, the correct limit is ambiguous, so the run stops.

## 8. Reconciliation vs validation

- **Validation** checks that each input is well-formed and consistent. For example: types,
  keys, foreign keys, FX coverage and overlaps.
- **Reconciliation** checks that numbers are preserved through every transformation. For
  example:
  - raw rows against the source control totals;
  - raw records = accepted + quarantined;
  - trades against netting sets, counterparties, the daily summary and the breakdowns.

  Explicit bridges explain differences that should exist, such as netting benefit and
  collateral benefit.

Neither one proves the data are *true*. A breach is a business result, not a
reconciliation failure.

## 9. Interview self-check questions

1. Why is notional not a measure of current exposure? When might it still be useful?
2. A set holds +100 and −60. What is the exposure with netting, and without netting?
3. Why must FX conversion happen before netting? (See `test_mtm_converted_before_aggregation`.)
4. Why can surplus collateral in one netting set not reduce exposure in another?
5. What is the difference between a zero limit and a missing limit? Why should neither show
   0% utilisation?
6. How does an effective-dating rule stop "future information" leaking into a past report?
7. What should happen when two limits overlap for the same counterparty? Why is choosing
   one silently dangerous?
8. Give an example of a quarantinable error and one of a fatal error. Why treat them
   differently?
9. Which reconciliation control catches a many-to-many join that duplicates trades?
10. Signed net MTM and net current exposure differ. Which bridges explain the difference?
11. What does a passing reconciliation **not** tell you?
12. Why should top-10 concentration always state its denominator?
13. What is missing from current exposure compared with SA-CCR or PFE-based measures?
14. Why is parent-group exposure "descriptive only" here?
