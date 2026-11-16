"""Generate the deterministic SYNTHETIC source data set.

Run from the project root:

    python scripts/generate_data.py --config configs/baseline.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from counterparty_mi.config import ConfigError, load_config, resolve
from counterparty_mi.synthetic import generate_synthetic_data, write_generated_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic source CSVs (counterparties, netting sets, trades, "
        "collateral, ratings, limits, FX rates, source control totals) and generation metadata."
    )
    parser.add_argument("--config", required=True, help="Path to JSON config, e.g. configs/baseline.json")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for raw CSVs (default: paths.raw_dir from the config, relative to the current directory). "
        "Existing files with the same names are overwritten.",
    )
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5
    out_dir = Path(args.output_dir) if args.output_dir else resolve(Path.cwd(), cfg["paths"]["raw_dir"])
    data = generate_synthetic_data(cfg)
    written = write_generated_data(data, out_dir)
    print(f"Synthetic data written to: {out_dir}")
    for name, path in written.items():
        rows = data.metadata["row_counts"].get(name)
        suffix = f" ({rows} rows)" if rows is not None else ""
        print(f"  {path.name}{suffix}")
    print(f"Distinct trades: {data.metadata['distinct_trades']}; netting sets: {data.metadata['distinct_netting_sets']}")
    print(f"Date range: {data.metadata['date_range']['start']} to {data.metadata['date_range']['end']} "
          f"({data.metadata['date_range']['n_dates']} business dates)")
    print("Scenario verification: " + ("all passed" if all(data.metadata["scenario_verification"].values()) else "FAILED"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
