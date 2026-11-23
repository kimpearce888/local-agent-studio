#!/usr/bin/env python3
"""Drive the real Local Agent Studio desktop app through a full
create-and-solve workflow, offline, under Xvfb, and screenshot every
stage with ffmpeg x11grab.

The interactions invoke the same Tk widget commands a mouse click does
(``plan_btn.invoke()``, ``GateDialog.on_approve()``); there is no
xdotool in this sandbox, so input is synthetic - but every window,
dialog and state in the screenshots is the real app rendering live.

Prerequisites (run from the repo root):
  Xvfb :99 -screen 0 1280x820x24 &   # then DISPLAY=:99
  python3 -m las providers add --name demo-mock --preset mock \\
      --data-dir gui-demo-data
  LAS_MOCK_SCRIPT must NOT be set for this process before StudioApp
  (we switch it between the planning and the run script explicitly).
"""

import json
import os
import subprocess
import sys
import time

import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # repo root
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from las.app.gui.app import StudioApp                   # noqa: E402
from las.app.gui.s7_gate import GateDialog              # noqa: E402

OUT = os.path.join(os.path.dirname(ROOT), "download", "e2e-screenshots")
os.makedirs(OUT, exist_ok=True)
DATA = os.path.join(ROOT, "gui-demo-data")

DISPLAY = os.environ.get("DISPLAY", ":99")


def shot(name: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab",
         "-video_size", "1280x820", "-i", DISPLAY, "-frames:v", "1",
         os.path.join(OUT, f"{name}.png")], check=True)
    print("  shot:", name)


def pump(app, seconds: float) -> None:
    """Pump the Tk event loop so background jobs and after() polls run."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.02)


def pump_until(app, cond, timeout: float, step: float = 0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        time.sleep(step)
        if cond():
            return True
    return False


def open_dialogs(app):
    """All mapped Toplevels anywhere in the widget tree - GateDialog's
    parent is the runview frame, not the root, so a flat
    winfo_children() on the app misses it."""
    out = []
    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, tk.Toplevel) and c.winfo_viewable():
                out.append(c)
            walk(c)
    walk(app)
    return out


def main() -> int:
    goal_text = open(os.path.join(HERE, "gui-goal.txt")).read().strip()

    # -- 1: the Runs screen on a fresh install (provider pre-seeded) ----
    app = StudioApp(DATA)
    pump(app, 1.5)
    shot("01-gui-runs-empty")

    # -- 2: New goal, typed like a user ----------------------------------
    app.show("goal")
    pump(app, 0.4)
    goal_screen = app._screens["goal"]
    goal_screen.goal.delete("1.0", "end")
    goal_screen.goal.insert("1.0", goal_text)
    goal_screen._update_counter()
    pump(app, 0.4)
    shot("02-gui-goal-typed")

    # -- 3: Plan (mock provider, scripted planner reply) ------------------
    os.environ["LAS_MOCK_SCRIPT"] = os.path.join(HERE, "gui-plan-script.json")
    goal_screen.plan_btn.invoke()
    ok = pump_until(app, lambda: app._current == "studio", 30)
    if not ok:
        print("PLANNING DID NOT OPEN THE STUDIO - state:", app._current)
        for d in open_dialogs(app):
            print("  dialog:", d.title())
        shot("99-gui-error")
        return 1
    pump(app, 1.0)
    shot("03-gui-plan-studio")

    # -- 4: Run - the session starts, t1 runs, the run pauses at the gate
    os.environ["LAS_MOCK_SCRIPT"] = os.path.join(HERE, "gui-run-script.json")
    studio = app._screens["studio"]
    studio.run_plan()
    ok = pump_until(
        app,
        lambda: any(isinstance(w, GateDialog) for w in open_dialogs(app)),
        30)
    pump(app, 0.6)
    shot("04-gui-gate-dialog" if ok else "04-gui-runview")
    if not ok:
        print("THE GATE DIALOG DID NOT OPEN - dialogs:",
              [w.title() for w in open_dialogs(app)])
        return 1

    # -- 5: Approve the gate (the dialog decides on the live session) ----
    gate = next(w for w in open_dialogs(app) if isinstance(w, GateDialog))
    gate.on_approve()
    pump(app, 0.5)
    ok = pump_until(
        app,
        lambda: app.engine.state(app._screens["studio"].run_id).status
        == "succeeded", 60)
    print("run succeeded:", ok)
    pump(app, 0.6)
    shot("05-gui-run-succeeded")

    # -- 6: the Runs list shows the finished run -------------------------
    app.show("runs")
    pump(app, 0.8)
    shot("06-gui-runs-final")

    # -- 7: the published deliverable, straight from the run workspace ---
    run_id = app._screens["studio"].run_id
    ws = os.path.join(DATA, "runs", run_id, "workspace")
    for fname in ("announcement.md", "draft.txt"):
        path = os.path.join(ws, fname)
        print(f"workspace file {fname}:",
              "present" if os.path.exists(path) else "MISSING")

    with open(os.path.join(OUT, "gui-run-state.json"), "w") as fh:
        state = app.engine.state(run_id)
        json.dump({
            "run_id": run_id,
            "status": state.status,
            "tasks": {t: {"state": ts.state,
                          "handoff": bool(ts.handoff)}
                      for t, ts in state.tasks.items()},
        }, fh, indent=1)
    print("state saved to gui-run-state.json")

    app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
