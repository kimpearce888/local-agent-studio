"""Run the Phase 0 benchmark: ``python -m bench`` (mock harness dry-run).

Real-model runs use the same corpus through a configured provider once one
is reachable: ``python -m bench --provider my-server`` (not available in
this environment; the flag exists and reports what it would need).
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

from bench.harness import run_all


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench")
    parser.add_argument("--goals", type=int, default=100,
                        help="corpus size (max 100)")
    parser.add_argument("--kills", type=int, default=200,
                        help="forced-kill/resume cycles")
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--out", default=None, help="results directory")
    parser.add_argument("--provider", default="mock",
                        help="provider for worker/planner calls")
    args = parser.parse_args(argv)

    if args.provider != "mock":
        print(f"provider {args.provider!r}: the benchmark needs a reachable "
              "server; none is available in this environment. The mock "
              "dry-run validates the harness itself.")
        return 2

    data_dir = args.data_dir or tempfile.mkdtemp(prefix="las-bench-")
    report, exit_code = run_all(data_dir, args.goals, args.kills, args.seed)

    out_dir = args.out or os.path.join("bench", "results")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(out_dir, f"bench-{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    c = report["corpus"]
    k = report["resume_after_kill"]
    print(f"Corpus: {c['goals']} goals (mock - harness dry-run)")
    print(f"  valid first attempt:  {c['first_attempt_valid']}/{c['goals']}"
          f"  ({c['first_attempt_valid'] / c['goals']:.0%},"
          f" PRD >= 90% with the reference model)")
    print(f"  valid after repair:   {c['valid_after_repair']}/{c['goals']}"
          f"  ({c['valid_after_repair'] / c['goals']:.0%}, PRD >= 99%)")
    print(f"  done without help:    {c['completed_without_help']}/{c['goals']}"
          f"  ({c['completed_without_help'] / c['goals']:.0%}, PRD >= 85%)"
          f"  [gate pauses: {c['gate_pauses']}]")
    print(f"Forced kills: {k['resumed_ok']}/{k['cycles']} resumed cleanly"
          f"  (PRD >= 99%)")
    cap = report["cap"]
    print(f"Cap enforcement: cap_reached={cap['cap_reached']}, "
          f"work_after_cap={cap['work_after_cap']} (PRD: zero)")
    net = report["network"]
    print(f"Outbound connections (default config): "
          f"{len(net['outbound_attempts'])} (PRD: zero)")
    print(f"Engine gates: {'PASSED' if report['engine_gates_passed'] else 'FAILED'}")
    print(f"Results: {path}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
