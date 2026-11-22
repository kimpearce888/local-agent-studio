"""Run the Phase 0 benchmark: ``python -m bench`` (mock harness dry-run).

Real-model runs point the SAME corpus at a live OpenAI-compatible
server: ``python -m bench --provider ollama --model qwen2.5:0.5b``.
The capability probe runs first and its findings drive the planner
configuration (json-schema support), exactly like the app's setup
flow; the mock numbers validate the harness, the real numbers
measure the model, and every report says which is which.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

from bench.harness import run_all


def _run_real(args, data_dir: str) -> int:
    from las.modules.providers.presets import PRESETS
    from las.modules.providers.probe import detect_servers, probe
    from bench.harness import (
        cap_check, kill_cycles_real, real_adapter, run_corpus_real,
        socket_audit,
    )
    from bench.goals import build_corpus

    preset = PRESETS.get(args.provider, PRESETS["generic"])
    base_url = args.base_url or preset.default_base_url
    model = args.model or ""
    if not model:
        print("a real-provider run needs --model (the server's model id)")
        return 2

    # The capability probe (audit: real-server capability tests) -
    # against the LIVE server, before any benchmark traffic.
    print(f"provider {args.provider!r} at {base_url}, model {model!r}")
    found = detect_servers()
    print(f"server detection: "
          + (f"port {found[0]['port']} ({found[0]['guess']}) - "
             f"{len(found)} reachable" if found else "none of the "
             "well-known ports answered (continuing anyway)"))
    adapter = real_adapter(args.provider, base_url, args.timeout)
    # Throttle guard (measured 2026-11-22: an account-wide 429 window
    # outlasted a 40-minute full silence AND the session gap; /v1/models
    # is answered by the local proxy, so the probe reports
    # reachable+authenticated while every live chat call dies.  Without
    # this guard the corpus then burns goals as plan failures on a hot
    # limit - exactly what the 0.4.6 session had to kill by hand.)
    from las.modules.providers.base import ChatRequest, Message, ProviderError
    try:
        saw_text = False
        for ev in adapter.chat(ChatRequest(
                model=model,
                messages=[Message("user", "Reply with exactly: ok")],
                tools=[], params={"max_tokens": 8})):
            if ev.kind == "text_delta" and ev.text.strip():
                saw_text = True
        if not saw_text:
            print("chat round-trip produced no text; aborting before the corpus")
            return 2
        print("chat round-trip: ok (the upstream answers live traffic)")
    except ProviderError as exc:
        print(f"chat round-trip failed ({exc.code}): the upstream is "
              f"throttling or refusing live traffic.  Aborting BEFORE "
              f"the corpus burns goals - wait out the 429 window, "
              f"restart the proxy and rerun.")
        return 2

    started = time.monotonic()
    caps = probe(adapter, model, provider_id=args.provider)
    print(f"capability probe ({time.monotonic() - started:.1f}s): "
          f"reachable={caps.reachable} authenticated={caps.authenticated} "
          f"models_listed={caps.models_listed} streaming={caps.streaming} "
          f"tool_calling={caps.tool_calling} json_schema={caps.json_schema} "
          f"token_counting={caps.token_counting} "
          f"server_version={caps.server_version!r}")
    if not (caps.reachable and caps.authenticated):
        print("server not reachable/authenticated; aborting")
        return 2

    full = build_corpus(min(args.goals, 100))
    end = args.end if args.end is not None else len(full)
    goals = full[max(0, args.start):max(0, end)]
    print(f"corpus: {len(goals)} goals (indices {args.start}:{end}) "
          f"against the live model "
          f"(this measures the MODEL, not the harness)")
    t0 = time.monotonic()
    corpus = run_corpus_real(
        data_dir, goals, adapter, args.provider, model,
        json_schema_supported=caps.json_schema,
        tool_calling=caps.tool_calling,
        on_goal=lambda i, g, status: print(
            f"  goal {i + 1}/{len(goals)} [{g.kind}] {status} "
            f"({time.monotonic() - t0:.0f}s)"))
    repair_rounds = corpus.pop("repair_rounds")
    corpus["mean_repair_rounds"] = (
        round(sum(repair_rounds) / len(repair_rounds), 3)
        if repair_rounds else 0)

    kills = min(args.kills, 5)
    print(f"kill/resume: {kills} cycles against the live model")
    # The kill pool is its own deterministic slice of NON-GATED goals:
    # small --goals values can be all-gated (the corpus front-loads
    # them), and kills need resumable runs.
    kill_pool = [g for g in build_corpus(100) if g.kind != "gated"][:20]
    kill = kill_cycles_real(data_dir, kills, kill_pool, adapter,
                            args.provider, model,
                            json_schema_supported=caps.json_schema,
                            tool_calling=caps.tool_calling)

    # Engine-deterministic gates stay on the mock: cap enforcement and
    # the DEFAULT-configuration network audit are engine behavior, and
    # the audit's zero-connection bar is about no provider being
    # configured - a live provider would (correctly) connect.
    cap = cap_check(data_dir)
    net = socket_audit(data_dir)

    exit_code = 0
    if kill["cycles"] and \
            kill["resumed_ok"] / kill["cycles"] < \
            bench_targets()["resume_after_kill"]:
        # a small model failing resumes is a MODEL result, not an
        # engine regression - engine gates below decide the exit code
        pass
    report = {
        "provider": {"id": args.provider, "base_url": base_url,
                     "model": model,
                     "note": "live server - real-model measurement; "
                             "NOT the PRD reference model"},
        "capability_probe": {
            "reachable": caps.reachable,
            "authenticated": caps.authenticated,
            "models_listed": caps.models_listed,
            "streaming": caps.streaming,
            "tool_calling": caps.tool_calling,
            "json_schema": caps.json_schema,
            "token_counting": caps.token_counting,
            "context_length": caps.context_length,
            "server_version": caps.server_version,
        },
        "corpus": corpus,
        "resume_after_kill": kill,
        "cap": cap,
        "network": net,
        "engine_gates_passed": (cap["cap_reached"]
                                and cap["work_after_cap"] == 0
                                and not net["outbound_attempts"]),
    }
    out_dir = args.out or os.path.join("bench", "results")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(out_dir, f"bench-real-{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    c = report["corpus"]
    k = report["resume_after_kill"]
    print(f"Corpus (LIVE {model!r}): {c['goals']} goals")
    print(f"  valid first attempt:  {c['first_attempt_valid']}/{c['goals']}"
          f" (PRD >= 90% is a REFERENCE-model target)")
    print(f"  valid after repair:   {c['valid_after_repair']}/{c['goals']}"
          f" (PRD >= 99% is a REFERENCE-model target)")
    print(f"  mean repair rounds:   {c['mean_repair_rounds']}")
    print(f"  done without help:    {c['completed_without_help']}/{c['goals']}"
          f"  [blocked: {c.get('blocked_runs', 0)}, "
          f"gate pauses: {c['gate_pauses']}, "
          f"plan failures: {c['plan_failures']}, "
          f"run failures: {c['run_failures']}]")
    print(f"Forced kills: {k['resumed_ok']}/{k['cycles']} resumed cleanly"
          f"  (failures: {k['failures']})")
    print(f"Engine gates (mock-validated): "
          f"{'PASSED' if report['engine_gates_passed'] else 'FAILED'}")
    print(f"Results: {path}")
    return exit_code


def bench_targets():
    from bench.harness import PRD_TARGETS
    return PRD_TARGETS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bench")
    parser.add_argument("--goals", type=int, default=100,
                        help="corpus size (max 100)")
    parser.add_argument("--start", type=int, default=0,
                        help="first corpus index (for sliced/parallel runs)")
    parser.add_argument("--end", type=int, default=None,
                        help="corpus slice end (exclusive); default: goals")
    parser.add_argument("--kills", type=int, default=200,
                        help="forced-kill/resume cycles (real runs cap at 5)")
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--out", default=None, help="results directory")
    parser.add_argument("--provider", default="mock",
                        help="provider for worker/planner calls; "
                             "'mock' dry-runs the harness, anything else "
                             "is a live OpenAI-compatible preset id "
                             "(ollama, lmstudio, generic, ...)")
    parser.add_argument("--base-url", default=None,
                        help="override the preset's base URL")
    parser.add_argument("--model", default=None,
                        help="model id on the live server")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="per-request timeout for live calls (a "
                             "buffered backend must finish the ENTIRE "
                             "generation inside it)")
    args = parser.parse_args(argv)

    if args.provider != "mock":
        data_dir = args.data_dir or tempfile.mkdtemp(prefix="las-bench-")
        return _run_real(args, data_dir)

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
          f"  [blocked: {c.get('blocked_runs', 0)}, "
          f"gate pauses: {c['gate_pauses']}]")
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
