"""Benchmark harness (PRD section 17): the machinery behind the metrics.

Every PRD benchmark metric that is deterministic engine behavior is
measured here against the mock provider:

- plans valid on the first attempt / after automatic repair;
- runs completing without human help (no gate pauses);
- resume after a forced kill (abandon a run mid-drive - the event-sourced
  equivalent of a process death - then resume and require success);
- runs that exceed a configured cap: zero work beyond one model call;
- outbound connections in the default configuration: zero (socket audit).

With a real provider (``--provider``) the same corpus measures the model;
the mock numbers validate the harness, and say so in every summary.
"""

from __future__ import annotations

import json
import random
import socket

from las.modules.execution import DriverConfig, Engine
from las.modules.planning import Planner
from las.modules.providers import ProviderRegistry
def _mock(script):
    """The bench harness is built on the mock provider; deferred so
    importing the harness never hard-fails without it."""
    from las.modules.providers.mock import MockAdapter  # optional-dep: providers.mock
    return MockAdapter(script)
from las.shared.plan_contract import Plan

from bench.goals import build_corpus, seed_workspace

PRD_TARGETS = {
    "first_attempt_valid": 0.90,
    "valid_after_repair": 0.99,
    "completed_without_help": 0.85,
    "resume_after_kill": 0.99,
    "cap_violations": 0,
    "outbound_connections": 0,
}


# -- scripted model replies -------------------------------------------------

def plan_reply(goal, tasks=None) -> dict:
    """A valid planner reply for the goal (gate on the last collector for
    gated goals)."""
    tasks = tasks or []
    if not tasks:
        for n, title in enumerate(goal.task_titles, 1):
            tasks.append({"id": f"t{n}", "type": "task", "title": title,
                          "instructions": f"{title} for {goal.title.lower()}.",
                          "needs": [f"t{n - 1}"] if n > 1 else []})
        tasks[-1]["type"] = "collector"
        if goal.kind == "gated":
            tasks[-1]["gate_before"] = True
    return {
        "title": goal.title,
        "constraints": [{"id": f"c{i + 1}", "text": text, "authority": "user"}
                        for i, text in enumerate(goal.constraints)],
        "tasks": tasks,
    }


def broken_reply(goal) -> dict:
    """A first reply that breaks V-03 (dependency on a later task)."""
    reply = plan_reply(goal)
    reply["tasks"][1]["needs"] = [f"t{len(reply['tasks'])}"]
    return reply


def planner_script(goal) -> list:
    if goal.kind == "repair":
        return [{"json": broken_reply(goal)}, {"json": plan_reply(goal)}]
    return [{"json": plan_reply(goal)}]


def handoff_text(task_id: str, plan: Plan) -> str:
    obj = {
        "task_id": task_id, "status": "done",
        "summary": f"Finished {task_id}.",
        "key_facts": [{"fact": "Work completed as instructed.",
                       "source": "message:1"}],
        "binding_constraints": [
            {"id": c.id, "text": c.text, "state": "active",
             "prerequisite": c.prerequisite, "authority": c.authority,
             "fallback": c.fallback}
            for c in plan.constraints],
        "decisions": [], "open_issues": [], "deliverables": [],
    }
    return (f"Finished {task_id}. Done.\n<handoff>\n"
            + json.dumps(obj, ensure_ascii=False, indent=1) + "\n</handoff>")


def run_script(plan: Plan) -> list:
    return [{"text": handoff_text(t.id, plan)} for t in plan.tasks]


def _registry(adapter) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register("mock", adapter)
    return registry


def _driver(engine: Engine, run_id: str, adapter, config=None):
    return engine.driver(run_id, _registry(adapter),
                         {"default": ("mock", "mock-model")},
                         config or DriverConfig(failure_policy="stop"))


# -- real-provider corpus ----------------------------------------------------

def _seed(engine, run_id, goal) -> None:
    """Write the goal's input files into the run workspace (see
    bench.goals.FIXTURES): the measured loop is read-files -> deliver
    -> hand off, which is the product's real contract."""
    from las.modules.security import Workspace
    seed_workspace(Workspace(engine.store.workspace_dir(run_id)), goal)


def run_worker_real(data_dir: str, goals, adapter, provider_id: str,
                    model: str, kills: int = 0) -> dict:
    """The RUN phase against a LIVE model with a known-good plan.

    Weak local models cannot produce valid plans (measured - see
    run_corpus_real), which starves the run/kill metrics.  This
    decomposition hands the worker a SCRIPTED valid plan (the same
    plan_reply the mock corpus uses) and measures what the live model
    does with the tasks: handoff validity, repairs, task outcomes,
    and - with ``kills`` - resume-after-abandon through the live
    provider.  Planner capability and worker capability are then
    separate, honest numbers.  Pass NON-GATED goals: a gate pause is
    a human decision, not a model result."""
    from bench.goals import build_corpus as _bc  # noqa: F401  (doc ref)
    rng = random.Random(20261121)
    metrics = {"goals": len(goals), "tasks_total": 0, "tasks_ok": 0,
               "tasks_failed": 0, "runs_succeeded": 0,
               "handoff_repairs": 0, "kill_cycles": kills,
               "kills_resumed_ok": 0}
    for goal in goals:
        plan = Planner(_mock(planner_script(goal)),
                       "mock-model").plan(goal.text).plan
        engine = Engine(data_dir)
        run_id = engine.create_run(plan)
        _seed(engine, run_id, goal)
        registry = ProviderRegistry()
        registry.register(provider_id, adapter)
        driver = engine.driver(run_id, registry,
                               {"default": (provider_id, model)},
                               DriverConfig(failure_policy="stop"))
        if kills:
            steps = rng.randint(1, max(1, len(plan.tasks) - 1))
            for _ in range(steps):
                driver.step()
            # abandon (the "kill"), then resume through the live model
            metrics["tasks_total"] += len(plan.tasks)
            engine.driver(run_id, registry,
                          {"default": (provider_id, model)},
                          DriverConfig(
                              failure_policy="stop")).run()
            state = engine.state(run_id)
            if state.status == "succeeded":
                metrics["runs_succeeded"] += 1
                metrics["kills_resumed_ok"] += 1
            events = engine.store.events(run_id)
            metrics["handoff_repairs"] += sum(
                1 for e in events
                if e.type == "task.attempt_failed")
            for e in events:
                if e.type == "task.succeeded":
                    metrics["tasks_ok"] += 1
                elif e.type == "task.failed":
                    metrics["tasks_failed"] += 1
            continue
        driver.run()
        metrics["tasks_total"] += len(plan.tasks)
        state = engine.state(run_id)
        if state.status == "succeeded":
            metrics["runs_succeeded"] += 1
        events = engine.store.events(run_id)
        metrics["handoff_repairs"] += sum(
            1 for e in events if e.type == "task.attempt_failed")
        for e in events:
            if e.type == "task.succeeded":
                metrics["tasks_ok"] += 1
            elif e.type == "task.failed":
                metrics["tasks_failed"] += 1
    return metrics


def real_adapter(preset: str, base_url: str, timeout: float = 300.0):
    """A live OpenAI-compatible adapter for ``--provider`` runs.  The
    preset string resolves through PRESETS (ollama/lmstudio/...), so
    quirks like the Unsloth enable_tools:false policy apply here too."""
    from las.modules.providers import ChatCompletionsAdapter
    return ChatCompletionsAdapter(preset, base_url, None,
                                  preset=preset, timeout=timeout)


def _real_driver(engine: Engine, run_id: str, provider_id: str,
                 adapter, model: str, config=None):
    registry = ProviderRegistry()
    registry.register(provider_id, adapter)
    return engine.driver(run_id, registry,
                         {"default": (provider_id, model)},
                         config or DriverConfig(failure_policy="stop"))


def run_corpus_real(data_dir: str, goals, adapter, provider_id: str,
                    model: str, json_schema_supported: bool = True,
                    params: dict | None = None,
                    on_goal=None) -> dict:
    """The corpus with a LIVE model: the same goals, the same metrics,
    no scripted replies.  What this measures is the MODEL - the harness
    itself was validated by the mock dry-run."""
    metrics = {"goals": len(goals), "first_attempt_valid": 0,
               "valid_after_repair": 0, "completed_without_help": 0,
               "blocked_runs": 0, "gate_pauses": 0, "plan_failures": 0,
               "run_failures": 0, "repair_rounds": []}
    for idx, goal in enumerate(goals):
        result = Planner(adapter, model,
                         params=params or {"temperature": 0.2,
                                           "max_tokens": 4096},
                         json_schema_supported=json_schema_supported
                         ).plan(goal.text)
        metrics["repair_rounds"].append(result.repair_rounds)
        if result.valid:
            metrics["valid_after_repair"] += 1
        if result.valid and result.repair_rounds == 0:
            metrics["first_attempt_valid"] += 1
        if not result.valid:
            metrics["plan_failures"] += 1
            if on_goal is not None:
                on_goal(idx, goal, "plan-invalid")
            continue
        engine = Engine(data_dir)
        run_id = engine.create_run(result.plan)
        _seed(engine, run_id, goal)
        _real_driver(engine, run_id, provider_id, adapter,
                     model).run()
        state = engine.state(run_id)
        if state.status == "succeeded":
            metrics["completed_without_help"] += 1
        elif state.status == "blocked":
            metrics["blocked_runs"] += 1
        elif state.status == "paused":
            metrics["gate_pauses"] += 1
        else:
            metrics["run_failures"] += 1
        if on_goal is not None:
            on_goal(idx, goal, state.status)
    return metrics


def kill_cycles_real(data_dir: str, cycles: int, goals, adapter,
                     provider_id: str, model: str,
                     json_schema_supported: bool = True,
                     seed: int = 20261121) -> dict:
    """Kill/resume against a LIVE model.  Abandon the driver at a task
    boundary, then resume the run through the real provider and require
    a clean finish - the same success bar as the mock cycles."""
    rng = random.Random(seed)
    pool = [g for g in goals if g.kind != "gated"]
    if not pool:
        raise ValueError("kill cycles need non-gated goals")
    result = {"cycles": cycles, "resumed_ok": 0, "failures": []}
    for _ in range(cycles):
        goal = pool[rng.randrange(len(pool))]
        plan_result = Planner(adapter, model,
                              json_schema_supported=json_schema_supported
                              ).plan(goal.text)
        if not plan_result.valid:
            result["failures"].append(
                {"goal": goal.gid, "steps": 0, "status": "plan-invalid"})
            continue
        plan = plan_result.plan
        engine = Engine(data_dir)
        run_id = engine.create_run(plan)
        _seed(engine, run_id, goal)
        driver = _real_driver(engine, run_id, provider_id, adapter,
                              model)
        steps = rng.randint(1, max(1, len(plan.tasks) - 1))
        for _ in range(steps):
            driver.step()
        # Abandon this driver (the "kill"); a fresh one resumes the run
        # through the same live provider.
        _real_driver(engine, run_id, provider_id, adapter, model).run()
        state = engine.state(run_id)
        if state.status == "succeeded":
            result["resumed_ok"] += 1
        else:
            result["failures"].append(
                {"goal": goal.gid, "steps": steps, "status": state.status})
    return result


# -- corpus metrics ---------------------------------------------------------

def run_corpus(data_dir: str, goals=None) -> dict:
    goals = goals if goals is not None else build_corpus()
    metrics = {"goals": len(goals), "first_attempt_valid": 0,
               "valid_after_repair": 0, "completed_without_help": 0,
               "blocked_runs": 0, "gate_pauses": 0, "plan_failures": 0,
               "run_failures": 0, "repair_rounds": []}
    for goal in goals:
        adapter = _mock(planner_script(goal))
        result = Planner(adapter, "mock-model").plan(goal.text)
        metrics["repair_rounds"].append(result.repair_rounds)
        if result.valid:
            metrics["valid_after_repair"] += 1
        if result.valid and result.repair_rounds == 0:
            metrics["first_attempt_valid"] += 1
        if not result.valid:
            metrics["plan_failures"] += 1
            continue
        engine = Engine(data_dir)
        run_id = engine.create_run(result.plan)
        _seed(engine, run_id, goal)
        worker = _mock(run_script(result.plan))
        _driver(engine, run_id, worker).run()
        state = engine.state(run_id)
        if state.status == "succeeded":
            metrics["completed_without_help"] += 1
        elif state.status == "blocked":
            metrics["blocked_runs"] += 1
        elif state.status == "paused":
            metrics["gate_pauses"] += 1
        else:
            metrics["run_failures"] += 1
    return metrics


# -- fault injection: forced kill then resume -------------------------------

def kill_cycles(data_dir: str, cycles: int = 200, seed: int = 20261121,
                goals=None) -> dict:
    """Abandon runs at random task boundaries (a process death leaves no
    farewell event - the store simply stops growing), then resume each
    with the remaining script and require a clean finish."""
    rng = random.Random(seed)
    pool = [g for g in (goals if goals is not None else build_corpus())
            if g.kind != "gated"]
    if not pool:
        raise ValueError("kill cycles need non-gated goals")
    result = {"cycles": cycles, "resumed_ok": 0, "failures": []}
    for i in range(cycles):
        goal = pool[rng.randrange(len(pool))]
        adapter = _mock([{"json": plan_reply(goal)}])
        plan = Planner(adapter, "mock-model").plan(goal.text).plan
        engine = Engine(data_dir)
        run_id = engine.create_run(plan)
        worker = _mock(run_script(plan))
        driver = _driver(engine, run_id, worker)
        steps = rng.randint(1, max(1, len(plan.tasks) - 1))
        for _ in range(steps):
            driver.step()
        # Abandon this driver (the "kill"); a fresh one resumes the run.
        consumed = worker.chat_calls
        remaining = _mock(run_script(plan)[consumed:])
        _driver(engine, run_id, remaining).run()
        state = engine.state(run_id)
        if state.status == "succeeded":
            result["resumed_ok"] += 1
        else:
            result["failures"].append(
                {"goal": goal.gid, "steps": steps, "status": state.status})
    return result


# -- cap enforcement ---------------------------------------------------------

def cap_check(data_dir: str) -> dict:
    """A tiny token cap must stop the run: CAP-REACHED is terminal, no
    task is attempted afterwards, and no extra model call is made."""
    goal = build_corpus(6)[5]                 # a 4-task goal
    adapter = _mock([{"json": plan_reply(goal)}])
    plan = Planner(adapter, "mock-model").plan(goal.text).plan
    engine = Engine(data_dir)
    run_id = engine.create_run(plan, cap_tokens=1)   # any call crosses it
    worker = _mock(run_script(plan))
    _driver(engine, run_id, worker,
            DriverConfig(failure_policy="stop", cap_tokens_run=1)).run()
    state = engine.state(run_id)
    events = engine.store.events(run_id)
    codes = [e.type for e in events]
    cap_at = codes.index("run.failed") if "run.failed" in codes else -1
    attempted_after = sum(
        1 for e in events[cap_at + 1:]
        if e.type in ("task.started", "task.succeeded", "task.failed"))
    return {"status": state.status,
            "cap_reached": cap_at >= 0 and events[cap_at].payload.get(
                "code") == "CAP-REACHED",
            "work_after_cap": attempted_after,
            "model_calls": worker.chat_calls}


# -- default-configuration network audit -------------------------------------

class _SocketGuard:
    """Fails the run if anything opens an outbound connection."""

    def __init__(self):
        self.attempts = []

    def __enter__(self):
        self._orig = socket.socket
        guard = self

        class Guarded(self._orig):
            def connect(self, address):
                guard.attempts.append(address)
                raise AssertionError(f"outbound connection: {address!r}")

        socket.socket = Guarded
        return self

    def __exit__(self, *exc):
        socket.socket = self._orig
        return False


def socket_audit(data_dir: str) -> dict:
    """A full goal, default configuration: zero outbound connections."""
    goal = build_corpus(6)[5]                  # a non-gated 4-task goal
    adapter = _mock([{"json": plan_reply(goal)}])
    plan = Planner(adapter, "mock-model").plan(goal.text).plan
    engine = Engine(data_dir)
    run_id = engine.create_run(plan)
    worker = _mock(run_script(plan))
    with _SocketGuard() as guard:
        _driver(engine, run_id, worker).run()
    state = engine.state(run_id)
    return {"status": state.status, "outbound_attempts": guard.attempts}


# -- orchestration ------------------------------------------------------------

def run_all(data_dir: str, goal_count: int = 100, kills: int = 200,
            seed: int = 20261121) -> tuple[dict, int]:
    goals = build_corpus(goal_count)
    corpus = run_corpus(data_dir, goals)
    repair_rounds = corpus.pop("repair_rounds")
    corpus["mean_repair_rounds"] = (
        round(sum(repair_rounds) / len(repair_rounds), 3) if repair_rounds else 0)
    kill = kill_cycles(data_dir, kills, seed, goals)
    cap = cap_check(data_dir)
    net = socket_audit(data_dir)
    report = {
        "provider": "mock (harness dry-run; real numbers need the reference model)",
        "corpus": corpus,
        "resume_after_kill": kill,
        "cap": cap,
        "network": net,
    }
    # Engine-deterministic gates: these MUST hold regardless of provider.
    exit_code = 0
    if kill["cycles"] and kill["resumed_ok"] / kill["cycles"] < PRD_TARGETS["resume_after_kill"]:
        exit_code = 1
    if cap["cap_reached"] is False or cap["work_after_cap"] > 0:
        exit_code = 1
    if net["outbound_attempts"]:
        exit_code = 1
    report["engine_gates_passed"] = exit_code == 0
    return report, exit_code
