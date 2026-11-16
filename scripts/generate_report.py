"""Generate management_report.md from saved pipeline outputs (no recalculation,
no regeneration of source data).

Run from the project root:

    python scripts/generate_report.py --results-dir results/baseline

Exit codes: 0 normal report (SUCCESS), 1 report for a DEGRADED run,
3/4 error summary written (FAILED_VALIDATION / FAILED_RECONCILIATION),
5 missing or unreadable outputs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from counterparty_mi.reporting import ReportInputError, write_report

EXIT = {"SUCCESS": 0, "DEGRADED": 1, "FAILED_VALIDATION": 3, "FAILED_RECONCILIATION": 4}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write management_report.md from the CSV/JSON outputs in a results directory. "
        "If the run failed, an error summary is written instead of a normal report."
    )
    parser.add_argument("--results-dir", required=True, help="Pipeline output directory, e.g. results/baseline")
    args = parser.parse_args(argv)
    try:
        path, status = write_report(Path(args.results_dir))
    except (ReportInputError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5
    kind = "error summary" if status in ("FAILED_VALIDATION", "FAILED_RECONCILIATION") else "management report"
    print(f"Wrote {kind}: {path} (run status {status})")
    return EXIT.get(status, 5)


if __name__ == "__main__":
    raise SystemExit(main())
