"""Run the stress harness: ``python -m tests.stress`` (or ``./redpen-stress``).

Default: the synthetic fuzzer (320 labelled cases) + the concurrency soak,
writing a report to tests/stress/last_report.md. Exit code is non-zero if any
unforgivable failure (false FAIL / false OK), corruption, or crash is found.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .harness import print_summary, render_report, run_all
from .live import live
from .soak import render_soak, soak, soak_summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="redpen-stress", description="RedPen stress / fuzz harness")
    ap.add_argument("--cases", type=int, default=320, help="number of synthetic cases")
    ap.add_argument("--seed", type=int, default=1234, help="seed (reproducible)")
    ap.add_argument("--no-soak", action="store_true", help="skip the concurrency soak")
    ap.add_argument("--soak-only", action="store_true", help="run only the concurrency soak")
    ap.add_argument("--live", action="store_true", help="also run real-agent eyeball mode (needs claude)")
    ap.add_argument("--report", default=str(Path(__file__).resolve().parent / "last_report.md"))
    args = ap.parse_args(argv)

    sections: list[str] = [f"_generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_\n"]
    failed = False

    if not args.soak_only:
        rep = run_all(n=args.cases, seed=args.seed)
        print_summary(rep)
        sections.append(render_report(rep))
        if rep.counts["false_fail"] or rep.counts["false_ok"] or rep.errors or rep.counts["leak"]:
            failed = True

    if args.soak_only or not args.no_soak:
        res = soak()
        print(soak_summary(res))
        sections.append(render_soak(res))
        if res.problems:
            failed = True

    Path(args.report).write_text("\n".join(sections), encoding="utf-8")
    print(f"report → {args.report}")

    if args.live:
        live()

    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
