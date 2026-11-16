"""Run the counterparty exposure and limit-monitoring pipeline on existing raw data.

Run from the project root:

    python scripts/run_pipeline.py --config configs/baseline.json
    python scripts/run_pipeline.py --config configs/baseline.json --as-of 2026-02-27

Exit codes: 0 SUCCESS, 1 DEGRADED (quarantined records), 2 usage error,
3 FAILED_VALIDATION (fatal), 4 FAILED_RECONCILIATION, 5 configuration/IO error.
This is a repeatable pipeline when invoked; it is not a scheduled service.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from counterparty_mi.config import ConfigError, load_config
from counterparty_mi.pipeline import PipelineError, run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate synthetic source data and produce exposure, limit, concentration, "
        "reconciliation and data-quality outputs plus static charts.",
        epilog="Exit codes: 0 success, 1 degraded, 2 usage error, 3 fatal validation, "
        "4 reconciliation failure, 5 configuration/IO error.",
    )
    parser.add_argument("--config", required=True, help="Path to JSON config, e.g. configs/baseline.json")
    parser.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                        help="Report as of this date (must be a snapshot date); history is limited to dates on or "
                        "before it. Default: latest snapshot date.")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory. Default: paths.results_dir from the config, or results/as_of_<date> "
                        "when --as-of is given. Pipeline-managed files in it are replaced on every run.")
    parser.add_argument("--no-figures", action="store_true", help="Skip PNG chart generation")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        result = run_pipeline(cfg, Path.cwd(), as_of=args.as_of, output_dir=args.output_dir,
                              make_figures=not args.no_figures)
    except (ConfigError, PipelineError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5
    print(f"Run ID: {result.run_id}")
    print(f"Run status: {result.status}")
    if result.latest_date:
        print(f"Latest as-of date: {result.latest_date}")
    print(f"Outputs: {result.output_dir}")
    for m in result.messages:
        print(f"  FATAL: {m}", file=sys.stderr)
    if result.status == "DEGRADED":
        print("WARNING: some source records were quarantined; exposure may be incomplete. See rejected_records.csv.")
    if result.status == "FAILED_RECONCILIATION":
        print("ERROR: reconciliation controls failed; see reconciliation.csv. Outputs are not approved for use.",
              file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
