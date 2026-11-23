"""The GUI run session's event path (found broken by the v0.4.8
end-to-end demo: the run succeeded in the engine, but the session's
_post raised TypeError on every completion because the event kind and
a payload field share the name 'kind' - the UI never heard 'finished'
and the status chip stuck at 'running')."""

import os
import queue
import tempfile
import unittest

from las.modules.execution import DriverConfig, Engine
from las.modules.providers import ProviderRegistry
from las.modules.providers.mock import MockAdapter
from las.modules.selftest import _selftest_script, selftest_plan


class RunSessionTests(unittest.TestCase):
    def _session(self, root: str):
        from las.app.gui.runner import RunSession
        engine = Engine(root)
        plan = selftest_plan()
        run_id = engine.create_run(plan)
        registry = ProviderRegistry()
        registry.register("mock", MockAdapter(_selftest_script(plan)))
        session = RunSession(engine, run_id, registry,
                             {"default": ("mock", "mock-model")},
                             DriverConfig(failure_policy="stop"))
        return engine, run_id, session

    def test_post_accepts_a_payload_field_named_kind(self):
        """The regression, pinned at the unit: the 'finished' event
        carries payload['kind'] (the run's terminal status); the
        parameter and the payload field must not collide."""
        from las.app.gui.runner import RunSession
        session = RunSession.__new__(RunSession)
        session.events = queue.Queue()
        session._post("finished", kind="run_succeeded", code="ok")
        event_kind, payload = session.events.get_nowait()
        self.assertEqual(event_kind, "finished")
        self.assertEqual(payload["kind"], "run_succeeded")

    def test_successful_run_posts_finished(self):
        """The integration that used to die: a full scripted run
        through RunSession.run() must end with a 'finished' event
        naming run_succeeded."""
        with tempfile.TemporaryDirectory() as root:
            engine, run_id, session = self._session(root)
            session.run()                      # direct: no thread needed
            kinds = []
            payload = {}
            while True:
                try:
                    event_kind, pl = session.events.get_nowait()
                except queue.Empty:
                    break
                kinds.append(event_kind)
                if event_kind == "finished":
                    payload = pl
            self.assertIn("finished", kinds)
            self.assertEqual(payload.get("kind"), "run_succeeded")
            self.assertEqual(engine.state(run_id).status, "succeeded")


if __name__ == "__main__":
    unittest.main()
