"""Mock-driven engine test harness (imports the mock provider module).

Engine integration tests use this; module unit tests use ``tests.planlib``
so they run in mock-less builds too.
"""

from __future__ import annotations

from typing import Optional

from las.modules.providers import ProviderRegistry
from las.modules.providers.mock import MockAdapter
from las.modules.execution import DriverConfig, Engine, RunDriver
from tests.planlib import (  # noqa: F401 - shared helpers
    constraint_objs, handoff_text, make_plan, request_json_for,
)


def success_turn(plan: Plan, task_id: str, summary: str = "Done.",
                 deliverables: Optional[list[dict]] = None,
                 **kwargs) -> dict:
    """A mock turn that produces a valid handoff for a task."""
    return {"text": handoff_text(task_id, summary, constraint_objs(plan),
                                 deliverables=deliverables, **kwargs)}


def run_with_mock(plan: Plan, script: list[dict], data_dir: str,
                  config: Optional[DriverConfig] = None,
                  adapter: Optional[MockAdapter] = None,
                  pause_after: Optional[str] = None):
    """Create a run and drive it to completion with a mock provider."""
    adapter = adapter or MockAdapter(script)
    engine = Engine(data_dir)
    registry = ProviderRegistry()
    registry.register("mock", adapter)
    run_id = engine.create_run(plan)
    config = config or DriverConfig(failure_policy="stop")
    driver = engine.driver(run_id, registry, {"default": ("mock", "mock-model")}, config)
    results = driver.run(pause_after=pause_after)
    return engine, run_id, driver, results
