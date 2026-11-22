#!/usr/bin/env python3
"""Live single-shot validation: run a slice of the corpus against a real
provider through the SAME harness path as the benchmark
(run_corpus_real), so a passing validation predicts the bench.

Usage: python scripts/validate_goals.py FIRST_INDEX COUNT
"""

import argparse
import json
import sys
import tempfile
import time

from bench.goals import build_corpus
from bench.harness import real_adapter, run_corpus_real


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("first", type=int)
    ap.add_argument("count", type=int)
    ap.add_argument("--provider", default="zai")
    ap.add_argument("--model", default="glm-4-plus")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    preset = args.provider
    from las.modules.providers.presets import PRESETS
    base_url = args.base_url or PRESETS.get(
        preset, PRESETS["generic"]).default_base_url
    adapter = real_adapter(preset, base_url, args.timeout)

    from las.modules.providers.probe import probe
    caps = probe(adapter, args.model, provider_id=args.provider)
    print(f"probe: tool_calling={caps.tool_calling} "
          f"json_schema={caps.json_schema}", flush=True)

    goals = build_corpus(100)[args.first:args.first + args.count]
    print(f"validating goals {[g.gid for g in goals]} "
          f"({[g.family for g in goals]}) against {args.model}", flush=True)
    data_dir = tempfile.mkdtemp(prefix="las-validate-")
    t0 = time.monotonic()
    metrics = run_corpus_real(
        data_dir, goals, adapter, preset, args.model,
        json_schema_supported=caps.json_schema,
        tool_calling=caps.tool_calling,
        on_goal=lambda i, g, status: print(
            f"  {g.gid} [{g.kind}] {status} "
            f"({time.monotonic() - t0:.0f}s)", flush=True))
    print(json.dumps(metrics, indent=2, default=str), flush=True)
    print(f"data dir: {data_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
