"""counterparty_mi: synthetic counterparty current-exposure and limit-monitoring pipeline.

All counterparties, trades, valuations and results produced by this package are
synthetic. The package implements a simplified current-exposure view only; it does
not implement trade pricing, potential future exposure, SA-CCR, regulatory EAD,
CVA, expected credit loss or regulatory capital.
"""

__version__ = "0.1.0"

REPORTING_CURRENCY = "GBP"
